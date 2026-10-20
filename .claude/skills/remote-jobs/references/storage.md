# Local database contract

All commands run from this project root. The only dependency is Python 3.10+
with its standard-library SQLite module. Helper output is JSON; errors use stderr
and a nonzero exit code. `--db PATH` is a global option before the command, useful
for isolated tests. Normal collection uses the default project database.

## Start and inspect

```sh
python3 scripts/jobs.py init
python3 scripts/jobs.py sources list
python3 scripts/jobs.py stats
python3 scripts/jobs.py profile show
python3 scripts/jobs.py run list
```

`init` imports missing Markdown source links; it does not delete removed links,
overwrite source settings, or reset jobs/reviews. To retire a source, disable it.
Additions can be made directly to the database:

```sh
python3 scripts/jobs.py sources add --name 'Example Careers' --url 'https://example.com/careers' --kind company_careers --method discover
python3 scripts/jobs.py sources update SOURCE_ID --method api --notes 'Verified endpoint, pagination, delay and request limits go here.'
python3 scripts/jobs.py sources update SOURCE_ID --enabled 0
```

The source kinds imported from the existing file are `job_board`,
`company_careers`, and `discovery`. Methods are `discover`, `api`, `rss`, `web`,
and `manual`. A method is metadata, not an installed scraper.

## Candidate profile

The dashboard's Your search profile form and the CLI read/write the same profile.
`profile show` returns `profile`, `version`, and `updated_at`. Update only fields
the user requests by supplying a JSON patch:

```sh
python3 scripts/jobs.py profile show
python3 scripts/jobs.py profile update --file data/profile-patch.json
```

Text fields: `name`, `headline`, `about`, `location`, `timezone`,
`work_authorization`, `salary_currency`, `salary_period` (`year`, `month`, `hour`),
`must_haves`, `avoid`, `portfolio_url`, `extra_context`.
Text arrays: `roles`, `skills`, `seniority`, `languages`, `target_locations`,
`employment_types`. Optional nonnegative numbers: `experience_years`, `salary_min`.
Booleans: `fully_remote_required`, `salary_is_requirement`. Empty defaults do not
establish candidate preferences, worldwide eligibility, or an obligation to filter.

Each `run start` saves the current profile and its version in `profile_snapshot`.
`run show` decodes that snapshot. A later profile edit does not rewrite history.

## Run tracking

```sh
python3 scripts/jobs.py run start --request 'Collect broadly; 20 minute budget'
python3 scripts/jobs.py run start --request 'Inspect one source' --source SOURCE_ID
```

Use the returned run ID. Omit `--source` to snapshot all currently enabled sources;
repeat it to select several. Jobs may only be ingested against a source planned
in a running run. Record source attempts and finish:

```sh
python3 scripts/jobs.py run source RUN_ID --source SOURCE_ID --status completed --query 'actual first query' --query 'actual second query' --pages 3 --note 'Traversed the available feed for the planned date window.'
python3 scripts/jobs.py run source RUN_ID --source SOURCE_ID --status partial --query 'actual query' --pages 2 --note 'Next page remained when the time budget ended.'
python3 scripts/jobs.py run finish RUN_ID --note 'Summary of remaining gaps'
python3 scripts/jobs.py run show RUN_ID
```

Source statuses: `completed`, `partial`, `blocked`, `failed`, `skipped`. Every
incomplete attempt requires a note. Repeated attempts append history. A source
reaching completion on retry has its latest status recorded as completed. The
run is completed only if all planned sources completed; otherwise it is partial.
Use `run finish RUN_ID --failed --note 'reason'` for an aborted run. A completed
source updates its last-success timestamp when the run finishes. Old running
runs remain visible until explicitly resumed or finished; an open run is not a
claim that a process is alive.

## Import jobs

Write one JSON batch per source to a file in the ignored `data/` directory, then:

```sh
python3 scripts/jobs.py ingest --run RUN_ID --source SOURCE_ID --file data/batch.json
```

Input is an array or an object with a `jobs` array. Required job fields are
`title`, `company`, and `source_url`. The following is a synthetic format example,
not a real vacancy to insert into the user's database:

```json
{
  "jobs": [
    {
      "title": "Customer Support Specialist",
      "company": "Example Company",
      "source_url": "https://example.com/jobs/42",
      "canonical_url": "https://example.com/jobs/42",
      "description": "Original cleaned job description, when fetched.",
      "location_text": "Remote; employer lists eligible countries separately",
      "eligible_countries": null,
      "remote_status": "remote",
      "employment_type": "full_time",
      "salary_min": null,
      "salary_max": null,
      "salary_currency": null,
      "salary_period": null,
      "salary_text": null,
      "availability": "unknown",
      "evidence": "Exact source wording supporting remote status and restrictions."
    }
  ]
}
```

Other accepted fields: `timezone_requirements`, `seniority`, `published_at`
(ISO date or datetime), `employer_job_id`, `source_job_id` (text IDs), and `skills`
(array of text). `eligible_countries` is also an array of text when known. Omit
unknown values or use null. `remote_status`: `remote`, `hybrid`, `onsite`,
`unknown`. `availability`: `open`, `closed`, `unknown`.

Salary bounds must be nonnegative numbers, currency a three-letter uppercase
code, and period explicit in text (for example `year`, `month`, or `hour`). Keep
original pay wording in `salary_text`; do not turn missing pay into zero.

Unknown fields are rejected. Never pass review fields to ingest. The entire batch
is transactional, including sightings. Repeated canonical URLs update one job;
each source/run/URL sighting is retained. Missing, empty or unknown observations
do not erase previously known fields. Raw input is kept in sightings so conflicts
remain inspectable. Review notes/status/tags live separately and are untouched.

Use canonical employer job URLs only after verification; otherwise use the
source URL. Normalization removes known tracking parameters, preserves meaningful
query parameters and hash routes, and keeps distinct URLs distinct. Cross-source
duplicates without a confirmed common canonical URL remain separate; fuzzy merges
are deliberately not automatic. A changed canonical identity for an already-seen
source URL needs manual resolution before import.

## Review, export and back up

```sh
python3 scripts/jobs.py list --limit 50 --offset 0
python3 scripts/jobs.py list --search support --remote remote --status new
python3 scripts/jobs.py review JOB_ID --status shortlisted --notes 'User-provided note' --tag support
python3 scripts/jobs.py review JOB_ID --reviewed 1
python3 scripts/jobs.py export --file exports/jobs.json
python3 scripts/jobs.py export --file exports/jobs.csv
python3 scripts/jobs.py backup --file backups/jobs-2026-10-20.sqlite3
```

`--tag` replaces the whole tag list with the supplied repeated values. Listing
and exports include review fields. JSON export also includes sources, runs,
sightings, reviews, candidate profiles, and job comments. CSV includes all jobs (not just the first listing page)
and neutralizes spreadsheet-formula prefixes in scraped text.

Use a new backup/export filename each time. Backups use SQLite's backup API to
include committed WAL data; do not copy only the main file during active writes.
The database, WAL sidecars, batches, exports and backups are ignored by Git. The
helper uses WAL, a busy timeout, and serialized write transactions. This protects
database writes; it is not a lock on the agent's network collection work.
