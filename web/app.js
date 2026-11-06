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

  /* Адрес, завёрнутый в CSS: url("…").

     Внутри кавычек у CSS свои правила: обратная косая черта начинает
     «экранирование», а кавычка закрывает строку. safeUrl пропускает
     только http и https, и кавычки браузер в таком адресе кодирует сам
     (%22) — но обратная черта после «?» остаётся как есть. Адрес вида
     https://сайт/картинка?a\ превращал конец строки в «\"», то есть в
     кавычку внутри строки: правило становилось битым, и обложка молча
     не ставилась. Выйти за пределы фона так нельзя (свойство задаётся
     по одному, а не куском стиля), но и терять картинку незачем.

     Экранируем способом самого CSS — \5c и \22 с пробелом после кода:
     адрес не меняется ни на букву, а строка закрывается ровно там, где
     задумано. Переводы строк в адресе safeUrl не пропускает, но и их
     заодно делаем безопасными. */
  function cssUrl(u) {
    return 'url("' + String(u).replace(/[\\"\n\r\f]/g, function (c) {
      return '\\' + c.charCodeAt(0).toString(16) + ' ';
    }) + '")';
  }

  /* Обложка с запасными вариантами.

     Раньше обложка ставилась одной строкой в style.backgroundImage. Если
     ссылка битая (а у источников такое бывает: адрес собран неправильно,
     картинку удалили, сайт сменил домен), в карточке оставалась пустота,
     и понять, что это за тайтл, было нельзя.

     Теперь картинка сначала грузится в памяти (new Image). Получилось —
     ставим её фоном. Нет — пробуем следующую ссылку из списка. Кончились
     ссылки, а в opts.lookup есть название, — спрашиваем сервер, не знает
     ли он обложку в справочниках (/api/poster). Не вышло и это —
     вызываем opts.onFail, и вызывающий показывает букву-заглушку.

     node   — элемент, которому ставится фон;
     urls   — строка или список ссылок по порядку предпочтения;
     opts   — {lookup: {title, title_en, year, key}, onOk(url), onFail()}.
              key — запись своего списка: сервер сам допишет ей найденную
              обложку, и в следующий раз она откроется сразу.

     Поправки к первой версии:
       * вопросы к серверу идут в очередь, не больше двух разом. На
         странице бывает три десятка карточек без картинки; все разом
         они занимали бы соединения браузера, и список, колокольчик и
         прочие запросы страницы ждали бы, пока сервер обойдёт
         справочники (он ходит к ним строго по одному);
       * ссылка, которая уже не загрузилась, второй раз не пробуется,
         даже если её же вернул сервер. */
  var posterLooked = {};
  var POSTER_ASK_AT_ONCE = 2;
  var posterAsking = 0;
  var posterQueue = [];

  function posterAsk(params) {
    return new Promise(function (resolve) {
      posterQueue.push({params: params, resolve: resolve});
      posterPump();
    });
  }

  function posterRun(job) {
    posterAsking++;
    api.get('/api/poster?' + job.params)
      .then(function (r) { return (r && r.poster) || ''; })
      .catch(function () { return ''; })
      .then(function (u) {
        posterAsking--;
        job.resolve(u);
        posterPump();
      });
  }

  function posterPump() {
    while (posterAsking < POSTER_ASK_AT_ONCE && posterQueue.length) {
      posterRun(posterQueue.shift());
    }
  }

  /* Обложка грузится, когда карточка вот-вот покажется на экране.

     Раньше каждая карточка тянула свою картинку сразу при отрисовке:
     открыл главную — браузер разом скачивает обложки всего списка, даже
     тех карточек, до которых никто не долистает; открыл окно частей
     «Ван-Пис» — семьдесят шесть картинок одновременно, и первые, нужные,
     ждут в общей очереди. Колокольчик со скрытым списком тоже грузил
     свои обложки, хотя его ни разу не открыли.

     Теперь за карточками следит IntersectionObserver: картинка
     начинает грузиться, когда до карточки остаётся ~400 точек
     прокрутки, — к моменту, когда она на экране, обложка обычно уже
     пришла. Старый браузер без него — всё как раньше, сразу. */
  var lazyEye = null;
  function whenSeen(node, go) {
    if (typeof IntersectionObserver !== 'function') { go(); return; }
    if (!lazyEye) {
      lazyEye = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) {
          if (!e.isIntersecting) return;
          lazyEye.unobserve(e.target);
          var run = e.target._whenSeen;
          e.target._whenSeen = null;
          if (run) run();
        });
      }, { rootMargin: '400px 0px' });
    }
    /* Второй вызов bg для того же узла (новый адрес) заменяет первый:
       грузиться будет только последнее. */
    node._whenSeen = go;
    lazyEye.observe(node);
    /* Список перерисовывается целиком (смена вкладки, языка, новая
       серия), и карточки, до которых так и не долистали, выбрасываются,
       не дождавшись показа. Наблюдатель держал бы их вечно. Раз в
       полсотни новых карточек отпускаем тех, кого уже нет на странице
       (а не просто ещё не вставили: даём им пять секунд). */
    lazyWaiting.push({ node: node, at: Date.now() });
    if (lazyWaiting.length % 50 === 0) {
      var now = Date.now();
      lazyWaiting = lazyWaiting.filter(function (w) {
        if (!w.node._whenSeen) return false;
        if (w.node.isConnected || now - w.at < 5000) return true;
        lazyEye.unobserve(w.node);
        w.node._whenSeen = null;
        return false;
      });
    }
  }
  var lazyWaiting = [];

  function bg(node, urls, opts) {
    opts = opts || {};
    var list = (Array.isArray(urls) ? urls : [urls]).map(safeUrl)
      .filter(function (u, i, all) { return u && all.indexOf(u) === i; });
    var tried = [];
    var asked = false;
    function done(u) {
      node.style.backgroundImage = cssUrl(u);
      if (opts.onOk) opts.onOk(u);
    }
    function fail() { if (opts.onFail) opts.onFail(); }
    function lookup() {
      var q = opts.lookup;
      if (asked || !q || !(q.title || q.title_en)) return fail();
      asked = true;
      var key = [q.title || '', q.title_en || '', q.year || '', q.key || ''].join('|');
      var pending = posterLooked[key];
      if (!pending) {
        var params = 'title=' + encodeURIComponent(String(q.title || q.title_en).slice(0, 300));
        if (q.title_en) params += '&title_en=' + encodeURIComponent(String(q.title_en).slice(0, 300));
        if (q.year) params += '&year=' + encodeURIComponent(q.year);
        if (q.key) params += '&key=' + encodeURIComponent(String(q.key).slice(0, 200));
        /* Один и тот же тайтл на странице встречается несколько раз —
           спрашиваем сервер один раз и делим ответ. */
        pending = posterLooked[key] = posterAsk(params);
      }
      pending.then(function (u) {
        u = safeUrl(u);
        if (u && tried.indexOf(u) < 0) { list.push(u); next(); } else fail();
      });
    }
    function next() {
      var u = list.shift();
      if (!u) return lookup();
      tried.push(u);
      var probe = new Image();
      probe.onload = function () { done(u); };
      probe.onerror = next;
      probe.src = u;
    }
    whenSeen(node, next);
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
    /* Заголовок X-UI-Lang нарочно не шлём: по нему сервер писал бы detail
       по-английски своими словами («login»), а на экране всё называется
       «username». Английский текст берём из словаря ниже (serverText),
       а для фраз, которых в нём нет, — из detail_en, который сервер
       шлёт всегда. */
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
      timer = setTimeout(function () { ctl.abort(); }, opts.timeout || TIMEOUT_MS);
    }
    function done(v) { if (timer) clearTimeout(timer); return v; }

    return fetch(path, init).then(function (r) {
      if (r.status === 204) { done(); return null; }
      var ct = r.headers.get('content-type') || '';
      var parse = ct.indexOf('application/json') !== -1 ? r.json() : r.text();
      return parse.then(function (data) {
        done();
        if (!r.ok) {
          /* Свои сообщения — на языке сайта. Текст от сервера (detail)
             приходит по-русски — на английской странице его переводит
             serverText (см. ниже). */
          var msg = (data && data.detail) ? data.detail : (t('Ошибка ', 'Error ') + r.status);
          /* Переводим только строку: список полей (422) ниже заменяется
             своей фразой, а String(список) дал бы «[object Object]».
             detail_en — английская фраза от самого сервера, запасной
             путь для фраз, которых нет в словаре serverText. */
          if (typeof msg === 'string' && data && data.detail) {
            msg = serverText(msg, r.status, typeof data.detail_en === 'string' ? data.detail_en : '');
          }
          /* 422 — сервер не принял поля формы (пустое, слишком короткое).
             Подробности приходят списком для программ, а не для людей, и
             раньше человек видел голое «Ошибка 422». */
          if (typeof msg !== 'string') {
            msg = r.status === 422
              ? t('Проверьте поля: что-то не заполнено или заполнено не так.',
                  'Check the fields: something is empty or not filled in correctly.')
              : t('Ошибка ', 'Error ') + r.status;
          }
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
        var err = new Error(r.ok ? t('Сервер ответил непонятно', 'The server sent an unreadable reply')
                                 : (t('Ошибка ', 'Error ') + r.status));
        err.status = r.status;
        throw err;
      });
    }, function (e) {
      done();
      var err = new Error(e && e.name === 'AbortError'
        ? t('Сервер долго не отвечает. Попробуйте ещё раз.',
            'The server is taking too long. Please try again.')
        : t('Нет связи с сервером', 'No connection to the server'));
      err.status = 0;
      throw err;
    });
  }

  /* Ответы сервера об ошибках — на языке сайта.

     Сервер пишет причину отказа (поле detail) только по-русски. Пока
     сайт был русским, этого хватало; на английской странице человек
     видел «Неверный логин или пароль» или «Слишком часто, подождите
     минуту» — то есть ровно в ту минуту, когда что-то пошло не так,
     переставал понимать, что именно.

     Здесь — английские версии всех фраз, которые сервер отдаёт людям.
     Фразы с числом внутри («через 3 мин.») узнаются по началу. Если
     сервер однажды скажет что-то новое, чего здесь нет, на английской
     странице появится общая фраза по коду ответа — по-английски, а не
     русский текст среди английского. Фразы, которые сервер уже пишет
     на двух языках через « · », делятся пополам: каждой странице — её
     половина. Сообщения помощника сервер пишет на языке сайта сам — они
     сюда не попадают (в них нет русских букв на английской странице). */
  var SERVER_EN = {
    'Неверный логин или пароль': 'Wrong username or password.',
    'Введите код из приложения': 'Enter the code from your app.',
    'Код не подошёл': 'The code did not match.',
    'Код не подошёл. Проверьте цифры.': 'The code did not match. Check the digits.',
    'Этот код уже использован. Дождитесь следующего в приложении.':
      'This code has already been used. Wait for the next one in your app.',
    'Слишком часто, подождите минуту': 'Too often — please wait a minute.',
    'Слишком частый поиск, подождите минуту': 'Too many searches — please wait a minute.',
    'Слишком часто. Попробуйте позже.': 'Too often. Please try again later.',
    'Слишком часто. Попробуйте через несколько минут.': 'Too often. Please try again in a few minutes.',
    'Слишком часто': 'Too often — please wait a little.',
    'Слишком много запросов': 'Too many requests — please wait a minute.',
    'Не найдено': 'Not found.',
    'Нужно войти': 'Please sign in.',
    'Неизвестный источник': 'Unknown source.',
    'Неизвестный статус': 'Unknown status.',
    'Файл слишком большой': 'The file is too large.',
    'Не удалось прочитать изображение': 'Could not read the picture.',
    'На сервере не установлен обработчик картинок': 'The server cannot process pictures right now.',
    'Пустое название': 'The title is empty.',
    'Нужно название': 'A title is needed.',
    'Нет такого пользователя': 'No such user.',
    'Это последний администратор': 'This is the last administrator.',
    'Себя удалить нельзя': 'You cannot delete yourself.',
    'Себя выключить нельзя': 'You cannot disable yourself.',
    'Такой логин уже занят': 'This username is already taken.',
    'Роль бывает user или admin': 'The role must be user or admin.',
    'роль бывает admin или user': 'The role must be user or admin.',
    'Пустой логин': 'The username is empty.',
    'Справочник сейчас не отвечает': 'The catalogue is not responding right now.',
    'Каталог сейчас не отвечает. Попробуйте ещё раз.': 'The catalogue is not responding. Please try again.',
    'Ни один плеер не отдал видео': 'No player returned a video.',
    'Ни один источник сейчас не отвечает. Попробуйте через несколько минут.':
      'No source is responding right now. Please try again in a few minutes.',
    'Источник не отвечает. Попробуйте ещё раз через минуту.':
      'The source is not responding. Please try again in a minute.',
    'Источник вернул серии в неизвестном виде': 'The source returned episodes in an unknown format.',
    'Такой серии нет': 'There is no such episode.',
    'Пароль не подошёл': 'Wrong password.',
    'Текущий пароль не подошёл': 'The current password is wrong.',
    'Новый пароль совпадает с текущим': 'The new password is the same as the current one.',
    'Пароль должен быть строкой': 'The password must be text.',
    'Такой пароль подбирают первым же перебором': 'This password is one of the first ones attackers try.',
    'В пароле слишком мало разных символов': 'The password has too few different characters.',
    'Не похоже на адрес почты': 'That does not look like an email address.',
    'Имя не может быть пустым': 'The name cannot be empty.',
    'Цвет должен быть вида #RRGGBB': 'The colour must look like #RRGGBB.',
    'Пустое объявление': 'The announcement is empty.',
    'Праздничное оформление выбирает администратор': 'Only the administrator picks the holiday decoration.',
    'Запрос отклонён': 'Request rejected.',
    'Запрос отклонён: не сходится метка формы':
      'Request rejected: the form token does not match. Reload the page.',
    'Гостевой доступ только на просмотр. За аккаунтом — к администратору.':
      'A guest pass is view-only. Ask the administrator for an account.',
    'Вы уже вошли в аккаунт. Чтобы зайти гостем, сначала выйдите.':
      'You are already signed in. Sign out first to browse as a guest.',
    'Сейчас слишком много гостей. Загляните позже.': 'Too many guests right now. Please come back later.',
    'Вход по коду уже включён.': 'The sign-in code is already on.',
    'Настройка устарела. Снимите галочку и начните заново.':
      'This setup has expired. Turn the switch off and start again.',
    'Ссылка на видео устарела — откройте серию заново': 'The video link has expired — open the episode again.',
    'Субтитры устарели — откройте серию заново': 'The subtitles have expired — open the episode again.',
    'Плейлист слишком большой': 'The playlist is too large.',
    'Проверка уже была только что — подождите пару минут':
      'A check has just run — please wait a couple of minutes.',
    'Этот источник русский, а язык аниме — английский':
      'This source is Russian, but your anime language is English.',
    'В демо-режиме английских источников нет': 'There are no English sources in demo mode.',
    'Настройки не помещаются в отведённое место': 'The settings do not fit into the space allowed.',
    'Внутренняя ошибка': 'Internal server error.'
  };
  var SERVER_EN_START = [
    ['Слишком много попыток', 'Too many attempts. Please try again in a few minutes.'],
    ['Пароль короче', 'The password is too short.'],
    ['Пароль длиннее', 'The password is too long.'],
    ['Логин: латиница', 'Username: Latin letters, digits, dot, hyphen and underscore, 3 to 32 characters.'],
    ['Недопустимое значение', 'This value is not allowed.'],
    ['Неизвестный источник', 'Unknown source.'],
    ['Озвучка «', 'This dub is not responding, and neither are the others.'],
    ['Хостинг видео ответил', 'The video host returned an error.'],
    ['Вход по коду уже включён', 'The sign-in code is already on. Turn it off first to change the app.'],
    ['Эту часть ни один источник не отдал', 'No source has this part. Try another part.'],
    ['Не нашёл этот тайтл у источника', 'The source no longer has this title — pick it again.'],
    ['Тайтл выпал из памяти сервера', 'The server lost track of this title — find it again.'],
    ['На сервере не установлены библиотеки', 'The server is missing its video source libraries.']
  ];
  var STATUS_EN = {
    400: 'The request was not accepted.', 401: 'Please sign in again.', 403: 'Not allowed.',
    404: 'Not found.', 409: 'This conflicts with the current state. Reload the page.',
    412: 'This source does not match your anime language.', 413: 'The file is too large.',
    429: 'Too many requests — please wait a minute.', 502: 'A source or the catalogue did not respond.',
    503: 'The service is unavailable right now.', 504: 'The source took too long to answer.'
  };
  var CYR = /[а-яё]/i;

  /* detailEn — английская фраза от самого сервера (api/errors.py шлёт её
     всегда, рядом с русским detail). Словарь ниже знает только фразы,
     которые были на момент его написания, и подгоняет их под слова на
     экране («username», а не «login»); новая фраза без него превращалась
     в общее «The request was not accepted.» — теперь берётся с сервера. */
  function serverText(detail, status, detailEn) {
    var text = String(detail);
    /* «по-русски · in English» — каждой странице её половина. */
    var cut = text.indexOf(' · ');
    if (cut > 0 && CYR.test(text.slice(0, cut)) && !CYR.test(text.slice(cut + 3))) {
      return lang === 'en' ? text.slice(cut + 3) : text.slice(0, cut);
    }
    if (lang !== 'en' || !CYR.test(text)) return text;
    var key = text.trim();
    if (Object.prototype.hasOwnProperty.call(SERVER_EN, key)) return SERVER_EN[key];
    for (var i = 0; i < SERVER_EN_START.length; i++) {
      if (key.indexOf(SERVER_EN_START[i][0]) === 0) return SERVER_EN_START[i][1];
    }
    if (detailEn && !CYR.test(detailEn)) return detailEn;
    return STATUS_EN[status] || (status >= 500 ? 'Server error ' + status + '.' : 'Error ' + status + '.');
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
    /* Подписи для чтения с экрана и всплывающие подсказки — тоже на
       языке сайта: data-ru-aria / data-en-aria и data-ru-title / data-en-title. */
    document.querySelectorAll('[data-' + lang + '-aria]').forEach(function (n) {
      n.setAttribute('aria-label', n.getAttribute('data-' + lang + '-aria'));
    });
    document.querySelectorAll('[data-' + lang + '-title]').forEach(function (n) {
      n.setAttribute('title', n.getAttribute('data-' + lang + '-title'));
    });
    /* И подпись картинки (alt) — например, у QR-кода входа по коду. */
    document.querySelectorAll('[data-' + lang + '-alt]').forEach(function (n) {
      n.setAttribute('alt', n.getAttribute('data-' + lang + '-alt'));
    });
    /* Выбранная кнопка языка отмечена не только цветом: aria-pressed
       говорит диктору, какой язык сейчас включён. */
    document.querySelectorAll('.lang').forEach(function (b) {
      var on = b.dataset.setLang === lang;
      b.classList.toggle('on', on);
      if (b.dataset.setLang) b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
    rememberLang(lang);
    document.dispatchEvent(new CustomEvent('langchange', { detail: lang }));
  }

  /* Один обработчик на все кнопки ЯЗЫКА САЙТА — на странице входа и в
     кабинете. Через делегирование — потому что кнопки есть и в разметке,
     которая появляется позже.

     Вошедшему выбор сразу уходит в аккаунт (ui_lang): язык сайта — его
     личная настройка, и на другом устройстве он должен быть тем же.
     Гостю и до входа — только в этот браузер. */
  /* Язык, который человек нажал ДО входа (на странице входа). Нужен,
     чтобы после входа выбор не пропал: раньше, если в аккаунте уже был
     сохранён другой язык, страница после входа молча переключалась
     на него — нажал EN, вошёл, получил русский. */
  var pickedBeforeLogin = '';
  function pickedLang() { return pickedBeforeLogin; }

  document.addEventListener('click', function (e) {
    var b = e.target.closest ? e.target.closest('.lang[data-set-lang]') : null;
    if (!b || b.disabled) return;
    e.preventDefault();
    applyLang(b.dataset.setLang);
    if (me && me.role !== 'guest' && me.kind !== 'anon') {
      api.post('/api/me/settings', { ui_lang: lang }).then(function (r) {
        if (r && r.settings && me) me.settings = r.settings;
      }).catch(function () { /* не сохранилось — останется в браузере */ });
    } else {
      pickedBeforeLogin = lang;
    }
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
    if (lang === 'en' && (row.title_eng || row.title_en)) return row.title_eng || row.title_en;
    return row.title || row.title_en || '';
  }

  /* Название карточки в «Моём списке» (fix-21). Одна карточка на аниме,
     на каком бы языке его ни смотрели, — и имя у неё на текущем языке:
     латинское, если по-английски сайт или аниме, иначе русское. Нет
     нужного — какое есть. */
  function listName(row) {
    if (!row) return '';
    if ((lang === 'en' || animeSrcLang() === 'en') && row.title_en) return row.title_en;
    return row.title || row.title_en || '';
  }

  /* Язык САЙТА: меню, кнопки, названия и описания из справочника. */
  function langCode() { return lang; }

  /* Язык АНИМЕ: озвучка и субтитры в плеере. Отдельная настройка.

     Раньше их был один: переключил меню на английский — и поиск уходил
     к англоязычному источнику. Человек, которому удобнее английские
     кнопки, но нужна русская озвучка, так смотреть не мог вовсе, а
     остальные думали, что кнопка «EN» переключает плеер.

     Всё, что ходит к источникам видео (/api/search, /api/resolve,
     /api/where, /api/subs, /api/videos, /api/sources), шлёт animeLang(),
     а не langCode(). Описания и каталог (/api/about, /api/find) — наоборот.

     Вариантов три: ru — русские озвучки, endub — английский дубляж,
     ensub — японская дорожка с английским текстом. Сервер проверяет то
     же самое сам (anime.ANIME_LANGS). «en» — старое имя ensub. */
  var ANIME_LANGS = ['ru', 'endub', 'ensub'];
  var ANIME_KEY = 'animedik.anime_lang';
  /* Язык аниме только для этой страницы (fix-21) — хентай, которого на
     вашем языке нет: плеер открывает его там, где он есть, а настройку
     не трогает. Ставит страница просмотра (watch.js), снимает любой
     явный выбор языка (setAnimeLang). */
  var pageAnime = '';
  function setPageAnimeLang(code) { pageAnime = ANIME_LANGS.indexOf(code) >= 0 ? code : ''; }
  function pageAnimeLang() { return pageAnime; }
  function animeLang() {
    if (pageAnime) return pageAnime;
    var want = (me && me.settings && me.settings.anime_lang) || '';
    if (!want) {
      try { want = localStorage.getItem(ANIME_KEY) || ''; } catch (e) { want = ''; }
    }
    if (want === 'en') want = 'ensub';
    return ANIME_LANGS.indexOf(want) >= 0 ? want : 'ru';
  }
  /* Язык источников для выбора: у обоих английских вариантов источник
     один, и всё, что сравнивает «наш ли это источник», смотрит сюда. */
  function animeSrcLang() { return animeLang() === 'ru' ? 'ru' : 'en'; }
  /* Что открывать первым: озвучку или текст поверх оригинала. */
  function animeTrack() { return animeLang() === 'ensub' ? 'sub' : 'dub'; }
  function setAnimeLang(code) {
    if (ANIME_LANGS.indexOf(code) < 0) return Promise.resolve(false);
    pageAnime = '';
    try { localStorage.setItem(ANIME_KEY, code); } catch (e) { /* переживём */ }
    var done = Promise.resolve(true);
    if (me && me.role !== 'guest') {
      /* Помним выбор сразу, не дожидаясь сервера: страница просмотра
         тут же спрашивает источники уже под новый язык. */
      if (me.settings) me.settings.anime_lang = code;
      done = api.post('/api/me/settings', { anime_lang: code }).then(function (r) {
        if (r && r.settings && me) me.settings = r.settings;
        return true;
      });
    }
    document.dispatchEvent(new CustomEvent('animelangchange', { detail: code }));
    return done;
  }

  /* Источник видео по умолчанию — для языка АНИМЕ, не сайта.

     Источники говорят каждый на своём: русские дают русские озвучки,
     англоязычный — английские субтитры и дубляж. */
  /* Пустая строка — «выбери сам»: сервер берёт основной источник для
     языка аниме (anime.default_source), а страница просмотра сверяется со
     списком /api/sources и берёт первый доступный. Своих имён источников
     у страницы нет: какие есть — знает только сервер. */
  function defaultSource() { return ''; }

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
    cm: ['реклама', 'commercial'], tv_short: ['короткий сериал', 'short series']
  };

  /* Карточки франшиз в поиске долго приходили только с `kind_ru` — без
     сырой метки. Тогда KINDS[row.kind] не находил ничего, и на
     английском под «Jujutsu Kaisen» стояло «сериал». Поэтому ищем пару
     ещё и по русской подписи: она тоже однозначно указывает на метку. */
  function kindPair(row) {
    var raw = String(row.kind || '');
    /* AniList присылает метки большими буквами: TV, MOVIE, TV_SHORT. */
    var pair = KINDS[raw.toLowerCase()];
    if (pair) return pair;
    var ru = row.kind_ru || raw;
    for (var k in KINDS) {
      if (Object.prototype.hasOwnProperty.call(KINDS, k) && KINDS[k][0] === ru) return KINDS[k];
    }
    return null;
  }

  function kindName(row) {
    if (!row) return '';
    var pair = kindPair(row);
    if (pair) return lang === 'en' ? pair[1] : pair[0];
    /* Метка незнакомая — показываем то, что прислал сервер. Но русское
       слово на английской странице хуже, чем ничего: тогда подписи нет. */
    if (lang === 'en') {
      var raw = String(row.kind || '');
      return raw && !/[а-яё]/i.test(raw) ? raw : '';
    }
    return row.kind_ru || row.kind || '';
  }

  /* Жанр на языке сайта.

     Источники видео и часть справочника присылают жанры только
     по-русски, отдельного английского поля у них нет. Раньше словарь
     ниже жил в index.js и переводил жанры только в «Рандоме», а «Итоги
     года» на английской странице показывали «Экшен, Сёнэн, Фэнтези».
     Теперь он здесь, общий для всех страниц.

     Незнакомый русский жанр на английской странице не показываем вовсе
     (пустая строка): русское слово среди английских выглядит поломкой,
     а без одного жанра список всё равно понятен. На русской странице и
     для латинских имён — как есть. */
  var EN_GENRES = {
    'экшен': 'Action', 'приключения': 'Adventure', 'комедия': 'Comedy',
    'драма': 'Drama', 'этти': 'Ecchi', 'фэнтези': 'Fantasy', 'ужасы': 'Horror',
    'махо-сёдзё': 'Mahou Shoujo', 'меха': 'Mecha', 'музыка': 'Music',
    'детектив': 'Mystery', 'психологическое': 'Psychological', 'психологический': 'Psychological',
    'романтика': 'Romance', 'фантастика': 'Sci-Fi', 'повседневность': 'Slice of Life',
    'спорт': 'Sports', 'мистика': 'Supernatural', 'сверхъестественное': 'Supernatural',
    'триллер': 'Thriller', 'школа': 'School', 'сёнен': 'Shounen', 'сёнэн': 'Shounen',
    'сёдзё': 'Shoujo', 'сэйнэн': 'Seinen', 'сейнен': 'Seinen', 'исторический': 'Historical',
    'военное': 'Military', 'самураи': 'Samurai', 'космос': 'Space', 'пародия': 'Parody',
    'игры': 'Games', 'гарем': 'Harem', 'магия': 'Magic', 'демоны': 'Demons',
    'вампиры': 'Vampire', 'боевые искусства': 'Martial Arts', 'супер сила': 'Super Power',
    'полиция': 'Police', 'машины': 'Cars', 'дзёсей': 'Josei', 'дзёсэй': 'Josei',
    'гурман': 'Gourmet', 'работа': 'Workplace', 'исекай': 'Isekai', 'исэкай': 'Isekai',
    'безумие': 'Dementia', 'детское': 'Kids', 'юри': 'Yuri', 'яой': 'Yaoi',
    'сёнен-ай': 'Shounen Ai', 'сёдзё-ай': 'Shoujo Ai', 'хентай': 'Hentai', 'эротика': 'Erotica'
  };

  function genreName(g) {
    g = String(g || '').trim();
    if (lang !== 'en' || !CYR.test(g)) return g;
    return EN_GENRES[g.toLowerCase()] || '';
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
  /* Обещание открытого окна и ответ «отмена» для него. Нужны, когда
     поверх одного окна открывают другое: раньше первое просто
     убиралось со страницы, а его обещание не выполнялось никогда. Тот,
     кто ждал ответа, ждал вечно: строка части в окне франшизы так и
     оставалась «занятой» (…), кнопка «В планы» — серой, пока не
     перезагрузишь страницу. Теперь вытесненное окно отвечает «отмена». */
  var dialogPending = null;
  /* Где стоял фокус до окна — туда он и вернётся, когда окно закроют.
     Без этого после «Отмена» фокус терялся в начале страницы, и с
     клавиатуры приходилось заново идти табом до нужного места. */
  var dialogOpener = null;
  var dialogSeq = 0;

  function closeDialog(answer, resolve) {
    if (!dialogBox) return;
    var box = dialogBox;
    dialogBox.remove();
    dialogBox = null;
    document.removeEventListener('keydown', dialogKeys, true);
    var pending = dialogPending;
    dialogPending = null;
    if (resolve) {
      resolve(answer);
      var back = dialogOpener;
      dialogOpener = null;
      if (back && back.focus && document.body.contains(back) &&
          (!document.activeElement || document.activeElement === document.body || box.contains(document.activeElement))) {
        try { back.focus(); } catch (e) { /* не вышло — не беда */ }
      }
    } else if (pending) {
      /* Окно вытеснили новым: старое отвечает «отмена». */
      pending.resolve(pending.cancel);
    }
  }

  var dialogEscape = null;
  /* Esc принадлежит самому верхнему окну. Раньше окно закрывалось, но
     нажатие летело дальше — до общего обработчика страницы, и тот
     закрывал заодно кабинет, из которого это окно открыли. Нажал «Отмена»
     клавишей — и потерял всё, что успел поменять в настройках.
     Слушаем на фазе захвата, поэтому успеваем первыми и гасим событие.

     Tab и Shift+Tab ходят по кругу внутри окна: окно модальное, и
     уходить фокусом на страницу под затемнением незачем — там ничего
     не нажмёшь мышью, а с клавиатуры получалось. */
  function dialogKeys(e) {
    if (e.key === 'Escape' && dialogEscape) {
      e.preventDefault();
      e.stopPropagation();
      dialogEscape();
      return;
    }
    if (e.key === 'Tab' && dialogBox) {
      var all = Array.prototype.filter.call(
        dialogBox.querySelectorAll('button, input, a[href], textarea, select'),
        function (n) { return !n.disabled && n.getClientRects().length > 0; });
      if (!all.length) return;
      var first = all[0], last = all[all.length - 1];
      var inside = dialogBox.contains(document.activeElement);
      if (e.shiftKey && (document.activeElement === first || !inside)) {
        e.preventDefault(); last.focus();
      } else if (!e.shiftKey && (document.activeElement === last || !inside)) {
        e.preventDefault(); first.focus();
      }
    }
  }

  /* Общее начало обоих окон: убрать прежнее, запомнить, откуда пришли,
     и подписать окно для экранного диктора — заголовок и текст
     связываются с окном через aria-labelledby и aria-describedby. */
  function dialogStart(resolve, cancel) {
    if (!dialogBox) dialogOpener = document.activeElement;
    closeDialog(null, null);
    dialogPending = { resolve: resolve, cancel: cancel };
    dialogSeq++;
    var veil = el('div', 'veil dialog show');
    var box = el('div', 'dbox');
    box.setAttribute('role', 'dialog');
    box.setAttribute('aria-modal', 'true');
    veil.appendChild(box);
    return { veil: veil, box: box, id: 'dlg' + dialogSeq, at: Date.now() };
  }

  /* Щелчок мимо окна в первые мгновения после открытия — не «отмена».

     Двойной щелчок по кнопке с подтверждением («×» на карточке, «Удалить»
     в админке) открывал окно первым щелчком, а второй — приходился уже
     на затемнение поверх кнопки и тут же закрывал окно как «отмену».
     Окно мелькало и пропадало, кнопка выглядела сломанной. Первые
     полсекунды затемнение щелчки не считает. */
  var DIALOG_GRACE_MS = 450;
  function tooEarly(d) { return Date.now() - d.at < DIALOG_GRACE_MS; }

  function dialogHead(d, title, text, pre) {
    if (title) {
      var h = el('h3', null, title);
      h.id = d.id + '-t';
      d.box.appendChild(h);
      d.box.setAttribute('aria-labelledby', h.id);
    }
    if (text) {
      var p = el('p', null, text);
      p.id = d.id + '-d';
      if (pre) p.style.whiteSpace = 'pre-wrap';
      d.box.appendChild(p);
      d.box.setAttribute('aria-describedby', p.id);
      if (!title) d.box.setAttribute('aria-labelledby', p.id);
    }
  }

  /* opts: {title, text, list, ok, cancel, danger, password}
     list — строки, которые надо показать списком: например, что именно
     удалится. Склеенные в одну фразу, пять названий не прочитать. */
  function ask(opts) {
    opts = opts || {};
    return new Promise(function (resolve) {
      var d = dialogStart(resolve, opts.password ? null : false);
      var veil = d.veil, box = d.box;

      dialogHead(d, opts.title, opts.text, false);
      if (opts.list && opts.list.length) {
        var ul = el('ul', 'dlist');
        opts.list.forEach(function (line) { ul.appendChild(el('li', null, line)); });
        box.appendChild(ul);
      }

      var field = null;
      if (opts.password) {
        field = el('input', 'inp');
        field.type = 'password';
        field.autocomplete = 'current-password';
        field.style.width = '100%';
        /* Подписи у поля нет — только текст окна выше. Диктору
           нужно имя самого поля. */
        field.setAttribute('aria-label', t('Пароль', 'Password'));
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
        if (e.target === veil && !tooEarly(d)) done(opts.password ? null : false);
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
      var d = dialogStart(resolve, null);
      var veil = d.veil, box = d.box;
      dialogHead(d, title, text, true);
      var row = el('div', 'drow');
      var okBtn = el('button', 'wide', t('Понятно', 'Got it'));
      okBtn.type = 'button';
      row.appendChild(okBtn);
      box.appendChild(row);
      document.body.appendChild(veil);
      dialogBox = veil;

      function done() { closeDialog(null, resolve); }
      dialogEscape = done;
      document.addEventListener('keydown', dialogKeys, true);
      okBtn.addEventListener('click', done);
      veil.addEventListener('click', function (e) { if (e.target === veil && !tooEarly(d)) done(); });
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
    цветВкладки();
  }

  /* Цвет строки браузера на телефоне (meta theme-color) — под фон
     темы. Иначе у «Бумаги» сверху висела бы тёмная полоса. Берём
     готовое значение из стилей, а не держим второй список цветов. */
  function цветВкладки() {
    var м = document.querySelector('meta[name="theme-color"]');
    if (!м) return;
    var фон = getComputedStyle(document.documentElement).getPropertyValue('--bg').trim();
    if (/^#[0-9a-f]{3,8}$/i.test(фон)) м.setAttribute('content', фон);
  }

  /* Какой сейчас праздник по календарю.

     Окна намеренно широкие и без географии. Новый год — 1 января почти
     везде, где живут по обычному календарю; разъезжается Рождество:
     25 декабря у одних, 7 января у других, плюс старый Новый год 14-го.
     Одно окно с 18 декабря по 15 января накрывает всё это разом, и
     определять страну не нужно вовсе.

     Хэллоуин — весь октябрь и до 2 ноября включительно: раньше окно
     открывалось только 24-го, и праздника почти не было видно. Сакура —
     к цветению, с 25 марта по 15 апреля. */
  function holidayOn(дата) {
    var d = дата || new Date();
    var м = d.getMonth() + 1, ч = d.getDate();
    if ((м === 12 && ч >= 18) || (м === 1 && ч <= 15)) return 'newyear';
    if (м === 10 || (м === 11 && ч <= 2)) return 'halloween';
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

  /* ==================================================================
     Хэллоуин: ночное небо с луной

     Сцена собрана из слоёв, снизу вверх (все — в нижнем слое праздника,
     под содержимым):
       небо   — заливка .zaliv (градиент в app.css, цвета от темы);
       звёзды — .zvezdy, несколько тусклых точек градиентами в app.css;
       луна   — luna(): полный диск целиком в верхнем углу, со свечением;
       даль   — kladbischeDal(): холм с часовней, крестами и деревцами,
                светлее и мягче переднего плана;
       туман  — .tuman (между далью и передним планом и ещё один у
                самого края), медленно плывёт;
       кладбище — kladbische(): кривые голые деревья, кованая ограда,
                надгробия, кресты и пара светящихся тыкв.

     Всё рисуется здесь узлами SVG и градиентами — ни одной картинки.
     Цвета — только переменные --hall-* (их задаёт app.css под каждую
     тему), поэтому на «Бумаге» та же сцена выходит светлой сумеречной,
     а на тёмных темах — ночной.

     Случайность у деревьев — своя, с постоянным зерном: деревья должны
     быть одними и теми же при каждом открытии страницы, а не «расти»
     заново. */
  function зерно(s) {
    /* Парк — Миллер: s · 16807 по модулю 2³¹−1. Умещается в обычное
       число без потерь, ничего новее ES5 не нужно. */
    s = (s % 2147483646) + 1;
    return function () {
      s = (s * 16807) % 2147483647;
      return (s - 1) / 2147483646;
    };
  }

  /* Кривое голое дерево. Ветвится рекурсивно; каждая ветка — дуга
     (квадратичная кривая с изгибом вбок), поэтому дерево выходит
     скрюченным, а не «ёлочкой» из прямых. Ветки одной толщины
     складываются в один путь: на дерево — пять-шесть узлов, а не сотня. */
  function derevo(x, земля, высота, толщина, сид, цвет) {
    var r = зерно(сид);
    var пути = {};
    function ветка(x1, y1, угол, длина, т, глуб) {
      var x2 = x1 + Math.cos(угол) * длина, y2 = y1 + Math.sin(угол) * длина;
      var изгиб = (r() - 0.5) * длина * 0.55;
      var cx = (x1 + x2) / 2 + Math.cos(угол + Math.PI / 2) * изгиб;
      var cy = (y1 + y2) / 2 + Math.sin(угол + Math.PI / 2) * изгиб;
      var к = т.toFixed(1);
      пути[к] = (пути[к] || '') + 'M' + x1.toFixed(1) + ' ' + y1.toFixed(1) +
                'Q' + cx.toFixed(1) + ' ' + cy.toFixed(1) + ' ' + x2.toFixed(1) + ' ' + y2.toFixed(1);
      if (глуб <= 0 || т < 1) return;
      var n = глуб >= 4 ? 2 : (r() < 0.45 ? 3 : 2);
      for (var i = 0; i < n; i++) {
        var веер = (i - (n - 1) / 2) * (0.5 + r() * 0.35);
        ветка(x2, y2, угол + веер + (r() - 0.5) * 0.5,
              длина * (0.6 + r() * 0.2), т * 0.64, глуб - 1);
      }
      /* Сучок — короткая боковая веточка: дерево «узловатее». */
      if (глуб >= 2 && r() < 0.5) {
        ветка(x1 + (x2 - x1) * 0.5, y1 + (y2 - y1) * 0.5,
              угол + (r() < 0.5 ? -1 : 1) * (0.9 + r() * 0.4), длина * 0.35, т * 0.45, 0);
      }
    }
    var наклон = (r() - 0.5) * 0.22;
    ветка(x, земля + 2, -Math.PI / 2 + наклон, высота * 0.36, толщина, 6);
    var g = [];
    /* Корни: ствол расширяется к земле. */
    g.push(узел('path', {
      d: 'M' + (x - толщина * 1.6) + ' ' + (земля + 3) + 'Q' + (x - толщина * 0.4) + ' ' +
         (земля - толщина * 0.6) + ' ' + (x - толщина * 0.45) + ' ' + (земля - толщина * 2.6) +
         'L' + (x + толщина * 0.45) + ' ' + (земля - толщина * 2.6) + 'Q' + (x + толщина * 0.5) +
         ' ' + (земля - толщина * 0.5) + ' ' + (x + толщина * 1.8) + ' ' + (земля + 3) + 'Z',
      fill: цвет
    }));
    Object.keys(пути).forEach(function (к) {
      g.push(узел('path', { d: пути[к], 'stroke-width': к }));
    });
    return узел('g', { stroke: цвет, fill: 'none', 'stroke-linecap': 'round' }, g);
  }

  /* Надгробие с закруглённым верхом, чуть накренённое. */
  function plita(x, земля, w, h, крен, цвет) {
    return узел('path', {
      d: 'M' + x + ' ' + (земля + 4) + 'v-' + (h - w / 2) +
         'a' + (w / 2) + ' ' + (w / 2) + ' 0 0 1 ' + w + ' 0v' + (h - w / 2) + 'z',
      fill: цвет, transform: 'rotate(' + крен + ' ' + (x + w / 2) + ' ' + земля + ')'
    });
  }

  /* Крест; с кольцом — кельтский. */
  function krest(x, земля, h, крен, кольцо, цвет) {
    var t = Math.max(4, h * 0.12), плечо = h * 0.62, верх = земля - h;
    var д = [узел('rect', { x: x - t / 2, y: верх, width: t, height: h + 4, rx: 1 }),
             узел('rect', { x: x - плечо / 2, y: верх + h * 0.2, width: плечо, height: t, rx: 1 })];
    if (кольцо) {
      д.push(узел('circle', { cx: x, cy: верх + h * 0.2 + t / 2, r: h * 0.17, fill: 'none',
                              stroke: цвет, 'stroke-width': t * 0.55 }));
    }
    return узел('g', { fill: цвет, transform: 'rotate(' + крен + ' ' + x + ' ' + земля + ')' }, д);
  }

  /* Обелиск — высокий узкий камень с острым верхом. */
  function obelisk(x, земля, w, h, цвет) {
    return узел('path', {
      d: 'M' + (x - w / 2 - 3) + ' ' + (земля + 4) + 'v-8h3l' + (w * 0.12) + ' -' + (h - 22) +
         'l' + (w * 0.38) + ' -14l' + (w * 0.38) + ' 14l' + (w * 0.12) + ' ' + (h - 22) +
         'h3v8z',
      fill: цвет
    });
  }

  /* Кованая ограда: прутья с остриями, две перекладины и столбы с
     шарами. Один путь на всю секцию. */
  function ograda(x, земля, ширина, h, крен, цвет) {
    var d = '';
    var шаг = 12;
    for (var i = 0; i <= ширина; i += шаг) {
      var столб = i % (шаг * 8) === 0;
      var w = столб ? 6 : 2.4;
      var hh = столб ? h + 6 : h;
      d += 'M' + (x + i - w / 2) + ' ' + (земля + 4) + 'v-' + (hh + 4) + 'h' + w + 'v' + (hh + 4) + 'z';
      if (столб) {
        d += 'M' + (x + i) + ' ' + (земля - hh - 12) + 'a5 5 0 1 0 0.01 0z';
      } else {
        d += 'M' + (x + i - 3.6) + ' ' + (земля - hh) + 'l3.6 -8l3.6 8z';
      }
    }
    d += 'M' + x + ' ' + (земля - h + 8) + 'h' + ширина + 'v3h-' + ширина + 'z';
    d += 'M' + x + ' ' + (земля - 12) + 'h' + ширина + 'v3h-' + ширина + 'z';
    return узел('path', {
      d: d, fill: цвет, transform: 'rotate(' + крен + ' ' + x + ' ' + земля + ')'
    });
  }

  /* Тыква-фонарь: оранжевая, с горящим лицом и мягким свечением. */
  function tykva(x, земля, r) {
    var y = земля - r * 0.74;
    return узел('g', {}, [
      узел('ellipse', { cx: x, cy: y, rx: r * 2.6, ry: r * 1.9, fill: 'url(#hl-ogon)' }),
      узел('ellipse', { cx: x, cy: y, rx: r, ry: r * 0.8, fill: 'var(--hall-tykva)' }),
      узел('path', { d: 'M' + (x - r * 0.35) + ' ' + y + 'c0 -' + (r * 0.75) + ' ' + (r * 0.7) +
                        ' -' + (r * 0.75) + ' ' + (r * 0.7) + ' 0', fill: 'none',
                     stroke: 'var(--hall-temno)', 'stroke-width': r * 0.08, opacity: 0.45 }),
      узел('rect', { x: x - r * 0.1, y: y - r * 1.06, width: r * 0.2, height: r * 0.34, rx: r * 0.06,
                     fill: 'var(--hall-temno)' }),
      узел('path', {
        d: 'M' + (x - r * 0.48) + ' ' + (y - r * 0.12) + 'l' + (r * 0.2) + ' -' + (r * 0.28) +
           'l' + (r * 0.2) + ' ' + (r * 0.28) + 'zM' + (x + r * 0.08) + ' ' + (y - r * 0.12) +
           'l' + (r * 0.2) + ' -' + (r * 0.28) + 'l' + (r * 0.2) + ' ' + (r * 0.28) + 'z' +
           'M' + (x - r * 0.5) + ' ' + (y + r * 0.18) + 'q' + (r * 0.5) + ' ' + (r * 0.42) + ' ' +
           r + ' 0l-' + (r * 0.2) + ' ' + (r * 0.1) + 'l-' + (r * 0.12) + ' -' + (r * 0.1) +
           'l-' + (r * 0.16) + ' ' + (r * 0.12) + 'l-' + (r * 0.16) + ' -' + (r * 0.12) +
           'l-' + (r * 0.12) + ' ' + (r * 0.1) + 'z',
        fill: 'var(--hall-ogon)'
      })
    ]);
  }

  /* Передний план: земля, деревья, ограда, надгробия, тыквы.

     Рисунок задуман шириной 2400 и высотой 260, но на экран он больше
     не ложится одной картинкой: земля тянется по ширине окна, а
     предметы расставляются заново (см. «Раскладка сцены под окно»).
     Поэтому каждый предмет — отдельно, со своим местом в рисунке и
     важностью: на узком экране первыми уходят те, что поменьше. Середина
     собрана так же плотно, как края, — её видно и на телефоне. */
  function kladbische() {
    var Т = 'var(--hall-temno)', К = 'var(--hall-kamen)';
    var ч = [
      { как: узел('defs', {}, [
        узел('radialGradient', { id: 'hl-ogon' }, [
          узел('stop', { offset: '0', style: 'stop-color:var(--hall-ogon);stop-opacity:.42' }),
          узел('stop', { offset: '1', style: 'stop-color:var(--hall-ogon);stop-opacity:0' })
        ])
      ]) },
      // Земля — пологие бугры по всей ширине.
      { пласт: узел('path', {
        d: 'M0 262V226C140 214 260 220 380 228S640 236 760 224 1000 206 1130 214 1360 232 1500 226' +
           ' 1760 208 1900 216 2160 234 2280 226L2400 218V262Z', fill: Т
      }), имя: 'земля' }
    ];
    function п(x, база, важн, рис) { ч.push(предмет(x, база, важн, рис, 'земля')); }
    // Дальние камни — чуть светлее (К): так у сцены появляется глубина.
    п(300, 226, 1, function (x) { return plita(x, 226, 30, 46, -6, К); });
    п(905, 222, 2, function (x) { return krest(x, 222, 40, 5, false, К); });
    п(1290, 224, 2, function (x) { return plita(x, 224, 26, 40, 4, К); });
    п(1985, 216, 2, function (x) { return krest(x, 216, 44, -4, true, К); });
    // Деревья.
    п(150, 228, 3, function (x) { return derevo(x, 228, 250, 13, 7, Т); });
    п(560, 230, 4, function (x) { return derevo(x, 230, 236, 12, 21, Т); });
    п(1365, 230, 2, function (x) { return derevo(x, 230, 168, 8, 5, Т); });
    п(1885, 214, 4, function (x) { return derevo(x, 214, 248, 13, 13, Т); });
    п(2290, 226, 2, function (x) { return derevo(x, 226, 190, 10, 33, Т); });
    // Ограда двумя секциями, вторая слегка завалилась.
    п(640, 232, 3, function (x) { return ograda(x, 232, 300, 44, 0, Т); });
    п(1560, 226, 3, function (x) { return ograda(x, 226, 240, 40, -1.2, Т); });
    // Ближние надгробия и кресты.
    п(400, 228, 2, function (x) { return plita(x, 228, 38, 60, 5, Т); });
    п(470, 232, 2, function (x) { return krest(x, 232, 58, -3, false, Т); });
    п(770, 230, 3, function (x) { return plita(x, 230, 44, 70, -3, Т); });
    п(1060, 214, 5, function (x) { return krest(x, 214, 66, 4, true, Т); });
    п(1130, 216, 5, function (x) { return plita(x, 216, 46, 62, -5, Т); });
    п(1225, 222, 5, function (x) { return obelisk(x, 222, 34, 92, Т); });
    п(1440, 232, 3, function (x) { return plita(x, 232, 40, 56, 7, Т); });
    п(1505, 230, 3, function (x) { return krest(x, 230, 52, -6, false, Т); });
    п(1990, 220, 3, function (x) { return plita(x, 220, 42, 64, -2, Т); });
    п(2085, 228, 2, function (x) { return krest(x, 228, 60, 3, false, Т); });
    п(2170, 232, 1, function (x) { return plita(x, 232, 34, 48, 6, Т); });
    // Тыквы — тёплые огоньки среди холодного.
    п(1175, 220, 5, function (x) { return tykva(x, 220, 13); });
    п(1690, 224, 3, function (x) { return tykva(x, 224, 10); });
    п(860, 232, 3, function (x) { return tykva(x, 232, 11); });
    /* Кладбище — тесное место: камням и кресту можно стоять почти
       вплотную, поэтому тесно = 0.45. */
    return сцена('kladbische', 260, 0.62, 0.45, ч);
  }

  /* Дальний план: холм с часовней, ряд крестиков и деревца. Светлее
     переднего (--hall-dal) — как будто за туманом. */
  function kladbischeDal() {
    var Д = 'var(--hall-dal)';
    var ч = [{ пласт: узел('path', {
      d: 'M0 300V214C220 196 420 200 600 208S900 190 1080 178 1400 172 1560 186 1900 214' +
         ' 2100 206 2300 196 2400 200V300Z', fill: Д
    }), имя: 'холм' }];
    function п(x, база, важн, рис) { ч.push(предмет(x, база, важн, рис, 'холм')); }
    // Часовня на холме: стены, двускатная крыша, шпиль с крестом. Арочное
    // окно часовни светится — кто-то там всё-таки есть.
    п(1222, 182, 6, function (x) {
      return узел('g', { transform: 'translate(' + (x - 1222) + ' 0)' }, [
        узел('path', {
          d: 'M1170 182v-44l26-22 26 22v44zM1196 116v-34M1189 90h14M1222 182v-30h52v30z' +
             'M1218 152l30-20 30 20z',
          fill: Д, stroke: Д, 'stroke-width': 4
        }),
        узел('path', { d: 'M1192 158v-8a4 4 0 0 1 8 0v8z', fill: 'var(--hall-okno)',
                       'fill-opacity': 0.75 })
      ]);
    });
    [[960, 190, 18], [1010, 186, 14], [1350, 178, 16], [1410, 182, 13], [1470, 186, 17],
     [700, 206, 14], [1700, 200, 15], [1760, 204, 12]].forEach(function (к) {
      п(к[0], к[1], 1, function (x) { return krest(x, к[1], к[2], 0, false, Д); });
    });
    п(420, 206, 2, function (x) { return derevo(x, 206, 150, 6, 41, Д); });
    п(830, 196, 2, function (x) { return derevo(x, 196, 120, 5, 43, Д); });
    п(1600, 192, 2, function (x) { return derevo(x, 192, 136, 6, 47, Д); });
    п(2060, 208, 2, function (x) { return derevo(x, 208, 150, 6, 53, Д); });
    return сцена('kladb-dal', 300, 0.7, 0.45, ч);
  }
  /* Луна: полный диск со свечением, мягкими «морями» и чуть темнее к
     краю — объёмная, а не плоский кружок. Виден целиком: стоит в
     верхнем углу, отступив от края окна (см. .luna в app.css). */
  function luna() {
    function стоп(o, цвет, прозр) {
      return узел('stop', { offset: o, style: 'stop-color:' + цвет + ';stop-opacity:' + прозр });
    }
    return узел('svg', { class: 'luna', viewBox: '0 0 200 200', 'aria-hidden': 'true' }, [
      узел('defs', {}, [
        узел('radialGradient', { id: 'hl-svet' }, [
          стоп('0.46', 'var(--hall-luna-svet)', 1), стоп('0.64', 'var(--hall-luna-svet)', 0.5),
          стоп('1', 'var(--hall-luna-svet)', 0)
        ]),
        узел('radialGradient', { id: 'hl-disk', cx: '0.42', cy: '0.38', r: '0.7' }, [
          стоп('0', 'var(--hall-luna-1)', 1), стоп('0.75', 'var(--hall-luna-2)', 1),
          стоп('1', 'var(--hall-luna-kray)', 1)
        ]),
        /* «Моря» — тёмные пятна с мягким, но заметным краем. */
        узел('radialGradient', { id: 'hl-more' }, [
          стоп('0', 'var(--hall-krater)', 1), стоп('0.62', 'var(--hall-krater)', 0.75),
          стоп('1', 'var(--hall-krater)', 0)
        ])
      ]),
      узел('circle', { cx: 100, cy: 100, r: 99, fill: 'url(#hl-svet)' }),
      узел('circle', { class: 'disk', cx: 100, cy: 100, r: 50, fill: 'url(#hl-disk)' }),
      узел('g', { opacity: 0.8 }, [
        узел('ellipse', { cx: 86, cy: 82, rx: 15, ry: 11, fill: 'url(#hl-more)',
                          transform: 'rotate(15 86 82)' }),
        узел('ellipse', { cx: 108, cy: 92, rx: 12, ry: 9, fill: 'url(#hl-more)' }),
        узел('ellipse', { cx: 117, cy: 112, rx: 17, ry: 11, fill: 'url(#hl-more)',
                          transform: 'rotate(-25 117 112)' }),
        узел('ellipse', { cx: 90, cy: 120, rx: 9, ry: 6, fill: 'url(#hl-more)' }),
        узел('ellipse', { cx: 72, cy: 101, rx: 6, ry: 8, fill: 'url(#hl-more)' }),
        // Мелкие кратеры — кружки с тонким тёмным ободком.
        узел('circle', { cx: 96, cy: 136, r: 3.4, fill: 'none', stroke: 'var(--hall-krater)',
                         'stroke-width': 1.2 }),
        узел('circle', { cx: 127, cy: 82, r: 2.6, fill: 'none', stroke: 'var(--hall-krater)',
                         'stroke-width': 1 }),
        узел('circle', { cx: 68, cy: 82, r: 2, fill: 'var(--hall-krater)' })
      ])
    ]);
  }

  /* ==================================================================
     Новый год и сакура: сцены тем же способом, что и Хэллоуин

     Устроены так же, слой за слоем, снизу вверх: небо (.zaliv), звёзды,
     луна, дальний план, дымка (.tuman t1), передний план, дымка у края
     (.tuman t2). Звёзды, луна и дымка — те же, что у Хэллоуина, и
     правила для них общие (место луны в поле справа, телефон, планшет,
     страница просмотра, «меньше движения»). Цвета этих общих кусков —
     переменные --hall-*: так они назывались, когда сцена была одна;
     каждый праздник задаёт их своими (см. app.css). Своё у праздника —
     только его даль и передний план (--ny-* и --sk-*).

     Новый год — зимняя ночь: синее небо со звёздами и северным сиянием,
     луна, заснеженные холмы с ельником и домиком (окна горят, из трубы
     дым), впереди — ели в снегу, у двух — тёплые огоньки, внизу сугробы.
     Снег идёт тот же, что и раньше (верхний слой).

     Сакура — весенние сумерки: розово-лиловое небо, бледная туманная
     луна (обородзуки), вдали — гора с белой шапкой, пагода и тории,
     впереди — цветущие деревья, фонарики на верёвке и каменный фонарь.
     Лепестки летят те же, что и раньше.

     Случайность — с постоянным зерном (зерно()), как у деревьев
     кладбища: картина одна и та же при каждом открытии. */

  /* Кружок одним куском пути: так сотню кружков кроны можно сложить
     в три пути, а не в сотню узлов. */
  function круг(cx, cy, r) {
    return 'M' + (cx - r).toFixed(1) + ' ' + cy.toFixed(1) + 'a' + r.toFixed(1) + ' ' +
           r.toFixed(1) + ' 0 1 0 ' + (2 * r).toFixed(1) + ' 0a' + r.toFixed(1) + ' ' +
           r.toFixed(1) + ' 0 1 0 ' + (-2 * r).toFixed(1) + ' 0z';
  }

  /* Радиальный градиент «свечение»: цвет в середине и в ноль к краю. */
  function свечение(id, цвет, сила) {
    return узел('radialGradient', { id: id }, [
      узел('stop', { offset: '0', style: 'stop-color:' + цвет + ';stop-opacity:' + сила }),
      узел('stop', { offset: '1', style: 'stop-color:' + цвет + ';stop-opacity:0' })
    ]);
  }

  /* Ель в снегу. Ярусы — треугольники с вогнутыми боками и рваным
     нижним краем; на каждом сверху — снежная шапка с волнистым краем.
     огни — у ели гирлянда: по провисающей дуге на ярус, тёплые
     огоньки мигают по очереди (класс ogonek, см. app.css). */
  function yolka(x, земля, h, сид, цвет, снег, огни) {
    var r = зерно(сид);
    var ярусов = h > 150 ? 5 : (h > 70 ? 4 : 3);
    var шаг = h * 0.86 / ярусов, высота = шаг * 1.55, полка = h * 0.36;
    var хвоя = '', шапки = '', g = [];
    g.push(узел('rect', { x: x - h * 0.035, y: земля - h * 0.12, width: h * 0.07,
                          height: h * 0.12 + 4, fill: цвет }));
    var дуги = [];
    for (var i = 0; i < ярусов; i++) {
      var низ = земля - h * 0.07 - i * шаг;
      var верх = i === ярусов - 1 ? земля - h : низ - высота;
      var hw = полка * (1 - i / ярусов * 0.72) * (0.92 + r() * 0.16);
      var th = низ - верх;
      // Бока чуть вогнуты, низ — зубцами вниз и вверх.
      хвоя += 'M' + x.toFixed(1) + ' ' + верх.toFixed(1) +
              'Q' + (x + hw * 0.32).toFixed(1) + ' ' + (верх + th * 0.62).toFixed(1) + ' ' +
              (x + hw).toFixed(1) + ' ' + (низ + 2).toFixed(1);
      var зубцов = 3 + Math.round(hw / 22);
      for (var k = 1; k <= зубцов; k++) {
        var px = x + hw - 2 * hw * k / зубцов;
        var mx = px + hw / зубцов;
        хвоя += 'L' + mx.toFixed(1) + ' ' + (низ - th * (0.08 + r() * 0.06)).toFixed(1) +
                'L' + px.toFixed(1) + ' ' + (низ + (k === зубцов ? 2 : r() * 4)).toFixed(1);
      }
      хвоя += 'Q' + (x - hw * 0.32).toFixed(1) + ' ' + (верх + th * 0.62).toFixed(1) + ' ' +
              x.toFixed(1) + ' ' + верх.toFixed(1) + 'Z';
      // Снежная шапка: от макушки яруса вниз по бокам, нижний край волной.
      var sy = верх + th * (0.55 + r() * 0.1), sw = hw * 0.68;
      шапки += 'M' + x.toFixed(1) + ' ' + (верх - 1).toFixed(1) +
               'Q' + (x + sw * 0.4).toFixed(1) + ' ' + (верх + th * 0.4).toFixed(1) + ' ' +
               (x + sw).toFixed(1) + ' ' + sy.toFixed(1);
      var волн = 3;
      for (var v = 1; v <= волн; v++) {
        var vx = x + sw - 2 * sw * v / волн;
        шапки += 'Q' + (vx + sw / волн).toFixed(1) + ' ' +
                 (sy - th * (0.16 + r() * 0.12)).toFixed(1) + ' ' +
                 vx.toFixed(1) + ' ' + (sy + (r() - 0.5) * th * 0.1).toFixed(1);
      }
      шапки += 'Q' + (x - sw * 0.4).toFixed(1) + ' ' + (верх + th * 0.4).toFixed(1) + ' ' +
               x.toFixed(1) + ' ' + (верх - 1).toFixed(1) + 'Z';
      if (огни && i < ярусов - 1) дуги.push([низ - th * 0.3, hw * 0.78]);
    }
    g.push(узел('path', { d: хвоя, fill: цвет }));
    g.push(узел('path', { d: шапки, fill: снег }));
    if (огни) {
      var свет = [], лампы = [];
      var цвета = ['var(--ny-ogon-1)', 'var(--ny-ogon-2)', 'var(--ny-ogon-3)'];
      var n = 0;
      дуги.forEach(function (д) {
        var y0 = д[0], w = д[1], штук = Math.max(3, Math.round(w / 11));
        for (var j = 0; j <= штук; j++) {
          var t = j / штук, lx = x - w + 2 * w * t;
          var ly = y0 + Math.sin(t * Math.PI) * w * 0.16 - (t - 0.5) * w * 0.12;
          свет.push(узел('circle', { cx: lx.toFixed(1), cy: ly.toFixed(1), r: 7,
                                     fill: 'url(#ny-ogon)' }));
          лампы.push(узел('circle', { class: 'ogonek o' + (n % 3), cx: lx.toFixed(1),
                                      cy: ly.toFixed(1), r: 2.1, fill: цвета[n % 3] }));
          n++;
        }
      });
      g.push(узел('g', { class: 'ogni-svet' }, свет));
      g.push(узел('g', {}, лампы));
    }
    return узел('g', { class: 'yolka' }, g);
  }

  /* Домик на холме: стены, крыша под снегом, труба с дымом, два тёплых
     окна и круглое окошко на чердаке. */
  function domik(x, земля) {
    var Д = 'var(--ny-dom)', С = 'var(--ny-krysha)', О = 'var(--ny-okno)';
    var g = [];
    g.push(узел('ellipse', { cx: x + 2, cy: земля - 26, rx: 74, ry: 46, fill: 'url(#ny-okno-svet)' }));
    // Стены и труба.
    g.push(узел('path', { d: 'M' + (x - 40) + ' ' + (земля + 3) + 'v-44h80v44z' +
                              'M' + (x + 18) + ' ' + (земля - 66) + 'v-22h12v34z', fill: Д }));
    // Крыша с напуском и снег на ней — толстой подушкой с наплывами.
    g.push(узел('path', { d: 'M' + (x - 50) + ' ' + (земля - 38) + 'L' + x + ' ' + (земля - 80) +
                              'L' + (x + 50) + ' ' + (земля - 38) + 'z', fill: Д }));
    g.push(узел('path', {
      d: 'M' + (x - 54) + ' ' + (земля - 36) + 'L' + x + ' ' + (земля - 84) + 'L' + (x + 54) + ' ' +
         (земля - 36) + 'q-4 5-10 2t-12 1-10-3-12 2q-4-14-10-24q-6 10-10 24q-6-4-12-2t-11-2-10 3z',
      fill: С
    }));
    g.push(узел('path', { d: 'M' + (x + 16) + ' ' + (земля - 89) + 'h16q1 -6-8 -6t-8 6z', fill: С }));
    // Окна: тёплый свет и тёмный переплёт.
    [[x - 30, земля - 30], [x + 10, земля - 30]].forEach(function (о) {
      g.push(узел('rect', { x: о[0], y: о[1], width: 18, height: 16, rx: 1.5, fill: О }));
      g.push(узел('path', { d: 'M' + (о[0] + 9) + ' ' + о[1] + 'v16M' + о[0] + ' ' + (о[1] + 8) + 'h18',
                            stroke: Д, 'stroke-width': 2 }));
    });
    g.push(узел('circle', { cx: x, cy: земля - 54, r: 6, fill: О, 'fill-opacity': 0.85 }));
    // Сугроб у стены.
    g.push(узел('path', { d: 'M' + (x - 58) + ' ' + (земля + 4) + 'q16-12 36-6t40-2 40 8z', fill: 'var(--ny-dal)' }));
    // Дым — три клуба, плывут вверх и тают по очереди.
    var дым = [];
    for (var i = 0; i < 3; i++) {
      дым.push(узел('circle', { class: 'dym d' + i, cx: x + 24, cy: земля - 98, r: 9 + i * 2,
                                fill: 'url(#ny-dym-g)' }));
    }
    g.push(узел('g', {}, дым));
    return узел('g', { class: 'domik' }, g);
  }

  /* Новый год, дальний план: два ряда заснеженных холмов, ельник по
     гребням и домик посередине (его видно и на телефоне). */
  function zimaDal() {
    var ч = [
      { как: узел('defs', {}, [свечение('ny-okno-svet', 'var(--ny-okno)', 0.34),
                               свечение('ny-dym-g', 'var(--ny-dym)', 1)]) },
      { пласт: узел('path', {
        d: 'M0 300V176C180 150 330 160 480 176S760 150 900 158 1150 182 1300 170 1560 132 1720 146' +
           ' 2020 182 2180 168 2340 150 2400 156V300Z', fill: 'var(--ny-dal-2)'
      }), имя: 'гребень' }
    ];
    // Ельник на дальнем гребне — мелкий, почти в цвет неба.
    [[120, 168, 34], [168, 164, 46], [215, 168, 30], [610, 168, 40], [660, 164, 52], [705, 166, 34],
     [1620, 140, 44], [1665, 136, 56], [1712, 140, 40], [1760, 146, 32], [2230, 162, 42],
     [2275, 158, 54], [2320, 154, 38]].forEach(function (е, i) {
      ч.push(предмет(е[0], е[1], 1, function (x) {
        return yolka(x, е[1], е[2], 300 + i, 'var(--ny-el-dal-2)', 'var(--ny-dal-2)', false);
      }, 'гребень'));
    });
    ч.push({ пласт: узел('path', {
      d: 'M0 300V214C200 198 380 204 560 214S860 206 1020 196 1200 186 1300 188 1500 200 1640 208' +
         ' 1900 222 2100 212 2300 200 2400 204V300Z', fill: 'var(--ny-dal)'
    }), имя: 'холмы' });
    // Ельник ближе — темнее, со снегом на лапах.
    [[380, 210, 70], [440, 212, 52], [960, 200, 64], [1012, 196, 82], [1062, 194, 58],
     [1398, 194, 72], [1450, 198, 56], [1880, 218, 66], [1936, 220, 50], [2080, 214, 60]
    ].forEach(function (е, i) {
      ч.push(предмет(е[0], е[1], 2, function (x) {
        return yolka(x, е[1], е[2], 400 + i, 'var(--ny-el-dal)', 'var(--ny-dal)', false);
      }, 'холмы'));
    });
    ч.push(предмет(1220, 188, 6, function (x) { return domik(x, 188); }, 'холмы'));
    return сцена('scena-dal zima-dal', 300, 0.7, 0.4, ч);
  }

  /* Новый год, передний план: сугробы волнами и ели в снегу. Как и у
     кладбища, середина (её видно на телефоне) собрана так же плотно,
     как края; у двух елей — гирлянда. Сугробы — поверх комлей. */
  function zima() {
    var Е = 'var(--ny-el)', С = 'var(--ny-el-sneg)';
    var ч = [{ как: узел('defs', {}, [свечение('ny-ogon', 'var(--ny-ogon-1)', 0.5)]) }];
    [[150, 232, 236, 61, false, 3], [262, 236, 120, 62, false, 1], [540, 236, 200, 63, false, 3],
     [660, 238, 90, 64, false, 1], [1040, 230, 172, 65, true, 5], [1150, 236, 84, 66, false, 2],
     [1420, 234, 150, 67, false, 4], [1530, 238, 76, 68, false, 2], [1880, 228, 240, 69, true, 4],
     [2010, 236, 104, 70, false, 2], [2290, 232, 196, 71, false, 3]].forEach(function (е) {
      ч.push(предмет(е[0], е[1], е[5], function (x) {
        return yolka(x, е[1], е[2], е[3], Е, С, е[4]);
      }, 'сугроб'));
    });
    // Сугробы — поверх комлей, с блеском по гребню.
    var гребень = 'M0 232C120 218 220 226 330 234S560 222 700 230 900 240 1040 226 1260 236 1380 230' +
                  ' 1560 222 1700 230 1900 222 2080 232 2260 222 2400 226';
    ч.push({ пласт: узел('path', { d: гребень + 'V262H0Z', fill: 'var(--ny-sugrob)' }), имя: 'сугроб' });
    ч.push({ пласт: узел('path', {
      d: гребень, fill: 'none', stroke: 'var(--ny-sugrob-blesk)', 'stroke-width': 3,
      'stroke-linecap': 'round', 'vector-effect': 'non-scaling-stroke'
    }) });
    // Пара холмиков снега у самого края — второй, ближний ряд.
    ч.push({ пласт: узел('path', {
      d: 'M0 262V248C90 240 180 244 260 252S420 246 520 250 700 258 760 262ZM1500 262C1580 248 1680 244' +
         ' 1780 250S1960 246 2060 252 2300 244 2400 248V262Z', fill: 'var(--ny-sugrob-2)'
    }) });
    return сцена('scena zima', 260, 0.62, 0.35, ч);
  }
  /* Пагода в пять ярусов: у каждого крыша с загнутыми вверх краями,
     над верхней — шпиль с кольцами. */
  function pagoda(x, земля, h, цвет) {
    var d = 'M' + (x - h * 0.2) + ' ' + (земля + 4) + 'v-' + (h * 0.1 + 4) + 'h' + (h * 0.4) +
            'v' + (h * 0.1 + 4) + 'z';
    var ярусов = 5, низ = земля - h * 0.1;
    for (var i = 0; i < ярусов; i++) {
      var w = h * (0.3 - i * 0.035), стена = h * 0.1 - i * h * 0.006, кр = w * 1.45;
      d += 'M' + (x - w * 0.62) + ' ' + низ + 'v-' + стена + 'h' + (w * 1.24) + 'v' + стена + 'z';
      var y = низ - стена;
      // Крыша: плоский низ, края вверх, середина горбом.
      d += 'M' + (x - кр) + ' ' + (y - h * 0.035) + 'Q' + (x - кр * 0.6) + ' ' + (y + h * 0.012) + ' ' +
           (x - кр * 0.35) + ' ' + (y + h * 0.006) + 'H' + (x + кр * 0.35) + 'Q' + (x + кр * 0.6) + ' ' +
           (y + h * 0.012) + ' ' + (x + кр) + ' ' + (y - h * 0.035) + 'Q' + (x + кр * 0.45) + ' ' +
           (y - h * 0.035) + ' ' + x + ' ' + (y - h * 0.07) + 'Q' + (x - кр * 0.45) + ' ' +
           (y - h * 0.035) + ' ' + (x - кр) + ' ' + (y - h * 0.035) + 'z';
      низ = y - h * 0.05;
    }
    d += 'M' + (x - 1.6) + ' ' + низ + 'v-' + (h * 0.2) + 'h3.2v' + (h * 0.2) + 'z';
    for (var k = 0; k < 4; k++) d += круг(x, низ - h * (0.04 + k * 0.04), 3.2 - k * 0.4);
    return узел('path', { d: d, fill: цвет });
  }

  /* Тории: два столба, верхняя балка с загнутыми концами и вторая под ней. */
  function torii(x, земля, h, цвет) {
    var w = h * 1.1, t = h * 0.09;
    var d = 'M' + (x - w * 0.36) + ' ' + (земля + 4) + 'l' + (t * 0.4) + ' -' + (h + 4) + 'h' + t +
            'l' + (t * 0.3) + ' ' + (h + 4) + 'z' +
            'M' + (x + w * 0.36 - t * 1.7) + ' ' + (земля + 4) + 'l' + (t * 0.3) + ' -' + (h + 4) + 'h' + t +
            'l' + (t * 0.4) + ' ' + (h + 4) + 'z' +
            'M' + (x - w * 0.46) + ' ' + (земля - h * 0.74) + 'h' + (w * 0.92) + 'v' + (t * 0.9) +
            'h-' + (w * 0.92) + 'z' +
            'M' + (x - w * 0.62) + ' ' + (земля - h * 1.02) + 'Q' + x + ' ' + (земля - h * 0.9) + ' ' +
            (x + w * 0.62) + ' ' + (земля - h * 1.02) + 'l-' + (w * 0.06) + ' ' + (t * 1.5) +
            'Q' + x + ' ' + (земля - h * 0.8) + ' ' + (x - w * 0.56) + ' ' + (земля - h * 1.02 + t * 1.5) + 'z' +
            'M' + (x - t * 0.4) + ' ' + (земля - h * 0.9) + 'v' + (h * 0.18) + 'h' + (t * 0.8) +
            'v-' + (h * 0.18) + 'z';
    return узел('path', { d: d, fill: цвет });
  }

  /* Сакура: ствол и ветви раскидистым зонтом, на концах ветвей — облака
     цвета. Ветви — как у деревьев кладбища (дуги с изгибом), только
     шире в стороны; концы ветвей запоминаются, и вокруг каждого
     собирается кисть из кружков: тень снизу, цвет, блик сверху-слева.
     Все кружки одного тона — один путь. */
  function sakuraDerevo(x, земля, высота, толщина, сид, тона) {
    var r = зерно(сид);
    var пути = {}, концы = [];
    function ветка(x1, y1, угол, длина, т, глуб) {
      var x2 = x1 + Math.cos(угол) * длина, y2 = y1 + Math.sin(угол) * длина;
      var изгиб = (r() - 0.5) * длина * 0.4;
      var cx = (x1 + x2) / 2 + Math.cos(угол + Math.PI / 2) * изгиб;
      var cy = (y1 + y2) / 2 + Math.sin(угол + Math.PI / 2) * изгиб;
      var к = т.toFixed(1);
      пути[к] = (пути[к] || '') + 'M' + x1.toFixed(1) + ' ' + y1.toFixed(1) +
                'Q' + cx.toFixed(1) + ' ' + cy.toFixed(1) + ' ' + x2.toFixed(1) + ' ' + y2.toFixed(1);
      if (глуб <= 0 || т < 1.2) { концы.push([x2, y2, длина]); return; }
      if (глуб <= 2) концы.push([x2, y2, длина * 0.8]);
      var n = r() < 0.5 ? 3 : 2;
      for (var i = 0; i < n; i++) {
        var веер = (i - (n - 1) / 2) * (0.7 + r() * 0.4);
        /* Ветви тянутся в стороны: угол понемногу клонится к горизонту. */
        var новый = угол + веер + (r() - 0.5) * 0.4;
        новый += (-Math.PI / 2 - новый) * -0.12;
        ветка(x2, y2, новый, длина * (0.66 + r() * 0.16), т * 0.62, глуб - 1);
      }
    }
    var наклон = (r() - 0.5) * 0.3;
    ветка(x, земля + 2, -Math.PI / 2 + наклон, высота * 0.3, толщина, 5);
    var g = [];
    g.push(узел('path', {
      d: 'M' + (x - толщина * 1.5) + ' ' + (земля + 3) + 'Q' + (x - толщина * 0.4) + ' ' +
         (земля - толщина * 0.5) + ' ' + (x - толщина * 0.45) + ' ' + (земля - толщина * 2.4) +
         'L' + (x + толщина * 0.45) + ' ' + (земля - толщина * 2.4) + 'Q' + (x + толщина * 0.5) +
         ' ' + (земля - толщина * 0.5) + ' ' + (x + толщина * 1.7) + ' ' + (земля + 3) + 'Z',
      fill: 'var(--sk-stvol)'
    }));
    var ветви = [];
    Object.keys(пути).forEach(function (к) {
      ветви.push(узел('path', { d: пути[к], 'stroke-width': к }));
    });
    g.push(узел('g', { stroke: 'var(--sk-stvol)', fill: 'none', 'stroke-linecap': 'round' }, ветви));
    var тень = '', цвет = '', блик = '';
    концы.forEach(function (к) {
      var штук = 2 + Math.floor(r() * 3);
      for (var i = 0; i < штук; i++) {
        var rr = (8 + r() * 8) * тона;
        var cx = к[0] + (r() - 0.5) * 34 * тона, cy = к[1] + (r() - 0.6) * 22 * тона;
        тень += круг(cx + 2, cy + rr * 0.35, rr);
        цвет += круг(cx, cy, rr * 0.9);
        if (r() < 0.7) блик += круг(cx - rr * 0.3, cy - rr * 0.35, rr * 0.45);
      }
    });
    g.push(узел('path', { d: тень, fill: 'var(--sk-cvet-ten)' }));
    g.push(узел('path', { d: цвет, fill: 'var(--sk-cvet)' }));
    g.push(узел('path', { d: блик, fill: 'var(--sk-cvet-blik)' }));
    return узел('g', { class: 'sakura-derevo' }, g);
  }

  /* Бумажный фонарик: овальный, с рёбрами, тёмные крышки сверху и
     снизу и тёплое свечение вокруг. */
  function fonarik(x, y, r) {
    var рёбра = '';
    for (var i = -1; i <= 1; i++) {
      рёбра += 'M' + (x - r * 0.95) + ' ' + (y + i * r * 0.42) + 'Q' + x + ' ' +
               (y + i * r * 0.42 + r * 0.12) + ' ' + (x + r * 0.95) + ' ' + (y + i * r * 0.42);
    }
    return узел('g', {}, [
      узел('ellipse', { class: 'fonar-svet', cx: x, cy: y, rx: r * 3.4, ry: r * 3, fill: 'url(#sk-svet)' }),
      узел('path', { d: 'M' + x + ' ' + (y - r * 1.5) + 'v-' + (r * 0.6), stroke: 'var(--sk-stvol)',
                     'stroke-width': 1.4 }),
      узел('ellipse', { cx: x, cy: y, rx: r, ry: r * 1.25, fill: 'var(--sk-fonar)' }),
      узел('ellipse', { cx: x - r * 0.25, cy: y - r * 0.2, rx: r * 0.5, ry: r * 0.7,
                        fill: 'var(--sk-ogon)', 'fill-opacity': 0.55 }),
      узел('path', { d: рёбра, fill: 'none', stroke: 'var(--sk-stvol)', 'stroke-width': 0.8,
                     'stroke-opacity': 0.45 }),
      узел('rect', { x: x - r * 0.55, y: y - r * 1.48, width: r * 1.1, height: r * 0.34, rx: 1,
                     fill: 'var(--sk-stvol)' }),
      узел('rect', { x: x - r * 0.55, y: y + r * 1.14, width: r * 1.1, height: r * 0.34, rx: 1,
                     fill: 'var(--sk-stvol)' })
    ]);
  }

  /* Каменный фонарь (торо): основание, столб, домик с горящим окошком,
     крыша с загнутыми краями и навершие. */
  function toro(x, земля, h, цвет) {
    var s = h / 100;
    var g = [];
    g.push(узел('ellipse', { class: 'fonar-svet', cx: x, cy: земля - 58 * s, rx: 40 * s, ry: 34 * s,
                             fill: 'url(#sk-svet)' }));
    g.push(узел('path', {
      d: 'M' + (x - 22 * s) + ' ' + (земля + 4) + 'v-' + (12 * s) + 'h' + (44 * s) + 'v' + (12 * s) + 'z' +
         'M' + (x - 7 * s) + ' ' + (земля - 8 * s) + 'v-' + (34 * s) + 'h' + (14 * s) + 'v' + (34 * s) + 'z' +
         'M' + (x - 20 * s) + ' ' + (земля - 42 * s) + 'v-' + (6 * s) + 'h' + (40 * s) + 'v' + (6 * s) + 'z' +
         'M' + (x - 16 * s) + ' ' + (земля - 48 * s) + 'v-' + (22 * s) + 'h' + (32 * s) + 'v' + (22 * s) + 'z' +
         'M' + (x - 30 * s) + ' ' + (земля - 68 * s) + 'Q' + x + ' ' + (земля - 74 * s) + ' ' +
         (x + 30 * s) + ' ' + (земля - 68 * s) + 'Q' + (x + 12 * s) + ' ' + (земля - 76 * s) + ' ' + x + ' ' +
         (земля - 90 * s) + 'Q' + (x - 12 * s) + ' ' + (земля - 76 * s) + ' ' + (x - 30 * s) + ' ' +
         (земля - 68 * s) + 'z' + круг(x, земля - 93 * s, 4 * s),
      fill: цвет
    }));
    g.push(узел('rect', { x: x - 8 * s, y: земля - 66 * s, width: 16 * s, height: 13 * s, rx: 1,
                          fill: 'var(--sk-ogon)' }));
    return узел('g', {}, g);
  }

  /* Сакура, дальний план: гора с белой шапкой посередине, холмы с
     кронами-облачками, пагода справа от горы и тории слева. Гора — не
     земля: она не тянется по ширине (иначе на телефоне вышла бы острой
     иглой, а на широком окне — блином) и стоит посередине всегда. */
  function sakuraDal() {
    var ч = [];
    // Гора — вогнутые склоны к широкому подножию, и снежная шапка с
    // языками вниз по распадкам.
    var гора = предмет(1188, 246, 9, function (x) {
      return узел('g', { transform: 'translate(' + (x - 1188) + ' 0)' }, [
        узел('path', {
          d: 'M560 300V246C760 226 930 170 1080 104Q1130 80 1150 70H1206Q1226 80 1276 104C1430 170' +
             ' 1600 226 1820 246V300Z', fill: 'var(--sk-gora)'
        }),
        узел('path', {
          d: 'M1094 98Q1132 78 1150 70H1206Q1224 78 1262 98L1250 112 1236 104 1226 124 1212 108 1198 128' +
             ' 1186 106 1172 122 1160 104 1146 118 1132 104 1116 114Z', fill: 'var(--sk-gora-sneg)'
        })
      ]);
    });
    гора.центр = true;
    ч.push(гора);
    ч.push({ пласт: узел('path', {
      d: 'M0 300V222C160 206 320 210 470 220S760 222 900 214 1080 226 1200 224 1440 208 1580 212' +
         ' 1840 228 2000 222 2240 206 2400 212V300Z', fill: 'var(--sk-dal)'
    }), имя: 'холмы' });
    // Вдали цветущие деревья — облачка на гребнях. Каждое — отдельно:
    // у края окна облачко либо целое, либо его нет.
    var r = зерно(901);
    [[90, 214], [240, 208], [380, 214], [760, 220], [860, 214], [1500, 210], [1660, 214],
     [1760, 222], [2060, 218], [2190, 210], [2330, 210]].forEach(function (о) {
      var облако = '';
      for (var i = 0; i < 4; i++) {
        облако += круг((r() - 0.5) * 40, о[1] - 6 - r() * 12, 9 + r() * 9);
      }
      ч.push(предмет(о[0], о[1], 1, function (x) {
        return узел('path', { d: облако, fill: 'var(--sk-dal-cvet)', transform: 'translate(' + x + ' 0)' });
      }, 'холмы'));
    });
    ч.push(предмет(1395, 214, 5, function (x) { return pagoda(x, 214, 150, 'var(--sk-pagoda)'); }, 'холмы'));
    ч.push(предмет(985, 222, 4, function (x) { return torii(x, 222, 52, 'var(--sk-pagoda)'); }, 'холмы'));
    return сцена('scena-dal sakura-dal', 300, 0.7, 0.3, ч);
  }

  /* Сакура, передний план: берег, деревья в цвету, верёвка с бумажными
     фонариками между двумя средними и каменный фонарь. */
  function sakuraSad() {
    var ч = [{ как: узел('defs', {}, [свечение('sk-svet', 'var(--sk-ogon)', 0.4)]) }];
    [[140, 236, 230, 12, 11, 1.15, 3], [520, 238, 190, 10, 17, 1, 2], [980, 232, 176, 9, 23, 1, 5],
     [1600, 236, 168, 9, 29, 1, 5], [1890, 230, 236, 12, 31, 1.15, 3], [2290, 236, 196, 10, 37, 1, 2]
    ].forEach(function (д, i) {
      var дерево = предмет(д[0], д[1], д[6], function (x) {
        return sakuraDerevo(x, д[1], д[2], д[3], д[4], д[5]);
      }, 'берег');
      дерево.id = 'd' + i;
      ч.push(дерево);
    });
    /* Верёвка с фонариками — провисает между двумя средними деревьями.
       Её место зависит от того, где деревья встали, поэтому она
       рисуется после раскладки; нет одного из деревьев — нет и верёвки. */
    ч.push({ потом: function (шир, где) {
      var a = где.d2, b = где.d3;
      if (!a || !b) return [];
      var x1 = a.место + 20, x2 = b.место - 20, y1 = 132 + a.dy, y2 = 138 + b.dy;
      var длина = x2 - x1;
      if (длина < 160) return [];
      var cx = (x1 + x2) / 2, cy = Math.max(y1, y2) + Math.min(70, 46 * длина / 580);
      var g = [узел('path', { d: 'M' + x1.toFixed(1) + ' ' + y1 + 'Q' + cx.toFixed(1) + ' ' + cy.toFixed(1) +
                                 ' ' + x2.toFixed(1) + ' ' + y2, fill: 'none',
                              stroke: 'var(--sk-stvol)', 'stroke-width': 1.6 })];
      var штук = Math.max(2, Math.min(8, Math.round(длина / 90)));
      for (var k = 1; k <= штук; k++) {
        var t = k / (штук + 1);
        var x = (1 - t) * (1 - t) * x1 + 2 * (1 - t) * t * cx + t * t * x2;
        var y = (1 - t) * (1 - t) * y1 + 2 * (1 - t) * t * cy + t * t * y2;
        g.push(fonarik(x, y + 14, 8));
      }
      return g;
    } });
    ч.push(предмет(760, 236, 4, function (x) { return toro(x, 236, 70, 'var(--sk-kamen)'); }, 'берег'));
    // Берег и опавшие лепестки на нём.
    ч.push({ пласт: узел('path', {
      d: 'M0 262V232C150 222 300 228 440 236S700 230 860 236 1100 228 1240 234 1500 240 1640 232' +
         ' 1880 224 2040 232 2260 238 2400 230V262Z', fill: 'var(--sk-bereg)'
    }), имя: 'берег' });
    ч.push({ потом: function (шир) {
      var r = зерно(77), опавшие = '';
      var n = Math.round(70 * шир / РИС);
      for (var i = 0; i < n; i++) опавшие += круг(r() * шир, 240 + r() * 18, 1.4 + r() * 1.8);
      return [узел('path', { d: опавшие, fill: 'var(--sk-cvet)', 'fill-opacity': 0.75 })];
    } });
    return сцена('scena sakura-sad', 260, 0.62, 0.3, ч);
  }
  /* ==================================================================
     Раскладка сцены под окно

     Раньше каждый план был одной картинкой 2400 в ширину, которая
     ложилась на экран по высоте (slice), а лишнее по бокам срезалось —
     что придётся: на широком окне половина крайнего дерева, на узком
     ещё и крона над горой; а всё, что выше рамки рисунка (верхушки
     голых деревьев, кроны), обрезалось сверху ровной линией. Сцена
     выглядела недорисованной. Теперь она собирается под окно:

       * ширина рисунка — ровно ширина окна (в единицах рисунка; высоту
         плана задаёт app.css), поэтому по краям ничего не срезается;
       * земля — холмы, сугробы, берег (пласт) — тянется или сжимается
         по ширине: ей это не вредит;
       * предметы — деревья, надгробия, пагода, домик — своего размера
         не меняют. Их места берутся из рисунка, растянутого под окно;
         на узком экране рисунок сжимается не сильнее, чем до «мин»,
         иначе всё слиплось бы в кучу;
       * предмет, который всё равно вылез за край, сдвигается внутрь,
         если виден хотя бы наполовину, а если меньше — не рисуется
         вовсе. Сдвинутый не должен налезать на соседей сильнее, чем в
         исходном рисунке (и не больше доли «тесно» от меньшего из
         двух); когда тесно, остаётся тот, кто важнее (важн);
       * то, что выше рамки, не срезается: у планов overflow:visible
         (app.css), над ними и так одно небо.

     Окно поменяло размер — раскладка пересобирается (см. «resize»
     ниже). Предметы при этом не строятся заново, а только
     переставляются. */
  var РИС = 2400, СЕРЕДИНА = 1200, ПОЛЕ = 8;
  var сцены = [];

  /* План сцены: класс (для app.css), высота рисунка, мин — насколько
     можно сжать рисунок на узком экране, тесно — сколько можно налезть
     на соседа, части — по порядку снизу вверх. Части бывают четырёх
     видов:
       { как: узел }        — как есть (defs с градиентами);
       { пласт: узел, имя } — земля: тянется по ширине; по имени её
                              верхний край находят предметы;
       предмет(…)           — стоит на своём месте, см. ниже;
       { потом: функция }   — рисуется по уже расставленным предметам
                              (верёвка с фонариками, опавшие лепестки). */
  function сцена(класс, высота, мин, тесно, части) {
    части.forEach(function (ч, i) { ч.i = i; });
    return { класс: класс, высота: высота, мин: мин, тесно: тесно, части: части, svg: null, шир: 0 };
  }

  /* Предмет: x — место в рисунке 2400, база — уровень земли под ним,
     важн — кто остаётся, когда тесно, рис(x) — рисует его в точке x,
     на — имя земли, на которой он стоит. */
  function предмет(x, база, важн, рис, на) {
    return { x: x, база: база, важн: важн, рис: рис, на: на || '' };
  }

  /* Верхний край земли — точки, где кончаются отрезки пути. По ним
     сдвинутый предмет встаёт на землю, а не висит над ней. В путях
     земли только абсолютные команды: M, L, H, V, C, S, Q. */
  function профиль(d) {
    var т = String(d).match(/[A-Za-z]|-?\d*\.?\d+/g) || [];
    var i = 0, к = '', x = 0, y = 0, все = [], низ = -Infinity;
    function n() { return parseFloat(т[i++]); }
    while (i < т.length) {
      if (/[A-Za-z]/.test(т[i])) { к = т[i++]; continue; }
      if (к === 'M' || к === 'L') { x = n(); y = n(); }
      else if (к === 'V') y = n();
      else if (к === 'H') x = n();
      else if (к === 'C') { i += 4; x = n(); y = n(); }
      else if (к === 'S' || к === 'Q') { i += 2; x = n(); y = n(); }
      else { i++; continue; }
      все.push([x, y]);
      низ = Math.max(низ, y);
    }
    /* Нижняя кромка (там, где путь замыкается под землёй) — не верх. */
    return все.filter(function (т) { return т[1] < низ; })
              .sort(function (a, b) { return a[0] - b[0]; });
  }

  function высотаЗемли(точки, x) {
    if (!точки || !точки.length) return 0;
    if (x <= точки[0][0]) return точки[0][1];
    for (var k = 1; k < точки.length; k++) {
      var a = точки[k - 1], b = точки[k];
      if (x <= b[0]) return a[1] + (b[1] - a[1]) * (x - a[0]) / Math.max(1, b[0] - a[0]);
    }
    return точки[точки.length - 1][1];
  }

  /* Ширина предмета — по его рамке (getBBox). Считается один раз, пока
     предмет на минуту вставлен в план. Если план не показан (рамка
     пустая), берём осторожную ширину и посчитаем в следующий раз. */
  function рамка(svg, ч) {
    if (ч.рамка) return ч.рамка;
    var р = null;
    svg.appendChild(ч.узел);
    try {
      var bb = ч.узел.getBBox();
      if (bb.width > 0) р = [bb.x - 2, bb.x + bb.width + 2];
    } catch (e) { /* не показан — ниже запасной вариант */ }
    svg.removeChild(ч.узел);
    if (р) ч.рамка = р;
    return р || [-60, 60];
  }

  function разложить(сц) {
    var svg = сц.svg;
    if (!svg || !svg.parentNode) return;
    var r = svg.getBoundingClientRect();
    /* План не виден (размеров нет) — рисуем как раньше, во всю ширину
       рисунка; при следующем изменении окна разложится как надо. */
    var шир = (r.width > 20 && r.height > 20) ? Math.round(r.width * сц.высота / r.height) : РИС;
    if (сц.шир === шир && svg.firstChild) return;
    сц.шир = шир;
    while (svg.firstChild) svg.removeChild(svg.firstChild);
    svg.setAttribute('viewBox', '0 0 ' + шир + ' ' + сц.высота);
    var c = Math.max(шир / РИС, сц.мин);
    function место(x) { return шир / 2 + (x - СЕРЕДИНА) * c; }
    function вРисунок(x) { return СЕРЕДИНА + (x - шир / 2) / c; }

    var земли = {}, предметы = [];
    сц.части.forEach(function (ч) {
      if (ч.пласт && ч.имя && !ч.точки) ч.точки = профиль(ч.пласт.getAttribute('d'));
      if (ч.пласт && ч.имя) земли[ч.имя] = ч.точки;
      if (ч.рис) {
        if (!ч.узел) ч.узел = ч.рис(0);
        предметы.push(ч);
      }
    });

    /* Расстановка: сначала важные, при равной важности — по порядку. */
    var стоят = [], где = {};
    предметы.slice().sort(function (a, b) { return (b.важн - a.важн) || (a.i - b.i); })
      .forEach(function (ч) {
        ч.место = null;
        ч.dy = 0;
        var р = рамка(svg, ч), л = р[0], п = р[1], w = п - л;
        var x0 = место(ч.x);
        if (ч.центр) { ч.место = x0; ч.р = р; стоят.push(ч); return; }
        if (w > шир - 2 * ПОЛЕ) return;
        var виден = (Math.min(x0 + п, шир) - Math.max(x0 + л, 0)) / w;
        if (виден < 0.5) return;
        var x = Math.min(Math.max(x0, ПОЛЕ - л), шир - ПОЛЕ - п);
        for (var k = 0; k < стоят.length; k++) {
          var д = стоят[k];
          var ov = Math.min(x + п, д.место + д.р[1]) - Math.max(x + л, д.место + д.р[0]);
          if (ov <= 0) continue;
          var ov0 = Math.min(ч.x + п, д.x + д.р[1]) - Math.max(ч.x + л, д.x + д.р[0]);
          if (ov > Math.max(ov0, сц.тесно * Math.min(w, д.р[1] - д.р[0])) + 1) return;
        }
        ч.место = x;
        ч.р = р;
        if (Math.abs(x - x0) > 0.5 && земли[ч.на]) {
          ч.dy = высотаЗемли(земли[ч.на], вРисунок(x)) - высотаЗемли(земли[ч.на], ч.x);
        }
        стоят.push(ч);
      });
    стоят.forEach(function (ч) { if (ч.id) где[ч.id] = ч; });

    /* Рисуем по порядку частей — это и порядок слоёв. */
    var земля = 'translate(' + (шир / 2).toFixed(1) + ' 0) scale(' + c.toFixed(4) + ' 1) translate(-' +
                СЕРЕДИНА + ' 0)';
    сц.части.forEach(function (ч) {
      if (ч.как) svg.appendChild(ч.как);
      else if (ч.пласт) svg.appendChild(узел('g', { transform: земля }, [ч.пласт]));
      else if (ч.потом) ч.потом(шир, где).forEach(function (э) { svg.appendChild(э); });
      else if (ч.рис && ч.место !== null) {
        svg.appendChild(узел('g', { transform: 'translate(' + ч.место.toFixed(1) + ' ' + ч.dy.toFixed(1) + ')' },
                             [ч.узел]));
      }
    });
  }

  /* План в разметке. Сам рисунок появится в разложить(): ему нужно
     знать размер, а размер есть только у вставленного в страницу. */
  function план(сц) {
    сц.svg = узел('svg', { class: сц.класс, preserveAspectRatio: 'xMidYMax meet', 'aria-hidden': 'true' });
    сцены.push(сц);
    return сц.svg;
  }

  /* Окно поменяло размер — перекладываем. Не на каждый пиксель, а когда
     размер устоялся; и только если ширина рисунка правда изменилась. */
  var перекройТаймер = null;
  window.addEventListener('resize', function () {
    if (!сцены.length) return;
    clearTimeout(перекройТаймер);
    перекройТаймер = setTimeout(function () { сцены.forEach(разложить); }, 120);
  });
  /* Северное сияние: несколько мягких полос в верхней половине неба,
     медленно колышутся. Только градиенты (см. .siyanie в app.css). */
  function siyanie() {
    var э = document.createElement('div');
    э.className = 'siyanie';
    var э2 = document.createElement('div');
    э2.className = 'siyanie s2';
    return [э, э2];
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

  /* Последний выбранный режим праздника: нужен, чтобы «по календарю»
     переключилось само, если вкладка простояла открытой через полночь
     на границе праздника. */
  var режимПраздника = '';

  /* Вкладку спрятали — снег и лепестки встают (класс на <html>, правило
     в app.css). Вернулись — едут дальше, а в режиме «по календарю»
     заодно сверяемся с датой. */
  document.addEventListener('visibilitychange', function () {
    var корень = document.documentElement;
    корень.classList.toggle('prazdnik-pauza', document.hidden);
    if (!document.hidden && режимПраздника === '' &&
        (корень.getAttribute('data-holiday') || '') !== holidayOn()) {
      applyHoliday('');
    }
  });

  /* Прокручена ли страница. Нужно луне на телефоне (см. .luna в
     app.css): у верха она стоит в просвете над списком, а когда список
     поехал под неё — гаснет, чтобы не стоять под текстом. Класс
     меняется только на границе, а не на каждый пиксель прокрутки. */
  var прокручено = false;
  window.addEventListener('scroll', function () {
    var сейчас = (window.pageYOffset || 0) > 24;
    if (сейчас === прокручено) return;
    прокручено = сейчас;
    document.documentElement.classList.toggle('prokrutka', сейчас);
  }, { passive: true });

  function applyHoliday(режим) {
    /* Режим: '' — по календарю, 'off' — выключено, иначе название. */
    режимПраздника = режим || '';
    var сейчас = режим === 'off' ? ''
               : (режим && ПРАЗДНИКИ.indexOf(режим) >= 0 ? режим : holidayOn());

    var корень = document.documentElement;
    if (сейчас) корень.setAttribute('data-holiday', сейчас);
    else корень.removeAttribute('data-holiday');

    document.querySelectorAll('.prazdnik').forEach(function (с) { с.remove(); });
    document.querySelectorAll('.prazdznak').forEach(function (з) { з.remove(); });
    сцены = [];
    if (!сейчас) return;

    /* Нижний слой — только заливка. */
    var низ = document.createElement('div');
    низ.className = 'prazdnik';
    низ.setAttribute('aria-hidden', 'true');
    var заливка = document.createElement('div');
    заливка.className = 'zaliv';
    низ.appendChild(заливка);
    if (сейчас === 'halloween') {
      /* Порядок — это и порядок слоёв: звёзды, луна, даль, туман,
         передний план, туман у самого края. */
      var звёзды = document.createElement('div');
      звёзды.className = 'zvezdy';
      низ.appendChild(звёзды);
      низ.appendChild(luna());
      низ.appendChild(план(kladbischeDal()));
      var туман = document.createElement('div');
      туман.className = 'tuman t1';
      низ.appendChild(туман);
      низ.appendChild(план(kladbische()));
      туман = document.createElement('div');
      туман.className = 'tuman t2';
      низ.appendChild(туман);
    }
    if (сейчас === 'newyear' || сейчас === 'sakura') {
      /* Тот же порядок слоёв, что у Хэллоуина. У Нового года над
         звёздами ещё северное сияние. */
      var небо = document.createElement('div');
      небо.className = 'zvezdy';
      низ.appendChild(небо);
      if (сейчас === 'newyear') siyanie().forEach(function (э) { низ.appendChild(э); });
      низ.appendChild(luna());
      низ.appendChild(план(сейчас === 'newyear' ? zimaDal() : sakuraDal()));
      var дымка = document.createElement('div');
      дымка.className = 'tuman t1';
      низ.appendChild(дымка);
      низ.appendChild(план(сейчас === 'newyear' ? zima() : sakuraSad()));
      дымка = document.createElement('div');
      дымка.className = 'tuman t2';
      низ.appendChild(дымка);
    }
    document.body.appendChild(низ);
    /* Планы раскладываются уже в странице: им нужен свой размер. */
    сцены.forEach(разложить);

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
      if (сейчас === 'newyear') сыпать(слой, 'snezh', skolko(20), function (э, r) {
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
      if (сейчас === 'sakura') сыпать(слой, 'lepestok', skolko(14), function (э, r) {
        var w = 8 + r() * 6;
        э.style.width = w + 'px'; э.style.height = (w * 0.72).toFixed(1) + 'px';
        э.style.setProperty('--vidno', (0.34 + r() * 0.36).toFixed(2));
        э.style.setProperty('--snos', Math.round(20 + r() * 120) + 'px');
        э.style.animationDuration = (10 + r() * 10).toFixed(1) + 's';
      });
      if (сейчас === 'halloween') летучки(слой, skolko(4));
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

  /* Значок «18+» на обложке и в строке части. Только у r_plus (этти) и
     только у того, кто сам включил «Показывать 18+»: остальным сервер
     такое и не присылает, но значок без настройки был бы странным.

     У хентая (rx) значок свой — «18+ · хентай», другого цвета: его
     нельзя спутать с этти ни на карточке, ни в строке части. Показывается
     только тому, у кого включены оба переключателя. */
  function ageBadge(it) {
    if (!it || !me || !me.settings || me.settings.adult !== true) return null;
    var b;
    if (it.hentai === true || it.rating === 'rx') {
      if (me.settings.hentai !== true) return null;
      b = el('span', 'agebadge hentai', t('18+ · хентай', '18+ · hentai'));
      b.title = t('Хентай', 'Hentai');
      return b;
    }
    if (!(it.adult === true || it.rating === 'r_plus')) return null;
    b = el('span', 'agebadge', '18+');
    b.title = t('18+ · этти', '18+ · ecchi');
    return b;
  }

  function applyLook(s) {
    s = s || {};
    applyTheme(s.theme || 'kak-seychas');
    applyHoliday(s.holiday || '');
    /* «Фон: глубже / мягче» больше не выбирается: всегда светлее (mid).
       Старое сохранённое depth не значит ничего. */
    document.documentElement.setAttribute('data-depth', 'mid');
    if (s.accent) document.documentElement.setAttribute('data-accent', s.accent);
    if (s.card_size) document.documentElement.style.setProperty('--card-min', s.card_size + 'px');
    if (s.logo) {
      document.querySelectorAll('.logo-v').forEach(function (v) {
        v.classList.toggle('on', v.dataset.v === String(s.logo));
      });
    }
    /* Точки на фоне: включены, пока человек явно их не выключил. */
    document.documentElement.setAttribute('data-dots', s.dots === false ? 'off' : 'on');
    if (s.ui_lang || s.lang) applyLang(s.ui_lang || s.lang);
    цветВкладки();
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
    /* Не вошёл — и спрашивать незачем.

       Раньше каждая страница первым делом спрашивала /api/me, и у того,
       кто ещё не вошёл, сервер честно отвечал 401. Ответ обрабатывался
       правильно, но браузер всё равно записывал в консоль красную строку
       «Failed to load resource: 401» — на каждом открытии страницы входа.
       Среди таких строк настоящая ошибка теряется.

       Признак входа виден и без запроса: вместе с кукой сессии сервер
       всегда ставит куку csrf (метку формы) с тем же сроком, а при
       выходе стирает обе. Куки сессии скрипт не видит (она httponly), а
       метку видит. Нет метки — нет и входа: даже если бы сессия где-то
       осталась, без метки она не смогла бы ничего сохранить.

       Ответ {kind: "anon"} с кодом 200 тоже значит «не вошёл» — на случай,
       если сервер однажды перестанет отвечать на это ошибкой. */
    if (!csrfToken()) { me = null; return Promise.resolve(null); }
    return api.get('/api/me').then(function (data) {
      return setMe(data && data.kind !== 'anon' ? data : null);
    }).catch(function (e) {
      if (e.status === 401) { me = null; return null; }
      throw e;
    });
  }

  /* Цвет буквы на кружке аватара — от фона, а не от места на странице.

     Раньше букву красил тот, кто стоял рядом: в шапке она наследовала
     приглушённый серый от подписи с именем, в карточке была почти
     чёрной, в списке аккаунтов — ещё какой-то. Один и тот же человек
     выглядел по-разному в трёх местах. Теперь цвет считается из фона
     кружка по той же формуле яркости, что и проверка контраста: на
     светлом — тёмная буква, на тёмном — светлая. */
  var AVA_DARK = '#14161C', AVA_LIGHT = '#F4F1EA';
  function avaInk(hex) {
    var m = /^#?([0-9a-f]{6})$/i.exec(String(hex || '').trim());
    if (!m) return AVA_DARK;
    var lin = [0, 2, 4].map(function (i) {
      var c = parseInt(m[1].substr(i, 2), 16) / 255;
      return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
    });
    var L = 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2];
    /* Контраст с тёмной буквой против контраста со светлой. */
    var dark = (L + 0.05) / (0.0082 + 0.05), light = (0.8775 + 0.05) / (L + 0.05);
    return dark >= light ? AVA_DARK : AVA_LIGHT;
  }

  /* Один способ нарисовать аватар — для шапки, карточки, кабинета и
     списка аккаунтов. m: {avatar_color, has_avatar, display_name|name}.
     photo — адрес фото; по умолчанию своё (/api/me/avatar). */
  function paintAvatar(node, m, photo) {
    if (!node || !m) return;
    /* Цвет кружка — только вида #RRGGBB. Сервер принимает ровно такой и
       сам (api_profile), но значение уходит в style.background, а туда
       можно записать и картинку: url(чужой адрес). В списке аккаунтов
       админка рисует кружки ДРУГИХ людей — и если бы в базу однажды
       попало что-то иное (старая версия, ручная правка), браузер
       администратора сходил бы по чужому адресу. Проверка здесь стоит
       копейки, а этот путь закрывает и со стороны страницы. */
    var bg = /^#[0-9a-f]{6}$/i.test(String(m.avatar_color || '')) ? m.avatar_color : '#84CBB6';
    var letter = (m.display_name || m.name || '?').charAt(0).toUpperCase();
    node.style.background = bg;
    node.style.color = avaInk(bg);
    node.textContent = '';
    if (m.has_avatar && photo !== false) {
      var img = new Image();
      img.alt = '';
      img.src = photo || '/api/me/avatar?v=' + Date.now();
      img.onerror = function () { node.textContent = letter; };
      node.appendChild(img);
    } else {
      node.textContent = letter;
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

  /* Что браузер помнит про ЧЕЛОВЕКА, а не про само устройство.

     Язык сайта и язык аниме — выбор устройства, их оставляем: следующий,
     кто откроет сайт на этом компьютере, скорее всего, тот же человек.
     А «о каких сериях уже уведомили» — это список ключей тайтлов, то есть
     кусок чужого списка просмотра: после выхода он оставался лежать в
     браузере, и следующий человек за тем же компьютером мог его
     прочитать. Туда же — «уведомлять в браузере»: его включал прежний
     человек для своих тайтлов, новому он не обещан. */
  var PER_PERSON_KEYS = ['animedik.notified', 'animedik.notify'];

  function forgetPerson() {
    PER_PERSON_KEYS.forEach(function (k) {
      try { localStorage.removeItem(k); } catch (e) { /* хранилища нет — и помнить нечего */ }
    });
  }

  /* Второй «Выйти» подряд (двойной щелчок, Enter и щелчок разом) раньше
     слал второй запрос: первый уже закрыл вход, второй получал отказ, и
     перед самым уходом со страницы мелькало окно «Выйти не получилось».
     Пока первый выход в пути, остальные нажатия ждут его. */
  var logoutPending = null;
  function logout() {
    if (logoutPending) return logoutPending;
    logoutPending = api.post('/api/auth/logout').then(function () {
      forgetPerson();
      location.href = '/';
    });
    logoutPending.catch(function () { logoutPending = null; });
    return logoutPending;
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
  /* Плашка «это демо» приходит с сервера уже после отрисовки страницы и
     вставала сверху, сдвигая всё вниз на свою высоту (сдвиг раскладки
     ~0,1 на «Итогах года» — на границе нормы). Ответ сервера помним в
     этом браузере и в следующий раз показываем плашку сразу; сервер
     потом подтвердит или уберёт. Первый заход — как раньше. */
  var MODE_KEY = 'animedik.mode';
  function showMode() {
    var box = document.getElementById('demonote');
    if (!box) return;
    try { if (localStorage.getItem(MODE_KEY) === 'demo') box.hidden = false; } catch (e) { /* нет хранилища */ }
    api.get('/api/mode').then(function (r) {
      var demo = !!(r && r.demo);
      box.hidden = !demo;
      try { localStorage.setItem(MODE_KEY, demo ? 'demo' : 'private'); } catch (e) { /* переживём */ }
    }).catch(function () { /* не ответил — молчим, плашка не появится */ });
  }

  /* Подтягиваем язык из настроек до отрисовки, чтобы не мигало */
  function boot(after) {
    showMode();
    loadMe().then(function () {
      /* Настройка аккаунта важнее — человек выбрал её осознанно. Но
         если её нет, берём то, что он выбрал в этом браузере. */
      applyLang((me && me.settings && (me.settings.ui_lang || me.settings.lang)) ||
                savedLang() || 'ru');
      if (after) after(me);
    }).catch(function () {
      applyLang(savedLang() || 'ru');
      if (after) after(null);
    });
  }

  global.App = {
    safeUrl: safeUrl, cssUrl: cssUrl, el: el, bg: bg,
    api: api, csrfToken: csrfToken,
    applyLang: applyLang, applyLook: applyLook, t: t,
    animeName: animeName, listName: listName, kindName: kindName, genreName: genreName,
    langCode: langCode, pickedLang: pickedLang, forgetPerson: forgetPerson,
    animeLang: animeLang, setAnimeLang: setAnimeLang, setPageAnimeLang: setPageAnimeLang,
    pageAnimeLang: pageAnimeLang, ANIME_LANGS: ANIME_LANGS,
    animeSrcLang: animeSrcLang, animeTrack: animeTrack,
    defaultSource: defaultSource,
    applyTheme: applyTheme, applyHoliday: applyHoliday, holidayOn: holidayOn,
    plural: plural, count: count, say: say,
    ask: ask, tell: tell,
    loadMe: loadMe, setMe: setMe, boot: boot, logout: logout,
    paintAvatar: paintAvatar, avaInk: avaInk, startGuestClock: startGuestClock,
    ageBadge: ageBadge,
    mmss: mmss, showError: showError, busy: busy,
    get me() { return me; }
  };
})(window);
