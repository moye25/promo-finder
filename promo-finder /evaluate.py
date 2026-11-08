"""Score the tool against an editor's judgement.

An editor fills the `editor_verdict` column of review_sheet.csv with keep / maybe / reject.
  python evaluate.py outputs/gemini-gemini-2.5-flash/review_sheet.csv
"""
import csv
import sys

rows = list(csv.DictReader(open(sys.argv[1])))
col = next(c for c in rows[0] if c.startswith("editor_verdict"))
v = [(r[col] or "").strip().lower() for r in rows]
done = [x for x in v if x]
if not done:
    sys.exit("No verdicts filled in yet.")
for k in (5, 10, 30):
    top = [x for x in v[:k] if x]
    if top:
        keep = sum(x == "keep" for x in top)
        soft = sum(x in ("keep", "maybe") for x in top)
        print(f"precision@{k}: {keep / len(top):.0%} keep, {soft / len(top):.0%} keep-or-maybe  (n={len(top)})")
print("rejections with notes:")
for r in rows:
    if (r[col] or "").strip().lower() == "reject":
        print(f"  #{r['rank']} {r['segments']}: {r.get('editor_note', '')}")
