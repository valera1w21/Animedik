"""Managing accounts from the console.

There is no sign-up through the site. Accounts are created from here or
from the administrator page. Run it like this:

    python -m api.admin add valera --admin
    python -m api.admin list
    python -m api.admin passwd valera
    python -m api.admin disable misha
    python -m api.admin delete kate

The password is typed blind; passing it in command-line arguments is not
allowed: it would stay in the shell history and in the process list.
"""

from __future__ import annotations

import argparse
import getpass
import sys
import time

from . import security, store


def ask_password(prompt: str = "Пароль: ") -> str:
    first = getpass.getpass(prompt)
    problem = security.password_problem(first)
    if problem:
        print("  ✗", problem)
        sys.exit(1)
    second = getpass.getpass("Повторите: ")
    if first != second:
        print("  ✗ Пароли не совпали")
        sys.exit(1)
    return first


def cmd_add(args) -> None:
    problem = security.login_problem(args.login)
    if problem:
        print("  ✗", problem)
        sys.exit(1)
    if store.get_user_by_login(args.login):
        print("  ✗ Такой логин уже занят")
        sys.exit(1)
    password = ask_password()
    role = "admin" if args.admin else "user"
    uid = store.create_user(args.login, password, role, args.name or args.login)
    print(f"  ✓ Создан аккаунт {args.login} (id {uid}, роль {role})")


def cmd_list(args) -> None:
    rows = store.list_users()
    if not rows:
        print("  Пока ни одного аккаунта.")
        print("  Создайте первый: python -m api.admin add ВАШ_ЛОГИН --admin")
        return
    print(f"  {'id':>3}  {'логин':<20} {'роль':<7} {'сессий':>6}  создан")
    for r in rows:
        made = time.strftime("%d.%m.%Y", time.localtime(r["created_at"]))
        mark = "  (выключен)" if r["disabled"] else ""
        print(f"  {r['id']:>3}  {r['login']:<20} {r['role']:<7} "
              f"{store.count_sessions(r['id']):>6}  {made}{mark}")


def cmd_passwd(args) -> None:
    user = store.get_user_by_login(args.login)
    if not user:
        print("  ✗ Нет такого пользователя")
        sys.exit(1)
    password = ask_password("Новый пароль: ")
    store.set_password(user["id"], password)
    print(f"  ✓ Пароль изменён. Все сессии {args.login} закрыты.")


def cmd_disable(args) -> None:
    user = store.get_user_by_login(args.login)
    if not user:
        print("  ✗ Нет такого пользователя")
        sys.exit(1)
    if user["role"] == "admin" and store.count_admins() <= 1:
        print("  ✗ Это последний администратор, его нельзя выключить")
        sys.exit(1)
    store.set_disabled(user["id"], True)
    print(f"  ✓ {args.login} больше не сможет войти. Сессии закрыты.")


def cmd_enable(args) -> None:
    user = store.get_user_by_login(args.login)
    if not user:
        print("  ✗ Нет такого пользователя")
        sys.exit(1)
    store.set_disabled(user["id"], False)
    print(f"  ✓ {args.login} снова может войти")


def cmd_delete(args) -> None:
    user = store.get_user_by_login(args.login)
    if not user:
        print("  ✗ Нет такого пользователя")
        sys.exit(1)
    if user["role"] == "admin" and store.count_admins() <= 1:
        print("  ✗ Это последний администратор")
        sys.exit(1)
    name = user["login"]
    print(f"  Будет удалён аккаунт {name} со всеми его данными.")
    # We compare against what is actually in the database, and by the
    # same rules as at sign-in. Otherwise "Valera" did not match "valera",
    # and deletion was cancelled on a correctly typed login.
    typed = security.normalize_login(input("  Введите логин ещё раз для подтверждения: "))
    if typed != name:
        print("  Отменено.")
        return
    store.delete_user(user["id"])
    print(f"  ✓ {args.login} удалён")


def main() -> None:
    store.init()
    p = argparse.ArgumentParser(prog="python -m api.admin",
                               description="Аккаунты анимеДик")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="создать аккаунт")
    a.add_argument("login")
    a.add_argument("--admin", action="store_true", help="сделать администратором")
    a.add_argument("--name", default="", help="отображаемое имя")
    a.set_defaults(func=cmd_add)

    a = sub.add_parser("list", help="показать аккаунты")
    a.set_defaults(func=cmd_list)

    a = sub.add_parser("passwd", help="сменить пароль")
    a.add_argument("login")
    a.set_defaults(func=cmd_passwd)

    a = sub.add_parser("disable", help="закрыть вход")
    a.add_argument("login")
    a.set_defaults(func=cmd_disable)

    a = sub.add_parser("enable", help="вернуть вход")
    a.add_argument("login")
    a.set_defaults(func=cmd_enable)

    a = sub.add_parser("delete", help="удалить аккаунт")
    a.add_argument("login")
    a.set_defaults(func=cmd_delete)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
