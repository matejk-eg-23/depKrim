# Базы для проверки внешних подключений

Здесь четыре независимые базы с демонстрационными данными. Магазин перенесён из исходного `shop.db` коммита `565f5ab`: 15 покупателей, 15 товаров и 120 заказов. Остальные наборы созданы специально для проверки связей, агрегаций, составных ключей и пустых результатов.

## Как подготовить и подключить

Из корня проекта создайте два файла SQLite:

```powershell
.\.venv\Scripts\python.exe sample_databases/build_sqlite.py
```

Скрипт создаёт `sqlite/logistics.db` и `sqlite/conference.db` внутри этого каталога. Уже существующие файлы сохраняются. Для SQLite в интерфейсе укажите абсолютный путь к нужному файлу. Узнать оба пути можно командой `Resolve-Path .\sample_databases\sqlite\*.db`.

Для серверных баз запустите Docker Desktop, затем из корня проекта выполните:

```powershell
docker compose -f sample_databases/compose.yaml up -d
docker compose -f sample_databases/compose.yaml logs mysql postgres
```

При первом запуске контейнеры создадут таблицы и загрузят данные из SQL-файлов. Дождитесь, пока обе базы закончат инициализацию. Compose публикует порты только на `127.0.0.1`. Если MySQL или PostgreSQL уже установлены локально, можно импортировать SQL из папок `mysql/` и `postgresql/` администратором в новые базы с указанными именами. Скрипты рассчитаны на пустые базы. [MySQL в Docker](https://hub.docker.com/_/mysql), [PostgreSQL в Docker](https://hub.docker.com/_/postgres).

| Название подключения | Тип | Сервер и порт | База |
| --- | --- | --- | --- |
| Магазин | MySQL | `127.0.0.1:13306` | `demo_shop` |
| Исследовательские гранты | PostgreSQL | `127.0.0.1:15432` | `demo_research` |
| Логистика | SQLite | Абсолютный путь к файлу | `logistics.db` |
| Конференция | SQLite | Абсолютный путь к файлу | `conference.db` |

Логин для обеих серверных баз — `demo_reader`, пароль — `depkrim-local-reader`. Это реквизиты локального тестового пользователя с правом чтения. Сервер и порт в форме вводятся в отдельные поля.

1. Войдите в приложение администратором и откройте «Подключения к базам».
2. Заполните параметры, нажмите «Проверить и сохранить», затем «Выбрать».
3. Для генерации вставьте формулировку в поле вопроса. Для проверки самого подключения нажмите **«Ввести SQL»**, вставьте один блок из нужного раздела и нажмите **«Проверить / выполнить»**. Готовый SQL можно проверить без Ollama.
4. Откройте «Структура → Диаграмма связей», чтобы сверить внешние ключи. Чтобы вернуться к ГрГУ, выберите встроенную базу в списке подключений.

Остановить серверные контейнеры можно командой `docker compose -f sample_databases/compose.yaml stop`. Данные сохраняются в томах.

## Магазин · MySQL

Таблицы `users`, `products`, `orders`. Заказ связывает покупателя и товар. Сумма заказа равна цене товара, умноженной на количество.

**Вопрос:** «Покажи выручку и количество доставленных заказов по категориям, от большей выручки к меньшей». Ожидаются 6 категорий. Первыми идут «Ноутбуки» с выручкой 2 444 660 и 18 заказами.

```sql
SELECT p.category, COUNT(*) AS deliveries, SUM(o.quantity * p.price) AS revenue
FROM orders o JOIN products p ON p.id = o.product_id
WHERE o.status = 'доставлен'
GROUP BY p.category
ORDER BY revenue DESC, p.category;
```

**Вопрос:** «Какие покупатели сделали не меньше десяти заказов, независимо от статуса?» Ожидаются 5 покупателей. У Ольги Морозовой 16 заказов.

```sql
SELECT u.id, u.name, COUNT(*) AS orders_count
FROM users u JOIN orders o ON o.user_id = u.id
GROUP BY u.id, u.name
HAVING COUNT(*) >= 10
ORDER BY orders_count DESC, u.id;
```

## Логистика · SQLite

6 таблиц. Отправление связано с клиентом, перевозчиком и двумя пунктами маршрута. У отправления может быть несколько посылок, у каждой посылки — последовательность событий сканирования. `delivered_at = NULL` означает, что доставка не завершена.

**Вопрос:** «Покажи число отправлений и просроченных доставок по каждому перевозчику, включая перевозчиков без отправлений». Ожидаются строки «Экспресс» — 5 и 1, «Транзит» — 3 и 2, «Почтовый маршрут» — 0 и 0.

```sql
SELECT c.name, COUNT(s.id) AS shipments,
       SUM(CASE WHEN s.delivered_at > s.promised_at THEN 1 ELSE 0 END) AS late
FROM carriers c LEFT JOIN shipments s ON s.carrier_id = c.id
GROUP BY c.id, c.name ORDER BY c.id;
```

**Вопрос:** «Покажи последнее событие каждой посылки из незавершённых отправлений». Ожидаются `LG-005 / transit`, `LG-006 / accepted` и `LG-009 / transit`.

```sql
WITH latest AS (
    SELECT p.tracking_code, sc.status, sc.scanned_at,
           ROW_NUMBER() OVER (PARTITION BY p.id ORDER BY sc.sequence_no DESC) AS position
    FROM parcels p
    JOIN shipments s ON s.id = p.shipment_id
    JOIN scans sc ON sc.parcel_id = p.id
    WHERE s.delivered_at IS NULL
)
SELECT tracking_code, status, scanned_at FROM latest
WHERE position = 1 ORDER BY tracking_code;
```

## Конференция · SQLite

8 таблиц. Доклад может иметь нескольких спикеров, участник может записаться на несколько докладов. Отзыв ссылается на регистрацию по составному ключу `(talk_id, attendee_id)`. В занятость зала входят только регистрации со статусом `confirmed`.

**Вопрос:** «Какие доклады заполнили зал подтверждёнными регистрациями?» Ожидаются «Практика SQL» (4 из 4) и «Контроль доступа» (2 из 2).

```sql
SELECT t.title, r.seats, COUNT(reg.attendee_id) AS confirmed
FROM talks t JOIN rooms r ON r.id = t.room_id
LEFT JOIN registrations reg ON reg.talk_id = t.id AND reg.status = 'confirmed'
GROUP BY t.id, t.title, r.seats
HAVING COUNT(reg.attendee_id) >= r.seats
ORDER BY t.id;
```

**Вопрос:** «Найди докладчиков, которые выступают больше одного раза, и посчитай их доклады». Ожидаются «Докладчик 01», «Докладчик 03» и «Докладчик 05», по 2 доклада.

```sql
SELECT s.name, COUNT(ts.talk_id) AS talks_count
FROM speakers s JOIN talk_speakers ts ON ts.speaker_id = s.id
GROUP BY s.id, s.name
HAVING COUNT(ts.talk_id) > 1
ORDER BY s.id;
```

## Исследовательские гранты · PostgreSQL

7 таблиц. У проектов есть лаборатории, участники, гранты и публикации. Один проект может получать несколько грантов. Расходы привязаны к гранту, а не напрямую к проекту. В наборе есть грант без расходов и проект без финансирования.

**Вопрос:** «Посчитай бюджет, расходы и остаток для каждого проекта, включая проекты без грантов». Ожидаются 6 проектов. У «Городской мобильности» бюджет 30 000, расходы 16 000, остаток 14 000. У проекта «Покрытия» все три суммы равны нулю.

```sql
WITH costs AS (
    SELECT grant_id, SUM(amount) AS spent FROM expenses GROUP BY grant_id
)
SELECT p.title, COALESCE(SUM(g.amount), 0) AS budget,
       COALESCE(SUM(c.spent), 0) AS spent,
       COALESCE(SUM(g.amount), 0) - COALESCE(SUM(c.spent), 0) AS balance
FROM projects p LEFT JOIN grants g ON g.project_id = p.id
LEFT JOIN costs c ON c.grant_id = g.id
GROUP BY p.id, p.title ORDER BY p.id;
```

**Вопрос:** «Кто участвует в проектах других лабораторий и в скольких таких проектах?» Ожидаются «Исследователь 05» — 2 проекта, «Исследователь 02» и «Исследователь 03» — по 1.

```sql
SELECT r.name, COUNT(*) AS projects_count
FROM researchers r JOIN project_members m ON m.researcher_id = r.id
JOIN projects p ON p.id = m.project_id
WHERE p.lab_id <> r.lab_id
GROUP BY r.id, r.name ORDER BY projects_count DESC, r.id;
```

## Дополнительные проверки

- Пустой результат для SQLite-логистики: `SELECT * FROM shipments WHERE id = 99999;`.
- Попытка записи для той же базы: `UPDATE shipments SET delivered_at = NULL WHERE id = 1;`. Ожидается блокировка внешней записи, а данные остаются прежними.
- Повторите готовый SELECT после смены подключения и возврата к исходной базе. История и избранное должны показывать записи текущего источника.
