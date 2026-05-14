# Moving from SQLite (Development) to MySQL (Production)

This guide covers two paths:
- **Path A** — Start fresh with MySQL in production (no existing data to migrate).
- **Path B** — Export your existing SQLite database and import it into MySQL.

---

## Prerequisites

Install the MySQL Python driver:

```bash
pip install pymysql
```

Add it to `requirements.txt`:

```
PyMySQL
```

---

## MySQL Server Setup

Do this once before either path. Connect to MySQL as root:

```bash
mysql -u root -p
```

Then create the database and a dedicated app user:

```sql
CREATE DATABASE fair_division CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER 'fd_user'@'%' IDENTIFIED BY 'strong-password-here';

GRANT ALL PRIVILEGES ON fair_division.* TO 'fd_user'@'%';

FLUSH PRIVILEGES;
```

Replace `fd_user` and `strong-password-here` with your own values. Use `'fd_user'@'localhost'` instead of `'%'` if the app and MySQL run on the same machine — it is more restrictive and preferred.

---

## Environment Variables

Set these on your production server before starting the app:

```bash
export FLASK_ENV=production
export DATABASE_URL="mysql+pymysql://fd_user:strong-password-here@HOST:3306/fair_division"
export SECRET_KEY="your-strong-secret-key"
export SECURITY_PASSWORD_SALT="your-strong-salt"
```

Replace `HOST` with your MySQL server address (or `localhost`).

---

## Path A — Fresh MySQL Database

### 1. Let Flask-SQLAlchemy create all tables

```bash
FLASK_ENV=production flask shell
```

```python
from app import app, db
with app.app_context():
    db.create_all()
```

The app is now running against MySQL with an empty database.

---

## Path B — Export SQLite → Import into MySQL

### Step 0: Back up your SQLite file first

```bash
cp instance/fair_division.db instance/fair_division.db.bak
```

Never skip this. If the import fails or produces bad data, you want to restore from this backup.

### Step 1: Stop the running app

Make sure no writes are happening during the migration. Stop your Flask process (or put the app in maintenance mode) before proceeding.

### Step 2: Install the migration tool

```bash
pip install sqlite3-to-mysql
```

### Step 3: Create the MySQL database

Follow the [MySQL Server Setup](#mysql-server-setup) section above if you haven't already.

### Step 4: Create the schema via SQLAlchemy

This is the important step. Do **not** let `sqlite3-to-mysql` create the tables — it would derive the schema from SQLite's DDL, which uses looser types and no FK enforcement. Instead, let SQLAlchemy create the tables directly from your models so the schema is exactly correct (proper column types, indexes, foreign key constraints).

```bash
FLASK_ENV=production flask shell
```

```python
from app import app, db
with app.app_context():
    db.create_all()
exit()
```

### Step 5: Copy only the data (no schema)

Now use `sqlite3-to-mysql` with `--without-tables` so it only inserts rows into the tables SQLAlchemy already created:

```bash
sqlite3mysql \
  --sqlite-file instance/fair_division.db \
  --mysql-database fair_division \
  --mysql-host HOST \
  --mysql-port 3306 \
  --mysql-user fd_user \
  --mysql-password strong-password-here \
  --mysql-charset utf8mb4 \
  --without-tables
```

Foreign key checks are disabled during import and re-enabled afterward, so row ordering is not an issue.

### Step 6: Verify the migration

Connect to MySQL and spot-check row counts:

```sql
USE fair_division;
SELECT table_name, table_rows
FROM information_schema.tables
WHERE table_schema = 'fair_division';
```

Compare with SQLite:

```bash
sqlite3 instance/fair_division.db "SELECT name FROM sqlite_master WHERE type='table';"
sqlite3 instance/fair_division.db "SELECT COUNT(*) FROM user;"
sqlite3 instance/fair_division.db "SELECT COUNT(*) FROM survey;"
sqlite3 instance/fair_division.db "SELECT COUNT(*) FROM item_ranking;"
# repeat for any other tables
```

Row counts should match. If they don't, restore from backup and investigate before retrying.

### Step 7: Switch the app to MySQL

Set the environment variables from the [Environment Variables](#environment-variables) section and start the app. The `DevelopmentConfig` (SQLite) is only loaded when `FLASK_ENV` is unset or `development`; `ProductionConfig` (MySQL) is loaded when `FLASK_ENV=production`.

---

## Known Differences Between SQLite and MySQL

These are handled automatically, but good to know if something unexpected happens.

### BOOLEAN columns → TINYINT(1)

SQLite stores booleans as integers (`0`/`1`). MySQL represents them as `TINYINT(1)`. SQLAlchemy maps both to Python `bool` transparently — no action needed.

### Foreign key enforcement

SQLite defines foreign keys but does **not** enforce them by default. MySQL (InnoDB) **does** enforce them. If your SQLite data has any orphaned rows (e.g., a `survey_participant` row pointing to a deleted `user`), the import will fail with a foreign key constraint error.

Fix: identify and delete orphaned rows in SQLite before migrating, then re-run the import.

```bash
# Example: find orphaned survey_participant rows
sqlite3 instance/fair_division.db \
  "SELECT * FROM survey_participant WHERE user_id NOT IN (SELECT id FROM user);"
```

### Case sensitivity on Linux

MySQL on Linux is **case-sensitive** for table names by default (`lower_case_table_names=0`). SQLAlchemy generates lowercase table names (`user`, `role`, `survey`, etc.), so as long as you don't hand-write queries with mixed case you'll be fine. Avoid renaming tables manually.

### Datetime columns

SQLite stores datetimes as plain text strings. MySQL stores them as `DATETIME`. `sqlite3-to-mysql` converts them automatically. SQLAlchemy abstracts the difference, so no app changes are needed.

### Idle connection drops

MySQL closes idle connections after `wait_timeout` (default: 8 hours). Without connection recycling, a Flask app that sits idle will hit `OperationalError: (2006, 'MySQL server has gone away')` on the next request.

The `ProductionConfig` in this app already sets `SQLALCHEMY_POOL_RECYCLE = 3600` (recycle connections after 1 hour) to prevent this. No action needed — just be aware of why it's there.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'pymysql'` | Run `pip install pymysql` |
| `Access denied for user` | Check `DATABASE_URL` credentials and that `GRANT` was run |
| `Unknown character set: utf8mb4` | MySQL version < 5.5.3 — use `utf8` instead |
| Text columns truncated | Ensure the database and all tables use `utf8mb4` |
| `INTEGER` auto-increment mismatch | After import, run `ALTER TABLE <name> AUTO_INCREMENT = <n+1>;` |
| Foreign key constraint error during import | Find and remove orphaned rows in SQLite first (see above) |
| `(2006, 'MySQL server has gone away')` | `SQLALCHEMY_POOL_RECYCLE` should prevent this — verify `FLASK_ENV=production` is set |
| Row counts don't match after import | Restore from backup, fix any FK violations in SQLite, re-run import |

---

## Keeping Both Environments

| Environment | `FLASK_ENV` value | Database |
|---|---|---|
| Local development | `development` (or unset) | `instance/fair_division.db` (SQLite) |
| Production | `production` | MySQL via `DATABASE_URL` |

No code changes are needed when switching — only the environment variable changes.
