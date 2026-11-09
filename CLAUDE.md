# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project context

Wintermute is an archived (2016, Georgia Tech OMSCS CS 7637 / KBAI) conversational Slack bot. It is a legacy snapshot preserved "for posterity" — expect Python 2.7 syntax (`print` statements, `httplib`, `urlparse`, `dict.has_key`) and pinned 2016-era dependencies (`requirements.txt`, `runtime.txt` = python-2.7.12). Do not "modernize" to Python 3 unless asked. There are no tests, linters, or build steps.

## Running

The bot is an [slackhq/python-rtmbot](https://github.com/slackhq/python-rtmbot) (v0.4.0) worker originally hosted on Heroku:

- Production: `Procfile` → `rtmbot` (reads `rtmbot.conf`)
- Local dev: `./rundev` → `heroku local worker --procfile Procfile.dev` → `rtmbot -c ./rtmbot.dev`

Neither `rtmbot.conf` nor `rtmbot.dev` is committed; they hold the Slack token and the list of active plugins (e.g. `plugins.wintermute.WintermuteDirectPlugin`). Required env vars (Heroku `.env`, gitignored):

- `GRAPHENEDB_BOLT_URL`, `GRAPHENEDB_BOLT_USER`, `GRAPHENEDB_BOLT_PASSWORD` — Neo4j (Bolt) connection
- `GOOGLE_KGSEARCH_API_KEY` — Google Knowledge Graph Search fallback

`assets/seed/graph.tar.gz` is a pre-populated Neo4j `graph.db` directory that can be restored instead of building the KB from scratch.

## Architecture

Nearly all logic lives in `plugins/wintermute.py` (`plugins/logger.py` is a trivial event-dump plugin).

**Import-time side effect:** the module ends with `_kb = WintermuteKnowledgebase()`. Importing the module connects to Neo4j, downloads the Schema.org hierarchy (schema.link.fish), and — on first run — loads FrameNet XML from `assets/framenet/` (frames, LUs, `frRelation.xml`), the GT course catalog (GatechVIP GitHub JSON), OMSCS course surveys (gt-surveyor Firebase), and specializations into the graph. Each loader is skipped once its flag is set on the singleton `(:WMKBStatus)` node; delete that property to force a reload. Paths like `./assets/framenet/frame` are relative to the repo root, so the bot must be run from there.

**Message flow:**
1. `WintermuteDirectPlugin.process_message` handles DM channels (`D…`); `WintermuteGeneralPlugin` just replies with a "DM me" stub for public channels (`C…`).
2. `WintermuteAgent.process_input` builds a `WintermuteContext` keyed by Slack channel ID (persisted in Neo4j via `load_context`/`save_context`) and wraps everything in a catch-all that returns a "please rephrase" message — uncomment the `raise` there to see real tracebacks.
3. `WintermuteContext.parse_input`: strip Slack emoji markup → Pattern `parsetree` → profanity check (raises `WintermuteProfanityBlock`) → `extract_terms` (maps Penn Treebank tags to FrameNet POS lemmata like `learn.v`, special-cases `CS 7637`-style course bigrams) → `resolve_terms` (Cypher query finding the common ancestor frame over `Inheritance`/`Reframing_Mapping` relations, then maps chunks to frame elements) → `retrieve_subject_matter` (topic lookup for NP chunks).
4. `create_response` tries handlers in order, first non-`None` wins: `_create_response_debug` (`what are my topics`, `setcfg <k> <v>`, `getcfg`) → `_create_response_simple` (Pattern `match()` canned patterns like `[what] be {*}`) → `_create_response_frame` (mostly a TODO; reports the frame when `setcfg debug true`) → `_create_response_chunks`.

**Knowledge lookup** (`WintermuteKnowledgebase.find_topics`): local Neo4j → DBpedia Lookup → Google KG Search. Results are saved back as nodes with an `(:Alias)-[:alias]->` link from the search string, then re-queried locally. Nodes are labeled with their full Schema.org ancestry (e.g. `Thing:CreativeWork:Course`); `Course` and `CourseSurvey` are custom extensions added in `_init_schema_org`.

**Domain classes:** `WintermuteThing` subclasses (one per Schema.org type: `WintermuteCourse`, `WintermuteBook`, …) each override `get_response()` to format a reply; `FN*` classes model FrameNet frames/FEs/LUs. To add a new response type, add a subclass and dispatch on its label in `_create_response_chunks`.

## Reference material

`README.md` contains a FrameNet 1.7 cheat sheet (top-level frames, phrase types, grammatical functions, frame relations). The FrameNet book and XML docs are in `assets/framenet/docs/`.
