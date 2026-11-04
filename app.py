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

from flask import Flask, request, jsonify, render_template, g, has_request_context
from auth import register_auth, initialize_auth, has_permission, PERM_VIEW_CONFIDENTIAL, PERM_DANGEROUS_SQL
import sqlite3
import requests
from sql_security import inspect_sql, run_statement
from security_audit import register_security, issue_preview, consume_preview

def start_ollama():
    try:
        requests.get("http://localhost:11434", timeout=2)
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
data_lock = threading.RLock()

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(BASE_DIR, "university.db")
HIST_PATH  = os.path.join(BASE_DIR, "history.json")
FAV_PATH   = os.path.join(BASE_DIR, "favorites.json")
PROMPTS_PATH = os.path.join(BASE_DIR, "prompts.json")

OLLAMA_URL = "http://localhost:11434/api/generate"

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
def get_model():
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        for prefer in ["llama3", "mistral", "phi3", "gemma", "tinyllama"]:
            for m in models:
                if prefer in m.lower():
                    return m
        return models[0] if models else "tinyllama"
    except:
        return "tinyllama"

# ── Схема БД ────────────────────────────────────────────────────────────────
def get_schema():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name != 'sqlite_sequence' ORDER BY name;"
    )
    tables = [r[0] for r in cursor.fetchall()]

    schema_text = ""
    schema_dict = {}

    for table in tables:
        if table.casefold().startswith('conf_') and not can_view_confidential():
            continue
        quoted_table = '"' + table.replace('"', '""') + '"'
        cursor.execute(f"PRAGMA table_info({quoted_table});")
        columns = cursor.fetchall()
        cursor.execute(f"SELECT COUNT(*) FROM {quoted_table};")
        row_count = cursor.fetchone()[0]

        schema_text += f"Table: {table}\nColumns:\n"
        for col in columns:
            schema_text += f"  {col[1]} {col[2]}"
            if col[5]:
                schema_text += " PRIMARY KEY"
            schema_text += "\n"
        cursor.execute(f"PRAGMA foreign_key_list({quoted_table});")
        for fk in cursor.fetchall():
            schema_text += f"  FOREIGN KEY ({fk[3]}) REFERENCES {fk[2]}({fk[4]})\n"
        # Только небольшие публичные справочники, без персональных данных.
        reference_columns = {
            "faculties": "id, name, short_name",
            "departments": "id, faculty_id, name",
            "specializations": "id, department_id, name",
            "courses": "id, department_id, name",
        }
        if table in reference_columns and set(reference_columns[table].split(', ')) <= {col[1] for col in columns}:
            values = cursor.execute(
                f"SELECT {reference_columns[table]} FROM {table} ORDER BY id LIMIT 20"
            ).fetchall()
            schema_text += f"Reference values ({reference_columns[table]}): "
            schema_text += json.dumps(values, ensure_ascii=False) + "\n"
        schema_text += "\n"

        schema_dict[table] = {
            "columns": [{"name": col[1], "type": col[2], "pk": bool(col[5])} for col in columns],
            "row_count": row_count,
        }

    conn.close()
    return schema_text, schema_dict

# ── Генерация SQL ────────────────────────────────────────────────────────────
def normalize_question(question):
    return " ".join(re.findall(r"\w+", question.casefold().replace("ё", "е")))


def can_view_confidential():
    return has_request_context() and has_permission(PERM_VIEW_CONFIDENTIAL)


def check_generated_sql(sql):
    result = run_statement(DB_PATH, sql, allow_write=can_modify_sql(),
                           confidential=can_view_confidential(), explain=True,
                           timeout=app.config['SQL_TIMEOUT_SECONDS'])
    return not (result.get('error') or result.get('security_blocked')), result.get('error', result.get('message', ''))


