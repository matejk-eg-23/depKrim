# app.py — запуск: python app.py

import subprocess
import sys
import webbrowser
import threading
import os
import json
import re
from datetime import datetime

required_libs = ["flask", "requests"]
for lib in required_libs:
    try:
        __import__(lib)
    except ImportError:
        print(f"Устанавливаю {lib}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", lib])

from flask import Flask, request, jsonify, render_template
import sqlite3
import requests

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

start_ollama()

app = Flask(__name__,
            template_folder='templates',
            static_folder='static')

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(BASE_DIR, "shop.db")
HIST_PATH  = os.path.join(BASE_DIR, "history.json")
FAV_PATH   = os.path.join(BASE_DIR, "favorites.json")

OLLAMA_URL = "http://localhost:11434/api/generate"

# ══════════════════════════════════════════════════════════════════════════════
# МОДУЛЬ БЕЗОПАСНОСТИ
# ══════════════════════════════════════════════════════════════════════════════

# ── Категории запрещённых паттернов ─────────────────────────────────────────

# 1. Деструктивные DDL/DML-команды
DESTRUCTIVE_PATTERNS = [
    (r"^\s*(DROP)\b",      "DROP — удаление таблицы/базы запрещено"),
    (r"^\s*(TRUNCATE)\b",  "TRUNCATE — очистка таблицы запрещена"),
    (r"^\s*(ALTER)\b",     "ALTER — изменение схемы запрещено"),
    (r"^\s*(CREATE)\b",    "CREATE — создание объектов запрещено"),
    (r"^\s*(INSERT)\b",    "INSERT — вставка данных запрещена"),
    (r"^\s*(UPDATE)\b",    "UPDATE — изменение данных запрещено"),
    (r"^\s*(DELETE)\b",    "DELETE — удаление строк запрещено"),
    (r"^\s*(REPLACE)\b",   "REPLACE — изменение данных запрещено"),
    (r"^\s*(ATTACH)\b",    "ATTACH — подключение сторонних БД запрещено"),
    (r"^\s*(DETACH)\b",    "DETACH — операция запрещена"),
    (r"^\s*(PRAGMA)\b",    "PRAGMA — чтение/изменение настроек SQLite запрещено"),
    (r"^\s*(VACUUM)\b",    "VACUUM — служебная операция запрещена"),
    (r"^\s*(REINDEX)\b",   "REINDEX — служебная операция запрещена"),
    (r"^\s*(SAVEPOINT)\b", "SAVEPOINT — управление транзакциями запрещено"),
    (r"^\s*(ROLLBACK)\b",  "ROLLBACK — управление транзакциями запрещено"),
    (r"^\s*(COMMIT)\b",    "COMMIT — управление транзакциями запрещено"),
    (r"^\s*(BEGIN)\b",     "BEGIN — управление транзакциями запрещено"),
]

# 2. SQL-инъекции: тавтологии и инъекции в строки
INJECTION_PATTERNS = [
    # Тавтологии в WHERE/HAVING без предшествующего AND/OR тоже опасны:
    #   WHERE 1=1  /  WHERE 1='1'  /  WHERE 'a'='a'  /  WHERE TRUE
    (r"\bWHERE\s+(1\s*=\s*1|1\s*=\s*'1'|'[^']*'\s*=\s*'[^']*'|TRUE|FALSE)\b",
     "Тавтология в WHERE (WHERE 1=1 / WHERE TRUE) — классическая инъекция"),
    # OR/AND тавтологии в любом месте:  OR '1'='1' / OR 1=1 / OR TRUE
    (r"(\bOR\b|\bAND\b)\s+(['\"0-9(].*?['\"]?\s*=\s*['\"0-9])",
     "Тавтологическая инъекция (OR/AND '1'='1' / OR 1=1)"),
    (r"(\bOR\b|\bAND\b)\s+(TRUE|FALSE|1\s*=\s*1|0\s*=\s*0)\b",
     "Тавтологическая инъекция (OR TRUE / OR 1=1)"),
    # Числовые тавтологии в любом контексте: 1=1, 2=2 и т.п.
    (r"\b([0-9]+)\s*=\s*\1\b",
     "Числовая тавтология (N=N) — инъекция обхода фильтра"),
    # Строковые тавтологии: 'x'='x', 'a'='a'
    (r"'([^']+)'\s*=\s*'\1'",
     "Строковая тавтология ('x'='x') — инъекция обхода фильтра"),
    # Классический комментарий для обрезки запроса
    (r"--\s*$",
     "Инъекция через SQL-комментарий (--) в конце запроса"),
    # Inline-комментарии /**/
    (r"/\*.*?\*/",
     "Инъекция через блочный комментарий (/**/)"),
    # Несколько запросов через точку с запятой (stacked queries)
    (r";.+",
     "Stacked-запросы (несколько команд через ;) запрещены"),
    # Попытка внедрения через hex/char
    (r"\b(CHAR|CHR|HEX|UNHEX|FROM_BASE64|TO_BASE64)\s*\(",
     "Кодирование строк (CHAR/HEX) — возможная инъекция"),
    # Конкатенация строк для обхода фильтров
    (r"'\s*\|\|\s*'",
     "Конкатенация строк через || — возможная инъекция"),
]

