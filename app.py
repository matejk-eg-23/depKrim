# app.py — запуск: python app.py

import subprocess
import webbrowser
import threading
import os
import json
import re
import csv
import io
import uuid
from html import escape
from datetime import datetime
from contextlib import closing
from pathlib import Path

from flask import Flask, request, jsonify, render_template, send_file, g, has_request_context
from auth import register_auth, initialize_auth, has_permission, PERM_VIEW_CONFIDENTIAL, PERM_DANGEROUS_SQL
import sqlite3
import requests
from sql_security import inspect_sql, run_statement
from security_audit import register_security, issue_preview, consume_preview
from database import CATALOG
from pdf_report import build_pdf_report
from schema_cache import SchemaCache
from external_connections import (register_connections, active_connection, connection_key,
                                  external_schema, run_external, safe_connection, ConnectionUnavailable)

OLLAMA_BASE_URL = "http://localhost:11434"


def start_ollama():
    try:
        requests.get(OLLAMA_BASE_URL, timeout=2)
        print("Ollama уже запущена")
    except:
        print("Запускаю Ollama...")
        subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import time
        time.sleep(3)
        print("Ollama запущена")

app = Flask(__name__,
            template_folder='templates',
            static_folder='static')
register_auth(app)
register_security(app)
schema_cache = app.extensions['schema_cache'] = SchemaCache()
register_connections(app)
data_lock = threading.RLock()

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(BASE_DIR, "grsu.db")
HIST_PATH  = os.path.join(BASE_DIR, "history.json")
FAV_PATH   = os.path.join(BASE_DIR, "favorites.json")
PROMPTS_PATH = os.path.join(BASE_DIR, "prompts.json")

OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/generate"

# ══════════════════════════════════════════════════════════════════════════════
# МОДУЛЬ БЕЗОПАСНОСТИ
# ══════════════════════════════════════════════════════════════════════════════

QUESTION_INJECTION_PATTERNS = [
    (r"'\s*(OR|AND)\s+'?[0-9]",          "Инъекция в вопросе: ' OR/AND '1'"),
    (r"'\s*(OR|AND)\s+'\w+'\s*=\s*'\w*", "Инъекция в вопросе: ' OR 'x'='x'"),
    (r"(OR|AND)\s+1\s*=\s*1",            "Тавтология в вопросе: OR 1=1"),
    (r"(OR|AND)\s+TRUE\b",               "Тавтология в вопросе: OR TRUE"),
    (r"UNION\s+(ALL\s+)?SELECT",         "UNION SELECT в вопросе"),
    (r";\s*(DROP|DELETE|UPDATE|INSERT)\b","Деструктивная команда в вопросе"),
    (r"--\s",                             "SQL-комментарий (--) в вопросе"),
    (r"/\*.*?\*/",                        "Блочный комментарий (/**/) в вопросе"),
    (r"\bsqlite_master\b",               "Обращение к sqlite_master в вопросе"),
    (r"\binformation_schema\b",          "Обращение к information_schema в вопросе"),
    (r"\bSLEEP\s*\(",                    "SLEEP() в вопросе — попытка DoS"),
]

def validate_question(question: str) -> tuple[bool, str]:
    """
    Проверяет вопрос пользователя на попытки инъекций ДО передачи в LLM.
    Это первый рубеж защиты — не даём модели даже увидеть вредоносный ввод.
    """
    q_upper = question.upper()
    for pattern, reason in QUESTION_INJECTION_PATTERNS:
        if re.search(pattern, q_upper, re.IGNORECASE | re.DOTALL):
            return False, f" Вопрос отклонён: обнаружена попытка инъекции — {reason}"
    return True, ""


def can_modify_sql():
    return has_request_context() and has_permission(PERM_DANGEROUS_SQL)


def validate_sql(sql):
    _, reason = inspect_sql(sql, can_modify_sql())
    return not reason, reason


# ══════════════════════════════════════════════════════════════════════════════
# ОСНОВНОЕ ПРИЛОЖЕНИЕ
# ══════════════════════════════════════════════════════════════════════════════

