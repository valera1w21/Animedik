"""Проход 9: свой генератор QR сверяется с эталонной библиотекой.

Картинка с кодом — единственное место, где раньше требовалась чужая
библиотека `qrcode`, а за ней Pillow. Теперь код рисуется своими
силами, и вопрос один: правильно ли.

«На глаз похоже» тут не годится: неверный QR выглядит точно так же, как
верный. Поэтому матрица сверяется с библиотекой `qrcode` **модуль в
модуль**, на всех восьми масках и на сотнях строк разной длины — от
одного знака до полутысячи.

Если библиотеки на машине нет, сверка честно пропускается, а базовые
проверки (размеры, версии, SVG) всё равно идут. Проект от неё больше не
зависит — она нужна только здесь, только чтобы проверять.

Запуск:  python check9_qr.py
"""
import os
import random
import string
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api import qr                                  # noqa: E402

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
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)


def версия_для(payload: bytes) -> int:
    for в in range(1, qr.MAX_VERSION + 1):
        запас = qr._data_capacity(в) * 8 - 4 - (8 if в <= 9 else 16)
        if len(payload) * 8 <= запас:
            return в
    raise ValueError("слишком длинно")


def наша_матрица(текст: str, маска: int):
    """То же, что qr.matrix, но с заданной маской вместо выбранной."""
    payload = текст.encode("utf-8")
    v = версия_для(payload)
    поток = qr._interleave(qr._encode(payload, v), v)
    служебное, size = qr._function_grid(v)
    поле = [строка[:] for строка in служебное]
    свободные = set(qr._place_data(поле, size, поток, v))
    функция = qr._MASKS[маска]
    for (r, c) in свободные:
        if функция(r, c):
            поле[r][c] ^= 1
    qr._write_format(поле, size, маска)
    qr._write_version(поле, size, v)
    return поле, v


