import argparse
import getpass
import sqlite3
import sys

from security_store import create_bootstrap_admin, database_path, initialize


def main():
    parser = argparse.ArgumentParser(
        description="Khởi tạo quản trị viên cho hệ thống báo giá U&I."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    create_admin = subparsers.add_parser(
        "create-admin", help="Tạo tài khoản quản trị viên đầu tiên."
    )
    create_admin.add_argument("username")
    create_admin.add_argument("display_name")
    args = parser.parse_args()

    initialize()
    password = getpass.getpass("Mật khẩu quản trị (tối thiểu 12 ký tự): ")
    confirmation = getpass.getpass("Nhập lại mật khẩu: ")
    if password != confirmation:
        parser.error("Hai mật khẩu không khớp.")

    try:
        create_bootstrap_admin(args.username, args.display_name, password)
    except (ValueError, sqlite3.IntegrityError) as error:
        parser.error(str(error))

    print("Đã tạo quản trị viên.")
    print("Cơ sở dữ liệu:", database_path())
    return 0


if __name__ == "__main__":
    sys.exit(main())
