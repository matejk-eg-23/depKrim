from pathlib import Path

from flask.testing import FlaskClient

INITIAL_PASSWORD = 'Start12345!'
CHANGED_PASSWORD = 'Different12345!'


class CSRFClient(FlaskClient):
    """Имитирует передачу CSRF-токена браузером, без отключения защиты."""
    def open(self, *args, **kwargs):
        use_csrf = kwargs.pop('use_csrf', True)
        if use_csrf and kwargs.get('method', 'GET').upper() in ('POST', 'PUT', 'PATCH', 'DELETE'):
            token = super().open('/api/auth/me').get_json()['csrf_token']
            kwargs.setdefault('headers', {}).setdefault('X-CSRF-Token', token)
        return super().open(*args, **kwargs)


def configure_test_auth(app, directory):
    directory = Path(directory)
    app.config.update(TESTING=True, RATE_LIMIT_ENABLED=False, SECRET_KEY='test-key-' * 8,
                      USERS_DB_PATH=str(directory / 'app_users.db'),
                      SESSION_KEY_PATH=str(directory / '.session-key'),
                      CONNECTIONS_KEY_PATH=str(directory / '.connections-key'),
                      INITIAL_PASSWORD_PATH=str(directory / '.initial-admin-password'),
                      INITIAL_ADMIN_PASSWORD=INITIAL_PASSWORD)
    app.test_client_class = CSRFClient


def authenticated_client(app, username='admin', password=INITIAL_PASSWORD):
    client = app.test_client()
    response = client.post('/api/auth/login', json={'username': username, 'password': password})
    assert response.status_code == 200, response.get_json()
    if response.get_json()['user']['must_change_password']:
        response = client.post('/api/auth/change_password', json={'old_password': password, 'new_password': CHANGED_PASSWORD})
        assert response.status_code == 200, response.get_json()
    return client
