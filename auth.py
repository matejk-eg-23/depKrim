"""Пользователи, сессии и серверная проверка прав."""

import os
import re
import secrets
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash
from flask.sessions import SecureCookieSessionInterface

PERM_READ = 1
PERM_DANGEROUS_SQL = 2
PERM_EXPORT = 4
PERM_MANAGE_USERS = 8
PERM_EXTERNAL_DB = 16
PERM_VIEW_CONFIDENTIAL = 64
ROLES = {'guest': 1, 'analyst': 5, 'operator': 7, 'admin': 127}
ROLE_NAMES = {'guest': 'Гость', 'analyst': 'Аналитик', 'operator': 'Оператор', 'admin': 'Администратор'}
auth_routes = Blueprint('auth', __name__)
_init_lock = threading.Lock()


def validate_password(password, username=''):
    if not isinstance(password, str) or not 10 <= len(password) <= 128:
        return 'Пароль должен содержать от 10 до 128 символов'
    if not (any(c.islower() for c in password) and any(c.isupper() for c in password)
            and any(c.isdigit() for c in password) and any(not c.isalnum() and not c.isspace() for c in password)):
        return 'Добавьте строчную и заглавную буквы, цифру и специальный символ'
    if username and username.casefold() in password.casefold():
        return 'Пароль не должен содержать логин'
    return ''


def connect_users():
    conn = sqlite3.connect(current_app.config['USERS_DB_PATH'])
    conn.row_factory = sqlite3.Row
    return conn


