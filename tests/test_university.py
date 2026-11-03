"""Проверки учебной базы без запуска Ollama и изменения локальных данных."""

import contextlib
import io
import runpy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DATABASE = runpy.run_path(str(ROOT / 'database.py'))


class UniversityDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'university.db'
        self.conn = sqlite3.connect(self.path)
        self.addCleanup(self.conn.close)
        with contextlib.redirect_stdout(io.StringIO()):
            DATABASE['create_tables'](self.conn)
            DATABASE['fill_data'](self.conn)

    def test_schema_counts_and_foreign_keys(self):
        tables = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        self.assertEqual(len(tables), 18)
        for table, expected in {'faculties': 4, 'departments': 9,
                                'specializations': 9, 'professors': 12,
                                'courses': 16, 'students': 80,
                                'dormitory_assignments': 40,
                                'conf_student_personal': 80,
                                'conf_student_financial': 80}.items():
            with self.subTest(table=table):
                self.assertEqual(self.conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], expected)
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual(self.conn.execute('PRAGMA foreign_keys').fetchone()[0], 1)

    def test_repeated_initialization_preserves_user_changes(self):
        self.conn.execute("UPDATE students SET first_name = 'Изменено' WHERE id = 1")
        self.conn.commit()
        before = list(self.conn.iterdump())
        with contextlib.redirect_stdout(io.StringIO()):
            DATABASE['create_tables'](self.conn)
            DATABASE['fill_data'](self.conn)
        self.assertEqual(list(self.conn.iterdump()), before)

    def test_seed_is_reproducible(self):
        with contextlib.closing(sqlite3.connect(':memory:')) as other:
            with contextlib.redirect_stdout(io.StringIO()):
                DATABASE['create_tables'](other)
                DATABASE['fill_data'](other)
            self.assertEqual(list(self.conn.iterdump()), list(other.iterdump()))

    def test_database_path_is_relative_to_script(self):
        self.assertEqual(DATABASE['DB_PATH'], ROOT / 'university.db')

    def test_application_reads_university_schema_and_join_queries(self):
        with patch('requests.get'), contextlib.redirect_stdout(io.StringIO()):
            module = runpy.run_path(str(ROOT / 'app.py'), run_name='verification')
        state = module['index'].__globals__
        self.assertEqual(Path(state['DB_PATH']), ROOT / 'university.db')
        state['DB_PATH'] = str(self.path)
        state['HIST_PATH'] = str(self.path.parent / 'history.json')
        state['FAV_PATH'] = str(self.path.parent / 'favorites.json')
        client = module['app'].test_client()
        page = client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertIn('университета', page.get_data(as_text=True))
        schema = client.get('/api/schema').get_json()['schema']
        self.assertEqual(len(schema), 18)
        self.assertEqual(schema['students']['row_count'], 80)
        examples = client.get('/api/examples').get_json()['examples']
        self.assertEqual(len(examples), 12)
        self.assertTrue(any('студент' in question for question in examples))
        queries = [
            'SELECT COUNT(*) AS count FROM students',
            "SELECT s.last_name FROM students s JOIN specializations sp ON s.specialization_id = sp.id JOIN departments d ON sp.department_id = d.id JOIN faculties f ON d.faculty_id = f.id WHERE f.short_name = 'ФИТ'",
            'SELECT d.name, COUNT(da.student_id) FROM dormitories d LEFT JOIN dormitory_assignments da ON d.id = da.dormitory_id GROUP BY d.id',
            'SELECT c.name, p.name FROM course_prerequisites cp JOIN courses c ON cp.course_id = c.id JOIN courses p ON cp.prerequisite_course_id = p.id',
            'SELECT gh.old_score, gh.new_score, s.last_name FROM grade_history gh JOIN grades g ON gh.grade_id = g.id JOIN students s ON g.student_id = s.id',
        ]
        for sql in queries:
            with self.subTest(sql=sql):
                result = client.post('/api/execute', json={'sql': sql}).get_json()
                self.assertNotIn('error', result)
                self.assertGreater(result['count'], 0)
        before = list(self.conn.iterdump())
        result = client.post('/api/execute', json={'sql': 'DELETE FROM students'}).get_json()
        self.assertTrue(result['readonly_blocked'])
        self.assertEqual(list(self.conn.iterdump()), before)


if __name__ == '__main__':
    unittest.main()