# ── Автоопределение модели ───────────────────────────────────────────────────
def get_ollama_status():
    try:
        response = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=2)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return {"status": "unavailable", "model": None}

    entries = data.get("models") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return {"status": "unavailable", "model": None}
    if not entries:
        return {"status": "no_models", "model": None}
    models = [entry.get("name") for entry in entries if isinstance(entry, dict)]
    models = [name for name in models if isinstance(name, str) and name.strip()]
    if not models:
        return {"status": "unavailable", "model": None}
    for prefer in ("llama3", "mistral", "phi3", "gemma", "tinyllama"):
        for model in models:
            if prefer in model.lower():
                return {"status": "ready", "model": model}
    return {"status": "ready", "model": models[0]}


def get_model():
    return get_ollama_status()["model"]

# ── Схема БД ────────────────────────────────────────────────────────────────
REFERENCE_COLUMNS = {
    "faculties": "id, name, short_name",
    "departments": "id, faculty_id, name",
    "specializations": "id, faculty_id, department_id, name, code",
    "courses": "id, department_id, name",
}


def read_local_schema(path, confidential):
    tables, references = {}, {}
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.execute('BEGIN')
        names = [name for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ) if not name.casefold().startswith('sqlite_')
            and (confidential or not name.casefold().startswith('conf_'))]
        visible_names = {name.casefold(): name for name in names}
        primary_keys = {}
        for table in names:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = conn.execute(f"PRAGMA table_info({quoted})").fetchall()
            primary_keys[table] = [col[1] for col in sorted(columns, key=lambda col: col[5]) if col[5]]
            foreign_keys = {}
            for fk in conn.execute(f"PRAGMA foreign_key_list({quoted})"):
                target = visible_names.get(fk[2].casefold())
                if target is None:
                    continue
                relation = foreign_keys.setdefault(fk[0], {
                    'table': target, 'columns': [], 'referred_columns': [],
                })
                relation['columns'].append(fk[3])
                relation['referred_columns'].append(fk[4])
            tables[table] = {
                'columns': [{'name': col[1], 'type': col[2], 'pk': bool(col[5])} for col in columns],
                'row_count': conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0],
                'foreign_keys': list(foreign_keys.values()),
            }
            fields = REFERENCE_COLUMNS.get(table)
            if fields and set(fields.split(', ')) <= {col[1] for col in columns}:
                references[table] = conn.execute(f"SELECT {fields} FROM {quoted} ORDER BY id").fetchall()
    for info in tables.values():
        for fk in info['foreign_keys']:
            if all(column is None for column in fk['referred_columns']):
                fk['referred_columns'] = primary_keys[fk['table']]
    return tables, references


def get_schema(question=''):
    external = active_connection()
    if external:
        try:
            return schema_cache.get((external['id'], external['user_id']), lambda: external_schema(external))
        except Exception:
            raise ConnectionUnavailable('Не удалось прочитать схему внешней базы. Проверьте подключение и ключ шифрования.') from None
    path = Path(DB_PATH).resolve()
    confidential = can_view_confidential()
    tables, references = schema_cache.get((None, path, confidential), lambda: read_local_schema(path, confidential))
    terms = {word[:5] for word in re.findall(r'\w+', question.casefold()) if len(word) >= 4}
    lines = []
    for table, info in tables.items():
        lines.extend((f"Table: {table}", "Columns:"))
        for col in info['columns']:
            lines.append(f"  {col['name']} {col['type']}" + (' PRIMARY KEY' if col['pk'] else ''))
        for fk in info['foreign_keys']:
            target = ', '.join(column or 'PRIMARY KEY' for column in fk['referred_columns'])
            lines.append(f"  FOREIGN KEY ({', '.join(fk['columns'])}) REFERENCES {fk['table']}({target})")
        if table in references:
            values = references[table]
            if terms:
                values = sorted(values, key=lambda row: -sum(term in str(row).casefold() for term in terms))
            values = values[:20 if table == 'faculties' else 10]
            lines.append(f"Reference values ({REFERENCE_COLUMNS[table]}): " + json.dumps(values, ensure_ascii=False))
        lines.append("")
    return '\n'.join(lines), tables

