let currentUser = null;
let csrfToken = '';
const roleNames = {guest: 'Гость', analyst: 'Аналитик', operator: 'Оператор', admin: 'Администратор'};

function showAuthScreen(id) {
    if (id === 'loginScreen') {
        currentUser = null;
        for (const element of ['historyList', 'favoritesList', 'schemaTree', 'examplesDropdown', 'resultsZone', 'usersBody', 'auditBody']) {
            document.getElementById(element).replaceChildren();
        }
        const empty = document.createElement('div');
        empty.id = 'emptyState';
        empty.className = 'empty-state';
        empty.textContent = 'Задайте вопрос к учебной базе ГрГУ имени Янки Купалы';
        document.getElementById('resultsZone').append(empty);
        for (const key of Object.keys(window)) if (key.startsWith('tdexp_')) delete window[key];
        if (typeof currentSQL !== 'undefined') currentSQL = '';
        if (typeof pendingConfirmations !== 'undefined') pendingConfirmations.clear();
        if (typeof closeModal === 'function') closeModal();
    }
    for (const screen of ['loginScreen', 'passwordScreen', 'mainApp']) {
        document.getElementById(screen).classList.toggle('hidden', screen !== id);
    }
    document.getElementById('adminDialog').close();
    document.getElementById('auditDialog').close();
}

async function apiFetch(url, options = {}) {
    const headers = new Headers(options.headers || {});
    if (!['GET', 'HEAD'].includes((options.method || 'GET').toUpperCase())) headers.set('X-CSRF-Token', csrfToken);
    const response = await fetch(url, {...options, headers});
    if (response.status === 401 && url !== '/api/auth/login') {
        currentUser = null;
        showAuthScreen('loginScreen');
    }
    if (response.status === 403) {
        const error = await response.clone().json();
        if (error.code === 'password_change_required') showPasswordScreen();
    }
    return response;
}

async function sendJSON(url, body, method = 'POST') {
    const response = await apiFetch(url, {method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Действие не выполнено');
    return data;
}

function showMainApp() {
    showAuthScreen('mainApp');
    document.getElementById('currentUserName').textContent = `${currentUser.display_name} · ${currentUser.role_name}`;
    document.getElementById('adminButton').classList.toggle('hidden', !(currentUser.permissions & 8));
    document.getElementById('auditButton').classList.toggle('hidden', currentUser.role !== 'admin');
    loadSchema();
    loadExamples();
}

function showPasswordScreen() {
    showAuthScreen('passwordScreen');
    document.getElementById('passwordForm').reset();
    document.getElementById('passwordUsername').value = currentUser?.username || '';
    document.getElementById('passwordError').textContent = '';
    document.getElementById('passwordHint').textContent = currentUser?.must_change_password
        ? 'Замените временный пароль, чтобы продолжить работу.' : 'После смены пароля остальные сеансы будут завершены.';
}

async function checkAuth() {
    try {
        const response = await fetch('/api/auth/me');
        const data = await response.json();
        csrfToken = data.csrf_token;
        currentUser = data.user;
        if (!data.authenticated) showAuthScreen('loginScreen');
        else if (currentUser.must_change_password) showPasswordScreen();
        else showMainApp();
    } catch {
        document.getElementById('loginError').textContent = 'Не удалось подключиться к серверу';
    }
}

async function handleLogout() {
    try {
        await sendJSON('/api/auth/logout', {});
        location.reload();
    } catch (error) {
        alert(error.message);
    }
}

document.getElementById('loginForm').addEventListener('submit', async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const button = form.querySelector('button[type=submit]');
    button.disabled = true;
    document.getElementById('loginError').textContent = '';
    try {
        const data = await sendJSON('/api/auth/login', {
            username: document.getElementById('loginUsername').value.trim(),
            password: document.getElementById('loginPassword').value,
        });
        csrfToken = data.csrf_token;
        currentUser = data.user;
        form.reset();
        location.reload();
    } catch (error) {
        document.getElementById('loginError').textContent = error.message;
    } finally { button.disabled = false; }
});

document.getElementById('passwordForm').addEventListener('submit', async event => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button[type=submit]');
    const newPassword = document.getElementById('newPassword').value;
    const errorBox = document.getElementById('passwordError');
    errorBox.textContent = '';
    if (newPassword !== document.getElementById('confirmPassword').value) {
        errorBox.textContent = 'Пароли не совпадают';
        return;
    }
    button.disabled = true;
    try {
        await sendJSON('/api/auth/change_password', {old_password: document.getElementById('oldPassword').value, new_password: newPassword});
        event.target.reset();
        await checkAuth();
    } catch (error) { errorBox.textContent = error.message; }
    finally { button.disabled = false; }
});

