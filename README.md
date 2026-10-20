# Local remote-job inbox

A project-scoped Claude skill collects jobs into a SQLite file on this MacBook.
The skill handles search planning and available web tools; a Python helper handles
validated storage, deduplication, review records, source registration and coverage.
No database server, MCP database server, Artifact storage, or Python packages are
required. A scraping connector remains optional for sources that need it.

```text
Manual request or local scheduled task
  -> .claude/skills/remote-jobs/SKILL.md
  -> available web search / fetch / official feeds and APIs
  -> scripts/jobs.py (validation and transactional writes)
  -> data/jobs.sqlite3
  -> local Kanban dashboard, manual review, CSV/JSON export
```

## Start

Requires Python 3.10+ with SQLite. Run from this directory:

```sh
python3 scripts/jobs.py init
python3 scripts/jobs.py sources list
python3 scripts/jobs.py stats
python3 scripts/serve.py
```

Open **http://127.0.0.1:8765**. The server listens only on this MacBook's loopback
interface. Stop it with Ctrl+C; restart with the same command. No npm install or
database server is needed.

The Notion-inspired board supports drag-and-drop statuses, an accessible status
selector in job details, reviewed/unreviewed markers, auto-saving private notes,
dated comments, search and review filters, a list view, manual job entry, CSV
export, source switches, and search history. Your search profile form saves to
SQLite; the skill reads it before each new search. Every run snapshots the
profile version it used, so later edits don't rewrite search history.

The first startup migrates version 1 databases without discarding existing jobs
or notes. CLI `init` performs the same migration. Browser tests use isolated
temporary databases, and no sample jobs are seeded into the personal store.

Open this project in Claude Code and invoke:

```text
/remote-jobs search for remote customer support and operations jobs; spend up to 20 minutes
/remote-jobs sources
/remote-jobs review new jobs
/remote-jobs coverage
```

The first example's job preferences are illustrative; no role or country
restrictions are saved by default. The original 11 source links start as unverified
leads; LinkedIn is also registered for native alerts or user-provided listings.
The helper does not scrape websites or install a connector. The skill
chooses among the web tools actually available in its Claude session.

## Local storage

`data/jobs.sqlite3` is the durable source of truth. Jobs, source sightings,
sources, search runs and coverage attempts are separate from your reviews.
The helper defaults to this project's database even when called from another
working directory. It returns JSON for both manual use and agent orchestration.

The database and generated files stay out of Git; a fresh clone has no job data.
WAL allows concurrent readers; transactions serialize writes and reject invalid
batches atomically. Database concurrency does not prevent two agents from doing
the same web search, so inspect existing running searches before collecting.

```sh
python3 scripts/jobs.py list --limit 50
python3 scripts/jobs.py list --search support --status new
python3 scripts/jobs.py export --file exports/jobs.csv
python3 scripts/jobs.py backup --file backups/jobs-2026-10-20.sqlite3
```

Use new filenames for each export or backup. Back up with the helper rather than
copying a live SQLite file without its WAL. Files are created with private
permissions; the database is not encrypted by this application.

The detailed commands and import format live in the skill's
[storage contract](.claude/skills/remote-jobs/references/storage.md).

## Scheduling and dashboard

Use a Claude Desktop **Local** scheduled task pointed at this exact project,
with a self-contained `/remote-jobs search ...` request and budget. Local tasks
depend on the MacBook and required app being available. A cloud routine gets a
different checkout and cannot see this ignored file. No task is scheduled yet.
See [Claude's local task documentation](https://code.claude.com/docs/en/desktop).

The dashboard runs on localhost through `scripts/serve.py` and uses the same
SQLite file as the skill. Scraping updates job facts; dashboard actions update
reviews and comments. The dashboard's Search with Claude control copies a skill
command to paste into Claude Code; it does not launch a hidden search process.

Profile fields include background, target roles, skills, seniority, languages,
location, authorization, timezone, employment types, pay preferences, requirements,
and extra context. Read it from the CLI with `python3 scripts/jobs.py profile show`.
To change selected fields through the CLI, use
`python3 scripts/jobs.py profile update --file path/to/patch.json`.

## Verification

```sh
python3 -m unittest discover -s tests -v
```

Tests use temporary databases, never the personal store. They verify persistence
and coverage behavior, HTTP writes, profile snapshots, and version 1 migration.
`tests/dashboard-browser.cjs` additionally checks browser interactions and responsive
layouts when Playwright and Chrome are available (`node tests/dashboard-browser.cjs`).
Actual Claude web-search recall and scheduled execution need separate live
validation in the target Claude environment.
