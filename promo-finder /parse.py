"""Parse Kuku screenplay .txt files into timed events.

Pure code, no AI: the screenplays are already structured, so parsing them
with rules is cheaper, faster and 100% reproducible.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

TIME_RE = re.compile(r"Start:\s*\((\d+):(\d+(?:\.\d+)?)\)\s*\|\s*End:\s*\((\d+):(\d+(?:\.\d+)?)\)")
HEADER_RE = re.compile(r"^\*\*(.+?)\*\*\s*$")


def to_sec(m: str, s: str) -> float:
    return round(int(m) * 60 + float(s), 2)


def fmt(t: float) -> str:
    """Seconds -> MM:SS.ss (same format as the scripts)."""
    m, s = divmod(round(t, 2), 60)
    return f"{int(m):02d}:{s:05.2f}"


@dataclass
class Event:
    start: float
    end: float
    kind: str          # CAMERA | ACTION | SFX | DIALOGUE
    text: str
    speaker: str = ""  # for DIALOGUE
    tone: str = ""     # e.g. "Cold, Commanding"
    scene: int = 0

    def line(self) -> str:
        """Compact one-line form used in LLM prompts."""
        who = f"{self.speaker} ({self.tone})" if self.speaker else self.kind
        return f"[{fmt(self.start)}-{fmt(self.end)}] {who}: {self.text}"


@dataclass
class Episode:
    num: int
    title: str
    logline: str
    notes: str
    events: list[Event] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return max((e.end for e in self.events), default=0.0)

    def window(self, start: float, end: float) -> list[Event]:
        return [e for e in self.events if e.start < end and e.end > start]


def parse_episode(path: Path) -> Episode:
    text = path.read_text(encoding="utf-8")
    num = int(re.search(r"EPISODE NUMBER:\s*Episode\s*(\d+)", text).group(1))
    logline = (re.search(r"EPISODE LOGLINE:\s*(.*)", text) or [None, ""])[1].strip()
    notes = (re.search(r"CONTINUITY NOTES:\s*(.*)", text) or [None, ""])[1].strip()

    events: list[Event] = []
    scene = 0
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith("SCENE "):
            scene = int(re.sub(r"\D", "", ln) or 0)
        h = HEADER_RE.match(ln)
        if h and i + 1 < len(lines):
            t = TIME_RE.search(lines[i + 1])
            if t:
                header = h.group(1).rstrip(":").strip()
                body = []
                j = i + 2
                while j < len(lines) and lines[j].strip() and not HEADER_RE.match(lines[j].strip()):
                    body.append(lines[j].strip())
                    j += 1
                start, end = to_sec(t.group(1), t.group(2)), to_sec(t.group(3), t.group(4))
                body_txt = " ".join(body).strip()
                if header in ("CAMERA", "ACTION"):
                    ev = Event(start, end, header, body_txt, scene=scene)
                elif header.startswith("SOUND"):
                    ev = Event(start, end, "SFX", body_txt, scene=scene)
                else:  # dialogue: NAME (Tone):
                    m = re.match(r"(.+?)\s*\((.*)\)$", header)
                    speaker, tone = (m.group(1), m.group(2)) if m else (header, "")
                    ev = Event(start, end, "DIALOGUE", body_txt.strip('"'), speaker.strip(), tone.strip(), scene)
                events.append(ev)
                i = j
                continue
        i += 1
    events.sort(key=lambda e: (e.start, e.end))
    return Episode(num, path.stem, logline, notes, events)


def load_show(folder: str | Path) -> list[Episode]:
    eps = [parse_episode(p) for p in sorted(Path(folder).glob("*.txt"))]
    return sorted(eps, key=lambda e: e.num)
