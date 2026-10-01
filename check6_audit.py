"""Проверки на то, что было найдено при разборе кода.

Отдельный набор. Смысл в том, что первые пять писались вместе с кодом
и проверяли ровно то, о чём автор уже подумал. Здесь — то, о чём не подумал:
каждая проверка ниже на прежней версии проваливалась.

Запуск:  python check6_audit.py
"""
import os
import re
import sys
import tempfile
import time

# Проверки публичной версии идут в демонстрационном режиме: другого здесь
# нет. Внешние источники видео не входят в этот репозиторий, и
# единственный рабочий источник — свободное видео (api/anime_demo.py).
os.environ.setdefault("MODE", "demo")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "audit.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ["SESSION_PEPPER"] = "audit-pepper"
os.environ["TRUST_PROXY"] = "1"          # как в docker-compose

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import logging                                    # noqa: E402
logging.getLogger("anime").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)

from fastapi.testclient import TestClient          # noqa: E402
from api import anime, main, security, store       # noqa: E402

# Вывод здесь на русском, а консоль Windows по умолчанию живёт в cp1251:
# первая же стрелка или галочка роняла весь запуск с UnicodeEncodeError,
# и проверки обрывались на середине, не дойдя до сути. Просим поток
# работать в utf-8; там, где он и так utf-8, строка ничего не меняет.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
GROUP = [""]
PASS = "SchastlivyiParol-2026"


