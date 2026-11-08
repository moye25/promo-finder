# Promo Finder (Kuku AI Generalist Assignment, Problem 1)

Reads a microdrama's timed screenplays and finds 20-60 second promo clips that open on a hook, end on a
cliffhanger and make sense to a stranger. It returns exact episode + start/end timestamps for every segment,
ranked.

## How it works

```
screenplays ─► parse (code) ─► propose (LLM, per episode) ─► validate (code) ─► flow checks + repair (code)
           ─► cross-episode joins (LLM) ─► stranger test on the shortlist (LLM) ─► score + rank (code) ─► outputs
```

| Step | Who does it | Why |
|---|---|---|
| Parse scripts into timed lines/shots | Code | Scripts are already structured; rules are free and exact |
| Pick moments, write hook / cliffhanger / why | LLM | Needs story judgement |
| Check timestamps exist, snap cuts to line boundaries, 20-60s, hook in first 3s | Code | LLMs invent or misplace timestamps |
| Independent hook/cliffhanger check from keywords and tone tags | Code | Catches a model calling a quiet moment a "hook" |
| Flow: joins share a character, story order, no reply at a cut, no question cut from its answer, no reference set up outside the clip, injuries match | Code | Cheap, explainable, catches "random parts glued together" |
| Stranger test: a model sees ONLY the clip and says if a new viewer can follow it | LLM (top N only) | Catches meaning gaps code can't ("the garden" vs "the briars") |
| Ranking | Code formula over LLM scores + rule scores + "funnel" (earlier episodes rank higher) | Transparent and tunable |
| Final yes/no | Human editor, via `review_sheet.csv` | The tool shortlists; the editor approves |

## App (recommended way to demo)

```bash
pip install -r requirements.txt
streamlit run app.py
```
Upload the episode .txt files → **Top 3**, **Per episode** (best promo per episode + alternatives),
**Pick your own** (choose start/end lines, the tool checks and scores your pick), **All promos**, **Downloads**.
Deploy free on Streamlit Community Cloud; add a free `GEMINI_API_KEY` in the app's Secrets.

## Command line

```bash
pip install requests
# 1) no AI, offline baseline
python run.py --input "input/<show folder>" --provider rules
# 2) with a free Gemini key (https://aistudio.google.com/apikey)
export GEMINI_API_KEY=...
python run.py --input "input/<show folder>" --provider gemini
# swap models with one flag
python run.py --input "input/<show folder>" --provider groq --model llama-3.3-70b-versatile
python run.py --input "input/<show folder>" --provider anthropic --model claude-sonnet-4-5
# any OpenAI-compatible gateway (e.g. one Kuku provides)
LLM_BASE_URL=https://... LLM_API_KEY=... LLM_MODEL=... python run.py --input ... --provider custom
```

Options: `--episodes 1-10` (subset), `--per-episode 3`, `--no-cross`, `--rpm 8` (free-tier rate limit).

Test without any key: `python -m tests.test_pipeline "input/<show folder>"`

## Outputs (`outputs/<provider-model>/`)

- `promo_finder_steps.xlsx`: one tab per step: parsed script → candidates → flow checks → scores (live formulas,
  editable weights) → promo scripts → best per episode → top 3
- `report.md`: ranked promos with segments, hook, cliffhanger, why, and score breakdown; "post these first"
- `promos.json` / `promos_ranked.csv`: same data, machine-readable
- `quality.md`: automatic checks (timestamp fixes, rejections and why, hook/cliffhanger disagreements, coverage)
- `review_sheet.csv`: top 30 for an editor to mark keep/maybe/reject → `python evaluate.py <sheet>` gives precision@5/10/30
- `raw/`: the unedited model response for every call (also a cache: reruns cost nothing)
- `rejected.json`: every proposal the validator threw out, with the reason

## Input format

One `.txt` per episode with `EPISODE NUMBER`, `EPISODE LOGLINE`, `CONTINUITY NOTES` and blocks of
`**CAMERA:** / **ACTION:** / **SOUND FX:** / **NAME (Tone):**` followed by `Start: (MM:SS.ss) | End: (MM:SS.ss)`.

> The input pack is Kuku's confidential content: `input/` is git-ignored and must not be published.
