# database.py — создание демонстрационной университетской базы данных
# Запуск: python database.py

import sqlite3
import json
from contextlib import closing
from random import Random
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / 'data' / 'grsu.db'
CATALOG = json.loads((ROOT / 'grsu_catalog.json').read_text(encoding='utf-8'))


def grsu_db_path():
    legacy = ROOT / 'grsu.db'
    return legacy if not DB_PATH.exists() and legacy.is_file() else DB_PATH


STUDENTS_PER_FACULTY = 20
STUDENT_COUNT = STUDENTS_PER_FACULTY * len(CATALOG['faculties'])

FEMALE_FIRST_NAMES = {"Мария", "Анна", "Ольга", "Екатерина", "Наталья", "Татьяна",
                      "Юлия", "Елена", "Виктория", "София", "Полина", "Алиса"}
MALE_FIRST_NAMES = {"Алексей", "Дмитрий", "Сергей", "Иван", "Андрей", "Михаил",
                    "Павел", "Роман", "Кирилл", "Артем", "Никита", "Максим"}
SURNAME_PAIRS = [(name, name + "а") for name in (
    "Иванов", "Петров", "Сидоров", "Козлов", "Новиков", "Морозов", "Волков",
    "Лебедев", "Соколов", "Попов", "Кузнецов", "Васильев", "Смирнов")]
PATRONYMIC_PAIRS = [
    ("Алексеевич", "Алексеевна"), ("Дмитриевич", "Дмитриевна"),
    ("Сергеевич", "Сергеевна"), ("Иванович", "Ивановна"),
    ("Андреевич", "Андреевна"), ("Михайлович", "Михайловна"),
]
# У каждого совместителя две кафедры по половине ставки.
SECONDARY_DEPARTMENTS = [
    (10, "Елена", "Морозова", 2),
    (11, "Дмитрий", "Петров", 10),
    (12, "Татьяна", "Соколова", 1),
]


def agree_demo_name(first_name, last_name, patronymic):
    """Согласует только известные формы из искусственного учебного набора."""
    if first_name not in FEMALE_FIRST_NAMES | MALE_FIRST_NAMES:
        return last_name, patronymic
    index = 1 if first_name in FEMALE_FIRST_NAMES else 0
    for pair in SURNAME_PAIRS:
        if last_name in pair:
            last_name = pair[index]
            break
    for pair in PATRONYMIC_PAIRS:
        if patronymic in pair:
            patronymic = pair[index]
            break
    return last_name, patronymic


