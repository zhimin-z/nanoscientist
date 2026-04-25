#!/usr/bin/env python3
"""Generate a two swim-lane research workflow diagram using matplotlib.

Usage:
    python generate.py --output PATH --research-steps JSON_LIST --write-steps JSON_LIST --topic TITLE

All content comes from arguments — no hardcoded domain content.
"""
import argparse
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch


# Color scheme
RESEARCH_FILL  = "#4A90D9"
RESEARCH_EDGE  = "#2C5F8A"
WRITE_FILL     = "#5BA55B"
WRITE_EDGE     = "#3A6E3A"
LANE_BG_TOP    = "#EEF5FC"
LANE_BG_BOT    = "#EEF7EE"
ARROW_COLOR    = "#555555"
DASHED_COLOR   = "#888888"
TEXT_COLOR     = "#FFFFFF"
LABEL_COLOR    = "#333333"


def _wrap(text: str, max_chars: int = 18) -> str:
    """Wrap text at word boundaries for box labels."""
    words = text.split()
    lines, line = [], []
    for w in words:
        if sum(len(x) for x in line) + len(line) + len(w) > max_chars and line:
            lines.append(" ".join(line))
            line = [w]
        else:
            line.append(w)
    if line:
        lines.append(" ".join(line))
    return "\n".join(lines)


def draw_diagram(
    research_steps: list,
    write_steps: list,
    topic: str,
    output_path: str,
) -> None:
    n_r = len(research_steps)
    n_w = len(write_steps)
    n_cols = max(n_r, n_w, 1)

    fig_w = max(16, n_cols * 2.6 + 2.0)
    fig_h = 6.0
    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_xlim(0, fig_w)
    ax.set_ylim(0, fig_h)
    ax.axis("off")

    # Lane background rects
    lane_h = (fig_h - 1.0) / 2  # space for title at top
    lane_top_y = fig_h - 1.0 - lane_h
    lane_bot_y = 0.0

    ax.add_patch(mpatches.FancyBboxPatch(
        (0.1, lane_top_y), fig_w - 0.2, lane_h - 0.05,
        boxstyle="round,pad=0.05", linewidth=1,
        edgecolor="#CCCCCC", facecolor=LANE_BG_TOP, zorder=0,
    ))
    ax.add_patch(mpatches.FancyBboxPatch(
        (0.1, lane_bot_y), fig_w - 0.2, lane_h - 0.05,
        boxstyle="round,pad=0.05", linewidth=1,
        edgecolor="#CCCCCC", facecolor=LANE_BG_BOT, zorder=0,
    ))

    # Lane labels
    ax.text(0.35, lane_top_y + lane_h / 2, "Research",
            fontsize=11, fontweight="bold", color=RESEARCH_EDGE,
            va="center", ha="left", rotation=90, zorder=2)
    ax.text(0.35, lane_bot_y + lane_h / 2, "Writing",
            fontsize=11, fontweight="bold", color=WRITE_EDGE,
            va="center", ha="left", rotation=90, zorder=2)

    # Title
    ax.text(fig_w / 2, fig_h - 0.45, topic,
            fontsize=13, fontweight="bold", color="#222222",
            va="center", ha="center", zorder=2,
            clip_on=True)

    box_w = min(2.2, (fig_w - 1.4) / max(n_cols, 1) - 0.3)
    box_h = 0.8
    x_start = 0.9
    x_step = (fig_w - 1.4) / max(n_cols, 1)

    def _box_x(col: int) -> float:
        return x_start + col * x_step

    def _draw_boxes(steps, lane_center_y, fill, edge, numbered=True):
        centers = []
        for i, step in enumerate(steps):
            cx = _box_x(i) + box_w / 2
            cy = lane_center_y
            bx = cx - box_w / 2
            by = cy - box_h / 2
            ax.add_patch(FancyBboxPatch(
                (bx, by), box_w, box_h,
                boxstyle="round,pad=0.06",
                linewidth=1.5,
                edgecolor=edge,
                facecolor=fill,
                zorder=3,
            ))
            label = f"({i+1}) " + _wrap(step) if numbered else _wrap(step)
            ax.text(cx, cy, label,
                    fontsize=8.5, color=TEXT_COLOR,
                    va="center", ha="center",
                    fontweight="bold", zorder=4,
                    multialignment="center")
            centers.append((cx, cy))
        return centers

    r_centers = _draw_boxes(
        research_steps,
        lane_top_y + lane_h / 2,
        RESEARCH_FILL, RESEARCH_EDGE,
    )
    w_centers = _draw_boxes(
        write_steps,
        lane_bot_y + lane_h / 2,
        WRITE_FILL, WRITE_EDGE,
    )

    # Horizontal arrows between boxes in each lane
    def _draw_arrows(centers, cy):
        for i in range(len(centers) - 1):
            x0 = centers[i][0] + box_w / 2 + 0.05
            x1 = centers[i + 1][0] - box_w / 2 - 0.05
            ax.annotate(
                "", xy=(x1, cy), xytext=(x0, cy),
                arrowprops=dict(
                    arrowstyle="-|>",
                    color=ARROW_COLOR,
                    lw=1.2,
                ),
                zorder=5,
            )

    _draw_arrows(r_centers, lane_top_y + lane_h / 2)
    _draw_arrows(w_centers, lane_bot_y + lane_h / 2)

    # Vertical dashed connector: Research lane (top) → Writing lane (bottom)
    mid_col = (n_cols - 1) / 2
    mid_x = _box_x(int(mid_col)) + box_w / 2
    y_from = lane_top_y + 0.05          # bottom edge of research lane
    y_to   = lane_bot_y + lane_h - 0.05  # top edge of writing lane
    ax.annotate(
        "", xy=(mid_x, y_to), xytext=(mid_x, y_from),
        arrowprops=dict(
            arrowstyle="-|>",
            color=DASHED_COLOR,
            lw=1.0,
            linestyle="dashed",
        ),
        zorder=5,
    )

    plt.tight_layout(pad=0.3)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out), dpi=200, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate research workflow diagram")
    ap.add_argument("--output", required=True, help="Output PNG path")
    ap.add_argument("--research-steps", required=True,
                    help="JSON array of research step labels")
    ap.add_argument("--write-steps", required=True,
                    help="JSON array of writing step labels")
    ap.add_argument("--topic", default="Research Workflow",
                    help="Diagram title")
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

    draw_diagram(
        research_steps=research_steps,
        write_steps=write_steps,
        topic=args.topic,
        output_path=args.output,
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
