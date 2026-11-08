# Prompt for Lovable: "Promo Finder"

> Paste everything below the line into Lovable as the first message. Turn on Lovable Cloud (for storage and
> edge functions) and Lovable AI (built-in Gemini, no key needed) when it asks.
> Build in this order, one Lovable message per step, and test each before the next:
> 1) upload + parsing, 2) AI promo finding per episode, 3) checks + scoring, 4) results screens,
> 5) pick-your-own, 6) export.

---

Build a web app called **Promo Finder** for a promo editor at a microdrama studio (short vertical series with 1-2 minute
episodes). The editor uploads the screenplays of one show (one .txt file per episode, often 50+). The app finds the
best 20-60 second promo clips in them, shows ONE best promo per episode, recommends the TOP 3 to post first, and lets the
editor build and score their own promo. No video editing: the output is only episode numbers + start/end timestamps
+ a line-by-line "promo script".

## What a good promo is
1. **Hook:** something in the first 3 seconds that stops a scrolling viewer (a slap, a strike, a reveal, a shocking or
   threatening line). The clip must start on it.
2. **Cliffhanger:** it ends on an unresolved moment (a threat, a question, a door opening) and cuts before the reaction.
3. **Standalone:** a stranger who never saw the show understands who is wronged and what is at stake.
4. **Flow:** it feels like ONE story. If it joins 2-3 pieces (from one episode or from different episodes), every piece
   shares a character with the one before, pieces are in story order, no piece opens on a reply to a line that was cut,
   and nothing is mentioned that was only set up outside the clip.
5. **Length:** 20-60 seconds in total.

## Input format (parse with code, not AI)
Each file has header lines `EPISODE NUMBER: Episode 12`, `EPISODE LOGLINE: ...`, `CONTINUITY NOTES: ...`, then blocks:
```
**CAMERA:**                     (or **ACTION:**, **SOUND FX:**, or a speaker like **LADY JULIANA (Fierce, Emotional):**)
Start: (01:05.40) | End: (01:09.20)
"the text of the shot, action, sound or line"
```
Parse every block into an event: episode, start (seconds), end (seconds), type (CAMERA/ACTION/SFX/DIALOGUE),
speaker, tone (text in brackets), text. Show a parse summary after upload: episodes found, events per episode,
episode length. Reject files that don't match, with a clear message.

## Pipeline (edge function; show live progress per episode)
1. **Find (AI, one call per episode).** Send the episode's timed lines as `[MM:SS.ss-MM:SS.ss] WHO (tone): text`,
   plus the show premise (episode 1's logline) and the episode logline. Ask for up to 3 promos as JSON:
   `{"promos":[{"segments":[{"start":"MM:SS.ss","end":"MM:SS.ss"}],"hook":"","hook_time":"MM:SS.ss","cliffhanger":"",
   "why":"one line","scores":{"hook":1-10,"cliffhanger":1-10,"standalone":1-10},"spoiler":"none|minor|major",
   "needs_context":""}]}`. Tell it to use only timestamps that exist, never cut inside a line, and follow the rules above.
2. **Join across episodes (AI, one call).** Give it the 40 best single-episode clips as building blocks (id, episode,
   range, hook, ending) plus all episode loglines; ask for up to 6 promos that join 2-3 blocks from different episodes,
   in story order, at most 5 episodes apart, same JSON plus a `block` id per segment.
3. **Check (code, never AI).** For every promo: snap each start/end to the nearest real event boundary; if a cut falls
   inside a dialogue line, move it to that line's start/end; reject if total length is outside 20-60s (keep the reason);
   flag if the hook lands more than 3s into the clip.
4. **Flow checks (code).** Flag: a joined piece opening on a reply word (No/Yes/Why/What/Him/Her/It/That/But/And/So);
   a question in a middle piece whose answer was cut; a joined piece with no character in common with earlier pieces;
   pieces going backwards in the story or jumping more than 5 episodes; a wound/blood/bandage in one piece that is not
   mentioned in a joined piece from a later episode. Also flag "dangling references": a noun used in the clip ("the
   ledger") that was introduced earlier in the episode but outside the clip; for these, also create a repaired version
   that starts earlier, at the line that introduces it, if it still fits 60s.
5. **Stranger test (AI, top 15 only).** Send ONLY the lines inside the clip (with "--- CUT ---" markers) and ask, as a
   viewer who never saw the show: `{"follow":1-10,"feels_random":true|false,"confusing":[],"open_question":"",
   "would_tap":1-10}`.
6. **Score (code).** Rule scores (0-1) from keywords: hook = action words (slap, strike, blood, chains, scream, kiss,
   gallows, fire...) and angry/shocked tone tags in the first 3s; cliffhanger = "cliffhanger/cut to black/ominous/freezes",
   a question, a threat, or the episode's last 2.5s in the last 5s. Mix each with the AI's 1-10 rating (70% AI, 30% rules).
   Flow = 0.7 x follow/10 + 0.3 x (1 - 0.25 per flag - 0.1 per dangling reference). Funnel = 1.0 for episodes 1-10,
   falling to 0.3 at the last episode (new viewers start at episode 1).
   **Score = 0.25 hook + 0.25 cliffhanger + 0.15 standalone + 0.20 flow + 0.15 funnel - penalties**
   (major spoiler -0.15, hook after 3s -0.10, feels random -0.10). Make the weights editable in Settings.
7. **Rank + dedupe.** Drop a promo that overlaps a higher-scored one by more than 60% in the same episode.

## Screens
- **Upload:** drag-and-drop many .txt files; parse summary; model picker (Lovable AI Gemini by default; also allow an
  OpenAI-compatible base URL + key + model name so a stronger model can be swapped in); "Find promos" button.
- **Top 3:** three cards: segments with timestamps, duration, hook, cliffhanger, why, the open question a new viewer is
  left with, a score bar per part (hook, cliffhanger, standalone, flow, funnel), and flags.
- **Per episode:** a list of all episodes; each shows its best promo, expandable to alternatives.
- **Promo detail:** the promo script as a table: clip time, episode, source timestamp, role (HOOK for the first 3s,
  CLIFFHANGER for the last beat, CUT rows between pieces, BUILD otherwise), who, text. Buttons: Approve / Reject + note.
- **Pick your own:** choose an episode, then click a start line and an end line on that episode's script (shown as a
  scrollable list of timed lines); optionally add a second or third piece; the app runs steps 3-6 on it live and shows
  its score and where it would rank against the AI's picks. "Save as my pick".
- **Export:** download all promos, approvals and editor picks as CSV and JSON.

## Data
Tables: shows, episodes, events, promos (with segments JSON, scores, flags, source = ai/ai-cross/repair/human),
reviews (promo id, verdict keep/maybe/reject, note). Show "precision@10" on the dashboard = share of the top 10 the
editor kept.

Keep the UI clean and fast: a left sidebar for Upload / Top 3 / Episodes / Pick your own / Export.
