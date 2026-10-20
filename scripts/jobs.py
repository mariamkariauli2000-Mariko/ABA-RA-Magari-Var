#!/usr/bin/env python3
"""Project-local job storage. Python standard library only; never fetches the web."""

import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import uuid
from contextlib import contextmanager


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / 'data' / 'jobs.sqlite3'
REVIEW_STATUSES = ('new', 'shortlisted', 'applied', 'interviewing', 'offer', 'rejected', 'archived')
SOURCE_STATUSES = ('completed', 'partial', 'blocked', 'failed', 'skipped')
TEXT_FIELDS = (
    'title', 'company', 'description', 'location_text', 'timezone_requirements',
    'remote_status', 'employment_type', 'seniority', 'salary_currency',
    'salary_period', 'salary_text', 'published_at', 'employer_job_id', 'availability',
)
JOB_FIELDS = set(TEXT_FIELDS) | {
    'source_url', 'canonical_url', 'source_job_id', 'evidence',
    'eligible_countries', 'skills', 'salary_min', 'salary_max',
}
PROFILE_DEFAULTS = {
    'name': '', 'headline': '', 'about': '', 'experience_years': None,
    'roles': [], 'skills': [], 'seniority': [], 'languages': [],
    'location': '', 'timezone': '', 'work_authorization': '',
    'target_locations': [], 'employment_types': [], 'salary_min': None,
    'salary_currency': 'USD', 'salary_period': 'year',
    'fully_remote_required': False, 'salary_is_requirement': False,
    'must_haves': '', 'avoid': '', 'portfolio_url': '', 'extra_context': '',
}


class ConflictError(ValueError):
    pass


def get_profile(db):
    row = db.execute("SELECT * FROM profiles WHERE id = 'default'").fetchone()
    return {'profile': {**PROFILE_DEFAULTS, **(json.loads(row['data_json']) if row else {})},
            'version': row['version'] if row else 0, 'updated_at': row['updated_at'] if row else None}


def save_profile(db, patch, expected_version=None):
    if not isinstance(patch, dict) or set(patch) - set(PROFILE_DEFAULTS):
        raise ValueError('Profile must contain only supported profile fields')
    for key, value in patch.items():
        default = PROFILE_DEFAULTS[key]
        if key in ('salary_min', 'experience_years'):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0):
                raise ValueError(f'{key} must be a nonnegative number or null')
        elif isinstance(default, list):
            if not isinstance(value, list) or len(value) > 100 or any(not isinstance(v, str) or len(v) > 500 for v in value):
                raise ValueError(f'{key} must be an array of text values')
        elif isinstance(default, bool):
            if not isinstance(value, bool):
                raise ValueError(f'{key} must be true or false')
        elif not isinstance(value, str) or len(value) > 20000:
            raise ValueError(f'{key} must be text, at most 20000 characters')
    if 'salary_currency' in patch and not re.fullmatch('[A-Z]{3}', patch['salary_currency']):
        raise ValueError('Salary currency must be a three-letter uppercase code')
    if patch.get('salary_period', 'year') not in ('year', 'month', 'hour'):
        raise ValueError('Salary period must be year, month, or hour')
    if patch.get('portfolio_url'):
        normalize_url(patch['portfolio_url'])
    with write_transaction(db):
        current = get_profile(db)
        if expected_version is not None and current['version'] != expected_version:
            raise ConflictError('Your profile changed in another window. Reload it before saving.')
        merged = {**current['profile'], **patch}
        db.execute("""INSERT INTO profiles VALUES ('default', ?, ?, ?) ON CONFLICT(id) DO UPDATE SET
                      data_json = excluded.data_json, version = excluded.version, updated_at = excluded.updated_at""",
                   (json.dumps(merged, ensure_ascii=False), current['version'] + 1, now()))
    return get_profile(db)


