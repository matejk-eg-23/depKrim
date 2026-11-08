CREATE TABLE customers (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL
);
CREATE TABLE hubs (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, city TEXT NOT NULL
);
CREATE TABLE carriers (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE
);
CREATE TABLE shipments (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(id),
    carrier_id INTEGER NOT NULL REFERENCES carriers(id),
    origin_id INTEGER NOT NULL REFERENCES hubs(id),
    destination_id INTEGER NOT NULL REFERENCES hubs(id),
    shipped_at TEXT NOT NULL, promised_at TEXT NOT NULL, delivered_at TEXT,
    CHECK (origin_id <> destination_id),
    CHECK (promised_at >= shipped_at),
    CHECK (delivered_at IS NULL OR delivered_at >= shipped_at)
);
CREATE TABLE parcels (
    id INTEGER PRIMARY KEY, shipment_id INTEGER NOT NULL REFERENCES shipments(id),
    tracking_code TEXT NOT NULL UNIQUE, weight_grams INTEGER NOT NULL CHECK (weight_grams > 0)
);
CREATE TABLE scans (
    parcel_id INTEGER NOT NULL REFERENCES parcels(id),
    sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
    hub_id INTEGER NOT NULL REFERENCES hubs(id),
    scanned_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('accepted', 'transit', 'delivered')),
    PRIMARY KEY (parcel_id, sequence_no)
);

INSERT INTO customers VALUES
    (1, 'Северный склад', 'Гродно'), (2, 'Мастерская Орбита', 'Минск'),
    (3, 'Учебный центр Линия', 'Брест'), (4, 'Студия Спектр', 'Витебск');
INSERT INTO hubs VALUES
    (1, 'Гродно Запад', 'Гродно'), (2, 'Минск Центр', 'Минск'),
    (3, 'Брест Юг', 'Брест'), (4, 'Витебск Север', 'Витебск');
INSERT INTO carriers VALUES (1, 'Экспресс'), (2, 'Транзит'), (3, 'Почтовый маршрут');
INSERT INTO shipments VALUES
    (1, 1, 1, 1, 2, '2026-09-01', '2026-09-03', '2026-09-03'),
    (2, 2, 1, 2, 4, '2026-09-01', '2026-09-04', '2026-09-06'),
    (3, 3, 2, 3, 1, '2026-09-02', '2026-09-04', '2026-09-05'),
    (4, 4, 2, 4, 3, '2026-09-04', '2026-09-07', NULL),
    (5, 1, 1, 1, 3, '2026-09-05', '2026-09-08', '2026-09-07'),
    (6, 2, 2, 2, 1, '2026-09-05', '2026-09-09', '2026-09-11'),
    (7, 3, 1, 3, 2, '2026-09-06', '2026-09-09', NULL),
    (8, 4, 1, 4, 2, '2026-09-06', '2026-09-10', '2026-09-10');
INSERT INTO parcels VALUES
    (1, 1, 'LG-001', 1250), (2, 1, 'LG-002', 4800), (3, 2, 'LG-003', 900),
    (4, 3, 'LG-004', 3100), (5, 4, 'LG-005', 2000), (6, 4, 'LG-006', 5500),
    (7, 5, 'LG-007', 750), (8, 6, 'LG-008', 1400), (9, 7, 'LG-009', 2400),
    (10, 8, 'LG-010', 3100), (11, 8, 'LG-011', 580);
INSERT INTO scans VALUES
    (1,1,1,'2026-09-01 09:00','accepted'), (1,2,2,'2026-09-03 13:00','delivered'),
    (2,1,1,'2026-09-01 09:00','accepted'), (2,2,2,'2026-09-03 13:00','delivered'),
    (3,1,2,'2026-09-01 08:00','accepted'), (3,2,4,'2026-09-06 15:00','delivered'),
    (4,1,3,'2026-09-02 08:30','accepted'), (4,2,2,'2026-09-03 10:00','transit'),
    (4,3,1,'2026-09-05 12:00','delivered'),
    (5,1,4,'2026-09-04 12:00','accepted'), (5,2,2,'2026-09-05 14:00','transit'),
    (6,1,4,'2026-09-04 12:00','accepted'),
    (7,1,1,'2026-09-05 10:00','accepted'), (7,2,3,'2026-09-07 10:00','delivered'),
    (8,1,2,'2026-09-05 11:00','accepted'), (8,2,1,'2026-09-11 16:00','delivered'),
    (9,1,3,'2026-09-06 10:30','accepted'), (9,2,2,'2026-09-08 10:00','transit'),
    (10,1,4,'2026-09-06 11:30','accepted'), (10,2,2,'2026-09-10 11:00','delivered'),
    (11,1,4,'2026-09-06 11:30','accepted'), (11,2,2,'2026-09-10 11:00','delivered');
