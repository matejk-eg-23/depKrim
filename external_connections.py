"""Личные подключения администратора. Внешние базы доступны только для чтения."""

import json
import math
import os
import threading
from contextlib import closing, contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app, g, has_request_context, jsonify, request, session
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool
import sqlparse
from sqlparse.sql import Function
from sqlparse import tokens as T

from auth import connect_users, has_permission, PERM_EXTERNAL_DB
from sql_security import inspect_sql, run_statement, SAFE_FUNCTIONS

_key_lock = threading.Lock()
CONNECTION_ERROR = 'Не удалось подключиться к базе. Проверьте адрес, реквизиты, права и доступность сервера.'


class ConnectionUnavailable(Exception):
    pass


def initialize_connections(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS external_connections (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
        name TEXT NOT NULL, kind TEXT NOT NULL, config_encrypted TEXT NOT NULL
    )''')


def cipher():
    key = os.environ.get('CONNECTIONS_KEY')
    if key:
        return Fernet(key.encode('ascii'))
    path = Path(current_app.config['CONNECTIONS_KEY_PATH'])
    with _key_lock:
        if not path.exists():
            # Создаётся только при сохранении подключения. Потерянный ключ нельзя заменять.
            with closing(connect_users()) as conn:
                if conn.execute('SELECT 1 FROM external_connections LIMIT 1').fetchone():
                    raise ValueError('Ключ подключений отсутствует. Восстановите .connections-key из резервной копии.')
            try:
                with path.open('xb') as handle:
                    handle.write(Fernet.generate_key())
            except FileExistsError:
                pass
        return Fernet(path.read_bytes().strip())


def validate_config(data):
    if not isinstance(data, dict):
        raise ValueError('Ожидаются параметры подключения')
    name, kind = data.get('name', ''), data.get('kind', '')
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        raise ValueError('Укажите название подключения: от 1 до 80 символов')
    if kind not in ('sqlite', 'postgresql', 'mysql'):
        raise ValueError('Поддерживаются SQLite, PostgreSQL и MySQL')
    config = {'kind': kind}
    if kind == 'sqlite':
        raw = data.get('database', '')
        if not isinstance(raw, str) or not raw or len(raw) > 1000 or not Path(raw).is_absolute():
            raise ValueError('Укажите абсолютный путь к существующему файлу SQLite на компьютере сервера')
        path = Path(raw).resolve(strict=True)
        if path == Path(current_app.config['USERS_DB_PATH']).resolve():
            raise ValueError('Служебную базу пользователей подключать нельзя')
        if not path.is_file():
            raise ValueError('Файл SQLite не найден')
        with path.open('rb') as handle:
            if handle.read(16) != b'SQLite format 3\x00':
                raise ValueError('Выбранный файл не является базой SQLite')
        config['database'] = str(path)
    else:
        for field in ('host', 'database', 'username'):
            value = data.get(field, '')
            if not isinstance(value, str) or not value.strip() or len(value) > 255 or any(ord(c) < 32 for c in value):
                raise ValueError('Заполните адрес сервера, имя базы и пользователя')
            config[field] = value.strip()
        # Запрещаем интерпретацию host как локального unix-сокета/строки опций драйвера.
        if any(c in config['host'] for c in '/\\= ?#@'):
            raise ValueError('В поле сервера укажите DNS-имя или IP-адрес без URL и параметров')
        port = data.get('port') or (5432 if kind == 'postgresql' else 3306)
        try:
            config['port'] = int(port)
            if isinstance(port, bool) or not 1 <= config['port'] <= 65535:
                raise ValueError()
            if str(config['port']) != str(port).strip():
                raise ValueError()
        except (ValueError, TypeError):
            raise ValueError('Порт должен быть числом от 1 до 65535') from None
        password = data.get('password', '')
        if not isinstance(password, str) or len(password) > 1024 or '\x00' in password:
            raise ValueError('Некорректный пароль подключения')
        config['password'] = password
    return name.strip(), config


def owned_connection(identifier):
    with closing(connect_users()) as conn:
        row = conn.execute('SELECT * FROM external_connections WHERE id=? AND user_id=?',
                           (identifier, g.current_user['id'])).fetchone()
    return dict(row) if row else None


def active_connection():
    if not has_request_context() or not has_permission(PERM_EXTERNAL_DB):
        return None
    if hasattr(g, '_active_connection'):
        return g._active_connection
    identifier = session.get('connection_id')
    if not identifier:
        return None
    row = owned_connection(identifier)
    if row is None:
        session.pop('connection_id', None)
        raise ConnectionUnavailable('Подключение удалено. Обновите страницу и выберите базу заново.')
    g._active_connection = row
    return row


def connection_key():
    row = active_connection()
    return f"external:{row['id']}" if row else 'local'


def decrypt_config(row):
    try:
        return json.loads(cipher().decrypt(row['config_encrypted'].encode('ascii')))
    except (InvalidToken, OSError, UnicodeError):
        raise ValueError('Не удалось расшифровать подключение. Проверьте ключ .connections-key.') from None


def safe_connection(row):
    # API никогда не отдаёт пароль, URL или зашифрованные реквизиты.
    return {'id': row['id'], 'name': row['name'], 'kind': row['kind'], 'readonly': True}


def make_engine(config, timeout):
    kind = config['kind']
    if kind == 'sqlite':
        url = URL.create('sqlite', database=Path(config['database']).as_uri() + '?mode=ro', query={'uri': 'true'})
        options = {'timeout': timeout}
    else:
        url = URL.create('postgresql+psycopg' if kind == 'postgresql' else 'mysql+pymysql',
                         username=config['username'], password=config['password'],
                         host=config['host'], port=config['port'], database=config['database'])
        options = {'connect_timeout': max(1, math.ceil(timeout))}
        if kind == 'mysql':
            options.update(read_timeout=max(1, math.ceil(timeout)), write_timeout=max(1, math.ceil(timeout)), charset='utf8mb4')
    return create_engine(url, connect_args=options, poolclass=NullPool, hide_parameters=True)


@contextmanager
def readonly_connection(config):
    timeout = current_app.config['SQL_TIMEOUT_SECONDS']
    engine = make_engine(config, timeout)
    try:
        with engine.connect() as conn:
            kind = config['kind']
            ms = max(1, int(timeout * 1000))
            if kind == 'postgresql':
                conn.exec_driver_sql('SET TRANSACTION READ ONLY')
                conn.exec_driver_sql(f'SET LOCAL statement_timeout = {ms}')
                conn.exec_driver_sql(f'SET LOCAL lock_timeout = {ms}')
            elif kind == 'mysql':
                # SET SESSION до начала рабочей транзакции; MAX_EXECUTION_TIME — для SELECT.
                conn.exec_driver_sql(f'SET SESSION MAX_EXECUTION_TIME = {ms}')
                conn.commit()
                conn.exec_driver_sql('START TRANSACTION READ ONLY')
            else:
                conn.exec_driver_sql('PRAGMA query_only=ON')
                import time
                deadline = time.monotonic() + timeout
                conn.connection.driver_connection.set_progress_handler(lambda: time.monotonic() >= deadline, 1000)
            try:
                yield conn
            finally:
                conn.rollback()
    finally:
        engine.dispose()


def test_connection(config):
    with readonly_connection(config) as conn:
        conn.exec_driver_sql('SELECT 1').close()


def external_schema(row):
    config = decrypt_config(row)
    with readonly_connection(config) as conn:
        inspector = inspect(conn)
        default_schema = inspector.default_schema_name
        result, text = {}, ''
        tables = inspector.get_table_names()
        if len(tables) > 100:
            raise ValueError('В подключённой схеме больше 100 таблиц. Выберите отдельную базу или схему для работы.')
        for table in sorted(tables):
            if table.startswith('sqlite_'):
                continue
            columns = inspector.get_columns(table)
            pk = inspector.get_pk_constraint(table).get('constrained_columns') or []
            fks = inspector.get_foreign_keys(table)
            result[table] = {'columns': [{'name': c['name'], 'type': str(c['type']), 'pk': c['name'] in pk} for c in columns],
                             'row_count': '—', 'foreign_keys': []}
            # Не сканируем внешние таблицы ради счётчика и не отправляем их содержимое в LLM.
            text += f'Table: {table}\nColumns:\n'
            for c in result[table]['columns']:
                text += f"  {c['name']} {c['type']}" + (' PRIMARY KEY' if c['pk'] else '') + '\n'
            for fk in fks:
                target = fk['referred_table']
                namespace = fk.get('referred_schema')
                columns = fk['constrained_columns']
                referred = [column or 'PRIMARY KEY' for column in fk['referred_columns']]
                if namespace and namespace != default_schema:
                    target = f'{namespace}.{target}'
                elif target in tables and not target.startswith('sqlite_'):
                    result[table]['foreign_keys'].append({
                        'table': target, 'columns': columns, 'referred_columns': referred,
                    })
                text += f"  FOREIGN KEY ({', '.join(columns)}) REFERENCES {target}({', '.join(referred)})\n"
            text += '\n'
            if len(text) > 20000:
                raise ValueError('Схема слишком велика для контекста модели. Используйте базу с меньшей схемой.')
    return text, result


def json_value(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        return '[двоичные данные]'
    return value


def run_external(row, sql, *, explain=False):
    kind, reason = inspect_sql(sql, allow_write=False)
    if reason:
        return {'security_blocked': True, 'readonly_blocked': True,
                'message': 'Внешние подключения доступны только для чтения. ' + reason, 'status': 403}
    statement = sqlparse.parse(sql)[0]
    def safe_functions(group):
        for token in group.tokens:
            if isinstance(token, Function) and (token.get_name() or '').casefold() not in SAFE_FUNCTIONS | {'cast', 'extract', 'date_trunc', 'concat', 'concat_ws', 'ceil', 'floor'}:
                return False
            if token.is_group and not safe_functions(token):
                return False
        return True
    if not safe_functions(statement) or any(t.normalized == 'INTO' or t.value == ':=' for t in statement.flatten() if t.ttype not in T.Literal.String.Single):
        return {'security_blocked': True, 'message': 'Запрещена функция или конструкция SQL во внешней базе', 'status': 403}
    config = decrypt_config(row)
    if config['kind'] == 'sqlite':
        return run_statement(config['database'], sql, confidential=True, explain=explain,
                             timeout=current_app.config['SQL_TIMEOUT_SECONDS'])
    try:
        with readonly_connection(config) as conn:
            cursor = conn.exec_driver_sql(('EXPLAIN ' if explain else '') + sql,
                                          execution_options={'stream_results': True, 'max_row_buffer': 1001})
            try:
                columns = list(cursor.keys())
                rows = [[json_value(v) for v in row] for row in cursor.fetchmany(1001)]
            finally:
                cursor.close()
        return {'columns': columns, 'rows': rows[:1000], 'count': min(len(rows), 1000),
                'truncated': len(rows) > 1000, 'statement_type': kind}
    except Exception as error:
        # DBAPI исключения могут включать SQL, параметры и реквизиты; наружу их не передаём.
        original = getattr(error, 'orig', None)
        code = getattr(original, 'sqlstate', None)
        args = getattr(original, 'args', ())
        if code == '57014' or (args and args[0] in (3024, 1205, 2013)):
            return {'error': 'Превышен таймаут выполнения запроса', 'timeout': True, 'status': 408}
        return {'error': 'Не удалось выполнить SQL: проверьте схему, синтаксис и права. Возможно, превышен таймаут.', 'status': 400}


def register_connections(app):
    app.config.setdefault('CONNECTIONS_KEY_PATH', str(Path(app.root_path) / '.connections-key'))

    @app.errorhandler(ConnectionUnavailable)
    def unavailable(error):
        return jsonify(error=str(error)), 409

    @app.before_request
    def protect_connections():
        if request.path.startswith('/api/connections') and not has_permission(PERM_EXTERNAL_DB):
            return jsonify(error='Подключения доступны только администратору'), 403

    @app.errorhandler(InvalidToken)
    def invalid_key(error):
        return jsonify(error='Не удалось расшифровать подключение. Проверьте ключ.'), 503

    @app.get('/api/connections')
    def list_connections():
        with closing(connect_users()) as conn:
            rows = conn.execute('SELECT * FROM external_connections WHERE user_id=? ORDER BY id', (g.current_user['id'],)).fetchall()
        return jsonify(connections=[safe_connection(row) for row in rows], active_id=session.get('connection_id'))

    @app.post('/api/connections')
    def add_connection():
        try:
            name, config = validate_config(request.get_json(silent=True))
            test_connection(config)
            encrypted = cipher().encrypt(json.dumps(config, ensure_ascii=False).encode('utf-8')).decode('ascii')
            with closing(connect_users()) as conn, conn:
                identifier = conn.execute('INSERT INTO external_connections (user_id,name,kind,config_encrypted) VALUES (?,?,?,?)',
                                          (g.current_user['id'], name, config['kind'], encrypted)).lastrowid
            return jsonify(connection={'id': identifier, 'name': name, 'kind': config['kind'], 'readonly': True}), 201
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except Exception:
            return jsonify(error=CONNECTION_ERROR), 400

    @app.post('/api/connections/activate')
    def activate_connection():
        data = request.get_json(silent=True) or {}
        identifier = data.get('id')
        if identifier is None:
            session.pop('connection_id', None)
            app.extensions['schema_cache'].invalidate(None)
            return jsonify(ok=True)
        if not isinstance(identifier, int) or isinstance(identifier, bool):
            return jsonify(error='Некорректный идентификатор подключения'), 400
        row = owned_connection(identifier)
        if not row:
            return jsonify(error='Подключение не найдено'), 404
        try:
            test_connection(decrypt_config(row))
        except Exception:
            return jsonify(error=CONNECTION_ERROR), 400
        session['connection_id'] = identifier
        app.extensions['schema_cache'].invalidate(identifier)
        return jsonify(ok=True)

    @app.delete('/api/connections/<int:identifier>')
    def delete_connection(identifier):
        with closing(connect_users()) as conn, conn:
            deleted = conn.execute('DELETE FROM external_connections WHERE id=? AND user_id=?', (identifier, g.current_user['id'])).rowcount
        if not deleted:
            return jsonify(error='Подключение не найдено'), 404
        app.extensions['schema_cache'].invalidate(identifier)
        if session.get('connection_id') == identifier:
            session.pop('connection_id', None)
        return jsonify(ok=True)
