// ─── Доступность Ollama ─────────────────────────────────
let ollamaMonitor = null;

function stopOllamaMonitor() {
    if (!ollamaMonitor) return;
    ollamaMonitor.controller.abort();
    clearTimeout(ollamaMonitor.timer);
    ollamaMonitor = null;
}

function startOllamaMonitor() {
    const monitor = {controller: new AbortController(), timer: null};
    ollamaMonitor = monitor;
    const indicator = document.getElementById('ollamaStatus');
    indicator.dataset.state = 'checking';
    indicator.textContent = 'Проверяем Ollama…';

    async function poll() {
        let state = 'unknown';
        let message = 'Не удалось проверить статус Ollama';
        try {
            const signal = AbortSignal.any([monitor.controller.signal, AbortSignal.timeout(5000)]);
            const response = await apiFetch('/api/model', {signal});
            if (response.ok) {
                const status = await response.json();
                if (status.status === 'ready' && status.model) {
                    state = 'ready';
                    message = `Ollama доступна · ${status.model}`;
                } else if (status.status === 'no_models') {
                    state = 'no_models';
                    message = 'Ollama запущена. Установите модель для генерации SQL.';
                } else if (status.status === 'unavailable') {
                    state = 'unavailable';
                    message = 'Ollama недоступна. Проверьте, запущена ли она.';
                }
            }
        } catch {
            // При недоступности Flask состояние самой Ollama неизвестно.
        }
        if (ollamaMonitor !== monitor) return;
        if (indicator.dataset.state !== state || indicator.textContent !== message) {
            indicator.dataset.state = state;
            indicator.textContent = message;
        }
        monitor.timer = setTimeout(poll, 15000);
    }

    poll();
}

// ─── Схема БД ─────────────────────────────────────────
let schemaRequest = 0;

async function loadSchema(refresh = false) {
    const requestId = ++schemaRequest;
    const user = currentUser;
    try {
        const res = await apiFetch("/api/schema", {method: refresh ? 'POST' : 'GET'});
        const data = await res.json();
        if (requestId !== schemaRequest || user !== currentUser) return;
        if (data.error) {
            updateSchemaDiagram(null, '', data.error);
            document.getElementById("schemaTree").innerHTML =
                `<div class="schema-error">${escapeHtml(data.error)}</div>`;
            return;
        }
        renderSchema(data.schema);
        updateSchemaDiagram(data.schema, data.connection?.name ?? 'ГрГУ имени Янки Купалы');
        document.getElementById('schemaLabel').textContent = data.connection ? `${data.connection.name} · таблицы` : 'ГрГУ · таблицы';
        document.getElementById('databaseName').textContent = data.connection ? data.connection.name : 'ГрГУ имени Янки Купалы';
        document.getElementById('databaseNote').textContent = data.connection ? `${data.connection.kind} · Только чтение` : 'Учебная база · Студенты, преподаватели и оценки вымышлены';
        document.getElementById('questionInput').placeholder = data.connection ? 'Задайте вопрос по таблицам подключённой базы…' : 'Например: покажи студентов специальности Кибербезопасность...';
        const emptyText = document.querySelector('#emptyState p');
        if (emptyText) emptyText.textContent = data.connection ? `Задайте вопрос к базе «${data.connection.name}»` : 'Задайте вопрос к учебной базе ГрГУ имени Янки Купалы';
    } catch {
        if (requestId !== schemaRequest || user !== currentUser) return;
        updateSchemaDiagram(null, '', 'Сервер недоступен');
        document.getElementById("schemaTree").innerHTML =
            '<div class="schema-error">Сервер недоступен</div>';
    }
}