# ── Паттерны для проверки ВОПРОСА пользователя (до генерации SQL) ───────────
# Ловим попытки инъекций прямо в тексте вопроса
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

# 3. UNION-атаки
UNION_PATTERNS = [
    (r"\bUNION\b\s+(ALL\s+)?SELECT\b",
     "UNION SELECT — попытка объединения данных из сторонних таблиц"),
]

# 4. Blind SQLi / разведка структуры БД
RECON_PATTERNS = [
    (r"\bsqlite_master\b",
     "Обращение к sqlite_master — разведка структуры БД запрещена"),
    (r"\bsqlite_schema\b",
     "Обращение к sqlite_schema — разведка структуры БД запрещена"),
    (r"\bsqlite_temp_master\b",
     "Обращение к sqlite_temp_master запрещено"),
    (r"\binformation_schema\b",
     "Обращение к information_schema — разведка структуры БД запрещена"),
    (r"\bpg_catalog\b",
     "Обращение к системным каталогам запрещено"),
    # Blind SQLi через условные функции
    (r"\bIF\s*\(\s*(SELECT|EXISTS)\b",
     "Blind SQLi через IF(SELECT...) — запрещено"),
    (r"\bCASE\s+WHEN\s+.*\bSELECT\b",
     "Blind SQLi через CASE WHEN SELECT — запрещено"),
    # Утечка данных через subquery в системные объекты
    (r"SELECT\b.*\bFROM\b.*\bsqlite_",
     "SELECT из системных таблиц SQLite запрещён"),
]

# 5. DoS-атаки на ресурсы
DOS_PATTERNS = [
    # Функции задержки
    (r"\bSLEEP\s*\(",          "SLEEP() — DoS-атака задержкой запрещена"),
    (r"\bBENCHMARK\s*\(",      "BENCHMARK() — DoS-атака запрещена"),
    (r"\bWAITFOR\s+DELAY\b",   "WAITFOR DELAY — DoS-атака запрещена"),
    (r"\bHEAVY_QUERY\b",       "HEAVY_QUERY — DoS-атака запрещена"),
    # Рекурсивные CTE без ограничений (потенциальный infinite loop)
    (r"\bWITH\s+RECURSIVE\b",  "WITH RECURSIVE — рекурсивные CTE запрещены (DoS-риск)"),
    # Картезианское произведение без WHERE (cross join)
    (r"\bCROSS\s+JOIN\b(?!.*\bON\b)(?!.*\bWHERE\b)",
     "CROSS JOIN без условия — высокая нагрузка, запрещено"),
    # Чрезмерно большой LIMIT
    (r"\bLIMIT\s+([0-9]{6,})\b",
     "LIMIT > 999999 — слишком большая выборка запрещена"),
    # Подзапросы глубже 3 уровней — эвристика через подсчёт SELECT
    # (обрабатывается отдельно ниже в validate_sql)
]

