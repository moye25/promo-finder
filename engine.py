"""The whole pipeline as one function, shared by the command line (run.py) and the app (app.py)."""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

from export import write_workbook
from flow import cast_tokens, check_flow, repaired_variant
from parse import fmt
from pipeline import dedupe, propose_cross, propose_single, score, stranger_test, validate


def seg_str(p):
    return " + ".join(f"Ep{s['episode']} {fmt(s['start'])}-{fmt(s['end'])}" for s in p["segments"])


def run_show(eps_list, llm, out: Path, per_episode=3, do_cross=True, stranger_top=30, log=print) -> dict:
    eps = {e.num: e for e in eps_list}
    raw = Path(out) / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    premise = eps_list[0].logline
    # 1. propose inside each episode
    proposals, errors = [], []
    for ep in eps_list:
        log(f"episode {ep.num}/{len(eps_list)} ...")
        for p in propose_single(ep, llm, premise, raw, per_episode):
            (errors if "_error" in p else proposals).append(p)
    cast = cast_tokens(eps)

    def check(batch):
        """validate -> flow checks -> add a repaired variant where a reference was set up before the clip -> score"""
        out_ok, out_bad = [], []
        for p in batch:
            validate(p, eps)
            if not p["valid"]:
                out_bad.append(p)
                continue
            check_flow(p, eps, cast)
            out_ok.append(score(p, eps, len(eps_list)))
            q = repaired_variant(p, eps)
            if q:
                validate(q, eps)
                if q["valid"]:
                    check_flow(q, eps, cast)
                    out_ok.append(score(q, eps, len(eps_list)))
                    batch_extra.append(q)
        return out_ok, out_bad

    # 2. validate + flow + score
    batch_extra = []
    valid, rejected = check(proposals)
    singles = dedupe(valid)
    # 3. cross-episode promos built from the best single-episode clips
    cross = []
    if llm and do_cross and len(eps) > 1:
        log("cross-episode pass ...")
        cross_props = []
        for p in propose_cross(eps, singles, llm, premise, raw):
            (errors if "_error" in p else cross_props).append(p)
        ok, bad = check(cross_props)
        proposals += cross_props
        rejected += bad
        cross = dedupe(ok)
    proposals += batch_extra
    ranked = sorted(singles + cross, key=lambda p: -p["score"])
    # 4. stranger test on the shortlist: a model sees ONLY the clip and says if it can follow it
    if llm and stranger_top > 0:
        log(f"stranger test on top {stranger_top} ...")
        for p in ranked[:stranger_top]:
            stranger_test(p, eps, llm, raw)
            score(p, eps, len(eps_list))
        ranked = sorted(ranked, key=lambda p: -p["score"])
    for i, p in enumerate(ranked, 1):
        p["rank"] = i
    best_per_ep = {}
    for p in ranked:
        best_per_ep.setdefault(p["segments"][0]["episode"], p)
    return {"ranked": ranked, "rejected": rejected, "errors": errors, "proposals": proposals,
            "best_per_ep": best_per_ep, "n_cross": len(cross), "eps": eps}


def write_outputs(res: dict, out: Path, show: str, tag: str, eps_list) -> None:
    out = Path(out)
    ranked, rejected, errors, proposals, eps, best_per_ep = (res[k] for k in
        ("ranked", "rejected", "errors", "proposals", "eps", "best_per_ep"))
    clean = lambda p: {k: v for k, v in p.items() if not k.startswith("_")}  # noqa: E731
    (out / "promos.json").write_text(json.dumps([clean(p) for p in ranked], indent=2))
    (out / "rejected.json").write_text(json.dumps([clean(p) for p in rejected] + errors, indent=2))
    with open(out / "promos_ranked.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "score", "segments", "duration_s", "hook", "cliffhanger", "why", "source",
                    "hook_c", "cliff_c", "standalone_c", "funnel", "spoiler", "needs_context", "issues"])
        for p in ranked:
            c = p["components"]
            w.writerow([p["rank"], p["score"], seg_str(p), p["duration"], p.get("hook", ""), p.get("cliffhanger", ""),
                        p.get("why", ""), p["source"], c["hook"], c["cliffhanger"], c["standalone"], c["funnel"],
                        p.get("spoiler", ""), p.get("needs_context", ""), "; ".join(p["issues"])])
    # review sheet for a human editor -> python evaluate.py
    with open(out / "review_sheet.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rank", "segments", "hook", "cliffhanger", "editor_verdict(keep/maybe/reject)", "editor_note"])
        for p in ranked[:30]:
            w.writerow([p["rank"], seg_str(p), p.get("hook", ""), p.get("cliffhanger", ""), "", ""])
    write_report(out, show, tag, ranked, rejected, errors, proposals, eps_list)
    write_workbook(out / "promo_finder_steps.xlsx", show, tag, eps_list, proposals, ranked, eps, best_per_ep)


