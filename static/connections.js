function updateConnectionFields() {
    const kind = document.getElementById('connectionKind').value;
    const server = kind !== 'sqlite';
    document.getElementById('connectionServerFields').classList.toggle('hidden', !server);
    document.getElementById('connectionDatabaseLabel').textContent = server ? 'Имя базы данных' : 'Абсолютный путь к файлу на сервере';
    document.getElementById('connectionPort').value = kind === 'mysql' ? 3306 : 5432;
    for (const id of ['connectionHost', 'connectionUsername', 'connectionPort']) document.getElementById(id).required = server;
    document.getElementById('connectionPassword').value = '';
}

async function loadConnections() {
    const response = await apiFetch('/api/connections');
    const data = await response.json();
    if (!response.ok) throw new Error(data.error);
    const list = document.getElementById('connectionsList');
    list.replaceChildren();
    for (const connection of [{id: null, name: 'Учебная база ГрГУ', kind: 'sqlite'}, ...data.connections]) {
        const row = document.createElement('div');
        row.className = 'connection-row';
        const label = document.createElement('span');
        label.textContent = `${connection.name} · ${connection.kind}${connection.id === data.active_id ? ' · Активна' : ''}`;
        const activate = document.createElement('button');
        activate.className = 'btn-sm';
        activate.textContent = 'Выбрать';
        activate.disabled = connection.id === data.active_id;
        activate.onclick = () => connectionAction(async () => {
            if (activeDatabaseRequests) throw new Error('Дождитесь завершения текущего запроса');
            // Удаляем результаты и подтверждения, чтобы запрос к прежней базе нельзя было повторить случайно.
            await sendJSON('/api/connections/activate', {id: connection.id});
            location.reload();
        });
        row.append(label, activate);
        if (connection.id !== null) {
            const remove = document.createElement('button');
            remove.className = 'btn-sm';
            remove.textContent = 'Удалить';
            remove.onclick = () => connectionAction(async () => {
                if (activeDatabaseRequests) throw new Error('Дождитесь завершения текущего запроса');
                await sendJSON(`/api/connections/${connection.id}`, {}, 'DELETE');
                if (connection.id === data.active_id) location.reload();
                else await loadConnections();
            });
            row.append(remove);
        }
        list.append(row);
    }
}

async function connectionAction(action) {
    document.getElementById('connectionError').textContent = '';
    const buttons = [...document.querySelectorAll('#connectionsDialog button')];
    const previous = buttons.map(b => b.disabled);
    buttons.forEach(b => b.disabled = true);
    try { await action(); }
    catch (error) { document.getElementById('connectionError').textContent = error.message; }
    finally { buttons.forEach((b, i) => b.disabled = previous[i]); }
}

async function showConnections() {
    document.getElementById('connectionError').textContent = '';
    document.getElementById('connectionStatus').textContent = '';
    updateConnectionFields();
    document.getElementById('connectionsDialog').showModal();
    await connectionAction(loadConnections);
}

document.getElementById('connectionKind').addEventListener('change', updateConnectionFields);
document.getElementById('connectionForm').addEventListener('submit', async event => {
    event.preventDefault();
    await connectionAction(async () => {
        document.getElementById('connectionStatus').textContent = 'Проверка подключения…';
        const body = {};
        for (const field of ['name', 'kind', 'database', 'host', 'port', 'username', 'password']) {
            body[field] = document.getElementById('connection' + field[0].toUpperCase() + field.slice(1)).value;
        }
        try {
            await sendJSON('/api/connections', body);
            document.getElementById('connectionForm').reset();
            updateConnectionFields();
            await loadConnections();
            document.getElementById('connectionStatus').textContent = 'Подключение проверено и сохранено. Нажмите «Выбрать», чтобы работать с ним.';
        } catch (error) {
            document.getElementById('connectionStatus').textContent = '';
            throw error;
        } finally { document.getElementById('connectionPassword').value = ''; }
    });
});
