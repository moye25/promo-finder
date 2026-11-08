"""Flow: does the promo feel like ONE story, or like random parts glued together?

Code checks (always on, free):
  1. reply_at_cut    - a joined piece opens on a reply ("No.", "Why?", "Him?") to a line we cut out
  2. question_cut    - a question mid-promo whose answer was cut (only the LAST beat may be left open)
  3. no_shared_cast  - a joined piece shares no character with what came before
  4. story_order     - pieces jump backwards in the story, or skip too many episodes
  5. dangling_ref    - a line mentions "the garden"/"the ring" that was only set up before the clip
  6. continuity      - a wound/bandage in one piece vanishes in a joined piece from a later episode
Repair: a dangling reference in the first piece -> also try a version that starts earlier, at the setup line.
AI "stranger test" (optional): a second model call sees ONLY the clip's lines and says if it can follow it.
"""
from __future__ import annotations

import re

from .parse import Episode, Event, fmt

REPLY_START = re.compile(r"^(no|yes|yeah|why|what|how|him|her|them|it|that|then|but|and|so|because|of course|indeed)\b[\s,.!?]", re.I)
INJURY = re.compile(r"wound|bandag|blood|bleed|bruis|gash|sling|stitch|lacerat|burn", re.I)
REF = re.compile(r"\b(?:the|my|your|his|her|their|that|this)\s+([a-z]{4,})\b", re.I)
STOP = set("""time thing things moment night morning day today world life truth matter house room place
way word words name family lady lord duke duchess husband wife sister brother mother father woman man girl boy
people heart eyes face hand hands voice door floor wall table chair fire grace again away before like dead rest
part lost past think save high breathing other same only very much more most last first next best worst rest
whole entire poor dear good great little young old""".split())
MAX_EP_GAP = 5


def cast_tokens(eps: dict[int, Episode]) -> set[str]:
    toks = set()
    for ep in eps.values():
        for e in ep.events:
            if e.speaker:
                toks.add(e.speaker.split()[-1].lower())
    return {t for t in toks if len(t) > 2 and t not in {"man", "woman", "guard", "voice", "all"}}


def _names(evs: list[Event], cast: set[str]) -> set[str]:
    out = set()
    for e in evs:
        if e.speaker:
            out.add(e.speaker.split()[-1].lower())
        low = e.text.lower()
        out |= {c for c in cast if re.search(rf"\b{c}\b", low)}
    return out


def _seg_events(eps, s):
    return eps[s["episode"]].window(s["start"], s["end"])


def check_flow(p: dict, eps: dict[int, Episode], cast: set[str]) -> dict:
    """Adds p['flow'] = {checks..., 'flags': [...], 'rule_flow': 0..1}."""
    segs = p["segments"]
    flags, dangling = [], []
    seen_text = ""
    seen_names: set[str] = set()
    for i, s in enumerate(segs):
        ep = eps[s["episode"]]
        evs = _seg_events(eps, s)
        dlg = [e for e in evs if e.kind == "DIALOGUE"]
        names = _names(evs, cast)
        if i > 0:
            prev = segs[i - 1]
            if dlg and REPLY_START.match(dlg[0].text + " ") and dlg[0].start - s["start"] < 4:
                flags.append(f"reply_at_cut: piece {i + 1} opens on a reply ('{dlg[0].text[:40]}')")
            if seen_names and not (names & seen_names):
                flags.append(f"no_shared_cast: piece {i + 1} shares no character with earlier pieces")
            if s["episode"] < prev["episode"] or (s["episode"] == prev["episode"] and s["start"] < prev["end"]):
                flags.append(f"story_order: piece {i + 1} goes back in time")
            if s["episode"] - prev["episode"] > MAX_EP_GAP:
                flags.append(f"story_order: jumps {s['episode'] - prev['episode']} episodes")
            prev_evs = _seg_events(eps, prev)
            if s["episode"] != prev["episode"] and s["episode"] - prev["episode"] <= 3:
                hurt_prev = any(INJURY.search(e.text) for e in prev_evs)
                hurt_now = any(INJURY.search(e.text) for e in evs)
                if hurt_prev and not hurt_now and (names & _names(prev_evs, cast)):
                    flags.append(f"continuity: injury shown in Ep{prev['episode']} not mentioned in Ep{s['episode']}")
        # a question in a non-final piece must get its answer inside the promo
        if i < len(segs) - 1 and dlg and dlg[-1].text.rstrip('".').endswith("?"):
            nxt = [e for e in ep.events if e.kind == "DIALOGUE" and e.start >= dlg[-1].end]
            if nxt and nxt[0].end > s["end"]:
                flags.append(f"question_cut: '{dlg[-1].text[:40]}' is cut from its answer")
        # dangling references: mentioned here, set up only before this piece
        for e in dlg:
            for m in REF.finditer(e.text):
                w = m.group(1).lower()
                if w in STOP or w in cast or w in seen_text:
                    continue
                # set up earlier as a noun ("the ledger", "a ring"), not just the same word used loosely
                noun = re.compile(rf"\b(?:the|a|an|my|your|his|her|their|that|this)\s+(?:\w+\s+)?{w}\b", re.I)
                before = [x for x in ep.events if x.end <= s["start"] and noun.search(x.text)]
                if before:
                    intro = before[-1]
                    dangling.append({"word": w, "piece": i + 1, "setup_at": intro.start, "setup_line": intro.text[:90]})
            seen_text += " " + e.text.lower()
        seen_text += " " + " ".join(x.text.lower() for x in evs)
        seen_names |= names
    hard = len(flags)
    rule_flow = max(0.0, 1.0 - 0.25 * hard - 0.1 * len(dangling))
    p["flow"] = {"flags": flags, "dangling": dangling, "rule_flow": round(rule_flow, 2)}
    return p


