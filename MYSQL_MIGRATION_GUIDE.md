# Moving from SQLite (Development) to MySQL (Production)

This guide covers two paths:
- **Path A** — Start fresh with MySQL in production (no existing data to migrate).
- **Path B** — Export your existing SQLite database and import it into MySQL.

---

## Prerequisites

**On your production server** (bash terminal):

```bash
pip install pymysql
```

Add it to `requirements.txt`:

```
PyMySQL
```

---

## MySQL Server Setup

Do this once before either path.

**On your production server** (bash terminal) — connect to MySQL as root:

```bash
mysql -u root -p
```

You are now in the **MySQL shell**. Run:

```sql
CREATE DATABASE fair_division CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

CREATE USER 'fd_user'@'%' IDENTIFIED BY 'strong-password-here';

GRANT ALL PRIVILEGES ON fair_division.* TO 'fd_user'@'%';

FLUSH PRIVILEGES;
```

Then exit the MySQL shell:

```sql
exit
```

Replace `fd_user` and `strong-password-here` with your own values. Use `'fd_user'@'localhost'` instead of `'%'` if the app and MySQL run on the same machine — it is more restrictive and preferred.

**If you already created the user** and need to switch to `mysql_native_password` (required to avoid needing the `cryptography` package with MySQL 8.0+):

```sql
ALTER USER 'fd_user'@'localhost' IDENTIFIED WITH mysql_native_password BY 'your-password';
FLUSH PRIVILEGES;
```

**If your password contains special characters** (`&`, `!`, `$`, etc.), wrap it in single quotes in `.env`:

```bash
export DB_PASSWORD='your&special!password'
```

Without quotes, bash interprets `&` as "run in background" and the variable ends up empty, causing `Access denied` errors.

---

## Environment Variables

The `.env` file in the project root holds all these values. Fill it in, then load it as described below.

> **Note:** `FLASK_ENV=production` is required even on your dev PC to activate MySQL. `DevelopmentConfig` hardcodes SQLite, so without this the app ignores the DB variables entirely.

```bash
export FLASK_ENV=production
export DB_HOST=localhost        # or your MySQL server address
export DB_NAME=fair_division
export DB_USER=fd_user
export DB_PASSWORD=strong-password-here
export SECRET_KEY=your-strong-secret-key
export SECURITY_PASSWORD_SALT=your-strong-salt
```

**`SECRET_KEY` and `SECURITY_PASSWORD_SALT`:**

- **Production:** generate strong random values. Run this command twice on the server (once per key) and paste the output into `.env`:
  ```bash
  python3 -c "import secrets; print(secrets.token_hex(32))"
  ```
- **Dev (your PC):** any value works, e.g., `dev-key-123` and `dev-salt-123`.

`DB_PORT` defaults to `3306` and `DB_HOST` defaults to `localhost` if not set.

**Loading the variables:**

- **Production server:** add each `export` line to your shell profile (e.g., `~/.bashrc`, `~/.profile`) or your process manager config (e.g., a `systemd` unit file) so they persist across reboots and SSH sessions.
- **Dev (your PC):** run `source .env` in your terminal before starting the app. The `.env` file is already in the project root — just fill in the values and source it each session (or add `source /path/to/project/.env` to `~/.bashrc`).

---

## Path A — Fresh MySQL Database

Set the environment variables above and start the app on your production server. On startup it automatically calls `db.create_all()`, creates default roles, the admin user, and system dummies — no manual steps needed.

---

## Path B — Export SQLite → Import into MySQL

All steps below run **on your production server** (bash terminal) unless otherwise noted.

### Step 1: Back up your SQLite file

**On your production server** (bash terminal):

```bash
cp instance/fair_division.db instance/fair_division.db.bak
```

Never skip this. If the import fails you want to be able to restore.

### Step 2: Stop the running app

No writes should happen during migration. Stop your Flask process before continuing.

### Step 3: Install the migration tool

**On your production server** (bash terminal):

```bash
pip install sqlite3-to-mysql
```

### Step 4: Copy the data

**On your production server** (bash terminal) — make sure the environment variables are set so `$DB_NAME`, `$DB_HOST`, etc. expand correctly:

```bash
sqlite3mysql \
  --sqlite-file instance/fair_division.db \
  --mysql-database $DB_NAME \
  --mysql-host $DB_HOST \
  --mysql-port 3306 \
  --mysql-user $DB_USER \
  --mysql-password $DB_PASSWORD \
  --mysql-charset utf8mb4 \
  --without-foreign-keys
```

`sqlite3mysql` creates the tables and inserts all rows. `--without-foreign-keys` disables foreign key checks during import so rows can be inserted in any order without constraint errors. When the app first starts after import, `run_migrations()` adds any columns that SQLAlchemy models have but the SQLite schema didn't capture.

### Step 5: Start the app

**On your production server** (bash terminal) — make sure the environment variables are set, then start the app. If it loads and you can log in with your existing account, the migration succeeded.

---

## Known Differences Between SQLite and MySQL

These are handled automatically, but good to know if something unexpected happens.

### Foreign key enforcement

SQLite defines foreign keys but does **not** enforce them. MySQL does. If your SQLite data has orphaned rows (e.g., a `survey_participant` pointing to a deleted `user`), the import will fail with a foreign key constraint error.

Fix: find and delete the orphaned rows in SQLite **before** importing. **On your production server** (bash terminal):

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
| `ModuleNotFoundError: No module named 'pymysql'` | Run `pip install pymysql` on the production server |
| `Access denied for user` | Check credentials in your environment variables and that `GRANT` was run in the MySQL shell. If your password has special characters (`&`, `!`, `$`), wrap it in single quotes in `.env`: `export DB_PASSWORD='pass&word'` |
| `Unknown character set: utf8mb4` | MySQL version < 5.5.3 — use `utf8` instead |
| Foreign key constraint error during import | Find and remove orphaned rows in SQLite first (see above) |
| `MySQL server has gone away` | Verify `FLASK_ENV=production` is set so `POOL_RECYCLE` is active |
| `$DB_NAME` not expanding in Step 4 | Make sure you ran the `export` commands (or `source .env`) in the same terminal session |
| `RuntimeError: 'cryptography' package is required` | MySQL 8.0+ uses `caching_sha2_password` by default. Switch the user to native password in the MySQL shell: `ALTER USER 'fd_user'@'localhost' IDENTIFIED WITH mysql_native_password BY 'your-password'; FLUSH PRIVILEGES;` |

---

## Keeping Both Environments

| Environment | `FLASK_ENV` value | Database | Load env vars via |
|---|---|---|---|
| Local dev (normal) | `development` (or unset) | `instance/fair_division.db` (SQLite) | nothing needed |
| Local dev (testing MySQL) | `production` | MySQL (local) | `source .env` in terminal |
| Production server | `production` | MySQL | shell profile or `systemd` unit |

No code changes are needed when switching — only the environment variable changes.
