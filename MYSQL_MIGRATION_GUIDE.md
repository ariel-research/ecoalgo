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

## Environment Variables

Set these on your production server before starting the app:

```bash
export FLASK_ENV=production
export DATABASE_URL="mysql+pymysql://USER:PASSWORD@HOST:3306/fair_division"
export SECRET_KEY="your-strong-secret-key"
export SECURITY_PASSWORD_SALT="your-strong-salt"
```

Replace `USER`, `PASSWORD`, `HOST` with your MySQL credentials. The app reads `FLASK_ENV` to select the production config, which uses `DATABASE_URL` as the connection string.

---

## Path A — Fresh MySQL Database

### 1. Create the database in MySQL

```sql
CREATE DATABASE fair_division CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 2. Let Flask-SQLAlchemy create all tables

```bash
FLASK_ENV=production flask shell
```

```python
from app import app, db
with app.app_context():
    db.create_all()
```

The app is now running against MySQL.

---

## Path B — Export SQLite → Import into MySQL

### Step 1: Install the migration tool

```bash
pip install sqlite3-to-mysql
```

### Step 2: Create the target MySQL database

```sql
CREATE DATABASE fair_division CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### Step 3: Export and import in one command

```bash
sqlite3mysql \
  --sqlite-file instance/fair_division.db \
  --mysql-database fair_division \
  --mysql-host HOST \
  --mysql-port 3306 \
  --mysql-user USER \
  --mysql-password PASSWORD \
  --mysql-charset utf8mb4
```

This creates all tables and copies all rows.

### Step 4: Verify the migration

Connect to MySQL and spot-check row counts against what was in SQLite:

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
# repeat for other tables
```

### Step 5: Switch the app to MySQL

Set the environment variables from the [Environment Variables](#environment-variables) section and restart the app. The `DevelopmentConfig` (SQLite) is only loaded when `FLASK_ENV` is not set or is `development`; `ProductionConfig` (MySQL) is loaded when `FLASK_ENV=production`.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'pymysql'` | Run `pip install pymysql` |
| `Access denied for user` | Check `DATABASE_URL` credentials |
| `Unknown character set: utf8mb4` | Your MySQL version is < 5.5.3 — use `utf8` instead |
| Text columns truncated | Ensure the database and all tables use `utf8mb4` |
| `INTEGER` auto-increment mismatch | After import, run `ALTER TABLE <name> AUTO_INCREMENT = <n+1>;` for any sequence gaps |

---

## Keeping Both Environments

| Environment | `FLASK_ENV` value | Database |
|---|---|---|
| Local development | `development` (or unset) | `instance/fair_division.db` (SQLite) |
| Production | `production` | MySQL via `DATABASE_URL` |

No code changes are needed when switching — only the environment variable changes.
