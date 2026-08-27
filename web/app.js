/* The part shared by every page: talking to the server, language, sign-in, settings.
 *
 * The rule kept everywhere here: nothing foreign is inserted into the page
 * as HTML. Names, titles and addresses arrive from source sites, so text is
 * set through textContent and addresses only after a check.
 */
(function (global) {
  'use strict';

  /* ------------------------------------------------------------------ */
  /* Addresses                                                          */
  /* ------------------------------------------------------------------ */
  /* The esc() function is no longer here. It escaped text for building
     markup out of strings — while all the code has long been assembling
     the page out of nodes through textContent, where escaping is not
     needed at all. Kept "just in case", it was an invitation to glue
     markup by hand one day and decide it was safe. There must be no such
     loophole in this project. */

  /* Only http and https are let through. Cuts off javascript:, data:, blob: */
  function safeUrl(u) {
    if (u == null || u === '') return '';
    try {
      var p = new URL(String(u), location.origin);
      return (p.protocol === 'http:' || p.protocol === 'https:') ? p.href : '';
    } catch (e) { return ''; }
  }

  /* A ready node with text — safer than any string assembly */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = String(text);
    return n;
  }

  /* ------------------------------------------------------------------ */
  /* Talking to the server                                              */
  /* ------------------------------------------------------------------ */
  function csrfToken() {
    var m = document.cookie.match(/(?:^|;\s*)csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  /* How long we wait for an answer. Calls to sources travel through other
     people's sites and can be slow, so the limit is generous — but it
     exists. Without it a hung request would hang forever: the "searching…"
     strip would never change, and on the watch page the spinner would turn
     until a reload. */
  var TIMEOUT_MS = 45000;

  function request(method, path, body, opts) {
    opts = opts || {};
    var headers = { 'Accept': 'application/json' };
    if (method !== 'GET') headers['X-CSRF-Token'] = csrfToken();
    var init = { method: method, headers: headers, credentials: 'same-origin' };
    /* keepalive asks the browser to carry the request through even if the
       tab is already closing. Without it, saving at the moment of leaving
       the page almost always broke off, and the last minute of watching was
       lost. Such a request must not and need not be cut off by a timer:
       the page is gone already. */
    if (opts.keepalive) init.keepalive = true;
    if (body !== undefined && body !== null) {
      if (body instanceof ArrayBuffer || body instanceof Blob) {
        init.body = body;
        headers['Content-Type'] = 'application/octet-stream';
      } else {
        init.body = JSON.stringify(body);
        headers['Content-Type'] = 'application/json';
      }
    }

    var timer = null;
    if (!opts.keepalive && typeof AbortController === 'function') {
      var ctl = new AbortController();
      init.signal = ctl.signal;
      timer = setTimeout(function () { ctl.abort(); }, TIMEOUT_MS);
    }
    function done(v) { if (timer) clearTimeout(timer); return v; }

    return fetch(path, init).then(function (r) {
      if (r.status === 204) { done(); return null; }
      var ct = r.headers.get('content-type') || '';
      var parse = ct.indexOf('application/json') !== -1 ? r.json() : r.text();
      return parse.then(function (data) {
        done();
        if (!r.ok) {
          var msg = (data && data.detail) ? data.detail : ('Ошибка ' + r.status);
          if (typeof msg !== 'string') msg = 'Ошибка ' + r.status;
          var err = new Error(msg);
          err.status = r.status;
          /* A separate sign for "password accepted, the code from the app
             is needed". An ordinary 401 does not tell that apart from a
             wrong password, and the behaviour has to differ. */
          err.needCode = r.headers.get('X-Need-Code') === '1';
          throw err;
        }
        return data;
      }, function (e) {
        /* An answer came, but unfolding it failed — truncated json, an
           error page from a proxy. The message must still be a human one
           rather than "Unexpected end of JSON input". */
        done();
        var err = new Error(r.ok ? 'Сервер ответил непонятно' : ('Ошибка ' + r.status));
        err.status = r.status;
        throw err;
      });
    }, function (e) {
      done();
      var err = new Error(e && e.name === 'AbortError'
        ? 'Сервер долго не отвечает. Попробуйте ещё раз.'
        : 'Нет связи с сервером');
      err.status = 0;
      throw err;
    });
  }

  var api = {
    get:  function (p) { return request('GET', p); },
    post: function (p, b, o) { return request('POST', p, b === undefined ? {} : b, o); },
    del:  function (p) { return request('DELETE', p); },
    raw:  function (p, buf) { return request('POST', p, buf); }
  };

  /* ------------------------------------------------------------------ */
  /* Language                                                           */
  /* ------------------------------------------------------------------ */
  var lang = 'ru';
  var LANG_KEY = 'animedik.lang';

  /* The chosen language is remembered by the browser itself.

     It used to live only in the account settings — that is, a person who
     does not read Russian had first to sign in, then find the account
     page, then find the right line in it. All of that in Russian. Now the
     language switches on any page and before any sign-in, and the choice
     survives a reload. */
  function savedLang() {
    try { return localStorage.getItem(LANG_KEY) || ''; } catch (e) { return ''; }
  }

  function rememberLang(code) {
    try { localStorage.setItem(LANG_KEY, code); } catch (e) { /* переживём */ }
  }

  function applyLang(code) {
    lang = (code === 'en') ? 'en' : 'ru';
    document.documentElement.setAttribute('data-lang', lang);
    document.documentElement.lang = lang;
    document.querySelectorAll('[data-' + lang + ']').forEach(function (n) {
      n.textContent = n.getAttribute('data-' + lang);
    });
    document.querySelectorAll('[data-' + lang + '-ph]').forEach(function (n) {
      n.setAttribute('placeholder', n.getAttribute('data-' + lang + '-ph'));
    });
    document.querySelectorAll('.lang').forEach(function (b) {
      b.classList.toggle('on', b.dataset.setLang === lang);
    });
    rememberLang(lang);
    document.dispatchEvent(new CustomEvent('langchange', { detail: lang }));
  }

  /* One handler for every language button, on every page. It used to live
     in the main page's code, and on the watch page the button would not
     have worked at all. Through delegation — because there are buttons in
     markup that appears later, too. */
  document.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('.lang[data-set-lang]') : null;
    if (!b) return;
    e.preventDefault();
    applyLang(b.dataset.setLang);
  });

  function t(ru, en) { return lang === 'en' ? en : ru; }

  /* The anime's name in the site's language.

     The catalogue gives both: the Russian and the Latin one. While the
     site was Russian only, the Latin one went nowhere — and to a person
     who switched the language "Магическая битва" says nothing, they need
     "Jujutsu Kaisen".

     If there is no Latin one, the Russian stays: showing at least
     something beats an empty line. */
  function animeName(row) {
    if (!row) return '';
    if (lang === 'en' && row.title_en) return row.title_en;
    return row.title || row.title_en || '';
  }

  function langCode() { return lang; }

  /* The default video source for the site's language.

     Sources each speak their own: the Russian ones give Russian dubs, an
     English-language one gives English subtitles and dub. They must not be
     mixed, otherwise a person who opened the site in English gets a
     Russian dub they never asked for. */
  function defaultSource() { return lang === 'en' ? 'source-en' : 'source-a'; }

  /* The kind of title in the site's language: series, film, OVA.

     The server gives both the raw label (`kind`) and the Russian caption
     (`kind_ru`). While the site was Russian the second was enough — while
     in English "сериал" stayed next to "Jujutsu Kaisen". We translate here,
     from the raw label: there is one of those for both languages. */
  var KINDS = {
    tv: ['сериал', 'TV series'], movie: ['фильм', 'movie'],
    ova: ['OVA', 'OVA'], ona: ['ONA', 'ONA'],
    special: ['спешл', 'special'], tv_special: ['спецвыпуск', 'TV special'],
    music: ['клип', 'music video'], pv: ['трейлер', 'trailer'],
    cm: ['реклама', 'commercial']
  };

  function kindName(row) {
    if (!row) return '';
    var pair = KINDS[row.kind];
    if (pair) return lang === 'en' ? pair[1] : pair[0];
    /* An unfamiliar label — we show what the server sent. */
    return row.kind_ru || row.kind || '';
  }

  /* =====================================================================
     Our own dialogs instead of the system confirm / prompt / alert.

     The browser is entitled not to show system dialogs — and does not:
     after several in a row Chrome offers to "block dialogs from this
     page", and in embedded windows and webviews they are muted at once.
     Because of that "Delete" on a card, "Delete" on an announcement and
     switching off sign-in by code simply did nothing: the code reached
     confirm(), got false and quietly left. The button presses, nothing
     happens, and there is nothing to explain it with.

     Our own dialogs are always shown, look like the rest of the site and
     close on Esc.
     ===================================================================== */
  var dialogBox = null;

  function closeDialog(answer, resolve) {
    if (!dialogBox) return;
    dialogBox.remove();
    dialogBox = null;
    document.removeEventListener('keydown', dialogKeys, true);
    if (resolve) resolve(answer);
  }

  var dialogEscape = null;
  function dialogKeys(e) {
    if (e.key === 'Escape' && dialogEscape) { e.preventDefault(); dialogEscape(); }
  }

  /* opts: {title, text, ok, cancel, danger, password} */
  function ask(opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      closeDialog(null, null);

      var veil = el('div', 'veil dialog show');
      var box = el('div', 'dbox');
      box.setAttribute('role', 'dialog');
      box.setAttribute('aria-modal', 'true');

      if (opts.title) box.appendChild(el('h3', null, opts.title));
      if (opts.text) box.appendChild(el('p', null, opts.text));

      var field = null;
      if (opts.password) {
        field = el('input', 'inp');
        field.type = 'password';
        field.autocomplete = 'current-password';
        field.style.width = '100%';
        box.appendChild(field);
      }

      var row = el('div', 'drow');
      var okBtn = el('button', 'wide' + (opts.danger ? ' danger' : ''),
                     opts.ok || t('Да', 'Yes'));
      okBtn.type = 'button';
      var noBtn = el('button', 'wide ghost', opts.cancel || t('Отмена', 'Cancel'));
      noBtn.type = 'button';
      row.appendChild(okBtn);
      row.appendChild(noBtn);
      box.appendChild(row);
      veil.appendChild(box);
      document.body.appendChild(veil);
      dialogBox = veil;

      function done(value) { closeDialog(value, resolve); }
      dialogEscape = function () { done(opts.password ? null : false); };
      document.addEventListener('keydown', dialogKeys, true);

      okBtn.addEventListener('click', function () {
        done(opts.password ? (field.value || '') : true);
      });
      noBtn.addEventListener('click', function () {
        done(opts.password ? null : false);
      });
      veil.addEventListener('click', function (e) {
        if (e.target === veil) done(opts.password ? null : false);
      });
      if (field) {
        field.addEventListener('keydown', function (e) {
          if (e.key === 'Enter') { e.preventDefault(); done(field.value || ''); }
        });
      }
      setTimeout(function () { (field || okBtn).focus(); }, 30);
    });
  }

  /* Just a message with one button — a replacement for alert. */
  function tell(title, text) {
    return new Promise(function (resolve) {
      closeDialog(null, null);
      var veil = el('div', 'veil dialog show');
      var box = el('div', 'dbox');
      box.setAttribute('role', 'dialog');
      box.setAttribute('aria-modal', 'true');
      if (title) box.appendChild(el('h3', null, title));
      if (text) {
        var p = el('p', null, text);
        p.style.whiteSpace = 'pre-wrap';
        box.appendChild(p);
      }
      var row = el('div', 'drow');
      var okBtn = el('button', 'wide', t('Понятно', 'Got it'));
      okBtn.type = 'button';
      row.appendChild(okBtn);
      box.appendChild(row);
      veil.appendChild(box);
      document.body.appendChild(veil);
      dialogBox = veil;

      function done() { closeDialog(null, resolve); }
      dialogEscape = done;
      document.addEventListener('keydown', dialogKeys, true);
      okBtn.addEventListener('click', done);
      veil.addEventListener('click', function (e) { if (e.target === veil) done(); });
      setTimeout(function () { okBtn.focus(); }, 30);
    });
  }

  /* Agreement of numerals.

     Captions used to be glued to the number directly — "1 тайтлов",
     "502 серий", "1 незакрытых". A trifle, but it gives away at once that
     the text was assembled in a hurry, and it catches the eye constantly:
     these counters hang on the main page all the time.

     ru — three forms: 1 тайтл, 2 тайтла, 5 тайтлов.
     en — two: 1 title, 2 titles. */
  function plural(n, ru, en) {
    if (lang === 'en') return en[Math.abs(n) === 1 ? 0 : 1];
    var a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return ru[2];
    if (b > 1 && b < 5) return ru[1];
    if (b === 1) return ru[0];
    return ru[2];
  }

  /* The number together with the right form of the word */
  function count(n, ru, en) { return n + ' ' + plural(n, ru, en); }

  /* Ready-made sets: let the words live in one place rather than ten
     times over in different files, where they easily drift apart. */
  var WORDS = {
    title: [['тайтл', 'тайтла', 'тайтлов'], ['title', 'titles']],
    episode: [['серия', 'серии', 'серий'], ['episode', 'episodes']],
    unfinished: [['незакрытый', 'незакрытых', 'незакрытых'],
                 ['unfinished', 'unfinished']],
    /* A part of a franchise: a season, a film, an OVA or a special. The
       word is needed where calling them "titles" would be wrong — "Наруто"
       is one title and twenty-nine parts. */
    part: [['часть', 'части', 'частей'], ['part', 'parts']]
  };

  function say(n, kind) { return count(n, WORDS[kind][0], WORDS[kind][1]); }

  /* ------------------------------------------------------------------ */
  /* Appearance                                                         */
  /* ------------------------------------------------------------------ */
  function applyLook(s) {
    s = s || {};
    if (s.depth) document.documentElement.setAttribute('data-depth', s.depth);
    if (s.accent) document.documentElement.setAttribute('data-accent', s.accent);
    if (s.card_size) document.documentElement.style.setProperty('--card-min', s.card_size + 'px');
    if (s.logo) {
      document.querySelectorAll('.logo-v').forEach(function (v) {
        v.classList.toggle('on', v.dataset.v === String(s.logo));
      });
    }
    if (s.lang) applyLang(s.lang);
  }

  /* ------------------------------------------------------------------ */
  /* Who am I                                                           */
  /* ------------------------------------------------------------------ */
  var me = null;
  var guestTimer = null;

  /* Remembers who we are. Sign-in and the guest pass answer with exactly
     the same set of fields as /api/me — it is one and the same me_payload
     on the server — so an extra request after signing in is not needed.

     There used to be no such possibility, and index.js called
     showApp(r.me) directly after signing in. The page drew correctly, but
     App.me stayed empty until the first reload. The consequences were
     quiet and varied:
       * in the account list an admin saw "Switch off" and "Delete"
         on their own row — the "this is me" check compares against App.me;
       * the library sorting and "show finished" rolled back to the
         defaults, whatever stood in the account page;
       * the name field on the account page lost its fallback value.
     All of it was cured by reloading the page, which is why it went
     unnoticed. */
  function setMe(data) {
    me = data || null;
    if (me) applyLook(me.settings);
    return me;
  }

  function loadMe() {
    return api.get('/api/me').then(function (data) {
      return setMe(data);
    }).catch(function (e) {
      if (e.status === 401) { me = null; return null; }
      throw e;
    });
  }

  function paintAvatar(node, m) {
    if (!node || !m) return;
    node.style.background = m.avatar_color || '#84CBB6';
    node.textContent = '';
    if (m.has_avatar) {
      var img = new Image();
      img.alt = '';
      img.src = '/api/me/avatar?v=' + Date.now();
      img.onerror = function () { node.textContent = (m.display_name || '?').charAt(0).toUpperCase(); };
      node.appendChild(img);
    } else {
      node.textContent = (m.display_name || m.name || '?').charAt(0).toUpperCase();
    }
  }

  /* The guest countdown. The server knows the end time, here it is only shown. */
  function startGuestClock(seconds, nodes) {
    var left = Math.max(0, seconds | 0);
    var total = left || 1;
    clearInterval(guestTimer);

    function paint() {
      var mm = String(Math.floor(left / 60)).padStart(2, '0');
      var ss = String(left % 60).padStart(2, '0');
      if (nodes.clock) nodes.clock.textContent = mm + ':' + ss;
      if (nodes.rail) nodes.rail.style.width = (left / total * 100) + '%';
      if (nodes.bar) {
        nodes.bar.classList.toggle('warn', left <= 300 && left > 60);
        nodes.bar.classList.toggle('last', left <= 60);
      }
    }
    paint();
    guestTimer = setInterval(function () {
      left--;
      if (left <= 0) {
        clearInterval(guestTimer);
        location.href = '/';
        return;
      }
      paint();
    }, 1000);
  }

  function logout() {
    return api.post('/api/auth/logout').then(function () { location.href = '/'; });
  }

  /* ------------------------------------------------------------------ */
  /* Small helpers                                                      */
  /* ------------------------------------------------------------------ */
  function mmss(sec) {
    sec = Math.max(0, sec | 0);
    return String(Math.floor(sec / 60)).padStart(2, '0') + ':' +
           String(sec % 60).padStart(2, '0');
  }

  function showError(node, text) {
    if (!node) return;
    node.textContent = text || '';
    node.classList.toggle('show', !!text);
  }

  function busy(node, on) {
    if (node) node.classList.toggle('busy', !!on);
  }

  /* Demonstration mode is visible at once, before any sign-in.

     We ask separately from "who am I": the banner has to be shown to
     someone who has not signed in yet too — otherwise a person presses
     "watch" and cannot tell why a cartoon about a rabbit plays instead of
     an episode. */
  function showMode() {
    var box = document.getElementById('demonote');
    if (!box) return;
    api.get('/api/mode').then(function (r) {
      box.hidden = !(r && r.demo);
    }).catch(function () { /* не ответил — молчим, плашка не появится */ });
  }

  /* We pull the language from the settings before drawing, so it does not flicker */
  function boot(after) {
    showMode();
    loadMe().then(function () {
      /* The account setting matters more — the person chose it
         deliberately. But if there is none, we take what they chose in
         this browser. */
      applyLang((me && me.settings && me.settings.lang) || savedLang() || 'ru');
      if (after) after(me);
    }).catch(function () {
      applyLang(savedLang() || 'ru');
      if (after) after(null);
    });
  }

  global.App = {
    safeUrl: safeUrl, el: el,
    api: api, csrfToken: csrfToken,
    applyLang: applyLang, applyLook: applyLook, t: t,
    animeName: animeName, kindName: kindName, langCode: langCode,
    defaultSource: defaultSource,
    plural: plural, count: count, say: say,
    ask: ask, tell: tell,
    loadMe: loadMe, setMe: setMe, boot: boot, logout: logout,
    paintAvatar: paintAvatar, startGuestClock: startGuestClock,
    mmss: mmss, showError: showError, busy: busy,
    get me() { return me; }
  };
})(window);
