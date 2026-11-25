import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

import app_config


PBKDF2_ITERATIONS = 600_000
PASSWORD_MIN_LENGTH = 12
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 900
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{3,64}$")
_INITIALIZED_PATHS = set()
DATABASE_ERRORS = (sqlite3.Error, psycopg.Error)


def database_path():
    configured = os.environ.get("UI_QUOTE_DB_PATH")
    if configured:
        return Path(configured).expanduser().resolve()

    root = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(root) / "UIExportQuotation" / "quotes.sqlite3"


def database_description():
    return "Supabase PostgreSQL" if app_config.database_url() else str(database_path())


class _ConnectionAdapter:
    def __init__(self, connection, is_postgres=False):
        self._connection = connection
        self.is_postgres = is_postgres

    def execute(self, query, parameters=()):
        if self.is_postgres:
            query = query.replace("?", "%s").replace(
                "CURRENT_TIMESTAMP",
                "TO_CHAR(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')",
            )
        return self._connection.execute(query, parameters)

    def executescript(self, script):
        return self._connection.executescript(script)


@contextmanager
def _connection(actor_user_id=None):
    configured_url = app_config.database_url()
    if configured_url:
        connection_options = conninfo_to_dict(configured_url)
        sslmode = connection_options.get("sslmode", "")
        if sslmode not in ("require", "verify-ca", "verify-full"):
            sslmode = "require"
        connection = psycopg.connect(
            configured_url,
            connect_timeout=15,
            sslmode=sslmode,
            row_factory=dict_row,
        )
        adapter = _ConnectionAdapter(connection, is_postgres=True)
    else:
        path = database_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=15)
        connection.row_factory = sqlite3.Row
        adapter = _ConnectionAdapter(connection)
    try:
        if configured_url:
            adapter.execute("SET search_path TO unismartquote")
            if actor_user_id is not None:
                adapter.execute(
                    "SELECT set_config('app.current_user_id', ?, TRUE)",
                    (str(actor_user_id),),
                )
        else:
            adapter.execute("PRAGMA foreign_keys = ON")
            adapter.execute("PRAGMA busy_timeout = 15000")
        yield adapter
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize():
    configured_url = app_config.database_url()
    path_key = (
        "postgres:"
        + hashlib.sha256(configured_url.encode("utf-8")).hexdigest()
        if configured_url
        else str(database_path())
    )
    if path_key in _INITIALIZED_PATHS:
        if configured_url:
            with _connection() as connection:
                connection.execute("SELECT 1")
            return
        with _connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        return
    if configured_url:
        with _connection() as connection:
            for table in ("users", "quotes", "app_settings"):
                connection.execute(f"SELECT 1 FROM {table} LIMIT 0")
            role = connection.execute(
                """
                SELECT rolsuper, rolbypassrls
                FROM pg_roles
                WHERE rolname = CURRENT_USER
                """
            ).fetchone()
            if role is None or role["rolsuper"] or role["rolbypassrls"]:
                raise RuntimeError(
                    "Use a dedicated non-superuser database role without "
                    "BYPASSRLS for the application."
                )
            quote_table = connection.execute(
                """
                SELECT relrowsecurity, relforcerowsecurity
                FROM pg_class
                WHERE oid = 'unismartquote.quotes'::regclass
                """
            ).fetchone()
            policies = connection.execute(
                """
                SELECT 1
                FROM pg_policies
                WHERE schemaname = 'unismartquote'
                  AND tablename = 'quotes'
                  AND policyname = 'quote_owner_isolation'
                """
            ).fetchone()
            if (
                quote_table is None
                or not quote_table["relrowsecurity"]
                or not quote_table["relforcerowsecurity"]
                or policies is None
            ):
                raise RuntimeError(
                    "Supabase quote row-level security is missing or disabled."
                )
        _INITIALIZED_PATHS.add(path_key)
        return
    with _connection() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA secure_delete = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                display_name TEXT NOT NULL,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'sales')),
                active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                must_change_password INTEGER NOT NULL DEFAULT 0
                    CHECK (must_change_password IN (0, 1)),
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until REAL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS quotes (
                id TEXT PRIMARY KEY,
                owner_id INTEGER NOT NULL REFERENCES users(id),
                quote_number TEXT NOT NULL,
                customer TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS quotes_owner_updated
                ON quotes(owner_id, updated_at DESC);
            """
        )
    _INITIALIZED_PATHS.add(path_key)


def _password_bytes(password):
    if not isinstance(password, str):
        raise ValueError("Mật khẩu không hợp lệ.")
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"Mật khẩu phải có ít nhất {PASSWORD_MIN_LENGTH} ký tự."
        )
    if len(password) > 256:
        raise ValueError("Mật khẩu không được vượt quá 256 ký tự.")
    return password.encode("utf-8")


def _hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", _password_bytes(password), salt, PBKDF2_ITERATIONS
    )


def _public_user(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "active": bool(row["active"]),
        "must_change_password": bool(row["must_change_password"]),
    }


def _insert_user(connection, username, display_name, password, role,
                 must_change_password):
    salt = secrets.token_bytes(16)
    password_hash = _hash_password(password, salt)
    cursor = connection.execute(
        """
        INSERT INTO users
            (username, display_name, password_salt, password_hash,
             role, must_change_password)
        VALUES (?, ?, ?, ?, ?, ?)
        RETURNING id
        """,
        (
            username,
            display_name,
            salt,
            password_hash,
            role,
            int(bool(must_change_password)),
        ),
    )
    return cursor.fetchone()["id"]


def create_user(username, display_name, password, role="sales",
                must_change_password=False):
    username = str(username or "").strip()
    display_name = str(display_name or "").strip()
    if not USERNAME_PATTERN.fullmatch(username):
        raise ValueError(
            "Tên đăng nhập phải có 3–64 ký tự: chữ, số, dấu chấm, gạch "
            "ngang hoặc gạch dưới."
        )
    if not display_name:
        raise ValueError("Vui lòng nhập tên nhân viên.")
    if role not in ("admin", "sales"):
        raise ValueError("Vai trò tài khoản không hợp lệ.")

    with _connection() as connection:
        return _insert_user(
            connection,
            username,
            display_name,
            password,
            role,
            must_change_password,
        )


def create_bootstrap_admin(
    username, display_name, password, must_change_password=False
):
    with _connection() as connection:
        if connection.is_postgres:
            connection.execute("SELECT pg_advisory_xact_lock(781934520)")
        admin_exists = connection.execute(
            "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
        ).fetchone()
        if admin_exists:
            raise ValueError("Tài khoản quản trị viên đã được khởi tạo.")
        username = str(username or "").strip()
        display_name = str(display_name or "").strip()
        if not USERNAME_PATTERN.fullmatch(username):
            raise ValueError(
                "Tên đăng nhập phải có 3–64 ký tự: chữ, số, dấu chấm, gạch "
                "ngang hoặc gạch dưới."
            )
        if not display_name:
            raise ValueError("Vui lòng nhập tên nhân viên.")
        return _insert_user(
            connection,
            username,
            display_name,
            password,
            "admin",
            must_change_password=must_change_password,
        )


def has_admin():
    with _connection() as connection:
        return connection.execute(
            "SELECT 1 FROM users WHERE role = 'admin' AND active = 1 LIMIT 1"
        ).fetchone() is not None


def get_user(user_id):
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        return _public_user(row)


def update_display_name(user_id, display_name):
    display_name = str(display_name or "").strip()
    if not display_name:
        raise ValueError("Vui lòng nhập họ và tên.")
    if len(display_name) > 120:
        raise ValueError("Họ và tên không được vượt quá 120 ký tự.")
    with _connection() as connection:
        cursor = connection.execute(
            "UPDATE users SET display_name = ? WHERE id = ? AND active = 1",
            (display_name, user_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Không tìm thấy tài khoản đang hoạt động.")


def authenticate(username, password):
    username = str(username or "").strip()
    password = password if isinstance(password, str) else ""
    now = time.time()
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM users WHERE LOWER(username) = LOWER(?)",
            (username,),
        ).fetchone()
        if row is None:
            dummy_salt = b"ui-quote-login-dummy"
            hashlib.pbkdf2_hmac(
                "sha256",
                password[:256].encode("utf-8"),
                dummy_salt,
                PBKDF2_ITERATIONS,
            )
            return None

        if (
            not row["active"]
            or (row["locked_until"] is not None and row["locked_until"] > now)
        ):
            return None

        try:
            candidate = _hash_password(password, row["password_salt"])
        except ValueError:
            candidate = b""
        if not hmac.compare_digest(candidate, row["password_hash"]):
            attempts = row["failed_attempts"] + 1
            locked_until = (
                now + LOCKOUT_SECONDS
                if attempts >= MAX_LOGIN_ATTEMPTS
                else None
            )
            connection.execute(
                """
                UPDATE users
                SET failed_attempts = ?, locked_until = ?
                WHERE id = ?
                """,
                (0 if locked_until else attempts, locked_until, row["id"]),
            )
            return None

        connection.execute(
            """
            UPDATE users
            SET failed_attempts = 0, locked_until = NULL
            WHERE id = ?
            """,
            (row["id"],),
        )
        return _public_user(row)


def change_password(user_id, password):
    salt = secrets.token_bytes(16)
    password_hash = _hash_password(password, salt)
    with _connection() as connection:
        cursor = connection.execute(
            """
            UPDATE users
            SET password_salt = ?, password_hash = ?,
                must_change_password = 0,
                failed_attempts = 0, locked_until = NULL
            WHERE id = ? AND active = 1
            """,
            (salt, password_hash, user_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Không tìm thấy tài khoản đang hoạt động.")


def list_users():
    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT id, username, display_name, role, active,
                   must_change_password, created_at
            FROM users
            ORDER BY role, username
            """
        ).fetchall()
        return [dict(row) for row in rows]


