"""Разбор и ограниченное выполнение одного SQLite-оператора."""

import hashlib
import re
import sqlite3
import threading
import time
from pathlib import Path

import sqlparse
from sqlparse import tokens as T

READ_COMMANDS = {'SELECT'}
WRITE_COMMANDS = {'INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER', 'DROP'}
FORBIDDEN_COMMANDS = {'ATTACH', 'DETACH', 'PRAGMA', 'VACUUM', 'REINDEX', 'ANALYZE',
                      'BEGIN', 'COMMIT', 'ROLLBACK', 'SAVEPOINT', 'RELEASE', 'REPLACE'}
SAFE_FUNCTIONS = {'abs', 'avg', 'coalesce', 'count', 'date', 'datetime', 'glob', 'ifnull',
                  'instr', 'julianday', 'length', 'like', 'lower', 'ltrim', 'max', 'min',
                  'nullif', 'replace', 'round', 'rtrim', 'strftime', 'substr', 'substring',
                  'sum', 'time', 'total', 'trim', 'typeof', 'unicode', 'unixepoch', 'upper',
                  'row_number', 'rank', 'dense_rank'}


def inspect_sql(sql, allow_write=False):
    if not isinstance(sql, str) or not sql.strip():
        return '', 'Пустой SQL-запрос'
    if len(sql) > 2000:
        return '', 'SQL-запрос превышает 2000 символов'
    try:
        statements = sqlparse.parse(sql.strip())
        if len(statements) != 1:
            return '', 'Разрешён только один SQL-оператор за запрос'
        statement = statements[0]
        flat = [token for token in statement.flatten() if not token.is_whitespace]
    except Exception:
        return '', 'Не удалось разобрать SQL'
    if any(token.ttype in T.Comment for token in flat):
        return '', 'SQL-комментарии запрещены'
    kind = statement.get_type()
    if kind not in READ_COMMANDS | WRITE_COMMANDS:
        return kind, 'Этот тип SQL-оператора запрещён'
    if kind in WRITE_COMMANDS and not allow_write:
        return kind, 'Для вашей роли разрешено только чтение данных'
    commands = {token.normalized for token in flat if token.ttype in T.Keyword}
    if commands & FORBIDDEN_COMMANDS:
        return kind, 'Служебные команды и управление транзакциями запрещены'
    if kind == 'SELECT' and any((token.ttype in T.DML or token.ttype in T.DDL) and token.normalized != 'SELECT' for token in flat):
        return kind, 'Вложенные команды изменения данных запрещены'
    structural = ' '.join(token.value if token.ttype not in T.Literal.String.Single else "'value'" for token in flat)
    if kind == 'CREATE' and not re.match(r'^CREATE\s+(?:TABLE|(?:UNIQUE\s+)?INDEX)\b', structural, re.I):
        return kind, 'Разрешено создание только обычных таблиц и индексов'
    if kind == 'DROP' and not re.match(r'^DROP\s+(?:TABLE|INDEX)\b', structural, re.I):
        return kind, 'Разрешено удаление только таблиц и индексов'
    if kind == 'ALTER' and not re.match(r'^ALTER\s+TABLE\b', structural, re.I):
        return kind, 'Разрешён только ALTER TABLE'
    if re.search(r'\b(?:sqlite_\w+|pragma_\w+|information_schema|pg_catalog)\b', structural, re.I):
        return kind, 'Прямой доступ к системным объектам запрещён'
    if re.search(r'\bWITH\s+RECURSIVE\b|\bUNION\b|\b(?:load_extension|readfile|writefile|randomblob|zeroblob)\s*\(', structural, re.I):
        return kind, 'Запрещённая конструкция или функция SQL'
    if re.search(r'\b(\d+)\s*=\s*\1\b|\b(?:OR|AND|WHERE)\s+(?:TRUE|FALSE)\b', structural, re.I):
        return kind, 'Тавтологическое условие запрещено'
    for left, equal, right in zip(flat, flat[1:], flat[2:]):
        if left.ttype in T.Literal.String.Single and right.ttype in T.Literal.String.Single and equal.value == '=' and left.value == right.value:
            return kind, 'Строковая тавтология запрещена'
    depth = 0
    for token in flat:
        if token.ttype in T.Punctuation:
            depth += (token.value == '(') - (token.value == ')')
            if depth > 8:
                return kind, 'Слишком высокая вложенность SQL'
    if sum(token.ttype in T.DML and token.normalized == 'SELECT' for token in flat) > 5:
        return kind, 'Слишком много вложенных SELECT'
    return kind, ''