# ── Генерация SQL ────────────────────────────────────────────────────────────
def normalize_question(question):
    return " ".join(re.findall(r"\w+", question.casefold().replace("ё", "е")))


def can_view_confidential():
    return has_request_context() and has_permission(PERM_VIEW_CONFIDENTIAL)


def check_generated_sql(sql):
    external = active_connection()
    if external:
        result = run_external(external, sql, explain=True)
        return not (result.get('error') or result.get('security_blocked')), result.get('error', result.get('message', ''))
    result = run_statement(DB_PATH, sql, allow_write=can_modify_sql(),
                           confidential=can_view_confidential(), explain=True,
                           timeout=app.config['SQL_TIMEOUT_SECONDS'])
    return not (result.get('error') or result.get('security_blocked')), result.get('error', result.get('message', ''))


def generate_sql(question):
    schema_text, _ = get_schema(question)
    external = active_connection()
    examples = [] if external else load_json(PROMPTS_PATH)
    normalized = normalize_question(question)
    # Для известных вопросов используем проверенную пару, без случайного ответа LLM.
    for example in examples:
        if normalize_question(example["question"]) == normalized:
            safe, _ = check_generated_sql(example["sql"])
            if safe:
                return {"sql": example["sql"], "error": None, "model": "проверенный шаблон"}

    words = set(normalized.split())
    ranked = sorted(examples, key=lambda ex: len(words & set(normalize_question(ex["question"]).split())), reverse=True)
    relevant = [ex for ex in ranked if words & set(normalize_question(ex["question"]).split())][:3]
    examples_text = "\n\n".join(f"Вопрос: {ex['question']}\nSQL: {ex['sql']}" for ex in relevant)
    model = get_model()
    if model is None:
        return {"sql": "", "error": "Модель Ollama недоступна. Проверьте, запущена ли Ollama и установлена ли модель.", "model": None}
    command_rule = ('Разрешены SELECT и команды INSERT/UPDATE/DELETE/CREATE TABLE/CREATE INDEX/ALTER TABLE/DROP TABLE/DROP INDEX. Изменения сначала проходят пробный запуск.' if can_modify_sql() else 'Разрешено только чтение: SELECT или WITH ... SELECT.')

    prompt = f"""Ты эксперт SQLite. Напиши SQL-запрос по схеме базы и вопросу пользователя.

УНИВЕРСИТЕТ: {CATALOG['name']} (Гродно, Республика Беларусь).
Названия факультетов и специальностей взяты из официальных источников; записи студентов и оценки искусственные.

СХЕМА БАЗЫ ДАННЫХ:
{schema_text}

ПРИМЕРЫ ДЛЯ ЭТОЙ СХЕМЫ:
{examples_text}

ПРАВИЛА (ОБЯЗАТЕЛЬНО):
- Отвечай ТОЛЬКО чистым SQL кодом, без объяснений, без markdown, без ```.
- {command_rule}
- Используй правильные имена таблиц и колонок из схемы.
- Соединяй таблицы только по указанным FOREIGN KEY; не пропускай промежуточные таблицы.
- Студенты связаны с факультетами через students.specialization_id → specializations.id и specializations.faculty_id → faculties.id.
- Поле specializations.department_id может быть NULL: не придумывай выпускающую кафедру, используй faculty_id для факультета.
- У военного факультета есть учебные циклы (departments.unit_type = 'цикл'); для подсчёта кафедр фильтруй unit_type = 'кафедра'.
- Экзаменационные оценки grades.score от 1 до 10; сдано от 4. У зачётов score = NULL, результат хранится в assessment_result.
- Денежные величины выражены в BYN. Сроки обучения или годы основания с NULL означают отсутствие подтверждённых сведений.
- Для фильтров используй значения из Reference values, не сокращай названия самостоятельно.
- Для поиска по тексту используй LIKE '%текст%'.
- Для дат используй формат YYYY-MM-DD.
- Не придумывай несуществующие алиасы (OLD., NEW. и т.д.).
- Ограничивай результат LIMIT 100, если не указано иное.

Вопрос: {question}

SQL:"""

    if external:
        dialect = {'sqlite': 'SQLite', 'postgresql': 'PostgreSQL', 'mysql': 'MySQL'}[external['kind']]
        prompt = f"""Ты эксперт {dialect}. Напиши один запрос SELECT по вопросу пользователя.
Верни только корректный SQL для {dialect}, без Markdown и объяснений.
Разрешено только чтение. Используй только перечисленные таблицы, поля и связи FOREIGN KEY.
Не предполагай, что база университетская. Не придумывай значения справочников.
Используй стандартные агрегаты COUNT, SUM, AVG, MIN, MAX. Ограничь результат LIMIT 100.
СХЕМА БАЗЫ:
{schema_text}
Вопрос: {question}
SQL:"""

    try:
        for attempt in range(2):
            response = requests.post(
                OLLAMA_URL,
                json={"model": model, "prompt": prompt, "stream": False,
                      "options": {"temperature": 0, "num_ctx": 8192}},
                timeout=90,
            )
            response.raise_for_status()
            sql = response.json().get("response", "").strip()
            sql = re.sub(r"^```(?:sql)?\s*|\s*```$", "", sql, flags=re.IGNORECASE).strip()
            safe, reason = validate_sql(sql)
            if not safe:
                return {"sql": "", "error": f"Модель предложила недопустимый SQL: {reason}", "model": model}
            valid, reason = check_generated_sql(sql)
            if valid:
                return {"sql": sql, "error": None, "model": model}
            if attempt == 0:
                prompt += f"\n\nПредыдущий SQL:\n{sql}\nОшибка SQL: {reason}\nИсправь запрос по схеме выше. Верни только SQL:"
        return {"sql": "", "error": "Не удалось получить корректный SQL после повторной генерации. Уточните вопрос.", "model": model}

    except requests.exceptions.ConnectionError:
        return {"sql": "", "error": "Ollama не запущена. Выполни в терминале: ollama serve", "model": model}
    except requests.exceptions.Timeout:
        return {"sql": "", "error": "Ollama не ответила за 90 секунд, попробуй ещё раз", "model": model}
    except Exception as e:
        return {"sql": "", "error": str(e), "model": model}

