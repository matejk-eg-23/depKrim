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

# ── Автоопределение модели ───────────────────────────────────────────────────
def get_model():
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        
        # Приоритет моделей
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
    except requests.exceptions.HTTPError as e:
        print("Ollama error response:", e.response.text)

# ── Выполнение SQL ───────────────────────────────────────────────────────────
def execute_sql(sql):
    sql_stripped = sql.strip()
    sql_upper    = sql_stripped.upper()

    # Запросы изменения данных — разрешаем генерировать, но не выполняем
    modifying = ["INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE", "CREATE"]
    for word in modifying:
        if re.search(rf"^\s*{word}\b", sql_upper):
            return {
                "readonly_blocked": True,
                "message": f"Запрос сгенерирован, но не выполнен — операция {word} изменяет базу данных и заблокирована в целях безопасности.",
            }

    if not re.match(r"^\s*(SELECT|WITH)\b", sql_upper):
        return {"error": "Разрешены только SELECT-запросы"}

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(sql_stripped)
        rows = cursor.fetchall()
        conn.close()

        if not rows:
            return {"columns": [], "rows": [], "count": 0}

        columns = list(rows[0].keys())
        data    = [list(row) for row in rows]
        return {"columns": columns, "rows": data, "count": len(data)}

    except sqlite3.OperationalError as e:
        return {"error": f"Ошибка SQL: {str(e)}"}

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

def add_to_history(question, sql, count, success):
    history = load_json(HIST_PATH)
    history.insert(0, {
        "id":       datetime.now().strftime("%Y%m%d%H%M%S%f"),
        "question": question,
        "sql":      sql,
        "count":    count,
        "success":  success,
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
            return jsonify({"error": f"База данных не найдена. Запусти: python database.py"}), 500
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
    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "Вопрос не может быть пустым"}), 400

    gen = generate_sql(question)
    if gen["error"]:
        return jsonify({"error": gen["error"]}), 500

    result  = execute_sql(gen["sql"])
    success = "error" not in result and not result.get("readonly_blocked")
    count   = result.get("count", 0)

    add_to_history(question, gen["sql"], count, success)

    return jsonify({
        "question": question,
        "sql":      gen["sql"],
        "model":    gen["model"],
        "result":   result,
    })

@app.route("/api/execute", methods=["POST"])
def execute():
    data = request.get_json()
    sql  = data.get("sql", "").strip()
    if not sql:
        return jsonify({"error": "SQL не может быть пустым"}), 400
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

# ── Удаление одного элемента из истории ───────────────────────────────
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

# ── Очистка всей истории ─────────────────────────────────────────────
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