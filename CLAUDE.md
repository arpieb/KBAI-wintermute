# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project context

Wintermute is a 2016 Georgia Tech OMSCS CS 7637 (KBAI) conversational agent, ported in 2026 from Python 2.7 / Neo4j / CLiPS Pattern / python-rtmbot to Python 3 / LadybugDB / spaCy / Slack Bolt. The 2016 implementation is in git history (`plugins/wintermute.py` at commit `e71e157`) and is the reference for intended behavior; bot reply wording is the author's and should be preserved.

## Commands

```sh
uv sync                                   # install (includes the en_core_web_sm spaCy model)
uv run wintermute init                    # build data/wintermute.lbdb (FrameNet + courses), ~20s
uv run wintermute [--debug] [-v|-vv] chat [--context ID]   # terminal chat (default command)
uv run wintermute slack                   # Slack bot; needs SLACK_BOT_TOKEN + SLACK_APP_TOKEN
uv run pytest                             # all tests (~20s; session fixture loads real FrameNet)
uv run pytest tests/test_agent.py::test_frame_resolution
```

`--debug` re-raises exceptions instead of returning the "please rephrase" reply. Env: `WINTERMUTE_DB`, `WINTERMUTE_CACHE`, `WINTERMUTE_REMOTE_LOOKUPS=0`, `WINTERMUTE_GOOGLE_KG=1` + `GOOGLE_KGSEARCH_API_KEY` (see `wintermute/config.py`). To force a dataset reload, delete `data/wintermute.lbdb` (or its `KBStatus` row).

## Architecture

- `wintermute/agent.py` — the front-end-agnostic core. `Agent.respond(text, context_id) -> str` is the only API front ends use. Each call builds a `Conversation` that loads the context (topics + config) by ID, parses, picks a reply, and saves the context.
- `wintermute/cli.py` (terminal REPL, entry point) and `wintermute/slack.py` (Bolt Socket Mode; DMs keyed by channel ID) are thin front ends.
- `wintermute/nlp.py` — spaCy layer replacing Pattern. `parse()` returns `Sentence` objects with `Chunk`s (`NP`, or `NPCD` for "CS 7637" course codes and bare numbers) and `fn_lemmata` mapping FrameNet-style `lemma.pos` keys (Penn tags → FrameNet POS via `FN_TO_PTB`) to tokens. `find_wh_be`/`phrase_for`/`contains_lemmas` stand in for Pattern's `match()` rules.
- `wintermute/kb/` — `Knowledgebase` facade over `Graph` (embedded LadybugDB). Loaders (`loaders.py`) run once each, tracked in the `KBStatus` table: FrameNet XML from `assets/framenet/` is bulk-loaded via CSV `COPY`; course catalog is fetched from GitHub; specializations are hardcoded and must load after courses.

**Turn pipeline:** strip Slack emoji → spaCy parse → profanity check (`wintermute/data/profanity.txt`, from Pattern) → `resolve_terms` (`kb.find_frames`: frames whose Core FEs have valence units from the sentence's LUs, then the deepest node shared by the longest `Inheritance` ancestor paths) → `retrieve_subject_matter` (topic lookup for NP chunks) → reply handlers in order, first non-`None` wins: debug (`what are my topics`, `setcfg k v`, `getcfg`) → simple canned/"what|who|where is X" → frame (stub; reports frame name when `setcfg debug true`) → chunk-topic fallback.

**Topic lookup** (`Knowledgebase.find_topics`): local name/alias match → DBpedia public SPARQL endpoint (exact English label, follows redirects; descriptions come from `dbo:description`; disables itself for the session on first failure) → Google KG (on hold: off unless `WINTERMUTE_GOOGLE_KG=1` and a key are set). A local hit short-circuits remote lookups, so local things need a `description` to produce a reply. Remote hits are saved as `Thing` nodes plus an alias from the query, then re-queried locally so label filtering (`Thing`/`Person`/`Place`) is consistent.

## LadybugDB notes

- Schema is declared up front in `kb/graph.py`. Schema.org multi-labels are a `labels STRING[]` column on `Thing` (filter with `list_contains`); arbitrary properties are a JSON `props` string. `Thing.id` defaults to md5 of labels+name, so `Course.create("CS 7637")` addresses the stored course node.
- Table names are case-insensitive (hence `alias_of`, not `alias`, alongside `Alias`). No Cypher list comprehensions — use `properties(nodes(p), 'ID')`, `list_*` functions. Recursive patterns filter with `-[:rel*1..N (r, _ | WHERE ...)]->`.
- Add new specialised thing types by subclassing `Thing` in `models.py` with `type_labels`; `Thing.from_record` dispatches on the most specific label.

## Known gaps

- `CourseSurvey` ratings come from OMSCentral's public Sanity dataset (`OMSCENTRAL_*` in `kb/loaders.py`; one aggregated GROQ query per KB build, ~10s). It is unofficial and undocumented and could be locked down; keep request volume minimal (apicdn host, no per-turn queries). Course codes come from each record's `codes` array ("CS-7637"), never from `_id`.
- `lookup.dbpedia.org` (the DBpedia Lookup API) refuses connections; the SPARQL endpoint is used instead.
- `assets/seed/graph.tar.gz` is a Neo4j 3.0 store from 2016 and is not used.
- `README.md` contains a FrameNet 1.7 cheat sheet (frames, phrase types, grammatical functions, relations); FrameNet docs are in `assets/framenet/docs/`.