# ── Выполнение SQL ───────────────────────────────────────────────────────────
def execute_sql(sql, confirmation_token=None):
    external = active_connection()
    if external:
        if confirmation_token is not None:
            return {'error': 'Подтверждение относится к другой базе. Внешние подключения доступны только для чтения.', 'status': 409}
        try:
            return run_external(external, sql)
        except Exception:
            return {'error': 'Не удалось открыть внешнюю базу. Проверьте подключение и ключ шифрования.', 'status': 503}
    expected = None
    if confirmation_token is not None:
        if not can_modify_sql():
            return {'security_blocked': True, 'message': 'Недостаточно прав для изменения данных', 'status': 403}
        expected = consume_preview(confirmation_token, sql)
        if expected is None:
            return {'error': 'Подтверждение недействительно или истекло. Выполните пробный запуск заново.', 'status': 409}
    result = run_statement(DB_PATH, sql, allow_write=can_modify_sql(),
                           confidential=can_view_confidential(),
                           timeout=app.config['SQL_TIMEOUT_SECONDS'],
                           preview=confirmation_token is None, expected_fingerprint=expected)
    if result.get('committed'):
        schema_cache.invalidate(None)
    fingerprint = result.pop('fingerprint', None)
    if fingerprint and has_request_context():
        result['confirmation_token'] = issue_preview(sql, fingerprint)
    return result


# ── История ──────────────────────────────────────────────────────────────────
def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return []

def save_json(path, data):
    temporary = str(path) + '.tmp'
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temporary, path)

def add_to_history(question, sql, count, success, blocked=False):
    with data_lock:
        history = load_json(HIST_PATH)
        history.insert(0, {
        "id":       uuid.uuid4().hex,
        "user_id": g.current_user['id'],
        "connection_key": connection_key(),
        "question": question,
        "sql":      sql,
        "count":    count,
        "success":  success,
        "blocked":  blocked,
        "time":     datetime.now().strftime("%d.%m.%Y %H:%M"),
        })
        mine = [item for item in history if item.get('user_id') == g.current_user['id']][:100]
        others = [item for item in history if item.get('user_id') != g.current_user['id']]
        save_json(HIST_PATH, mine + others)