def write_report(out, show, tag, ranked, rejected, errors, proposals, eps_list):
    L = [f"# Promos for *{show}*", "", f"Model: `{tag}`  ·  episodes: {len(eps_list)}  ·  promos kept: {len(ranked)}", "",
         "Score = 0.25·hook + 0.25·cliffhanger + 0.15·standalone + 0.20·flow + 0.15·funnel − penalties "
         "(each part mixes the model's own rating with a rule-based check).", ""]
    L += ["## Post these first", ""]
    for p in ranked[:5]:
        c = p["components"]
        reasons = []
        if c["hook"] >= 0.7: reasons.append("strong 3-second hook")
        if c["cliffhanger"] >= 0.7: reasons.append("sharp open ending")
        if c["standalone"] >= 0.7: reasons.append("clear to a new viewer")
        if c["flow"] >= 0.9: reasons.append("flows as one story")
        if c["funnel"] >= 0.9: reasons.append("comes from the first 10 episodes, so new viewers reach the payoff fast")
        if p["source"] == "llm-cross": reasons.append("tells a mini-arc across episodes")
        L.append(f"**#{p['rank']} · {seg_str(p)} ({p['duration']:.0f}s)**: {', '.join(reasons) or 'best overall balance'}.")
    L += ["", "## All promos, ranked", ""]
    for p in ranked:
        c = p["components"]
        L += [f"### #{p['rank']} · score {p['score']:.2f} · {p['duration']:.0f}s · {p['source']}",
              f"- **Segments:** {seg_str(p)}",
              f"- **Hook:** {p.get('hook', '')}",
              f"- **Cliffhanger:** {p.get('cliffhanger', '')}",
              f"- **Why it works:** {p.get('why', '')}",
              f"- Breakdown: hook {c['hook']} · cliff {c['cliffhanger']} · standalone {c['standalone']} · flow {c['flow']} · funnel {c['funnel']}"
              + (f" · penalty −{c['penalty']}" if c["penalty"] else "")]
        if p.get("needs_context"): L.append(f"- Needs context: {p['needs_context']}")
        if p.get("spoiler") and p["spoiler"] != "none": L.append(f"- Spoiler: {p['spoiler']}")
        if p.get("flow", {}).get("flags"): L.append(f"- Flow flags: {'; '.join(p['flow']['flags'])}")
        if p.get("stranger", {}).get("open_question"): L.append(f"- Leaves the viewer asking: {p['stranger']['open_question']}")
        if p["issues"]: L.append(f"- ⚠️ {'; '.join(p['issues'])}")
        L.append("")
    (out / "report.md").write_text("\n".join(L))

    # ---- automatic quality checks
    Q = [f"# Quality checks: `{tag}`", ""]
    n = len(proposals)
    fixes = sum(1 for p in proposals if p.get("fixes"))
    Q += ["## Validator", "",
          f"- Proposed (single-episode): {n}",
          f"- Needed timestamp fixes (snapped to real line/shot boundaries): {fixes} ({fixes / max(1, n):.0%})",
          f"- Rejected: {len(rejected)}  ·  parse errors: {len(errors)}", ""]
    reasons = Counter(re.sub(r"ep\d+: |[\d.]+s\b|\(.*\)", "", i).strip() for p in rejected for i in p["issues"])
    for r, k in reasons.most_common():
        Q.append(f"  - {r}: {k}")
    hook_late = [p for p in ranked if any("hook lands" in i for i in p["issues"])]
    no_text_hook = [p for p in ranked if p["rule_scores"]["hook"] == 0]
    Q += ["", "## Hook / cliffhanger cross-checks", "",
          f"- Kept promos whose stated hook lands after 3s: {len(hook_late)}",
          f"- Kept promos with nothing intense in the text of the first 3s (rule hook = 0): {len(no_text_hook)}"]
    if any(p.get("scores") for p in ranked):
        disagree = [p for p in ranked if p.get("scores") and float(p["scores"].get("hook", 0) or 0) >= 8
                    and p["rule_scores"]["hook"] < 0.3]
        Q.append(f"- Model rates hook ≥8/10 but rules see little: {len(disagree)} → check these by eye: "
                 + ", ".join(f"#{p['rank']}" for p in disagree[:15]))
    flagged = [p for p in ranked if p.get("flow", {}).get("flags")]
    dang = [p for p in ranked if p.get("flow", {}).get("dangling")]
    reps = [p for p in ranked if "+repair" in p["source"]]
    multi = [p for p in ranked if len(p["segments"]) > 1]
    Q += ["", "## Flow (does it feel like one story?)", "",
          f"- Kept promos with more than one piece: {len(multi)}",
          f"- Kept promos with a join/flow flag: {len(flagged)}" + (" → " + ", ".join(f"#{p['rank']}" for p in flagged[:15]) if flagged else ""),
          f"- Kept promos that mention something set up before the clip: {len(dang)}",
          f"- Repaired variants (started earlier to include the setup) that beat their original: {len(reps)}"]
    st = [p for p in ranked if p.get("stranger") and "follow" in p["stranger"]]
    if st:
        rnd = [p for p in st if p["stranger"].get("feels_random") is True]
        Q.append(f"- Stranger test on {len(st)} promos: average follow {sum(float(p['stranger']['follow']) for p in st) / len(st):.1f}/10; "
                 f"felt random: {len(rnd)}" + (" → " + ", ".join(f"#{p['rank']}" for p in rnd) if rnd else ""))
    ep_cov = Counter(s["episode"] for p in ranked for s in p["segments"][:1])
    Q += ["", "## Coverage", "",
          f"- Episodes with ≥1 promo: {len(ep_cov)}/{len(eps_list)}",
          f"- Top-20 promos by episode: " + ", ".join(f"Ep{p['segments'][0]['episode']}" for p in ranked[:20]),
          f"- Durations: min {min((p['duration'] for p in ranked), default=0):.0f}s, "
          f"max {max((p['duration'] for p in ranked), default=0):.0f}s", "",
          "## Human check", "", "Fill `review_sheet.csv` (keep / maybe / reject), then run "
          "`python evaluate.py " + str(out) + "/review_sheet.csv` for precision@5/10/30."]
    (out / "quality.md").write_text("\n".join(Q))