function renderSchema(schema) {
    const container = document.getElementById("schemaTree");
    const entries = Object.entries(schema);
    if (entries.length === 0) {
        container.innerHTML = '<div class="schema-error">Таблицы не найдены</div>';
        return;
    }
    container.innerHTML = "";
    for (const [tableName, info] of entries) {
        const item = document.createElement("div");
        item.className = "table-item";
        const colsHtml = info.columns.map(col => `
            <div class="col-row">
                ${col.pk ? '<span class="col-pk">PK</span>' : '<span class="col-spacer"></span>'}
                <span class="col-name">${escapeHtml(col.name)}</span>
                <span class="col-type">${escapeHtml(col.type)}</span>
            </div>
        `).join("");

        item.innerHTML = `
            <div class="table-header" onclick="toggleTable(this)">
                <span class="table-dot"></span>
                <span class="table-name">${escapeHtml(tableName)}</span>
                <span class="table-count">${info.row_count}</span>
                <span class="table-chevron">&#9654;</span>
            </div>
            <div class="columns-list">${colsHtml}</div>
        `;
        container.appendChild(item);
    }
}

function toggleTable(header) {
    header.closest(".table-item").classList.toggle("open");
}

// ─── Вкладки ──────────────────────────────────────────
function switchTab(tabName) {
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    event.target.classList.add("active");
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    document.getElementById(tabName + "Panel").classList.add("active");
    if (tabName === "history") loadHistory();
    if (tabName === "favorites") loadFavorites();
}

// ─── История ──────────────────────────────────────────
async function loadHistory() {
    try {
        const res = await apiFetch("/api/history");
        const data = await res.json();
        const container = document.getElementById("historyList");

        if (!data.history || data.history.length === 0) {
            container.innerHTML = '<div class="side-hint">Запросов пока нет</div>';
            return;
        }

        renderSavedQueries(container, data.history, true);
    } catch (e) {
        console.error("Error loading history:", e);
    }
}

async function deleteHistoryItem(id) {
    if (!confirm("Удалить этот запрос из истории?")) return;
    try {
        const response = await apiFetch("/api/history/remove", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id })
        });
        if (response.ok) {
            loadHistory();
        } else {
            alert("Ошибка при удалении");
        }
    } catch (e) {
        console.error(e);
        alert("Ошибка соединения");
    }
}

async function clearHistory() {
    if (!confirm("Очистить всю историю запросов?")) return;
    try {
        await apiFetch("/api/history/clear", { method: "POST" });
        loadHistory();
    } catch (e) {
        alert("Ошибка при очистке истории");
    }
}

// ─── Избранное ────────────────────────────────────────
async function loadFavorites() {
    try {
        const res = await apiFetch("/api/favorites");
        const data = await res.json();
        const container = document.getElementById("favoritesList");

        if (!data.favorites || data.favorites.length === 0) {
            container.innerHTML = '<div class="side-hint">Нет избранных запросов</div>';
            return;
        }

        renderSavedQueries(container, data.favorites, false);
    } catch (e) {
        console.error("Error loading favorites:", e);
    }
}

function renderSavedQueries(container, items, fromHistory) {
    container.replaceChildren(...items.map(item => {
        const question = item.question || '';
        const sql = item.sql || '';
        const count = Number(item.count) || 0;
        const row = document.createElement('div');
        row.className = 'side-item';
        row.innerHTML = `
            <button class="side-item-q" type="button"></button>
            <div class="side-item-meta">
                ${fromHistory ? `<span class="status-dot ${item.blocked ? 'dot-warn' : item.success ? 'dot-ok' : 'dot-err'}"
                    ${item.blocked ? 'title="Заблокирован защитой"' : ''}></span>` : ''}
                <span>${escapeHtml(item.time)}</span>
                ${fromHistory ? `<span>•</span><span>${count} ${declension(count)}</span>` : ''}
            </div>
            <div class="side-item-actions">
                <button class="side-btn" type="button" data-saved-sql title="Открыть сохранённый SQL" aria-label="Открыть сохранённый SQL">SQL</button>
                ${fromHistory ? '<button class="side-btn" type="button" data-save title="В избранное" aria-label="В избранное">★</button>' : ''}
                <button class="side-btn del" type="button" data-remove title="Удалить" aria-label="Удалить">×</button>
            </div>`;
        const questionButton = row.querySelector('.side-item-q');
        questionButton.textContent = question.trim() ? question : 'Без исходного вопроса';
        questionButton.disabled = !question.trim();
        questionButton.title = question.trim() ? `Повторить вопрос: ${question}` : '';
        questionButton.addEventListener('click', () => fillQuestion(question));

        const sqlButton = row.querySelector('[data-saved-sql]');
        sqlButton.hidden = !sql.trim();
        sqlButton.addEventListener('click', () => showSQLModal(question, sql));
        row.querySelector('[data-save]')?.addEventListener('click', () => addToFavorites(question, sql));
        row.querySelector('[data-remove]').addEventListener('click', () => {
            if (fromHistory) deleteHistoryItem(item.id);
            else removeFavorite(item.id);
        });
        return row;
    }));
}

