"""Управление аккаунтами из консоли.

Регистрации через сайт нет. Аккаунты заводятся отсюда либо из кабинета
администратора. Запускается так:

    python -m api.admin add your_login --admin
    python -m api.admin list
    python -m api.admin passwd your_login
    python -m api.admin disable misha
    python -m api.admin delete kate
    python -m api.admin 2fa-off your_login          # выключить вход по коду
    python -m api.admin backup /data/copy.db    # копия базы

Пароль вводится вслепую, в аргументах командной строки его передавать
нельзя: он остался бы в истории оболочки и в списке процессов.
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
    # Имя чистится так же, как в кабинете: без управляющих символов и не
    # длиннее сорока знаков (перевод строки в имени портил бы журнал).
    name = "".join(ch for ch in (args.name or "") if ch.isprintable()).strip()[:40]
    uid = store.create_user(args.login, password, role, name or args.login)
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
    # Только для включённого: выключенный администратор в подсчёт и так
    # не входит, и «выключить его ещё раз» ничего не отнимает.
    if store.is_last_admin(user):
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
    # Удалить выключенного администратора можно всегда: действующих
    # от этого меньше не станет.
    if store.is_last_admin(user):
        print("  ✗ Это последний администратор")
        sys.exit(1)
    name = user["login"]
    print(f"  Будет удалён аккаунт {name} со всеми его данными.")
    # Сравниваем с тем, что реально лежит в базе, и по тем же правилам,
    # что и при входе. Иначе «Hozyain» не совпадала с «hozyain», и удаление
    # отменялось при верно введённом логине.
    typed = security.normalize_login(input("  Введите логин ещё раз для подтверждения: "))
    if typed != name:
        print("  Отменено.")
        return
    store.delete_user(user["id"])
    print(f"  ✓ {args.login} удалён")


def cmd_2fa_off(args) -> None:
    """Выключить вход по коду из приложения — когда войти иначе нельзя.

    Нужна в двух случаях: потерян телефон вместе с запасными кодами, или
    сменили SESSION_PEPPER (секрет приложения хранится зашифрованным ею,
    и со старой строкой он больше не читается). Раньше команды не было, а
    комментарий в коде отсылал «к консоли сервера» — где сделать это было
    нечем, кроме ручной правки базы. Все входы человека при этом
    закрываются: защиту сняли — пусть войдёт заново, уже без кода.
    """
    user = store.get_user_by_login(args.login)
    if not user:
        print("  ✗ Нет такого пользователя")
        sys.exit(1)
    store.set_totp(user["id"], "", False)
    closed = store.drop_all_sessions(user["id"])
    print(f"  ✓ Вход по коду у {user['login']} выключен, закрыто входов: {closed}. "
          "Включить снова — в кабинете, раздел «Безопасность».")


def cmd_backup(args) -> None:
    """Копия базы через backup API SQLite (см. store.backup)."""
    store.backup(args.path)
    print(f"  ✓ Копия базы: {args.path}")


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

    a = sub.add_parser("2fa-off", help="выключить вход по коду из приложения")
    a.add_argument("login")
    a.set_defaults(func=cmd_2fa_off)

    a = sub.add_parser("backup", help="копия базы (согласованная, без остановки сайта)")
    a.add_argument("path")
    a.set_defaults(func=cmd_backup)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