def create_tables(conn):
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        -- =====================================================
        -- ПУБЛИЧНЫЕ ТАБЛИЦЫ
        -- =====================================================

        CREATE TABLE IF NOT EXISTS faculties (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            short_name TEXT NOT NULL,
            dean_name TEXT,
            founded_year INTEGER,
            source_url TEXT
        );

        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            faculty_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            head_name TEXT,
            room_number TEXT,
            unit_type TEXT NOT NULL DEFAULT 'кафедра' CHECK(unit_type IN ('кафедра','цикл')),
            FOREIGN KEY (faculty_id) REFERENCES faculties(id)
        );

        CREATE TABLE IF NOT EXISTS specializations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            department_id INTEGER,
            faculty_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            code TEXT NOT NULL,
            degree_type TEXT,
            study_duration INTEGER,
            FOREIGN KEY (department_id) REFERENCES departments(id),
            FOREIGN KEY (faculty_id) REFERENCES faculties(id),
            UNIQUE(code, name)
        );

        CREATE TABLE IF NOT EXISTS professors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            patronymic TEXT,
            academic_degree TEXT NOT NULL,
            position TEXT NOT NULL,
            hire_date TEXT NOT NULL
        );

        -- Связь многие-ко-многим: преподаватель может числиться на нескольких кафедрах
        CREATE TABLE IF NOT EXISTS professor_departments (
            professor_id INTEGER NOT NULL,
            department_id INTEGER NOT NULL,
            rate REAL NOT NULL DEFAULT 1.0,
            PRIMARY KEY (professor_id, department_id),
            FOREIGN KEY (professor_id) REFERENCES professors(id),
            FOREIGN KEY (department_id) REFERENCES departments(id)
        );

        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            department_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            code TEXT NOT NULL UNIQUE,
            hours INTEGER NOT NULL,
            exam_type TEXT NOT NULL,
            semester INTEGER NOT NULL,
            FOREIGN KEY (department_id) REFERENCES departments(id)
        );

        -- Рекурсивная связь: пререквизиты курсов
        CREATE TABLE IF NOT EXISTS course_prerequisites (
            course_id INTEGER NOT NULL,
            prerequisite_course_id INTEGER NOT NULL,
            PRIMARY KEY (course_id, prerequisite_course_id),
            FOREIGN KEY (course_id) REFERENCES courses(id),
            FOREIGN KEY (prerequisite_course_id) REFERENCES courses(id)
        );

        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            patronymic TEXT,
            birth_date TEXT NOT NULL,
            enrollment_date TEXT NOT NULL,
            specialization_id INTEGER NOT NULL,
            course_year INTEGER NOT NULL,
            group_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'обучается',
            FOREIGN KEY (specialization_id) REFERENCES specializations(id)
        );

        CREATE TABLE IF NOT EXISTS course_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            professor_id INTEGER NOT NULL,
            room TEXT NOT NULL,
            day_of_week INTEGER NOT NULL,
            time_start TEXT NOT NULL,
            time_end TEXT NOT NULL,
            semester INTEGER NOT NULL,
            FOREIGN KEY (course_id) REFERENCES courses(id),
            FOREIGN KEY (professor_id) REFERENCES professors(id)
        );

        -- Связь многие-ко-многим: зачисления студентов на курсы
        CREATE TABLE IF NOT EXISTS enrollments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            semester INTEGER NOT NULL,
            enrollment_date TEXT NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (course_id) REFERENCES courses(id)
        );

        CREATE TABLE IF NOT EXISTS grades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            course_id INTEGER NOT NULL,
            grade_type TEXT NOT NULL,
            score INTEGER CHECK(score BETWEEN 1 AND 10),
            assessment_result TEXT,
            grade_date TEXT NOT NULL,
            semester INTEGER NOT NULL,
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (course_id) REFERENCES courses(id)
        );

        -- История изменения оценок
        CREATE TABLE IF NOT EXISTS grade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grade_id INTEGER NOT NULL,
            old_score INTEGER NOT NULL,
            new_score INTEGER NOT NULL,
            changed_by TEXT NOT NULL,
            changed_at TEXT NOT NULL,
            reason TEXT,
            FOREIGN KEY (grade_id) REFERENCES grades(id)
        );

        CREATE TABLE IF NOT EXISTS dormitories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            address TEXT NOT NULL,
            capacity INTEGER
        );

        CREATE TABLE IF NOT EXISTS dormitory_assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            dormitory_id INTEGER NOT NULL,
            room_number TEXT NOT NULL,
            move_in_date TEXT NOT NULL,
            move_out_date TEXT,
            FOREIGN KEY (student_id) REFERENCES students(id),
            FOREIGN KEY (dormitory_id) REFERENCES dormitories(id)
        );

        -- =====================================================
        -- СИНТЕТИЧЕСКИЕ КОНФИДЕНЦИАЛЬНЫЕ ДАННЫЕ
        -- Доступ предоставляется только администратору.
        -- =====================================================

        CREATE TABLE IF NOT EXISTS conf_student_personal (
            student_id INTEGER PRIMARY KEY,
            passport_series TEXT NOT NULL,
            passport_number TEXT NOT NULL,
            personal_number TEXT NOT NULL,
            phone TEXT NOT NULL,
            email TEXT,
            registration_address TEXT NOT NULL,
            actual_address TEXT,
            FOREIGN KEY (student_id) REFERENCES students(id)
        );

        CREATE TABLE IF NOT EXISTS conf_student_financial (
            student_id INTEGER PRIMARY KEY,
            contract_number TEXT NOT NULL,
            tuition_fee REAL NOT NULL,
            paid_amount REAL NOT NULL DEFAULT 0,
            debt_amount REAL NOT NULL DEFAULT 0,
            scholarship_amount REAL NOT NULL DEFAULT 0,
            payment_status TEXT NOT NULL DEFAULT 'оплачено',
            currency TEXT NOT NULL DEFAULT 'BYN',
            FOREIGN KEY (student_id) REFERENCES students(id)
        );

        CREATE TABLE IF NOT EXISTS conf_disciplinary_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            description TEXT NOT NULL,
            decision_date TEXT NOT NULL,
            decision_by TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'активно',
            FOREIGN KEY (student_id) REFERENCES students(id)
        );

        CREATE TABLE IF NOT EXISTS conf_professor_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            professor_id INTEGER NOT NULL,
            review_period TEXT NOT NULL,
            performance_score INTEGER NOT NULL,
            student_feedback_summary TEXT,
            disciplinary_notes TEXT,
            reviewed_by TEXT NOT NULL,
            review_date TEXT NOT NULL,
            FOREIGN KEY (professor_id) REFERENCES professors(id)
        );
    """)
    print("[OK] Таблицы созданы")



PROFESSORS = [('Александр', 'Иванов', 'Дмитриевич', 'к.т.н.', 'доцент', '2015-09-01'),
 ('Кирилл', 'Смирнов', 'Андреевич', 'д.т.н.', 'профессор', '2010-03-15'),
 ('Михаил', 'Козлов', 'Николаевич', 'д.ф.-м.н.', 'профессор', '2008-01-20'),
 ('Анна', 'Лебедева', 'Романовна', 'к.э.н.', 'доцент', '2012-08-10'),
 ('Павел', 'Новиков', 'Викторович', 'д.э.н.', 'профессор', '2005-04-01'),
 ('Ольга', 'Сидорова', 'Леонидовна', 'д.ю.н.', 'профессор', '2002-09-01'),
 ('Роман', 'Кузнецов', 'Григорьевич', 'к.ю.н.', 'доцент', '2018-01-15'),
 ('Наталья', 'Попова', 'Дмитриевна', 'д.ф.-м.н.', 'профессор', '1999-09-01'),
 ('Сергей', 'Васильев', 'Тимофеевич', 'к.ф.-м.н.', 'доцент', '2014-03-01'),
 ('Елена', 'Морозова', 'Сергеевна', 'к.т.н.', 'старший преподаватель', '2019-09-01'),
 ('Дмитрий', 'Петров', 'Ильич', 'к.ю.н.', 'доцент', '2016-02-01'),
 ('Татьяна', 'Соколова', 'Александровна', 'д.т.н.', 'профессор', '2011-09-01')]

CORE_COURSES = [(3, 'Базы данных', 'БД-101', 72, 'экзамен', 3),
 (3, 'Программирование на Python', 'ПР-101', 108, 'экзамен', 1),
 (4, 'Машинное обучение', 'МЛ-201', 90, 'экзамен', 5),
 (4, 'Нейронные сети', 'НС-301', 72, 'зачет', 6),
 (2, 'Криптография', 'КР-101', 72, 'экзамен', 3),
 (2, 'Безопасность компьютерных сетей', 'БКС-201', 54, 'зачет', 5),
 (6, 'Микроэкономика', 'МИК-101', 72, 'экзамен', 1),
 (6, 'Макроэкономика', 'МАК-201', 72, 'экзамен', 2),
 (5, 'Стратегический менеджмент', 'СМ-301', 54, 'зачет', 5),
 (9, 'Гражданское право', 'ГП-101', 90, 'экзамен', 2),
 (12, 'Уголовное право', 'УП-101', 90, 'экзамен', 2),
 (12, 'Криминалистика', 'КРМ-201', 72, 'экзамен', 3),
 (1, 'Математический анализ', 'МА-101', 144, 'экзамен', 1),
 (1, 'Линейная алгебра', 'ЛА-101', 108, 'экзамен', 1),
 (13, 'Общая физика', 'ОФ-101', 144, 'экзамен', 1),
 (14, 'Квантовая механика', 'КМ-301', 72, 'экзамен', 5)]

PREREQUISITES = [(3, 2), (4, 3), (5, 2), (6, 5), (8, 7), (16, 15)]

def fill_data(conn):
    """Создаёт учебные записи по официальному справочнику ГрГУ; заполненную БД не меняет."""
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
    for (table,) in tables:
        quoted = '"' + table.replace('"', '""') + '"'
        if conn.execute(f'SELECT 1 FROM {quoted} LIMIT 1').fetchone():
            print('[INFO] База уже содержит данные; заполнение пропущено')
            return
    random = Random(42)
    with conn:
        for faculty in CATALOG['faculties']:
            conn.execute('INSERT INTO faculties (id,name,short_name,dean_name,founded_year,source_url) VALUES (?,?,?,?,?,?)',
                         (faculty['id'], faculty['name'], faculty['short_name'], faculty['dean_name'], faculty['founded_year'], CATALOG['sources']['directory']))
        for unit in CATALOG['departments']:
            conn.execute('INSERT INTO departments (id,faculty_id,name,unit_type) VALUES (?,?,?,?)',
                         (unit['id'], unit['faculty_id'], unit['name'], unit['unit_type']))
        for spec in CATALOG['specializations']:
            conn.execute('INSERT INTO specializations (id,faculty_id,department_id,name,code,degree_type,study_duration) VALUES (?,?,?,?,?,?,?)',
                         (spec['id'], spec['faculty_id'], spec['department_id'], spec['name'], spec['code'], spec['degree_type'], spec['study_duration']))

        # Преподаватели и их ставки вымышлены; реальные деканы хранятся отдельно в faculties.
        professors = list(PROFESSORS)
        primary_units = [3, 4, 2, 6, 5, 9, 12, 1, 13, 3, 9, 4]
        assignments = [(pid, unit, 0.5 if pid in (10, 11, 12) else 1.0) for pid, unit in enumerate(primary_units, 1)]
        assignments += [(pid, unit, 0.5) for pid, _, _, unit in SECONDARY_DEPARTMENTS]
        first_names = sorted(FEMALE_FIRST_NAMES | MALE_FIRST_NAMES)
        for unit in CATALOG['departments']:
            if unit['id'] in primary_units:
                continue
            first = random.choice(first_names)
            last, patronymic = agree_demo_name(first, random.choice(SURNAME_PAIRS)[0], random.choice(PATRONYMIC_PAIRS)[0])
            professors.append((first, last, patronymic, 'учебная запись', 'преподаватель (учебная запись)', '2020-09-01'))
            assignments.append((len(professors), unit['id'], 1.0))
        conn.executemany('INSERT INTO professors (first_name,last_name,patronymic,academic_degree,position,hire_date) VALUES (?,?,?,?,?,?)', professors)
        conn.executemany('INSERT INTO professor_departments (professor_id,department_id,rate) VALUES (?,?,?)', assignments)

        # Дисциплины, часы и семестры иллюстрируют работу приложения, не заменяют учебный план ГрГУ.
        courses = list(CORE_COURSES)
        for unit in CATALOG['departments']:
            if unit['id'] not in {row[0] for row in courses}:
                courses.append((unit['id'], 'Введение: ' + unit['name'], f"DEMO-{unit['id']:03d}", 36, 'экзамен', 1))
        conn.executemany('INSERT INTO courses (department_id,name,code,hours,exam_type,semester) VALUES (?,?,?,?,?,?)', courses)
        conn.executemany('INSERT INTO course_prerequisites (course_id,prerequisite_course_id) VALUES (?,?)', PREREQUISITES)
        unit_faculty = {unit['id']: unit['faculty_id'] for unit in CATALOG['departments']}
        for cid, course in enumerate(courses, 1):
            professor = next(pid for pid, unit, _ in assignments if unit == course[0])
            day = 1 if cid == 5 else (cid - 1) % 5 + 1
            conn.execute('INSERT INTO course_schedules (course_id,professor_id,room,day_of_week,time_start,time_end,semester) VALUES (?,?,?,?,?,?,?)',
                         (cid, professor, f'DEMO-{course[0]:03d}', day, '09:00', '10:30', course[5]))

        students = []
        for faculty in CATALOG['faculties']:
            programs = [spec for spec in CATALOG['specializations'] if spec['faculty_id'] == faculty['id']]
            for index in range(STUDENTS_PER_FACULTY):
                first = random.choice(sorted(MALE_FIRST_NAMES) if faculty['id'] == 13 else first_names)
                last, patronymic = agree_demo_name(first, random.choice(SURNAME_PAIRS)[0], random.choice(PATRONYMIC_PAIRS)[0])
                spec = programs[index % len(programs)]
                year = index % 4 + 1
                admission = datetime(2026 - year, 9, 1)
                birth = (datetime(admission.year - 19, 1, 1) + timedelta(days=random.randint(0, 364))).strftime('%Y-%m-%d')
                group = f"{faculty['short_name']}-{spec['id']:02d}-{str(admission.year)[2:]}"
                status = 'академический отпуск' if index == 18 else 'отчислен' if index == 19 else 'обучается'
                students.append((first, last, patronymic, birth, admission.strftime('%Y-%m-%d'), spec['id'], year, group, status))
        conn.executemany('INSERT INTO students (first_name,last_name,patronymic,birth_date,enrollment_date,specialization_id,course_year,group_name,status) VALUES (?,?,?,?,?,?,?,?,?)', students)
        programs_by_id = {spec['id']: spec for spec in CATALOG['specializations']}
        for sid, student in enumerate(students, 1):
            faculty_id = programs_by_id[student[5]]['faculty_id']
            available = [cid for cid, course in enumerate(courses, 1) if unit_faculty[course[0]] == faculty_id and course[5] <= student[6] * 2]
            selected = random.sample(available, min(len(available), random.randint(3, 6)))
            for cid in selected:
                course = courses[cid - 1]
                semester = course[5]
                start_year = int(student[4][:4]) + (semester - 1) // 2
                enrolled = f'{start_year}-09-01' if semester % 2 else f'{start_year + 1}-02-01'
                grade_date = f'{start_year + 1}-01-20' if semester % 2 else f'{start_year + 1}-06-20'
                conn.execute('INSERT INTO enrollments (student_id,course_id,semester,enrollment_date) VALUES (?,?,?,?)', (sid, cid, semester, enrolled))
                score = random.randint(1, 10) if course[4] == 'экзамен' else None
                result = ('сдано' if score >= 4 else 'не сдано') if score is not None else ('не зачтено' if sid % 9 == 0 else 'зачтено')
                conn.execute('INSERT INTO grades (student_id,course_id,grade_type,score,assessment_result,grade_date,semester) VALUES (?,?,?,?,?,?,?)',
                             (sid, cid, course[4], score, result, grade_date, semester))
        exam_grades = conn.execute('SELECT id,score FROM grades WHERE score IS NOT NULL ORDER BY id LIMIT 3').fetchall()
        for gid, score in exam_grades:
            conn.execute('INSERT INTO grade_history (grade_id,old_score,new_score,changed_by,changed_at,reason) VALUES (?,?,?,?,?,?)',
                         (gid, max(1, score - 1), score, 'Учебный администратор', '2026-06-25 14:30:00', 'Вымышленный пример пересмотра отметки'))

        for dorm in CATALOG['dormitories']:
            conn.execute('INSERT INTO dormitories (id,name,address) VALUES (?,?,?)', (dorm['id'], dorm['name'], dorm['address']))
        residents = [(sid, student) for sid, student in enumerate(students, 1) if student[8] == 'обучается'][:STUDENT_COUNT // 2]
        for index, (sid, student) in enumerate(residents):
            conn.execute('INSERT INTO dormitory_assignments (student_id,dormitory_id,room_number,move_in_date) VALUES (?,?,?,?)',
                         (sid, index % len(CATALOG['dormitories']) + 1, f'DEMO-{index + 1:03d}', student[4]))
        for sid, student in enumerate(students, 1):
            address = f'Учебный адрес №{sid}, г. Гродно'
            conn.execute('INSERT INTO conf_student_personal (student_id,passport_series,passport_number,personal_number,phone,email,registration_address,actual_address) VALUES (?,?,?,?,?,?,?,?)',
                         (sid, 'DEMO', f'DEMO-P-{sid:04d}', f'DEMO-BY-{sid:04d}', f'+375 (00) 000-{sid:04d}', f'student{sid}@example.invalid', address, address))
            fee = random.choice([4000, 4500, 5000, 5500])
            paid = random.choice([0, fee // 2, fee])
            conn.execute('INSERT INTO conf_student_financial (student_id,contract_number,tuition_fee,paid_amount,debt_amount,scholarship_amount,payment_status,currency) VALUES (?,?,?,?,?,?,?,?)',
                         (sid, f'DEMO-Д-{sid:04d}', fee, paid, fee - paid, random.choice([0, 150, 200, 250]), 'оплачено' if paid == fee else 'долг', 'BYN'))
        for sid in (5, 12, 23, 34, 45):
            conn.execute('INSERT INTO conf_disciplinary_actions (student_id,action_type,description,decision_date,decision_by,status) VALUES (?,?,?,?,?,?)',
                         (sid, 'учебный пример', 'Вымышленная запись для проверки разграничения доступа', '2026-04-15', 'Учебный администратор', 'снято'))
        for pid in range(1, 6):
            conn.execute('INSERT INTO conf_professor_reviews (professor_id,review_period,performance_score,student_feedback_summary,disciplinary_notes,reviewed_by,review_date) VALUES (?,?,?,?,?,?,?)',
                         (pid, '2025/2026', random.randint(7, 10), 'Искусственный отзыв для демонстрации', None, 'Учебный администратор', '2026-06-30'))
    print(f"[OK] Учебная база ГрГУ: {len(CATALOG['faculties'])} факультетов, {len(CATALOG['departments'])} подразделений, {len(CATALOG['specializations'])} вариантов подготовки, {len(students)} студентов")


if __name__ == '__main__':
    path = grsu_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        create_tables(conn)
        fill_data(conn)
    print(f'[Готово] Учебная база сохранена: {path}')