def update_review(db, job_id, *, status=None, notes=None, tags=None, reviewed=None):
    if status is not None and status not in REVIEW_STATUSES:
        raise ValueError('Unknown review status')
    if notes is not None and (not isinstance(notes, str) or len(notes) > 50000):
        raise ValueError('Notes must be text, at most 50000 characters')
    if tags is not None and (not isinstance(tags, list) or any(not isinstance(t, str) for t in tags)):
        raise ValueError('Tags must be an array of text')
    if reviewed is not None and not isinstance(reviewed, bool):
        raise ValueError('reviewed must be true or false')
    with write_transaction(db):
        require_row(db, 'jobs', 'id', job_id)
        row = db.execute('SELECT * FROM reviews WHERE job_id = ?', (job_id,)).fetchone()
        current = dict(row) if row else {'status': 'new', 'notes': '', 'tags_json': '[]', 'reviewed_at': None}
        reviewed_at = current['reviewed_at']
        if reviewed is True or (reviewed is None and (status is not None or notes is not None)):
            reviewed_at = reviewed_at or now()
        elif reviewed is False:
            reviewed_at = None
        db.execute('''INSERT INTO reviews(job_id, status, notes, tags_json, updated_at, reviewed_at)
                      VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(job_id) DO UPDATE SET status = excluded.status,
                      notes = excluded.notes, tags_json = excluded.tags_json, updated_at = excluded.updated_at,
                      reviewed_at = excluded.reviewed_at''',
                   (job_id, status if status is not None else current['status'], notes if notes is not None else current['notes'],
                    json.dumps(tags, ensure_ascii=False) if tags is not None else current['tags_json'], now(), reviewed_at))
    return decode_row(require_row(db, 'reviews', 'job_id', job_id))


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def emit(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False))


def stable_id(prefix, value):
    return prefix + hashlib.sha256(value.encode()).hexdigest()[:24]


@contextmanager
def write_transaction(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


def normalize_url(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('URL must be a nonempty http(s) string')
    parts = urlsplit(value.strip())
    if parts.scheme.lower() not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
        raise ValueError('URL must use http(s), with a host and no embedded credentials')
    # Preserve job-identifying parameters and hash routes; remove only known tracking keys.
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('gclid', 'fbclid', 'msclkid')]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/',
                       urlencode(sorted(query)), parts.fragment))


def require_row(db, table, key, value):
    row = db.execute(f'SELECT * FROM {table} WHERE {key} = ?', (value,)).fetchone()
    if row is None:
        raise ValueError(f'Unknown {table} {key}: {value}')
    return row


def require_running(db, run_id):
    row = require_row(db, 'search_runs', 'id', run_id)
    if row['status'] != 'running':
        raise ValueError('This search run is finished; start a new run')
    return row


def add_source(db, name, url, kind, method='discover', notes=''):
    if not name.strip():
        raise ValueError('Source name must not be empty')
    url = normalize_url(url)
    source_id = stable_id('src_', url)
    db.execute('''INSERT INTO sources(id, name, url, kind, method, notes)
                  VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(url) DO NOTHING''',
               (source_id, name.strip(), url, kind, method, notes))
    return source_id


def initialize(db):
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version == 1:
        db.executescript((ROOT / 'db' / 'migrations' / '002_profiles_and_reviews.sql').read_text())
    db.executescript((ROOT / 'db' / 'schema.sql').read_text())
    seed_file = ROOT / 'remote-job-links.md'
    kind = 'discovery'
    with write_transaction(db):
        for line in seed_file.read_text().splitlines() if seed_file.exists() else []:
            if line.startswith('## '):
                kind = {'Job boards': 'job_board', 'Company career pages': 'company_careers'}.get(line[3:], 'discovery')
            match = re.match(r'^- \[([^]]+)\]\((https?://[^)]+)\)', line)
            if match:
                linkedin = urlsplit(match[2]).hostname in ('linkedin.com', 'www.linkedin.com')
                method = 'manual' if linkedin else 'discover'
                notes = ('LinkedIn: native alerts or user-provided listings; automated scraping is not configured.' if linkedin else
                         'Seeded from remote-job-links.md; collection method not yet verified.')
                add_source(db, match[1], match[2], kind, method=method, notes=notes)
    return {'database': str(Path(db.execute('PRAGMA database_list').fetchone()[2])),
            'schema_version': 2, 'sources': db.execute('SELECT count(*) FROM sources').fetchone()[0]}


