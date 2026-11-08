"""Write every step of a run into one Excel workbook, one tab per step."""
from __future__ import annotations

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .flow import promo_script_rows
from .parse import fmt
from .rules import cliff_score, hook_score

F = "Arial"
HEAD = PatternFill("solid", fgColor="1F3A5F")
INPUT = PatternFill("solid", fgColor="FFF2CC")
ROLE_FILL = {"HOOK": "E2F0D9", "CLIFFHANGER": "FCE4D6", "CUT": "D9D9D9"}


def _sheet(wb, title, headers, widths):
    ws = wb.create_sheet(title)
    ws.append(headers)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
        c = ws.cell(row=1, column=i)
        c.font = Font(name=F, bold=True, color="FFFFFF")
        c.fill = HEAD
        c.alignment = Alignment(wrap_text=True, vertical="center")
    ws.freeze_panes = "A2"
    return ws


def _finish(ws):
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font = Font(name=F, size=10, bold=c.font.bold)
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.auto_filter.ref = ws.dimensions


def seg_str(p):
    return " + ".join(f"Ep{s['episode']} {fmt(s['start'])}-{fmt(s['end'])}" for s in p["segments"])


def write_workbook(path, show, tag, eps_list, proposals, ranked, eps, best_per_ep):
    wb = Workbook()
    wb.remove(wb.active)

    # 0. how to read
    ws = _sheet(wb, "0_How_to_read", ["Item", "Explanation"], [26, 110])
    rows = [
        ("Show", show), ("Model", tag), ("Episodes", len(eps_list)), ("Promos kept", len(ranked)),
        ("Step 1: Parsed_Script", "Every timed shot / action / sound / line from the screenplays, with the rule-based hook and cliffhanger score of each line (0-1)."),
        ("Step 2: Candidates", "Every promo proposed (by the model, or by rules), and what happened to it: kept, rejected (why), near-duplicate, or a repaired variant."),
        ("Step 3: Flow_Checks", "Does it feel like ONE story? Code checks joins (reply at a cut, question cut from its answer, no shared character, story order, injury continuity) and references set up before the clip. Repairs start the clip earlier. With a model, a 'stranger test' rates if a new viewer can follow it."),
        ("Step 4: Scores", "Final score = weighted sum of hook, cliffhanger, standalone, flow, funnel minus penalties. Weights are the yellow cells below; change them and the Score column recalculates."),
        ("Step 5: Promo_Scripts", "Line-by-line script of the top promos: clip time, source episode + timestamp, role (HOOK / BUILD / CLIFFHANGER / CUT)."),
        ("Step 6: Best_Per_Episode", "The single best promo found in each episode."),
        ("Step 7: Top_3", "The three promos to post first, and why."),
        ("Funnel (assumption)", "New viewers start at episode 1, so promos from episodes 1-10 score 1.0, falling to 0.3 by the last episode."),
    ]
    for r in rows:
        ws.append(list(r))
    ws.append([])
    ws.append(["WEIGHTS (edit the yellow cells)", ""])
    ws.cell(row=ws.max_row, column=1).font = Font(name=F, bold=True)
    weights = [("w_hook", 0.25), ("w_cliffhanger", 0.25), ("w_standalone", 0.15), ("w_flow", 0.20), ("w_funnel", 0.15)]
    wrow = {}
    for name, v in weights:
        ws.append([name, v])
        wrow[name] = ws.max_row
        ws.cell(row=ws.max_row, column=2).fill = INPUT
    ws.append(["weights total", f"=SUM(B{wrow['w_hook']}:B{wrow['w_funnel']})"])
    _finish(ws)

    # 1. parsed script
    ws = _sheet(wb, "1_Parsed_Script", ["Episode", "Start", "End", "Type", "Speaker", "Tone", "Text", "Hook score", "Cliff score"],
                [8, 9, 9, 10, 18, 22, 80, 10, 10])
    for ep in eps_list:
        for e in ep.events:
            ws.append([ep.num, fmt(e.start), fmt(e.end), e.kind, e.speaker, e.tone, e.text,
                       round(hook_score(e), 2), round(cliff_score(e, ep.duration), 2)])
    _finish(ws)

    # 2. candidates
    ws = _sheet(wb, "2_Candidates", ["Candidate", "Source", "Segments", "Duration (s)", "Status", "Reason / fixes", "Hook", "Cliffhanger"],
                [10, 16, 34, 10, 16, 50, 45, 45])
    kept_ids = {id(p) for p in ranked}
    for i, p in enumerate(proposals, 1):
        p["_cid"] = f"C{i}"
        if not p.get("valid", True):
            status, why = "rejected", "; ".join(p.get("issues", []))
        elif id(p) in kept_ids:
            status, why = f"kept (rank {p['rank']})", "; ".join(p.get("fixes", []))
        elif p.get("dropped"):
            status, why = "dropped", p["dropped"]
        else:
            status, why = "not ranked", ""
        ws.append([p["_cid"], p.get("source", ""), seg_str(p) if p.get("segments") else "", p.get("duration", ""),
                   status, why, p.get("hook", ""), p.get("cliffhanger", "")])
    _finish(ws)

    # 3. flow checks
    ws = _sheet(wb, "3_Flow_Checks", ["Rank", "Candidate", "Segments", "Pieces", "Join / flow flags", "Set up before the clip (dangling)",
                                      "Rule flow (0-1)", "Stranger: follow (1-10)", "Stranger: feels random?", "Stranger: confusing",
                                      "Open question left", "Fix applied"],
                [7, 10, 34, 7, 50, 50, 10, 11, 11, 40, 40, 40])
    for p in ranked:
        fl, st = p.get("flow", {}), p.get("stranger") or {}
        dang = "; ".join(f"'{d['word']}' set up at {fmt(d['setup_at'])}" for d in fl.get("dangling", []))
        ws.append([p["rank"], p.get("_cid", ""), seg_str(p), len(p["segments"]), "; ".join(fl.get("flags", [])) or "none",
                   dang or "none", fl.get("rule_flow", ""), st.get("follow", ""), st.get("feels_random", ""),
                   "; ".join(st.get("confusing", []) or []), st.get("open_question", ""), "; ".join(p.get("fixes", []))])
    _finish(ws)

    # 4. scores (live formulas)
    ws = _sheet(wb, "4_Scores", ["Rank", "Candidate", "Segments", "Source", "Hook", "Cliffhanger", "Standalone", "Flow", "Funnel",
                                 "Penalty", "Score (formula)", "Rule hook", "Rule cliff", "Model hook /10", "Model cliff /10"],
                [7, 10, 34, 14, 8, 10, 10, 8, 8, 8, 12, 9, 9, 10, 10])
    W = "'0_How_to_read'!$B$"
    for p in ranked:
        c, r, sc = p["components"], p["rule_scores"], p.get("scores") or {}
        ws.append([p["rank"], p.get("_cid", ""), seg_str(p), p["source"], c["hook"], c["cliffhanger"], c["standalone"],
                   c["flow"], c["funnel"], c["penalty"], None, r["hook"], r["cliff"], sc.get("hook", ""), sc.get("cliffhanger", "")])
        n = ws.max_row
        ws.cell(row=n, column=11).value = (f"=ROUND(E{n}*{W}{wrow['w_hook']}+F{n}*{W}{wrow['w_cliffhanger']}+G{n}*{W}{wrow['w_standalone']}"
                                           f"+H{n}*{W}{wrow['w_flow']}+I{n}*{W}{wrow['w_funnel']}-J{n},3)")
    _finish(ws)

    # 5. promo scripts for the top 15
    ws = _sheet(wb, "5_Promo_Scripts", ["Rank", "Clip time", "Episode", "Source time", "Role", "Who", "Line / shot"],
                [7, 9, 8, 10, 13, 22, 95])
    for p in ranked[:15]:
        ws.append([p["rank"], "", "", "", "PROMO", seg_str(p), f"{p['duration']:.0f}s · {p.get('why', '')}"])
        for cell in ws[ws.max_row]:
            cell.font = Font(name=F, bold=True)
        for rr in promo_script_rows(p, eps):
            ws.append([p["rank"], rr["clip_time"], rr["episode"], rr["source_time"], rr["role"], rr["who"], rr["text"]])
            if rr["role"] in ROLE_FILL:
                ws.cell(row=ws.max_row, column=5).fill = PatternFill("solid", fgColor=ROLE_FILL[rr["role"]])
        ws.append([])
    _finish(ws)

    # 6. best per episode
    ws = _sheet(wb, "6_Best_Per_Episode", ["Episode", "Overall rank", "Score", "Segments", "Duration (s)", "Hook", "Cliffhanger",
                                           "Why it works", "Flow flags"],
                [8, 9, 8, 34, 9, 45, 45, 45, 40])
    for n in sorted(best_per_ep):
        p = best_per_ep[n]
        ws.append([n, p["rank"], p["score"], seg_str(p), p["duration"], p.get("hook", ""), p.get("cliffhanger", ""),
                   p.get("why", ""), "; ".join(p.get("flow", {}).get("flags", [])) or "none"])
    _finish(ws)

    # 7. top 3
    ws = _sheet(wb, "7_Top_3", ["Post order", "Segments", "Duration (s)", "Hook", "Cliffhanger", "Why it works",
                                "Why it ranks here", "Editor verdict (keep / maybe / reject)", "Editor note"],
                [8, 34, 9, 45, 45, 45, 50, 14, 30])
    for p in ranked[:3]:
        c = p["components"]
        reasons = [f"hook {c['hook']}", f"cliffhanger {c['cliffhanger']}", f"flow {c['flow']}", f"funnel {c['funnel']}"]
        ws.append([p["rank"], seg_str(p), p["duration"], p.get("hook", ""), p.get("cliffhanger", ""), p.get("why", ""),
                   ", ".join(reasons), "", ""])
        ws.cell(row=ws.max_row, column=8).fill = INPUT
        ws.cell(row=ws.max_row, column=9).fill = INPUT
    _finish(ws)
    wb.save(path)
