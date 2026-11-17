# Remote job inbox

Use `.claude/skills/remote-jobs/SKILL.md` for job searches, source management,
stored-job review, and coverage reports. The local store is `data/jobs.sqlite3`.
Initialize and access it through `python3 scripts/jobs.py`.

- Keep user reviews separate from collected job facts; collection must preserve
  notes, tags and application statuses.
- Read `python3 scripts/jobs.py profile show` before every search. New runs save a
  profile snapshot; use it for the run, and apply later edits to the next search.
- The local dashboard at `http://127.0.0.1:8765` runs as a macOS launch agent
  (`python3 scripts/dashboard_service.py install|status|uninstall`): it starts at
  login and restarts after any exit, logging to `data/dashboard.log`. Without it,
  run `python3 scripts/serve.py`. Browser writes go through the local API to the same file.
- `remote-job-links.md` contains the original source leads. Initialization imports
  missing links without changing existing source settings; unverified methods
  remain `discover` until checked. `edtech-careers.csv`,
  `startup-support-orgs.csv`, `b2b-saas-careers.csv` and `job-sources.csv`
  (all other sources) back up
  the saved sources and are imported the same way; run
  `python3 scripts/jobs.py sources sync-lists` after changing any source.
- The database, batches, backups and exports are local data ignored by Git.
- Manual and scheduled collection must use this same local project directory.
  A cloud checkout cannot access this MacBook's database.
- Verify changes to storage with `python3 -m unittest discover -s tests -v`.
