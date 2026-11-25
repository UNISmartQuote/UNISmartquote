import argparse
import getpass
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from security_store import database_path


TABLES = {
    "users": (
        "id",
        "username",
        "display_name",
        "password_salt",
        "password_hash",
        "role",
        "active",
        "must_change_password",
        "failed_attempts",
        "locked_until",
        "created_at",
    ),
    "quotes": (
        "id",
        "owner_id",
        "quote_number",
        "customer",
        "updated_at",
        "payload",
    ),
    "app_settings": ("key", "value", "updated_at"),
}


def _read_only_connection(path):
    uri_path = path.resolve().as_posix()
    return sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)


def inspect_source(path):
    if not path.is_file():
        raise FileNotFoundError("SQLite source database does not exist.")
    with closing(_read_only_connection(path)) as connection:
        existing_tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing_tables = set(TABLES) - existing_tables
        if missing_tables:
            raise ValueError("SQLite source schema is incomplete.")
        counts = {
            table: connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
            for table in TABLES
        }
        orphan_quotes = connection.execute(
            """
            SELECT COUNT(*)
            FROM quotes AS q
            LEFT JOIN users AS u ON u.id = q.owner_id
            WHERE u.id IS NULL
            """
        ).fetchone()[0]
        duplicate_usernames = connection.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT LOWER(username)
                FROM users
                GROUP BY LOWER(username)
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
    if orphan_quotes:
        raise ValueError("SQLite contains quotes without a matching owner.")
    if duplicate_usernames:
        raise ValueError("SQLite contains duplicate case-insensitive usernames.")
    return counts


def _read_source_rows(path):
    with closing(_read_only_connection(path)) as connection:
        connection.row_factory = sqlite3.Row
        return {
            table: [
                tuple(row[column] for column in columns)
                for row in connection.execute(
                    f"SELECT {', '.join(columns)} FROM {table}"
                )
            ]
            for table, columns in TABLES.items()
        }


def _target_connection(url):
    options = conninfo_to_dict(url)
    sslmode = options.get("sslmode", "")
    if sslmode not in ("require", "verify-ca", "verify-full"):
        sslmode = "require"
    return psycopg.connect(
        url,
        connect_timeout=15,
        row_factory=dict_row,
        sslmode=sslmode,
    )


def migrate(path, url):
    source_counts = inspect_source(path)
    source_rows = _read_source_rows(path)
    with _target_connection(url) as connection:
        connection.execute("SET search_path TO unismartquote")
        role = connection.execute(
            """
            SELECT rolsuper, rolbypassrls
            FROM pg_roles
            WHERE rolname = CURRENT_USER
            """
        ).fetchone()
        if role is None or not role["rolsuper"]:
            raise ValueError(
                "Migration requires the Supabase database owner connection."
            )
        target_counts = {
            table: connection.execute(
                f"SELECT COUNT(*) AS count FROM {table}"
            ).fetchone()["count"]
            for table in TABLES
        }
        if any(target_counts.values()):
            raise ValueError("Target database is not empty; migration stopped.")

        for table, columns in TABLES.items():
            placeholders = ", ".join("%s" for _ in columns)
            column_names = ", ".join(columns)
            for row in source_rows[table]:
                connection.execute(
                    f"INSERT INTO {table} ({column_names}) "
                    f"VALUES ({placeholders})",
                    row,
                )

        connection.execute(
            """
            SELECT setval(
                pg_get_serial_sequence('users', 'id'),
                COALESCE((SELECT MAX(id) FROM users), 1),
                EXISTS(SELECT 1 FROM users)
            )
            """
        )
        migrated_counts = {
            table: connection.execute(
                f"SELECT COUNT(*) AS count FROM {table}"
            ).fetchone()["count"]
            for table in TABLES
        }
        if migrated_counts != source_counts:
            raise RuntimeError("Migration verification failed; transaction rolled back.")
    return migrated_counts


def main():
    parser = argparse.ArgumentParser(
        description="Inspect or explicitly migrate a local SQLite database."
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=database_path(),
        help="SQLite database path (default: current local database).",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Prompt for a PostgreSQL URL and migrate into an empty database.",
    )
    args = parser.parse_args()

    try:
        counts = inspect_source(args.source)
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.error(str(error))

    if not args.execute:
        print("Dry run only. No data was copied.")
        for table, count in counts.items():
            print(f"{table}: {count} rows")
        print("Review the counts, back up SQLite, then rerun with --execute.")
        return 0

    url = getpass.getpass(
        "Supabase PostgreSQL URL (input hidden; not saved): "
    ).strip()
    if not url:
        parser.error("A PostgreSQL URL is required.")
    confirmation = input(
        "Type MIGRATE only after verifying the target is the UNISmartQuote "
        "project and is empty: "
    )
    if confirmation != "MIGRATE":
        print("Migration cancelled; SQLite was not changed.")
        return 1

    try:
        migrated_counts = migrate(args.source, url)
    except (OSError, psycopg.Error, RuntimeError, sqlite3.Error, ValueError):
        parser.error(
            "Migration failed or was rolled back. Check the target configuration "
            "and schema; no credential or database content was printed."
        )
    print("Migration completed and row counts verified:")
    for table, count in migrated_counts.items():
        print(f"{table}: {count} rows")
    return 0


if __name__ == "__main__":
    sys.exit(main())
