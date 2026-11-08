"""Offline test of the LLM path with a scripted fake model (no API key needed).

  python -m tests.test_pipeline "input/<show folder>"
Checks: JSON parsing, timestamp snapping, length/ordering validation, hook-offset check, cross-episode blocks.
"""
import json
import sys
import tempfile
from pathlib import Path

from promo_finder.parse import load_show
from promo_finder.pipeline import propose_cross, propose_single, score, validate


class FakeLLM:
    provider, model = "fake", "scripted"

    def __init__(self, replies):
        self.replies = list(replies)

    def complete(self, system, user):
        return self.replies.pop(0)


def main(folder):
    eps = {e.num: e for e in load_show(folder)[:2]}
    ep1 = eps[1]
    single_reply = "```json\n" + json.dumps({"promos": [
        # good: continuous, ends on the cut-to-black; start given slightly off a boundary on purpose
        {"segments": [{"start": "01:40.10", "end": "02:18.00"}], "hook": "x", "hook_time": "01:40.10",
         "cliffhanger": "y", "why": "z", "scores": {"hook": 8, "cliffhanger": 9, "standalone": 7}, "spoiler": "none"},
        # bad: 90 seconds long -> must be rejected
        {"segments": [{"start": "00:00.00", "end": "01:30.00"}], "hook": "x", "cliffhanger": "y", "why": "z",
         "scores": {"hook": 5, "cliffhanger": 5, "standalone": 5}},
        # bad: hook claimed 10s into the clip -> flagged
        {"segments": [{"start": "00:11.00", "end": "00:40.00"}], "hook": "x", "hook_time": "00:21.80",
         "cliffhanger": "y", "why": "z", "scores": {"hook": 9, "cliffhanger": 6, "standalone": 6}},
    ]}) + "\n```"
    with tempfile.TemporaryDirectory() as d:
        props = propose_single(ep1, FakeLLM([single_reply]), "premise", Path(d))
        assert len(props) == 3, props
        for p in props:
            validate(p, eps)
        good, long_, late = props
        assert good["valid"] and 20 <= good["duration"] <= 60, good
        assert not long_["valid"] and any("too long" in i for i in long_["issues"]), long_
        assert any("hook lands" in i for i in late["issues"]), late
        # snapped onto a real boundary
        bounds = {e.start for e in ep1.events}
        assert good["segments"][0]["start"] in bounds, good["segments"]
        # a cut never falls inside a dialogue line
        for p in (good, late):
            for s in p["segments"]:
                for e in eps[s["episode"]].events:
                    if e.kind == "DIALOGUE":
                        assert not (e.start < s["start"] < e.end) and not (e.start < s["end"] < e.end)
        score(good, eps, 50)
        score(late, eps, 50)
        assert good["score"] > late["score"] - 0.2
        # cached on disk: second call doesn't need the model
        again = propose_single(ep1, FakeLLM([]), "premise", Path(d))
        assert len(again) == 3

        good["score"] = 0.9
        e2 = eps[2]
        cross_reply = json.dumps({"promos": [{"segments": [
            {"block": "B1", "start": "01:40.10", "end": "01:55.00"},
            {"block": "B2", "start": "00:00.00", "end": "00:20.00"}],
            "hook": "a", "cliffhanger": "b", "why": "c", "scores": {"hook": 7, "cliffhanger": 7, "standalone": 6}}]})
        b2 = {"segments": [{"episode": 2, "start": 0.0, "end": min(40.0, e2.duration)}], "hook": "h", "cliffhanger": "c",
              "score": 0.8}
        cross = propose_cross(eps, [good, b2], FakeLLM([cross_reply]), "premise", Path(d))
        validate(cross[0], eps)
        assert cross[0]["valid"] and [s["episode"] for s in cross[0]["segments"]] == [1, 2], cross[0]
    print("all pipeline checks passed")


if __name__ == "__main__":
    main(sys.argv[1])