async function addToFavorites(question, sql) {
    try {
        await apiFetch("/api/favorites/add", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question, sql })
        });
        alert("✅ Добавлено в избранное");
    } catch (e) {
        alert("Ошибка при добавлении в избранное");
    }
}

async function removeFavorite(id) {
    try {
        await apiFetch("/api/favorites/remove", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ id })
        });
        loadFavorites();
    } catch (e) {
        alert("Ошибка при удалении из избранного");
    }
}

const savedSqlDialog = document.getElementById('sqlModal');

function showSQLModal(question, sql) {
    document.getElementById('modalSqlQuestion').textContent = question;
    document.getElementById('modalSqlCode').value = sql;
    savedSqlDialog.showModal();
}

function openSavedSQL() {
    const question = document.getElementById('modalSqlQuestion').textContent;
    const sql = document.getElementById('modalSqlCode').value;
    savedSqlDialog.close();
    renderResult({question, sql}, {saved: true});
}

savedSqlDialog.addEventListener('close', () => {
    if (savedSqlDialog.open) return;
    document.getElementById('modalSqlQuestion').textContent = '';
    document.getElementById('modalSqlCode').value = '';
});

// ─── Примеры ──────────────────────────────────────────
async function loadExamples() {
    try {
        const res = await apiFetch("/api/examples");
        const data = await res.json();
        const dropdown = document.getElementById("examplesDropdown");
        document.getElementById('examplesBtn').classList.toggle('hidden', !data.examples?.length);
        dropdown.innerHTML = data.examples.map(ex =>
            `<button class="example-btn" onclick="fillQuestion('${ex.replace(/'/g, "\\'")}')">${ex}</button>`
        ).join("");
    } catch {}
}

document.getElementById("examplesBtn").addEventListener("click", function (e) {
    e.stopPropagation();
    document.getElementById("examplesDropdown").classList.toggle("visible");
});

document.addEventListener("click", function () {
    document.getElementById("examplesDropdown").classList.remove("visible");
});

function fillQuestion(text) {
    document.getElementById("questionInput").value = text;
    document.getElementById("examplesDropdown").classList.remove("visible");
    document.getElementById("questionInput").focus();
}

// ─── Отправка запроса ─────────────────────────────────
async function handleSend() {
    const question = document.getElementById("questionInput").value.trim();
    if (!question) return;

    const btn = document.getElementById("sendBtn");
    btn.disabled = true;
    btn.textContent = "Генерирую...";
    document.getElementById("emptyState")?.remove();

    try {
        const res = await apiFetch("/api/generate", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question })
        });
        const data = await res.json();
        renderResult(data);
    } catch {
        showError("Не удалось подключиться к серверу. Убедитесь что запущен app.py");
    } finally {
        btn.disabled = false;
        btn.textContent = "Сгенерировать SQL";
    }
}

document.getElementById("questionInput").addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
    }
});

// ─── Отображение результата ───────────────────────────
const pendingConfirmations = new Map();

