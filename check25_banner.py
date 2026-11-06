"""Проход 25: баннер профиля.

Рядом с фото в «Настройки → Профиль» можно поставить баннер — картинку в
шапке карточки профиля и настроек. Устроен как аватар, и проверки те же:

* картинка принимается и пересобирается сервером в чистый JPEG 3:1 (не
  больше 960×320); маленькая не растягивается;
* не картинка (html, php, подделанный заголовок), слишком большой файл и
  «бомба» (огромный холст в крошечном файле) — отказ;
* гостю, без входа и без метки формы — нельзя;
* пустое тело — убрать баннер; после этого его нет;
* баннер отдаётся только самому хозяину (адрес — /api/me/banner, как у
  аватара): другой вошедший получает свой или ничего, гость — ничего;
* отдаётся с теми же заголовками, что аватар: private-кэш, nosniff;
* удалили аккаунт — баннер ушёл вместе с ним;
* страница: кнопки «Выбрать файл» / «Убрать баннер», баннер в шапках,
  и цвет кружка выключается, когда стоит фото.

Сети нет: всё внутри TestClient.
"""
import io
import logging
import os
import sys
import tempfile

os.environ["MODE"] = "private"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "p25.db")
os.environ["COOKIE_SECURE"] = "0"
os.environ.setdefault("SESSION_PEPPER", "pass25")
logging.getLogger("httpx").setLevel(logging.ERROR)
logging.getLogger("anime").setLevel(logging.CRITICAL)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from fastapi.testclient import TestClient          # noqa: E402
from PIL import Image                              # noqa: E402
from api import main, store                        # noqa: E402
import fake_sources                                 # noqa: E402
fake_sources.install()

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

FAILS = []
G = [""]
PASS = "Long-Passw0rd-125"


def group(n):
    G[0] = n
    print("\n== " + n + " ==")


def check(name, ok, extra=""):
    print(("  OK   " if ok else "  ПРОВАЛ ") + name + (("  -> " + str(extra)) if extra else ""))
    if not ok:
        FAILS.append(G[0] + " / " + name)
    return ok


def reset():
    for lim in (main.login_guard, main.api_limit, main.guest_limit, main.write_limit,
                main.avatar_limit):
        lim.reset()


def login(name):
    reset()
    c = TestClient(main.app)
    r = c.post("/api/auth/login", json={"login": name, "password": PASS})
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return c


def guest():
    reset()
    g = TestClient(main.app)
    r = g.post("/api/auth/guest", json={})
    g.headers["X-CSRF-Token"] = r.json()["csrf"]
    return g


