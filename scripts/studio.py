#!/usr/bin/env python3
"""Pollinations Studio — one skill, one creative pack.

Turns a short brief into a finished creative package through the Pollinations
API: a written concept, key visuals, a voice-over and a manifest that ties it
together. Standard library only, so it runs anywhere Python 3.10+ runs.

    export POLLINATIONS_API_KEY=sk_...
    python studio.py models --category image
    python studio.py concept --brief "premium electricity brand"
    python studio.py image --prompt "..." --model black-forest-labs/flux.1.1-pro
    python studio.py voice --text "..." --voice nova
    python studio.py pack --brief "..." --images 3

Every command prints a short human summary and the paths it wrote, so an agent
can chain them without parsing JSON.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://gen.pollinations.ai/v1"
ISSUER = "https://enter.pollinations.ai"
# Publishable App Key of this skill. It is a public client id, safe to ship:
# users sign in to their own account through it, no secret is stored here.
CLIENT_ID = os.environ.get("POLLINATIONS_CLIENT_ID", "pk_YxXyBK7lNdUC9mYf")


def default_credentials_path() -> Path:
    """Where the sign-in token lives: platform conventions, overridable."""
    override = os.environ.get("POLLINATIONS_CREDENTIALS")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "pollinations" / "credentials.json"
    return Path.home() / ".config" / "pollinations" / "credentials.json"


CREDENTIALS = default_credentials_path()
FALLBACK_CREDENTIALS = Path.cwd() / ".pollinations-credentials.json"
DEFAULT_TEXT_MODEL = "openai/gpt-5.4-nano"
DEFAULT_IMAGE_MODEL = "black-forest-labs/flux.1.1-pro"
DEFAULT_TTS_MODEL = "openai/tts-1"
DEFAULT_VOICE = "alloy"
USER_AGENT = "pollinations-studio/1.0"
TIMEOUT = 180
RETRIES = 3


# --------------------------------------------------------------------------- #
# plumbing
# --------------------------------------------------------------------------- #
class StudioError(RuntimeError):
    pass


def stored_token() -> str | None:
    """Read the token saved by `studio.py login` (sk_...), if any."""
    for path in (CREDENTIALS, FALLBACK_CREDENTIALS):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        token = (data.get("access_token") or "").strip()
        if token:
            return token
    return None


def save_token(payload: dict) -> Path:
    """Persist the token, falling back to the working directory when the
    default location is not writable (restricted shells, sandboxes, CI)."""
    blob = json.dumps(payload, indent=2).encode("utf-8")
    try:
        path = write(CREDENTIALS, blob)
    except OSError as exc:
        path = write(FALLBACK_CREDENTIALS, blob)
        print(f"\nnote: {CREDENTIALS} is not writable ({exc.strerror or exc});")
        print(f"      the token was stored in {path} for this directory instead.")
        print("      set POLLINATIONS_CREDENTIALS to choose another path.")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def api_key(explicit: str | None = None) -> str:
    """Resolve a key: explicit flag, then environment, then saved sign-in."""
    key = (explicit or os.environ.get("POLLINATIONS_API_KEY") or "").strip()
    if key:
        return key
    token = stored_token()
    if token:
        return token
    raise StudioError(
        "Not signed in. Run `python scripts/studio.py login` once — it opens "
        "https://enter.pollinations.ai/device, you approve, and the token is "
        "stored locally. (For automation you may instead set "
        "POLLINATIONS_API_KEY or pass --key.)"
    )


def request(method: str, path: str, key: str, payload: dict | None = None,
            raw: bool = False):
    """Call the API with retries; return parsed JSON (or raw bytes)."""
    url = f"{API}{path}"
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {key}", "User-Agent": USER_AGENT}
    if body is not None:
        headers["Content-Type"] = "application/json"
    last = ""
    for attempt in range(RETRIES):
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                data = resp.read()
            if raw:
                return data
            return json.loads(data.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read()[:400].decode("utf-8", "replace")
            except Exception:
                pass
            last = f"HTTP {exc.code}: {detail.strip()}"
            if exc.code in (400, 401, 403, 404):
                raise StudioError(last) from None
        except Exception as exc:  # transient network / TLS wobble
            last = f"{type(exc).__name__}: {exc}"
        if attempt < RETRIES - 1:
            time.sleep(2 + attempt * 3)
    raise StudioError(last or "request failed")


def slug(text: str, limit: int = 48) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.UNICODE).strip()
    cleaned = re.sub(r"[\s_-]+", "-", cleaned)
    return (cleaned or "output")[:limit].strip("-")


def parse_size(raw: str | None) -> tuple[int, int]:
    if not raw:
        return 1024, 1024
    match = re.match(r"^\s*(\d{2,4})\s*[xX*]\s*(\d{2,4})\s*$", raw)
    if not match:
        raise StudioError(f"bad size '{raw}', expected WxH like 1024x1024")
    width, height = int(match.group(1)), int(match.group(2))
    if not (256 <= width <= 2048 and 256 <= height <= 2048):
        raise StudioError("size must stay between 256 and 2048 px per side")
    return width, height


def write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def oauth_post(path: str, payload: dict) -> tuple[int, dict]:
    """POST to the issuer (device flow lives outside the /v1 API)."""
    url = f"{ISSUER}{path}"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"error": raw[:300]}


def cmd_login(args) -> int:
    if stored_token() and not args.force:
        print(f"already signed in ({CREDENTIALS}); use --force to sign in again")
        return 0
    status, info = oauth_post("/api/device/code",
                              {"client_id": CLIENT_ID, "scope": "generate profile"})
    if status != 200 or "device_code" not in info:
        raise StudioError(f"device flow rejected: {info.get('error_description') or info}")
    print("To connect your Pollinations account:\n")
    print(f"  1. open   {info.get('verification_uri')}")
    print(f"  2. enter  {info.get('user_code')}")
    print(f"  (or open  {info.get('verification_uri_complete')})\n")
    print(f"client: {CLIENT_ID}")
    interval = int(info.get("interval") or 5)
    deadline = time.time() + int(info.get("expires_in") or 900)
    print("waiting for approval", end="", flush=True)
    while time.time() < deadline:
        time.sleep(interval)
        print(".", end="", flush=True)
        status, token = oauth_post("/api/oauth/token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": info["device_code"],
            "client_id": CLIENT_ID,
        })
        if status == 200 and token.get("access_token"):
            payload = {
                "access_token": token["access_token"],
                "token_type": token.get("token_type", "Bearer"),
                "scope": token.get("scope", "generate profile"),
                "client_id": CLIENT_ID,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
            stored_at = save_token(payload)
            print(f"\nsigned in — token stored in {stored_at}")
            print("try: python scripts/studio.py image --prompt \"a blue circle\" --out out/test")
            return 0
        error = token.get("error") or ""
        if error == "slow_down":
            interval += 5
            continue
        if error in ("authorization_pending", "expired_token"):
            if error == "expired_token":
                raise StudioError("the code expired before approval, run login again")
            continue
        raise StudioError(f"sign-in failed: {token.get('error_description') or error or token}")
    raise StudioError("timed out waiting for approval")


def cmd_logout(args) -> int:
    if CREDENTIALS.exists():
        CREDENTIALS.unlink()
        print(f"signed out, removed {CREDENTIALS}")
    else:
        print("not signed in")
    return 0


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_models(args) -> int:
    data = request("GET", "/models", api_key(args.key))
    rows = data.get("data", [])
    if args.category:
        rows = [m for m in rows if m.get("category") == args.category]
    if args.free_only:
        rows = [m for m in rows if not m.get("paid_only")]
    if not rows:
        print("no models matched", file=sys.stderr)
        return 1
    print(f"{len(rows)} model(s):")
    for model in rows:
        price = model.get("pricing") or {}
        bits = ", ".join(f"{k}={v}" for k, v in price.items() if k != "currency")
        print(f"  {model['id']}")
        title = model.get("title") or ""
        extra = f" — {title}" if title else ""
        tail = f"  [{bits}]" if bits else ""
        print(f"      category={model.get('category')} paid_only={model.get('paid_only')}{extra}{tail}")
    return 0


def chat(key: str, model: str, prompt: str, system: str | None = None,
         max_tokens: int = 700) -> str:
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    data = request("POST", "/chat/completions", key, {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
    })
    try:
        return data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError, TypeError):
        raise StudioError(f"unexpected chat response: {str(data)[:300]}") from None


CONCEPT_SYSTEM = (
    "You are a creative director. Answer in the language of the brief. "
    "Return four short blocks and nothing else:\n"
    "CONCEPT: one sentence that names the idea.\n"
    "PALETTE: five hex colours, comma separated.\n"
    "VISUALS: three image prompts in English, one per line, prefixed with '- '.\n"
    "VOICE: a 25-40 word voice-over script."
)


def cmd_concept(args) -> int:
    text = chat(api_key(args.key), args.model, args.brief, CONCEPT_SYSTEM)
    print(text)
    if args.out:
        write(Path(args.out), text.encode("utf-8"))
        print(f"\nwritten: {args.out}")
    return 0


def generate_image(key: str, prompt: str, model: str, width: int, height: int,
                   out_dir: Path, name: str | None = None, seed: int | None = None) -> Path:
    payload = {
        "model": model,
        "prompt": prompt,
        "n": 1,
        "size": f"{width}x{height}",
        "response_format": "b64_json",
    }
    if seed is not None:
        payload["seed"] = seed
    data = request("POST", "/images/generations", key, payload)
    try:
        item = data["data"][0]
    except (KeyError, IndexError, TypeError):
        raise StudioError(f"unexpected image response: {str(data)[:300]}") from None
    if item.get("b64_json"):
        blob = base64.b64decode(item["b64_json"])
    elif item.get("url"):
        blob = request("GET", "", key, raw=True) if False else urllib.request.urlopen(
            item["url"], timeout=TIMEOUT).read()
    else:
        raise StudioError("image response had neither b64_json nor url")
    suffix = ".png" if blob[:8] == b"\x89PNG\r\n\x1a\n" else ".jpg"
    target = out_dir / f"{(name or slug(prompt))}{suffix}"
    return write(target, blob)


def cmd_image(args) -> int:
    key = api_key(args.key)
    width, height = parse_size(args.size)
    out_dir = Path(args.out)
    paths = []
    for index in range(args.count):
        prompt = args.prompt if args.count == 1 else f"{args.prompt} (variant {index + 1})"
        path = generate_image(key, prompt, args.model, width, height, out_dir,
                              name=f"{slug(args.prompt)}-{index + 1}", seed=args.seed)
        size_kb = round(path.stat().st_size / 1024, 1)
        print(f"image: {path} ({size_kb} KB)")
        paths.append(str(path))
    if args.json:
        print(json.dumps({"images": paths}, ensure_ascii=False))
    return 0


def cmd_voice(args) -> int:
    key = api_key(args.key)
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8").strip()
    data = request("POST", "/audio/speech", key, {
        "model": args.model,
        "input": text,
        "voice": args.voice,
    }, raw=True)
    suffix = ".mp3" if data[:3] == b"ID3" else ".wav"
    target = Path(args.out) if args.out else Path(f"voiceover{suffix}")
    if target.suffix not in (".mp3", ".wav"):
        target = target.with_suffix(suffix)
    write(target, data)
    print(f"voice: {target} ({round(len(data) / 1024, 1)} KB, {len(text)} chars)")
    return 0


def cmd_pack(args) -> int:
    key = api_key(args.key)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    brief = args.brief

    print(f"brief: {brief}")
    print("1/4 concept ...")
    concept_text = chat(key, args.text_model, brief, CONCEPT_SYSTEM)
    concept_path = write(out_dir / "concept.md", concept_text.encode("utf-8"))
    print(f"    {concept_path}")

    image_prompts = [line[2:].strip() for line in concept_text.splitlines()
                     if line.strip().startswith("- ")]
    if not image_prompts:
        image_prompts = [brief]
    image_prompts = image_prompts[: args.images]
    width, height = parse_size(args.size)

    print(f"2/4 visuals ({len(image_prompts)}) ...")
    images = []
    for index, prompt in enumerate(image_prompts, start=1):
        path = generate_image(key, prompt, args.image_model, width, height, out_dir / "images",
                              name=f"visual-{index:02d}")
        images.append(str(path))
        print(f"    {path} ({round(path.stat().st_size / 1024, 1)} KB)")

    print("3/4 voice-over ...")
    script = ""
    for line in concept_text.splitlines():
        if line.upper().startswith("VOICE:"):
            script = line.split(":", 1)[1].strip()
    if not script:
        script = args.fallback_script.format(brief=brief)
    script_path = write(out_dir / "voice-script.txt", script.encode("utf-8"))
    voice_path = None
    if not args.no_voice:
        data = request("POST", "/audio/speech", key, {
            "model": args.tts_model,
            "input": script,
            "voice": args.voice,
        }, raw=True)
        suffix = ".mp3" if data[:3] == b"ID3" else ".wav"
        voice_path = write(out_dir / f"voiceover{suffix}", data)
        print(f"    {voice_path} ({round(len(data) / 1024, 1)} KB)")

    print("4/4 manifest ...")
    manifest = {
        "brief": brief,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "models": {
            "text": args.text_model,
            "image": args.image_model,
            "tts": None if args.no_voice else args.tts_model,
        },
        "concept": str(concept_path),
        "images": images,
        "voice_script": str(script_path),
        "voiceover": str(voice_path) if voice_path else None,
    }
    manifest_path = write(out_dir / "manifest.json",
                          json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    print(f"    {manifest_path}")
    print(f"\npack ready: {out_dir}")
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="studio.py",
        description="Make something creative with Pollinations models.",
    )
    parser.add_argument("--key", help="API key (defaults to POLLINATIONS_API_KEY)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("models", help="list models with categories and pricing")
    p.add_argument("--category", choices=["text", "image", "audio", "video", "embedding",
                                          "realtime", "decision"])
    p.add_argument("--free-only", action="store_true", help="hide paid-only models")
    p.set_defaults(func=cmd_models)

    p = sub.add_parser("concept", help="write a creative direction for a brief")
    p.add_argument("--brief", required=True)
    p.add_argument("--model", default=DEFAULT_TEXT_MODEL)
    p.add_argument("--out")
    p.set_defaults(func=cmd_concept)

    p = sub.add_parser("image", help="generate one or more images")
    p.add_argument("--prompt", required=True)
    p.add_argument("--model", default=DEFAULT_IMAGE_MODEL)
    p.add_argument("--size", default="1024x1024")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--seed", type=int)
    p.add_argument("--out", default="out/images")
    p.add_argument("--json", action="store_true", help="also print machine-readable paths")
    p.set_defaults(func=cmd_image)

    p = sub.add_parser("voice", help="text to speech")
    p.add_argument("--text")
    p.add_argument("--file", help="read the script from a text file")
    p.add_argument("--model", default=DEFAULT_TTS_MODEL)
    p.add_argument("--voice", default=DEFAULT_VOICE)
    p.add_argument("--out")
    p.set_defaults(func=cmd_voice)

    p = sub.add_parser("pack", help="concept + visuals + voice-over + manifest")
    p.add_argument("--brief", required=True)
    p.add_argument("--images", type=int, default=3)
    p.add_argument("--size", default="1024x1024")
    p.add_argument("--text-model", default=DEFAULT_TEXT_MODEL)
    p.add_argument("--image-model", default=DEFAULT_IMAGE_MODEL)
    p.add_argument("--tts-model", default=DEFAULT_TTS_MODEL)
    p.add_argument("--voice", default=DEFAULT_VOICE)
    p.add_argument("--out", default="out/pack")
    p.add_argument("--no-voice", action="store_true")
    p.add_argument("--fallback-script",
                   default="A short introduction to {brief}, read in a calm, confident voice.")
    p.set_defaults(func=cmd_pack)

    p = sub.add_parser("login", help="sign in with Pollinations (device flow, no key to paste)")
    p.add_argument("--force", action="store_true", help="sign in again even if a token exists")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("logout", help="forget the stored sign-in token")
    p.set_defaults(func=cmd_logout)
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv[1:])
    if args.command == "voice" and not (args.text or args.file):
        print("voice: pass --text or --file", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except StudioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

