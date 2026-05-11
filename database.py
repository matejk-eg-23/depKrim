# database.py — run once to create the demo database: python database.py

import sqlite3
import random
from datetime import datetime, timedelta

DB_PATH = "shop.db"


def create_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            city TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL,
            price REAL NOT NULL,
            stock INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            order_date TEXT NOT NULL,
            status TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (product_id) REFERENCES products(id)
        );
    """)
    print("Tables created")


def fill_data(conn):
    cities = ["Москва", "Санкт-Петербург", "Казань", "Екатеринбург", "Новосибирск", "Краснодар"]
    names = [
        "Алексей Иванов", "Мария Петрова", "Дмитрий Сидоров", "Анна Козлова",
        "Сергей Новиков", "Ольга Морозова", "Иван Волков", "Екатерина Лебедева",
        "Андрей Соколов", "Наталья Попова", "Михаил Орлов", "Татьяна Зайцева",
        "Павел Белов", "Юлия Фёдорова", "Роман Гусев"
    ]

    users = []
    for i, name in enumerate(names):
        email = f"user{i}@mail.ru"
        city = random.choice(cities)
        date = (datetime(2023, 1, 1) + timedelta(days=random.randint(0, 700))).strftime("%Y-%m-%d")
        users.append((name, email, city, date))
    conn.executemany("INSERT OR IGNORE INTO users (name, email, city, created_at) VALUES (?,?,?,?)", users)

    products = [
        ("iPhone 15", "Смартфоны", 89990, 25),
        ("Samsung Galaxy S24", "Смартфоны", 74990, 30),
        ("Xiaomi Redmi Note 13", "Смартфоны", 24990, 50),
        ("MacBook Air M2", "Ноутбуки", 119990, 10),
        ("Lenovo IdeaPad 5", "Ноутбуки", 54990, 20),
        ("ASUS ZenBook 14", "Ноутбуки", 69990, 15),
        ("Sony WH-1000XM5", "Наушники", 29990, 40),
        ("AirPods Pro 2", "Наушники", 24990, 35),
        ("JBL Tune 770NC", "Наушники", 7990, 60),
        ("iPad Air 5", "Планшеты", 59990, 18),
        ("Samsung Galaxy Tab S9", "Планшеты", 64990, 12),
        ("Logitech MX Master 3", "Аксессуары", 8990, 80),
        ("Keychron K2", "Аксессуары", 9990, 45),
        ("Anker PowerBank 20000", "Аксессуары", 3990, 100),
        ("Apple Watch Series 9", "Умные часы", 39990, 22),
    ]
    conn.executemany("INSERT OR IGNORE INTO products (name, category, price, stock) VALUES (?,?,?,?)", products)

    statuses = ["доставлен", "доставлен", "доставлен", "в пути", "отменён", "обрабатывается"]
    orders = []
    for _ in range(120):
        user_id = random.randint(1, len(users))
        product_id = random.randint(1, len(products))
        quantity = random.randint(1, 3)
        date = (datetime(2024, 1, 1) + timedelta(days=random.randint(0, 364))).strftime("%Y-%m-%d")
        status = random.choice(statuses)
        orders.append((user_id, product_id, quantity, date, status))
    conn.executemany(
        "INSERT INTO orders (user_id, product_id, quantity, order_date, status) VALUES (?,?,?,?,?)",
        orders
    )

    conn.commit()
    print(f"Inserted: {len(users)} users, {len(products)} products, {len(orders)} orders")


if __name__ == "__main__":
    conn = sqlite3.connect(DB_PATH)
    create_tables(conn)
    fill_data(conn)
    conn.close()
    print(f"Done! Database saved to: {DB_PATH}")