def generate_sql(question):
    schema_text, _ = get_schema()
    examples = load_json(PROMPTS_PATH)
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
    command_rule = ('Разрешены SELECT и команды INSERT/UPDATE/DELETE/CREATE TABLE/CREATE INDEX/ALTER TABLE/DROP TABLE/DROP INDEX. Изменения сначала проходят пробный запуск.' if can_modify_sql() else 'Разрешено только чтение: SELECT или WITH ... SELECT.')

    prompt = f"""Ты эксперт SQLite. Напиши SQL-запрос по схеме базы и вопросу пользователя.

СХЕМА БАЗЫ ДАННЫХ:
{schema_text}

ПРИМЕРЫ ДЛЯ ЭТОЙ СХЕМЫ:
{examples_text}

ПРАВИЛА (ОБЯЗАТЕЛЬНО):
- Отвечай ТОЛЬКО чистым SQL кодом, без объяснений, без markdown, без ```.
- {command_rule}
- Используй правильные имена таблиц и колонок из схемы.
- Соединяй таблицы только по указанным FOREIGN KEY; не пропускай промежуточные таблицы.
- Студенты связаны с факультетами через students → specializations → departments → faculties.
- Для фильтров используй значения из Reference values, не сокращай названия самостоятельно.
- Для поиска по тексту используй LIKE '%текст%'.
- Для дат используй формат YYYY-MM-DD.
- Не придумывай несуществующие алиасы (OLD., NEW. и т.д.).
- Ограничивай результат LIMIT 100, если не указано иное.

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
                prompt += f"\n\nПредыдущий SQL:\n{sql}\nОшибка SQLite: {reason}\nИсправь запрос по схеме выше. Верни только SQL:"
        return {"sql": "", "error": "Не удалось получить корректный SQL после повторной генерации. Уточните вопрос.", "model": model}

    except requests.exceptions.ConnectionError:
        return {"sql": "", "error": "Ollama не запущена. Выполни в терминале: ollama serve", "model": model}
    except requests.exceptions.Timeout:
        return {"sql": "", "error": "Ollama не ответила за 90 секунд, попробуй ещё раз", "model": model}
    except Exception as e:
        return {"sql": "", "error": str(e), "model": model}

# ── Выполнение SQL ───────────────────────────────────────────────────────────
def execute_sql(sql, confirmation_token=None):
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
    return render_template("index.html")

@app.route("/api/schema")
def schema():
    try:
        if not os.path.exists(DB_PATH):
            return jsonify({"error": "База данных не найдена. Запусти: python database.py"}), 500
        _, d = get_schema()
        return jsonify({"schema": d})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/model")
def model_info():
    return jsonify({"model": get_model()})

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
    return jsonify({"examples": [
        "Покажи всех студентов факультета информационных технологий",
        "Какой средний балл по курсу Базы данных?",
        "Сколько студентов проживает в каждом общежитии?",
        "Покажи преподавателей, которые работают на нескольких кафедрах",
        "Топ-5 студентов по среднему баллу за экзамены",
        "Какие курсы требуют прохождения других курсов?",
        "Покажи расписание занятий по кафедре кибербезопасности на понедельник",
        "Какие студенты имеют академический отпуск?",
        "Сколько часов занятий у каждого преподавателя?",
        "Покажи изменения оценок с указанием кто и когда изменил",
        "Show students from IT faculty",
        "What is the average grade for Machine Learning course?",
    ]})

# ── История (API) ────────────────────────────────────────────────────────────
@app.route("/api/history")
def history():
    return jsonify({"history": [item for item in load_json(HIST_PATH) if item.get('user_id') == g.current_user['id']]})

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
        save_json(HIST_PATH, [h for h in load_json(HIST_PATH) if h.get('user_id') != g.current_user['id']])
    return jsonify({"ok": True})

# ── Избранное (API) ──────────────────────────────────────────────────────────
@app.route("/api/favorites")
def favorites():
    return jsonify({"favorites": [item for item in load_json(FAV_PATH) if item.get('user_id') == g.current_user['id']]})

@app.route("/api/favorites/add", methods=["POST"])
def favorites_add():
    data = request.get_json()
    item = {
        "id":       uuid.uuid4().hex,
        "user_id": g.current_user['id'],
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
    return jsonify(error='Неизвестный формат экспорта'), 400


if __name__ == "__main__":
    initialize_auth(app)
    start_ollama()
    if not os.path.exists(DB_PATH):
        print(f"ВНИМАНИЕ: База данных не найдена — {DB_PATH}")
        print("Сначала запусти: python database.py")
    else:
        print(f"База данных: {DB_PATH}")

    print(f"Модель Ollama: {get_model()}")
    print("Сервер: http://localhost:5000  |  Остановка: Ctrl+C")

    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(debug=False, port=5000)
