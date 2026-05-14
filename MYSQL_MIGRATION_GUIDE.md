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

Set the environment variables above and run the app. On startup it automatically calls `db.create_all()`, creates default roles, the admin user, and system dummies — no manual steps needed.

---

## Path B — Export SQLite → Import into MySQL

### Step 1: Back up your SQLite file

```bash
cp instance/fair_division.db instance/fair_division.db.bak
```

Never skip this. If the import fails you want to be able to restore.

### Step 2: Stop the running app

No writes should happen during migration. Stop your Flask process before continuing.

### Step 3: Install the migration tool

```bash
pip install sqlite3-to-mysql
```

### Step 4: Create the schema via SQLAlchemy

Do **not** let `sqlite3-to-mysql` create the tables — it derives the schema from SQLite's loose DDL and the result won't match what SQLAlchemy expects. Instead, run just `db.create_all()` via Flask shell so the schema is created directly from your models:

```bash
FLASK_ENV=production flask shell
```

```python
from app import db
db.create_all()
exit()
```

We use `flask shell` instead of running the app here because running the app would also create default roles, admin user, and system dummies — which would conflict with the data being imported from SQLite.

### Step 5: Copy the data

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

`--without-tables` tells the tool to only insert rows, not recreate the schema. Foreign key checks are disabled during import and re-enabled afterward.

### Step 6: Start the app

Set the environment variables from the [Environment Variables](#environment-variables) section and start the app. If it loads and you can log in with your existing account, the migration succeeded.

---

## Known Differences Between SQLite and MySQL

These are handled automatically, but good to know if something unexpected happens.

### Foreign key enforcement

SQLite defines foreign keys but does **not** enforce them. MySQL does. If your SQLite data has orphaned rows (e.g., a `survey_participant` pointing to a deleted `user`), the import will fail with a foreign key constraint error.

Fix: find and delete the orphaned rows in SQLite before importing:

```bash
sqlite3 instance/fair_division.db \
  "SELECT * FROM survey_participant WHERE user_id NOT IN (SELECT id FROM user);"
```

### Case sensitivity on Linux

MySQL on Linux is case-sensitive for table names by default. SQLAlchemy generates all-lowercase table names, so this is fine as long as you don't write raw queries with mixed-case table names.

### Idle connection drops

MySQL closes idle connections after 8 hours by default. `ProductionConfig` already sets `SQLALCHEMY_POOL_RECYCLE = 3600` to prevent the `MySQL server has gone away` error — no action needed.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'pymysql'` | Run `pip install pymysql` |
| `Access denied for user` | Check `DATABASE_URL` credentials and that `GRANT` was run |
| `Unknown character set: utf8mb4` | MySQL version < 5.5.3 — use `utf8` instead |
| Foreign key constraint error during import | Find and remove orphaned rows in SQLite first (see above) |
| `MySQL server has gone away` | Verify `FLASK_ENV=production` is set so `POOL_RECYCLE` is active |

---

## Keeping Both Environments

| Environment | `FLASK_ENV` value | Database |
|---|---|---|
| Local development | `development` (or unset) | `instance/fair_division.db` (SQLite) |
| Production | `production` | MySQL via `DATABASE_URL` |

No code changes are needed when switching — only the environment variable changes.
