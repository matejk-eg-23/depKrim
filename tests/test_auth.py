import contextlib
import io
import json
import runpy
import sqlite3
import tempfile
import unittest
from pathlib import Path

from werkzeug.security import check_password_hash

from auth_helpers import (INITIAL_PASSWORD, CHANGED_PASSWORD,
                          authenticated_client, configure_test_auth)

ROOT = Path(__file__).resolve().parents[1]


class AuthTests(unittest.TestCase):
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
        with contextlib.closing(sqlite3.connect(self.state['DB_PATH'])) as conn:
            with contextlib.redirect_stdout(io.StringIO()):
                database['create_tables'](conn)
                database['fill_data'](conn)
        self.admin = authenticated_client(self.app)

    def create_user(self, username, role='guest'):
        response = self.admin.post('/api/admin/users', json={
            'username': username, 'password': INITIAL_PASSWORD,
            'display_name': '<Тестовый пользователь>', 'role': role,
        })
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()['id']

    def test_login_password_hash_and_forced_change(self):
        user_id = self.create_user('reader')
        client = self.app.test_client()
        self.assertEqual(client.get('/api/schema').status_code, 401)
        self.assertEqual(client.post('/api/auth/login', json={'username': 'reader', 'password': 'wrong'}).status_code, 401)
        response = client.post('/api/auth/login', json={'username': 'reader', 'password': INITIAL_PASSWORD})
        self.assertTrue(response.get_json()['user']['must_change_password'])
        self.assertNotIn('password_hash', response.get_json()['user'])
        for path in ('/api/schema', '/api/history', '/api/admin/users'):
            self.assertEqual(client.get(path).status_code, 403)
        self.assertEqual(client.post('/api/execute', json={'sql': 'SELECT 1'}).status_code, 403)
        self.assertEqual(client.post('/api/auth/change_password', json={'old_password': INITIAL_PASSWORD, 'new_password': 'weak'}).status_code, 400)
        response = client.post('/api/auth/change_password', json={'old_password': INITIAL_PASSWORD, 'new_password': CHANGED_PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get('/api/schema').status_code, 200)
        with contextlib.closing(sqlite3.connect(self.app.config['USERS_DB_PATH'])) as conn:
            password_hash = conn.execute('SELECT password_hash FROM app_users WHERE id=?', (user_id,)).fetchone()[0]
            self.assertNotEqual(password_hash, CHANGED_PASSWORD)
            self.assertTrue(check_password_hash(password_hash, CHANGED_PASSWORD))
        client.post('/api/auth/logout', json={})
        self.assertEqual(client.get('/api/schema').status_code, 401)

    def test_csrf_required_and_no_user_data_without_login(self):
        outsider = self.app.test_client()
        response = outsider.post('/api/auth/login', json={'username': 'admin', 'password': CHANGED_PASSWORD}, use_csrf=False)
        self.assertEqual(response.status_code, 403)
        for path in ('/api/schema', '/api/history', '/api/favorites', '/api/model', '/api/examples', '/api/admin/users'):
            self.assertEqual(outsider.get(path).status_code, 401, path)
        self.assertEqual(self.admin.post('/api/favorites/add', json={'sql': 'SELECT 1'}, use_csrf=False).status_code, 403)

    def test_roles_export_and_server_side_confidential_access(self):
        queries = [
            'SELECT * FROM conf_student_personal',
            'SELECT COUNT(*) FROM "CONF_STUDENT_PERSONAL"',
            'SELECT s.id FROM students s WHERE EXISTS (SELECT 1 FROM conf_student_financial c WHERE c.student_id=s.id)',
            'WITH hidden AS (SELECT * FROM conf_student_personal) SELECT * FROM hidden',
        ]
        with contextlib.closing(sqlite3.connect(self.state['DB_PATH'])) as conn, conn:
            conn.execute('CREATE VIEW exposed_personal AS SELECT * FROM conf_student_personal')
        queries.append('SELECT * FROM exposed_personal')
        for role in ('guest', 'analyst', 'operator'):
            self.create_user(role, role)
            client = authenticated_client(self.app, role)
            schema = client.get('/api/schema').get_json()['schema']
            self.assertEqual(len(schema), 14)
            self.assertFalse(any(table.startswith('conf_') for table in schema))
            self.assertEqual(client.get('/api/admin/users').status_code, 403)
            self.assertEqual(client.post('/api/admin/users', json={}).status_code, 403)
            self.assertEqual(client.post('/api/execute', json={'sql': 'SELECT COUNT(*) FROM students'}).get_json()['rows'], [[80]])
            for sql in queries:
                with self.subTest(role=role, sql=sql):
                    response = client.post('/api/execute', json={'sql': sql})
                    self.assertEqual(response.status_code, 403, response.get_json())
                    self.assertTrue(response.get_json()['permission_denied'])
            for fmt in ('csv', 'excel'):
                response = client.post('/api/export', json={'format': fmt, 'columns': ['ФИО'], 'rows': [['Анна']]})
                self.assertEqual(response.status_code, 403 if role == 'guest' else 200)
                if response.status_code == 200:
                    self.assertIn('Анна', response.get_json()['data'])
            response = client.post('/api/execute', json={'sql': "UPDATE students SET first_name='Тест' WHERE id=1"}).get_json()
            if role == 'operator':
                self.assertTrue(response['sandbox'], response)
            else:
                self.assertTrue(response['readonly_blocked'], response)
        self.assertEqual(len(self.admin.get('/api/schema').get_json()['schema']), 18)
        self.assertEqual(self.admin.post('/api/execute', json={'sql': 'SELECT COUNT(*) FROM conf_student_personal'}).get_json()['rows'], [[80]])

    def test_history_favorites_and_legacy_entries_are_isolated(self):
        self.create_user('alice')
        self.create_user('boris')
        alice = authenticated_client(self.app, 'alice')
        boris = authenticated_client(self.app, 'boris')
        legacy = [{'id': 'legacy', 'question': 'Старые данные', 'sql': 'SELECT 1'}]
        Path(self.state['HIST_PATH']).write_text(json.dumps(legacy), encoding='utf-8')
        Path(self.state['FAV_PATH']).write_text(json.dumps(legacy), encoding='utf-8')
        question = 'Топ-5 студентов по среднему баллу за экзамены'
        alice.post('/api/generate', json={'question': question})
        boris.post('/api/generate', json={'question': question})
        alice_history = alice.get('/api/history').get_json()['history']
        self.assertEqual(len(alice_history), 1)
        self.assertEqual(boris.post('/api/history/remove', json={'id': alice_history[0]['id']}).status_code, 404)
        self.assertEqual(len(alice.get('/api/history').get_json()['history']), 1)
        alice.post('/api/history/clear', json={})
        self.assertEqual(alice.get('/api/history').get_json()['history'], [])
        self.assertEqual(len(boris.get('/api/history').get_json()['history']), 1)
        item = alice.post('/api/favorites/add', json={'question': 'Test', 'sql': 'SELECT 1'}).get_json()['item']
        self.assertEqual(boris.get('/api/favorites').get_json()['favorites'], [])
        self.assertEqual(boris.post('/api/favorites/remove', json={'id': item['id']}).status_code, 404)
        self.assertEqual(alice.post('/api/favorites/remove', json={'id': item['id']}).status_code, 200)
        for path in (self.state['HIST_PATH'], self.state['FAV_PATH']):
            self.assertIn(legacy[0], json.loads(Path(path).read_text(encoding='utf-8')))

    def test_admin_role_changes_deletion_and_session_revocation(self):
        user_id = self.create_user('worker', 'analyst')
        client = authenticated_client(self.app, 'worker')
        self.assertEqual(self.admin.patch(f'/api/admin/users/{user_id}', json={'role': 'admin'}).status_code, 200)
        self.assertEqual(client.get('/api/schema').status_code, 401)
        client = authenticated_client(self.app, 'worker', CHANGED_PASSWORD)
        self.assertEqual(client.get('/api/admin/users').status_code, 200)
        self.assertEqual(self.admin.patch(f'/api/admin/users/{user_id}', json={'role': 'guest'}).status_code, 200)
        self.assertEqual(client.get('/api/admin/users').status_code, 401)
        own_id = self.admin.get('/api/auth/me').get_json()['user']['id']
        self.assertEqual(self.admin.delete(f'/api/admin/users/{own_id}', json={}).status_code, 400)
        self.assertEqual(self.admin.patch(f'/api/admin/users/{own_id}', json={'role': 'guest'}).status_code, 400)
        client = authenticated_client(self.app, 'worker', CHANGED_PASSWORD)
        self.assertEqual(self.admin.delete(f'/api/admin/users/{user_id}', json={}).status_code, 200)
        self.assertEqual(client.get('/api/schema').status_code, 401)

    def test_password_change_revokes_other_sessions_and_removes_initial_file(self):
        self.assertFalse(Path(self.app.config['INITIAL_PASSWORD_PATH']).exists())
        second = authenticated_client(self.app, 'admin', CHANGED_PASSWORD)
        response = self.admin.post('/api/auth/change_password', json={
            'old_password': CHANGED_PASSWORD, 'new_password': 'AnotherStrong987!'
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(second.get('/api/schema').status_code, 401)
        self.assertEqual(self.admin.get('/api/schema').status_code, 200)

    def test_signed_session_survives_application_restart(self):
        def restarted_app():
            state = runpy.run_path(str(ROOT / 'app.py'), run_name='verification')['index'].__globals__
            configure_test_auth(state['app'], self.directory)
            state['app'].secret_key = None
            return state['app']

        first = restarted_app()
        client = authenticated_client(first, 'admin', CHANGED_PASSWORD)
        cookie = client.get_cookie('session')
        self.assertTrue((self.directory / '.session-key').exists())
        second = restarted_app()
        restored = second.test_client()
        restored.set_cookie('session', cookie.value)
        response = restored.get('/api/auth/me')
        self.assertTrue(response.get_json()['authenticated'])
        self.assertEqual(response.get_json()['user']['username'], 'admin')


if __name__ == '__main__':
    unittest.main()
