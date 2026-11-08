"""Promo Finder app: upload a show's screenplays, get one promo per episode, top 3, or pick your own.

  streamlit run app.py
Deploy free on Streamlit Community Cloud; put a free key in Secrets (e.g. GEMINI_API_KEY) so it runs without typing one.
"""
from __future__ import annotations

import io
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from engine import run_show, seg_str, write_outputs
from flow import cast_tokens, check_flow, promo_script_rows
from llm import LLM, PROVIDERS
from parse import fmt, parse_episode
from pipeline import score, stranger_test, validate

st.set_page_config(page_title="Promo Finder", layout="wide")
st.title("Promo Finder")
st.caption("Finds 20-60s promos in a microdrama: a hook in the first 3 seconds, a cliffhanger ending, and one clear story.")

# ---------------------------------------------------------------- sidebar: model
with st.sidebar:
    st.header("Model")
    names = ["rules (no AI)", "gemini", "groq", "openrouter", "anthropic", "openai", "ollama", "custom"]
    provider = st.selectbox("Provider", names, help="Swap the model here; nothing else changes.")
    default_model = "" if provider.startswith("rules") else ("claude-sonnet-4-5" if provider == "anthropic" else PROVIDERS[provider][2])
    model = st.text_input("Model", value=default_model, disabled=provider.startswith("rules"))
    env = None if provider.startswith("rules") else ("ANTHROPIC_API_KEY" if provider == "anthropic" else PROVIDERS[provider][1])
    try:
        secret_key = st.secrets.get(env, "") if env else ""
    except Exception:  # noqa: BLE001  (no secrets file)
        secret_key = ""
    key = st.text_input("API key", type="password", value="", disabled=provider.startswith("rules") or provider == "ollama",
                        help="Used only for this session. Leave empty to use the app's own free key, if configured.")
    st.header("Options")
    per_episode = st.slider("Promos to look for per episode", 1, 5, 3)
    do_cross = st.checkbox("Also join moments across episodes", value=True)
    stranger_top = st.slider("Stranger test on top N (AI only)", 0, 50, 15,
                             help="A second model call sees ONLY the clip and says if a new viewer can follow it.")
    rpm = st.number_input("Max AI calls per minute", 1, 60, 10)


def make_llm():
    if provider.startswith("rules"):
        return None
    return LLM(provider, model or None, min_interval=60 / rpm, api_key=key or secret_key or None)


# ---------------------------------------------------------------- input
files = st.file_uploader("Episode screenplays (.txt, one per episode)", type="txt", accept_multiple_files=True)
if st.button("Find promos", type="primary", disabled=not files):
    work = Path(tempfile.mkdtemp())
    (work / "in").mkdir()
    for f in files:
        (work / "in" / Path(f.name).name).write_bytes(f.getvalue())
    try:
        eps_list = sorted((parse_episode(p) for p in (work / "in").glob("*.txt")), key=lambda e: e.num)
    except Exception as e:  # noqa: BLE001
        st.error(f"Could not read a screenplay: {e}")
        st.stop()
    try:
        llm = make_llm()
    except SystemExit as e:
        st.error(str(e))
        st.stop()
    tag = "rules" if llm is None else f"{llm.provider}-{llm.model}".replace("/", "_").replace(":", "_")
    out = work / "out"
    with st.status(f"Running on {len(eps_list)} episodes with {tag} ...", expanded=True) as box:
        res = run_show(eps_list, llm, out, per_episode, do_cross, stranger_top, log=box.write)
        write_outputs(res, out, files[0].name.rsplit(".", 1)[0], tag, eps_list)
        box.update(label=f"Done: {len(res['ranked'])} promos", state="complete")
    st.session_state.update(res=res, out=str(out), tag=tag, picks=[])

if "res" not in st.session_state:
    st.info("Upload the episode .txt files and press **Find promos**. Try 'rules (no AI)' first: it needs no key.")
    st.stop()

res, eps, out = st.session_state["res"], st.session_state["res"]["eps"], Path(st.session_state["out"])
ranked = res["ranked"]


def show_promo(p, expanded=False):
    c = p["components"]
    title = f"#{p.get('rank', '-')} · {seg_str(p)} · {p['duration']:.0f}s · score {p['score']:.2f}"
    with st.expander(title, expanded=expanded):
        a, b = st.columns(2)
        a.markdown(f"**Hook:** {p.get('hook', '')}")
        a.markdown(f"**Cliffhanger:** {p.get('cliffhanger', '')}")
        a.markdown(f"**Why it works:** {p.get('why', '')}")
        b.markdown(f"hook **{c['hook']}** · cliffhanger **{c['cliffhanger']}** · standalone **{c['standalone']}** · "
                   f"flow **{c['flow']}** · funnel **{c['funnel']}**" + (f" · penalty −{c['penalty']}" if c["penalty"] else ""))
        fl = p.get("flow", {})
        if fl.get("flags"):
            b.warning("Flow: " + "; ".join(fl["flags"]))
        if fl.get("dangling"):
            b.info("Mentions something set up before the clip: " + ", ".join(f"'{d['word']}'" for d in fl["dangling"]))
        stt = p.get("stranger") or {}
        if stt.get("open_question"):
            b.markdown(f"**Leaves a new viewer asking:** {stt['open_question']}  (follow {stt.get('follow')}/10)")
        if p.get("fixes"):
            b.caption("; ".join(p["fixes"]))
        st.dataframe(pd.DataFrame(promo_script_rows(p, eps)), hide_index=True, use_container_width=True)


