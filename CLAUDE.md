# Remote job inbox

Use `.claude/skills/remote-jobs/SKILL.md` for job searches, source management,
stored-job review, and coverage reports. The local store is `data/jobs.sqlite3`.
Initialize and access it through `python3 scripts/jobs.py`.

- Keep user reviews separate from collected job facts; collection must preserve
  notes, tags and application statuses.
- Read `python3 scripts/jobs.py profile show` before every search. New runs save a
  profile snapshot; use it for the run, and apply later edits to the next search.
- Start the local dashboard with `python3 scripts/serve.py` at
  `http://127.0.0.1:8765`. Browser writes go through the local API to the same file.
- `remote-job-links.md` contains the original source leads. Initialization imports
  missing links without changing existing source settings; unverified methods
  remain `discover` until checked.
- The database, batches, backups and exports are local data ignored by Git.
- Manual and scheduled collection must use this same local project directory.
  A cloud checkout cannot access this MacBook's database.
- Verify changes to storage with `python3 -m unittest discover -s tests -v`.
