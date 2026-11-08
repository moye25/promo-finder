"""Cheap, deterministic signals: hook strength, cliffhanger strength, standalone-ness.

Used two ways:
  1. As a no-AI baseline ("rules" provider) that proposes promos on its own.
  2. As an independent check on whatever the LLM proposes (if the LLM says
     "great hook" but the first 3 seconds contain nothing intense, flag it).
"""
from __future__ import annotations

import re

from .parse import Episode, Event

# Physical / visual shocks a scrolling viewer reacts to.
HOOK_ACTION = re.compile(
    r"\b(slap|slaps|strikes?|struck|punch|shove|shoves|grabs?|seiz|yank|drag|throws?|hurls?|"
    r"slams?|smash|shatter|explo|collaps|faint|kneel|knee|kiss|blood|bleed|wound|sword|blade|dagger|"
    r"knife|gun|pistol|fire|flames?|blaze|burn|chains?|noose|gallows|scream|shriek|gasp|tears? (?:the|her|his)|"
    r"rip|crash|whip|crop|branding|iron|poison|arrest|guards? (?:seize|grab|drag)|proposal|ring)\w*",
    re.I,
)
HOT_TONE = re.compile(
    r"(furious|enraged|rage|scream|shout|yell|shock|horrif|desperate|venom|vicious|cruel|"
    r"menac|threat|sneer|mock|contempt|defiant|fierce|seething|livid|hiss|disgust|explosive)",
    re.I,
)
# Endings that leave a question open.
CLIFF_TEXT = re.compile(
    r"(cliffhanger|cut to black|ominous|freezes?|unresolved|sting|swells|looms|"
    r"door (?:bursts|swings|creaks) open|footsteps|who is|reveal)",
    re.I,
)
THREAT = re.compile(r"\b(or (?:he|she|you|i)|will (?:hang|die|burn|pay)|unless|until|never|"
                    r"you will|before (?:dawn|sunrise)|kill|hang|execute|destroy)\b", re.I)
# Words that only make sense if you watched earlier episodes.
CONTEXT_NEEDED = re.compile(r"\b(again|still|ever since|like (?:last|before)|remember when|as I promised)\b", re.I)


def hook_score(ev: Event) -> float:
    """0..1 how much this single event would stop a scroll."""
    s = 0.0
    if HOOK_ACTION.search(ev.text):
        s += 0.5 if ev.kind in ("ACTION", "CAMERA", "SFX") else 0.3
    if ev.kind == "DIALOGUE":
        if HOT_TONE.search(ev.tone):
            s += 0.35
        if ev.text.endswith(("!", "?")) or len(ev.text) < 40:
            s += 0.15
    if ev.kind == "SFX" and re.search(r"crack|slam|crash|bang|scream|shatter|explos|thud", ev.text, re.I):
        s += 0.3
    return min(s, 1.0)


def cliff_score(ev: Event, episode_end: float) -> float:
    """0..1 how well cutting right after this event leaves the viewer hanging."""
    s = 0.0
    if CLIFF_TEXT.search(ev.text):
        s += 0.4
    if ev.kind == "DIALOGUE":
        if ev.text.rstrip('".').endswith("?"):
            s += 0.35
        if THREAT.search(ev.text):
            s += 0.35
        if HOT_TONE.search(ev.tone):
            s += 0.15
    if episode_end - ev.end < 2.5:  # episodes are written to end on a cliffhanger
        s += 0.4
    return min(s, 1.0)


def first_n_seconds(events: list[Event], t0: float, n: float = 3.0) -> list[Event]:
    return [e for e in events if t0 <= e.start < t0 + n]


def window_features(ep: Episode, start: float, end: float) -> dict:
    """Rule features for one continuous segment [start, end] of an episode."""
    evs = ep.window(start, end)
    head = first_n_seconds(evs, start, 3.0) or evs[:1]
    tail = [e for e in evs if e.end >= end - 5.0] or evs[-1:]
    speakers = {e.speaker for e in evs if e.speaker}
    ctx_hits = sum(bool(CONTEXT_NEEDED.search(e.text)) for e in evs if e.kind == "DIALOGUE")
    dialogue = [e for e in evs if e.kind == "DIALOGUE"]
    return {
        "hook": max((hook_score(e) for e in head), default=0.0),
        "cliff": max((cliff_score(e, ep.duration) for e in tail), default=0.0),
        "n_speakers": len(speakers),
        "context_words": ctx_hits,
        "n_lines": len(dialogue),
        "ends_at_episode_end": ep.duration - end < 2.5,
    }