function renderExecution(result, sqlId, tblId, executedSQL) {
    const block = document.getElementById(tblId);
    pendingConfirmations.delete(tblId);
    if (result.security_blocked || result.readonly_blocked) {
        block.innerHTML = `<div class="security-block"><span>${escapeHtml(result.message)}</span></div>`;
    } else if (result.error) {
        block.innerHTML = `<div class="error-block">${escapeHtml(result.error)}</div>`;
    } else if (result.sandbox) {
        const effect = ['INSERT', 'UPDATE', 'DELETE'].includes(result.statement_type)
            ? `Затронуто строк: ${result.affected_rows}.` : 'Изменение структуры базы данных.';
        block.innerHTML = `<div class="sandbox-result"><strong>Пробный запуск: изменения отменены</strong><p>Оператор: ${escapeHtml(result.statement_type)}. ${effect}</p><p>Проверьте SQL. Чтобы сохранить изменения, нажмите кнопку ниже. Подтверждение действует 2 минуты.</p></div>`;
        pendingConfirmations.set(tblId, {sql: executedSQL, token: result.confirmation_token});
        const button = document.createElement('button');
        button.className = 'btn-sm primary';
        button.textContent = 'Сохранить изменения';
        button.addEventListener('click', () => confirmSQL(sqlId, tblId, button));
        block.querySelector('.sandbox-result').append(button);
    } else if (result.committed) {
        const effect = ['INSERT', 'UPDATE', 'DELETE'].includes(result.statement_type)
            ? `Затронуто строк: ${result.affected_rows}.` : 'Структура базы обновлена.';
        block.innerHTML = `<div class="committed-result">Изменения сохранены. ${effect}</div>`;
        loadSchema();
    } else if (result.columns?.length) {
        block.innerHTML = buildTable(result, tblId, block.closest('.result-card').dataset.question, executedSQL);
    } else {
        block.innerHTML = '<div class="no-rows">Запрос выполнен — строк не найдено.</div>';
    }
}

function renderResult(data, {saved = false, manual = false} = {}) {
    const zone = document.getElementById('resultsZone');
    const card = document.createElement('div');
    card.className = 'result-card';
    card.dataset.question = data.question || '';
    const suffix = crypto.randomUUID();
    const sqlId = 'sql_' + suffix;
    const tblId = 'tbl_' + suffix;
    const title = data.question || (manual ? 'Ручной запрос' : saved ? 'Запрос без исходного вопроса' : 'Результат запроса');
    let html = `<div class="card-question"><span class="q-dot"></span><span>${escapeHtml(title)}</span></div>`;
    if (data.sql || manual) {
        const label = manual ? 'SQL для выполнения' : saved ? 'Сохранённый SQL' : 'Сгенерированный SQL';
        html += `<div class="sql-block"><div class="block-label" id="label_${sqlId}">${label}</div><textarea class="sql-textarea" id="${sqlId}" aria-labelledby="label_${sqlId}" spellcheck="false">${escapeHtml(data.sql)}</textarea><div class="sql-actions"><button class="btn-sm primary" onclick="runSQL('${sqlId}', '${tblId}')">Проверить / выполнить</button><button class="btn-sm" onclick="copySQL('${sqlId}', this)">Копировать</button><button class="btn-sm star" data-favorite>★ В избранное</button></div></div>`;
    }
    html += `<div class="table-block" id="${tblId}"></div>`;
    card.innerHTML = html;
    zone.insertBefore(card, zone.firstChild);
    card.querySelector('[data-favorite]')?.addEventListener('click', () => {
        const sql = document.getElementById(sqlId).value;
        if (sql.trim()) addToFavorites(data.question, sql);
    });
    if (saved || manual) {
        document.getElementById('emptyState')?.remove();
        document.getElementById(sqlId).focus();
    } else {
        renderExecution(data.error ? {error: data.error} : data.result || {}, sqlId, tblId, data.sql);
    }
}

async function runSQL(sqlId, tblId) {
    const sql = document.getElementById(sqlId).value.trim();
    if (!sql) return;
    try {
        const response = await apiFetch('/api/execute', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({sql})});
        renderExecution(await response.json(), sqlId, tblId, sql);
    } catch { showError('Не удалось подключиться к серверу'); }
}

