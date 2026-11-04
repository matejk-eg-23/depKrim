// ─── Схема БД ─────────────────────────────────────────
async function loadSchema() {
    try {
        const res = await apiFetch("/api/schema");
        const data = await res.json();
        if (data.error) {
            document.getElementById("schemaTree").innerHTML =
                `<div class="schema-error">${escapeHtml(data.error)}</div>`;
            return;
        }
        renderSchema(data.schema);
    } catch {
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

        container.innerHTML = data.history.map(item => `
            <div class="side-item">
                <div class="side-item-q">${escapeHtml(item.question)}</div>
                <div class="side-item-meta">
                    <span class="status-dot ${item.blocked ? 'dot-warn' : item.success ? 'dot-ok' : 'dot-err'}"></span>
                    <span>${item.time}</span>
                    <span>•</span>
                    <span>${item.count || 0} строк</span>
                    ${item.blocked ? '<span title="Заблокирован защитой"></span>' : ''}
                </div>
                <div class="side-item-actions">
                    <button class="side-btn" onclick="reuseSQL(${JSON.stringify(item.sql).replace(/"/g, '&quot;')})" title="Использовать SQL">SQL</button>
                    <button class="side-btn" onclick="addToFavoritesFromHistory('${item.id}')" title="В избранное">★</button>
                    <button class="side-btn del" onclick="deleteHistoryItem('${item.id}')" title="Удалить">×</button>
                </div>
            </div>
        `).join("");
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

        container.innerHTML = data.favorites.map(item => `
            <div class="side-item">
                <div class="side-item-q">${escapeHtml(item.question)}</div>
                <div class="side-item-meta">
                    <span>${item.time}</span>
                </div>
                <div class="side-item-actions">
                    <button class="side-btn" onclick="reuseSQL(${JSON.stringify(item.sql).replace(/"/g, '&quot;')})" title="Использовать SQL">SQL</button>
                    <button class="side-btn del" onclick="removeFavorite('${item.id}')" title="Удалить">×</button>
                </div>
            </div>
        `).join("");
    } catch (e) {
        console.error("Error loading favorites:", e);
    }
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

async function addToFavoritesFromHistory(id) {
    try {
        const res = await apiFetch("/api/history");
        const data = await res.json();
        const item = data.history.find(h => h.id === id);
        if (item) {
            addToFavorites(item.question, item.sql);
        }
    } catch (e) {
        console.error(e);
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

let currentSQL = "";

function showSQLModal(sql) {
    currentSQL = sql;
    document.getElementById("modalSqlCode").value = sql;
    document.getElementById("sqlModal").style.display = "flex";
}

function closeModal() {
    document.getElementById("sqlModal").style.display = "none";
}

function copyModalSQL() {
    if (!currentSQL) return;
    navigator.clipboard.writeText(currentSQL).then(() => {
        const btn = document.querySelector('.modal-actions button:first-child');
        if (btn) {
            const originalText = btn.textContent;
            btn.textContent = "✅ Скопировано!";
            btn.style.background = "#1a6b3c";
            btn.style.color = "white";
            setTimeout(() => {
                btn.textContent = originalText;
                btn.style.background = "";
                btn.style.color = "";
            }, 2000);
        }
    }).catch(err => {
        console.error("Ошибка копирования:", err);
        alert("Не удалось скопировать");
    });
}

function useThisSQL() {
    document.getElementById("questionInput").value = currentSQL;
    closeModal();
    document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
    document.querySelector('.tab[onclick*="structure"]').classList.add("active");
    document.querySelectorAll(".tab-panel").forEach(p => p.classList.remove("active"));
    document.getElementById("structurePanel").classList.add("active");
}

function reuseSQL(sql) {
    showSQLModal(sql);
}

// ─── Примеры ──────────────────────────────────────────
async function loadExamples() {
    try {
        const res = await apiFetch("/api/examples");
        const data = await res.json();
        const dropdown = document.getElementById("examplesDropdown");
        dropdown.innerHTML = data.examples.map(ex =>
            `<button class="example-btn" onclick="useExample('${ex.replace(/'/g, "\\'")}')">${ex}</button>`
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

function useExample(text) {
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
        block.innerHTML = buildTable(result);
    } else {
        block.innerHTML = '<div class="no-rows">Запрос выполнен — строк не найдено.</div>';
    }
}

function renderResult(data) {
    const zone = document.getElementById('resultsZone');
    const card = document.createElement('div');
    card.className = 'result-card';
    const suffix = crypto.randomUUID();
    const sqlId = 'sql_' + suffix;
    const tblId = 'tbl_' + suffix;
    let html = `<div class="card-question"><span class="q-dot"></span><span>${escapeHtml(data.question || 'Результат запроса')}</span></div>`;
    if (data.sql) {
        html += `<div class="sql-block"><div class="block-label">Сгенерированный SQL</div><textarea class="sql-textarea" id="${sqlId}" spellcheck="false">${escapeHtml(data.sql)}</textarea><div class="sql-actions"><button class="btn-sm primary" onclick="runSQL('${sqlId}', '${tblId}')">Проверить / выполнить</button><button class="btn-sm" onclick="copySQL('${sqlId}', this)">Копировать</button><button class="btn-sm star" data-favorite>★ В избранное</button></div></div>`;
    }
    html += `<div class="table-block" id="${tblId}"></div>`;
    card.innerHTML = html;
    zone.insertBefore(card, zone.firstChild);
    card.querySelector('[data-favorite]')?.addEventListener('click', () => addToFavorites(data.question, document.getElementById(sqlId).value));
    renderExecution(data.error ? {error: data.error} : data.result || {}, sqlId, tblId, data.sql);
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
function buildTable(result) {
    const { columns, rows, count } = result;
    const exportId = "exp_" + Date.now();
    window["td" + exportId] = { columns, rows };
    const rowLabel = declension(count);

    let html = `<div class="table-toolbar">
        <div class="table-meta">${count} ${rowLabel}</div>
        <div class="export-group" ${currentUser?.permissions & 4 ? "" : "hidden"}>
            <button class="btn-export" onclick="exportCSV('${exportId}')">Экспорт CSV</button>
            <button class="btn-export" onclick="exportExcel('${exportId}')">Экспорт Excel</button>
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
async function exportResult(exportId, format) {
    try {
        const table = window["td" + exportId];
        const result = await sendJSON('/api/export', {...table, format});
        download(result.data, result.filename, result.mime);
    } catch (error) { showError(error.message); }
}

function exportCSV(exportId) { return exportResult(exportId, 'csv'); }
function exportExcel(exportId) { return exportResult(exportId, 'excel'); }

function download(content, filename, mimeType) {
    const blob = new Blob(["\uFEFF" + content], { type: mimeType });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
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