def png(w, h, color=(60, 90, 110)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def run():
    store.init()
    store.create_user("hozyain", PASS, "admin", "Хозяин")
    store.create_user("drug", PASS, "user", "Друг")
    store.create_user("ushyol", PASS, "user", "Ушедший")

    # ==================================================================
    group("1. Загрузка")
    v = login("hozyain")
    me = v.get("/api/me").json()
    check("сначала баннера нет", me.get("banner_at") == 0, me.get("banner_at"))
    check("GET без баннера — 404", v.get("/api/me/banner").status_code == 404)
    r = v.post("/api/me/banner", content=png(1600, 900, (200, 40, 40)))
    check("картинка принята", r.status_code == 200, r.text[:200])
    check("в ответе — когда поставлен", (r.json().get("banner_at") or 0) > 0, r.json())
    me = v.get("/api/me").json()
    check("в /api/me баннер виден (banner_at)", me.get("banner_at", 0) > 0, me.get("banner_at"))
    got = v.get("/api/me/banner")
    check("баннер отдаётся", got.status_code == 200, got.status_code)
    check("отдаётся как jpeg", got.headers.get("content-type") == "image/jpeg",
          got.headers.get("content-type"))
    check("угадывание типа запрещено", got.headers.get("x-content-type-options") == "nosniff")
    # Как у аватара: общий слой ответов ставит всему /api/ «no-store»;
    # главное — никакого общего (public) кэша для чужих глаз.
    cc = got.headers.get("cache-control", "")
    check("кэш не общий (как у аватара)", cc in ("no-store", "private, max-age=60") and "public" not in cc, cc)
    img = Image.open(io.BytesIO(got.content))
    check("пересобран в JPEG", img.format == "JPEG", img.format)
    check("приведён к 960×320", img.size == (960, 320), img.size)
    check("весит немного", len(got.content) < 120 * 1024, len(got.content))
    check("цвет картинки сохранился (это та самая)",
          abs(img.getpixel((480, 160))[0] - 200) < 12, img.getpixel((480, 160)))

    r = v.post("/api/me/banner", content=png(300, 600, (20, 160, 60)))
    check("высокая картинка — тоже принята", r.status_code == 200, r.status_code)
    img = Image.open(io.BytesIO(v.get("/api/me/banner").content))
    check("обрезана по центру до 3:1, не растянута", img.size == (300, 100), img.size)
    r = v.post("/api/me/banner", content=png(60, 60, (240, 200, 40)))
    check("крошечная 60×60 — принята", r.status_code == 200, r.status_code)
    img = Image.open(io.BytesIO(v.get("/api/me/banner").content))
    check("маленькая не растягивается", img.size == (60, 20), img.size)

    # ==================================================================
    group("2. Не картинка и слишком большое")
    reset()
    check("html вместо картинки — 400",
          v.post("/api/me/banner", content=b"<script>alert(1)</script>").status_code == 400)
    check("php вместо картинки — 400",
          v.post("/api/me/banner", content=b"<?php system($_GET[0]); ?>").status_code == 400)
    check("подделанный заголовок — 400",
          v.post("/api/me/banner", content=b"GIF89a<script>alert(1)</script>").status_code == 400)
    gif = io.BytesIO()
    Image.new("RGB", (300, 100), (1, 2, 3)).save(gif, format="GIF")
    check("GIF не принимается (только JPG, PNG, WebP)",
          v.post("/api/me/banner", content=gif.getvalue()).status_code == 400)
    reset()
    check("слишком большой файл — 413",
          v.post("/api/me/banner", content=b"\x00" * (main.BANNER_MAX_BYTES + 1)).status_code == 413)
    bomb = png(5000, 5000, (10, 20, 30))
    check("бомба помещается в предел по весу", len(bomb) < main.BANNER_MAX_BYTES, len(bomb))
    check("но по размеру холста отклонена",
          v.post("/api/me/banner", content=bomb).status_code == 400)
    img = Image.open(io.BytesIO(v.get("/api/me/banner").content))
    check("после отказов остался прежний баннер", img.size == (60, 20), img.size)

    # ==================================================================
    group("3. Кому нельзя")
    anon = TestClient(main.app)
    check("без входа — 401", anon.post("/api/me/banner", content=png(300, 100)).status_code == 401)
    check("без входа GET — 401", anon.get("/api/me/banner").status_code == 401)
    g = guest()
    check("гостю — 403", g.post("/api/me/banner", content=png(300, 100)).status_code == 403)
    check("гостю GET — 404", g.get("/api/me/banner").status_code == 404)
    check("у гостя banner_at = 0", g.get("/api/me").json().get("banner_at") == 0)
    f = login("drug")
    del f.headers["X-CSRF-Token"]
    check("без метки формы — 403", f.post("/api/me/banner", content=png(300, 100)).status_code == 403)
    f.headers["X-CSRF-Token"] = "not-the-right-token"
    check("с чужой меткой — 403", f.post("/api/me/banner", content=png(300, 100)).status_code == 403)

    # ==================================================================
    group("4. Чужой баннер не отдаётся")
    f = login("drug")
    check("у другого своего нет — 404, а не чужой", f.get("/api/me/banner").status_code == 404)
    check("у другого banner_at = 0", f.get("/api/me").json().get("banner_at") == 0)
    f.post("/api/me/banner", content=png(900, 300, (30, 30, 200)))
    mine = Image.open(io.BytesIO(f.get("/api/me/banner").content))
    v = login("hozyain")
    his = Image.open(io.BytesIO(v.get("/api/me/banner").content))
    check("баннеры не перепутаны", mine.size != his.size and mine.getpixel((5, 5)) != his.getpixel((5, 5)),
          (mine.size, his.size))
    routes = [r.path for r in main.app.routes if "banner" in getattr(r, "path", "")]
    check("адресов с номером чужого баннера нет", all("{" not in p for p in routes), routes)

    # ==================================================================
    group("5. Убрать")
    reset()
    r = v.post("/api/me/banner", content=b"")
    check("пустое тело — убрать", r.status_code == 200 and r.json().get("removed") is True, r.text[:200])
    check("после этого — 404", v.get("/api/me/banner").status_code == 404)
    check("и banner_at = 0", v.get("/api/me").json().get("banner_at") == 0)
    f = login("drug")
    check("у другого его баннер на месте", f.get("/api/me/banner").status_code == 200)

    # ==================================================================
    group("6. Удалили аккаунт — баннер ушёл")
    u = login("ushyol")
    u.post("/api/me/banner", content=png(600, 200))
    uid = store.get_user_by_login("ushyol")["id"]
    check("баннер был", store.get_banner(uid) is not None)
    store.delete_user(uid)
    left = store.connect().execute("SELECT COUNT(*) AS n FROM banners WHERE user_id = ?", (uid,)).fetchone()
    check("и ушёл вместе с аккаунтом", left["n"] == 0, left["n"])

    # ==================================================================
    group("7. Частота")
    v = login("hozyain")
    main.avatar_limit.reset()
    codes = [v.post("/api/me/banner", content=png(120, 40)).status_code for _ in range(14)]
    check("частая загрузка ограничена", 429 in codes, codes)

    # ==================================================================
    group("8. Страница")
    html = open(os.path.join(HERE, "web", "index.html"), encoding="utf-8").read()
    js = open(os.path.join(HERE, "web", "index.js"), encoding="utf-8").read()
    css = open(os.path.join(HERE, "web", "app.css"), encoding="utf-8").read()
    check("в «Профиле» — выбор баннера и «Убрать баннер»",
          'id="s-banner"' in html and 'id="s-banner-btn"' in html and 'id="s-banner-clear"' in html
          and "Убрать баннер" in html and "Remove banner" in html)
    check("баннер — в шапке карточки и настроек", html.count('class="ucban"') == 2)
    check("страница шлёт баннер на /api/me/banner",
          "A.api.raw('/api/me/banner', blob)" in js and "A.api.raw('/api/me/banner', new Blob([]))" in js)
    check("затемнение под именем", ".ucban::after" in css and ".has-ban .ucban{display:block}" in css)
    check("цвет кружка при фото выключен и подписан",
          "Цвет не нужен — стоит фото" in html and 'id="s-ava-note"' in html
          and "function paintAvaLock(" in js and "b.disabled = guest || photo" in js)

    print("\n" + "=" * 60)
    if FAILS:
        print("ПРОВАЛЫ:")
        for x in FAILS:
            print("   - " + x)
        return 1
    print("ПРОХОД 25 — все проверки пройдены.")
    return 0


if __name__ == "__main__":
    sys.exit(run())