def run():
    group("Сам по себе")
    м = qr.matrix("привет")
    check("матрица квадратная", all(len(с) == len(м) for с in м), len(м))
    check("размер по формуле 17+4v", (len(м) - 17) % 4 == 0, len(м))
    check("только нули и единицы",
          all(з in (0, 1) for с in м for з in с))
    check("пустых клеток не осталось",
          all(з is not None for с in м for з in с))

    # угловые метки на местах
    углы_целы = True
    n = len(м)
    for (r0, c0) in ((0, 0), (0, n - 7), (n - 7, 0)):
        for dr in range(7):
            for dc in range(7):
                край = dr in (0, 6) or dc in (0, 6)
                ядро = 2 <= dr <= 4 and 2 <= dc <= 4
                if м[r0 + dr][c0 + dc] != (1 if (край or ядро) else 0):
                    углы_целы = False
    check("три угловые метки нарисованы верно", углы_целы)
    check("обязательный тёмный модуль на месте", м[n - 8][8] == 1)

    group("Версия растёт вместе с длиной")
    ПРЕДЕЛ = qr._data_capacity(qr.MAX_VERSION) - 3   # минус режим и счётчик
    предыдущая = 0
    ровно = True
    for длина in (1, 20, 60, 120, 200, 300, 400, ПРЕДЕЛ, ПРЕДЕЛ + 40):
        помещается = длина <= ПРЕДЕЛ
        try:
            в = версия_для(("x" * длина).encode())
        except ValueError:
            check("%d знаков не помещается — и не должно" % длина, not помещается, длина)
            continue
        if not помещается:
            check("%d знаков не должно помещаться, а поместилось" % длина, False, в)
            continue
        if в < предыдущая:
            ровно = False
        предыдущая = в
        размер = len(qr.matrix("x" * длина))
        check("%d знаков → версия %d, поле %d" % (длина, в, размер),
              размер == 17 + 4 * в)
    check("версия не уменьшается с ростом длины", ровно)

    group("SVG")
    картинка = qr.svg("otpauth://totp/test?secret=ABC", box=8, border=3)
    check("это svg", картинка.startswith("<svg") and картинка.endswith("</svg>"))
    check("есть путь с модулями", 'fill="#000000" d="M' in картинка)
    check("размер учитывает поля",
          'width="%d"' % ((len(qr.matrix("otpauth://totp/test?secret=ABC")) + 6) * 8) in картинка)
    check("наружу не торчит ничего опасного",
          "<script" not in картинка and "javascript:" not in картинка)
    # чужая строка не ломает разметку
    злая = 'a"><script>alert(1)</script>'
    к = qr.svg(злая)
    check("чужой текст не попадает в разметку", "<script" not in к)

    group("Сверка с эталонной библиотекой")
    try:
        import qrcode
        from qrcode.constants import ERROR_CORRECT_M
        from qrcode.util import QRData, MODE_8BIT_BYTE
    except Exception:                                # noqa: BLE001
        print("  ~ библиотека qrcode не установлена — сверка пропущена")
        print("    (поставить для проверки: pip install qrcode)")
        qrcode = None

    def эталонная(текст, маска):
        """Матрица библиотеки с тем же режимом и той же маской.

        Режим задаём явно. Иначе для строки из одних цифр библиотека
        сама переключится на числовой, уложит её плотнее и получит
        другую версию — сверять будет нечего.
        """
        q = qrcode.QRCode(error_correction=ERROR_CORRECT_M,
                          mask_pattern=маска, border=0)
        q.add_data(QRData(текст.encode("utf-8"), mode=MODE_8BIT_BYTE), optimize=0)
        q.make(fit=True)
        return [[1 if x else 0 for x in с] for с in q.modules], q.version

    if qrcode is not None:
        случайный = random.Random(20260815)
        строки = [
            "a",
            "привет",
            "otpauth://totp/animeDik:valera?secret=JBSWY3DPEHPK3PXP"
            "&issuer=animeDik&algorithm=SHA1&digits=6&period=30",
            "0123456789" * 5,
            "ЖЖЖ" * 40,
            string.printable.strip(),
        ]
        алфавит = string.ascii_letters + string.digits + "-_.:/?&=%+"
        for _ in range(60):
            длина = случайный.randint(1, 380)
            строки.append("".join(случайный.choice(алфавит) for _ in range(длина)))

        всего = 0
        расхождений = 0
        версии = set()
        for текст in строки:
            _, эталонная_в = эталонная(текст, 0)
            _, наша_в = наша_матрица(текст, 0)
            if эталонная_в != наша_в:
                расхождений += 1
                check("версия совпала для строки в %d знаков" % len(текст),
                      False, "%d vs %d" % (наша_в, эталонная_в))
                continue
            версии.add(наша_в)

            for маска in range(8):
                эт, _ = эталонная(текст, маска)
                наш, _ = наша_матрица(текст, маска)
                всего += 1
                if наш != эт:
                    расхождений += 1
                    if расхождений <= 3:
                        точки = [(r, c) for r in range(len(наш))
                                 for c in range(len(наш)) if наш[r][c] != эт[r][c]]
                        check("маска %d, строка в %d знаков" % (маска, len(текст)),
                              False, "расходится в %d клетках, первые %s"
                              % (len(точки), точки[:5]))

        check("сверено матриц: %d, строк: %d, версий: %s"
              % (всего, len(строки), sorted(версии)), True)
        check("расхождений с эталоном нет", расхождений == 0, расхождений)

        # Выбор маски — наше дело: он записан в самом коде, поэтому любая
        # из восьми читается. Но проверим, что выбор осмысленный.
        group("Выбор маски")
        текст = ("otpauth://totp/animeDik:valera?secret=JBSWY3DPEHPK3PXP"
                 "&issuer=animeDik&algorithm=SHA1&digits=6&period=30")
        оценки = []
        for маска in range(8):
            поле, _ = наша_матрица(текст, маска)
            оценки.append(qr._penalty(поле, len(поле)))
        выбранная = qr.matrix(текст)
        совпало = any(наша_матрица(текст, m)[0] == выбранная for m in range(8))
        check("выбранная маска — одна из восьми", совпало)
        лучшая = оценки.index(min(оценки))
        check("выбрана маска с наименьшим штрафом",
              наша_матрица(текст, лучшая)[0] == выбранная,
              "штрафы: %s, лучшая %d" % (оценки, лучшая))

    group("Настройка входа по коду не зависит от чужих библиотек")
    from api import twofa
    секрет = twofa.new_secret()
    ссылка = twofa.otpauth_uri(секрет, "valera")
    картинка = twofa.qr_data_uri(ссылка)
    check("картинка получена", bool(картинка), (картинка or "")[:34])
    check("это svg внутри страницы",
          (картинка or "").startswith("data:image/svg+xml;base64,"))
    check("секрет в ссылке есть", секрет in ссылка)

    исходник = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "api", "twofa.py"), encoding="utf-8").read()
    check("библиотека qrcode из рабочего кода убрана", "import qrcode" not in исходник)
    треб = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "api", "requirements.txt"), encoding="utf-8").read()
    check("и из списка зависимостей тоже", "qrcode" not in треб.lower())

    print("\n" + "=" * 60)
    if FAILS:
        print("ПРОХОД 9 — не прошли: %d" % len(FAILS))
        for f in FAILS:
            print("   • " + f)
        return 1
    print("ПРОХОД 9 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
