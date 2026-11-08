"""Find the best promos in a microdrama show.

  python run.py --input "input/My Show" --provider rules            # no AI, offline baseline
  python run.py --input "input/My Show" --provider gemini           # free Gemini key in GEMINI_API_KEY
  python run.py --input "input/My Show" --provider groq --model llama-3.3-70b-versatile
"""
from __future__ import annotations

import argparse
from pathlib import Path

from promo_finder.engine import run_show, write_outputs
from promo_finder.llm import LLM
from promo_finder.parse import load_show


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="folder of episode .txt screenplays")
    ap.add_argument("--provider", default="rules", help="rules | gemini | groq | openrouter | openai | ollama | anthropic | custom")
    ap.add_argument("--model", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--episodes", default=None, help="e.g. 1-10 to run a subset")
    ap.add_argument("--per-episode", type=int, default=3)
    ap.add_argument("--no-cross", action="store_true", help="skip cross-episode promos")
    ap.add_argument("--stranger-top", type=int, default=30, help="run the AI stranger test on the top N (0 = off)")
    ap.add_argument("--rpm", type=float, default=8, help="max LLM calls per minute (free tiers are ~10)")
    a = ap.parse_args()

    eps_list = load_show(a.input)
    if a.episodes:
        lo, hi = map(int, a.episodes.split("-"))
        eps_list = [e for e in eps_list if lo <= e.num <= hi]
    show = Path(a.input.rstrip("/")).name
    llm = None if a.provider == "rules" else LLM(a.provider, a.model, min_interval=60 / a.rpm)
    tag = "rules" if llm is None else f"{llm.provider}-{llm.model}".replace("/", "_").replace(":", "_")
    out = Path(a.out or f"outputs/{tag}")

    res = run_show(eps_list, llm, out, a.per_episode, not a.no_cross, a.stranger_top)
    write_outputs(res, out, show, tag, eps_list)
    print(f"\n{len(res['ranked'])} promos ({res['n_cross']} cross-episode), {len(res['rejected'])} rejected -> {out}/")


if __name__ == "__main__":
    main()
