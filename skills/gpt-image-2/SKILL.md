---
name: gpt-image-2
description: Image generation via openai/gpt-5.4-image-2 routed through OpenRouter. Use whenever an agent needs to (a) generate an image from a text prompt, (b) produce posters with dense typography or Chinese text, (c) render hi-res or widescreen visuals for papers/reports. Reads OPENROUTER_API_KEY from .env file; writes PNG/JPEG/WebP to disk. Prompt-craft references under references/ for photorealism, posters, infographics, and character sheets.
required-keys: [OPENROUTER_API_KEY]
allowed-tools: Bash
license: CC BY 4.0 (prompt patterns attributed to original authors)
---

# gpt-image-2

Image generation via `openai/gpt-5.4-image-2` on OpenRouter. Reads `OPENROUTER_API_KEY` from .env file; writes PNG/JPEG/WebP to disk.

## Usage

```bash
python skills/gpt-image-2/scripts/generate.py -p "PROMPT" [-f OUT] [options]
```

## CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `-p, --prompt` | required | Text prompt |
| `-f, --file` | auto | Output path. Auto-named `YYYY-MM-DD-HH-MM-SS-<slug>.<fmt>` in `./fig/` or cwd if omitted |
| `--model` | `openai/gpt-5.4-image-2` | OpenRouter model ID |
| `--size` | `1024x1024` | `1024x1024`, `1536x1024`, `1024x1536` |
| `--quality` | `high` | `auto` / `low` / `medium` / `high` |
| `--format` | `png` | `png` / `jpeg` / `webp` |
| `--moderation` | — | `auto` / `low` |

## Examples

```bash
# Basic generation
python skills/gpt-image-2/scripts/generate.py -p "photorealistic city skyline at dusk" -f fig/city.png

# Portrait poster with Chinese text, high quality
python skills/gpt-image-2/scripts/generate.py \
  -p 'Design a 3:4 tea poster. Exact copy: "山川茶事" / "冷泡系列" / "中杯 16 元"' \
  --size 1024x1536 --quality high -f fig/poster.png

# Landscape figure for paper
python skills/gpt-image-2/scripts/generate.py \
  -p "cinematic mountain landscape at golden hour" \
  --size 1536x1024 --quality high -f fig/landscape.png
```

## Size guide

| Intent | Size |
|--------|------|
| Default / social square | `1024x1024` |
| Portrait poster / mobile | `1024x1536` |
| Landscape / paper figure | `1536x1024` |

## Error surface

| Condition | Exit | stderr |
|-----------|------|--------|
| `OPENROUTER_API_KEY` unset | 2 | `error: OPENROUTER_API_KEY not set.` |
| OpenRouter returns non-2xx | 1 | `error: <exception message>` |
| No image data in response | 1 | `error: no image data in response: <item>` |

## Prompt-craft references (load only when needed)

- `references/craft.md` — 12 cross-cutting principles: exact-text-in-quotes, aspect-ratio-first, camera/shot language, scene density, style anchoring, negation, dense Chinese text, three-glances test.
- `references/gallery.md` — 56 community-curated templates across 8 categories: photography, games, UI/UX, typography, infographics, character consistency, editing, collage.
- `references/openai-cookbook.md` — OpenAI's official GPT Image prompting guide. Load when the user needs authoritative parameter semantics, UI mockups, pitch-deck slides, scientific diagrams, or billboard mockups.

Load a reference only when the request signals that category (poster → `gallery.md` typography section; Chinese text → `craft.md` sections 1, 7, 10; endpoint semantics → `openai-cookbook.md`).

## Attribution

Prompt patterns curated from [`ZeroLu/awesome-gpt-image`](https://github.com/ZeroLu/awesome-gpt-image) under CC BY 4.0.