# 6. Файловые операции SQLite
FILE_PATTERNS = [
    (r"\bLOAD_FILE\s*\(",      "LOAD_FILE() — чтение файлов запрещено"),
    (r"\bINTO\s+OUTFILE\b",    "INTO OUTFILE — запись файлов запрещена"),
    (r"\bINTO\s+DUMPFILE\b",   "INTO DUMPFILE — запись файлов запрещена"),
    (r"\bATTACH\s+DATABASE\b", "ATTACH DATABASE — подключение сторонних БД запрещено"),
    (r"\breadfile\s*\(",       "readfile() — чтение файлов запрещено"),
    (r"\bwritefile\s*\(",      "writefile() — запись файлов запрещена"),
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


def validate_sql(sql: str) -> tuple[bool, str]:
    """
    Многоуровневая проверка SQL на безопасность.
    Возвращает (is_safe: bool, reason: str).
    """
    sql_upper = sql.upper().strip()
    sql_clean = sql.strip()

    # ── Уровень 0: запрос должен начинаться с SELECT или WITH (не рекурсивным) ──
    if not re.match(r"^\s*(SELECT|WITH)\b", sql_upper):
        return False, "Разрешены только SELECT-запросы"

    # ── Проверяем все категории паттернов ────────────────────────────────────
    all_pattern_groups = [
        DESTRUCTIVE_PATTERNS,
        INJECTION_PATTERNS,
        UNION_PATTERNS,
        RECON_PATTERNS,
        DOS_PATTERNS,
        FILE_PATTERNS,
    ]

    for group in all_pattern_groups:
        for pattern, reason in group:
            if re.search(pattern, sql_upper, re.IGNORECASE | re.DOTALL):
                return False, f" Запрос заблокирован: {reason}"

    # ── Уровень 1: ограничение глубины вложенности подзапросов ───────────────
    select_count = len(re.findall(r"\bSELECT\b", sql_upper))
    if select_count > 4:
        return False, " Запрос заблокирован: слишком высокая вложенность подзапросов (> 4 SELECT)"

    # ── Уровень 2: ограничение количества JOIN без WHERE ─────────────────────
    join_count = len(re.findall(r"\bJOIN\b", sql_upper))
    has_where  = bool(re.search(r"\bWHERE\b", sql_upper))
    if join_count >= 3 and not has_where:
        return False, " Запрос заблокирован: множественные JOIN без WHERE — риск высокой нагрузки"

    # ── Уровень 3: длина запроса (против очень длинных инъекций) ─────────────
    if len(sql_clean) > 2000:
        return False, " Запрос заблокирован: длина запроса превышает допустимый лимит (2000 символов)"

    return True, ""


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
        cursor.execute(f"PRAGMA table_info({table});")
        columns = cursor.fetchall()
        cursor.execute(f"SELECT COUNT(*) FROM {table};")
        row_count = cursor.fetchone()[0]

        schema_text += f"Table: {table}\nColumns:\n"
        for col in columns:
            schema_text += f"  {col[1]} {col[2]}"
            if col[5]:
                schema_text += " PRIMARY KEY"
            schema_text += "\n"
        schema_text += "\n"

        schema_dict[table] = {
            "columns": [{"name": col[1], "type": col[2], "pk": bool(col[5])} for col in columns],
            "row_count": row_count,
        }

    conn.close()
    return schema_text, schema_dict

# ── Генерация SQL ────────────────────────────────────────────────────────────
def generate_sql(question):
    schema_text, _ = get_schema()
    model = get_model()

    prompt = f"""Ты эксперт SQLite. Напиши SQL-запрос по схеме базы и вопросу пользователя.

СХЕМА БАЗЫ ДАННЫХ:
{schema_text}

ПРАВИЛА (ОБЯЗАТЕЛЬНО):
- Отвечай ТОЛЬКО чистым SQL кодом, без объяснений, без markdown, без ```.
- Запрос должен начинаться с SELECT.
- Используй правильные имена таблиц и колонок из схемы.
- Для поиска по тексту используй LIKE '%текст%'.
- Для дат используй формат YYYY-MM-DD.
- Не придумывай несуществующие алиасы (OLD., NEW. и т.д.).
- Ограничивай результат LIMIT 100, если не указано иное.

Вопрос: {question}

SQL:"""

    try:
        response = requests.post(
            OLLAMA_URL,
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=90,
        )
        response.raise_for_status()

        sql = response.json().get("response", "").strip()
        sql = re.sub(r"```(?:sql)?", "", sql, flags=re.IGNORECASE)
        sql = sql.replace("```", "").strip()
        sql = sql.split(";")[0].strip() + ";"

        return {"sql": sql, "error": None, "model": model}

    except requests.exceptions.ConnectionError:
        return {"sql": "", "error": "Ollama не запущена. Выполни в терминале: ollama serve", "model": model}
    except requests.exceptions.Timeout:
        return {"sql": "", "error": "Ollama не ответила за 90 секунд, попробуй ещё раз", "model": model}
    except Exception as e:
        return {"sql": "", "error": str(e), "model": model}

# ── Выполнение SQL ───────────────────────────────────────────────────────────
def execute_sql(sql):
    sql_stripped = sql.strip()
    sql_upper    = sql_stripped.upper()

    # ── Проверка на изменяющие операции (показываем SQL, но не выполняем) ────
    modifying = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE"]
    for word in modifying:
        if re.search(rf"^\s*{word}\b", sql_upper):
            return {
                "readonly_blocked": True,
                "message": f"Запрос сгенерирован, но не выполнен — операция {word} изменяет базу данных и заблокирована в целях безопасности.",
            }

    # ── Многоуровневая проверка безопасности ─────────────────────────────────
    is_safe, reason = validate_sql(sql_stripped)
    if not is_safe:
        return {"security_blocked": True, "message": reason}

    # ── Выполнение в режиме только чтения ────────────────────────────────────
    try:
        # uri=True + mode=ro гарантирует, что даже при обходе фильтров
        # SQLite не позволит изменить файл БД на уровне ОС
        conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row

        # Дополнительные ограничения внутри соединения
        conn.execute("PRAGMA query_only = ON;")           # запрет любых изменений
        conn.execute("PRAGMA max_page_count = 10000;")    # ограничение объёма данных

        cursor = conn.cursor()
        cursor.execute(sql_stripped)
        rows = cursor.fetchmany(1000)                     # жёсткий лимит строк
        conn.close()

        if not rows:
            return {"columns": [], "rows": [], "count": 0}

        columns = list(rows[0].keys())
        data    = [list(row) for row in rows]
        return {"columns": columns, "rows": data, "count": len(data)}

    except sqlite3.OperationalError as e:
        err_msg = str(e)
        # Не раскрываем детали схемы в сообщениях об ошибках
        if "no such table" in err_msg or "no such column" in err_msg:
            return {"error": "Ошибка SQL: указанная таблица или колонка не существует"}
        return {"error": f"Ошибка SQL: {err_msg}"}

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
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def add_to_history(question, sql, count, success, blocked=False):
    history = load_json(HIST_PATH)
    history.insert(0, {
        "id":       datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "question": question,
        "sql":      sql,
        "count":    count,
        "success":  success,
        "blocked":  blocked,
        "time":     datetime.now().strftime("%d.%m.%Y %H:%M"),
    })
    save_json(HIST_PATH, history[:100])

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
    blocked = result.get("security_blocked") or result.get("readonly_blocked")
    success = "error" not in result and not blocked
    count   = result.get("count", 0)

    add_to_history(question, gen["sql"], count, success, blocked=bool(blocked))

    return jsonify({
        "question": question,
        "sql":      gen["sql"],
        "model":    gen["model"],
        "result":   result,
    })

@app.route("/api/execute", methods=["POST"])
def execute():
    data = request.get_json()
    sql  = (data.get("sql", "") or "").strip()
    if not sql:
        return jsonify({"error": "SQL не может быть пустым"}), 400

    # Ограничение длины SQL от пользователя
    if len(sql) > 2000:
        return jsonify({"error": "SQL-запрос слишком длинный (максимум 2000 символов)"}), 400

    return jsonify(execute_sql(sql))

@app.route("/api/examples")
def examples():
    return jsonify({"examples": [
        "Покажи 5 самых дорогих товаров",
        "Сколько заказов было в 2024 году?",
        "Какой товар заказывали чаще всего?",
        "Покажи все отменённые заказы",
        "Сколько пользователей в каждом городе?",
        "Средняя цена товара по категориям",
        "Товары с остатком меньше 20 штук",
        "Пользователи с более чем 3 заказами",
        "Покажи всех пользователей из Москвы",
        "Show the 5 most expensive products",
        "How many orders in 2024?",
        "Show all cancelled orders",
    ]})

# ── История (API) ────────────────────────────────────────────────────────────
@app.route("/api/history")
def history():
    return jsonify({"history": load_json(HIST_PATH)})

@app.route("/api/history/remove", methods=["POST"])
def history_remove():
    data = request.get_json()
    hist_id = data.get("id")
    if not hist_id:
        return jsonify({"error": "ID не указан"}), 400
    history = load_json(HIST_PATH)
    history = [h for h in history if h["id"] != hist_id]
    save_json(HIST_PATH, history)
    return jsonify({"ok": True})

@app.route("/api/history/clear", methods=["POST"])
def history_clear():
    save_json(HIST_PATH, [])
    return jsonify({"ok": True})

# ── Избранное (API) ──────────────────────────────────────────────────────────
@app.route("/api/favorites")
def favorites():
    return jsonify({"favorites": load_json(FAV_PATH)})

@app.route("/api/favorites/add", methods=["POST"])
def favorites_add():
    data = request.get_json()
    favs = load_json(FAV_PATH)
    item = {
        "id":       datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "question": data.get("question", ""),
        "sql":      data.get("sql", ""),
        "time":     datetime.now().strftime("%d.%m.%Y %H:%M"),
    }
    favs.insert(0, item)
    save_json(FAV_PATH, favs)
    return jsonify({"ok": True, "item": item})

@app.route("/api/favorites/remove", methods=["POST"])
def favorites_remove():
    data = request.get_json()
    fav_id = data.get("id")
    favs = [f for f in load_json(FAV_PATH) if f["id"] != fav_id]
    save_json(FAV_PATH, favs)
    return jsonify({"ok": True})


if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"ВНИМАНИЕ: База данных не найдена — {DB_PATH}")
        print("Сначала запусти: python database.py")
    else:
        print(f"База данных: {DB_PATH}")

    print(f"Модель Ollama: {get_model()}")
    print("Сервер: http://localhost:5000  |  Остановка: Ctrl+C")

    threading.Timer(1.2, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(debug=False, port=5000)