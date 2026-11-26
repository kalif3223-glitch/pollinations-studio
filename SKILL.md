---
name: pollinations-studio
description: Turn a one-line brief into a finished creative package (concept, key visuals, voice-over, manifest) using Pollinations text, image and audio models. Use when the user asks for brand concepts, moodboards, campaign visuals, narration or a media pack and no design app is at hand; also use to list Pollinations models with categories and pricing before choosing one.
license: MIT
metadata:
  category: creative
  tags: [pollinations, image-generation, text-to-speech, moodboard, branding]
  requires: python>=3.10
---

# Pollinations Studio

One skill that turns a sentence into a creative pack: a written direction, three
key visuals, a voice-over and a manifest that links everything together. All
through one API, no SDK, no build step — the script is standard library only.

## Setup (once)

```bash
python scripts/studio.py login
```

That prints a code and the address `https://enter.pollinations.ai/device`, waits
for the approval, and stores the token in `~/.config/pollinations/credentials.json`
so every later command works without a key. `logout` removes it.

Automation may skip the sign-in and pass a key instead:

```bash
export POLLINATIONS_API_KEY=sk_...   # optional, overrides the stored token
```

On Windows PowerShell: `$env:POLLINATIONS_API_KEY = "sk_..."`. Never commit a key.
## What to run

```bash
# 1. see what is available and what it costs
python scripts/studio.py models --category image
python scripts/studio.py models --category audio --free-only

# 2. one creative direction for a brief
python scripts/studio.py concept --brief "premium electricity brand for industrial parks"

# 3. a single image, any supported model and size
python scripts/studio.py image \
  --prompt "editorial brand tile, interlocking circles emblem, graphite and cyan" \
  --model black-forest-labs/flux.1.1-pro --size 1024x1024 --out out/images

# 4. narration
python scripts/studio.py voice --text "Русский свет интеграция." --voice nova

# 5. everything at once
python scripts/studio.py pack --brief "brand concept for an industrial power platform" --images 3
```

`pack` writes:

```
out/pack/
  concept.md          CONCEPT / PALETTE / VISUALS / VOICE blocks
  images/visual-01..03
  voice-script.txt
  voiceover.mp3
  manifest.json       brief, models used, every artifact path
```

## Choosing models

- Images: `black-forest-labs/flux.1.1-pro` for finished brand work,
  `black-forest-labs/flux.1-schnell` (alias `flux`) when iteration speed matters,
  `tongyi-mai/z-image-turbo` for cheap drafts.
- Text and creative direction: `openai/gpt-5.4-nano` is fast and cheap; any model
  from `models --category text` works.
- Audio: `openai/tts-1` (voices `alloy`, `echo`, `fable`, `onyx`, `nova`,
  `shimmer`), `openai/tts-1-hd` for higher fidelity.
- Check `paid_only` before promising anything: `models --free-only` hides models
  that a quest balance cannot pay for.

## Working rules

1. Read the brief, then run `concept` first — the visuals are written from it, so
   a weak direction produces weak images.
2. Keep image prompts in English even when the brief is Russian: image models
   follow English far better, while the concept text stays in the user's language.
3. Generate at least two variants per key visual and pick, never ship the first
   sample unseen.
4. Inspect every artifact (open the image, play the audio) before reporting done.
   A 200 response is not proof of a usable artifact.
5. If a call fails, read the error body: `401` means a bad key, `402` an empty
   balance, `400` a payload the model does not accept (for example a text model on
   the audio endpoint).

## Scope and safety

- The pack is a starting point for a designer, not a substitute: typography,
  layout and brand rules still come from a human or a design system.
- Prompts leave the machine and are processed by third-party providers. Do not
  send confidential client material, personal data or internal documents.
- Generated visuals must be reviewed for text artefacts; models invent lettering.

