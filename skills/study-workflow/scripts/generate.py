#!/usr/bin/env python3
"""Generate a research workflow diagram via the OpenRouter image API.

Usage:
    python generate.py --output PATH --research-steps JSON_LIST --write-steps JSON_LIST --topic TITLE
"""
import argparse
import base64
import json
import os
import sys
from pathlib import Path


def _build_prompt(topic: str, research_steps: list[str], write_steps: list[str]) -> str:
    r_bullets = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(research_steps))
    w_bullets = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(write_steps))

    return f"""Generate a camera-ready academic workflow diagram in a modern infographic style.

TOPIC: {topic}

STYLE:
- Clean, minimal, publication-quality (similar to top-tier conference/journal papers)
- Soft color palette with stage-based color coding (e.g., blue → orange → green → purple)
- Rounded rectangles, thin borders, subtle shadows
- Consistent iconography (line icons)
- Clear directional arrows (left-to-right primary flow)

LAYOUT:
- Top row: Research pipeline stages (left-to-right flow)
- Bottom row: Writing pipeline stages (left-to-right flow)
- Vertical dashed arrow connecting center of Research row to Writing row (knowledge transfer)
- Right side: summarized outcomes / contributions

RESEARCH STAGES (top row):
{r_bullets}

WRITING STAGES (bottom row):
{w_bullets}

CONTENT STRUCTURE:
For each stage box:
- Title (1–2 words, bold)
- 1-line description (action-oriented)
- 2–3 concise bullet points (methods, data, or operations)

REQUIREMENTS:
- Avoid redundancy across boxes
- Use short, technical phrasing (no full sentences)
- Emphasize transformations (input → process → output)
- Include quantitative signals if available (e.g., dataset size, scale)
- Ensure visual balance and alignment

OUTPUT:
- High-resolution, camera-ready diagram
- Aspect ratio 3:2, landscape orientation
- Suitable for direct inclusion in an academic paper"""


def _load_env(project_root: Path) -> None:
    env_file = project_root / ".env"
    if env_file.exists() and "OPENROUTER_API_KEY" not in os.environ:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate research workflow diagram via OpenRouter image API")
    ap.add_argument("--output", required=True, help="Output PNG path")
    ap.add_argument("--research-steps", required=True, help="JSON array of research step labels")
    ap.add_argument("--write-steps", required=True, help="JSON array of writing step labels")
    ap.add_argument("--topic", default="Research Workflow", help="Diagram title")
    args = ap.parse_args()

    try:
        research_steps = json.loads(args.research_steps)
        write_steps = json.loads(args.write_steps)
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON — {e}", file=sys.stderr)
        sys.exit(1)

    if not research_steps:
        research_steps = ["Literature Survey", "Data Collection", "Analysis"]
    if not write_steps:
        write_steps = ["Introduction", "Methods", "Results", "Conclusion"]

    project_root = Path(__file__).resolve().parents[3]
    _load_env(project_root)

    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("error: OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    prompt = _build_prompt(args.topic, research_steps, write_steps)

    import urllib.request
    payload = json.dumps({
        "model": "openai/gpt-5.4-image-2",
        "prompt": prompt,
        "n": 1,
        "size": "1536x1024",
        "response_format": "b64_json",
    }).encode()

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/images/generations",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"error: API request failed — {e}", file=sys.stderr)
        sys.exit(1)

    try:
        b64 = data["data"][0]["b64_json"]
    except (KeyError, IndexError) as e:
        print(f"error: unexpected response shape — {e}: {str(data)[:300]}", file=sys.stderr)
        sys.exit(1)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(base64.b64decode(b64))
    print(args.output)


if __name__ == "__main__":
    main()
