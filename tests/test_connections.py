"""Реальные SQLite-подключения и проверки серверных адаптеров без рабочих БД."""

import json
import runpy
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from decimal import Decimal
from datetime import date
from unittest.mock import MagicMock, patch
from sqlalchemy.exc import DBAPIError

from auth_helpers import authenticated_client, configure_test_auth, INITIAL_PASSWORD, CHANGED_PASSWORD
from external_connections import decrypt_config, make_engine, readonly_connection, run_external

ROOT = Path(__file__).resolve().parents[1]


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.state = runpy.run_path(str(ROOT / 'app.py'), run_name='verification')['index'].__globals__
        self.app = self.state['app']
        configure_test_auth(self.app, self.directory)
        for field, filename in [('DB_PATH', 'local.db'), ('HIST_PATH', 'history.json'), ('FAV_PATH', 'favorites.json')]:
            self.state[field] = str(self.directory / filename)
        self.external = self.directory / 'library.db'
        for path, title in [(self.state['DB_PATH'], 'Учебная'), (self.external, 'Библиотека')]:
            with closing(sqlite3.connect(path)) as conn, conn:
                conn.executescript('CREATE TABLE books(id INTEGER PRIMARY KEY, title TEXT); CREATE TABLE loans(book_id INTEGER REFERENCES books(id));')
                conn.execute('INSERT INTO books VALUES (1,?)', (title,))
        self.admin = authenticated_client(self.app)

    def add(self, **kwargs):
        data = dict(name='Библиотека', kind='sqlite', database=str(self.external))
        data.update(kwargs)
        response = self.admin.post('/api/connections', json=data)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()['connection']['id']

    def activate(self, identifier):
        response = self.admin.post('/api/connections/activate', json={'id': identifier})
        self.assertEqual(response.status_code, 200, response.get_json())

    def test_sqlite_switch_schema_query_and_return_to_local_without_modification(self):
        before = self.external.read_bytes()
        identifier = self.add()
        self.activate(identifier)
        schema = self.admin.get('/api/schema').get_json()
        self.assertEqual(schema['connection']['id'], identifier)
        self.assertEqual(set(schema['schema']), {'books', 'loans'})
        self.assertEqual(schema['schema']['loans']['foreign_keys'], [
            {'table': 'books', 'columns': ['book_id'], 'referred_columns': ['id']},
        ])
        result = self.admin.post('/api/execute', json={'sql': 'SELECT * FROM books'}).get_json()
        self.assertEqual(result['rows'], [[1, 'Библиотека']])
        for sql in ['UPDATE books SET title=\'oops\'', 'DROP TABLE books', 'SELECT * INTO OUTFILE \'oops\' FROM books', 'SELECT evil_function()']:
            response = self.admin.post('/api/execute', json={'sql': sql})
            self.assertEqual(response.status_code, 403, response.get_json())
        self.assertEqual(self.external.read_bytes(), before)
        self.assertEqual(self.admin.get('/api/examples').get_json()['examples'], [])
        self.activate(None)
        result = self.admin.post('/api/execute', json={'sql': 'SELECT * FROM books'}).get_json()
        self.assertEqual(result['rows'], [[1, 'Учебная']])

    def test_credentials_encrypted_persist_after_restart_and_never_returned_or_audited(self):
        secret = 'p@ss:/#?%!*'
        with patch('external_connections.test_connection'):
            identifier = self.add(kind='postgresql', host='localhost', port=5432, database='demo', username='readonly', password=secret)
        with closing(sqlite3.connect(self.app.config['USERS_DB_PATH'])) as conn:
            encrypted = conn.execute('SELECT config_encrypted FROM external_connections WHERE id=?', (identifier,)).fetchone()[0]
        self.assertNotIn(secret, encrypted)
        key = (self.directory / '.connections-key').read_bytes()
        state = runpy.run_path(str(ROOT / 'app.py'), run_name='verification')['index'].__globals__
        configure_test_auth(state['app'], self.directory)
        restored = authenticated_client(state['app'], password=CHANGED_PASSWORD)
        listing = restored.get('/api/connections').get_json()
        self.assertEqual(listing['connections'][0]['id'], identifier)
        with state['app'].app_context():
            self.assertEqual(decrypt_config({'config_encrypted': encrypted})['password'], secret)
        with patch('external_connections.test_connection'):
            self.assertEqual(restored.post('/api/connections/activate', json={'id': identifier}).status_code, 200)
        audit = restored.get('/api/admin/audit').get_json()
        output = json.dumps([listing, audit])
        self.assertNotIn(secret, output)
        self.assertNotIn('config_encrypted', output)
        self.assertNotIn('readonly', json.dumps(audit))
        self.assertEqual(key, (self.directory / '.connections-key').read_bytes())

    def test_roles_ownership_delete_and_missing_profile_do_not_access_other_data(self):
        identifier = self.add()
        for username, role in [('otheradmin', 'admin'), ('reader', 'analyst'), ('writer', 'operator')]:
            self.admin.post('/api/admin/users', json={'username': username, 'role': role, 'password': INITIAL_PASSWORD})
            client = authenticated_client(self.app, username)
            if role == 'admin':
                self.assertEqual(client.get('/api/connections').get_json()['connections'], [])
                self.assertEqual(client.post('/api/connections/activate', json={'id': identifier}).status_code, 404)
                self.assertEqual(client.delete(f'/api/connections/{identifier}', json={}).status_code, 404)
            else:
                self.assertEqual(client.get('/api/connections').status_code, 403)
                self.assertEqual(client.post('/api/connections/activate', json={'id': identifier}).status_code, 403)
        self.activate(identifier)
        second = authenticated_client(self.app, password=CHANGED_PASSWORD)
        second.delete(f'/api/connections/{identifier}', json={})
        response = self.admin.post('/api/execute', json={'sql': 'SELECT * FROM books'})
        self.assertEqual(response.status_code, 409)
        self.assertNotIn('rows', response.get_json())

    def test_invalid_inputs_do_not_create_databases_or_save_failed_profiles(self):
        absent = self.directory / 'missing.db'
        cases = [dict(database=str(absent)), dict(database='relative.db'),
                 dict(database=self.app.config['USERS_DB_PATH']), dict(kind='oracle'),
                 dict(kind='postgresql', host='host dbname=other', database='demo', username='user'),
                 dict(kind='mysql', host='localhost', database='demo', username='user', port=99999)]
        for case in cases:
            data = dict(name='Bad', kind='sqlite', database=str(self.external))
            data.update(case)
            self.assertEqual(self.admin.post('/api/connections', json=data).status_code, 400)
        with patch('external_connections.test_connection', side_effect=RuntimeError('secret password')):
            response = self.admin.post('/api/connections', json=dict(name='Failed', kind='sqlite', database=str(self.external)))
        self.assertNotIn('secret password', response.get_data(as_text=True))
        self.assertEqual(self.admin.get('/api/connections').get_json()['connections'], [])
        self.assertFalse(absent.exists())

    def test_generation_uses_external_dialect_schema_without_university_examples(self):
        identifier = self.add()
        self.activate(identifier)
        response = MagicMock()
        response.json.return_value = {'response': 'SELECT title FROM books LIMIT 100'}
        with patch.dict(self.state, get_model=lambda: 'testmodel'), patch('requests.post', return_value=response) as post:
            result = self.admin.post('/api/generate', json={'question': 'Покажи все факультеты ГрГУ'})
        self.assertEqual(result.status_code, 200, result.get_json())
        self.assertEqual(result.get_json()['result']['rows'], [['Библиотека']])
        prompt = post.call_args.kwargs['json']['prompt']
        self.assertIn('Table: books', prompt)
        self.assertIn('FOREIGN KEY (book_id) REFERENCES books(id)', prompt)
        self.assertNotIn('Reference values', prompt)
        self.assertNotIn('students.specialization_id', prompt)

    def test_history_favorites_and_confirmation_are_bound_to_database(self):
        self.admin.post('/api/favorites/add', json={'question': 'local', 'sql': 'SELECT * FROM books'})
        token = self.admin.post('/api/execute', json={'sql': "UPDATE books SET title='Changed' WHERE id=1"}).get_json()['confirmation_token']
        identifier = self.add()
        self.activate(identifier)
        self.assertEqual(self.admin.get('/api/favorites').get_json()['favorites'], [])
        response = self.admin.post('/api/execute', json={'sql': "UPDATE books SET title='Changed' WHERE id=1", 'confirmation_token': token})
        self.assertEqual(response.status_code, 409)
        self.admin.post('/api/favorites/add', json={'question': 'external', 'sql': 'SELECT * FROM books'})
        self.activate(None)
        self.assertEqual([i['question'] for i in self.admin.get('/api/favorites').get_json()['favorites']], ['local'])
        with closing(sqlite3.connect(self.state['DB_PATH'])) as conn:
            self.assertEqual(conn.execute('SELECT title FROM books').fetchone()[0], 'Учебная')

    def test_missing_encryption_key_is_not_silently_replaced(self):
        identifier = self.add()
        (self.directory / '.connections-key').unlink()
        response = self.admin.post('/api/connections/activate', json={'id': identifier})
        self.assertEqual(response.status_code, 400)
        self.assertFalse((self.directory / '.connections-key').exists())

    def test_driver_urls_escape_credentials_and_remote_adapters_set_readonly_timeouts(self):
        for kind in ['postgresql', 'mysql']:
            config = dict(kind=kind, username='u@ser', password='p@ss:/#?%', host='localhost', port=5432 if kind == 'postgresql' else 3306, database='demo')
            with patch('external_connections.create_engine') as create:
                make_engine(config, 3)
                url = create.call_args.args[0]
                self.assertEqual(url.password, config['password'])
                self.assertEqual(url.username, config['username'])
                self.assertNotIn(config['password'], str(url))
            engine = MagicMock()
            conn = engine.connect.return_value.__enter__.return_value
            with self.app.app_context(), patch('external_connections.make_engine', return_value=engine):
                with readonly_connection(config):
                    pass
            calls = [c.args[0] for c in conn.exec_driver_sql.call_args_list]
            self.assertTrue(any('READ ONLY' in sql for sql in calls))
            self.assertTrue(any('timeout' in sql or 'MAX_EXECUTION_TIME' in sql for sql in calls))
            conn.rollback.assert_called_once()
            engine.dispose.assert_called_once()
            # Без подключения к серверу запрещаем запись, неизвестные функции и выгрузку в файл.
            for sql in ['DELETE FROM books', 'SELECT pg_read_file(\'/etc/passwd\')', 'SELECT * INTO OUTFILE \'x\' FROM books']:
                self.assertEqual(run_external({'kind': kind}, sql)['status'], 403)

    def test_remote_result_limits_types_timeout_and_error_redaction(self):
        config = {'kind': 'postgresql'}
        conn = MagicMock()
        cursor = conn.exec_driver_sql.return_value
        cursor.keys.return_value = ['amount', 'day', 'binary']
        cursor.fetchmany.return_value = [(Decimal('12.30'), date(2026, 1, 1), b'abc')] * 1001
        with self.app.app_context(), patch('external_connections.decrypt_config', return_value=config), patch('external_connections.readonly_connection') as opened:
            opened.return_value.__enter__.return_value = conn
            result = run_external({}, 'SELECT amount, day, binary FROM payments')
            self.assertEqual(len(result['rows']), 1000)
            self.assertTrue(result['truncated'])
            self.assertEqual(result['rows'][0], ['12.30', '2026-01-01', '[двоичные данные]'])
            cursor.close.assert_called_once()
            original = RuntimeError('private-password')
            original.sqlstate = '57014'
            conn.exec_driver_sql.side_effect = DBAPIError('SELECT', None, original)
            result = run_external({}, 'SELECT * FROM books')
            self.assertEqual(result['status'], 408)
            self.assertTrue(result['timeout'])
            self.assertNotIn('private-password', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