def normalize_job(record):
    if not isinstance(record, dict):
        raise ValueError('Each job must be an object')
    extra = set(record) - JOB_FIELDS
    if extra:
        raise ValueError('Unsupported job fields: ' + ', '.join(sorted(extra)))
    for field in ('title', 'company', 'source_url'):
        if not isinstance(record.get(field), str) or not record[field].strip():
            raise ValueError(f'Job requires nonempty {field}')
    normalized = {}
    for key in TEXT_FIELDS:
        value = record.get(key)
        if value is not None:
            if not isinstance(value, str):
                raise ValueError(f'{key} must be text or null')
            normalized[key] = value.strip()
    for key in ('source_job_id', 'evidence'):
        if record.get(key) is not None and not isinstance(record[key], str):
            raise ValueError(f'{key} must be text or null')
    for key in ('eligible_countries', 'skills'):
        if record.get(key) is not None:
            if not isinstance(record[key], list) or any(not isinstance(v, str) for v in record[key]):
                raise ValueError(f'{key} must be an array of strings or null')
            normalized[key + '_json'] = json.dumps(record[key], ensure_ascii=False)
    for key in ('salary_min', 'salary_max'):
        value = record.get(key)
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f'{key} must be a finite nonnegative number or null')
            normalized[key] = value
    if normalized.get('salary_min', 0) > normalized.get('salary_max', float('inf')):
        raise ValueError('salary_min must not exceed salary_max')
    if 'salary_currency' in normalized and not re.fullmatch('[A-Z]{3}', normalized['salary_currency']):
        raise ValueError('salary_currency must be a three-letter uppercase code')
    if normalized.get('remote_status', 'unknown') not in ('remote', 'hybrid', 'onsite', 'unknown'):
        raise ValueError('remote_status must be remote, hybrid, onsite, or unknown')
    if normalized.get('availability', 'unknown') not in ('open', 'closed', 'unknown'):
        raise ValueError('availability must be open, closed, or unknown')
    if normalized.get('published_at'):
        datetime.fromisoformat(normalized['published_at'].replace('Z', '+00:00'))
    normalized['canonical_url'] = normalize_url(record.get('canonical_url') or record['source_url'])
    return normalized, normalize_url(record['source_url'])


def ingest(db, run_id, source_id, payload):
    records = payload.get('jobs') if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        raise ValueError('Input must be an array of jobs or an object with a jobs array')
    # Validate the whole batch before changing any records.
    validated = [(record, *normalize_job(record)) for record in records]
    inserted = updated = 0
    with write_transaction(db):
        require_running(db, run_id)
        if db.execute('SELECT 1 FROM run_sources WHERE run_id = ? AND source_id = ?', (run_id, source_id)).fetchone() is None:
            raise ValueError('Source is not in this run; start a run including this source')
        for record, values, source_url in validated:
            existing = db.execute('SELECT * FROM jobs WHERE canonical_url = ?', (values['canonical_url'],)).fetchone()
            previous = db.execute('SELECT job_id FROM sightings WHERE source_id = ? AND source_url = ? LIMIT 1',
                                  (source_id, source_url)).fetchone()
            if previous and (existing is None or previous['job_id'] != existing['id']):
                raise ValueError('Source URL already belongs to a different canonical job. Resolve identity before importing; no records were merged.')
            timestamp = now()
            if existing:
                job_id = existing['id']
                # Missing, empty or unknown observations do not erase previously known facts.
                changes = {k: v for k, v in values.items() if v not in ('', 'unknown', '[]')}
                changes['last_seen_at'] = timestamp
                db.execute('UPDATE jobs SET ' + ', '.join(f'{k} = ?' for k in changes) + ' WHERE id = ?',
                           (*changes.values(), job_id))
                updated += 1
            else:
                job_id = stable_id('job_', values['canonical_url'])
                values = {'id': job_id, **values, 'first_seen_at': timestamp, 'last_seen_at': timestamp}
                db.execute('INSERT INTO jobs (' + ', '.join(values) + ') VALUES (' + ', '.join('?' for _ in values) + ')',
                           tuple(values.values()))
                inserted += 1
            db.execute('''INSERT INTO sightings(run_id, source_id, source_url, job_id, source_job_id, retrieved_at, evidence, raw_json)
                          VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                          ON CONFLICT(run_id, source_id, source_url) DO UPDATE SET
                          retrieved_at = excluded.retrieved_at, evidence = excluded.evidence, raw_json = excluded.raw_json,
                          source_job_id = excluded.source_job_id''',
                       (run_id, source_id, source_url, job_id, record.get('source_job_id'), timestamp,
                        record.get('evidence') or '', json.dumps(record, ensure_ascii=False, allow_nan=False)))
    return {'inserted': inserted, 'updated': updated, 'processed': len(records)}