# ── Маршруты ─────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", university=CATALOG['short_name'])

@app.route("/api/schema", methods=['GET', 'POST'])
def schema():
    try:
        external = active_connection()
        if not external and not os.path.exists(DB_PATH):
            return jsonify({"error": "База данных не найдена. Запусти: python database.py"}), 500
        if request.method == 'POST':
            schema_cache.invalidate(external['id'] if external else None)
        _, d = get_schema()
        return jsonify({"schema": d, 'connection': safe_connection(external) if external else None})
    except ConnectionUnavailable:
        raise
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/model")
def model_info():
    return jsonify(get_ollama_status())

@app.route("/api/generate", methods=["POST"])
def generate():
    data     = request.get_json()
    question = (data.get("question", "") or "").strip()

    if not question:
        return jsonify({"error": "Вопрос не может быть пустым"}), 400

    # Ограничение длины вопроса пользователя
    if len(question) > 500:
        return jsonify({"error": "Вопрос слишком длинный (максимум 500 символов)"}), 400

    # ── Рубеж 1: проверка самого вопроса на инъекции ─────────────────────────
    q_safe, q_reason = validate_question(question)
    if not q_safe:
        add_to_history(question, "", 0, False, blocked=True)
        return jsonify({
            "question": question,
            "sql":      "",
            "model":    "",
            "result":   {"security_blocked": True, "message": q_reason},
        })

    gen = generate_sql(question)
    if gen["error"]:
        return jsonify({"error": gen["error"]}), 500

    result  = execute_sql(gen["sql"])
    result_status = result.pop("status", 200)
    blocked = result.get("security_blocked") or result.get("readonly_blocked")
    success = "error" not in result and not blocked
    count   = result.get("count", 0)

    add_to_history(question, gen["sql"], count, success, blocked=bool(blocked))

    return jsonify({
        "question": question,
        "sql":      gen["sql"],
        "model":    gen["model"],
        "result":   result,
    }), result_status

@app.route("/api/execute", methods=["POST"])
def execute():
    data = request.get_json()
    sql  = (data.get("sql", "") or "").strip()
    if not sql:
        return jsonify({"error": "SQL не может быть пустым"}), 400

    # Ограничение длины SQL от пользователя
    if len(sql) > 2000:
        return jsonify({"error": "SQL-запрос слишком длинный (максимум 2000 символов)"}), 400

    result = execute_sql(sql, data.get('confirmation_token'))
    status = result.pop('status', 200)
    return jsonify(result), status

@app.route("/api/examples")
def examples():
    return jsonify({'examples': [] if active_connection() else [item['question'] for item in load_json(PROMPTS_PATH) if item.get('show_in_menu')]})

# ── История (API) ────────────────────────────────────────────────────────────
@app.route("/api/history")
def history():
    return jsonify({"history": [item for item in load_json(HIST_PATH) if item.get('user_id') == g.current_user['id'] and item.get('connection_key', 'local') == connection_key()]})

@app.route("/api/history/remove", methods=["POST"])
def history_remove():
    data = request.get_json()
    hist_id = data.get("id")
    if not hist_id:
        return jsonify({"error": "ID не указан"}), 400
    with data_lock:
        history = load_json(HIST_PATH)
        filtered = [h for h in history if not (h['id'] == hist_id and h.get('user_id') == g.current_user['id'])]
        if len(filtered) == len(history):
            return jsonify(error='Запись не найдена'), 404
        save_json(HIST_PATH, filtered)
    return jsonify({"ok": True})

@app.route("/api/history/clear", methods=["POST"])
def history_clear():
    with data_lock:
        save_json(HIST_PATH, [h for h in load_json(HIST_PATH) if h.get('user_id') != g.current_user['id'] or h.get('connection_key', 'local') != connection_key()])
    return jsonify({"ok": True})

