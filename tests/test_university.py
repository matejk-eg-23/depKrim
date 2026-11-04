"""Проверки учебной базы без запуска Ollama и изменения локальных данных."""

import contextlib
import io
import json
import runpy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from repair_demo_data import repair_demo_data
from auth_helpers import configure_test_auth, authenticated_client

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
        for table, expected in {'faculties': 13, 'departments': 52,
                                'specializations': 59, 'professors': 55,
                                'courses': 58, 'students': 260,
                                'dormitory_assignments': 130,
                                'conf_student_personal': 260,
                                'conf_student_financial': 260}.items():
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
        self.assertEqual(DATABASE['DB_PATH'], ROOT / 'grsu.db')

    def test_grsu_catalog_and_every_faculty_has_demo_students(self):
        self.assertEqual(self.conn.execute("SELECT code FROM specializations WHERE name='Кибербезопасность'").fetchone()[0], '6-05-0533-12')
        self.assertEqual(self.conn.execute("SELECT name FROM faculties WHERE short_name='ФаМИ'").fetchone()[0], 'Факультет математики и информатики')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM departments WHERE unit_type='кафедра'").fetchone()[0], 49)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM departments WHERE unit_type='цикл'").fetchone()[0], 3)
        counts = self.conn.execute('SELECT f.id,COUNT(s.id) FROM faculties f LEFT JOIN specializations sp ON sp.faculty_id=f.id LEFT JOIN students s ON s.specialization_id=sp.id GROUP BY f.id').fetchall()
        self.assertEqual(len(counts), 13)
        self.assertTrue(all(count == 20 for _, count in counts))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM specializations WHERE department_id IS NOT NULL').fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM students s JOIN specializations sp ON s.specialization_id=sp.id JOIN faculties f ON sp.faculty_id=f.id WHERE s.group_name NOT LIKE f.short_name || '-%'").fetchone()[0], 0)

    def test_belarusian_demo_grades_contacts_and_currency(self):
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM grades WHERE grade_type='экзамен' AND (score IS NULL OR score<1 OR score>10)").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM grades WHERE grade_type='зачет' AND (score IS NOT NULL OR assessment_result NOT IN ('зачтено','не зачтено'))").fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM grades g LEFT JOIN enrollments e ON e.student_id=g.student_id AND e.course_id=g.course_id WHERE e.id IS NULL OR g.grade_date<e.enrollment_date').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM grades g JOIN students s ON g.student_id=s.id JOIN specializations sp ON s.specialization_id=sp.id JOIN courses c ON c.id=g.course_id JOIN departments d ON d.id=c.department_id WHERE d.faculty_id<>sp.faculty_id').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT DISTINCT currency FROM conf_student_financial').fetchall(), [('BYN',)])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM conf_student_personal WHERE phone NOT LIKE '+375%' OR personal_number NOT LIKE 'DEMO-%' OR email NOT LIKE '%@example.invalid'").fetchone()[0], 0)

    def test_student_names_agree_and_professors_have_multiple_departments(self):
        female = DATABASE['FEMALE_FIRST_NAMES']
        for first, last, patronymic in self.conn.execute(
                'SELECT first_name, last_name, patronymic FROM students'):
            with self.subTest(first=first, last=last):
                if first in female:
                    self.assertTrue(last.endswith('а'), last)
                    self.assertTrue(patronymic.endswith('на'), patronymic)
                else:
                    self.assertFalse(last.endswith('а'), last)
                    self.assertTrue(patronymic.endswith('ич'), patronymic)
        professors = self.conn.execute(
            'SELECT professor_id, COUNT(*), SUM(rate) FROM professor_departments '
            'GROUP BY professor_id HAVING COUNT(*) > 1 ORDER BY professor_id'
        ).fetchall()
        self.assertEqual(professors, [(10, 2, 1.0), (11, 2, 1.0), (12, 2, 1.0)])

    def test_repair_preserves_related_data_and_is_repeatable(self):
        for prof_id, _, _, department_id in DATABASE['SECONDARY_DEPARTMENTS']:
            self.conn.execute('DELETE FROM professor_departments WHERE professor_id=? AND department_id=?',
                              (prof_id, department_id))
        self.conn.execute("UPDATE students SET first_name='Роман', last_name='Козлова', patronymic='Алексеевна' WHERE id=1")
        self.conn.execute("UPDATE students SET first_name='Юлия', last_name='Соколов', patronymic='Иванович' WHERE id=2")
        self.conn.commit()
        grades_before = self.conn.execute('SELECT * FROM grades').fetchall()
        self.assertEqual(repair_demo_data(self.conn), (2, 3))
        self.assertEqual(self.conn.execute('SELECT last_name, patronymic FROM students WHERE id=1').fetchone(),
                         ('Козлов', 'Алексеевич'))
        self.assertEqual(self.conn.execute('SELECT last_name, patronymic FROM students WHERE id=2').fetchone(),
                         ('Соколова', 'Ивановна'))
        self.assertEqual(self.conn.execute('SELECT * FROM grades').fetchall(), grades_before)
        self.assertEqual(repair_demo_data(self.conn), (0, 0))
        self.assertEqual(self.conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def generation_state(self):
        with patch('requests.get'), contextlib.redirect_stdout(io.StringIO()):
            module = runpy.run_path(str(ROOT / 'app.py'), run_name='verification')
        state = module['index'].__globals__
        state['DB_PATH'] = str(self.path)
        state['HIST_PATH'] = str(self.path.parent / 'history.json')
        state['FAV_PATH'] = str(self.path.parent / 'favorites.json')
        configure_test_auth(state['app'], self.path.parent)
        return state

    def test_all_suggested_questions_use_valid_templates(self):
        state = self.generation_state()
        client = authenticated_client(state['app'])
        for question in client.get('/api/examples').get_json()['examples']:
            with self.subTest(question=question), patch('requests.post') as post:
                result = state['generate_sql'](question)
                self.assertIsNone(result['error'], result)
                self.assertEqual(result['model'], 'проверенный шаблон')
                post.assert_not_called()
        with patch('requests.post') as post:
            result = client.post('/api/generate', json={
                'question': '  ПОКАЖИ всех студентов факультета математики и информатики?! '
            }).get_json()
            self.assertEqual(result['result']['count'], 20, result)
            post.assert_not_called()

    def test_all_templates_compile_and_execute(self):
        state = self.generation_state()
        examples = json.loads((ROOT / 'prompts.json').read_text(encoding='utf-8'))
        for example in examples:
            with self.subTest(question=example['question']):
                self.assertEqual(state['check_generated_sql'](example['sql']), (True, ''))
                self.conn.execute(example['sql']).fetchall()

    def test_model_receives_relationships_values_and_repairs_invalid_column(self):
        state = self.generation_state()
        with patch.dict(state, {'get_model': lambda: 'test-model'}), patch('requests.post') as post:
            post.return_value.json.side_effect = [
                {'response': 'SELECT nonexistent FROM students;'},
                {'response': '```sql\nSELECT COUNT(*) FROM students;\n```'},
            ]
            result = state['generate_sql']('Сосчитай общее число студентов университета')
            self.assertIsNone(result['error'], result)
            self.assertEqual(post.call_count, 2)
            first = post.call_args_list[0].kwargs['json']
            self.assertIn('FOREIGN KEY (department_id) REFERENCES departments(id)', first['prompt'])
            self.assertIn('Факультет математики и информатики', first['prompt'])
            self.assertEqual(first['options']['temperature'], 0)
            self.assertIn('no such column', post.call_args_list[1].kwargs['json']['prompt'])

    def test_invalid_or_destructive_model_sql_is_not_executed(self):
        state = self.generation_state()
        before = list(self.conn.iterdump())
        with patch.dict(state, {'get_model': lambda: 'test-model'}), patch('requests.post') as post:
            post.return_value.json.return_value = {'response': 'DROP TABLE students;'}
            self.assertIsNotNone(state['generate_sql']('Удали учебные записи')['error'])
            self.assertEqual(post.call_count, 1)
        with patch.dict(state, {'get_model': lambda: 'test-model'}), patch('requests.post') as post:
            post.return_value.json.return_value = {'response': 'SELECT nonexistent FROM students;'}
            self.assertIsNotNone(state['generate_sql']('Сосчитай все учебные записи')['error'])
            self.assertEqual(post.call_count, 2)
        self.assertEqual(list(self.conn.iterdump()), before)

    def test_application_reads_university_schema_and_join_queries(self):
        state = self.generation_state()
        client = authenticated_client(state['app'])
        page = client.get('/')
        self.assertEqual(page.status_code, 200)
        self.assertIn('ГрГУ имени Янки Купалы', page.get_data(as_text=True))
        schema = client.get('/api/schema').get_json()['schema']
        self.assertEqual(len(schema), 18)
        self.assertEqual(schema['students']['row_count'], 260)
        examples = client.get('/api/examples').get_json()['examples']
        self.assertEqual(len(examples), 12)
        self.assertTrue(any('студент' in question for question in examples))
        professors = client.post('/api/generate', json={
            'question': 'Покажи преподавателей, которые работают на нескольких кафедрах'
        }).get_json()
        self.assertEqual(professors['result']['count'], 3, professors)
        queries = [
            'SELECT COUNT(*) AS count FROM students',
            "SELECT s.last_name FROM students s JOIN specializations sp ON s.specialization_id = sp.id JOIN faculties f ON sp.faculty_id = f.id WHERE f.short_name = 'ФаМИ'",
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
        result = client.post('/api/execute', json={'sql': "UPDATE students SET first_name='Тест' WHERE id=1"}).get_json()
        self.assertTrue(result['sandbox'])
        self.assertEqual(list(self.conn.iterdump()), before)


if __name__ == '__main__':
    unittest.main()
