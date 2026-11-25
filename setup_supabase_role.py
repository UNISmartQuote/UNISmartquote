import base64
import getpass
import hashlib
import hmac
import secrets
import sys

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo


host = input("Session pooler host (copy from Supabase Connect): ").strip()
project_ref = input("Supabase project ref: ").strip()

if not host or not project_ref:
    print("Cần nhập pooler host và project ref.")
    raise SystemExit(1)

admin_user = f"postgres.{project_ref}"
app_user = f"unisq_app.{project_ref}"

admin_password = getpass.getpass("Mật khẩu database quản trị (ẩn): ")
new_password = getpass.getpass("Mật khẩu mới cho unisq_app (ẩn): ")
confirm_password = getpass.getpass("Nhập lại mật khẩu mới (ẩn): ")

if new_password != confirm_password:
    print("Hai mật khẩu mới không khớp; chưa thay đổi mật khẩu.")
    raise SystemExit(1)

if len(new_password) < 20 or any(
    ord(char) < 33 or ord(char) > 126 for char in new_password
):
    print("Dùng ít nhất 20 ký tự ASCII hiển thị, không có dấu cách.")
    raise SystemExit(1)

# Tạo SCRAM-SHA-256 verifier phía máy này, không gửi mật khẩu gốc trong SQL.
iterations = 4096
salt = secrets.token_bytes(16)
salted_password = hashlib.pbkdf2_hmac(
    "sha256", new_password.encode("ascii"), salt, iterations
)
client_key = hmac.new(salted_password, b"Client Key", hashlib.sha256).digest()
stored_key = hashlib.sha256(client_key).digest()
server_key = hmac.new(salted_password, b"Server Key", hashlib.sha256).digest()

verifier = (
    "SCRAM-SHA-256$"
    + str(iterations)
    + ":"
    + base64.b64encode(salt).decode("ascii")
    + "$"
    + base64.b64encode(stored_key).decode("ascii")
    + ":"
    + base64.b64encode(server_key).decode("ascii")
)

admin_conninfo = make_conninfo(
    host=host,
    port=5432,
    dbname="postgres",
    user=admin_user,
    password=admin_password,
    sslmode="require",
    connect_timeout=15,
)

try:
    with psycopg.connect(admin_conninfo) as conn:
        role = conn.execute(
            """
            SELECT rolcanlogin, rolsuper, rolbypassrls
            FROM pg_roles
            WHERE rolname = %s
            """,
            ("unisq_app",),
        ).fetchone()

        if role is None:
            print("Không tìm thấy role unisq_app.")
            raise SystemExit(1)

        if not role[0] or role[1] or role[2]:
            print("Role không có cấu hình LOGIN/RLS an toàn như mong đợi.")
            raise SystemExit(1)

        statement = sql.SQL("ALTER ROLE {} WITH PASSWORD {}").format(
            sql.Identifier("unisq_app"),
            sql.Literal(verifier),
        )
        conn.execute(statement)

except psycopg.Error as error:
    print(
        "Không đặt được mật khẩu. Kiểm tra quyền quản trị và cấu hình kết nối."
    )
    print("Mã lỗi PostgreSQL:", error.sqlstate or "không có")
    raise SystemExit(1)
finally:
    admin_conninfo = None
    admin_password = None

print("Đã đặt mật khẩu role unisq_app. Đang kiểm tra đăng nhập...")

app_conninfo = make_conninfo(
    host=host,
    port=5432,
    dbname="postgres",
    user=app_user,
    password=new_password,
    sslmode="require",
    connect_timeout=15,
)

try:
    with psycopg.connect(app_conninfo) as conn:
        current_user, current_database = conn.execute(
            "SELECT current_user, current_database()"
        ).fetchone()

    print("Kết nối thành công.")
    print("Database role:", current_user)
    print("Database:", current_database)

except psycopg.Error as error:
    print(
        "Đã đặt mật khẩu, nhưng bước kiểm tra kết nối thất bại. "
        "Kiểm tra pooler host, project ref và quyền CONNECT."
    )
    print("Mã lỗi PostgreSQL:", error.sqlstate or "không có")
    raise SystemExit(2)
finally:
    app_conninfo = None
    new_password = None
    confirm_password = None
    verifier = None
    salted_password = None
    client_key = None
    stored_key = None
    server_key = None