# ── Избранное (API) ──────────────────────────────────────────────────────────
@app.route("/api/favorites")
def favorites():
    return jsonify({"favorites": [item for item in load_json(FAV_PATH) if item.get('user_id') == g.current_user['id'] and item.get('connection_key', 'local') == connection_key()]})

@app.route("/api/favorites/add", methods=["POST"])
def favorites_add():
    data = request.get_json()
    item = {
        "id":       uuid.uuid4().hex,
        "user_id": g.current_user['id'],
        "connection_key": connection_key(),
        "question": data.get("question", ""),
        "sql":      data.get("sql", ""),
        "time":     datetime.now().strftime("%d.%m.%Y %H:%M"),
    }
    if not isinstance(item['question'], str) or not isinstance(item['sql'], str) or len(item['question']) > 500 or len(item['sql']) > 2000:
        return jsonify(error='Некорректный вопрос или SQL'), 400
    with data_lock:
        favs = load_json(FAV_PATH)
        favs.insert(0, item)
        save_json(FAV_PATH, favs)
    return jsonify({"ok": True, "item": item})

@app.route("/api/favorites/remove", methods=["POST"])
def favorites_remove():
    data = request.get_json()
    fav_id = data.get("id")
    with data_lock:
        favs = load_json(FAV_PATH)
        filtered = [f for f in favs if not (f['id'] == fav_id and f.get('user_id') == g.current_user['id'])]
        if len(filtered) == len(favs):
            return jsonify(error='Запись не найдена'), 404
        save_json(FAV_PATH, filtered)
    return jsonify({"ok": True})


@app.post('/api/export')
def export_data():
    data = request.get_json() or {}
    if not isinstance(data, dict):
        return jsonify(error='Некорректная таблица экспорта'), 400
    columns, rows = data.get('columns'), data.get('rows')
    if not isinstance(columns, list) or not columns or len(columns) > 200 or not isinstance(rows, list) or len(rows) > 1000:
        return jsonify(error='Некорректная таблица экспорта'), 400
    if any(not isinstance(row, list) or len(row) != len(columns) for row in rows):
        return jsonify(error='Некорректные строки таблицы'), 400
    if data.get('format') == 'csv':
        output = io.StringIO(newline='')
        writer = csv.writer(output)
        writer.writerow(columns)
        writer.writerows(rows)
        return jsonify(data=output.getvalue(), filename='результат.csv', mime='text/csv;charset=utf-8;')
    if data.get('format') == 'excel':
        def xml_row(row):
            cells = ''.join('<Cell><Data ss:Type="String">' + escape(str(value) if value is not None else '') + '</Data></Cell>' for value in row)
            return '<Row>' + cells + '</Row>'
        content = '<?xml version="1.0" encoding="UTF-8"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet ss:Name="Результат"><Table>'
        content += xml_row(columns) + ''.join(xml_row(row) for row in rows) + '</Table></Worksheet></Workbook>'
        return jsonify(data=content, filename='результат.xls', mime='application/vnd.ms-excel;charset=utf-8;')
    if data.get('format') == 'pdf':
        question, sql = data.get('question', ''), data.get('sql', '')
        if not isinstance(question, str) or len(question) > 500 or not isinstance(sql, str) or len(sql) > 2000:
            return jsonify(error='Некорректный вопрос или SQL для отчёта'), 400
        try:
            report = build_pdf_report(columns, rows, question, sql, data.get('truncated') is True)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except RuntimeError as error:
            return jsonify(error=str(error)), 503
        return send_file(report, mimetype='application/pdf', as_attachment=True, download_name='результат.pdf')
    return jsonify(error='Неизвестный формат экспорта'), 400


if __name__ == "__main__":
    initialize_auth(app)
    start_ollama()
    if not os.path.exists(DB_PATH):
        print(f"ВНИМАНИЕ: База данных не найдена — {DB_PATH}")
        print("Сначала запусти: python database.py")
    else:
        print(f"База данных: {DB_PATH}")

    print(f"Модель Ollama: {get_model() or 'недоступна'}")
    print("Сервер: http://localhost:5000  |  Остановка: Ctrl+C")

    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(debug=False, port=5000)
