"""Однократное исправление учебных ФИО и совместительства с резервной копией."""

import sqlite3
from contextlib import closing
from datetime import datetime

from database import DB_PATH, SECONDARY_DEPARTMENTS, agree_demo_name


def repair_demo_data(conn):
    """Не пересоздаёт базу и сохраняет идентификаторы и связанные записи."""
    changed_students = 0
    added_departments = 0
    with conn:
        for student_id, first, last, patronymic in conn.execute(
            'SELECT id, first_name, last_name, patronymic FROM students'
        ).fetchall():
            corrected = agree_demo_name(first, last, patronymic)
            if corrected != (last, patronymic):
                conn.execute('UPDATE students SET last_name=?, patronymic=? WHERE id=?',
                             (*corrected, student_id))
                changed_students += 1
        for prof_id, first, last, department_id in SECONDARY_DEPARTMENTS:
            # Не добавляем связи чужим записям, если учебные ID уже изменены.
            row = conn.execute('SELECT first_name, last_name FROM professors WHERE id=?',
                               (prof_id,)).fetchone()
            if row == (first, last):
                cursor = conn.execute(
                    'INSERT OR IGNORE INTO professor_departments '
                    '(professor_id, department_id, rate) VALUES (?, ?, 0.5)',
                    (prof_id, department_id))
                added_departments += cursor.rowcount
    return changed_students, added_departments


if __name__ == '__main__':
    with closing(sqlite3.connect(DB_PATH.as_uri() + '?mode=rw', uri=True)) as conn:
        conn.execute('PRAGMA foreign_keys = ON')
        backup_dir = DB_PATH.parent / '.local-backups'
        backup_dir.mkdir(exist_ok=True)
        backup_path = backup_dir / f'university-before-data-fix-{datetime.now():%Y%m%d-%H%M%S-%f}.db'
        with closing(sqlite3.connect(backup_path)) as backup:
            conn.backup(backup)
        students, departments = repair_demo_data(conn)
        print(f'Backup: {backup_path}')
        print(f'Corrected students: {students}; added department assignments: {departments}')