def standalone_score(f: dict) -> float:
    """Crude: fewer speakers and fewer 'you had to be there' words = easier for a stranger."""
    s = 1.0 - 0.12 * max(0, f["n_speakers"] - 2) - 0.15 * f["context_words"]
    if f["n_lines"] == 0:
        s -= 0.3  # pure visuals with no line rarely explain themselves
    return max(0.0, min(1.0, s))


def rule_candidates(ep: Episode, min_len=20.0, max_len=60.0, top_k=3) -> list[dict]:
    """No-AI baseline: start on a strong hook event, end on the best cliffhanger 20-60s later."""
    starts = [e for e in ep.events if hook_score(e) >= 0.5]
    # The cold open of an episode is often a hook too.
    starts += [e for e in ep.events[:3]]
    ends = [e for e in ep.events if e.kind != "CAMERA" or CLIFF_TEXT.search(e.text)]
    out = []
    for s in starts:
        best = None
        for e in ends:
            length = e.end - s.start
            if not (min_len <= length <= max_len):
                continue
            c = cliff_score(e, ep.duration)
            if best is None or c > best[0]:
                best = (c, e)
        if not best:
            continue
        f = window_features(ep, s.start, best[1].end)
        score = 0.4 * f["hook"] + 0.4 * f["cliff"] + 0.2 * standalone_score(f)
        out.append({
            "segments": [{"episode": ep.num, "start": s.start, "end": best[1].end}],
            "hook": s.text if not s.speaker else f'{s.speaker}: "{s.text}"',
            "cliffhanger": best[1].text if not best[1].speaker else f'{best[1].speaker}: "{best[1].text}"',
            "why": "Rule baseline: strongest hook event followed by the strongest open ending within 20-60s.",
            "_rule_score": score,
        })
    # keep non-overlapping best ones
    out.sort(key=lambda c: -c["_rule_score"])
    kept = []
    for c in out:
        s0, e0 = c["segments"][0]["start"], c["segments"][0]["end"]
        if all(_overlap(s0, e0, k["segments"][0]["start"], k["segments"][0]["end"]) < 0.5 for k in kept):
            kept.append(c)
        if len(kept) >= top_k:
            break
    return kept


def _overlap(a0, a1, b0, b1) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    return inter / max(1e-6, min(a1 - a0, b1 - b0))


REPLY = re.compile(r"^(no|yes|why|what|how|him|her|it|that|then|but|and|so)\b", re.I)


def rule_two_piece(ep: Episode, min_len=20.0, max_len=60.0) -> list[dict]:
    """No-AI joined promo inside one episode: the strongest hook beat, skip the slow middle, then the ending.
    Only built when both pieces share a speaker, so the join reads as 'and then...'."""
    dur = ep.duration
    hooks = sorted((e for e in ep.events if e.end < 0.7 * dur and hook_score(e) >= 0.5), key=lambda e: -hook_score(e))
    for h in hooks[:5]:
        p1_end = [e.end for e in ep.events if e.start >= h.start and 6 <= e.end - h.start <= 18 and e.kind != "CAMERA"]
        if not p1_end:
            continue
        a1, b1 = h.start, max(p1_end)
        for e in reversed(ep.events):
            a2 = e.start
            if not (12 <= dur - a2 <= 30) or a2 - b1 < 5:
                continue
            if e.kind == "DIALOGUE" and REPLY.match(e.text):
                continue
            if any(x.kind == "DIALOGUE" and x.start < a2 < x.end for x in ep.events):
                continue
            total = (b1 - a1) + (dur - a2)
            if not (min_len <= total <= max_len):
                continue
            sp1 = {x.speaker for x in ep.window(a1, b1) if x.speaker}
            sp2 = {x.speaker for x in ep.window(a2, dur) if x.speaker}
            if not (sp1 & sp2):
                continue
            last = ep.events[-1]
            return [{"segments": [{"episode": ep.num, "start": a1, "end": b1}, {"episode": ep.num, "start": a2, "end": dur}],
                     "hook": h.text if not h.speaker else f'{h.speaker}: "{h.text}"',
                     "cliffhanger": last.text if not last.speaker else f'{last.speaker}: "{last.text}"',
                     "why": "Rule baseline: strongest hook beat, slow middle skipped, ends on the episode's cliffhanger; "
                            "both pieces share a speaker.",
                     "_rule_score": 0.0}]
    return []
