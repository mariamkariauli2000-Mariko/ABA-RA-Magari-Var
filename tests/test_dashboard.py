"""Real HTTP -> SQLite tests for the local review workspace."""
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import jobs
import serve


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.database = Path(self.temp.name) / 'jobs.sqlite3'
        self.server = serve.AppServer(('127.0.0.1', 0), self.database)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, path, method='GET', data=None, token=True, origin=None, expect=200):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['X-Workspace-Token'] = self.server.token
        if origin:
            headers['Origin'] = origin
        request = Request(self.base + path, method=method, headers=headers,
                          data=json.dumps(data).encode() if data is not None else None)
        try:
            response = urlopen(request, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            self.assertEqual(response.status, expect)
            content = response.read().decode()
            return json.loads(content) if response.headers['Content-Type'].startswith('application/json') else content

    def add_job(self):
        return self.request('/api/jobs', 'POST', {'job': {'title': 'Support Lead', 'company': 'Example Company',
                            'source_url': 'https://example.invalid/jobs/1', 'description': 'A synthetic role.', 'remote_status': 'remote'}}, expect=201)

    def test_page_and_assets_are_available(self):
        html = self.request('/')
        self.assertIn('Job Journal', html)
        self.assertIn(self.server.token, html)
        self.assertNotIn('__WORKSPACE_TOKEN__', html)
        self.assertIn('renderProfile', self.request('/app.js'))
        self.assertIn('.job-card', self.request('/styles.css'))
        self.assertEqual(self.request('/api/state')['jobs'], [])

    def test_status_notes_comments_and_reviewed_state_persist(self):
        job_id = self.add_job()['id']
        saved = self.request(f'/api/jobs/{job_id}/review', 'PATCH', {'status': 'shortlisted', 'notes': 'Ask about timezone overlap.'})
        self.assertEqual(saved['review']['status'], 'shortlisted')
        self.assertIsNotNone(saved['review']['reviewed_at'])
        commented = self.request(f'/api/jobs/{job_id}/comments', 'POST', {'body': 'Looks interesting.'}, expect=201)
        self.assertEqual(commented['comments'][0]['body'], 'Looks interesting.')
        self.request(f'/api/jobs/{job_id}/review', 'PATCH', {'reviewed': False})
        state = self.request('/api/state')
        self.assertIsNone(state['jobs'][0]['reviewed_at'])
        self.assertEqual(state['jobs'][0]['review_notes'], 'Ask about timezone overlap.')
        self.assertEqual(state['jobs'][0]['comment_count'], 1)
        self.assertIn('Ask about timezone overlap.', self.request('/api/export.csv'))
        comment_id = commented['comments'][0]['id']
        self.assertEqual(self.request('/api/comments/' + comment_id, 'DELETE')['comments'], [])
        with serve.connect(self.database) as db:
            self.assertEqual(db.execute('SELECT notes FROM reviews WHERE job_id = ?', (job_id,)).fetchone()[0], 'Ask about timezone overlap.')

    def test_saved_profile_is_captured_by_next_search_and_not_retroactive(self):
        original = self.request('/api/profile')
        self.assertEqual(original['version'], 0)
        profile = self.request('/api/profile', 'PUT', {'profile': {'name': 'Test Person', 'roles': ['Support Lead'],
                               'skills': ['Operations'], 'location': 'Georgia', 'fully_remote_required': True}, 'expected_version': 0})
        self.assertEqual(profile['version'], 1)
        with serve.connect(self.database) as db:
            source = db.execute('SELECT id FROM sources LIMIT 1').fetchone()[0]
            run = jobs.execute(db, serve.SimpleNamespace(command='run', action='start', request='Use saved profile', source=[source]))
            snapshot = jobs.execute(db, serve.SimpleNamespace(command='run', action='show', id=run['run_id']))['profile_snapshot']
        self.assertEqual(snapshot['profile']['roles'], ['Support Lead'])
        self.assertEqual(run['profile_version'], 1)
        self.request('/api/profile', 'PUT', {'profile': {'roles': ['Operations Manager']}, 'expected_version': 1})
        with serve.connect(self.database) as db:
            unchanged = jobs.execute(db, serve.SimpleNamespace(command='run', action='show', id=run['run_id']))
            self.assertEqual(unchanged['profile_snapshot']['profile']['roles'], ['Support Lead'])
        self.request('/api/profile', 'PUT', {'profile': {'roles': ['Stale change']}, 'expected_version': 1}, expect=409)
        self.request('/api/profile', 'PUT', {'profile': {'salary_min': -1}}, expect=400)

    def test_untrusted_web_origins_and_invalid_writes_are_rejected(self):
        self.request('/api/jobs', 'POST', {}, token=False, expect=403)
        self.request('/api/jobs', 'POST', {}, origin='https://example.invalid', expect=403)
        self.request('/api/jobs', 'POST', {'job': {'title': 'Missing URL'}}, expect=400)
        self.assertEqual(self.request('/api/state')['jobs'], [])

    def test_source_switches_change_next_run_scope(self):
        source_id = self.request('/api/state')['sources'][0]['id']
        self.request('/api/sources/' + source_id, 'PATCH', {'enabled': False})
        with serve.connect(self.database) as db:
            run = jobs.execute(db, serve.SimpleNamespace(command='run', action='start', request='Enabled sources only', source=None))
        self.assertNotIn(source_id, run['planned_sources'])

    def test_v1_migration_preserves_saved_jobs_and_review_notes(self):
        job_id = self.add_job()['id']
        self.request(f'/api/jobs/{job_id}/review', 'PATCH', {'notes': 'A note from the old database', 'status': 'applied'})
        with sqlite3.connect(self.database) as db:
            db.executescript('''DROP TABLE job_comments; DROP TABLE profiles;
                ALTER TABLE reviews DROP COLUMN reviewed_at;
                ALTER TABLE search_runs DROP COLUMN profile_snapshot_json;
                PRAGMA user_version = 1;''')
        with serve.connect(self.database) as db:
            jobs.initialize(db)
            review = db.execute('SELECT * FROM reviews WHERE job_id = ?', (job_id,)).fetchone()
            self.assertEqual(review['notes'], 'A note from the old database')
            self.assertEqual(review['status'], 'applied')
            self.assertIsNotNone(review['reviewed_at'])
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
