"""CLI integration tests. Every write is confined to a temporary SQLite database."""

from concurrent.futures import ThreadPoolExecutor
import csv
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / 'scripts' / 'jobs.py'


class JobStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.database = self.folder / 'jobs.sqlite3'
        self.call('init')
        self.sources = self.call('sources', 'list')
        self.source = self.sources[0]['id']

    def call(self, *args, payload=None, ok=True):
        result = subprocess.run(
            [sys.executable, str(CLI), '--db', str(self.database), *map(str, args)],
            input=json.dumps(payload) if payload is not None else None,
            text=True, capture_output=True, cwd=self.folder,
        )
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn('Traceback', result.stderr)
        return json.loads(result.stderr)

    def start(self, *sources):
        selection = [item for source in (sources or (self.source,)) for item in ('--source', source)]
        return self.call('run', 'start', '--request', 'Synthetic integration test', *selection)['run_id']

    def job(self, suffix='1', **changes):
        return {'title': 'Operations Specialist', 'company': 'Example Company',
                'source_url': f'https://example.invalid/jobs/{suffix}',
                'description': 'Synthetic job description for storage tests.', **changes}

    def ingest(self, run, jobs, source=None, ok=True):
        return self.call('ingest', '--run', run, '--source', source or self.source,
                         '--file', '-', payload={'jobs': jobs}, ok=ok)

    def sql(self, statement):
        with sqlite3.connect(self.database) as db:
            return db.execute(statement).fetchall()

    def test_initialization_is_idempotent_and_preserves_source_settings(self):
        self.assertGreater(len(self.sources), 12)  # 12 links from remote-job-links.md plus the company-list CSVs
        linkedin = next(s for s in self.sources if 'linkedin.com' in s['url'])
        saved = next(r for r in csv.DictReader((ROOT / 'job-sources.csv').read_text().splitlines()) if 'linkedin.com' in r['url'])
        self.assertEqual(linkedin['method'], saved['method'])  # restored from the backup, not the Markdown default
        self.call('sources', 'update', self.source, '--enabled', '0', '--method', 'rss', '--notes', 'User configuration')
        self.call('init')
        source = next(s for s in self.call('sources', 'list') if s['id'] == self.source)
        self.assertEqual((source['enabled'], source['method'], source['notes']), (0, 'rss', 'User configuration'))
        self.assertEqual(self.call('stats')['sources'], len(self.sources))
        self.assertEqual(self.database.stat().st_mode & 0o777, 0o600)

    def test_company_lists_round_trip_saved_sources(self):
        spec = importlib.util.spec_from_file_location('jobs_cli', CLI)
        jobs = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(jobs)

        def connect(path):
            db = sqlite3.connect(path)
            db.row_factory = sqlite3.Row
            return db
        folder = self.folder / 'lists'
        folder.mkdir()
        (folder / 'edtech-careers.csv').write_text(
            'group,company,website,page_called,vacancy_url\n'
            'G,Acme Learn,https://acme.example,Careers,https://acme.example/careers\n'
            'G,Beta Edu,https://beta.example,Jobs,https://beta.example/jobs\n')
        (folder / 'b2b-saas-careers.csv').write_text(
            'group,company,website,page_called,vacancy_url,open_roles_2026_11_17\n'
            'AI,Gamma SaaS,https://gamma.example,Careers,https://jobs.ashbyhq.com/gamma-saas,3\n')
        db = connect(self.database)
        jobs.seed_lists(db, folder)
        db.commit()
        self.assertTrue(any(s['url'] == 'https://jobs.ashbyhq.com/gamma-saas' for s in self.call('sources', 'list')))
        # A row that records its own page is not merged into a source that merely shares a name prefix.
        self.call('sources', 'add', '--name', 'Gamma SaaS Jobs Board', '--url', 'https://board.example/gamma', '--kind', 'job_board')
        row = {'company': 'Gamma SaaS', 'searched_url': 'https://other.example/careers', 'website': 'https://other.example'}
        self.assertIsNone(jobs.list_row_source(db, row, 'company'))
        acme = next(s for s in self.call('sources', 'list') if s['name'] == 'Acme Learn')
        self.assertEqual((acme['url'], acme['kind'], acme['enabled']), ('https://acme.example/careers', 'company_careers', 1))
        self.call('sources', 'update', acme['id'], '--url', 'https://boards.example/acme',
                  '--method', 'api', '--enabled', '0', '--notes', 'Website https://acme.example. Board API.')
        board = next(s for s in self.call('sources', 'list') if 'linkedin.com' in s['url'])
        self.call('sources', 'update', board['id'], '--method', 'web', '--notes', 'Checked in the browser.')
        self.assertEqual(jobs.sync_lists(db, folder)['edtech-careers.csv']['without_source'], [])
        rows = list(csv.DictReader((folder / 'edtech-careers.csv').read_text().splitlines()))
        self.assertEqual((rows[0]['searched_url'], rows[0]['method'], rows[0]['enabled']), ('https://boards.example/acme', 'api', '0'))
        db.close()
        rebuilt = self.folder / 'rebuilt.sqlite3'
        fresh = connect(rebuilt)
        jobs.initialize(fresh, folder)
        fresh.commit()
        self.assertEqual(tuple(fresh.execute("SELECT method, notes FROM sources WHERE url = ?", (board['url'],)).fetchone()),
                         ('web', 'Checked in the browser.'))
        self.assertEqual(fresh.execute('SELECT count(*) FROM sources').fetchone()[0], len(self.call('sources', 'list')))
        row = fresh.execute("SELECT url, method, enabled, notes FROM sources WHERE name = 'Acme Learn'").fetchone()
        self.assertEqual(tuple(row), ('https://boards.example/acme', 'api', 0, 'Website https://acme.example. Board API.'))
        before = fresh.execute('SELECT count(*) FROM sources').fetchone()[0]
        jobs.seed_lists(fresh, folder)
        self.assertEqual(fresh.execute('SELECT count(*) FROM sources').fetchone()[0], before)
        fresh.close()

    def test_source_update_can_move_a_source_to_its_careers_page(self):
        moved = self.call('sources', 'update', self.source, '--name', 'Example — Jobs board',
                          '--url', 'https://jobs.example.org/', '--kind', 'job_board', '--method', 'web')
        self.assertEqual((moved['id'], moved['name'], moved['url'], moved['kind'], moved['method']),
                         (self.source, 'Example — Jobs board', 'https://jobs.example.org/', 'job_board', 'web'))
        other = next(s for s in self.sources if s['id'] != self.source)
        error = self.call('sources', 'update', other['id'], '--url', 'https://jobs.example.org/', ok=False)
        self.assertIn('error', error)
        self.assertEqual(next(s for s in self.call('sources', 'list') if s['id'] == other['id'])['url'], other['url'])

    def test_repeat_import_and_multiple_sources_preserve_review(self):
        second = self.sources[1]['id']
        run = self.start(self.source, second)
        original = self.job(canonical_url='https://employer.invalid/jobs/42', remote_status='remote', skills=['writing'])
        self.assertEqual(self.ingest(run, [original])['inserted'], 1)
        job_id = self.call('list')[0]['id']
        self.call('review', job_id, '--status', 'shortlisted', '--notes', 'Keep my decision', '--tag', 'priority')
        self.assertEqual(self.ingest(run, [original])['updated'], 1)
        self.ingest(run, [self.job('other', canonical_url=original['canonical_url'], remote_status='unknown', skills=[])], source=second)
        jobs = self.call('list')
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]['review_status'], 'shortlisted')
        self.assertEqual(jobs[0]['review_notes'], 'Keep my decision')
        self.assertEqual(jobs[0]['review_tags'], ['priority'])
        self.assertEqual(jobs[0]['remote_status'], 'remote')
        self.assertEqual(jobs[0]['skills'], ['writing'])
        self.assertNotIn('eligible_countries_json', jobs[0])
        self.assertIsNone(jobs[0]['eligible_countries'])
        self.assertEqual(self.sql('SELECT count(*) FROM sightings')[0][0], 2)

    def test_same_titles_and_hash_routes_do_not_merge_distinct_jobs(self):
        run = self.start()
        jobs = [self.job(source_url='https://example.invalid/#/jobs/1'),
                self.job(source_url='https://example.invalid/#/jobs/2'),
                self.job(source_url='https://example.invalid/?job=3&utm_source=test')]
        self.ingest(run, jobs)
        saved = self.call('list')
        self.assertEqual(len(saved), 3)
        self.assertTrue(any(row['canonical_url'] == 'https://example.invalid/?job=3' for row in saved))

    def test_validation_rejects_entire_batch_and_review_fields(self):
        run = self.start()
        self.ingest(run, [self.job(), self.job('2', salary_min=100, salary_max=50)], ok=False)
        self.assertEqual(self.call('stats')['jobs'], 0)
        self.ingest(run, [self.job(review_notes='Do not write reviews through ingest')], ok=False)
        self.assertEqual(self.sql('SELECT count(*) FROM sightings')[0][0], 0)
        self.ingest(run, [self.job(source_url='https://user:password@example.invalid/job')], ok=False)

    def test_conflicting_identity_rolls_back_preceding_job_in_batch(self):
        run = self.start()
        self.ingest(run, [self.job()])
        result = self.ingest(run, [self.job('2'), self.job(canonical_url='https://another.invalid/job')], ok=False)
        self.assertIn('identity', result['error'])
        self.assertEqual(self.call('stats')['jobs'], 1)
        self.assertEqual(self.sql('SELECT count(*) FROM sightings')[0][0], 1)

    def test_partial_salary_update_cannot_invalidate_existing_range(self):
        run = self.start()
        self.ingest(run, [self.job(salary_min=100, salary_max=200, salary_currency='USD')])
        self.ingest(run, [self.job(salary_max=50)], ok=False)
        saved = self.call('list')[0]
        self.assertEqual((saved['salary_min'], saved['salary_max']), (100, 200))

    def test_coverage_stays_partial_for_unfinished_and_blocked_sources(self):
        second = self.sources[1]['id']
        run = self.start(self.source, second)
        self.call('run', 'source', run, '--source', self.source, '--status', 'completed', '--query', 'actual query', '--pages', '2')
        self.call('run', 'source', run, '--source', second, '--status', 'blocked', ok=False)
        self.call('run', 'source', run, '--source', second, '--status', 'blocked', '--note', 'Login required')
        self.assertEqual(self.call('run', 'finish', run)['status'], 'partial')
        coverage = self.call('run', 'show', run)
        completed = next(s for s in coverage['sources'] if s['source_id'] == self.source)
        self.assertEqual(completed['attempts'][0]['queries'], ['actual query'])
        self.ingest(run, [self.job()], ok=False)
        self.assertEqual(self.call('stats')['jobs'], 0)
        sources = {s['id']: s for s in self.call('sources', 'list')}
        self.assertIsNotNone(sources[self.source]['last_successful_at'])
        self.assertIsNone(sources[second]['last_successful_at'])

    def test_successful_retry_keeps_attempt_history_and_zero_matches_can_complete(self):
        run = self.start()
        self.call('run', 'source', run, '--source', self.source, '--status', 'partial', '--note', 'Two pages remain')
        self.call('run', 'source', run, '--source', self.source, '--status', 'completed', '--pages', '2')
        self.assertEqual(self.call('run', 'finish', run)['status'], 'completed')
        self.assertEqual(len(self.call('run', 'show', run)['sources'][0]['attempts']), 2)
        self.assertEqual(self.call('stats')['jobs'], 0)

    def test_csv_json_and_sqlite_backup_contain_all_records(self):
        run = self.start()
        jobs = [self.job(str(i)) for i in range(65)]
        jobs[0]['title'] = '=UNTRUSTED_FORMULA()'
        self.ingest(run, jobs)
        self.assertEqual(len(self.call('list')), 50)
        self.assertEqual(len(self.call('list', '--offset', '50')), 15)
        csv_path, json_path, backup_path = [self.folder / name for name in ('jobs.csv', 'jobs.json', 'backup.sqlite3')]
        self.call('export', '--file', csv_path)
        with csv_path.open(newline='') as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 65)
        self.assertTrue(any(row['title'].startswith("'=UNTRUSTED") for row in rows))
        self.call('export', '--file', json_path)
        self.assertEqual(len(json.loads(json_path.read_text())['jobs']), 65)
        self.call('export', '--file', json_path, ok=False)
        self.call('backup', '--file', backup_path)
        with sqlite3.connect(backup_path) as backup:
            self.assertEqual(backup.execute('SELECT count(*) FROM jobs').fetchone()[0], 65)
            self.assertEqual(backup.execute('PRAGMA integrity_check').fetchone()[0], 'ok')

    def test_deadlines_are_validated_kept_and_editable(self):
        run = self.start()
        error = self.ingest(run, [self.job('1', application_deadline='26 November')], ok=False)
        self.assertIn('2026-11-26', error['error'])
        self.ingest(run, [self.job('1', application_deadline='2026-11-26', deadline_text='Apply by 26 November'),
                          self.job('2', deadline_text='Not listed')])
        dated, undated = sorted(self.call('list'), key=lambda job: job['canonical_url'])
        self.assertEqual((dated['application_deadline'], dated['deadline_text']), ('2026-11-26', 'Apply by 26 November'))
        self.assertEqual((undated['application_deadline'], undated['deadline_text']), (None, 'Not listed'))
        # A later sighting without a deadline does not erase the known one.
        self.ingest(run, [self.job('1')])
        self.assertEqual(self.sql(f"SELECT application_deadline FROM jobs WHERE id = '{dated['id']}'"), [('2026-11-26',)])
        updated = self.call('deadline', undated['id'], '--date', '2026-12-01', '--text', 'Closes 1 December')
        self.assertEqual(updated['application_deadline'], '2026-12-01')
        self.call('deadline', undated['id'], '--date', '2026-02-30', ok=False)
        self.assertEqual(self.call('deadline', undated['id'], '--availability', 'closed')['availability'], 'closed')
        self.assertEqual(self.sql(f"SELECT application_deadline FROM jobs WHERE id = '{undated['id']}'"), [('2026-12-01',)])

    def test_feedback_lists_comments_and_notes_newest_first(self):
        run = self.start()
        self.ingest(run, [self.job('1')])
        job_id = self.call('list')[0]['id']
        self.call('review', job_id, '--status', 'archived', '--notes', 'Entry level')
        feedback = self.call('feedback')
        self.assertEqual([(f['kind'], f['text'], f['status']) for f in feedback], [('note', 'Entry level', 'archived')])
        self.assertEqual(self.call('feedback', '--since', '2999-01-01'), [])

    def test_assessments_are_validated_and_sort_listing(self):
        run = self.start()
        self.ingest(run, [self.job('1'), self.job('2')])
        low, high = sorted(job['id'] for job in self.call('list'))
        self.call('review', low, '--status', 'shortlisted', '--notes', 'Keep me')
        error = self.call('assess', '--file', '-', ok=False,
                          payload=[{'job_id': high, 'fit_score': 101, 'fit_reason': '+ x', 'summary': 'y', 'apply_tips': '→ Tip'}])
        self.assertIn('1 to 100', error['error'])
        error = self.call('assess', '--file', '-', ok=False,
                          payload=[{'job_id': high, 'fit_score': 50, 'fit_reason': 'A long prose paragraph.', 'summary': 'y', 'apply_tips': '→ Tip'}])
        self.assertIn('"+ "', error['error'])
        error = self.call('assess', '--file', '-', ok=False,
                          payload=[{'job_id': high, 'fit_score': 50, 'fit_reason': '+ x', 'summary': 'y', 'apply_tips': 'Write a cover letter.'}])
        self.assertIn('"→ "', error['error'])
        self.assertEqual(self.sql('SELECT count(*) FROM job_assessments'), [(0,)])
        saved = self.call('assess', '--file', '-', payload=[
            {'job_id': low, 'fit_score': 40, 'fit_reason': '+ Partial match\n− Gap', 'summary': 'Short.', 'apply_tips': '→ Lead with metrics'},
            {'job_id': high, 'fit_score': 90, 'fit_reason': '+ Strong match', 'summary': 'Short too.', 'apply_tips': '→ Name the program\n→ Confirm hours'}])
        self.assertEqual(saved['assessed'], 2)
        listed = self.call('list')
        self.assertEqual([job['id'] for job in listed], [high, low])
        self.assertEqual(listed[0]['fit_reason'], '+ Strong match')
        self.assertEqual(listed[0]['apply_tips'], '→ Name the program\n→ Confirm hours')
        reviewed = next(job for job in listed if job['id'] == low)
        self.assertEqual((reviewed['review_status'], reviewed['review_notes']), ('shortlisted', 'Keep me'))

    def test_parallel_imports_do_not_create_duplicates(self):
        run = self.start()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.ingest(run, [self.job()]), range(8)))
        self.assertEqual(sum(r['inserted'] for r in results), 1)
        self.assertEqual(sum(r['updated'] for r in results), 7)
        self.assertEqual(self.call('stats')['jobs'], 1)
        self.assertEqual(self.sql('SELECT count(*) FROM sightings')[0][0], 1)


if __name__ == '__main__':
    unittest.main()
