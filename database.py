# database.py — создание демонстрационной университетской базы данных
# Запуск: python database.py

import sqlite3
from random import Random
from pathlib import Path
from datetime import datetime, timedelta

DB_PATH = Path(__file__).resolve().parent / "university.db"


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
            dean_name TEXT NOT NULL,
            founded_year INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS departments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            faculty_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            head_name TEXT NOT NULL,
            room_number TEXT,
            FOREIGN KEY (faculty_id) REFERENCES faculties(id)
        );

        CREATE TABLE IF NOT EXISTS specializations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            department_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            code TEXT NOT NULL UNIQUE,
            degree_type TEXT NOT NULL,
            study_duration INTEGER NOT NULL,
            FOREIGN KEY (department_id) REFERENCES departments(id)
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
            score INTEGER NOT NULL,
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
            capacity INTEGER NOT NULL
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
        -- Ограничение доступа будет добавлено вместе с авторизацией.
        -- =====================================================

        CREATE TABLE IF NOT EXISTS conf_student_personal (
            student_id INTEGER PRIMARY KEY,
            passport_series TEXT NOT NULL,
            passport_number TEXT NOT NULL,
            snils TEXT NOT NULL,
            inn TEXT,
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


def fill_data(conn):
    """Заполняет пустую учебную БД; существующие данные не изменяет."""
    cursor = conn.cursor()
    tables = [row[0] for row in cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()]
    for table in tables:
        quoted_name = table.replace('"', '""')
        if cursor.execute(f'SELECT 1 FROM "{quoted_name}" LIMIT 1').fetchone():
            print("[INFO] База уже содержит данные; заполнение пропущено")
            return
    # Повторяемый набор для демонстрации и проверки SQL-примеров.
    random = Random(42)

    # ── Факультеты ──
    faculties = [
        ("Факультет информационных технологий", "ФИТ", "Проф. Соколов А.В.", 1998),
        ("Факультет экономики и управления", "ФЭУ", "Проф. Волкова Е.П.", 1965),
        ("Юридический факультет", "ЮФ", "Проф. Петров И.М.", 1954),
        ("Факультет естественных наук", "ФЕН", "Проф. Морозова Т.С.", 1972),
    ]
    cursor.executemany(
        "INSERT OR IGNORE INTO faculties (name, short_name, dean_name, founded_year) VALUES (?,?,?,?)",
        faculties
    )

    # ── Кафедры ──
    departments = [
        (1, "Кафедра программной инженерии", "Доц. Иванов Д.С.", "301"),
        (1, "Кафедра искусственного интеллекта", "Проф. Смирнов К.А.", "405"),
        (1, "Кафедра кибербезопасности", "Проф. Козлов М.Н.", "412"),
        (2, "Кафедра экономической теории", "Доц. Лебедева А.Р.", "205"),
        (2, "Кафедра менеджмента", "Проф. Новиков П.В.", "208"),
        (3, "Кафедра гражданского права", "Проф. Сидорова О.Л.", "102"),
        (3, "Кафедра уголовного права", "Доц. Кузнецов Р.Г.", "105"),
        (4, "Кафедра математики", "Проф. Попова Н.Д.", "501"),
        (4, "Кафедра физики", "Доц. Васильев С.Т.", "505"),
    ]
    cursor.executemany(
        "INSERT OR IGNORE INTO departments (faculty_id, name, head_name, room_number) VALUES (?,?,?,?)",
        departments
    )

    # ── Специальности ──
    specializations = [
        (1, "Программная инженерия", "09.03.04", "бакалавриат", 4),
        (2, "Искусственный интеллект и машинное обучение", "09.03.01", "бакалавриат", 4),
        (3, "Информационная безопасность", "10.03.01", "бакалавриат", 4),
        (4, "Экономика", "38.03.01", "бакалавриат", 4),
        (5, "Менеджмент", "38.03.02", "бакалавриат", 4),
        (6, "Юриспруденция", "40.03.01", "бакалавриат", 4),
        (7, "Правоохранительная деятельность", "40.03.02", "бакалавриат", 4),
        (8, "Математика и компьютерные науки", "01.03.02", "бакалавриат", 4),
        (9, "Физика", "03.03.01", "бакалавриат", 4),
    ]
    cursor.executemany(
        "INSERT OR IGNORE INTO specializations (department_id, name, code, degree_type, study_duration) VALUES (?,?,?,?,?)",
        specializations
    )

    # ── Преподаватели ──
    professors_data = [
        ("Александр", "Иванов", "Дмитриевич", "к.т.н.", "доцент", "2015-09-01"),
        ("Кирилл", "Смирнов", "Андреевич", "д.т.н.", "профессор", "2010-03-15"),
        ("Михаил", "Козлов", "Николаевич", "д.ф.-м.н.", "профессор", "2008-01-20"),
        ("Анна", "Лебедева", "Романовна", "к.э.н.", "доцент", "2012-08-10"),
        ("Павел", "Новиков", "Викторович", "д.э.н.", "профессор", "2005-04-01"),
        ("Ольга", "Сидорова", "Леонидовна", "д.ю.н.", "профессор", "2002-09-01"),
        ("Роман", "Кузнецов", "Григорьевич", "к.ю.н.", "доцент", "2018-01-15"),
        ("Наталья", "Попова", "Дмитриевна", "д.ф.-м.н.", "профессор", "1999-09-01"),
        ("Сергей", "Васильев", "Тимофеевич", "к.ф.-м.н.", "доцент", "2014-03-01"),
        ("Елена", "Морозова", "Сергеевна", "к.т.н.", "старший преподаватель", "2019-09-01"),
        ("Дмитрий", "Петров", "Ильич", "к.ю.н.", "доцент", "2016-02-01"),
        ("Татьяна", "Соколова", "Александровна", "д.т.н.", "профессор", "2011-09-01"),
    ]
    cursor.executemany(
        "INSERT OR IGNORE INTO professors (first_name, last_name, patronymic, academic_degree, position, hire_date) VALUES (?,?,?,?,?,?)",
        professors_data
    )

    # ── Связь преподавателей с кафедрами (совместительство) ──
    prof_deps = [
        (1, 1, 1.0),   # Иванов — ПИ
        (2, 2, 1.0),   # Смирнов — ИИ
        (3, 3, 1.0),   # Козлов — КБ
        (4, 4, 1.0),   # Лебедева — ЭТ
        (5, 5, 1.0),   # Новиков — Менеджмент
        (6, 6, 1.0),   # Сидорова — ГП
        (7, 7, 1.0),   # Кузнецов — УП
        (8, 8, 1.0),   # Попова — Математика
        (9, 9, 1.0),   # Васильев — Физика
        (10, 1, 0.5),  # Морозова — ПИ (совместительство 0.5)
        (11, 6, 0.5),  # Петров — ГП (совместительство)
        (12, 2, 0.5),  # Соколова — ИИ (совместительство)
    ]
    cursor.executemany(
        "INSERT OR IGNORE INTO professor_departments (professor_id, department_id, rate) VALUES (?,?,?)",
        prof_deps
    )

    # ── Курсы ──
    courses_data = [
        (1, "Базы данных", "БД-101", 72, "экзамен", 3),
        (1, "Программирование на Python", "ПР-101", 108, "экзамен", 1),
        (2, "Машинное обучение", "МЛ-201", 90, "экзамен", 5),
        (2, "Нейронные сети", "НС-301", 72, "зачет", 6),
        (3, "Криптография", "КР-101", 72, "экзамен", 3),
        (3, "Этичный хакинг", "ЭХ-201", 54, "зачет", 5),
        (4, "Микроэкономика", "МИК-101", 72, "экзамен", 1),
        (4, "Макроэкономика", "МАК-201", 72, "экзамен", 2),
        (5, "Стратегический менеджмент", "СМ-301", 54, "зачет", 5),
        (6, "Гражданское право", "ГП-101", 90, "экзамен", 2),
        (7, "Уголовное право", "УП-101", 90, "экзамен", 2),
        (7, "Криминалистика", "КРМ-201", 72, "экзамен", 3),
        (8, "Математический анализ", "МА-101", 144, "экзамен", 1),
        (8, "Линейная алгебра", "ЛА-101", 108, "экзамен", 1),
        (9, "Общая физика", "ОФ-101", 144, "экзамен", 1),
        (9, "Квантовая механика", "КМ-301", 72, "экзамен", 5),
    ]
    cursor.executemany(
        "INSERT OR IGNORE INTO courses (department_id, name, code, hours, exam_type, semester) VALUES (?,?,?,?,?,?)",
        courses_data
    )

    # ── Пререквизиты курсов ──
    prereqs = [
        (3, 2),   # МЛ требует Python
        (4, 3),   # НС требует МЛ
        (5, 2),   # КР требует Python
        (6, 5),   # ЭХ требует КР
        (8, 7),   # МАК требует МИК
        (16, 15), # КМ требует ОФ
    ]
    cursor.executemany(
        "INSERT OR IGNORE INTO course_prerequisites (course_id, prerequisite_course_id) VALUES (?,?)",
        prereqs
    )

    # ── Студенты ──
    first_names = ["Алексей", "Мария", "Дмитрий", "Анна", "Сергей", "Ольга", "Иван", "Екатерина",
                   "Андрей", "Наталья", "Михаил", "Татьяна", "Павел", "Юлия", "Роман", "Елена",
                   "Кирилл", "Виктория", "Артем", "София", "Никита", "Полина", "Максим", "Алиса"]
    last_names = ["Иванов", "Петров", "Сидоров", "Козлова", "Новиков", "Морозова", "Волков", "Лебедева",
                  "Соколов", "Попова", "Кузнецов", "Васильева", "Петрова", "Смирнов", "Козлов", "Иванова"]
    patronymics = ["Алексеевич", "Алексеевна", "Дмитриевич", "Дмитриевна", "Сергеевич", "Сергеевна",
                   "Иванович", "Ивановна", "Андреевич", "Андреевна", "Михайлович", "Михайловна"]
    groups = ["ПИ-101", "ПИ-102", "ИИ-201", "ИИ-202", "КБ-301", "КБ-302",
              "ЭК-101", "ЭК-102", "МН-201", "МН-202", "ЮР-101", "ЮР-102",
              "МТ-101", "МТ-102", "ФЗ-201", "ФЗ-202"]
    statuses = ["обучается", "обучается", "обучается", "обучается", "академический отпуск", "отчислен"]

    students_data = []
    for i in range(80):
        fn = random.choice(first_names)
        ln = random.choice(last_names)
        pat = random.choice(patronymics)
        if fn in ["Мария", "Анна", "Ольга", "Екатерина", "Наталья", "Татьяна", "Елена", "Виктория", "София", "Полина", "Алиса"]:
            pat = pat.replace("евич", "евна").replace("ич", "на")
        spec_id = random.randint(1, 9)
        course_year = random.randint(1, 4)
        group = random.choice(groups)
        birth = (datetime(2000, 1, 1) + timedelta(days=random.randint(0, 2000))).strftime("%Y-%m-%d")
        enroll = (datetime(2021, 9, 1) + timedelta(days=random.randint(0, 730))).strftime("%Y-%m-%d")
        status = random.choice(statuses)
        students_data.append((fn, ln, pat, birth, enroll, spec_id, course_year, group, status))

    cursor.executemany(
        "INSERT INTO students (first_name, last_name, patronymic, birth_date, enrollment_date, specialization_id, course_year, group_name, status) VALUES (?,?,?,?,?,?,?,?,?)",
        students_data
    )

    # ── Расписание ──
    days = [1, 2, 3, 4, 5]  # пн-пт
    times = [("09:00", "10:30"), ("10:45", "12:15"), ("13:00", "14:30"), ("14:45", "16:15"), ("16:30", "18:00")]
    rooms = ["301", "302", "405", "406", "412", "205", "208", "102", "105", "501", "505", "Ауд.1", "Ауд.2"]

    schedules = []
    for course_id in range(1, 17):
        prof_id = random.randint(1, 12)
        day = random.choice(days)
        t_start, t_end = random.choice(times)
        room = random.choice(rooms)
        sem = random.choice([1, 2, 3, 4, 5, 6, 7, 8])
        schedules.append((course_id, prof_id, room, day, t_start, t_end, sem))
    cursor.executemany(
        "INSERT INTO course_schedules (course_id, professor_id, room, day_of_week, time_start, time_end, semester) VALUES (?,?,?,?,?,?,?)",
        schedules
    )

    # ── Зачисления ──
    enrollments_data = []
    for student_id in range(1, 81):
        num_courses = random.randint(3, 6)
        course_ids = random.sample(range(1, 17), num_courses)
        for cid in course_ids:
            sem = random.choice([1, 2, 3, 4, 5, 6, 7, 8])
            ed = (datetime(2023, 9, 1) + timedelta(days=random.randint(0, 365))).strftime("%Y-%m-%d")
            enrollments_data.append((student_id, cid, sem, ed))
    cursor.executemany(
        "INSERT INTO enrollments (student_id, course_id, semester, enrollment_date) VALUES (?,?,?,?)",
        enrollments_data
    )

    # ── Оценки ──
    grade_types = ["экзамен", "зачет", "курсовая", "практика"]
    grades_data = []
    for student_id in range(1, 81):
        num_grades = random.randint(4, 10)
        for _ in range(num_grades):
            cid = random.randint(1, 16)
            gt = random.choice(grade_types)
            score = random.randint(2, 5) if gt == "зачет" else random.randint(60, 100)
            gd = (datetime(2023, 1, 15) + timedelta(days=random.randint(0, 500))).strftime("%Y-%m-%d")
            sem = random.randint(1, 8)
            grades_data.append((student_id, cid, gt, score, gd, sem))
    cursor.executemany(
        "INSERT INTO grades (student_id, course_id, grade_type, score, grade_date, semester) VALUES (?,?,?,?,?,?)",
        grades_data
    )

    # ── История изменения оценок (несколько записей) ──
    grade_history_data = [
        (1, 85, 92, "admin", "2024-01-20 14:30:00", "Пересмотр работы"),
        (5, 72, 78, "Иванов А.Д.", "2024-02-10 10:15:00", "Дополнительные задания"),
        (12, 60, 75, "Смирнов К.А.", "2024-01-25 16:00:00", "Устный пересчет"),
    ]
    cursor.executemany(
        "INSERT INTO grade_history (grade_id, old_score, new_score, changed_by, changed_at, reason) VALUES (?,?,?,?,?,?)",
        grade_history_data
    )

    # ── Общежития ──
    dorms = [
        ("Общежитие №1", "ул. Ленина, 25", 250),
        ("Общежитие №2", "ул. Гагарина, 14", 180),
        ("Общежитие №3", "пр. Мира, 78", 300),
    ]
    cursor.executemany(
        "INSERT OR IGNORE INTO dormitories (name, address, capacity) VALUES (?,?,?)",
        dorms
    )

    # ── Расселение ──
    dorm_assignments = []
    for student_id in range(1, 41):
        did = random.randint(1, 3)
        room = f"{random.randint(1, 5)}{random.randint(0, 9):02d}"
        mid = (datetime(2023, 9, 1) + timedelta(days=random.randint(0, 30))).strftime("%Y-%m-%d")
        dorm_assignments.append((student_id, did, room, mid, None))
    cursor.executemany(
        "INSERT INTO dormitory_assignments (student_id, dormitory_id, room_number, move_in_date, move_out_date) VALUES (?,?,?,?,?)",
        dorm_assignments
    )

    # ── Конфиденциальные данные студентов ──
    conf_personal = []
    for student_id in range(1, 81):
        ps = f"{random.randint(10, 99):02d}"
        pn = f"{random.randint(100000, 999999)}"
        snils = f"{random.randint(100, 999)}-{random.randint(100, 999)}-{random.randint(100, 999)} {random.randint(10, 99)}"
        inn = f"{random.randint(1000000000, 9999999999)}"
        phone = f"+7(9{random.randint(10, 99)}){random.randint(100, 999)}-{random.randint(10, 99)}-{random.randint(10, 99)}"
        email = f"student{student_id}@edu.ru"
        reg_addr = f"г. Москва, ул. {random.choice(['Ленина', 'Гагарина', 'Мира', 'Пушкина'])}, д. {random.randint(1, 100)}, кв. {random.randint(1, 200)}"
        conf_personal.append((student_id, ps, pn, snils, inn, phone, email, reg_addr, reg_addr))
    cursor.executemany(
        "INSERT INTO conf_student_personal (student_id, passport_series, passport_number, snils, inn, phone, email, registration_address, actual_address) VALUES (?,?,?,?,?,?,?,?,?)",
        conf_personal
    )

    # ── Финансовые данные ──
    conf_financial = []
    for student_id in range(1, 81):
        contract = f"Д-{random.randint(1000, 9999)}-{random.randint(2021, 2024)}"
        fee = random.choice([150000, 200000, 250000, 300000])
        paid = random.choice([0, fee, fee // 2, fee * 3 // 4])
        debt = fee - paid
        scholarship = random.choice([0, 0, 0, 3000, 5000, 7000, 10000])
        status = "оплачено" if debt == 0 else "долг" if debt > 0 else "переплата"
        conf_financial.append((student_id, contract, fee, paid, debt, scholarship, status))
    cursor.executemany(
        "INSERT INTO conf_student_financial (student_id, contract_number, tuition_fee, paid_amount, debt_amount, scholarship_amount, payment_status) VALUES (?,?,?,?,?,?,?)",
        conf_financial
    )

    # ── Дисциплинарные взыскания ──
    conf_disc = [
        (5, "замечание", "Опоздание на занятия более 5 раз", "2024-03-15", "Козлов М.Н.", "активно"),
        (12, "выговор", "Нарушение правил проживания в общежитии", "2024-02-20", "Администрация", "активно"),
        (23, "строгий выговор", "Использование чужих учетных данных", "2024-01-10", "Соколов А.В.", "снято"),
        (34, "замечание", "Неявка на экзамен без уважительной причины", "2024-04-05", "Иванов Д.С.", "активно"),
        (45, "выговор", "Повреждение университетского имущества", "2023-12-18", "Волкова Е.П.", "активно"),
    ]
    cursor.executemany(
        "INSERT INTO conf_disciplinary_actions (student_id, action_type, description, decision_date, decision_by, status) VALUES (?,?,?,?,?,?)",
        conf_disc
    )

    # ── Рецензии на преподавателей ──
    conf_reviews = [
        (1, "2023-2024", 85, "Хорошая подача материала, много практики", None, "Попова Н.Д.", "2024-06-15"),
        (2, "2023-2024", 92, "Отличные лекции, сложные но интересные задания", None, "Соколова А.А.", "2024-06-15"),
        (3, "2023-2024", 78, "Требуется больше практических занятий", "Студенты жалуются на строгость", "Иванов Д.С.", "2024-06-15"),
        (5, "2023-2024", 88, "Актуальные кейсы, хорошая обратная связь", None, "Лебедева А.Р.", "2024-06-15"),
        (8, "2023-2024", 95, "Выдающийся преподаватель, глубокие знания", None, "Васильев С.Т.", "2024-06-15"),
    ]
    cursor.executemany(
        "INSERT INTO conf_professor_reviews (professor_id, review_period, performance_score, student_feedback_summary, disciplinary_notes, reviewed_by, review_date) VALUES (?,?,?,?,?,?,?)",
        conf_reviews
    )

    conn.commit()
    print(f"[OK] Данные заполнены: студенты, курсы, оценки, общежития, конфиденциальные таблицы")


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    create_tables(conn)
    fill_data(conn)
    conn.close()
    print(f"[Готово] База данных сохранена: {DB_PATH}")