def set_user_active(user_id, active):
    with _connection() as connection:
        cursor = connection.execute(
            "UPDATE users SET active = ? WHERE id = ?",
            (int(bool(active)), user_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Không tìm thấy tài khoản.")


def reset_user_password(user_id, password):
    salt = secrets.token_bytes(16)
    password_hash = _hash_password(password, salt)
    with _connection() as connection:
        cursor = connection.execute(
            """
            UPDATE users
            SET password_salt = ?, password_hash = ?,
                must_change_password = 1,
                failed_attempts = 0, locked_until = NULL
            WHERE id = ? AND role = 'sales'
            """,
            (salt, password_hash, user_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Không tìm thấy tài khoản Sales.")


def get_ollama_model():
    with _connection() as connection:
        row = connection.execute(
            "SELECT value FROM app_settings WHERE key = 'ollama_model'"
        ).fetchone()
        return row["value"] if row else "qwen2.5vl:3b"


def set_ollama_model(user_id, model):
    model = str(model or "").strip()
    if not model or len(model) > 128:
        raise ValueError("Tên mô hình phải có từ 1 đến 128 ký tự.")
    with _connection() as connection:
        user = connection.execute(
            "SELECT role, active FROM users WHERE id = ?", (user_id,)
        ).fetchone()
        if user is None or user["role"] != "admin" or not user["active"]:
            raise PermissionError(
                "Chỉ quản trị viên đang hoạt động được đổi mô hình AI."
            )
        connection.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES ('ollama_model', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (model,),
        )


def save_quote(owner_id, quote_id, quote_number, customer, payload):
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    with _connection(actor_user_id=owner_id) as connection:
        if quote_id is not None:
            existing = connection.execute(
                "SELECT id FROM quotes WHERE id = ? AND owner_id = ?",
                (quote_id, owner_id),
            ).fetchone()
        else:
            existing = None

        if existing:
            connection.execute(
                """
                UPDATE quotes
                SET quote_number = ?, customer = ?, payload = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND owner_id = ?
                """,
                (quote_number, customer, serialized, quote_id, owner_id),
            )
        else:
            quote_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO quotes
                    (id, owner_id, quote_number, customer, payload)
                VALUES (?, ?, ?, ?, ?)
                """,
                (quote_id, owner_id, quote_number, customer, serialized),
            )
    return quote_id


def list_quotes(owner_id):
    with _connection(actor_user_id=owner_id) as connection:
        rows = connection.execute(
            """
            SELECT id, quote_number, customer, updated_at
            FROM quotes
            WHERE owner_id = ?
            ORDER BY updated_at DESC, quote_number
            """,
            (owner_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_quote(owner_id, quote_id):
    with _connection(actor_user_id=owner_id) as connection:
        row = connection.execute(
            """
            SELECT id, quote_number, customer, updated_at, payload
            FROM quotes
            WHERE id = ? AND owner_id = ?
            """,
            (quote_id, owner_id),
        ).fetchone()
        if row is None:
            return None
        quote = dict(row)
        quote["payload"] = json.loads(quote["payload"])
        return quote


def delete_quote(owner_id, quote_id):
    with _connection(actor_user_id=owner_id) as connection:
        cursor = connection.execute(
            "DELETE FROM quotes WHERE id = ? AND owner_id = ?",
            (quote_id, owner_id),
        )
        return cursor.rowcount == 1


def delete_quotes(owner_id, quote_ids):
    quote_ids = list(dict.fromkeys(str(quote_id) for quote_id in quote_ids))
    if not quote_ids:
        return []
    placeholders = ", ".join("?" for _ in quote_ids)
    parameters = [owner_id, *quote_ids]
    with _connection(actor_user_id=owner_id) as connection:
        rows = connection.execute(
            f"""
            SELECT id FROM quotes
            WHERE owner_id = ? AND id IN ({placeholders})
            """,
            parameters,
        ).fetchall()
        if len(rows) != len(quote_ids):
            return []
        cursor = connection.execute(
            f"""
            DELETE FROM quotes
            WHERE owner_id = ? AND id IN ({placeholders})
            """,
            parameters,
        )
        return quote_ids if cursor.rowcount == len(quote_ids) else []
