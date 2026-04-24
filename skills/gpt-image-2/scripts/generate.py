#!/usr/bin/env python3
"""Image generation via openai/gpt-5.4-image-2 on OpenRouter."""
import argparse
import base64
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from openai import OpenAI


def _client() -> OpenAI:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("error: OPENROUTER_API_KEY not set.", file=sys.stderr)
        sys.exit(2)
    return OpenAI(base_url="https://openrouter.ai/api/v1", api_key=key)


def _auto_path(prompt: str, fmt: str, out_dir: Path) -> Path:
    slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower())[:40].strip("-")
    ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{ts}-{slug}.{fmt}"


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate images via openai/gpt-5.4-image-2 on OpenRouter")
    ap.add_argument("-p", "--prompt", required=True, help="Text prompt")
    ap.add_argument("-f", "--file", help="Output path (auto-named if omitted)")
    ap.add_argument("--model", default="openai/gpt-5.4-image-2", help="Model ID on OpenRouter")
    _SIZES = ["1024x1024", "1536x1024", "1024x1536"]
    ap.add_argument("--size", default="1024x1024", choices=_SIZES, help="Image size")
    ap.add_argument("--quality", default="high", choices=["auto", "low", "medium", "high"])
    ap.add_argument("--format", dest="fmt", default="png", choices=["png", "jpeg", "webp"])
    ap.add_argument("--moderation", default=None, choices=["auto", "low"])
    args = ap.parse_args()

    client = _client()

    extra: dict = {"size": args.size, "quality": args.quality, "output_format": args.fmt}
    if args.moderation:
        extra["moderation"] = args.moderation

    try:
        response = client.chat.completions.create(
            model=args.model,
            messages=[{"role": "user", "content": args.prompt}],
            extra_body=extra,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    cwd = Path.cwd()
    out_dir = cwd / "fig" if (cwd / "fig").exists() else cwd
    dest = Path(args.file) if args.file else _auto_path(args.prompt, args.fmt, out_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)

    import re as _re

    raw = response.model_dump()
    msg = raw.get("choices", [{}])[0].get("message", {})

    # OpenRouter returns image in message.images[0].image_url.url as a data URL
    images = msg.get("images") or []
    if images:
        url_val = (images[0].get("image_url") or {}).get("url", "")
        m = _re.search(r'base64,([A-Za-z0-9+/=\n]+)', url_val)
        if m:
            dest.write_bytes(base64.b64decode(m.group(1).replace("\n", "")))
        elif url_val.startswith("http"):
            import urllib.request
            urllib.request.urlretrieve(url_val, dest)
        else:
            print(f"error: unrecognised image_url format: {url_val[:100]}", file=sys.stderr)
            sys.exit(1)
    else:
        print("error: no images in response", file=sys.stderr)
        sys.exit(1)

    print(dest)
    sys.exit(0)


if __name__ == "__main__":
    main()