view = st.radio("View", ["Top 3", "Per episode", "Pick your own", "All promos", "Downloads"], horizontal=True,
                key="view", label_visibility="collapsed")

if view == "Top 3":
    st.subheader("Post these first")
    for p in ranked[:3]:
        show_promo(p, expanded=True)

if view == "Per episode":
    ep_num = st.selectbox("Episode", sorted(eps), format_func=lambda n: f"Episode {n}: {eps[n].logline[:90]}")
    mine = [p for p in ranked if p["segments"][0]["episode"] == ep_num]
    if not mine:
        st.write("No promo passed the checks for this episode.")
    for i, p in enumerate(mine):
        if i == 0:
            st.markdown("**Best promo for this episode**")
        elif i == 1:
            st.markdown("**Alternatives**")
        show_promo(p, expanded=(i == 0))

if view == "Pick your own":
    st.write("Choose your own cut points; the tool checks length, cut points and flow, and scores it like its own picks.")
    cast = cast_tokens(eps)

    def piece_picker(label, min_ep=None):
        c1, c2, c3 = st.columns([1, 2, 2])
        epn = c1.selectbox(f"{label}: episode", [n for n in sorted(eps) if min_ep is None or n >= min_ep], key=f"{label}e")
        evs = eps[epn].events
        starts = sorted({e.start for e in evs})
        line = lambda t, attr: next((f"{e.speaker or e.kind}: {e.text[:60]}" for e in evs if getattr(e, attr) == t), "")  # noqa: E731
        a = c2.selectbox(f"{label}: start", starts, format_func=lambda t: f"{fmt(t)} · {line(t, 'start')}", key=f"{label}s")
        ends = sorted({e.end for e in evs if e.end > a})
        b = c3.selectbox(f"{label}: end", ends, format_func=lambda t: f"{fmt(t)} · {line(t, 'end')}", key=f"{label}x")
        return {"episode": epn, "start": a, "end": b}

    segs = [piece_picker("Piece 1")]
    if st.checkbox("Add a second piece"):
        segs.append(piece_picker("Piece 2", segs[0]["episode"]))
    note = st.text_input("Why this works (your note)")
    use_ai = st.checkbox("Run the AI stranger test on my pick", value=not provider.startswith("rules"))
    if st.button("Check my promo"):
        first, last = eps[segs[0]["episode"]].window(segs[0]["start"], segs[0]["end"]), \
            eps[segs[-1]["episode"]].window(segs[-1]["start"], segs[-1]["end"])
        p = {"segments": [dict(s) for s in segs], "source": "human", "why": note,
             "hook": (first[0].speaker + ": " if first[0].speaker else "") + first[0].text if first else "",
             "cliffhanger": (last[-1].speaker + ": " if last[-1].speaker else "") + last[-1].text if last else ""}
        validate(p, eps)
        if not p["valid"]:
            st.error("; ".join(p["issues"]))
        else:
            check_flow(p, eps, cast)
            llm = make_llm() if use_ai else None
            if llm:
                stranger_test(p, eps, llm, out / "raw")
            score(p, eps, len(eps))
            better = sum(1 for q in ranked if q["score"] > p["score"])
            st.success(f"Score {p['score']:.2f}: would rank #{better + 1} of {len(ranked) + 1}")
            show_promo(p, expanded=True)
            st.session_state["picks"].append(p)
    if st.session_state.get("picks"):
        st.markdown(f"**Your saved picks:** {len(st.session_state['picks'])}")
        st.download_button("Download my picks (CSV)", pd.DataFrame(
            [{"segments": seg_str(p), "duration": p["duration"], "score": p["score"], "hook": p["hook"],
              "cliffhanger": p["cliffhanger"], "note": p["why"]} for p in st.session_state["picks"]]).to_csv(index=False),
            "my_picks.csv")

if view == "All promos":
    st.dataframe(pd.DataFrame([{"rank": p["rank"], "score": p["score"], "segments": seg_str(p), "secs": p["duration"],
                                "hook": p.get("hook", ""), "cliffhanger": p.get("cliffhanger", ""), "source": p["source"],
                                "flow flags": "; ".join(p.get("flow", {}).get("flags", []))} for p in ranked]),
                 hide_index=True, use_container_width=True)

if view == "Downloads":
    xlsx = out / "promo_finder_steps.xlsx"
    if xlsx.exists():
        st.download_button("Spreadsheet: every step (xlsx)", xlsx.read_bytes(), "promo_finder_steps.xlsx")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for f in out.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(out))
    st.download_button("All raw outputs (zip)", buf.getvalue(), "promo_finder_outputs.zip")
    st.markdown((out / "quality.md").read_text() if (out / "quality.md").exists() else "")
