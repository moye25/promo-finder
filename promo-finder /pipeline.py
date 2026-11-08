"""Find -> validate -> score -> rank. The LLM proposes; code checks every claim it can."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import prompts
from .llm import LLM, parse_json
from .parse import Episode, fmt
from .rules import _overlap, rule_candidates, rule_two_piece, standalone_score, window_features

MIN_LEN, MAX_LEN = 20.0, 60.0
TS = re.compile(r"(\d+):(\d+(?:\.\d+)?)")


def ts(x) -> float:
    if isinstance(x, (int, float)):
        return float(x)
    m = TS.search(str(x))
    if not m:
        raise ValueError(f"bad timestamp {x!r}")
    return int(m.group(1)) * 60 + float(m.group(2))


def transcript(ep: Episode, camera_chars: int = 140) -> str:
    out = []
    for e in ep.events:
        if e.kind == "CAMERA" and len(e.text) > camera_chars:
            e = type(e)(**{**e.__dict__, "text": e.text[:camera_chars] + "..."})
        out.append(e.line())
    return "\n".join(out)


# ---------------------------------------------------------------- proposing
def propose_single(ep: Episode, llm: LLM | None, premise: str, raw_dir: Path, max_promos: int = 3) -> list[dict]:
    if llm is None:
        out = [dict(c, source="rules") for c in rule_candidates(ep, MIN_LEN, MAX_LEN, max_promos)]
        out += [dict(c, source="rules-2piece") for c in rule_two_piece(ep, MIN_LEN, MAX_LEN)]
        return out
    raw_path = raw_dir / f"ep{ep.num:03d}.json"
    if raw_path.exists():  # cache: reruns are free and outputs stay identical
        text = json.loads(raw_path.read_text())["response"]
    else:
        user = prompts.EPISODE_PROMPT.format(premise=premise, num=ep.num, logline=ep.logline, notes=ep.notes,
                                             duration=fmt(ep.duration), transcript=transcript(ep),
                                             max_promos=max_promos)
        text = llm.complete(prompts.SYSTEM, user)
        raw_path.write_text(json.dumps({"episode": ep.num, "model": f"{llm.provider}/{llm.model}",
                                        "response": text}, indent=2))
    try:
        promos = parse_json(text).get("promos", [])
    except Exception as e:  # noqa: BLE001
        return [{"_error": f"ep{ep.num}: could not parse model JSON ({e})"}]
    for p in promos:
        p["source"] = "llm-single"
        for s in p.get("segments", []):
            s["episode"] = ep.num
    return promos


def propose_cross(eps: dict[int, Episode], singles: list[dict], llm: LLM, premise: str, raw_dir: Path,
                  max_promos: int = 6, n_blocks: int = 40) -> list[dict]:
    blocks = {}
    for p in sorted(singles, key=lambda p: -p["score"])[:n_blocks]:
        for s in p["segments"]:
            bid = f"B{len(blocks) + 1}"
            blocks[bid] = {**s, "hook": p["hook"], "cliffhanger": p["cliffhanger"]}
    block_txt = "\n".join(f'{b}: ep{v["episode"]} [{fmt(v["start"])}-{fmt(v["end"])}] hook: {v["hook"]} | ends: {v["cliffhanger"]}'
                          for b, v in blocks.items())
    raw_path = raw_dir / "cross_episode.json"
    if raw_path.exists():
        text = json.loads(raw_path.read_text())["response"]
    else:
        loglines = "\n".join(f"Ep{n}: {e.logline}" for n, e in sorted(eps.items()))
        text = llm.complete(prompts.SYSTEM, prompts.CROSS_PROMPT.format(
            premise=premise, loglines=loglines, blocks=block_txt, max_promos=max_promos))
        raw_path.write_text(json.dumps({"model": f"{llm.provider}/{llm.model}", "blocks": blocks, "response": text},
                                       indent=2))
    try:
        promos = parse_json(text).get("promos", [])
    except Exception as e:  # noqa: BLE001
        return [{"_error": f"cross-episode: could not parse model JSON ({e})"}]
    for p in promos:
        p["source"] = "llm-cross"
        for s in p.get("segments", []):
            b = blocks.get(str(s.get("block")))
            if b is None:
                s["_bad_block"] = True
                continue
            s["episode"] = b["episode"]
            s["_block_range"] = (b["start"], b["end"])
    return promos


# ---------------------------------------------------------------- validating
def snap(ep: Episode, t: float, edge: str) -> float:
    """Move a cut point onto a real event boundary and never inside a dialogue line."""
    t = max(0.0, min(ep.duration, t))
    for e in ep.events:  # don't cut a line in half
        if e.kind == "DIALOGUE" and e.start < t < e.end:
            return e.start if edge == "start" else e.end
    bounds = [e.start for e in ep.events] if edge == "start" else [e.end for e in ep.events]
    return min(bounds, key=lambda b: abs(b - t))


def validate(p: dict, eps: dict[int, Episode]) -> dict:
    p.setdefault("issues", [])
    p.setdefault("fixes", [])
    segs = []
    for s in p.get("segments", []):
        if s.get("_bad_block") or s.get("episode") not in eps:
            p["issues"].append("segment refers to unknown block/episode")
            continue
        ep = eps[s["episode"]]
        try:
            a, b = ts(s["start"]), ts(s["end"])
        except (KeyError, ValueError) as e:
            p["issues"].append(f"bad timestamp: {e}")
            continue
        if "_block_range" in s:
            lo, hi = s["_block_range"]
            if a < lo - 0.5 or b > hi + 0.5:
                p["fixes"].append(f"ep{ep.num}: segment clipped to its building block")
                a, b = max(a, lo), min(b, hi)
        a2, b2 = snap(ep, a, "start"), snap(ep, b, "end")
        if abs(a2 - a) > 0.3 or abs(b2 - b) > 0.3:
            p["fixes"].append(f"ep{ep.num}: snapped {fmt(a)}-{fmt(b)} -> {fmt(a2)}-{fmt(b2)}")
        if b2 - a2 < 2.0:
            p["issues"].append(f"ep{ep.num}: segment shorter than 2s")
            continue
        segs.append({"episode": ep.num, "start": round(a2, 2), "end": round(b2, 2)})
    order = [(s["episode"], s["start"]) for s in segs]
    if p.get("source") != "llm-cross" and order != sorted(order):
        p["issues"].append("segments out of time order")
    p["segments"] = segs
    p["duration"] = round(sum(s["end"] - s["start"] for s in segs), 2)
    if not segs:
        p["valid"] = False
        return p
    if p["duration"] > MAX_LEN + 2:
        p["issues"].append(f"too long ({p['duration']:.0f}s > 60s)")
    if p["duration"] < MIN_LEN - 2:
        p["issues"].append(f"too short ({p['duration']:.0f}s < 20s)")
    # hook must land in the first 3 seconds
    if p.get("hook_time"):
        try:
            off = ts(p["hook_time"]) - segs[0]["start"]
            p["hook_offset"] = round(off, 2)
            if off > 3.5 or off < -1:
                p["issues"].append(f"hook lands {off:.1f}s into the clip (should be <3s)")
        except ValueError:
            pass
    p["valid"] = not any(i.startswith(("too long", "too short", "segments out", "segment refers")) for i in p["issues"])
    return p


# ---------------------------------------------------------------- scoring
def score(p: dict, eps: dict[int, Episode], n_eps: int) -> dict:
    first, last = p["segments"][0], p["segments"][-1]
    f_first = window_features(eps[first["episode"]], first["start"], first["end"])
    f_last = window_features(eps[last["episode"]], last["start"], last["end"])
    f_all = [window_features(eps[s["episode"]], s["start"], s["end"]) for s in p["segments"]]
    rule = {"hook": f_first["hook"], "cliff": f_last["cliff"],
            "standalone": min(standalone_score(f) for f in f_all)}
    llm = p.get("scores") or {}
    def mix(llm_key, rule_key):
        v = llm.get(llm_key)
        try:
            return 0.7 * float(v) / 10 + 0.3 * rule[rule_key]
        except (TypeError, ValueError):
            return rule[rule_key]
    hook, cliff, alone = mix("hook", "hook"), mix("cliffhanger", "cliff"), mix("standalone", "standalone")
    # Funnel: a new viewer lands on episode 1. Promos from early episodes pay off within the free episodes
    # and spoil less. (Assumption; tune with real install data.)
    earliest = min(s["episode"] for s in p["segments"])
    funnel = max(0.3, 1.0 - 0.7 * max(0, earliest - 10) / max(1, n_eps - 10))
    penalty = 0.0
    if str(p.get("spoiler", "")).lower().startswith("major"):
        penalty += 0.15
    if any("hook lands" in i for i in p["issues"]):
        penalty += 0.10
    if p.get("source", "").startswith("llm") and rule["hook"] == 0:
        penalty += 0.05  # nothing in the text of the first 3s backs up the claimed hook
    # Flow: does it feel like one story? Code checks always; the AI stranger test when available.
    fl = p.get("flow", {})
    flow = fl.get("rule_flow", 1.0)
    st = p.get("stranger")
    if st:
        try:
            flow = 0.7 * float(st.get("follow", 5)) / 10 + 0.3 * flow
        except (TypeError, ValueError):
            pass
        if st.get("feels_random") is True:
            penalty += 0.10
    p["rule_scores"] = {k: round(v, 2) for k, v in rule.items()}
    p["components"] = {"hook": round(hook, 2), "cliffhanger": round(cliff, 2), "standalone": round(alone, 2),
                       "flow": round(flow, 2), "funnel": round(funnel, 2), "penalty": round(penalty, 2)}
    p["score"] = round(0.25 * hook + 0.25 * cliff + 0.15 * alone + 0.20 * flow + 0.15 * funnel - penalty, 3)
    return p


def dedupe(promos: list[dict]) -> list[dict]:
    kept = []
    for p in sorted(promos, key=lambda p: -p["score"]):
        dup = False
        for k in kept:
            for s in p["segments"]:
                for t in k["segments"]:
                    if s["episode"] == t["episode"] and _overlap(s["start"], s["end"], t["start"], t["end"]) > 0.6:
                        dup = True
        if dup:
            p["dropped"] = "near-duplicate of a higher-ranked promo"
        else:
            kept.append(p)
    return kept


def stranger_test(p: dict, eps: dict[int, Episode], llm: LLM, raw_dir: Path) -> dict:
    """Second opinion from a model that sees ONLY what is inside the clip, like a new viewer would."""
    from .flow import clip_text
    key = "_".join(f"{s['episode']}-{s['start']}-{s['end']}" for s in p["segments"]).replace(".", "p")
    path = raw_dir / "stranger" / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        text = json.loads(path.read_text())["response"]
    else:
        clip = clip_text(p, eps)
        text = llm.complete(prompts.SYSTEM, prompts.STRANGER_PROMPT.format(clip=clip))
        path.write_text(json.dumps({"segments": p["segments"], "clip": clip, "model": f"{llm.provider}/{llm.model}",
                                    "response": text}, indent=2))
    try:
        p["stranger"] = parse_json(text)
    except Exception as e:  # noqa: BLE001
        p["stranger"] = {"error": str(e)}
    return p