def repaired_variant(p: dict, eps: dict[int, Episode], max_len: float = 60.0, max_back: float = 20.0) -> dict | None:
    """If the first piece references something set up just before it, start earlier to include the setup."""
    d0 = [d for d in p.get("flow", {}).get("dangling", []) if d["piece"] == 1]
    if not d0:
        return None
    s0 = p["segments"][0]
    ep = eps[s0["episode"]]
    new_start = min(d["setup_at"] for d in d0)
    if s0["start"] - new_start > max_back:
        return None
    # snap onto the start of the line/shot that contains the setup
    cands = [e.start for e in ep.events if e.start <= new_start + 0.01]
    new_start = max(cands) if cands else new_start
    extra = s0["start"] - new_start
    if p["duration"] + extra > max_len + 2:
        return None
    q = {k: v for k, v in p.items() if k not in ("flow", "components", "rule_scores", "score", "issues", "fixes")}
    q["segments"] = [dict(s0, start=new_start)] + [dict(s) for s in p["segments"][1:]]
    q["issues"], q["fixes"] = [], [f"repair: started {extra:.1f}s earlier to include the setup for "
                                  + ", ".join(f"'{d['word']}'" for d in d0)]
    q["source"] = p["source"] + "+repair"
    q["duration"] = round(p["duration"] + extra, 2)
    q.pop("hook_time", None)
    return q


def clip_text(p: dict, eps: dict[int, Episode]) -> str:
    """What a stranger would experience: only the lines inside the clip, with cut markers."""
    out, t = [], 0.0
    for i, s in enumerate(p["segments"]):
        if i > 0:
            same = s["episode"] == p["segments"][i - 1]["episode"]
            out.append("--- CUT (later in the same episode) ---" if same else "--- CUT (a later episode) ---")
        for e in _seg_events(eps, s):
            rel = t + max(0.0, e.start - s["start"])
            who = f"{e.speaker} ({e.tone})" if e.speaker else e.kind
            out.append(f"[{fmt(rel)}] {who}: {e.text}")
        t += s["end"] - s["start"]
    return "\n".join(out)


def promo_script_rows(p: dict, eps: dict[int, Episode]) -> list[dict]:
    """Line-by-line promo script with roles: HOOK (first 3s), BUILD, CLIFFHANGER (last line/shot)."""
    rows, t = [], 0.0
    total = p["duration"]
    for i, s in enumerate(p["segments"]):
        if i > 0:
            rows.append({"clip_time": fmt(t), "episode": s["episode"], "source_time": fmt(s["start"]),
                         "role": "CUT", "who": "", "text": f"--- cut to Ep{s['episode']} {fmt(s['start'])} ---"})
        evs = _seg_events(eps, s)
        for e in evs:
            rel = t + max(0.0, e.start - s["start"])
            role = "HOOK" if rel < 3.0 else "BUILD"
            rows.append({"clip_time": fmt(rel), "episode": s["episode"], "source_time": fmt(e.start), "role": role,
                         "who": e.speaker or e.kind, "text": e.text})
        t += s["end"] - s["start"]
    # mark the closing beat(s): anything starting in the last 3 seconds
    for r in rows:
        if r["role"] == "BUILD":
            m, sec = r["clip_time"].split(":")
            if int(m) * 60 + float(sec) >= total - 3.0:
                r["role"] = "CLIFFHANGER"
    if rows and rows[-1]["role"] == "BUILD":
        rows[-1]["role"] = "CLIFFHANGER"
    return rows