def group(name):
    GROUP[0] = name
    print("\n== " + name + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(GROUP[0] + " / " + name)
    return ok


def reset():
    main.login_guard.reset()
    for lim in (main.api_limit, main.search_limit, main.guest_limit, main.write_limit):
        lim.reset()
    main.guests._items.clear()


def login(c, user=None, pwd=None):
    r = c.post("/api/auth/login", json={"login": user or "valera", "password": pwd or PASS})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def without_comments(src):
    """Код без комментариев.

    Нужно для проверок вида «такого-то куска больше нет». Рядом с каждым
    исправлением стоит комментарий, объясняющий ошибку, и он обязан
    называть её по имени — иначе объяснение бесполезно. Проверка,
    срабатывающая на такое объяснение, заставляет его удалить: так
    теряются ровно те комментарии, ради которых всё и писалось.
    """
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    return re.sub(r"(?m)//.*$", " ", src)


def run():
    store.init()
    for name, role in (("valera", "admin"), ("misha", "user")):
        if store.get_user_by_login(name) is None:
            store.create_user(name, PASS, role)

    # ==================================================================
    group("Подделка адреса больше не обходит ограничения")
    # Так выглядит запрос после нашего nginx: X-Real-IP он ЗАМЕНЯЕТ целиком,
    # а X-Forwarded-For по умолчанию ДОПИСЫВАЕТ — то есть то, что придумал
    # посетитель, остаётся в цепочке первым. Настоящий адрес один и тот же.
    def through_nginx(spoof, real="198.51.100.9"):
        return {"X-Forwarded-For": f"{spoof}, {real}", "X-Real-IP": real}

    reset()
    c = TestClient(main.app)
    codes = {}
    for i in range(120):
        r = c.post("/api/auth/login",
                   json={"login": f"nosuch{i:04d}", "password": "Password12345"},
                   headers=through_nginx(f"10.1.{i // 256}.{i % 256}"))
        codes[r.status_code] = codes.get(r.status_code, 0) + 1
    # На прежней версии здесь было 120 ответов 401 и ни одной блокировки:
    # каждый запрос выглядел как новый посетитель.
    check("распыление пароля упирается в блокировку", codes.get(429, 0) > 0, codes)
    check("прошло не больше потолка по адресу",
          codes.get(401, 0) <= main.login_guard.MAX_PER_IP + 2, codes)

    reset()
    c = TestClient(main.app)
    given = 0
    for i in range(40):
        r = c.post("/api/auth/guest",
                   headers=through_nginx(f"172.16.{i // 256}.{i % 256}"))
        c.cookies.clear()
        if r.status_code == 200:
            given += 1
    # Прежде выдавалось все сорок при лимите шесть.
    check("гостевые пропуска не выдаются сверх лимита", given <= 8, given)

    reset()
    c = TestClient(main.app)
    r = c.post("/api/auth/guest",
               headers={"X-Forwarded-For": "not-an-address, 198.51.100.9",
                        "X-Real-IP": "198.51.100.9"})
    check("мусор в цепочке не мешает найти настоящий адрес",
          r.status_code == 200, r.status_code)
    r = c.post("/api/auth/guest", headers={"X-Real-IP": "sdelay-mne-krasivo"})
    c.cookies.clear()
    check("мусор вместо адреса не роняет сервер",
          r.status_code in (200, 429, 503), r.status_code)

    # Настройка nginx — часть той же защиты, проверяем и её.
    conf = open("deploy/nginx-anime.conf", encoding="utf-8").read()
    check("nginx перезаписывает X-Forwarded-For",
          "proxy_set_header X-Forwarded-For $remote_addr;" in conf)
    check("перезапись стоит после include proxy_params",
          conf.index("include /etc/nginx/proxy_params;")
          < conf.index("proxy_set_header X-Forwarded-For $remote_addr;"))

    # ==================================================================
    group("Метка формы привязана к сессии")
    reset()
    c = TestClient(main.app)
    login(c)
    real = c.cookies.get("csrf")
    check("метка выдана", bool(real))
    check("метка считается из токена сессии",
          real == security.csrf_for(c.cookies.get("sid")))

    # Подставляем свою метку сразу в куку и в заголовок — так выглядела бы
    # атака через навязанную куку. Прежняя схема такое пропускала.
    forged = "a" * 32
    c.cookies.set("csrf", forged)
    r = c.post("/api/me/profile", json={"display_name": "взломано"},
               headers={"X-CSRF-Token": forged})
    check("навязанная кука не проходит", r.status_code == 403, r.status_code)

    c.cookies.set("csrf", real)
    r = c.post("/api/me/profile", json={"display_name": "Валера"},
               headers={"X-CSRF-Token": real})
    check("настоящая метка проходит", r.status_code == 200, r.status_code)

    # ==================================================================
    group("Поиск: пусто и отказ — это разные вещи")
    reset()
    c = TestClient(main.app)
    login(c)
    saved = main.try_source

    async def nothing(source, q):
        return []

    async def silence(source, q):
        return None

    main.try_source = nothing
    r = c.get("/api/search?q=такогоаниместочнонет")
    check("ничего не нашлось -> 200 и пустой список",
          r.status_code == 200 and r.json()["items"] == [], r.status_code)

    reset()
    main.try_source = silence
    r = c.get("/api/search?q=такогоаниместочнонет")
    check("все источники молчат -> 502", r.status_code == 502, r.status_code)
    main.try_source = saved

    # ==================================================================
    group("Настройки: только те значения, что есть в интерфейсе")
    reset()
    c = TestClient(main.app)
    login(c)
    # Настройка «размер обложек» убрана из кабинета целиком: её выставляют
    # один раз и больше не трогают. Поле удалено и из модели, поэтому
    # проверяем не отказ, а то, что оно не оседает в базе.
    c.post("/api/me/settings", json={"card_size": 200})
    check("удалённый размер обложек не сохраняется",
          "card_size" not in c.get("/api/me").json()["settings"])
    r = c.post("/api/me/settings", json={"logo": 9})
    # 400 от белого списка или 422 от разбора запроса — важно, что не 200
    check("несуществующий логотип отклонён", r.status_code in (400, 422), r.status_code)

    # Настройки должны переживать запись и чтение целиком.
    store.set_settings(1, {"lang": "en", "accent": "sky"})
    check("настройки читаются обратно", store.get_settings(1).get("accent") == "sky")

    # ==================================================================
    group("Смена пароля")
    reset()
    c = TestClient(main.app)
    login(c, "misha")
    r = c.post("/api/me/password", json={"current": PASS, "new": PASS})
    check("новый пароль не может совпадать со старым", r.status_code == 400, r.status_code)
    r = c.post("/api/me/password", json={"current": PASS, "new": "Drugoi-Parol-2026"})
    check("другой пароль принимается", r.status_code == 200, r.status_code)
    store.set_password(store.get_user_by_login("misha")["id"], PASS)

    # ==================================================================
    group("Статус в /watched проверяется")
    reset()
    c = TestClient(main.app)
    login(c)
    body = {"key": "x:1", "title": "Проверка", "watched_ep": 1, "status": "выдуманный"}
    r = c.post("/api/library/watched", json=body)
    check("неизвестный статус отклонён", r.status_code == 400, r.status_code)
    body["status"] = "watching"
    r = c.post("/api/library/watched", json=body)
    check("известный статус принят", r.status_code == 200, r.status_code)

    # ==================================================================
    group("Сессии не копятся бесконечно")
    reset()
    uid = store.get_user_by_login("misha")["id"]
    store.drop_all_sessions(uid)
    for i in range(store.MAX_SESSIONS_PER_USER + 15):
        store.create_session(uid, security.new_token())
    n = store.count_sessions(uid)
    check("держим не больше потолка", n == store.MAX_SESSIONS_PER_USER, n)
    store.drop_all_sessions(uid)

    # ==================================================================
    group("Кэш источников используется по назначению")
    anime._cache.clear()

    class FakeAnime:
        pass

    class FakeItem:
        def __init__(self):
            self.calls = 0

        async def a_get_anime(self):
            self.calls += 1
            return FakeAnime()

    item = FakeItem()
    anime.cache_put("raw:source-a:777", item)

    import asyncio
    loop = asyncio.new_event_loop()
    got = loop.run_until_complete(
        anime.find_anime("source-a", "777", ""))
    # Прежде заготовка из поиска не читалась никогда, и без title сюда
    # прилетал бы отказ 409.
    check("заготовка из поиска разворачивается без похода в сеть",
          isinstance(got, FakeAnime) and item.calls == 1, item.calls)
    got2 = loop.run_until_complete(
        anime.find_anime("source-a", "777", ""))
    check("второй раз берётся из кэша", got2 is got and item.calls == 1, item.calls)
    loop.close()

    # ==================================================================
    group("Разметка страниц собрана верно")
    from html.parser import HTMLParser
    void = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
            "meta", "param", "source", "track", "wbr",
            "path", "circle", "rect", "ellipse", "stop", "use"}

    class Nest(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack = []
            self.errs = []

        def handle_starttag(self, tag, attrs):
            if tag not in void:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if tag in void:
                return
            if not self.stack:
                self.errs.append("лишний </%s>" % tag)
            elif self.stack[-1] == tag:
                self.stack.pop()
            elif tag in self.stack:
                self.stack = self.stack[: self.stack.index(tag)]
            else:
                self.errs.append("лишний </%s>" % tag)

    for page in ("index.html", "watch.html", "stats.html"):
        p = Nest()
        with open(os.path.join("web", page), encoding="utf-8") as fh:
            p.feed(fh.read())
        # В watch.html был лишний </div>, из-за которого правая колонка
        # вываливалась из сетки страницы.
        check(f"{page}: теги закрыты правильно",
              not p.errs and not p.stack, p.errs or p.stack)

    # ==================================================================
    group("Интерфейс покрывает то, что обещает")
    js = open("web/index.js", encoding="utf-8").read()
    watch = open("web/watch.js", encoding="utf-8").read()
    check("в админке есть выключение", "/disable" in js)
    check("в админке есть включение", "/enable" in js)
    check("в админке есть удаление", "api.del('/api/admin/users/" in js)
    check("настройка автоперехода читается на просмотре", "autonext" in watch)
    # Автоотметка на 90% теперь работает всегда, без настройки: выключать
    # её незачем, а лишняя настройка копит код и место в базе.
    check("автоотметка на 90% работает", "0.9" in watch and "autoMarkEnabled" in watch)
    index_html = open("web/index.html", encoding="utf-8").read()
    check("настройки автоотметки в кабинете не осталось",
          'data-k="automark"' not in index_html)
    check("сохранение при уходе со страницы переживает закрытие вкладки",
          "keepalive" in watch)

    # ==================================================================
    group("Мёртвого кода не осталось")
    app_js = open("web/app.js", encoding="utf-8").read()
    check("функции esc больше нет", "function esc(" not in app_js)
    main_py = open("api/main.py", encoding="utf-8").read()
    for dead in ("import json", "import secrets", "Cookie,", "Header,"):
        check(f"нет неиспользуемого: {dead}", dead not in main_py)
    check("устаревших on_event не осталось", "@app.on_event" not in main_py)

    # ==================================================================
    group("«Кто я» заполняется сразу после входа")
    # Найдено живым прогоном в браузере. index.js после входа звал
    # showApp(r.me) напрямую, а модульная переменная me в app.js
    # оставалась пустой до перезагрузки страницы. Последствия тихие:
    # админ видел «Выключить» и «Удалить» на своей же строке, а
    # сортировка списка откатывалась к умолчанию.
    check("app.js умеет запоминать, кто вошёл", "function setMe(" in app_js)
    check("setMe отдан наружу", "setMe: setMe" in app_js)
    check("вход и гостевой пропуск обновляют «кто я»",
          js.count("A.setMe(r.me)") >= 2, js.count("A.setMe(r.me)"))
    # Ищем именно в коде, а не в тексте: в watch.js рядом с исправлением
    # стоит комментарий, который эту ошибку и объясняет, и он обязан
    # называть её по имени. Проверка, срабатывающая на объяснение
    # исправленной ошибки, заставляет убрать объяснение — так теряются
    # ровно те комментарии, ради которых всё и писалось.
    check("устаревшего window.__sources не осталось в коде",
          "__sources" not in without_comments(watch)
          and "__sources" not in without_comments(js))

    group("Страницы не кэшируются, файлы — с переспросом")
    check("под правило попали и /watch, и /stats",
          '"/", "/watch", "/stats"' in main_py)
    check("для файлов задан переспрос", "no-cache" in main_py)

    group("Найденное в живом браузере")
    css = open("web/app.css", encoding="utf-8").read()
    # .wrap — это и обёртка обложки в карточке, и контейнер страницы итогов.
    # Пока правило было записано как просто `.wrap`, оно било по обоим:
    # обложка сжималась вдвое, под ней оставалось 90px пустоты.
    import re as _re
    bare_wrap = _re.search(r"(?m)^\.wrap\s*\{", css)
    check("правило контейнера страницы не бьёт по карточкам",
          bare_wrap is None, "нашлось голое .wrap{")
    check("контейнер страницы ограничен прямым потомком body",
          "body > .wrap{" in css)

    # Кнопка «убрать из списка»: ручка на сервере была с самого начала,
    # а нажать её было негде.
    check("на карточке есть кнопка удаления", "function removeCard(" in js)
    check("удаление зовёт настоящую ручку", "api.del('/api/library/" in js)
    check("у кнопки есть стиль", ".card .del{" in css)
    check("на сенсорном экране кнопка видна всегда", "@media (hover:none)" in css)

    # Склонения: «1 тайтлов» и «502 серий» вылезали на главной постоянно.
    check("склонение числительных вынесено в общий файл",
          "function plural(" in app_js and "function say(" in app_js)
    check("списки пользуются им", "A.say(" in js)

    # Обложка, если её неоткуда взять
    check("страница просмотра умеет достать обложку", "function ensurePoster(" in watch)

    # Номер серии и их количество — разные величины
    check("номер последней серии считается отдельно", "function lastOrdinal(" in watch)
    check("«из N» больше не берёт длину списка",
          "' из ', ' of ') + st.episodes.length" not in watch)

    # Полный экран: отказ обязан быть обработан
    check("отказ полного экрана перехвачен",
          "attempt.catch(" in watch and "webkitEnterFullscreen" in watch)

    group("Фильтры убраны целиком")
    # Панель отбирала только среди уже сохранённого, а искать новое аниме
    # по жанрам нечем: источники умеют лишь текстовый поиск. Панель,
    # обещающая не то, что делает, убрана — вместе со всеми следами.
    html = index_html
    for след in ('id="filters"', 'id="g-chips"', 'id="y-chips"',
                 'id="f-random"', 'id="f-reset"', 'id="f-count"',
                 'id="btn-filt"', 'id="filt-num"'):
        check(f"в разметке не осталось {след}", след not in html)
    for след in ("var GENRES", "var YEARS", "buildChips", "readFilters",
                 "state.genres", "state.years", "bucketOf"):
        check(f"в скрипте не осталось {след}", след not in js)
    check("стили панели убраны", ".filters{" not in css and ".frow{" not in css)

    group("Рулетка берёт случайное из каталога")
    js_code = without_comments(js)
    check("вынесена в шапку", 'id="btn-roul"' in html and "$('btn-roul')" in js)
    # Раньше она крутила ваш же список — то есть предлагала то, что вы
    # и так однажды выбрали. Теперь берёт случайное аниме из открытого
    # каталога, и запрос идёт через наш сервер, а не прямо из браузера.
    check("спрашивает у сервера, а не крутит свой список",
          "api.get('/api/random')" in js)
    check("барабана и его геометрии не осталось",
          "function drawReel(" not in js and "function offsetFor(" not in js
          and "reelin" not in js_code)
    check("у результата есть обложка", "'cover'" in js and ".pickres .cover{" in css)
    check("показывается описание", "it.about" in js)
    check("есть кнопка поиска у источников",
          'id="roul-watch"' in html and "$('roul-watch')" in js)
    check("кнопка ищет выпавшее", "doSearch(it.title)" in js)

    group("Справочник ходит через свой сервер")
    catalog_py = open("api/catalog.py", encoding="utf-8").read()
    # Прямой запрос из браузера отправлял бы адрес каждого посетителя
    # на чужой сайт при каждом открытии страницы просмотра.
    # По коду, а не по тексту: рядом с исправлением стоит комментарий,
    # который обязан называть каталог по имени.
    check("в браузере нет обращений к чужому каталогу",
          "anilist" not in js_code.lower()
          and "anilist" not in without_comments(watch).lower())
    check("сервер ходит туда сам", "graphql.anilist.co" in catalog_py)
    check("описание чистится от разметки", "def clean_description(" in catalog_py)
    check("и обрезается до завязки", "_SPOILER" in catalog_py)
    check("страница просмотра показывает описание", "/api/about" in watch)

    group("Лишние кнопки под плеером убраны")
    watch_html = open("web/watch.html", encoding="utf-8").read()
    check("кнопок нет в разметке",
          'id="w-mark"' not in watch_html and 'id="w-skip"' not in watch_html)
    check("и обработчиков к ним тоже",
          "w-mark" not in watch and "w-skip" not in watch)

    group("Новости и вход по коду")
    check("кнопка новостей есть и только для админа",
          'id="btn-news"' in html and "$('btn-news').hidden = !isAdmin" in js)
    check("объявление вставляется текстом, а не разметкой",
          "sitenews-text').textContent" in js)
    check("вход по коду: галочка и код после неё",
          'id="s-2fa"' in html and 'id="twofa-box"' in html)
    check("секрет наружу не отдаётся в /api/me",
          "totp_secret" not in open("api/main.py", encoding="utf-8").read()
          .split("def me_payload")[1].split("def ")[0])
    check("выключение спрашивает пароль", "2fa/disable" in js and "password: pass" in js)


    group("Свои окна вместо системных")
    # Системные confirm/prompt/alert браузер вправе не показывать, и после
    # нескольких подряд Chrome прямо предлагает их заблокировать. Из-за
    # этого «Удалить» на карточке, «Удалить» у объявления и выключение
    # входа по коду молча ничего не делали: код доходил до confirm(),
    # получал false и выходил.
    for f in ("app.js", "index.js", "watch.js", "stats.js"):
        code = without_comments(open("web/" + f, encoding="utf-8").read())
        for bad_call in ("confirm(", "prompt(", "alert("):
            # window.confirm = ... в проверках не в счёт, здесь только вызовы
            check(f"{f}: нет системного {bad_call.rstrip('(')}",
                  bad_call not in code.replace("window." + bad_call, ""),
                  bad_call)
    check("свои окна объявлены", "function ask(" in app_js and "function tell(" in app_js)
    check("и отданы наружу", "ask: ask" in app_js and "tell: tell" in app_js)
    check("окно лежит поверх кабинета",
          ".veil.dialog{z-index:400}" in css)
    check("удаление с карточки спрашивает своим окном", "A.ask({" in js)

    group("Прогресс под обложкой, а не на ней")
    check("счётчик серий есть", "'cnum'" in js and ".cnum{" in css)
    check("полоса есть", "'cbar'" in js and ".cbar{" in css)
    # Раньше и то и другое лежало поверх обложки: на светлых кадрах их
    # не было видно. Плашки убраны совсем — если вернутся, тут упадёт.
    check("на обложке ничего не лежит",
          not any(k in css for k in (".card .count{", ".card .line{",
                                     ".card .badge{", ".card .done-mark{")))
    check("строка прогресса прижата к низу карточки",
          "margin:auto 2px 0" in css and "flex-direction:column" in css)
    # «2 / 1» — источник насчитал меньше серий, чем просмотрено.
    check("нестыковка не показывается", "knownTotal" in js)
    check("без известного числа серий полосы нет", "nobar" in js and ".cprog.nobar" in css)

    group("Переключатели вместо галочек")
    check("вход по коду — переключатель",
          'id="s-2fa"' in html and 'class="sw"' in html)
    check("письма — переключатель", 'id="s-mailnew"' in html)
    check("галочек не осталось", 'type="checkbox" id="s-2fa"' not in html
          and 'type="checkbox" id="s-mailnew"' not in html)
    # Общий обработчик .sw переключал состояние вторым разом поверх своего.
    check("общий обработчик берёт только настройки без своего кода",
          "querySelectorAll('.sw[data-k]')" in js)

    group("Справочник отвечает по-русски")
    check("основной справочник русскоязычный", "shikimori.one" in catalog_py)
    check("жанры переводятся, если ответ английский", "GENRE_RU" in catalog_py)
    # Заголовки HTTP однобайтовые: кириллица в них роняет запрос до отправки.
    ua = [l for l in catalog_py.split(chr(10)) if "User-Agent" in l]
    check("в User-Agent нет кириллицы",
          all(all(ord(ch) < 128 for ch in line) for line in ua), ua[:1])
    check("разметка Shikimori вырезается", "_BB_PAIR" in catalog_py)

    group("Загрузка модуля источника обёрнута")
    anime_py = open("api/anime.py", encoding="utf-8").read()
    head = anime_py[anime_py.index("def get_extractor"):]
    head = head[:head.index("# ------")]
    check("import_module не оставлен голым",
          "try:" in head and "upstream_error" in head)

    # ==================================================================
    print("\n" + "=" * 60)
    if FAILS:
        print("ПРОХОД 6 — не прошли: %d" % len(FAILS))
        for f in FAILS:
            print("   •", f)
        return 1
    print("ПРОХОД 6 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
