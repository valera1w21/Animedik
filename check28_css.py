"""Проход 28: стили целы — скобки сходятся, у каждой анимации есть кадры.

Хозяин: «анимация как будто сломана, связана с Хэллоуином». Причина —
правка подвала в fix-20: при замене куска app.css съелось правило
.letuchka и половина следующего, а обрывок «color-mix(…))}» браузер
склеил с @keyframes polyot в одно битое правило и выбросил оба. Летучие
мыши остались без position и без анимации — неподвижной кучкой в левом
верхнем углу; при «меньше движения» не расставлялись тоже. Остальные
праздники это не задело — поэтому сломанным выглядел только Хэллоуин.

Такое глазами в диффе не видно, поэтому проверяем сам файл:
  * вне комментариев фигурные и круглые скобки сходятся, и ни одна
    закрывающая не стоит раньше открывающей;
  * у каждого имени в animation / animation-name есть свой @keyframes;
  * мыши Хэллоуина: правило .letuchka с position:absolute и анимацией
    polyot на месте, кадры polyot — тоже; «меньше движения» их
    останавливает и расставляет.
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


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(name)
    return ok


def strip_comments(css: str) -> str:
    # Строки в app.css кавычек со звёздочками не содержат — простого
    # выреза достаточно; переводы строк сохраняем ради номеров строк.
    return re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), css, flags=re.S)


def balance(css: str) -> list[str]:
    errs, depth, paren = [], 0, 0
    line = 1
    for ch in css:
        if ch == "\n":
            line += 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if paren:
                errs.append(f"строка {line}: «}}» внутри незакрытой «(»")
                paren = 0
            if depth < 0:
                errs.append(f"строка {line}: лишняя «}}»")
                depth = 0
        elif ch == "(":
            paren += 1
        elif ch == ")":
            paren -= 1
            if paren < 0:
                errs.append(f"строка {line}: лишняя «)»")
                paren = 0
    if depth:
        errs.append(f"в конце файла не закрыто «{{»: {depth}")
    return errs


KEYWORDS = {"linear", "ease", "ease-in", "ease-out", "ease-in-out", "infinite", "alternate",
            "alternate-reverse", "reverse", "normal", "forwards", "backwards", "both", "none",
            "running", "paused", "step-start", "step-end", "initial", "inherit", "unset",
            "revert", "jump-start", "jump-end", "jump-none", "jump-both", "start", "end"}


def animation_names(css: str) -> set[tuple[str, int]]:
    out = set()
    for m in re.finditer(r"(?<![-\w])animation(-name)?\s*:\s*([^;{}]+)", css):
        line = css.count("\n", 0, m.start()) + 1
        value = re.sub(r"\b[\w-]+\([^()]*\)", " ", m.group(2))       # var(), cubic-bezier()
        value = value.replace("!important", " ")
        for part in value.split(","):
            for tok in part.split():
                if tok in KEYWORDS or re.match(r"^-?[\d.]+(m?s)?$", tok):
                    continue
                if re.match(r"^-?[A-Za-z_][\w-]*$", tok):
                    out.add((tok, line))
    return out


def run():
    for name in ("web/app.css",):
        with open(os.path.join(HERE, name), encoding="utf-8") as f:
            raw = f.read()
        css = strip_comments(raw)
        print(f"\n== {name} ==")
        errs = balance(css)
        check("скобки сходятся, лишних нет", not errs, errs[:5])
        frames = set(re.findall(r"@keyframes\s+([\w-]+)", css))
        missing = sorted({(n, ln) for n, ln in animation_names(css) if n not in frames},
                         key=lambda x: x[1])
        check("у каждой анимации есть @keyframes", not missing, missing[:8])

        print("\n== Хэллоуин: летучие мыши ==")
        rule = re.search(r"(?m)^\.letuchka\{([^}]*)\}", css)
        body = rule.group(1) if rule else ""
        check("правило .letuchka на месте", rule is not None)
        check(".letuchka — position:absolute", "position:absolute" in body, body)
        check(".letuchka — анимация polyot", re.search(r"animation:polyot\b", body) is not None,
              body)
        check("@keyframes polyot на месте", "polyot" in frames)
        check("мыши с лунным контуром на тёмных темах",
              'html[data-holiday="halloween"]:not([data-theme="bumaga"]) .letuchka svg{' in css)
        check("нет обрывков вне правил (строка, начатая с пробела и «color-mix»)",
              re.search(r"(?m)^ color-mix\(", css) is None)
        rm = css[css.index("@media (prefers-reduced-motion: reduce)"):]
        rm = rm[:rm.index("\n}\n")]
        check("«меньше движения»: мыши стоят и расставлены по --stoyat/--stoyat-x",
              ".snezh, .lepestok, .letuchka {\n    animation: none;\n    top: var(--stoyat, 30%);" in rm
              and ".letuchka { left: var(--stoyat-x, 30%); }" in rm)

    print()
    if FAILS:
        print("ПРОВАЛЫ:")
        for f in FAILS:
            print("  - " + f)
        sys.exit(1)
    print("Все проверки прохода 28 прошли.")


if __name__ == "__main__":
    run()