def decode_row(row):
    result = dict(row)
    for key in list(result):
        if key.endswith('_json'):
            value = result.pop(key)
            result[key[:-5]] = json.loads(value) if value is not None else None
    return result


def list_jobs(db, args):
    clauses, params = [], []
    for field, value in (('j.remote_status', args.remote), ("COALESCE(r.status, 'new')", args.status)):
        if value:
            clauses.append(field + ' = ?')
            params.append(value)
    if args.search:
        clauses.append('(j.title LIKE ? OR j.company LIKE ? OR j.description LIKE ? OR j.location_text LIKE ?)')
        params.extend(['%' + args.search + '%'] * 4)
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    rows = db.execute('''SELECT j.*, COALESCE(r.status, 'new') AS review_status,
                         COALESCE(r.notes, '') AS review_notes, COALESCE(r.tags_json, '[]') AS review_tags_json,
                         r.reviewed_at, (SELECT count(*) FROM job_comments c WHERE c.job_id = j.id) AS comment_count
                         FROM jobs j LEFT JOIN reviews r ON j.id = r.job_id''' + where +
                      ' ORDER BY j.first_seen_at DESC, j.id LIMIT ? OFFSET ?', (*params, args.limit, args.offset)).fetchall()
    return [decode_row(row) for row in rows]