def database_fingerprint(path, deadline=None):
    digest = hashlib.sha256()
    for filename in (Path(path), Path(str(path) + '-wal')):
        if filename.exists():
            digest.update(filename.name.encode('utf-8'))
            with filename.open('rb') as handle:
                for block in iter(lambda: handle.read(65536), b''):
                    if deadline is not None and time.monotonic() >= deadline:
                        raise TimeoutError()
                    digest.update(block)
        digest.update(b'\0')
    return digest.hexdigest()


def run_statement(path, sql, *, allow_write=False, confidential=False, preview=True,
                  timeout=3.0, expected_fingerprint=None, explain=False):
    kind, reason = inspect_sql(sql, allow_write)
    if reason:
        return {'security_blocked': True, 'readonly_blocked': kind in WRITE_COMMANDS and not allow_write,
                'message': reason, 'status': 403}
    modifying = kind in WRITE_COMMANDS
    # Имена проверяются также при подготовке оператора SQLite, включая триггеры и представления.
    if not confidential:
        tokens = sqlparse.parse(sql)[0].flatten()
        if any(token.ttype not in T.Literal.String.Single and
               re.search(r'\bconf_', token.value, re.I) for token in tokens):
            return {'security_blocked': True, 'permission_denied': True,
                    'message': 'Недостаточно прав для доступа к конфиденциальным таблицам', 'status': 403}
    conn = None
    timer = None
    denied = [False]
    deadline = time.monotonic() + timeout

    def authorize(action, first, second, database, source):
        tables = [first or '']
        if action in (sqlite3.SQLITE_ALTER_TABLE, sqlite3.SQLITE_CREATE_INDEX, sqlite3.SQLITE_DROP_INDEX):
            tables.append(second or '')
        if not confidential and any(table.casefold().startswith('conf_') for table in tables):
            denied[0] = True
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            function = (second or '').casefold()
            if function not in SAFE_FUNCTIONS and not (kind == 'ALTER' and function.startswith('sqlite_rename_')):
                denied[0] = True
                return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH, sqlite3.SQLITE_PRAGMA):
            denied[0] = True
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    try:
        mode = 'rw' if modifying and not explain else 'ro'
        conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=' + mode,
                               uri=True, isolation_level=None, timeout=max(0.001, timeout))
        conn.execute('PRAGMA foreign_keys = ON')
        if mode == 'ro':
            conn.execute('PRAGMA query_only = ON')
        if hasattr(conn, 'setlimit'):
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
            conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 2100)
        conn.set_authorizer(authorize)
        conn.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
        timer = threading.Timer(max(0.001, deadline - time.monotonic()), conn.interrupt)
        timer.daemon = True
        timer.start()
        conn.execute('BEGIN IMMEDIATE' if modifying and not explain else 'BEGIN')
        fingerprint = database_fingerprint(path, deadline) if modifying and not explain else None
        if expected_fingerprint and fingerprint != expected_fingerprint:
            return {'error': 'База изменилась после пробного запуска. Выполните проверку заново.', 'status': 409}
        cursor = conn.execute(('EXPLAIN ' if explain else '') + sql)
        columns = [column[0] for column in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(1001) if cursor.description else []
        cursor.close()
        affected = conn.total_changes
        if time.monotonic() >= deadline:
            raise TimeoutError()
        if modifying and not explain and not preview:
            conn.commit()
        else:
            conn.rollback()
        result = {'columns': columns, 'rows': [list(row) for row in rows[:1000]],
                  'count': min(len(rows), 1000), 'truncated': len(rows) > 1000,
                  'statement_type': kind}
        if modifying and not explain:
            result.update(sandbox=preview, committed=not preview, affected_rows=affected,
                          message='Пробный запуск завершён. Изменения отменены.' if preview else 'Изменения сохранены.')
            if preview:
                result['fingerprint'] = fingerprint
        return result
    except (sqlite3.Error, TimeoutError) as exc:
        if isinstance(exc, TimeoutError) or time.monotonic() >= deadline or 'interrupted' in str(exc):
            return {'error': 'Превышен таймаут выполнения SQL. Изменения отменены.', 'timeout': True, 'status': 408}
        if denied[0]:
            return {'security_blocked': True, 'permission_denied': True,
                    'message': 'Недостаточно прав или использована запрещённая функция SQL', 'status': 403}
        return {'error': 'Ошибка SQL: ' + str(exc), 'status': 400}
    finally:
        if timer:
            timer.cancel()
            timer.join()
        if conn:
            conn.set_progress_handler(None, 0)
            conn.set_authorizer(None)
            if conn.in_transaction:
                conn.rollback()
            conn.close()
