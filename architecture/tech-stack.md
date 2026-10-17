# Tech Stack

**Status:** Draft

## Problem / Why

Every spec in `new_specs/` has been written stack-agnostic so far, with
several explicitly flagging "depends on the final project stack" as an open
question. Before `backend/` gets scaffolded, the project needs one place that
declares what's actually being built with — a single source of truth every
other spec is implemented against, rather than each one guessing or picking
its own.

## Goals

- Declare the backend/ingestion language and framework.
- Declare the database and how the app talks to it.
- Declare the frontend language and framework.
- Flag every part of the stack that's still undecided, so nobody assumes a
  decision that hasn't actually been made.

## Non-Goals

- Picking a hosting/deployment platform — not decided, not blocking local
  development.
- Picking CI/CD tooling.
- Deciding source-specific library choices owned by individual feature
  specs (e.g. which X API client the Twitter ingestion uses) — this spec
  only sets the language/framework floor those specs build on.

## Decisions

- **Backend / ingestion:** Python, using **FastAPI** for any HTTP API
  surface. Every ingestion source and any background/scheduled job runs as
  Python code within this backend, not a separate service/language.
- **Database:** PostgreSQL.
- **ORM:** SQLAlchemy — models live in a `models/` folder, one module/class
  per table in the schema.
- **Frontend:** React. Language (TypeScript vs. JavaScript) not decided yet
  — see Open Questions.
- **Python environment management:** `uv`. Dependencies declared in
  `pyproject.toml`, pinned in `uv.lock` (committed, so every developer
  resolves the same versions). Run things with `uv run ...` — no manual
  `venv` activation needed. `uv sync` installs/updates the environment
  from the lockfile.
- **Migrations:** none — new project, tables are created directly from the
  SQLAlchemy models rather than via Alembic.
- **Scheduler:** async, in-process, running alongside FastAPI in the same
  process — not a separate worker. Matches
  [scheduler.md](../new_specs/ingestion/scheduler.md)'s Design/Approach.
- **API shape:** REST, confirmed.
- **Testing:** skipped entirely — no test suite.
- **Secrets:** local `.env` confirmed sufficient — no secrets manager.

## Suggested Layout

Left light — exact structure can shift once the open questions below are
resolved, but roughly:

```
backend/
  models/          # SQLAlchemy models, one per db-design.md table
  ingestion/        # news-aggregator, twitter-aggregator, polymarket, kalshi
  scheduler/        # scheduler.md's implementation
  api/              # FastAPI routes
  core/             # config/env loading, db session setup
frontend/
src/
```

## Relationship to Other Docs

This is a source-of-truth doc, not a feature spec — it has no upstream
dependencies on other specs. Direction runs the other way: every spec in
`new_specs/` is implemented against the decisions here, not referenced by
them.

- Depends on nothing in `new_specs/`.
- Referenced by: every ingestion spec, the scheduler, and any future spec
  that touches the backend, database, or frontend.
- [`mock_db_design/db-design.md`](../mock_db_design/db-design.md) is a
  peer doc (schema design, independent of language) — SQLAlchemy models
  should mirror it once implementation starts.

## Open Questions

- **TypeScript vs. JavaScript for the frontend** — explicitly undecided.
- **Node package manager** — `npm`, `pnpm`, or `yarn`? (Tied to the
  TS/JS decision.)
- **Frontend build tooling** — Vite, Next.js, Create React App, something
  else? "React" alone doesn't settle this.
- **Local Postgres** — resolved: local Postgres for development, or any hosted
  Postgres (e.g. Neon) by pointing `DATABASE_URL` at it.

## Acceptance Criteria

- A new developer can read this file and know what to install locally
  (Python version, Node version, Postgres) to start on any spec.
- A single ingestion source can be run end-to-end locally (SQLAlchemy model
  + a migration + the source's poll function writing to Postgres) using
  only the decisions in this file — no stack guesswork required.
