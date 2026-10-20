#!/usr/bin/env python3
"""Local Job Journal UI and API. Run with python3 scripts/serve.py."""

import argparse
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import secrets
import sqlite3
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid
from contextlib import closing

import jobs as storage


WEB = storage.ROOT / 'web'


def connect(path):
    db = sqlite3.connect(path, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys = ON')
    return db


def all_jobs(db):
    return storage.list_jobs(db, SimpleNamespace(remote=None, status=None, search=None, limit=-1, offset=0))


def detail(db, job_id):
    row = storage.require_row(db, 'jobs', 'id', job_id)
    result = storage.decode_row(row)
    review = db.execute('SELECT * FROM reviews WHERE job_id = ?', (job_id,)).fetchone()
    result['review'] = storage.decode_row(review) if review else {'status': 'new', 'notes': '', 'tags': [], 'reviewed_at': None}
    result['comments'] = [dict(r) for r in db.execute('SELECT * FROM job_comments WHERE job_id = ? ORDER BY created_at', (job_id,))]
    result['sources'] = [dict(r) for r in db.execute('''SELECT s.name, si.source_url, si.evidence, max(si.retrieved_at) AS retrieved_at
                          FROM sightings si JOIN sources s ON s.id = si.source_id WHERE si.job_id = ?
                          GROUP BY s.id, si.source_url ORDER BY retrieved_at DESC''', (job_id,))]
    return result


def create_manual_job(db, payload):
    record = payload.get('job')
    storage.normalize_job(record)
    status = payload.get('status', 'new')
    if status not in storage.REVIEW_STATUSES:
        raise ValueError('Unknown status')
    with storage.write_transaction(db):
        source = storage.add_source(db, 'Manually added', 'http://localhost/manual', 'manual', 'manual', 'Jobs added through the local dashboard.')
        db.execute('UPDATE sources SET enabled = 0 WHERE id = ?', (source,))
    run = storage.execute(db, SimpleNamespace(command='run', action='start', request='Manual entry from Job Journal', source=[source]))['run_id']
    try:
        storage.ingest(db, run, source, [record])
        storage.execute(db, SimpleNamespace(command='run', action='source', id=run, source=source, status='completed', query=[], pages=1, note='User-provided listing.'))
        storage.execute(db, SimpleNamespace(command='run', action='finish', id=run, failed=False, note='Manual entry.'))
    except Exception:
        storage.execute(db, SimpleNamespace(command='run', action='finish', id=run, failed=True, note='Manual entry did not complete.'))
        raise
    canonical = storage.normalize_job(record)[0]['canonical_url']
    job_id = db.execute('SELECT id FROM jobs WHERE canonical_url = ?', (canonical,)).fetchone()[0]
    if status != 'new':
        storage.update_review(db, job_id, status=status)
    return detail(db, job_id)


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, database):
        self.database = Path(database).expanduser().resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(connect(self.database)) as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1, 2):
                raise ValueError(f'Unsupported database version {version}')
            db.execute('PRAGMA journal_mode = WAL')
            storage.initialize(db)
        self.token = secrets.token_urlsafe(32)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f'{self.log_date_time_string()} {fmt % args}', flush=True)

    def send(self, status, content, content_type='application/json; charset=utf-8', filename=None):
        if content_type.startswith('application/json'):
            content = json.dumps(content, ensure_ascii=False, allow_nan=False).encode()
        elif isinstance(content, str):
            content = content.encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_PUT(self):
        self.handle_request()

    def do_PATCH(self):
        self.handle_request()

    def do_DELETE(self):
        self.handle_request()

    def handle_request(self):
        port = self.server.server_port
        allowed = {f'127.0.0.1:{port}', f'localhost:{port}'}
        if self.headers.get('Host') not in allowed:
            self.send(403, {'error': 'This workspace is available on localhost only.'})
            return
        try:
            path = urlsplit(self.path).path
            data = {}
            if self.command != 'GET':
                if self.headers.get('X-Workspace-Token') != self.server.token:
                    self.send(403, {'error': 'Refresh this workspace before making changes.'})
                    return
                origin = self.headers.get('Origin')
                if origin and origin not in {f'http://{host}' for host in allowed}:
                    self.send(403, {'error': 'Cross-origin writes are not allowed.'})
                    return
                size = int(self.headers.get('Content-Length', '0'))
                if size < 0 or size > 2_000_000:
                    self.send(413, {'error': 'Request is too large.'})
                    return
                data = json.loads(self.rfile.read(size) or b'{}')
                if not isinstance(data, dict):
                    raise ValueError('Request must be a JSON object')
            if path.startswith('/api/'):
                with closing(connect(self.server.database)) as db:
                    self.api(db, path, data)
                return
            if self.command != 'GET':
                self.send(405, {'error': 'Method not allowed'})
                return
            files = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/styles.css': ('styles.css', 'text/css; charset=utf-8'), '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
            if path not in files:
                self.send(404, {'error': 'Not found'})
                return
            name, content_type = files[path]
            content = (WEB / name).read_text()
            if name == 'index.html':
                content = content.replace('__WORKSPACE_TOKEN__', self.server.token)
            self.send(200, content, content_type)
        except storage.ConflictError as error:
            self.send(409, {'error': str(error)})
        except (ValueError, TypeError, KeyError) as error:
            self.send(400, {'error': str(error)})
        except sqlite3.Error as error:
            self.log_message('Database error: %s', error)
            self.send(503, {'error': 'The database is busy or unavailable. Try again shortly.'})
        except Exception as error:
            self.log_message('Unexpected error: %s', error)
            self.send(500, {'error': 'Something went wrong. Your saved data remains in the local database.'})

    def api(self, db, path, data):
        method = self.command
        if path == '/api/state' and method == 'GET':
            rows = all_jobs(db)
            for row in rows:
                row['has_description'] = bool(row.pop('description'))
            runs = [storage.decode_row(r) for r in db.execute('SELECT * FROM search_runs ORDER BY started_at DESC LIMIT 25')]
            self.send(200, {'jobs': rows, 'profile': storage.get_profile(db), 'sources': [dict(r) for r in db.execute("SELECT * FROM sources WHERE kind != 'manual' ORDER BY name")], 'runs': runs})
            return
        if path == '/api/profile':
            if method == 'GET':
                self.send(200, storage.get_profile(db))
                return
            if method == 'PUT':
                expected = data.get('expected_version')
                if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int)):
                    raise ValueError('expected_version must be an integer')
                self.send(200, storage.save_profile(db, data.get('profile'), expected))
                return
        if path == '/api/jobs' and method == 'POST':
            self.send(201, create_manual_job(db, data))
            return
        parts = path.strip('/').split('/')
        if len(parts) >= 3 and parts[:2] == ['api', 'jobs']:
            job_id = parts[2]
            storage.require_row(db, 'jobs', 'id', job_id)
            if len(parts) == 3 and method == 'GET':
                self.send(200, detail(db, job_id))
                return
            if len(parts) == 4 and parts[3] == 'review' and method == 'PATCH':
                if not data or set(data) - {'status', 'notes', 'tags', 'reviewed'}:
                    raise ValueError('Supply status, notes, tags, or reviewed')
                storage.update_review(db, job_id, **data)
                self.send(200, detail(db, job_id))
                return
            if len(parts) == 4 and parts[3] == 'comments' and method == 'POST':
                body = data.get('body')
                if not isinstance(body, str) or not body.strip() or len(body) > 10000:
                    raise ValueError('Write a comment between 1 and 10000 characters')
                with storage.write_transaction(db):
                    db.execute('INSERT INTO job_comments VALUES (?, ?, ?, ?)', (uuid.uuid4().hex, job_id, body.strip(), storage.now()))
                storage.update_review(db, job_id, reviewed=True)
                self.send(201, detail(db, job_id))
                return
        if len(parts) == 3 and parts[:2] == ['api', 'comments'] and method == 'DELETE':
            with storage.write_transaction(db):
                comment = storage.require_row(db, 'job_comments', 'id', parts[2])
                db.execute('DELETE FROM job_comments WHERE id = ?', (parts[2],))
            self.send(200, detail(db, comment['job_id']))
            return
        if len(parts) == 3 and parts[:2] == ['api', 'sources'] and method == 'PATCH':
            enabled = data.get('enabled')
            if not isinstance(enabled, bool):
                raise ValueError('enabled must be true or false')
            with storage.write_transaction(db):
                storage.require_row(db, 'sources', 'id', parts[2])
                db.execute('UPDATE sources SET enabled = ? WHERE id = ?', (int(enabled), parts[2]))
            self.send(200, {'ok': True})
            return
        if path == '/api/export.csv' and method == 'GET':
            rows = all_jobs(db)
            output = io.StringIO()
            columns = ['title', 'company', 'canonical_url', 'location_text', 'remote_status', 'salary_text', 'review_status', 'reviewed_at', 'review_notes', 'comment_count']
            writer = csv.DictWriter(output, fieldnames=columns, extrasaction='ignore')
            writer.writeheader()
            for row in rows:
                writer.writerow({key: "'" + value if isinstance(value, str) and value.lstrip().startswith(('=', '+', '-', '@')) else value for key, value in row.items()})
            self.send(200, output.getvalue(), 'text/csv; charset=utf-8', 'job-journal.csv')
            return
        self.send(404, {'error': 'Endpoint not found'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--db', type=Path, default=storage.DEFAULT_DB)
    args = parser.parse_args()
    os.umask(0o077)
    server = AppServer(('127.0.0.1', args.port), args.db)
    print(f'Job Journal is ready at http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