async function confirmSQL(sqlId, tblId, button) {
    const preview = pendingConfirmations.get(tblId);
    if (!preview || preview.sql.trim() !== document.getElementById(sqlId).value.trim()) {
        showError('SQL изменён. Сначала выполните пробный запуск заново.');
        return;
    }
    button.disabled = true;
    try {
        const response = await apiFetch('/api/execute', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({sql: preview.sql, confirmation_token: preview.token})});
        renderExecution(await response.json(), sqlId, tblId, preview.sql);
    } catch { showError('Не удалось получить результат. Проверьте журнал перед повторным выполнением.'); }
    finally { button.disabled = false; }
}

function copySQL(sqlId, btn) {
    const sql = document.getElementById(sqlId).value;
    navigator.clipboard.writeText(sql).then(() => {
        const orig = btn.textContent;
        btn.textContent = "Скопировано";
        setTimeout(() => btn.textContent = orig, 1500);
    });
}

// ─── Построение таблицы ───────────────────────────────
function buildTable(result, tableId, question, sql) {
    const { columns, rows, count } = result;
    const exportId = "exp_" + tableId;
    window["td" + exportId] = { columns, rows, question, sql, truncated: result.truncated };
    const rowLabel = declension(count);

    let html = `<div class="table-toolbar">
        <div class="table-meta">${count} ${rowLabel}</div>
        <div class="export-group" ${currentUser?.permissions & 4 ? "" : "hidden"}>
            <button class="btn-export" onclick="exportCSV('${exportId}')">Экспорт CSV</button>
            <button class="btn-export" onclick="exportExcel('${exportId}')">Экспорт Excel</button>
            <button class="btn-export" onclick="exportResult('${exportId}', 'pdf', this)">Экспорт PDF</button>
        </div>
    </div>
    <div class="table-wrap">
        <table>
            <thead>
                <tr>${columns.map(c => `<th>${escapeHtml(c)}</th>`).join("")}</tr>
            </thead>
            <tbody>`;

    for (const row of rows) {
        html += "<tr>" + row.map(v =>
            v === null
                ? '<td><span class="null-val">null</span></td>'
                : `<td>${escapeHtml(String(v))}</td>`
        ).join("") + "</tr>";
    }
    html += "</tbody></table></div>";
    return html;
}

// ─── Экспорт ──────────────────────────────────────────
async function exportResult(exportId, format, button) {
    if (button) button.disabled = true;
    try {
        const {columns, rows, question, sql, truncated} = window["td" + exportId];
        if (format === 'pdf') {
            const response = await apiFetch('/api/export', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({format, columns, rows, question, sql, truncated}),
            });
            if (!response.ok) {
                const error = await response.json().catch(() => ({}));
                throw new Error(error.error || 'Не удалось сформировать PDF. Проверьте размер таблицы.');
            }
            downloadBlob(await response.blob(), 'результат.pdf');
            return;
        }
        const result = await sendJSON('/api/export', {columns, rows, format});
        download(result.data, result.filename, result.mime);
    } catch (error) { showError(error.message); }
    finally { if (button) button.disabled = false; }
}

function exportCSV(exportId) { return exportResult(exportId, 'csv'); }
function exportExcel(exportId) { return exportResult(exportId, 'excel'); }

function download(content, filename, mimeType) {
    downloadBlob(new Blob(["\uFEFF" + content], {type: mimeType}), filename);
}

function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ─── Вспомогательные функции ──────────────────────────
function showError(message) {
    const zone = document.getElementById("resultsZone");
    const el = document.createElement("div");
    el.className = "error-block";
    el.style.marginBottom = "14px";
    el.textContent = message;
    zone.insertBefore(el, zone.firstChild);
}

function escapeHtml(str) {
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
}

function declension(n) {
    if (n % 100 >= 11 && n % 100 <= 14) return "строк";
    const r = n % 10;
    if (r === 1) return "строка";
    if (r >= 2 && r <= 4) return "строки";
    return "строк";
}

// ─── Инициализация ────────────────────────────────────
