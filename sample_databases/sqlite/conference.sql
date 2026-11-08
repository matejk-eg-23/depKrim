CREATE TABLE tracks (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE rooms (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, seats INTEGER NOT NULL CHECK (seats > 0));
CREATE TABLE speakers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, organization TEXT NOT NULL);
CREATE TABLE talks (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, track_id INTEGER NOT NULL REFERENCES tracks(id),
    room_id INTEGER NOT NULL REFERENCES rooms(id), starts_at TEXT NOT NULL,
    UNIQUE (room_id, starts_at)
);
CREATE TABLE talk_speakers (
    talk_id INTEGER NOT NULL REFERENCES talks(id), speaker_id INTEGER NOT NULL REFERENCES speakers(id),
    PRIMARY KEY (talk_id, speaker_id)
);
CREATE TABLE attendees (id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL);
CREATE TABLE registrations (
    talk_id INTEGER NOT NULL REFERENCES talks(id), attendee_id INTEGER NOT NULL REFERENCES attendees(id),
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'waitlist', 'cancelled')),
    PRIMARY KEY (talk_id, attendee_id)
);
CREATE TABLE feedback (
    talk_id INTEGER NOT NULL, attendee_id INTEGER NOT NULL, rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    PRIMARY KEY (talk_id, attendee_id),
    FOREIGN KEY (talk_id, attendee_id) REFERENCES registrations(talk_id, attendee_id)
);

INSERT INTO tracks VALUES (1, 'Данные'), (2, 'Безопасность'), (3, 'Инфраструктура');
INSERT INTO rooms VALUES (1, 'Альфа', 4), (2, 'Бета', 3), (3, 'Гамма', 2);
INSERT INTO speakers VALUES
    (1, 'Докладчик 01', 'Лаборатория Север'), (2, 'Докладчик 02', 'Лаборатория Север'),
    (3, 'Докладчик 03', 'Студия Открытие'), (4, 'Докладчик 04', 'Институт Горизонт'),
    (5, 'Докладчик 05', 'Институт Горизонт');
INSERT INTO talks VALUES
    (1, 'Практика SQL', 1, 1, '2026-10-12 10:00'),
    (2, 'Разбор инцидентов', 2, 2, '2026-10-12 10:00'),
    (3, 'Контроль доступа', 2, 3, '2026-10-12 11:00'),
    (4, 'Наблюдаемость', 3, 1, '2026-10-12 11:00'),
    (5, 'Качество данных', 1, 2, '2026-10-12 12:00'),
    (6, 'Резервное копирование', 3, 3, '2026-10-12 12:00');
INSERT INTO talk_speakers VALUES (1,1),(1,2),(2,3),(3,3),(3,4),(4,5),(5,1),(6,5);
INSERT INTO attendees VALUES
    (1, 'Участник 01', 'Гродно'), (2, 'Участник 02', 'Минск'),
    (3, 'Участник 03', 'Брест'), (4, 'Участник 04', 'Гродно'),
    (5, 'Участник 05', 'Витебск'), (6, 'Участник 06', 'Минск');
INSERT INTO registrations VALUES
    (1,1,'confirmed'), (1,2,'confirmed'), (1,3,'confirmed'), (1,4,'confirmed'), (1,5,'waitlist'),
    (2,1,'confirmed'), (2,3,'confirmed'), (2,6,'cancelled'),
    (3,2,'confirmed'), (3,5,'confirmed'), (3,6,'waitlist'),
    (4,1,'confirmed'), (4,2,'confirmed'), (4,4,'confirmed'),
    (5,3,'confirmed'), (5,6,'confirmed');
INSERT INTO feedback VALUES (1,1,5),(1,2,4),(1,4,5),(2,1,5),(2,3,5),(3,2,4),(4,1,3),(4,4,4);
