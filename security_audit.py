"""Аудит, одноразовые подтверждения SQL и ограничения запросов по IP."""

import hashlib
import json
import math
import secrets
import threading
import time
from collections import deque
from contextlib import closing
from datetime import date, datetime, timedelta

from flask import current_app, g, jsonify, request, session

from auth import connect_users


def initialize_security_tables(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, username TEXT NOT NULL,
        action TEXT NOT NULL, details TEXT NOT NULL,
        ip_address TEXT NOT NULL, timestamp TEXT NOT NULL,
        status_code INTEGER NOT NULL
    )''')
    conn.execute('CREATE INDEX IF NOT EXISTS audit_timestamp ON audit_log(timestamp, id)')
    conn.execute('''CREATE TABLE IF NOT EXISTS sql_previews (
        token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL,
        session_version INTEGER NOT NULL, sql_hash TEXT NOT NULL,
        fingerprint TEXT NOT NULL, expires REAL NOT NULL
    )''')


def sha256(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def issue_preview(sql, fingerprint):
    token = secrets.token_urlsafe(32)
    user = g.current_user
    with closing(connect_users()) as conn, conn:
        conn.execute('DELETE FROM sql_previews WHERE expires < ?', (time.time(),))
        conn.execute('INSERT INTO sql_previews VALUES (?,?,?,?,?,?)',
                     (sha256(token), user['id'], user['session_version'], sha256(sql), fingerprint,
                      time.time() + current_app.config['SQL_PREVIEW_TTL']))
    return token


def consume_preview(token, sql):
    if not isinstance(token, str) or len(token) > 100:
        return None
    user = g.current_user
    with closing(connect_users()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT * FROM sql_previews WHERE token_hash=?', (sha256(token),)).fetchone()
        if not row or row['expires'] < time.time() or row['user_id'] != user['id'] or row['session_version'] != user['session_version'] or row['sql_hash'] != sha256(sql):
            return None
        conn.execute('DELETE FROM sql_previews WHERE token_hash=?', (sha256(token),))
        return row['fingerprint']


def register_security(app):
    app.config.update(SQL_TIMEOUT_SECONDS=3.0, SQL_PREVIEW_TTL=120,
                      RATE_LIMIT_ENABLED=True,
                      RATE_LIMITS={'all': (120, 60), 'login': (10, 60),
                                   'generate': (10, 60), 'execute': (30, 60), 'export': (20, 60)})
    requests_by_ip = {}
    rate_lock = threading.Lock()

    @app.before_request
    def limit_requests():
        if not app.config['RATE_LIMIT_ENABLED'] or not request.path.startswith('/api/'):
            return
        bucket = {'/api/auth/login': 'login', '/api/generate': 'generate',
                  '/api/execute': 'execute', '/api/export': 'export'}.get(request.path)
        now = time.monotonic()
        with rate_lock:
            # Удаляем неактивные IP; не доверяем клиентскому X-Forwarded-For.
            for key in list(requests_by_ip):
                queue, window = requests_by_ip[key]
                while queue and queue[0] <= now - window:
                    queue.popleft()
                if not queue:
                    del requests_by_ip[key]
            queues = []
            for name in ('all', bucket):
                if name is None or name not in app.config['RATE_LIMITS']:
                    continue
                maximum, window = app.config['RATE_LIMITS'][name]
                queue, _ = requests_by_ip.setdefault((request.remote_addr, name), (deque(), window))
                if len(queue) >= maximum:
                    retry = max(1, math.ceil(window - (now - queue[0])))
                    response = jsonify(error=f'Слишком много запросов. Повторите через {retry} сек.', retry_after=retry)
                    response.status_code = 429
                    response.headers['Retry-After'] = str(retry)
                    return response
                queues.append(queue)
            for queue in queues:
                queue.append(now)

    @app.after_request
    def record_audit(response):
        event = None
        path = request.path
        if request.method == 'POST':
            event = {'/api/auth/login': 'login', '/api/auth/logout': 'logout',
                     '/api/auth/change_password': 'password_change',
                     '/api/generate': 'sql_generate', '/api/execute': 'sql_execute',
                     '/api/export': 'export', '/api/admin/users': 'user_create'}.get(path)
        if path.startswith('/api/admin/users/') and request.method in ('PATCH', 'DELETE'):
            event = 'user_role_change' if request.method == 'PATCH' else 'user_delete'
        if not event:
            return response
        data = request.get_json(silent=True)
        data = data if isinstance(data, dict) else {}
        output = response.get_json(silent=True)
        output = output if isinstance(output, dict) else {}
        result = output.get('result', output)
        result = result if isinstance(result, dict) else {}
        if event == 'sql_execute' and result.get('sandbox'):
            event = 'sql_preview'
        events = [event]
        if event == 'sql_generate' and output.get('sql') and not output.get('error'):
            events.append('sql_preview' if result.get('sandbox') else 'sql_execute')
        details = {'status': response.status_code}
        for key, limit in (('question', 500), ('sql', 2000), ('format', 10), ('role', 20)):
            value = output.get(key, data.get(key))
            if isinstance(value, str):
                details[key] = value[:limit]
        if path.startswith('/api/admin/users/'):
            details['target_user_id'] = path.rsplit('/', 1)[-1]
        if event == 'user_create':
            details['target_username'] = str(data.get('username', ''))[:40]
        details['outcome'] = ('blocked' if result.get('security_blocked') or result.get('readonly_blocked')
                              else 'error' if response.status_code >= 400 or result.get('error')
                              else 'preview' if result.get('sandbox') else 'committed' if result.get('committed') else 'success')
        for key in ('count', 'affected_rows', 'timeout'):
            if key in result:
                details[key] = result[key]
        user = getattr(g, 'current_user', None)
        user_id = user['id'] if user else session.get('user_id')
        username = user['username'] if user else str(data.get('username', ''))[:40]
        with closing(connect_users()) as conn, conn:
            if user_id and not username:
                row = conn.execute('SELECT username FROM app_users WHERE id=?', (user_id,)).fetchone()
                username = row['username'] if row else ''
            for action in events:
                conn.execute('INSERT INTO audit_log (user_id,username,action,details,ip_address,timestamp,status_code) VALUES (?,?,?,?,?,?,?)',
                             (user_id, username, action, json.dumps(details, ensure_ascii=False), request.remote_addr or '',
                              datetime.now().isoformat(timespec='seconds'), response.status_code))
        return response

    @app.get('/api/admin/audit')
    def audit_log():
        where, parameters = [], []
        for field, column in (('user', 'username'), ('action', 'action')):
            value = request.args.get(field, '').strip()
            if value:
                where.append(f'{column} = ?')
                parameters.append(value[:80])
        try:
            start, end = request.args.get('date_from'), request.args.get('date_to')
            if start:
                where.append('timestamp >= ?')
                parameters.append(date.fromisoformat(start).isoformat() + 'T00:00:00')
            if end:
                where.append('timestamp < ?')
                parameters.append((date.fromisoformat(end) + timedelta(days=1)).isoformat() + 'T00:00:00')
            if start and end and start > end:
                raise ValueError()
            page = int(request.args.get('page', 1))
            limit = int(request.args.get('limit', 50))
            if page < 1 or not 1 <= limit <= 100:
                raise ValueError()
        except (ValueError, OverflowError):
            return jsonify(error='Некорректный период или номер страницы'), 400
        clause = ' WHERE ' + ' AND '.join(where) if where else ''
        with closing(connect_users()) as conn:
            total = conn.execute('SELECT COUNT(*) FROM audit_log' + clause, parameters).fetchone()[0]
            rows = conn.execute('SELECT * FROM audit_log' + clause + ' ORDER BY id DESC LIMIT ? OFFSET ?',
                                parameters + [limit, (page - 1) * limit]).fetchall()
        return jsonify(logs=[dict(row) | {'details': json.loads(row['details'])} for row in rows],
                       total=total, page=page, limit=limit)
