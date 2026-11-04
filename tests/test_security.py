import contextlib
import io
import json
import runpy
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from auth_helpers import INITIAL_PASSWORD, authenticated_client, configure_test_auth

ROOT = Path(__file__).resolve().parents[1]


class SQLSecurityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.state = runpy.run_path(str(ROOT / 'app.py'), run_name='verification')['index'].__globals__
        self.app = self.state['app']
        configure_test_auth(self.app, self.directory)
        for name, filename in [('DB_PATH', 'university.db'), ('HIST_PATH', 'history.json'), ('FAV_PATH', 'favorites.json')]:
            self.state[name] = str(self.directory / filename)
        database = runpy.run_path(str(ROOT / 'database.py'))
        with self.database() as conn, contextlib.redirect_stdout(io.StringIO()):
            database['create_tables'](conn)
            database['fill_data'](conn)
        self.admin = authenticated_client(self.app)

    @contextlib.contextmanager
    def database(self):
        with contextlib.closing(sqlite3.connect(self.state['DB_PATH'])) as conn:
            yield conn

    def snapshot(self):
        with self.database() as conn:
            return list(conn.iterdump())

    def user(self, username, role):
        response = self.admin.post('/api/admin/users', json={'username': username, 'role': role, 'password': INITIAL_PASSWORD})
        self.assertEqual(response.status_code, 201, response.get_json())
        return authenticated_client(self.app, username)

    def preview(self, client, sql):
        response = client.post('/api/execute', json={'sql': sql})
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()
        self.assertTrue(result['sandbox'])
        return result['confirmation_token']

    def test_write_requires_rollback_preview_then_explicit_one_time_confirmation(self):
        operator = self.user('writer', 'operator')
        sql = "UPDATE students SET first_name='Исправлено' WHERE id=1"
        before = self.snapshot()
        file_before = Path(self.state['DB_PATH']).read_bytes()
        token = self.preview(operator, sql)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(Path(self.state['DB_PATH']).read_bytes(), file_before)
        response = operator.post('/api/execute', json={'sql': sql, 'confirmation_token': token})
        self.assertTrue(response.get_json()['committed'], response.get_json())
        self.assertEqual(response.get_json()['affected_rows'], 1)
        with self.database() as conn:
            self.assertEqual(conn.execute('SELECT first_name FROM students WHERE id=1').fetchone()[0], 'Исправлено')
        self.assertEqual(operator.post('/api/execute', json={'sql': sql, 'confirmation_token': token}).status_code, 409)

    def test_confirmation_bound_to_user_sql_database_and_expiry(self):
        operator = self.user('writer', 'operator')
        sql = "UPDATE students SET first_name='Изменено' WHERE id=1"
        token = self.preview(operator, sql)
        self.assertEqual(self.admin.post('/api/execute', json={'sql': sql, 'confirmation_token': token}).status_code, 409)
        self.assertEqual(operator.post('/api/execute', json={'sql': sql.replace('id=1', 'id=2'), 'confirmation_token': token}).status_code, 409)
        with self.database() as conn, conn:
            conn.execute("UPDATE students SET group_name='Новое значение' WHERE id=2")
        self.assertEqual(operator.post('/api/execute', json={'sql': sql, 'confirmation_token': token}).status_code, 409)
        self.app.config['SQL_PREVIEW_TTL'] = -1
        expired = self.preview(operator, sql)
        self.assertEqual(operator.post('/api/execute', json={'sql': sql, 'confirmation_token': expired}).status_code, 409)
        self.assertEqual(operator.post('/api/execute', json={'sql': sql, 'confirmation_token': 'invented'}).status_code, 409)

    def test_guest_and_analyst_cannot_modify_even_inside_cte(self):
        before = self.snapshot()
        for role in ('guest', 'analyst'):
            client = self.user(role, role)
            for sql in ("UPDATE students SET first_name='Тест' WHERE id=1",
                        "WITH chosen AS (SELECT id FROM students WHERE id=1) DELETE FROM students WHERE id IN (SELECT id FROM chosen)",
                        'CREATE TABLE forbidden(id INTEGER)'):
                with self.subTest(role=role, sql=sql):
                    response = client.post('/api/execute', json={'sql': sql})
                    self.assertEqual(response.status_code, 403)
                    self.assertTrue(response.get_json()['readonly_blocked'])
        self.assertEqual(self.snapshot(), before)

    def test_ddl_create_rename_and_drop_are_transactional(self):
        operator = self.user('writer', 'operator')
        for sql in ('CREATE TABLE demo_table (id INTEGER PRIMARY KEY, label TEXT)',
                    'ALTER TABLE demo_table RENAME TO demo_renamed',
                    'CREATE INDEX demo_index ON demo_renamed(label)',
                    'DROP INDEX demo_index', 'DROP TABLE demo_renamed'):
            with self.subTest(sql=sql):
                before = self.snapshot()
                token = self.preview(operator, sql)
                self.assertEqual(self.snapshot(), before)
                response = operator.post('/api/execute', json={'sql': sql, 'confirmation_token': token})
                self.assertEqual(response.status_code, 200, response.get_json())
                self.assertTrue(response.get_json()['committed'])
        self.assertEqual(self.admin.get('/api/schema').status_code, 200)

    def test_insert_delete_and_constraints(self):
        for sql in ("INSERT INTO faculties(name,short_name,dean_name,founded_year) VALUES ('Тестовый','ТСТ','Декан',2026)",
                    "DELETE FROM faculties WHERE short_name='ТСТ'"):
            before = self.snapshot()
            token = self.preview(self.admin, sql)
            self.assertEqual(self.snapshot(), before)
            response = self.admin.post('/api/execute', json={'sql': sql, 'confirmation_token': token})
            self.assertTrue(response.get_json()['committed'], response.get_json())
        before = self.snapshot()
        response = self.admin.post('/api/execute', json={'sql': 'UPDATE students SET specialization_id=99999 WHERE id=1'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.snapshot(), before)

    def test_operator_cannot_modify_copy_or_rename_confidential_tables(self):
        operator = self.user('writer', 'operator')
        before = self.snapshot()
        for sql in ("UPDATE conf_student_personal SET phone='123' WHERE student_id=1",
                    'DROP TABLE conf_student_personal',
                    'ALTER TABLE conf_student_personal RENAME TO public_copy',
                    'ALTER TABLE students RENAME TO conf_students',
                    'CREATE TABLE public_copy AS SELECT * FROM conf_student_personal'):
            response = operator.post('/api/execute', json={'sql': sql})
            self.assertEqual(response.status_code, 403, response.get_json())
        self.assertEqual(self.snapshot(), before)

    def test_control_commands_comments_multiple_statements_and_functions_blocked(self):
        before = self.snapshot()
        for sql in ('SELECT 1; DELETE FROM students', 'PRAGMA query_only=OFF',
                    "ATTACH DATABASE 'other.db' AS other", 'BEGIN', 'COMMIT', 'ROLLBACK',
                    'SELECT 1 /* comment */', 'SELECT 1 -- comment',
                    'SELECT * FROM sqlite_master', 'SELECT randomblob(999999999)',
                    "SELECT load_extension('x')", 'CREATE VIEW x AS SELECT * FROM students',
                    'CREATE TRIGGER x AFTER UPDATE ON students BEGIN DELETE FROM students; END'):
            with self.subTest(sql=sql):
                response = self.admin.post('/api/execute', json={'sql': sql})
                self.assertEqual(response.status_code, 403, response.get_json())
        for sql in ("SELECT 'UPDATE; DROP TABLE students' AS literal", "SELECT CASE WHEN 2 > 1 THEN 'Да' ELSE 'Нет' END"):
            self.assertEqual(self.admin.post('/api/execute', json={'sql': sql}).status_code, 200)
        self.assertEqual(self.snapshot(), before)

    def test_timeout_interrupts_reads_and_rolls_back_partial_update(self):
        self.app.config['SQL_TIMEOUT_SECONDS'] = 0.025
        heavy = '(SELECT CAST(COUNT(*) AS TEXT) FROM students a, students b, students c, students d, students e)'
        before = self.snapshot()
        for sql in ('SELECT ' + heavy,
                    "UPDATE students SET first_name=CASE WHEN id < 40 THEN 'Тест' ELSE " + heavy + ' END'):
            start = time.monotonic()
            response = self.admin.post('/api/execute', json={'sql': sql})
            self.assertEqual(response.status_code, 408, response.get_json())
            self.assertTrue(response.get_json()['timeout'])
            self.assertLess(time.monotonic() - start, 1.5)
            self.assertEqual(self.snapshot(), before)

    def test_generated_write_is_only_preview(self):
        sql = "UPDATE students SET group_name='ПИ-202' WHERE id=1"
        before = self.snapshot()
        with patch.dict(self.state, {'get_model': lambda: 'test'}), patch('requests.post') as post:
            post.return_value.json.return_value = {'response': sql}
            response = self.admin.post('/api/generate', json={'question': 'Измени группу студента 1'})
        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()['result']['sandbox'])
        self.assertEqual(self.snapshot(), before)
        logs = self.admin.get('/api/admin/audit').get_json()['logs']
        self.assertEqual([row['action'] for row in logs[:2]], ['sql_preview', 'sql_generate'])

    def test_rate_limit_uses_real_ip_returns_retry_and_records_failure(self):
        self.app.config.update(RATE_LIMIT_ENABLED=True, RATE_LIMITS={'login': (2, 60)})
        client = self.app.test_client()
        for _ in range(2):
            self.assertEqual(client.post('/api/auth/login', json={'username': 'missing', 'password': 'bad'}).status_code, 401)
        response = client.post('/api/auth/login', json={'username': 'missing', 'password': 'bad'}, headers={'X-Forwarded-For': '10.0.0.2'})
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response.headers['Retry-After']), 0)
        other_ip = client.post('/api/auth/login', json={'username': 'missing', 'password': 'bad'}, environ_overrides={'REMOTE_ADDR': '127.0.0.2'})
        self.assertEqual(other_ip.status_code, 401)
        logs = self.admin.get('/api/admin/audit?action=login&user=missing').get_json()['logs']
        self.assertTrue(any(log['status_code'] == 429 for log in logs))

    def test_audit_events_filters_pagination_and_secret_redaction(self):
        self.admin.post('/api/generate', json={'question': 'Топ-5 студентов по среднему баллу за экзамены'})
        generated_logs = self.admin.get('/api/admin/audit').get_json()['logs']
        self.assertEqual([row['action'] for row in generated_logs[:2]], ['sql_execute', 'sql_generate'])
        self.admin.post('/api/export', json={'format': 'csv', 'columns': ['Имя'], 'rows': [['Анна']]})
        token = self.preview(self.admin, "UPDATE students SET group_name='Тест' WHERE id=1")
        self.admin.post('/api/execute', json={'sql': "UPDATE students SET group_name='Тест' WHERE id=1", 'confirmation_token': token})
        guest = self.user('viewer', 'guest')
        self.assertEqual(guest.get('/api/admin/audit').status_code, 403)
        guest.post('/api/auth/logout', json={})
        response = self.admin.get('/api/admin/audit')
        logs = response.get_json()['logs']
        actions = {log['action'] for log in logs}
        self.assertTrue({'login', 'logout', 'password_change', 'sql_generate', 'sql_preview', 'sql_execute', 'export', 'user_create'} <= actions)
        for log in logs:
            self.assertTrue(log['timestamp'])
            self.assertEqual(log['ip_address'], '127.0.0.1')
        serialized = json.dumps(logs)
        for secret in (INITIAL_PASSWORD, token, 'Different12345!', 'csrf_token', 'password_hash'):
            self.assertNotIn(secret, serialized)
        filtered = self.admin.get('/api/admin/audit?action=export&user=admin').get_json()
        self.assertEqual(filtered['total'], 1)
        self.assertEqual(filtered['logs'][0]['details']['format'], 'csv')
        today = datetime.now().date().isoformat()
        self.assertEqual(self.admin.get(f'/api/admin/audit?date_from={today}&date_to={today}').get_json()['total'], len(logs))
        first = self.admin.get('/api/admin/audit?limit=2&page=1').get_json()
        second = self.admin.get('/api/admin/audit?limit=2&page=2').get_json()
        self.assertTrue({row['id'] for row in first['logs']}.isdisjoint({row['id'] for row in second['logs']}))
        self.assertEqual(self.admin.get('/api/admin/audit?date_from=bad').status_code, 400)
        self.assertEqual(self.admin.get('/api/admin/audit?limit=999999').status_code, 400)


if __name__ == '__main__':
    unittest.main()
