"""Manage login accounts.

  python scripts/manage_users.py create <username>
  python scripts/manage_users.py passwd <username>
  python scripts/manage_users.py disable|enable|delete <username>
  python scripts/manage_users.py list
"""
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from core import auth


def _prompt_password() -> str:
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords do not match.")
    return password


def main(argv: list[str]) -> None:
    if argv == ["list"]:
        for user in auth.list_users():
            print(f"{user['username']:<32} {'active' if user.get('active', True) else 'disabled'}")
        return
    if len(argv) != 2:
        sys.exit(__doc__)
    command, username = argv
    actions = {
        "create": lambda: auth.create_user(username, _prompt_password()),
        "passwd": lambda: auth.set_password(username, _prompt_password()),
        "disable": lambda: auth.set_active(username, False),
        "enable": lambda: auth.set_active(username, True),
        "delete": lambda: auth.delete_user(username),
    }
    if command not in actions:
        sys.exit(__doc__)
    try:
        auth.users().create_index("username", unique=True)
        actions[command]()
    except ValueError as exc:
        sys.exit(str(exc))
    print(f"{command}: {username} ok")


if __name__ == "__main__":
    main(sys.argv[1:])
