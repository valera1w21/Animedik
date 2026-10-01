/* Общая часть всех страниц: обращение к серверу, язык, вход, настройки.
 *
 * Правило, которое здесь соблюдается везде: ничего чужого не вставляется
 * в страницу как HTML. Названия, имена и адреса приходят с сайтов-источников,
 * поэтому текст ставим через textContent, адреса — только после проверки.
 */
(function (global) {
  'use strict';

  /* ------------------------------------------------------------------ */
  /* Адреса                                                             */
  /* ------------------------------------------------------------------ */
  /* Функции esc() здесь больше нет. Она экранировала текст для сборки
     разметки строками — а весь код давно собирает страницу из узлов через
     textContent, где экранирование не нужно вовсе. Оставленная «на всякий
     случай», она была приглашением однажды склеить разметку вручную
     и решить, что это безопасно. Такой лазейки в проекте быть не должно. */

  /* Пропускаем только http и https. Отсекает javascript:, data:, blob: */
  function safeUrl(u) {
    if (u == null || u === '') return '';
    try {
      var p = new URL(String(u), location.origin);
      return (p.protocol === 'http:' || p.protocol === 'https:') ? p.href : '';
    } catch (e) { return ''; }
  }

  /* Готовый узел с текстом — безопаснее любой сборки строк */
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = String(text);
    return n;
  }

  /* ------------------------------------------------------------------ */
  /* Обращения к серверу                                                */
  /* ------------------------------------------------------------------ */
  function csrfToken() {
    var m = document.cookie.match(/(?:^|;\s*)csrf=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : '';
  }

  /* Сколько ждём ответа. Обращения к источникам идут через чужие сайты и
     бывают долгими, поэтому срок щедрый — но он есть. Без него зависший
     запрос висел бы вечно: полоска «ищем…» не сменилась бы никогда,
     а на странице просмотра колесо крутилось бы до перезагрузки. */
  var TIMEOUT_MS = 45000;

  function request(method, path, body, opts) {
    opts = opts || {};
    var headers = { 'Accept': 'application/json' };
    if (method !== 'GET') headers['X-CSRF-Token'] = csrfToken();
    var init = { method: method, headers: headers, credentials: 'same-origin' };
    /* keepalive просит браузер довести запрос до конца, даже если вкладку
       уже закрывают. Без него сохранение в момент ухода со страницы почти
       всегда обрывалось, и последняя минута просмотра пропадала.
       Обрывать такой запрос по таймеру нельзя и незачем: страницы уже нет. */
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
          /* Отдельный признак «пароль принят, нужен код из приложения».
             Обычным 401 это не отличить от неверного пароля, а вести
             себя надо по-разному. */
          err.needCode = r.headers.get('X-Need-Code') === '1';
          throw err;
        }
        return data;
      }, function (e) {
        /* Ответ пришёл, но развернуть его не удалось — обрезанный json,
           страница ошибки от прокси. Сообщение всё равно должно быть
           человеческим, а не «Unexpected end of JSON input». */
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
  /* Язык                                                               */
  /* ------------------------------------------------------------------ */
  var lang = 'ru';
  var LANG_KEY = 'animedik.lang';

  /* Выбранный язык помнит сам браузер.

     Раньше он жил только в настройках аккаунта — то есть человек,
     который не читает по-русски, должен был сперва войти, потом найти
     кабинет, потом найти в нём нужную строку. Всё это по-русски. Теперь
     язык переключается на любой странице и до всякого входа, а выбор
     переживает перезагрузку. */
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

  /* Один обработчик на все кнопки языка, на всех страницах. Раньше он
     жил в коде главной, и на странице просмотра кнопка не работала бы
     вовсе. Через делегирование — потому что кнопки есть и в разметке,
     которая появляется позже. */
  document.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('.lang[data-set-lang]') : null;
    if (!b) return;
    e.preventDefault();
    applyLang(b.dataset.setLang);
  });

  function t(ru, en) { return lang === 'en' ? en : ru; }

  /* Название аниме на языке сайта.

     Справочник отдаёт оба: русское и латинское. Пока сайт был только
     русским, латинское никуда не шло — а человеку, переключившему язык,
     «Магическая битва» не говорит ничего, ему нужно «Jujutsu Kaisen».

     Если латинского нет, остаётся русское: показать хоть что-то лучше,
     чем пустую строку. */
  function animeName(row) {
    if (!row) return '';
    if (lang === 'en' && row.title_en) return row.title_en;
    return row.title || row.title_en || '';
  }

  function langCode() { return lang; }

  /* Источник видео по умолчанию для языка сайта.

     Источники говорят каждый на своём: русские дают русские озвучки,
     англоязычный — английские субтитры и дубляж. Смешивать их нельзя,
     иначе человек, открывший сайт по-английски, получает русскую
     озвучку, о которой не просил. */
  function defaultSource() { return lang === 'en' ? 'source-en' : 'source-a'; }

  /* Вид тайтла на языке сайта: сериал, фильм, OVA.

     Сервер отдаёт и сырую метку (`kind`), и русскую подпись (`kind_ru`).
     Пока сайт был русским, хватало второй — а на английском рядом с
     «Jujutsu Kaisen» оставалось «сериал». Переводим здесь, из сырой
     метки: она одна на оба языка. */
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
    /* Метка незнакомая — показываем то, что прислал сервер. */
    return row.kind_ru || row.kind || '';
  }

  /* =====================================================================
     Свои окна вместо системных confirm / prompt / alert.

     Системные окна браузер вправе не показывать — и не показывает:
     после нескольких подряд Chrome предлагает «блокировать диалоги
     этой страницы», а во встроенных окнах и вебвью их глушат сразу.
     Из-за этого «Удалить» на карточке, «Удалить» у объявления и
     выключение входа по коду просто ничего не делали: код доходил до
     confirm(), получал false и молча выходил. Кнопка нажимается,
     ничего не происходит, объяснить нечем.

     Свои окна показываются всегда, выглядят как остальной сайт и
     закрываются по Esc.
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

  /* Просто сообщение с одной кнопкой — замена alert. */
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

  /* Склонение числительных.

     Раньше подписи склеивались с числом напрямую — «1 тайтлов»,
     «502 серий», «1 незакрытых». Мелочь, но она сразу выдаёт, что текст
     собирали наспех, и попадается на глаза постоянно: эти счётчики
     висят на главной всё время.

     ru — три формы: 1 тайтл, 2 тайтла, 5 тайтлов.
     en — две: 1 title, 2 titles. */
  function plural(n, ru, en) {
    if (lang === 'en') return en[Math.abs(n) === 1 ? 0 : 1];
    var a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return ru[2];
    if (b > 1 && b < 5) return ru[1];
    if (b === 1) return ru[0];
    return ru[2];
  }

  /* Число вместе с правильной формой слова */
  function count(n, ru, en) { return n + ' ' + plural(n, ru, en); }

  /* Готовые наборы: пусть слова живут в одном месте, а не по десять раз
     в разных файлах, где их легко разойтись между собой. */
  var WORDS = {
    title: [['тайтл', 'тайтла', 'тайтлов'], ['title', 'titles']],
    episode: [['серия', 'серии', 'серий'], ['episode', 'episodes']],
    unfinished: [['незакрытый', 'незакрытых', 'незакрытых'],
                 ['unfinished', 'unfinished']],
    /* Часть франшизы: сезон, фильм, OVA или спешл. Слово нужно там, где
       считать «тайтлами» неверно — «Наруто» это один тайтл и двадцать
       девять частей. */
    part: [['часть', 'части', 'частей'], ['part', 'parts']]
  };

  function say(n, kind) { return count(n, WORDS[kind][0], WORDS[kind][1]); }

  /* ------------------------------------------------------------------ */
  /* Оформление                                                         */
  /* ------------------------------------------------------------------ */

  /* ==================================================================
     Тема и праздничное оформление

     Тема — это цвета: фон, поверхности, линии, текст. Праздник — слой
     поверх: заливка и мелкие летающие штуки. Одно другого не отменяет,
     поэтому во время праздника темы переключаются как обычно, а
     праздник при переключении темы остаётся на месте.

     Всё считается здесь, по часам устройства. Сервер про праздники не
     знает ничего и знать не должен.
     ================================================================== */
  var ТЕМЫ = ['kak-seychas', 'noch', 'ugol', 'bumaga'];
  var ПРАЗДНИКИ = ['newyear', 'halloween', 'sakura'];

  function applyTheme(имя) {
    if (ТЕМЫ.indexOf(имя) < 0) имя = 'kak-seychas';
    document.documentElement.setAttribute('data-theme', имя);
  }

  /* Какой сейчас праздник по календарю.

     Окна намеренно широкие и без географии. Новый год — 1 января почти
     везде, где живут по обычному календарю; разъезжается Рождество:
     25 декабря у одних, 7 января у других, плюс старый Новый год 14-го.
     Одно окно с 18 декабря по 15 января накрывает всё это разом, и
     определять страну не нужно вовсе.

     Хэллоуин привязан к 31 октября, сакура — к цветению, конец марта и
     первая половина апреля. */
  function holidayOn(дата) {
    var d = дата || new Date();
    var м = d.getMonth() + 1, ч = d.getDate();
    if ((м === 12 && ч >= 18) || (м === 1 && ч <= 15)) return 'newyear';
    if ((м === 10 && ч >= 24) || (м === 11 && ч <= 2)) return 'halloween';
    if ((м === 3 && ч >= 25) || (м === 4 && ч <= 15)) return 'sakura';
    return '';
  }

  /* Сколько украшений рисовать. Меньше на телефоне: там и экран
     меньше, и батарею жалко. */
  function skolko(base) {
    var узко = Math.min(innerWidth, innerHeight) < 620;
    return узко ? Math.round(base * 0.5) : base;
  }

  /* Значки праздника рядом с логотипом.

     Собираются узлами, а не строкой в innerHTML. Строки тут постоянные,
     и соблазн написать одну строчку велик — но именно так однажды и
     появляется innerHTML со значением, пришедшим снаружи. Проще не
     заводить эту привычку вовсе. */
  function узел(тег, свойства, дети) {
    var э = document.createElementNS('http://www.w3.org/2000/svg', тег);
    for (var к in свойства) {
      if (Object.prototype.hasOwnProperty.call(свойства, к)) э.setAttribute(к, свойства[к]);
    }
    (дети || []).forEach(function (д) { э.appendChild(д); });
    return э;
  }

  function картинка(размер, дети) {
    return узел('svg', {
      width: размер, height: размер, viewBox: '0 0 20 20',
      fill: 'none', 'aria-hidden': 'true'
    }, дети);
  }

  var ЗНАЧКИ = {
    newyear: function () {
      return картинка(19, [
        узел('path', { d: 'M10 2.2 13.4 7H6.6L10 2.2Z', fill: '#8FC7EA' }),
        узел('path', { d: 'M10 6.4 14.4 12H5.6L10 6.4Z', fill: '#A8D6F2' }),
        узел('path', { d: 'M10 10.6 15.4 17H4.6L10 10.6Z', fill: '#C6E5F8' }),
        узел('rect', { x: 9, y: 16.4, width: 2, height: 2.4, rx: 0.6, fill: '#8A6B4F' })
      ]);
    },
    halloween: function () {
      return картинка(19, [
        узел('path', {
          d: 'M10 5.2c3.4 0 5.6 2.4 5.6 5.6S13.4 17 10 17s-5.6-3-5.6-6.2S6.6 5.2 10 5.2Z',
          fill: '#E68A3C'
        }),
        узел('path', {
          d: 'M9.4 5.3c0-1.3.5-2.2 1.6-2.7', stroke: '#6C8A46',
          'stroke-width': 1.4, 'stroke-linecap': 'round'
        }),
        узел('path', { d: 'M7.6 9.6 9 11H6.2l1.4-1.4ZM12.4 9.6 13.8 11H11l1.4-1.4Z', fill: '#3A2416' }),
        узел('path', {
          d: 'M7.4 13.4h5.2', stroke: '#3A2416',
          'stroke-width': 1.3, 'stroke-linecap': 'round'
        })
      ]);
    },
    sakura: function () {
      var лепестки = [[10, 5.4, 0], [14.4, 8.6, 72], [12.7, 13.8, 144],
                      [7.3, 13.8, 216], [5.6, 8.6, 288]].map(function (л) {
        return узел('ellipse', {
          cx: л[0], cy: л[1], rx: 2.5, ry: 3.3,
          transform: 'rotate(' + л[2] + ' ' + л[0] + ' ' + л[1] + ')'
        });
      });
      return картинка(19, [
        узел('g', { fill: '#F3AFC5' }, лепестки),
        узел('circle', { cx: 10, cy: 10, r: 1.7, fill: '#FFF0B8' })
      ]);
    }
  };

  /* Кладбищенский задник для Хэллоуина.

     Тянется во всю ширину у нижнего края. Собран из простых фигур, а
     не из одного огромного пути: так его видно в коде и можно
     поправить, не расшифровывая полсотни чисел подряд. */
  function kladbische() {
    var g = [];

    function надгробие(x, w, h, крест) {
      var верх = 160 - h;
      g.push(узел('path', {
        d: 'M' + x + ' 160 v-' + (h - w / 2) +
           ' a' + (w / 2) + ' ' + (w / 2) + ' 0 0 1 ' + w + ' 0' +
           ' v' + (h - w / 2) + ' z'
      }));
      if (крест) {
        g.push(узел('rect', { x: x + w / 2 - 3, y: верх + 12, width: 6, height: 26, rx: 1.5,
                              fill: 'var(--hall-kamen)' }));
        g.push(узел('rect', { x: x + w / 2 - 11, y: верх + 19, width: 22, height: 6, rx: 1.5,
                              fill: 'var(--hall-kamen)' }));
      }
    }

    function tykva(x, r) {
      g.push(узел('ellipse', { cx: x, cy: 160 - r * 0.72, rx: r, ry: r * 0.78 }));
      g.push(узел('rect', { x: x - 2.5, y: 160 - r * 1.6, width: 5, height: r * 0.4, rx: 1.5 }));
      // прорези глаз и рта — цветом свечения, чтобы тыква «горела»
      g.push(узел('path', {
        d: 'M' + (x - r * 0.42) + ' ' + (160 - r * 0.95) + ' l' + (r * 0.3) + ' ' + (r * 0.34) +
           ' h-' + (r * 0.6) + ' z', fill: 'var(--hall-ogon)'
      }));
      g.push(узел('path', {
        d: 'M' + (x + r * 0.12) + ' ' + (160 - r * 0.95) + ' l' + (r * 0.3) + ' ' + (r * 0.34) +
           ' h-' + (r * 0.6) + ' z', fill: 'var(--hall-ogon)'
      }));
      g.push(узел('rect', { x: x - r * 0.45, y: 160 - r * 0.5, width: r * 0.9, height: r * 0.16,
                            rx: r * 0.08, fill: 'var(--hall-ogon)' }));
    }

    function cherep(x) {
      g.push(узел('path', {
        d: 'M' + x + ' 160 v-14 a13 13 0 0 1 26 0 v14 z', fill: 'var(--hall-kost)'
      }));
      g.push(узел('circle', { cx: x + 8, cy: 150, r: 3.4, fill: 'var(--hall-temno)' }));
      g.push(узел('circle', { cx: x + 18, cy: 150, r: 3.4, fill: 'var(--hall-temno)' }));
      g.push(узел('rect', { x: x + 11, y: 155, width: 4, height: 5, rx: 1,
                            fill: 'var(--hall-temno)' }));
    }

    function derevo(x, k) {
      var ветки = 'M' + x + ' 160 v-58 ' +
                  'M' + x + ' 128 l-22 -20 m22 4 l-16 -22 ' +
                  'M' + x + ' 118 l20 -18 m-20 2 l15 -24 ' +
                  'M' + x + ' 102 l-12 -20 M' + x + ' 102 l11 -17';
      g.push(узел('path', {
        d: ветки, stroke: 'var(--hall-temno)', 'stroke-width': k,
        'stroke-linecap': 'round', fill: 'none'
      }));
    }

    function zabor(x, ширина) {
      for (var i = 0; i < ширина; i += 22) {
        g.push(узел('path', {
          d: 'M' + (x + i) + ' 160 v-30 l5 -7 l5 7 v30 z'
        }));
      }
      g.push(узел('rect', { x: x, y: 138, width: ширина, height: 5 }));
      g.push(узел('rect', { x: x, y: 152, width: ширина, height: 5 }));
    }

    // земля
    g.push(узел('path', {
      d: 'M0 160 v-16 q90 -12 190 -4 t210 2 t180 -8 t200 6 t220 -4 t200 8 v16 z'
    }));
    derevo(88, 7);
    надгробие(190, 46, 74, true);
    cherep(258);
    zabor(300, 154);
    надгробие(492, 40, 58, false);
    tykva(576, 26);
    надгробие(660, 52, 86, true);
    derevo(792, 6);
    tykva(880, 20);
    надгробие(950, 44, 66, false);
    zabor(1030, 132);

    /* Без preserveAspectRatio:none. С ним рисунок натягивался на любую
       ширину окна: круглые тыквы становились овальными, а на узком
       экране всё сплющивалось. Пусть лучше высота считается от ширины —
       фигуры останутся фигурами. */
    return узел('svg', {
      class: 'kladbische', viewBox: '0 0 1200 160',
      fill: 'var(--hall-temno)', 'aria-hidden': 'true'
    }, g);
  }

  /* Луна: круг со свечением вокруг. */
  function luna() {
    return узел('svg', { class: 'luna', viewBox: '0 0 120 120',
                         fill: 'none', 'aria-hidden': 'true' }, [
      узел('circle', { cx: 60, cy: 60, r: 46, fill: 'var(--hall-luna-svet)' }),
      узел('circle', { cx: 60, cy: 60, r: 30, fill: 'var(--hall-luna)' }),
      узел('circle', { cx: 50, cy: 52, r: 5.5, fill: 'var(--hall-krater)' }),
      узел('circle', { cx: 68, cy: 66, r: 7.5, fill: 'var(--hall-krater)' }),
      узел('circle', { cx: 66, cy: 45, r: 3.5, fill: 'var(--hall-krater)' })
    ]);
  }

  /* Летучая мышь для Хэллоуина — тем же способом. */
  function мышь(размер) {
    return узел('svg', {
      width: размер, height: Math.round(размер * 0.5), viewBox: '0 0 32 16',
      fill: 'currentColor', 'aria-hidden': 'true'
    }, [узел('path', {
      d: 'M16 4.6c1-1.7 2.4-2.3 3.6-1.4.9.7 1 1.9.6 3 1.6-1.6 3.4-2.6 5.4-2.9-1.2 1.2-1.8 '
         + '2.6-1.9 4.2 1.4-.7 2.8-.9 4.3-.7-2 .9-3.4 2.3-4.2 4.2-1.4-.9-2.8-1-4.2-.3-1.2.6-2.1 '
         + '1.6-2.6 2.9-.5-1.3-1.4-2.3-2.6-2.9-1.4-.7-2.8-.6-4.2.3-.8-1.9-2.2-3.3-4.2-4.2 1.5-.2 '
         + '2.9 0 4.3.7-.1-1.6-.7-3-1.9-4.2 2 .3 3.8 1.3 5.4 2.9-.4-1.1-.3-2.3.6-3 1.2-.9 2.6-.3 3.6 1.4Z'
    })]);
  }

  function applyHoliday(режим) {
    /* Режим: '' — по календарю, 'off' — выключено, иначе название. */
    var сейчас = режим === 'off' ? ''
               : (режим && ПРАЗДНИКИ.indexOf(режим) >= 0 ? режим : holidayOn());

    var корень = document.documentElement;
    if (сейчас) корень.setAttribute('data-holiday', сейчас);
    else корень.removeAttribute('data-holiday');

    document.querySelectorAll('.prazdnik').forEach(function (с) { с.remove(); });
    document.querySelectorAll('.prazdznak').forEach(function (з) { з.remove(); });
    if (!сейчас) return;

    /* Нижний слой — только заливка. */
    var низ = document.createElement('div');
    низ.className = 'prazdnik';
    низ.setAttribute('aria-hidden', 'true');
    var заливка = document.createElement('div');
    заливка.className = 'zaliv';
    низ.appendChild(заливка);
    if (сейчас === 'halloween') {
      низ.appendChild(luna());
      низ.appendChild(kladbische());
    }
    document.body.appendChild(низ);

    /* Верхний — летающее, над карточками. */
    var слой = document.createElement('div');
    слой.className = 'prazdnik verh';
    слой.setAttribute('aria-hidden', 'true');

    /* Настройку «меньше движения» соблюдает CSS: летающее там просто
       скрыто. Дублировать проверку здесь я пробовал — и получил две
       правды об одном и том же: правило в стилях говорило одно, условие
       в скрипте другое, а при переключении настройки в системе нужна
       была перезагрузка. Решает одно место. */
    {
      if (сейчас === 'newyear') сыпать(слой, 'snezh', skolko(26), function (э, r) {
        var d = 4 + r() * 7;
        э.style.setProperty('--razmer', d.toFixed(1) + 'px');
        var видно = 0.32 + r() * 0.4;
        э.style.setProperty('--vidno', видно.toFixed(2));
        /* Для светлой темы своя прозрачность: на белом фоне те же
           значения читаются заметно слабее. */
        э.style.setProperty('--vidno-svet', Math.min(1, видно + 0.42).toFixed(2));
        э.style.setProperty('--snos', Math.round(-40 + r() * 80) + 'px');
        э.style.animationDuration = (9 + r() * 11).toFixed(1) + 's';
      });
      if (сейчас === 'sakura') сыпать(слой, 'lepestok', skolko(20), function (э, r) {
        var w = 8 + r() * 6;
        э.style.width = w + 'px'; э.style.height = (w * 0.72).toFixed(1) + 'px';
        э.style.setProperty('--vidno', (0.34 + r() * 0.36).toFixed(2));
        э.style.setProperty('--snos', Math.round(20 + r() * 120) + 'px');
        э.style.animationDuration = (10 + r() * 10).toFixed(1) + 's';
      });
      if (сейчас === 'halloween') летучки(слой, skolko(5));
    }

    document.body.appendChild(слой);

    var где = document.querySelector('.brand') || document.querySelector('.back');
    if (где && ЗНАЧКИ[сейчас]) {
      var знак = document.createElement('span');
      знак.className = 'prazdznak';
      знак.setAttribute('aria-hidden', 'true');
      знак.appendChild(ЗНАЧКИ[сейчас]());
      где.appendChild(знак);
    }
  }

  function сыпать(слой, класс, сколько, настроить) {
    var r = Math.random;
    for (var i = 0; i < сколько; i++) {
      var э = document.createElement('span');
      э.className = класс;
      э.style.left = (r() * 100).toFixed(2) + '%';
      /* Куда встать, если движение выключено в системе. Считаем всегда:
         одно свойство дешевле, чем вторая ветка кода, которая рано или
         поздно разойдётся с первой. */
      э.style.setProperty('--stoyat', (r() * 96).toFixed(1) + '%');
      /* Отрицательная задержка: снег идёт уже при открытии страницы, а
         не выпадает одной строчкой через десять секунд. */
      э.style.animationDelay = '-' + (r() * 18).toFixed(1) + 's';
      настроить(э, r);
      слой.appendChild(э);
    }
  }

  function летучки(слой, сколько) {
    var r = Math.random;
    for (var i = 0; i < сколько; i++) {
      var э = document.createElement('span');
      э.className = 'letuchka';
      э.style.top = (6 + r() * 46).toFixed(1) + '%';
      э.style.setProperty('--stoyat', (6 + r() * 46).toFixed(1) + '%');
      э.style.setProperty('--stoyat-x', (8 + r() * 78).toFixed(1) + '%');
      э.style.animationDelay = '-' + (r() * 26).toFixed(1) + 's';
      э.style.animationDuration = (22 + r() * 18).toFixed(1) + 's';
      э.appendChild(мышь(Math.round(12 + r() * 12)));
      слой.appendChild(э);
    }
  }

  function applyLook(s) {
    s = s || {};
    applyTheme(s.theme || 'kak-seychas');
    applyHoliday(s.holiday || '');
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
  /* Кто я                                                              */
  /* ------------------------------------------------------------------ */
  var me = null;
  var guestTimer = null;

  /* Запоминает, кто мы. Вход и гостевой пропуск отвечают ровно тем же
     набором полей, что и /api/me — это один и тот же me_payload на
     сервере, — поэтому лишний запрос после входа не нужен.

     Раньше такой возможности не было, и index.js после входа звал
     showApp(r.me) напрямую. Страница рисовалась верно, но App.me
     оставался пустым до первой перезагрузки. Последствия были тихие
     и разные:
       * в списке аккаунтов админ видел «Выключить» и «Удалить»
         на своей же строке — проверка «это я» сравнивает с App.me;
       * сортировка списка и «показывать законченные» откатывались
         к умолчанию, что бы ни стояло в кабинете;
       * поле имени в кабинете теряло запасное значение.
     Всё это чинилось перезагрузкой страницы, поэтому и не замечалось. */
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

  /* Обратный отсчёт гостя. Время конца знает сервер, здесь только показ. */
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
  /* Мелкие помощники                                                   */
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

  /* Демонстрационный режим виден сразу, до всякого входа.

     Спрашиваем отдельно от «кто я»: плашку надо показать и тому, кто
     ещё не вошёл, — иначе человек нажмёт «смотреть» и не поймёт, почему
     вместо серии играет мультфильм про кролика. */
  function showMode() {
    var box = document.getElementById('demonote');
    if (!box) return;
    api.get('/api/mode').then(function (r) {
      box.hidden = !(r && r.demo);
    }).catch(function () { /* не ответил — молчим, плашка не появится */ });
  }

  /* Подтягиваем язык из настроек до отрисовки, чтобы не мигало */
  function boot(after) {
    showMode();
    loadMe().then(function () {
      /* Настройка аккаунта важнее — человек выбрал её осознанно. Но
         если её нет, берём то, что он выбрал в этом браузере. */
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
    applyTheme: applyTheme, applyHoliday: applyHoliday, holidayOn: holidayOn,
    plural: plural, count: count, say: say,
    ask: ask, tell: tell,
    loadMe: loadMe, setMe: setMe, boot: boot, logout: logout,
    paintAvatar: paintAvatar, startGuestClock: startGuestClock,
    mmss: mmss, showError: showError, busy: busy,
    get me() { return me; }
  };
})(window);