def initialize_auth(app):
    with _init_lock:
        if app.config.get('AUTH_INITIALIZED'):
            return
        if not app.secret_key:
            key_path = Path(app.config['SESSION_KEY_PATH'])
            try:
                with key_path.open('x', encoding='utf-8') as handle:
                    handle.write(secrets.token_hex(32))
            except FileExistsError:
                pass
            app.secret_key = key_path.read_text(encoding='utf-8').strip()
            if len(app.secret_key) < 32:
                raise RuntimeError('Файл ключа сессий повреждён')
        with closing(sqlite3.connect(app.config['USERS_DB_PATH'])) as conn, conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS app_users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('guest','analyst','operator','admin')),
                must_change_password INTEGER NOT NULL DEFAULT 1,
                session_version INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                last_login TEXT
            )''')
            from security_audit import initialize_security_tables
            initialize_security_tables(conn)
            from external_connections import initialize_connections
            initialize_connections(conn)
            if conn.execute('SELECT COUNT(*) FROM app_users').fetchone()[0] == 0:
                password = app.config.get('INITIAL_ADMIN_PASSWORD') or secrets.token_urlsafe(16) + 'aA1!'
                reason = validate_password(password, 'admin')
                if reason:
                    raise ValueError('INITIAL_ADMIN_PASSWORD: ' + reason)
                # Сохраняем только временный пароль первого входа, отдельно от Git.
                password_path = Path(app.config['INITIAL_PASSWORD_PATH'])
                password_path.write_text(password, encoding='utf-8')
                conn.execute('INSERT INTO app_users (username,password_hash,display_name,role,created_at) VALUES (?,?,?,?,?)',
                             ('admin', generate_password_hash(password), 'Администратор', 'admin', datetime.now().isoformat()))
                if not app.testing:
                    print(f'[AUTH] Первый вход: admin. Временный пароль: {password_path}')
        app.config['AUTH_INITIALIZED'] = True


def public_user(user):
    return {key: user[key] for key in ('id', 'username', 'display_name', 'role', 'created_at', 'last_login')} | {
        'permissions': ROLES[user['role']], 'role_name': ROLE_NAMES[user['role']],
        'must_change_password': bool(user['must_change_password']),
    }


def has_permission(permission):
    user = getattr(g, 'current_user', None)
    return bool(user and ROLES[user['role']] & permission == permission)


def register_auth(app):
    root = Path(app.root_path)
    app.config.update(
        USERS_DB_PATH=str(root / 'app_users.db'),
        SESSION_KEY_PATH=str(root / '.session-key'),
        INITIAL_PASSWORD_PATH=str(root / '.initial-admin-password'),
        INITIAL_ADMIN_PASSWORD=os.environ.get('INITIAL_ADMIN_PASSWORD'),
        SECRET_KEY=os.environ.get('SECRET_KEY'),
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax',
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    app.register_blueprint(auth_routes)

    class InitializedSessions(SecureCookieSessionInterface):
        def open_session(self, application, incoming_request):
            initialize_auth(application)
            return super().open_session(application, incoming_request)

    app.session_interface = InitializedSessions()

    @app.before_request
    def authenticate_request():
        initialize_auth(app)
        session.setdefault('csrf_token', secrets.token_urlsafe(32))
        g.current_user = None
        if 'user_id' in session:
            with closing(connect_users()) as conn:
                user = conn.execute('SELECT * FROM app_users WHERE id=?', (session['user_id'],)).fetchone()
            if user and user['session_version'] == session.get('session_version'):
                g.current_user = dict(user)
            else:
                session.clear()
                session['csrf_token'] = secrets.token_urlsafe(32)
        if not request.path.startswith('/api/'):
            return
        if request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
            token = request.headers.get('X-CSRF-Token', '')
            if not secrets.compare_digest(token, session['csrf_token']):
                return jsonify(error='Обновите страницу и повторите действие', code='csrf'), 403
        public_paths = {'/api/auth/login', '/api/auth/me', '/api/auth/logout'}
        if request.path in public_paths:
            return
        if not g.current_user:
            return jsonify(error='Требуется вход в систему', code='unauthorized'), 401
        if g.current_user['must_change_password'] and request.path != '/api/auth/change_password':
            return jsonify(error='Сначала смените временный пароль', code='password_change_required'), 403
        if request.path.startswith('/api/admin/') and not has_permission(PERM_MANAGE_USERS):
            return jsonify(error='Недостаточно прав'), 403
        if request.path == '/api/export' and not has_permission(PERM_EXPORT):
            return jsonify(error='Экспорт недоступен для вашей роли'), 403

    @app.after_request
    def private_response(response):
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response


@auth_routes.get('/api/auth/me')
def me():
    return jsonify(authenticated=bool(g.current_user),
                   user=public_user(g.current_user) if g.current_user else None,
                   csrf_token=session['csrf_token'])


@auth_routes.post('/api/auth/login')
def login():
    data = request.get_json() or {}
    username, password = data.get('username', ''), data.get('password', '')
    if not isinstance(username, str) or not isinstance(password, str) or len(password) > 128:
        return jsonify(error='Неверный логин или пароль'), 401
    with closing(connect_users()) as conn, conn:
        user = conn.execute('SELECT * FROM app_users WHERE username=?', (username.strip(),)).fetchone()
        if not user or not check_password_hash(user['password_hash'], password):
            return jsonify(error='Неверный логин или пароль'), 401
        session.clear()
        session.update(user_id=user['id'], session_version=user['session_version'],
                       csrf_token=secrets.token_urlsafe(32))
        session.permanent = True
        conn.execute('UPDATE app_users SET last_login=? WHERE id=?', (datetime.now().isoformat(), user['id']))
        user = conn.execute('SELECT * FROM app_users WHERE id=?', (user['id'],)).fetchone()
    return jsonify(ok=True, user=public_user(user), csrf_token=session['csrf_token'])


@auth_routes.post('/api/auth/logout')
def logout():
    session.clear()
    return jsonify(ok=True)


@auth_routes.post('/api/auth/change_password')
def change_password():
    data = request.get_json() or {}
    old, new = data.get('old_password', ''), data.get('new_password', '')
    if not isinstance(old, str) or not check_password_hash(g.current_user['password_hash'], old):
        return jsonify(error='Текущий пароль неверен'), 400
    reason = validate_password(new, g.current_user['username'])
    if reason:
        return jsonify(error=reason), 400
    if old == new:
        return jsonify(error='Новый пароль должен отличаться от текущего'), 400
    with closing(connect_users()) as conn, conn:
        conn.execute('UPDATE app_users SET password_hash=?,must_change_password=0,session_version=session_version+1 WHERE id=?',
                     (generate_password_hash(new), g.current_user['id']))
        session['session_version'] = conn.execute('SELECT session_version FROM app_users WHERE id=?',
                                                 (g.current_user['id'],)).fetchone()[0]
    if g.current_user['username'] == 'admin':
        Path(current_app.config['INITIAL_PASSWORD_PATH']).unlink(missing_ok=True)
    return jsonify(ok=True)


@auth_routes.route('/api/admin/users', methods=['GET', 'POST'])
def users():
    with closing(connect_users()) as conn, conn:
        if request.method == 'GET':
            return jsonify(users=[public_user(dict(row)) for row in conn.execute('SELECT * FROM app_users ORDER BY id')])
        data = request.get_json() or {}
        username, password = data.get('username', ''), data.get('password', '')
        role, display = data.get('role', 'guest'), data.get('display_name', '')
        if not isinstance(username, str) or not re.fullmatch(r'[a-zA-Z0-9_]{3,40}', username):
            return jsonify(error='Логин: от 3 до 40 латинских букв, цифр или подчёркиваний'), 400
        if not isinstance(role, str) or role not in ROLES:
            return jsonify(error='Неизвестная роль'), 400
        if not isinstance(display, str) or len(display) > 100:
            return jsonify(error='Имя должно содержать не более 100 символов'), 400
        reason = validate_password(password, username)
        if reason:
            return jsonify(error=reason), 400
        try:
            cursor = conn.execute('INSERT INTO app_users (username,password_hash,display_name,role,created_at) VALUES (?,?,?,?,?)',
                                  (username, generate_password_hash(password), display.strip() or username, role, datetime.now().isoformat()))
        except sqlite3.IntegrityError:
            return jsonify(error='Этот логин уже занят'), 409
        return jsonify(ok=True, id=cursor.lastrowid), 201


@auth_routes.route('/api/admin/users/<int:user_id>', methods=['PATCH', 'DELETE'])
def edit_user(user_id):
    if user_id == g.current_user['id']:
        return jsonify(error='Нельзя удалить свою учётную запись или изменить собственную роль'), 400
    with closing(connect_users()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        user = conn.execute('SELECT * FROM app_users WHERE id=?', (user_id,)).fetchone()
        if not user:
            return jsonify(error='Пользователь не найден'), 404
        new_role = (request.get_json() or {}).get('role') if request.method == 'PATCH' else None
        if user['role'] == 'admin' and (request.method == 'DELETE' or new_role != 'admin'):
            if conn.execute("SELECT COUNT(*) FROM app_users WHERE role='admin'").fetchone()[0] <= 1:
                return jsonify(error='Нельзя удалить последнего администратора или понизить его роль'), 400
        if request.method == 'DELETE':
            conn.execute('DELETE FROM external_connections WHERE user_id=?', (user_id,))
            conn.execute('DELETE FROM app_users WHERE id=?', (user_id,))
        else:
            role = new_role
            if not isinstance(role, str) or role not in ROLES:
                return jsonify(error='Неизвестная роль'), 400
            conn.execute('UPDATE app_users SET role=?,session_version=session_version+1 WHERE id=?', (role, user_id))
    return jsonify(ok=True)