async function loadUsers() {
    const response = await apiFetch('/api/admin/users');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    const body = document.getElementById('usersBody');
    body.replaceChildren();
    for (const user of data.users) {
        const row = document.createElement('tr');
        for (const value of [user.username, user.display_name]) {
            const cell = row.insertCell();
            cell.textContent = value;
        }
        const select = document.createElement('select');
        for (const [role, name] of Object.entries(roleNames)) select.add(new Option(name, role));
        select.value = user.role;
        select.disabled = user.id === currentUser.id;
        select.setAttribute('aria-label', `Роль пользователя ${user.username}`);
        row.insertCell().append(select);
        row.insertCell().textContent = user.must_change_password ? 'Требуется смена' : 'Изменён';
        const actions = row.insertCell();
        if (user.id === currentUser.id) actions.textContent = 'Ваша учётная запись';
        else {
            for (const [label, method] of [['Сохранить роль', 'PATCH'], ['Удалить', 'DELETE']]) {
                const button = document.createElement('button');
                button.className = 'btn-sm';
                button.textContent = label;
                button.addEventListener('click', async () => {
                    if (method === 'DELETE' && !confirm(`Удалить пользователя ${user.username}?`)) return;
                    try {
                        await sendJSON(`/api/admin/users/${user.id}`, {role: select.value}, method);
                        document.getElementById('adminMessage').textContent = method === 'PATCH' ? 'Роль сохранена. Пользователю потребуется войти заново.' : 'Пользователь удалён.';
                        document.getElementById('adminError').textContent = '';
                        await loadUsers();
                    } catch (error) { document.getElementById('adminError').textContent = error.message; }
                });
                actions.append(button);
            }
        }
        body.append(row);
    }
}

async function showAdminPanel() {
    document.getElementById('adminDialog').showModal();
    document.getElementById('adminError').textContent = '';
    document.getElementById('adminMessage').textContent = '';
    try { await loadUsers(); }
    catch (error) { document.getElementById('adminError').textContent = error.message; }
}

document.getElementById('addUserForm').addEventListener('submit', async event => {
    event.preventDefault();
    const button = event.currentTarget.querySelector('button[type=submit]');
    button.disabled = true;
    try {
        await sendJSON('/api/admin/users', {
            username: document.getElementById('newUserLogin').value.trim(),
            display_name: document.getElementById('newUserName').value.trim(),
            password: document.getElementById('newUserPassword').value,
            role: document.getElementById('newUserRole').value,
        });
        event.target.reset();
        document.getElementById('adminError').textContent = '';
        document.getElementById('adminMessage').textContent = 'Пользователь создан. Передайте ему логин и временный пароль; при первом входе потребуется смена.';
        await loadUsers();
    } catch (error) { document.getElementById('adminError').textContent = error.message; }
    finally { button.disabled = false; }
});

document.addEventListener('DOMContentLoaded', checkAuth);

let auditPage = 1;
const auditActionNames = {login: 'Вход', logout: 'Выход', password_change: 'Смена пароля',
    sql_generate: 'Генерация SQL', sql_preview: 'Пробный запуск', sql_execute: 'Выполнение SQL',
    export: 'Экспорт', user_create: 'Создание пользователя', user_role_change: 'Изменение роли', user_delete: 'Удаление пользователя'};
const auditOutcomes = {success: 'Успешно', error: 'Ошибка', blocked: 'Заблокировано', preview: 'Откат', committed: 'Сохранено'};

async function loadAudit(page = 1) {
    const query = new URLSearchParams({page, limit: 50});
    for (const [id, parameter] of [['auditUser', 'user'], ['auditAction', 'action'], ['auditFrom', 'date_from'], ['auditTo', 'date_to']]) {
        const value = document.getElementById(id).value.trim();
        if (value) query.set(parameter, value);
    }
    document.getElementById('auditError').textContent = '';
    try {
        const response = await apiFetch('/api/admin/audit?' + query);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'Журнал недоступен');
        auditPage = data.page;
        const body = document.getElementById('auditBody');
        body.replaceChildren();
        for (const log of data.logs) {
            const row = document.createElement('tr');
            for (const value of [new Date(log.timestamp).toLocaleString('ru-RU'), log.username || 'Без входа',
                auditActionNames[log.action] || log.action, `${auditOutcomes[log.details.outcome] || ''} · ${log.status_code}`,
                JSON.stringify(log.details, null, 2), log.ip_address]) {
                row.insertCell().textContent = value;
            }
            body.append(row);
        }
        if (!data.logs.length) {
            const row = body.insertRow();
            const cell = row.insertCell();
            cell.colSpan = 6;
            cell.textContent = 'Записей по выбранным фильтрам нет';
        }
        document.getElementById('auditPageInfo').textContent = `Страница ${data.page} · Всего записей: ${data.total}`;
        document.getElementById('auditPrevious').disabled = data.page <= 1;
        document.getElementById('auditNext').disabled = data.page * data.limit >= data.total;
    } catch (error) { document.getElementById('auditError').textContent = error.message; }
}

async function showAuditPanel() {
    document.getElementById('auditDialog').showModal();
    await loadAudit();
}

document.getElementById('auditFilterForm').addEventListener('submit', event => { event.preventDefault(); loadAudit(); });
document.getElementById('auditPrevious').addEventListener('click', () => loadAudit(auditPage - 1));
document.getElementById('auditNext').addEventListener('click', () => loadAudit(auditPage + 1));
