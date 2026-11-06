"""Проход 44: сторож публичной версии — ничего личного и ничего лишнего.

    python check44_public.py

Публичная версия собрана из личной: из неё убраны источники видео
(разборщики чужих сайтов), обслуживание сервера владельца (ops/),
внутренние отчёты и всё личное. Этот набор следит, чтобы ничего из этого
не вернулось с очередной правкой — например, при переносе исправления
из личной версии.

Что проверяется:
  1. в дереве нет файлов и папок, которых в публичной версии быть не
     должно (модули источников, ops/, отчёты аудита…);
  2. ни в одном текстовом файле нет имён и адресов сайтов-источников
     видео и их библиотек — ни латиницей, ни кириллицей;
  3. нет личного: имени и логина владельца, путей и имён его сервера
     (кроме строки авторских прав в LICENSE — она там по закону);
  4. нет похожего на секреты: ключей, токенов, закрытых ключей.

Запрещённые слова записаны здесь ЗАДОМ НАПЕРЁД — иначе этот файл сам
попадал бы под проверку (а он тоже часть публичной версии). Новое слово
добавляется так же: python -c "print('слово'[::-1])".

Код сайта здесь не запускается, сети нет, зависимостей нет.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]


def group(n):
    G[0] = n
    print("\n== " + n + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)[:600]) if extra and not ok else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)
    return ok


# ---------------------------------------------------------------- что искать
# (слово задом наперёд, как искать):
#   "sub"  — где угодно внутри текста, без учёта регистра;
#   "word" — только целым словом (буквы и цифры по краям не продолжаются):
#            короткие имена иначе цеплялись бы за обычные слова.
SOURCE_WORDS = [
    ("airbilina", "sub"), ("ytrebilina", "sub"), ("ирбилина", "sub"), ("ogemina", "sub"),
    ("og emina", "sub"), ("огемина", "sub"), ("inay", "word"), ("ymmuy", "sub"),
    ("иммю", "sub"), ("tsovemina", "sub"), ("tsov emina", "sub"), ("тсовемина", "sub"),
    ("тсов емина", "sub"), ("tsacsremaerd", "sub"), ("tsacmaerd", "sub"),
    ("tsac maerd", "sub"), ("akzerdh", "sub"), ("akzer", "sub"), ("акзер", "word"),
    ("екзер", "word"), ("укзер", "word"), ("йокзер", "word"), ("kidok", "sub"),
    ("кидок", "sub"), ("moobina", "sub"), ("tenbis", "sub"), ("ustuj", "word"),
    ("us.tuj", "sub"), ("ilcina", "sub"), ("ilc-ina", "sub"), ("ypina", "sub"),
    ("eminaih", "sub"), ("ehapemina", "sub"), ("ehap", "word"), ("kiwk", "sub"),
    ("enozina", "sub"), ("buhemina", "sub"), ("emina321", "sub"), ("yalpagem", "sub"),
    ("oedivohce", "sub"), ("ppa.bdina", "sub"), ("ppabdina", "sub"), ("iappo", "sub"),
    ("йаппо", "sub"), ("axevina", "sub"), ("orurim", "sub"), ("ipa-wfsn", "sub"),
    ("naecoiatneh", "sub"), ("draug-sodd", "sub"), ("bilemina", "sub"), ("embilina", "sub"),
    ("dnabemas", "sub"), ("ndcowo", "sub"), ("tacecapsym", "sub"), ("balina", "sub"),
    ("okoz", "sub"), ("otokina", "sub"), ("lloryhcnurc", "sub"), ("aynudemina", "sub"),
    ("sevawina", "sub"), ("mpina", "word"), ("aak", "word"), ("budina", "sub"),
    ("azihs", "sub"),
]
# Личное владельца. В LICENSE его имя стоит законно (авторские права).
PERSONAL_WORDS = [
    ("arelav", "sub"), ("релав", "sub"), ("hcivonra", "sub"), ("чивонра", "sub"),
    ("2eminaetis", "sub"), ("eminaeits", "sub"), ("/emoh/", "sub"),
]
# Похожее на секреты: ключи Groq/OpenAI/GitHub/AWS/Slack, токен
# Telegram-бота, закрытые ключи. Шаблоны пишутся как есть: сами по себе
# они ничего не раскрывают.
SECRET_PATTERNS = [
    r"\bgsk_[A-Za-z0-9]{20,}", r"\bsk-[A-Za-z0-9_-]{20,}", r"\bgh[pousr]_[A-Za-z0-9]{30,}",
    r"\bAKIA[0-9A-Z]{16}\b", r"\bxox[abpr]-[A-Za-z0-9-]{10,}", r"\b\d{8,10}:AA[A-Za-z0-9_-]{30,}",
    r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
]
# Чего в публичной версии быть не должно вовсе (п. 1–3 при сборке).
# README.md и прочие документы для людей сюда не входят: их пишут
# заново, а их текст проверяется на слова выше, как и всё остальное.
FORBIDDEN_PATHS = [
    "api/english.py", "api/english_web.py", "api/english_live.py",
    "api/" + "iappo"[::-1] + ".py", "api/" + "axevina"[::-1] + ".py",
    "english_check.py", "debug-sources", "debug-sources.zip", "testdata/sources",
    "ops", "audit", "АУДИТ.md", "AUDIT-BE.md", "AUDIT-FE.md", "AUDIT-OPS.md",
    "ЧТО-ИСПРАВЛЕНО.md",
]
FORBIDDEN_GLOBS = [re.compile(r"^ОТЧЁТ-АУДИТА-.*\.md$"), re.compile(r"^AUDIT-.*\.md$")]

SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", "журналы-проверок"}
# Двоичные файлы: шрифты, картинки, видео — текста в них нет.
SKIP_EXT = (".woff2", ".woff", ".png", ".ico", ".jpg", ".jpeg", ".gif", ".webp", ".webm",
            ".mp4", ".m4s", ".db", ".pyc", ".zip")


def build(words):
    parts = []
    for rev, how in words:
        w = rev[::-1]
        body = re.escape(w)
        if how == "word":
            body = r"(?<![0-9A-Za-zА-Яа-яЁё_])" + body + r"(?![0-9A-Za-zА-Яа-яЁё_])"
        parts.append(body)
    return re.compile("|".join(parts), re.I)


def files():
    for root, dirs, names in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            if name.lower().endswith(SKIP_EXT):
                continue
            path = os.path.join(root, name)
            rel = os.path.relpath(path, HERE).replace(os.sep, "/")
            try:
                with open(path, encoding="utf-8") as fh:
                    yield rel, fh.read()
            except (UnicodeDecodeError, OSError):
                continue


def hits(rx, skip=()):
    out = []
    for rel, text in files():
        if rel in skip:
            continue
        # Имя файла — тоже текст: «iappo_search.html» выдал бы источник.
        for m in rx.finditer(rel):
            out.append(f"{rel} (имя файла): {m.group(0)}")
        for n, line in enumerate(text.splitlines(), 1):
            for m in rx.finditer(line):
                out.append(f"{rel}:{n}: {m.group(0)}")
    return out


def run():
    group("1. Лишних файлов нет")
    present = [p for p in FORBIDDEN_PATHS if os.path.exists(os.path.join(HERE, p))]
    present += [n for n in os.listdir(HERE) if any(g.match(n) for g in FORBIDDEN_GLOBS)
                and n not in present]
    check("модулей источников, ops/, отчётов аудита и образцов чужих страниц нет",
          not present, present)

    group("2. Имён и адресов источников видео нет")
    found = hits(build(SOURCE_WORDS))
    check("ни одного упоминания источников и их библиотек", not found, found[:20])
    print(f"  совпадений: {len(found)}")

    group("3. Личного нет")
    found = hits(build(PERSONAL_WORDS), skip=("LICENSE",))
    check("ни имени и логина владельца, ни путей и имён его сервера", not found, found[:20])
    print(f"  совпадений: {len(found)}")

    group("4. Секретов нет")
    found = hits(re.compile("|".join(SECRET_PATTERNS)))
    check("ни ключей, ни токенов, ни закрытых ключей", not found, found[:20])
    env = [rel for rel, _ in files() if os.path.basename(rel) == ".env"]
    check("файла .env в дереве нет (только .env.example)", not env, env)

    group("5. Сторож сам ловит то, что должен")
    # Проверка проверки: если вписать запрещённое слово обратно — найдётся.
    rx = build(SOURCE_WORDS)
    probe = {w[::-1]: bool(rx.search("x " + w[::-1] + " y")) for w, _ in SOURCE_WORDS}
    check("каждое слово списка находится", all(probe.values()),
          [k for k, v in probe.items() if not v])
    check("и в другом регистре", bool(rx.search("airbiLinA"[::-1].upper())))
    check("короткие имена — только целым словом (обычные слова не задевает)",
          not rx.search("нарезка обрезка резкий Jujutsu siyanie") and bool(rx.search(" " + "akzer"[::-1] + " ")))
    rx_p = build(PERSONAL_WORDS)
    check("личное находится", bool(rx_p.search("login " + "arelav"[::-1])))
    rx_s = re.compile("|".join(SECRET_PATTERNS))
    check("ключ находится", bool(rx_s.search("HELPER_API_KEY=gsk_" + "A1" * 20)))

    print("\n" + "=" * 60)
    if FAILS:
        print("ПРОХОД 44 — ПРОВАЛЫ:")
        for f in FAILS:
            print("  - " + f)
        return 1
    print("ПРОХОД 44 — публичная версия чистая.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
