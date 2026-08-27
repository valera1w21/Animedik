"""Letters about new episodes.

The template is laid out with tables and inline styles. It looks like
markup from the 2000s, and it is: in twenty years mail clients have not
learned to show modern CSS properly. Gmail strips <style> out of <head>,
Outlook draws with the Word engine and knows neither flex nor grid.
Tables with attributes are the only thing that looks the same everywhere.

Sending is switched on by the SMTP_* environment variables. If they are
not set, letters simply do not go out, and that is not an error: the
site works without mail.
"""

from __future__ import annotations

import html
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("anime.mail")

SITE_NAME = "анимеДик"
SITE_URL = os.getenv("SITE_URL", "").rstrip("/")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587") or 587)
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER)


def enabled() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


# --------------------------------------------------------------------------
# Template
# --------------------------------------------------------------------------
# The colours are the site's own: a letter should look like its
# continuation, not like a message from an unrelated service.
BG = "#12141A"
CARD = "#191C24"
LINE = "#262A34"
TEXT = "#ECEEF3"
DIM = "#9AA3B2"
MINT = "#84CBB6"
SKY = "#8DB5DF"


def _esc(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def episode_html(*, display_name: str, title: str, episode: int,
                 season: str = "", released: str = "", about: str = "",
                 poster: str = "", watch_url: str = "") -> str:
    """The "a new episode is out" letter."""
    name = _esc(display_name or "")
    title_e = _esc(title)
    about_e = _esc(about)
    poster_e = _esc(poster)
    watch = _esc(watch_url or SITE_URL or "#")

    season_row = ""
    if season:
        season_row = f"""
          <tr>
            <td style="padding:2px 0;color:{DIM};font-size:14px;">Сезон</td>
            <td style="padding:2px 0;color:{TEXT};font-size:14px;text-align:right;font-weight:700;">{_esc(season)}</td>
          </tr>"""

    released_row = ""
    if released:
        released_row = f"""
          <tr>
            <td style="padding:2px 0;color:{DIM};font-size:14px;">Вышла</td>
            <td style="padding:2px 0;color:{TEXT};font-size:14px;text-align:right;font-weight:700;">{_esc(released)}</td>
          </tr>"""

    poster_block = ""
    if poster:
        poster_block = f"""
              <td width="104" valign="top" style="padding-right:18px;">
                <img src="{poster_e}" width="104" alt=""
                     style="display:block;width:104px;border-radius:10px;border:1px solid {LINE};">
              </td>"""

    about_block = ""
    if about:
        about_block = f"""
          <tr><td style="padding-top:16px;border-top:1px solid {LINE};">
            <div style="color:{DIM};font-size:12px;letter-spacing:.08em;text-transform:uppercase;padding-bottom:6px;">
              О чём это &middot; без спойлеров
            </div>
            <div style="color:{TEXT};font-size:14px;line-height:1.6;">{about_e}</div>
          </td></tr>"""

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title_e} — новая серия</title>
</head>
<body style="margin:0;padding:0;background:{BG};">
<!-- Скрытая строка: её показывают почтовые клиенты рядом с темой письма -->
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">
  {title_e} — серия {episode} уже доступна
</div>

<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       style="background:{BG};padding:32px 16px;">
<tr><td align="center">

  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
         style="max-width:560px;background:{CARD};border:1px solid {LINE};border-radius:16px;">

    <!-- шапка -->
    <tr><td style="padding:26px 28px 20px;border-bottom:1px solid {LINE};">
      <table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
        <td style="padding-right:10px;">
          <div style="width:30px;height:30px;border-radius:9px;
                      background:linear-gradient(135deg,{MINT},{SKY});"></div>
        </td>
        <td style="font-family:'Nunito',Arial,sans-serif;font-size:19px;font-weight:800;color:{TEXT};">
          аниме<span style="color:{MINT};">Дик</span>
        </td>
      </tr></table>
    </td></tr>

    <!-- главное -->
    <tr><td style="padding:26px 28px 4px;font-family:'Nunito',Arial,sans-serif;">
      <div style="color:{MINT};font-size:12px;font-weight:700;letter-spacing:.1em;
                  text-transform:uppercase;padding-bottom:8px;">Новая серия</div>
      <div style="color:{TEXT};font-size:23px;font-weight:800;line-height:1.25;">{title_e}</div>
    </td></tr>

    <tr><td style="padding:18px 28px 0;font-family:'Nunito',Arial,sans-serif;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
        {poster_block}
        <td valign="top">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
            <tr>
              <td style="padding:2px 0;color:{DIM};font-size:14px;">Серия</td>
              <td style="padding:2px 0;color:{MINT};font-size:20px;text-align:right;font-weight:800;">{int(episode)}</td>
            </tr>{season_row}{released_row}
          </table>
        </td>
      </tr></table>
    </td></tr>

    <tr><td style="padding:20px 28px 0;font-family:'Nunito',Arial,sans-serif;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">
        {about_block}
      </table>
    </td></tr>

    <!-- кнопка -->
    <tr><td align="center" style="padding:24px 28px 28px;">
      <a href="{watch}"
         style="display:inline-block;background:{MINT};color:#16201D;text-decoration:none;
                font-family:'Nunito',Arial,sans-serif;font-size:15px;font-weight:800;
                padding:13px 34px;border-radius:999px;">Смотреть</a>
    </td></tr>

    <!-- подвал -->
    <tr><td style="padding:18px 28px 24px;border-top:1px solid {LINE};
                   font-family:'Nunito',Arial,sans-serif;font-size:12.5px;color:{DIM};line-height:1.6;">
      Письмо пришло, потому что {name or "вы"} включили уведомления о новых сериях
      в настройках {SITE_NAME}. Отключить их можно там же, в разделе «Уведомления».
    </td></tr>

  </table>

</td></tr>
</table>
</body>
</html>"""


def episode_text(*, title: str, episode: int, season: str = "",
                 released: str = "", about: str = "", watch_url: str = "") -> str:
    """The same thing as plain text.

    Some people read mail without pictures and markup, and spam filters
    treat a letter with no text part as suspicious. We write both.
    """
    lines = [f"{SITE_NAME} — новая серия", "", title, f"Серия: {episode}"]
    if season:
        lines.append(f"Сезон: {season}")
    if released:
        lines.append(f"Вышла: {released}")
    if about:
        lines += ["", "О чём это (без спойлеров):", about]
    if watch_url or SITE_URL:
        lines += ["", f"Смотреть: {watch_url or SITE_URL}"]
    lines += ["", "Отключить эти письма можно в настройках, раздел «Уведомления»."]
    return "\n".join(lines)


def send_episode(to: str, **kw) -> bool:
    """Sends the letter. With no SMTP settings it simply does nothing."""
    if not enabled() or not to:
        return False
    msg = EmailMessage()
    msg["Subject"] = f"{kw.get('title', 'Аниме')} — серия {kw.get('episode', '')}"
    msg["From"] = SMTP_FROM
    msg["To"] = to
    msg.set_content(episode_text(**{k: v for k, v in kw.items() if k != "display_name"}))
    msg.add_alternative(episode_html(**kw), subtype="html")
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            s.starttls(context=ssl.create_default_context())
            if SMTP_USER:
                s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        return True
    except Exception as exc:                         # noqa: BLE001
        log.warning("письмо не ушло: %s: %s", type(exc).__name__, exc)
        return False
