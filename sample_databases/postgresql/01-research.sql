BEGIN;

CREATE TABLE labs (id integer PRIMARY KEY, name text NOT NULL UNIQUE);
CREATE TABLE researchers (
    id integer PRIMARY KEY, name text NOT NULL, lab_id integer NOT NULL REFERENCES labs(id)
);
CREATE TABLE projects (
    id integer PRIMARY KEY, title text NOT NULL, lab_id integer NOT NULL REFERENCES labs(id),
    started_on date NOT NULL, finished_on date, CHECK (finished_on >= started_on)
);
CREATE TABLE project_members (
    project_id integer NOT NULL REFERENCES projects(id), researcher_id integer NOT NULL REFERENCES researchers(id),
    member_role text NOT NULL, PRIMARY KEY (project_id, researcher_id)
);
CREATE TABLE grants (
    id integer PRIMARY KEY, project_id integer NOT NULL REFERENCES projects(id),
    sponsor text NOT NULL, amount numeric(12,2) NOT NULL CHECK (amount > 0),
    awarded_on date NOT NULL
);
CREATE TABLE expenses (
    id integer PRIMARY KEY, grant_id integer NOT NULL REFERENCES grants(id),
    category text NOT NULL, amount numeric(12,2) NOT NULL CHECK (amount > 0), booked_on date NOT NULL
);
CREATE TABLE publications (
    id integer PRIMARY KEY, project_id integer NOT NULL REFERENCES projects(id),
    title text NOT NULL, published_on date NOT NULL
);

INSERT INTO labs VALUES (1,'Аналитика'), (2,'Инженерия'), (3,'Городские исследования'), (4,'Новые материалы');
INSERT INTO researchers VALUES
    (1,'Исследователь 01',1), (2,'Исследователь 02',1), (3,'Исследователь 03',2),
    (4,'Исследователь 04',2), (5,'Исследователь 05',3), (6,'Исследователь 06',4);
INSERT INTO projects VALUES
    (1,'Городская мобильность',1,'2025-01-10',NULL),
    (2,'Энергоаудит',2,'2025-03-01','2026-04-30'),
    (3,'Карта шума',3,'2025-06-15',NULL),
    (4,'Открытые данные',1,'2026-02-10',NULL),
    (5,'Датчики воздуха',2,'2026-03-01',NULL),
    (6,'Покрытия',4,'2026-07-01',NULL);
INSERT INTO project_members VALUES
    (1,1,'руководитель'),(1,3,'участник'),(1,5,'участник'),
    (2,3,'руководитель'),(2,4,'участник'),(3,5,'руководитель'),(3,2,'участник'),
    (4,2,'руководитель'),(4,1,'участник'),(5,4,'руководитель'),(5,5,'участник'),(6,6,'руководитель');
INSERT INTO grants VALUES
    (1,1,'Учебный фонд А',20000,'2025-01-10'),(2,1,'Учебный фонд Б',10000,'2025-07-01'),
    (3,2,'Учебный фонд Б',15000,'2025-03-01'),(4,3,'Учебный фонд А',12000,'2025-06-15'),
    (5,4,'Учебный фонд А',8000,'2026-02-10'),(6,5,'Учебный фонд В',18000,'2026-03-01');
INSERT INTO expenses VALUES
    (1,1,'оборудование',7000,'2025-02-01'),(2,1,'данные',4500,'2025-04-20'),
    (3,2,'полевые работы',3000,'2025-08-10'),(4,2,'публикации',1500,'2026-01-20'),
    (5,3,'оборудование',9000,'2025-05-10'),(6,3,'анализ',6000,'2026-03-15'),
    (7,4,'оборудование',4000,'2025-08-05'),(8,4,'полевые работы',2000,'2026-03-01'),
    (9,6,'оборудование',11000,'2026-03-10'),(10,6,'данные',3000,'2026-05-01');
INSERT INTO publications VALUES
    (1,1,'Потоки транспорта', '2025-11-01'), (2,1,'Доступность остановок', '2026-04-05'),
    (3,2,'Профиль энергопотребления', '2026-03-20'),
    (4,3,'Шум в жилых районах', '2026-06-15'), (5,4,'Метрики открытых данных', '2026-09-09');

CREATE ROLE demo_reader LOGIN PASSWORD 'depkrim-local-reader';
GRANT USAGE ON SCHEMA public TO demo_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO demo_reader;

COMMIT;
