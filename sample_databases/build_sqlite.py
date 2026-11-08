"""Создаёт SQLite-файлы для проверки внешних подключений."""

import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory


def build():
    directory = Path(__file__).resolve().parent / 'sqlite'
    for seed in sorted(directory.glob('*.sql')):
        target = seed.with_suffix('.db')
        if target.exists():
            print(f'Уже существует: {target}')
            continue
        with TemporaryDirectory(dir=directory) as temp:
            candidate = Path(temp) / target.name
            with closing(sqlite3.connect(candidate)) as conn:
                conn.execute('PRAGMA foreign_keys = ON')
                conn.executescript(seed.read_text(encoding='utf-8'))
                if conn.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError(f'Нарушены внешние ключи: {seed.name}')
            candidate.replace(target)
        print(f'Создана база: {target}')


if __name__ == '__main__':
    build()