def execute(db, args):
    if args.command == 'init':
        return initialize(db)
    if args.command == 'profile':
        if args.action == 'show':
            return get_profile(db)
        content = sys.stdin.read() if args.file == '-' else Path(args.file).read_text()
        return save_profile(db, json.loads(content))
    if args.command == 'sources':
        if args.action == 'list':
            return [dict(row) for row in db.execute('SELECT * FROM sources ORDER BY name')]
        with write_transaction(db):
            if args.action == 'add':
                source_id = add_source(db, args.name, args.url, args.kind, args.method, args.notes)
            else:
                source_id = args.id
                require_row(db, 'sources', 'id', source_id)
                updates = {k: v for k, v in {'method': args.method, 'notes': args.notes, 'enabled': args.enabled}.items() if v is not None}
                if not updates:
                    raise ValueError('Supply --method, --notes, or --enabled')
                db.execute('UPDATE sources SET ' + ', '.join(f'{k} = ?' for k in updates) + ' WHERE id = ?',
                           (*updates.values(), source_id))
        return dict(require_row(db, 'sources', 'id', source_id))
    if args.command == 'run':
        if args.action == 'list':
            return [dict(row) for row in db.execute('SELECT * FROM search_runs ORDER BY started_at DESC LIMIT 30')]
        if args.action == 'show':
            run = decode_row(require_row(db, 'search_runs', 'id', args.id))
            run['sources'] = [decode_row(row) for row in db.execute('''SELECT rs.*, s.name FROM run_sources rs
                              JOIN sources s ON s.id = rs.source_id WHERE rs.run_id = ? ORDER BY s.name''', (args.id,))]
            return run
        with write_transaction(db):
            if args.action == 'start':
                source_ids = list(dict.fromkeys(args.source or [row['id'] for row in db.execute('SELECT id FROM sources WHERE enabled = 1')]))
                if not source_ids:
                    raise ValueError('No sources selected')
                for source_id in source_ids:
                    require_row(db, 'sources', 'id', source_id)
                run_id = uuid.uuid4().hex
                profile = get_profile(db)
                db.execute("INSERT INTO search_runs(id, request, started_at, status, profile_snapshot_json) VALUES (?, ?, ?, 'running', ?)",
                           (run_id, args.request, now(), json.dumps(profile, ensure_ascii=False)))
                db.executemany('INSERT INTO run_sources(run_id, source_id) VALUES (?, ?)', [(run_id, sid) for sid in source_ids])
                return {'run_id': run_id, 'planned_sources': source_ids, 'profile_version': profile['version']}
            require_running(db, args.id)
            if args.action == 'source':
                row = db.execute('SELECT * FROM run_sources WHERE run_id = ? AND source_id = ?', (args.id, args.source)).fetchone()
                if row is None:
                    raise ValueError('Source is not in this run')
                if args.status != 'completed' and not args.note:
                    raise ValueError('An incomplete source needs --note explaining the gap')
                attempts = json.loads(row['attempts_json'])
                attempts.append({'at': now(), 'status': args.status, 'queries': args.query, 'pages': args.pages, 'note': args.note})
                db.execute('UPDATE run_sources SET status = ?, attempts_json = ? WHERE run_id = ? AND source_id = ?',
                           (args.status, json.dumps(attempts, ensure_ascii=False), args.id, args.source))
                return {'run_id': args.id, 'source_id': args.source, 'status': args.status}
            statuses = [row[0] for row in db.execute('SELECT status FROM run_sources WHERE run_id = ?', (args.id,))]
            status = 'failed' if args.failed else ('completed' if all(s == 'completed' for s in statuses) else 'partial')
            finished = now()
            db.execute('UPDATE search_runs SET status = ?, finished_at = ?, notes = ? WHERE id = ?', (status, finished, args.note, args.id))
            db.execute('''UPDATE sources SET last_successful_at = ? WHERE id IN
                          (SELECT source_id FROM run_sources WHERE run_id = ? AND status = 'completed')''', (finished, args.id))
            return {'run_id': args.id, 'status': status, 'pending_or_incomplete_sources': sum(s != 'completed' for s in statuses)}
    if args.command == 'ingest':
        content = sys.stdin.read() if args.file == '-' else Path(args.file).read_text()
        return ingest(db, args.run, args.source, json.loads(content))
    if args.command == 'list':
        return list_jobs(db, args)
    if args.command == 'review':
        if args.status is None and args.notes is None and args.tag is None and args.reviewed is None:
            raise ValueError('Supply --status, --notes, --tag, or --reviewed')
        return update_review(db, args.id, status=args.status, notes=args.notes, tags=args.tag,
                             reviewed=None if args.reviewed is None else bool(args.reviewed))
    if args.command == 'stats':
        return {'jobs': db.execute('SELECT count(*) FROM jobs').fetchone()[0],
                'sources': db.execute('SELECT count(*) FROM sources').fetchone()[0],
                'running_searches': db.execute("SELECT count(*) FROM search_runs WHERE status = 'running'").fetchone()[0],
                'reviews': {row[0]: row[1] for row in db.execute('SELECT status, count(*) FROM reviews GROUP BY status')}}
    target = Path(args.file).expanduser().resolve()
    if target.exists():
        raise ValueError('Output already exists; choose a new filename')
    target.parent.mkdir(parents=True, exist_ok=True)
    if args.command == 'backup':
        with sqlite3.connect(target) as backup:
            db.backup(backup)
        return {'backup': str(target)}
    # Pin one read snapshot so concurrent collection cannot split an export across states.
    db.execute('BEGIN')
    if target.suffix.lower() == '.csv':
        rows = list_jobs(db, argparse.Namespace(remote=None, status=None, search=None, limit=-1, offset=0))
        count = len(rows)
        columns = list(rows[0]) if rows else ['id', 'title', 'company', 'canonical_url', 'review_status', 'review_notes']
        with target.open('x', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                cells = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()}
                # Neutralize formulas in untrusted scraped text when opened in a spreadsheet.
                writer.writerow({k: "'" + v if isinstance(v, str) and v.lstrip().startswith(('=', '+', '-', '@')) else v for k, v in cells.items()})
    else:
        snapshot = {table: [decode_row(row) for row in db.execute(f'SELECT * FROM {table}')]
                    for table in ('sources', 'search_runs', 'run_sources', 'jobs', 'sightings', 'reviews', 'profiles', 'job_comments')}
        count = len(snapshot['jobs'])
        with target.open('x') as handle:
            json.dump({'schema_version': 2, 'exported_at': now(), **snapshot}, handle, ensure_ascii=False, indent=2)
    db.commit()
    return {'export': str(target), 'jobs': count}


def nonnegative(value):
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError('must be nonnegative')
    return result


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', type=Path, default=DEFAULT_DB, help='Defaults to this project/data/jobs.sqlite3, independent of cwd')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('init')
    profile = sub.add_parser('profile').add_subparsers(dest='action', required=True)
    profile.add_parser('show')
    profile.add_parser('update').add_argument('--file', required=True, help='JSON patch file or - for stdin')
    sources = sub.add_parser('sources').add_subparsers(dest='action', required=True)
    sources.add_parser('list')
    add = sources.add_parser('add')
    for name in ('name', 'url', 'kind'):
        add.add_argument('--' + name, required=True)
    add.add_argument('--method', default='discover', choices=('discover', 'api', 'rss', 'web', 'manual'))
    add.add_argument('--notes', default='')
    update = sources.add_parser('update')
    update.add_argument('id')
    update.add_argument('--method', choices=('discover', 'api', 'rss', 'web', 'manual'))
    update.add_argument('--notes')
    update.add_argument('--enabled', type=int, choices=(0, 1))
    runs = sub.add_parser('run').add_subparsers(dest='action', required=True)
    start = runs.add_parser('start')
    start.add_argument('--request', required=True)
    start.add_argument('--source', action='append')
    runs.add_parser('list')
    show = runs.add_parser('show')
    show.add_argument('id')
    source = runs.add_parser('source')
    source.add_argument('id')
    source.add_argument('--source', required=True)
    source.add_argument('--status', required=True, choices=SOURCE_STATUSES)
    source.add_argument('--query', action='append', default=[])
    source.add_argument('--pages', type=nonnegative, default=0)
    source.add_argument('--note', default='')
    finish = runs.add_parser('finish')
    finish.add_argument('id')
    finish.add_argument('--failed', action='store_true')
    finish.add_argument('--note', default='')
    ingest_cmd = sub.add_parser('ingest')
    ingest_cmd.add_argument('--run', required=True)
    ingest_cmd.add_argument('--source', required=True)
    ingest_cmd.add_argument('--file', required=True, help='JSON file, or - for stdin')
    listing = sub.add_parser('list')
    listing.add_argument('--search')
    listing.add_argument('--remote', choices=('remote', 'hybrid', 'onsite', 'unknown'))
    listing.add_argument('--status', choices=REVIEW_STATUSES)
    listing.add_argument('--limit', type=nonnegative, default=50)
    listing.add_argument('--offset', type=nonnegative, default=0)
    review = sub.add_parser('review')
    review.add_argument('id')
    review.add_argument('--status', choices=REVIEW_STATUSES)
    review.add_argument('--notes')
    review.add_argument('--reviewed', type=int, choices=(0, 1))
    review.add_argument('--tag', action='append', help='Replaces all tags with the supplied repeated --tag values')
    sub.add_parser('stats')
    for command in ('export', 'backup'):
        output = sub.add_parser(command)
        output.add_argument('--file', required=True)
    return p


def main():
    args = parser().parse_args()
    os.umask(0o077)
    path = args.db.expanduser().resolve()
    if args.command != 'init' and not path.is_file():
        raise ValueError('Database does not exist; run init first')
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    try:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version not in (0, 1, 2) or (version != 2 and args.command != 'init'):
            raise ValueError(f'Unsupported or uninitialized database schema: {version}')
        db.execute('PRAGMA foreign_keys = ON')
        db.execute('PRAGMA journal_mode = WAL')
        emit(execute(db, args))
    finally:
        db.close()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, sqlite3.Error) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        sys.exit(1)
