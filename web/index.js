/* Главная страница: вход, полка, поиск, рулетка, кабинет. */
(function () {
  'use strict';
  var A = window.App;
  var $ = function (id) { return document.getElementById(id); };

  /* Поле search отсюда убрано: оно заводилось, но никогда не читалось
     и не записывалось — результаты поиска живут прямо в своей секции. */
  var state = { items: [], tab: 'open' };
  /* Когда кончается гостевой пропуск и сколько он длился — для
     «осталось NN мин» в карточке профиля. */
  var guestEnds = 0, guestTotal = 1;
  /* Источник по умолчанию берётся от языка сайта: у русского и
     английского он разный, и подменять один другим нельзя. Если
     выбранный молчит, сервер переберёт остальные — но только того же
     языка. */
  function currentSource() { return A.defaultSource(); }

  /* =============== обложки ===============
     Все обложки на странице ставятся через A.bg (см. app.js), а не прямой
     записью в style.backgroundImage. Прямая запись знала одну ссылку: не
     загрузилась — в карточке оставалась буква, и понять, что это за
     тайтл, было нельзя. A.bg пробует ссылки по очереди, потом спрашивает
     сервер (/api/poster ищет по трём справочникам), и только потом
     сдаётся — тогда остаётся буква-заглушка, как раньше.

     node — куда ставить фон; urls — ссылки по порядку (пустые и повторы
     A.bg выкинет сам); it — тайтл, по названию и году которого искать;
     own — это запись своего списка: сервер допишет ей найденную обложку,
     чтобы в следующий раз не искать. */
  function setPoster(node, urls, it, own, opts) {
    opts = opts || {};
    it = it || {};
    A.bg(node, urls, {
      lookup: {
        title: it.title || '',
        title_en: it.title_en || '',
        year: it.year || '',
        key: own && A.me && A.me.role !== 'guest' ? (it.key || '') : ''
      },
      onOk: opts.onOk,
      onFail: opts.onFail
    });
  }

  /* =============== вход =============== */
  function showGate() {
    $('gate').hidden = false;
    $('app').hidden = true;
  }

  function showApp(me) {
    $('gate').hidden = true;
    $('app').hidden = false;
    paintMe(me);
    if (me.role === 'guest') {
      $('guestbar').classList.add('show');
      A.startGuestClock(me.expires_in, {
        clock: $('clock'), rail: $('rail'), bar: $('guestbar')
      });
      /* Карточке профиля нужно «осталось NN мин»: считаем от того же
         срока, что и часы в плашке гостя. */
      guestEnds = Date.now() + Math.max(0, me.expires_in | 0) * 1000;
      guestTotal = Math.max(1, me.expires_in | 0) * 1000;
    } else {
      $('guestbar').classList.remove('show');
    }
    loadLibrary();
    var isAdmin = me.role === 'admin';
    /* Раздел «Админка» в кабинете и вход в него из карточки — только
       администратору. Сам раздел прячет и показывает переключение
       разделов (showSec), здесь — только его вкладка. */
    /* Вкладки «Админка» в полосе настроек больше нет: админка открывается
       своим окном из карточки профиля (openSet('admin')). */
    $('cab-t-admin').hidden = true;
    $('uc-admin').hidden = !isAdmin;
    $('uc-star').hidden = !isAdmin;
    $('btn-news').hidden = !isAdmin;
    cardMode(me);
    /* Праздник назначает администратор: остальным — «по календарю» и
       «выключить». Сервер это же и проверяет, здесь только вид. */
    document.querySelectorAll('#s-holiday .adm-only').forEach(function (b) {
      b.hidden = !isAdmin;
    });
    try {
      $('hol-motion').hidden = !matchMedia('(prefers-reduced-motion: reduce)').matches;
    } catch (e) { $('hol-motion').hidden = true; }
    if (isAdmin) loadAdmin();
    if (me.role !== 'guest') paintSecurity(me);
    loadNews();
    paintNotify();
    lockForGuest(me.role === 'guest');
    boot(showToast);
  }

  function paintMe(me) {
    $('whoami').textContent = me.display_name || me.name;
    $('set-name').textContent = me.display_name || me.name;
    /* У гостя имени нет (сервер зовёт его «guest»), и в карточке это
       выглядело бы как чужой логин. Пишем по-человечески. */
    var ucName = $('uc-name');
    if (me.role === 'guest') {
      ucName.setAttribute('data-ru', 'Гостевой вход');
      ucName.setAttribute('data-en', 'Guest pass');
      ucName.textContent = A.t('Гостевой вход', 'Guest pass');
    } else {
      ucName.removeAttribute('data-ru');
      ucName.removeAttribute('data-en');
      ucName.textContent = me.display_name || me.name;
    }
    A.paintAvatar($('ava'), me);
    A.paintAvatar($('set-ava'), me);
    A.paintAvatar($('uc-ava'), me);
    paintBanner(me);
    /* Сайт — для своих: обычный аккаунт называется «Друг», а не
       безликое «Пользователь». */
    var roles = {
      admin: ['Админ', 'Admin'],
      user: ['Друг', 'Friend'],
      guest: ['Гость', 'Guest']
    };
    var kind = roles[me.role] ? me.role : 'user';
    ['set-role', 'uc-role'].forEach(function (id) {
      var r = $(id);
      r.setAttribute('data-kind', kind);
      r.setAttribute('data-ru', roles[kind][0]);
      r.setAttribute('data-en', roles[kind][1]);
      r.textContent = A.t(roles[kind][0], roles[kind][1]);
    });

    if (me.role !== 'guest') {
      $('s-name').value = me.display_name || me.name;
      langText($('sess-note'), 'Активных входов: ' + (me.sessions || 1),
               'Active sessions: ' + (me.sessions || 1));
      var s = me.settings || {};
      mark('#s-accent .sc', 'a', s.accent || 'mint');
      markTheme(s.theme || 'kak-seychas');
      markHoliday(s.holiday || '');
      mark('#s-logo .logopick', 'l', String(s.logo || 1));
      mark('#s-ava .sc', 'c', me.avatar_color || '#84CBB6');
      /* Переключатели-настройки показывают сохранённое значение, а не то,
         что стояло в разметке. Сейчас таких в кабинете нет, но если
         появятся — «Сохранить» не должен отправлять значение из разметки. */
      document.querySelectorAll('.sw[data-k]').forEach(function (b) {
        if (b.dataset.k in s) b.setAttribute('aria-checked', s[b.dataset.k] ? 'true' : 'false');
      });
      $('s-photo-clear').hidden = !me.has_avatar;
      paintAvaLock(me);
      setCheck('s-adult', s.adult === true);
      setCheck('s-hentai', s.adult === true && s.hentai === true);
      paintHentai();
      setCheck('s-dots', s.dots !== false);
      /* Как и под плеером: не выключено явно — значит включено. */
      setCheck('s-autonext', s.autonext !== false);
      setDirty(false);
    }
    markAnimeLang(A.animeLang());
  }

  /* Текст, который сам перепишется при смене языка: обе версии лежат
     в data-ru / data-en, и applyLang (app.js) берёт нужную. Без этого
     такие строки оставались на прежнем языке до следующей перерисовки:
     переключил сайт на английский — а в «Безопасности» всё ещё
     «Активных входов: 2». */
  function langText(node, ru, en) {
    if (!node) return;
    node.setAttribute('data-ru', ru);
    node.setAttribute('data-en', en);
    node.textContent = A.t(ru, en);
  }

  /* Баннер профиля — картинка в шапке карточки и кабинета вместо цветной
     полосы с точками. Адрес меняется вместе со временем, когда баннер
     поставили (banner_at), и счётчиком этой вкладки: иначе браузер
     показал бы из своего кэша прежний. */
  var bannerBust = 0;
  function paintBanner(me) {
    var at = me && me.role !== 'guest' ? (me.banner_at | 0) : 0;
    document.querySelectorAll('.uchead, .panel.cab .phead').forEach(function (h) {
      h.classList.toggle('has-ban', !!at);
      var b = h.querySelector('.ucban');
      if (b) b.style.backgroundImage = at ? 'url("/api/me/banner?v=' + at + '-' + bannerBust + '")' : '';
    });
    $('ucard').classList.toggle('has-ban', !!at);
    $('s-banner-clear').hidden = !at;
  }

  /* Цвет кружка виден, только пока в кружке буква. Стоит фото — выбор
     цвета ничего не меняет на экране, поэтому он выключен и подписан;
     убрали фото — снова включён. Гостю он выключен в любом случае. */
  function paintAvaLock(me) {
    var guest = !me || me.role === 'guest';
    var photo = !guest && !!me.has_avatar;
    document.querySelectorAll('#s-ava .sc').forEach(function (b) { b.disabled = guest || photo; });
    $('s-ava').classList.toggle('off', photo);
    $('s-ava-note').hidden = !photo;
  }

  /* Язык аниме: подсвечиваем выбранный (ru, endub или ensub). */
  function markAnimeLang(code) {
    document.querySelectorAll('#s-animelang button, #uc-alang button').forEach(function (b) {
      var on = b.dataset.animeLang === code;
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }

  /* Выбранная кнопка в ряду (цвет, значок): класс on для глаз и
     aria-pressed для экранного диктора — иначе он читал «мятный, кнопка»
     и не говорил, какой цвет выбран сейчас. */
  function mark(sel, attr, value) {
    document.querySelectorAll(sel).forEach(function (b) {
      var on = b.dataset[attr] === value;
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
    });
  }
  /* Значение переключателя-настройки, или null, если его на этой
     странице нет.

     Раньше отсутствующий переключатель считался включённым. Так было с
     autonext: в кабинете его нет (он живёт на странице просмотра), а
     «Сохранить» отправлял autonext: true — и молча включал обратно то,
     что человек выключил в плеере. null значит «не трогать»: такое поле
     в запрос не попадает, и сервер оставляет прежнее значение. */
  function getSwitch(key) {
    var b = document.querySelector('.sw[data-k="' + key + '"]');
    return b ? b.getAttribute('aria-checked') === 'true' : null;
  }

  /* Переключатели в кабинете.

     Раньше это были обычные галочки <input type="checkbox">. Выглядели
     они чужеродно — браузер рисует их своим стилем, который ни к чему
     на сайте не подходит, — и вели себя иначе, чем такой же
     переключатель на странице просмотра. Теперь везде одно и то же:
     кнопка с role="switch", как того требует разметка для доступности. */
  function getCheck(id) {
    var b = $(id);
    return !!b && b.getAttribute('aria-checked') === 'true';
  }

  function setCheck(id, on) {
    var b = $(id);
    if (b) b.setAttribute('aria-checked', on ? 'true' : 'false');
  }

  /* Один обработчик на оба переключателя: щелчок переворачивает
     состояние и сообщает об этом тому, кто на него подписан. */
  function onSwitch(id, handler) {
    var b = $(id);
    if (!b) return;
    b.addEventListener('click', function () {
      var next = !getCheck(id);
      setCheck(id, next);
      handler(next);
    });
  }

  /* Пока запрос входа в пути, второй не шлём. Класс busy убирает только
     мышь (pointer-events), а Enter в поле пароля проходил: два нажатия
     подряд — два входа, каждый по полсекунды подсчёта пароля на сервере
     и по сессии в базе. */
  var loginSending = false;
  $('login-form').addEventListener('submit', function (e) {
    e.preventDefault();
    if (loginSending) return;
    loginSending = true;
    var box = $('gate').querySelector('.box');
    A.showError($('gate-err'), '');
    A.busy(box, true);
    A.api.post('/api/auth/login', {
      login: $('lg-user').value, password: $('lg-pass').value,
      code: $('lg-code').value
    }).then(function (r) {
      $('lg-pass').value = '';
      $('lg-code').value = '';
      $('lg-code-box').hidden = true;
      /* setMe, а не applyLook: иначе оформление применилось бы, а
         «кто я» осталось бы пустым до первой перезагрузки страницы. */
      A.setMe(r.me);
      /* Язык сайта выбран на странице входа, а в аккаунте его ещё нет —
         переносим выбор в аккаунт, чтобы он был и на других устройствах.

         Если человек нажал язык прямо сейчас, на странице входа, —
         этот выбор важнее сохранённого в аккаунте: иначе страница
         после входа молча переключалась бы обратно. */
      var s = (r.me && r.me.settings) || {};
      var picked = A.pickedLang();
      if (picked && picked !== (s.ui_lang || s.lang)) A.applyLang(picked);
      if (r.me && r.me.settings && (picked || !(s.ui_lang || s.lang))) {
        A.api.post('/api/me/settings', { ui_lang: A.langCode() }).then(function (x) {
          if (x && x.settings && A.me) A.me.settings = x.settings;
        }).catch(function () { /* останется в браузере */ });
      }
      showApp(r.me);
    }).catch(function (err) {
      /* Сервер говорит «пароль верный, нужен код» отдельным сообщением.
         Показываем поле и просим только код — заново вводить пароль
         в этот момент незачем. */
      if (err.needCode) {
        $('lg-code-box').hidden = false;
        $('lg-code').focus();
        A.showError($('gate-err'), A.t('Введите код из приложения',
                                       'Enter the code from your app'));
        return;
      }
      A.showError($('gate-err'), err.message);
    }).finally(function () { A.busy(box, false); loginSending = false; });
  });

  $('btn-guest').addEventListener('click', function () {
    if (loginSending) return;
    loginSending = true;
    var box = $('gate').querySelector('.box');
    A.showError($('gate-err'), '');
    A.busy(box, true);
    A.api.post('/api/auth/guest').then(function (r) {
      A.setMe(r.me);
      showApp(r.me);
    }).catch(function (err) {
      A.showError($('gate-err'), err.message);
    }).finally(function () { A.busy(box, false); loginSending = false; });
  });

  /* Выход тоже может не случиться: сервер лежит, связь пропала. Раньше
     ошибка тонула молча — кнопка нажималась, а человек оставался внутри
     и не понимал почему. */
  function logoutFailed(err) {
    A.tell(A.t('Выйти не получилось', 'Could not sign out'), err.message);
  }

  $('btn-out').addEventListener('click', function (e) {
    e.stopPropagation();
    A.logout().catch(logoutFailed);
  });

  $('empty-retry').addEventListener('click', function () {
    var b = this;
    b.disabled = true;
    loadLibrary().then(function () { b.disabled = false; });
  });

  /* =============== полка =============== */
  /* Список не загрузился — это не то же самое, что «список пуст».
     Раньше при обрыве связи показывалось «Здесь пока пусто», и человек
     решал, что его список пропал. Теперь — честная ошибка и кнопка
     «Попробовать снова». Сообщение держим в state, чтобы при смене
     языка render() нарисовал его заново, а не «пусто». */
  function loadLibrary() {
    return A.api.get('/api/library').then(function (r) {
      state.items = r.items || [];
      state.loadError = '';
      render();
      fixMissingPosters();
      /* Колокольчик заводится сам, а не по нажатию: иначе о новой серии
         узнаёшь, только если догадаешься его нажать. См. startUpdates. */
      startUpdates();
    }).catch(function (err) {
      state.items = [];
      state.loadError = (err && err.message) || A.t('Нет связи с сервером', 'No connection to the server');
      render();
    });
  }

  /* Дописывает обложки тем тайтлам, у которых их нет.

     Картинка приезжает вместе с карточкой из поиска. Но тайтл может
     попасть в список и без неё — по ссылке, по закладке, или он сохранён
     старой версией. Раньше такой тайтл оставался с серой заглушкой
     навсегда: обложку никто и нигде не переспрашивал.

     Просим не больше нескольких за раз: каждая — обращение к чужому
     сайту, и вываливать туда весь список разом нельзя. Остальные
     подтянутся при следующем заходе. */
  var POSTER_FIX_AT_ONCE = 4;
  var posterTried = {};

  function fixMissingPosters() {
    if (!A.me || A.me.role === 'guest') return;
    var need = state.items.filter(function (it) {
      return !it.poster && it.key && it.source && !posterTried[it.key];
    }).slice(0, POSTER_FIX_AT_ONCE);
    if (!need.length) return;

    need.forEach(function (it) { posterTried[it.key] = true; });
    Promise.all(need.map(function (it) {
      return A.api.post('/api/library/poster',
                        {key: it.key, source: it.source, title: it.title})
        .then(function (r) { if (r && r.poster) it.poster = r.poster; })
        .catch(function () { /* нет так нет — заглушка останется */ });
    })).then(function () { render(); });
  }

  /* Две вкладки: что не досмотрено и что досмотрено.
     Всё, кроме «done», считается незаконченным — включая отложенное
     и брошенное: с точки зрения «досмотрел или нет» это одно и то же.
     Делит по вкладкам tabBoxes() — коробками, см. там. */

  /* Порядок один: сверху то, что трогали последним. Настройка сортировки
     убрана из кабинета — с одним списком без вкладок она лишняя. */
  function sortItems(list) {
    return list.slice().sort(function (a, b) { return b.updated_at - a.updated_at; });
  }

  /* Карточка собирается из узлов, а не склейкой строк:
     название приходит с чужого сайта и внутрь HTML не попадает. */
  function watchHref(it) {
    /* Обложку, год и жанры тащим с собой: если тайтла ещё нет в
       библиотеке, странице просмотра их взять больше неоткуда,
       и карточка потом сохранилась бы без картинки. */
    return '/watch?key=' + encodeURIComponent(it.key) +
           '&source=' + encodeURIComponent(it.source || '') +
           '&title=' + encodeURIComponent(it.title || '') +
           '&poster=' + encodeURIComponent(it.poster || '') +
           '&year=' + encodeURIComponent(it.year || '') +
           '&genres=' + encodeURIComponent(it.genres || '') +
           '&total=' + encodeURIComponent(it.total_eps || 0) +
           (it.cid ? '&cid=' + encodeURIComponent(it.cid) : '') +
           /* Хентай, найденный не на вашем языке (fix-21): плеер откроет его
              на том языке, где он есть, и подпишет «Есть только на …». */
           (it.alang ? '&rx=1&alang=' + encodeURIComponent(it.alang) : '');
  }

  /* Убрать тайтл из своей полки.

     Ручка удаления на сервере есть с самого начала, а нажать её было
     негде: из интерфейса тайтл убрать было нельзя вообще никак. Один раз
     открыл — и он остался в списке навсегда. Кнопка живёт на самой
     карточке, потому что решение «убрать» приходит именно там, когда
     смотришь на список. */
  function removeCard(it) {
    var name = it.title || A.t('этот тайтл', 'this title');
    A.ask({
      title: A.t('Убрать из списка?', 'Remove from list?'),
      text: A.t('«' + name + '» пропадёт из списка вместе с отметками о просмотре.',
                '"' + name + '" will be removed along with your watch progress.'),
      ok: A.t('Убрать', 'Remove'),
      danger: true
    }).then(function (yes) {
      if (!yes) return;
      return A.api.del('/api/library/' + encodeURIComponent(it.key))
        .then(loadLibrary)
        .catch(function (err) { A.tell(A.t('Не получилось', 'Did not work'), err.message); });
    });
  }

  /* inLibrary=false для карточек из поиска: убирать оттуда нечего,
     кнопка там только сбивала бы с толку. */
  /* Карточка тайтла.

     Здесь дважды меняли крайности. Сначала на карточке жило всё сразу:
     счётчик, секунда остановки, полоса, значок «продолжить», строка
     «Смотрю · 502 серии» — пять чисел на одной картинке, уже не карточка,
     а таблица. Потом всё убрали, и стало непонятно, на чём остановился.

     Осталось ровно то, что отвечает на вопрос «где я»: сколько серий
     из скольких и полоса под обложкой. Одно число и одна линия.
  */
  function cardNode(it, inLibrary) {
    var a = A.el('a', 'card');
    a.href = watchHref(it);

    var wrap = A.el('div', 'wrap');
    if (inLibrary && A.me && A.me.role !== 'guest') {
      var del = A.el('button', 'del', '×');
      del.type = 'button';
      del.title = A.t('Убрать из списка', 'Remove from list');
      del.setAttribute('aria-label', A.t('Убрать из списка', 'Remove from list'));
      del.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        removeCard(it);
      });
      wrap.appendChild(del);
    }

    var pic = A.el('div', 'pic');
    var fill = A.el('i');
    /* Буква лежит под картинкой: не нашлось обложки — она и видна. */
    setPoster(fill, [it.poster], it, inLibrary);
    pic.appendChild(A.el('span', 'noart', (it.title || '?').charAt(0).toUpperCase()));
    pic.appendChild(fill);
    /* Результат поиска прямо у источника: ключ уже известен, искать
       ссылку не нужно — закладка сохраняет как есть. */
    if (!inLibrary && A.me && A.me.role !== 'guest') {
      pic.appendChild(planMark({
        find: function () { return itemByKey(it.key) || libItemFor(it.title); },
        target: function () {
          return Promise.resolve({
            key: it.key, source: it.source, title: it.title, title_en: it.title_en || '',
            poster: it.poster || '', year: +it.year || null, genres: it.genres || '',
            total_eps: it.total_eps || 0
          });
        }
      }));
    }

    var done = it.status === 'done';
    var seen = it.watched_ep || 0;
    var total = it.total_eps || 0;

    /* «Сколько всего» показываем, только если это число не противоречит
       тому, на чём вы остановились. Источники иногда отдают меньше серий,
       чем уже просмотрено: тайтл перевыложили кусками или это вообще
       другой тайтл. «2 / 1» на карточке выглядит как поломка, хотя сломан
       не сайт. В таком случае показываем просто номер серии. */
    var knownTotal = total > 0 && total >= seen;

    wrap.appendChild(pic);
    a.appendChild(wrap);
    a.appendChild(A.el('h3', null, it.title || A.t('Без названия', 'Untitled')));
    a.appendChild(progressRow(done, seen, total, knownTotal));
    return a;
  }

  /* =============== коробки ===============

     В списке лежат отдельные записи: каждая часть, каждый источник —
     своя строка. У «Ван-Пис» их выходило четыре подряд: сериал с одного
     источника, он же с другого, фильм и ещё один без обложки. Четыре
     одинаковых названия в ряд — это не список, это ошибка, которая
     выглядит как список.

     Поэтому записи складываются в коробку — одну на историю. На обложке
     то, что нужно знать не открывая: где вы остановились и закончили ли.
     Внутри — все части, и свои, и те, что ещё не смотрели. */

  /* Название без хвостов, по которому части узнают друг друга.
     «Ван-Пис», «Ван-Пис. Фильм», «Ван-Пис: Остров Рыболюдей» и
     «Ван-Пис 2» — одна история. */
  /* Отрезает подзаголовок после двоеточия или тире — но только если до
     него стоит настоящее название, а не приставка.

     Раньше резали всегда, и «Re:Zero. Жизнь с нуля…», «Re:Монстр» и
     «Re:Creators» превращались в одно и то же «re». Три разные истории
     ложились в одну коробку «Re», а крестик на ней удалял все три вместе
     с отметками о просмотре. «Re», «Dr», «K» — это часть имени, а не
     имя: короче четырёх букв и цифр голову не отделяем. */
  var MIN_HEAD = 4;
  function cutSubtitle(t) {
    var parts = t.split(/\s*[:—–]\s*/);
    var head = parts[0];
    if (parts.length < 2) return t;
    var letters = head.replace(/[^\wа-яё]/gi, '').length;
    return letters >= MIN_HEAD ? head : t;
  }

  function baseName(title) {
    var t = (title || '').toLowerCase().replace(/ё/g, 'е');
    t = t.replace(/\[[^\]]*\]|\([^)]*\)/g, ' ');   // счётчики серий в скобках
    t = t.split(' / ')[0];                          // «Наруто / Naruto»
    t = cutSubtitle(t);                             // подзаголовок после двоеточия
    t = t.replace(/[^\wа-я\s]+/gi, ' ');
    /* Хвост «2», «сезон 3», «фильм», «часть 2» — это не другая история,
       а её продолжение. */
    for (var i = 0; i < 2; i++) {
      t = t.replace(/\s+(?:\d+|[ivx]+)?\s*(?:сезон|season|часть|part|фильм|movie|тв|tv|ova|ona|спешл|special)\s*\d*\s*$/i, '');
      t = t.replace(/\s+\d+\s*$/, '');
    }
    return t.replace(/\s+/g, ' ').trim() || (title || '').toLowerCase();
  }

  /* Название истории из названия одной её части: те же отсечения, что и
     в baseName, но с сохранением исходного вида. baseName приводит всё
     к нижнему регистру и выкидывает знаки — для сравнения это то что
     нужно, а показывать «ван пис» человеку нельзя. */
  function boxTitle(title) {
    var t = (title || '').trim();
    var cut = cutSubtitle(t).trim();
    for (var i = 0; i < 2; i++) {
      cut = cut.replace(/[.,]?\s+(?:\d+|[IVX]+)?\s*(?:сезон|season|часть|part|фильм|movie|тв|tv|ova|ona|спешл|special)\s*\d*\s*$/i, '').trim();
      cut = cut.replace(/\s+\d+$/, '').trim();
    }
    /* Если после отсечений ничего осмысленного не осталось — оставляем
       как было: лучше длинное название, чем огрызок. */
    return cut.length >= 2 ? cut : t;
  }

  /* Собирает записи в коробки, сохраняя порядок «сверху то, что трогали
     последним». */
  function boxes(list) {
    var order = [];
    var by = {};
    list.forEach(function (it) {
      var k = baseName(it.title);
      if (!by[k]) { by[k] = {key: k, items: [], updated_at: 0}; order.push(k); }
      by[k].items.push(it);
      if (it.updated_at > by[k].updated_at) by[k].updated_at = it.updated_at;
    });
    /* Второй проход: коробка, чьё имя начинается с имени другой,
       переезжает в неё.

       Первый проход отрезает хвосты по списку слов — «сезон», «фильм»,
       номер, подзаголовок после двоеточия. Но продолжения называют и
       иначе: «Наруто Ураганные хроники» — это не «Наруто 2» и не
       «Наруто: что-то», перечислить все такие хвосты нельзя. Зато видно
       другое: имя начинается с имени первой части. Этого достаточно.

       Сравниваем по границе слова, иначе «Бета» утащила бы к себе
       «Бетани», а «Атака» — всё, что начинается на эти буквы. И хозяин
       должен быть не короче MIN_HEAD: коробка «Re» иначе снова собрала
       бы к себе и «Re Монстр», и «Re Creators». */
    order.slice().sort(function (a, b) { return a.length - b.length; })
      .forEach(function (k) {
        if (!by[k]) return;
        var host = order.filter(function (other) {
          return by[other] && other !== k && other.length < k.length
                 && other.replace(/\s+/g, '').length >= MIN_HEAD
                 && k.indexOf(other + ' ') === 0;
        }).sort(function (a, b) { return a.length - b.length; })[0];
        if (!host) return;
        by[host].items = by[host].items.concat(by[k].items);
        by[host].updated_at = Math.max(by[host].updated_at, by[k].updated_at);
        delete by[k];
      });
    order = order.filter(function (k) { return !!by[k]; });

    return order.map(function (k) {
      var box = by[k];
      /* Части внутри коробки — по порядку, в каком их трогали: сверху
         то, на чём остановились. */
      box.items.sort(function (a, b) { return b.updated_at - a.updated_at; });
      box.last = box.items[0];
      /* Имя коробки — самое короткое из названий: у «Ван-Пис» и
         «Ван-Пис: Остров Рыболюдей» историю зовут первым. */
      /* Имя коробки — общее начало названий, а не самое короткое из них.

         Разница видна там, где самой первой части в списке нет. У
         «Ван-Пис» лежали фильм, две OVA и пара спешлов, но не сам
         сериал — и коробка называлась «Ван-Пис. Фильм». Формально это
         правда самое короткое название, а по смыслу история называется
         «Ван-Пис». */
      var shortest = box.items.slice().sort(function (a, b) {
        return (a.title || '').length - (b.title || '').length;
      })[0] || box.last;
      box.title = boxTitle(A.listName(shortest) || shortest.title || box.last.title);
      box.title_en = shortest.title_en || '';
      box.poster = (box.items.filter(function (i) { return i.poster; })[0] || {}).poster || '';
      box.done = box.items.every(function (i) { return i.status === 'done'; });
      return box;
    }).sort(function (a, b) { return b.updated_at - a.updated_at; });
  }

  /* Обложки коробки по порядку: сначала выбранная для неё (box.poster),
     потом обложки остальных частей. */
  function boxPosters(box) {
    return [box.poster].concat(box.items.map(function (i) { return i.poster; }));
  }

  /* Картинка в шапке окна частей.

     Ставится не на саму шапку, а на свежий узел внутри неё. Окно одно
     на всех: открыли коробку А, не дождались обложки, открыли Б — и
     опоздавшая картинка А легла бы в шапку Б. Узел А к этому времени
     уже выброшен вместе со старым содержимым шапки, и опоздавшей
     картинке просто некуда лечь. */
  function frArt(urls, it, own, letterOf) {
    var art = $('fr-art');
    art.textContent = '';
    art.style.backgroundImage = '';
    var fill = A.el('i', 'frfill');
    var letter = A.el('span', 'noart', (letterOf || '?').charAt(0).toUpperCase());
    art.appendChild(letter);
    art.appendChild(fill);
    setPoster(fill, urls, it, own);
  }

  /* Карточка-коробка: одна на историю, будь в ней одна часть или пять.

     Как она устроена — и почему.

     Раньше на ней было три вещи: обложка, название и тонкая полоса с
     кружком и подписью «1 / 6». По ней нельзя было понять ни сколько
     осталось, ни где вы остановились внутри серии, ни что вышло новое.
     А слева у каждой карточки торчала зелёная полоска — намёк на «стопку
     частей», который никто не понимал и который был у всех карточек
     подряд, включая одиночные.

     Теперь каждая строка отвечает на свой вопрос:
       * на обложке, внизу — «на какой я серии» («Серия 3 из 12»), сверху
         — «вышло ли новое» (плашка «Новая серия»);
       * под названием — «что это» (части, год, источник);
       * внизу — «сколько осталось» и «с какой секунды продолжу», с
         полосой без лишних кружков.
     Надписи на обложке лежат на тёмной подложке, а не прямо на кадре:
     обложки бывают любые, и только так их видно на каждой. Всё под
     обложкой — на фоне страницы цветами темы, поэтому читается в любой
     теме и на любом празднике.

     Наведение или фокус с клавиатуры показывают на обложке ▶ и что
     именно случится по нажатию: «Продолжить», «Смотреть» или «Выбрать
     часть». */
  function boxNode(box) {
    /* Класс boxcard, а не box: короткое `box` уже занято формой входа,
       и карточка молча забирала её оформление — фон, рамку, отступы в
       тридцать пикселей и даже ограничение ширины. Второе такое
       столкновение подряд после `bar`: короткие имена в общем файле
       стилей почти всегда уже чьи-то. */
    var a = A.el('a', 'card boxcard');
    var last = box.last;
    var many = box.items.length > 1;
    a.href = watchHref(last);
    a._box = box;

    var wrap = A.el('div', 'wrap');
    /* bare — обложки нет: затемнение снизу нужно только картинке, на
       ровной заглушке (особенно светлой) оно выглядит грязным пятном. */
    var pic = A.el('div', 'pic bare');
    var fill = A.el('i');
    /* Пробуем обложки всех частей коробки: у одной адрес мог умереть,
       у другой — нет. Не загрузилась ни одна — ищем по самой свежей
       части, ей же сервер и допишет найденное. */
    setPoster(fill, boxPosters(box), box.items[0] || box.last, true, {
      onOk: function () { pic.classList.remove('bare'); }
    });
    pic.appendChild(A.el('span', 'noart', (box.title || '?').charAt(0).toUpperCase()));
    pic.appendChild(fill);

    var info = boxInfo(box);
    if (info.fresh) {
      a.classList.add('hasnew');
      pic.appendChild(A.el('span', 'newep', info.fresh));
    }
    var chip = A.el('span', 'epchip' + (box.done ? ' done' : '') + (box.later ? ' plan' : ''), info.chip);
    pic.appendChild(chip);

    /* ▶ поверх обложки — что будет по нажатию. Для мыши появляется при
       наведении, для клавиатуры — при фокусе (Tab); на телефоне наведения
       нет, и там она не мешает смотреть на обложку. */
    var hint = A.el('span', 'playhint');
    hint.setAttribute('aria-hidden', 'true');
    hint.appendChild(A.el('span', 'pbtn', many ? '☰' : '▶'));
    hint.appendChild(A.el('b', null, info.action));
    if (info.actionSub) hint.appendChild(A.el('small', null, info.actionSub));
    pic.appendChild(hint);

    /* Крестик живёт на самой обложке: под мышкой она приподнимается, и
       крестик, лежащий рядом, а не на ней, оставался бы на месте —
       на шесть пикселей мимо угла. */
    if (A.me && A.me.role !== 'guest') {
      var del = A.el('button', 'del', '×');
      del.type = 'button';
      del.title = A.t('Убрать из списка', 'Remove from list');
      del.setAttribute('aria-label', A.t('Убрать из списка', 'Remove from list'));
      del.addEventListener('click', function (e) {
        e.preventDefault();
        e.stopPropagation();
        removeBox(box);
      });
      pic.appendChild(del);
    }

    wrap.appendChild(pic);
    a.appendChild(wrap);

    a.appendChild(A.el('h3', null, box.title || A.t('Без названия', 'Untitled')));
    /* Строка «что это». Пустой она не бывает почти никогда, но место под
       неё держим всегда: иначе нижние строки соседних карточек разъедутся. */
    a.appendChild(A.el('div', 'meta', info.meta || ' '));

    /* Низ карточки: полоса и под ней две подписи.

       Полоса без кружка на конце: кружок выглядел как ползунок, за
       который хочется потянуть, а тянуть там нечего. Прижата к низу,
       чтобы у всех карточек в ряду она была на одной линии, как бы ни
       различалась длина названий. */
    var foot = A.el('div', 'foot');
    var line = A.el('div', 'lbar' + (box.done ? ' full' : '') + (info.pct === null ? ' nobar' : ''));
    var bar = A.el('i');
    bar.style.width = (info.pct === null ? 0 : Math.max(0, Math.min(100, info.pct))) + '%';
    line.appendChild(bar);
    foot.appendChild(line);
    var row = A.el('div', 'lrow');
    var left = A.el('span', 'l' + (info.hot ? ' hot' : ''), info.left || '');
    row.appendChild(left);
    var right = A.el('span', 'r', info.right || '');
    if (info.rightTitle) right.title = info.rightTitle;
    row.appendChild(right);
    foot.appendChild(row);
    a.appendChild(foot);

    /* Ссылка читается вслух целиком, а не «Ван-Пис, ×, Серия 40…». */
    a.setAttribute('aria-label', [box.title, info.fresh, info.chip, info.meta,
                                  info.left, info.right].filter(Boolean).join('. '));

    /* Нажатие открывает коробку, а не сразу плеер: в ней несколько
       частей, и выбрать должен человек. Если часть одна — открывать
       нечего, идём прямо в плеер. */
    if (many) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        openBox(box);
      });
    }
    return a;
  }

  /* Новая серия у тайтла: {now, fresh} или null.

     Пока колокольчик не спросил сервер, верим тому, что пришло вместе со
     списком (latest_ep — что нашла фоновая проверка). Когда спросил —
     только его ответу: после «я видел» сервер новинку уже погасил, а в
     загруженном раньше списке она ещё числится. */
  function freshOf(it) {
    if (it.status === 'later') return null;   // не начато — и новинок нет
    var u = updatesByKey[it.key];
    if (u) return {now: u.now, fresh: u.fresh};
    if (updatesLoaded) return null;
    var seenTotal = it.total_eps || 0;
    var latest = it.latest_ep || 0;
    if (it.status !== 'done' && seenTotal > 0 && latest > seenTotal) {
      return {now: latest, fresh: latest - seenTotal};
    }
    return null;
  }

  /* «из 2 частей», «из 21 части» — родительный падеж у русского слова
     свой, обычный plural здесь не подходит. */
  function ofParts(n) {
    return A.t('из ' + n + (n % 10 === 1 && n % 100 !== 11 ? ' части' : ' частей'),
               'of ' + n + (n === 1 ? ' part' : ' parts'));
  }

  /* «Осталась 1 серия», «Осталось 2 серии». */
  function leftEps(n) {
    return A.t((n % 10 === 1 && n % 100 !== 11 ? 'Осталась ' : 'Осталось ') + A.say(n, 'episode'),
               A.say(n, 'episode') + ' left');
  }

  /* Когда закрыли: «сегодня», «вчера», «3 дня назад», дальше — дата. */
  function whenText(ts) {
    if (!ts) return '';
    var d = new Date(ts * 1000);
    var today = new Date();
    today.setHours(0, 0, 0, 0);
    var days = Math.floor((today.getTime() - new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()) / 86400000);
    if (days <= 0) return A.t('сегодня', 'today');
    if (days === 1) return A.t('вчера', 'yesterday');
    if (days < 7) return A.t(A.count(days, ['день', 'дня', 'дней'], ['day', 'days']) + ' назад',
                             A.count(days, ['день', 'дня', 'дней'], ['day', 'days']) + ' ago');
    try {
      return d.toLocaleDateString(A.langCode() === 'en' ? 'en-GB' : 'ru-RU',
        days > 300 ? {day: 'numeric', month: 'short', year: 'numeric'} : {day: 'numeric', month: 'short'});
    } catch (e) {
      return d.toISOString().slice(0, 10);
    }
  }

  /* Всё, что пишется на карточке, — в одном месте и без DOM: так легче
     увидеть, какое состояние что показывает. */
  function boxInfo(box) {
    var it = box.last;
    var many = box.items.length > 1;
    var seen = it.watched_ep || 0;

    /* Новая серия — у той части, что смотрится, или у любой другой. */
    var fr = null;
    box.items.forEach(function (part) {
      var f = freshOf(part);
      if (f && (!fr || part === it)) fr = f;
    });
    var own = freshOf(it);
    var total = Math.max(it.total_eps || 0, own ? own.now : 0);
    /* «Сколько всего» показываем, только если это число не противоречит
       тому, на чём вы остановились. Источники иногда отдают меньше серий,
       чем уже просмотрено: «2 / 1» выглядит как поломка, хотя сломан не
       сайт. Тогда — просто номер серии. */
    var known = total > 0 && total >= seen;
    var pos = it.position || 0;
    var out = {chip: '', fresh: '', meta: '', left: '', right: '', rightTitle: '',
               pct: null, hot: false, action: '', actionSub: ''};

    if (fr) {
      out.fresh = fr.fresh > 1 ? '+' + A.say(fr.fresh, 'episode') : A.t('Новая серия', 'New episode');
    }

    if (box.later) {
      /* Запланированное: прогресса нет и быть не может — только пометка
         и сколько там серий. */
      out.chip = A.t('Планирую', 'Planned');
      out.meta = meta0(box, it);
      out.left = known ? A.say(total, 'episode') : A.t('Ещё не начинали', 'Not started yet');
      out.right = A.t('в планах', 'planned');
      out.action = many ? A.t('Выбрать часть', 'Choose a part') : A.t('Смотреть', 'Watch');
      out.actionSub = many ? '' : A.t('с первой серии', 'from episode 1');
      return out;
    }
    if (box.done) out.chip = '✓ ' + A.t('Досмотрено', 'Finished');
    else if (seen > 0 && known) out.chip = A.t('Серия ' + seen + ' из ' + total, 'Ep. ' + seen + ' of ' + total);
    else if (seen > 0) out.chip = A.t('Серия ' + seen, 'Episode ' + seen);
    else out.chip = A.t('Не начато', 'Not started');

    out.meta = meta0(box, it);

    if (many) {
      /* Коробка: прогресс по частям. Досмотренная часть — целая, начатая
         — своей долей, остальные — ноль. */
      var doneParts = 0;
      var sum = 0;
      box.items.forEach(function (p) {
        var s = p.watched_ep || 0;
        var tt = p.total_eps || 0;
        if (p.status === 'done') { doneParts++; sum += 1; }
        else if (tt > 0 && s > 0 && tt >= s) sum += s / tt;
      });
      out.pct = box.done ? 100 : sum / box.items.length * 100;
      out.left = box.done
        ? A.t('Все ' + A.say(box.items.length, 'part'), 'All ' + A.say(box.items.length, 'part'))
        : A.t(doneParts + ' ' + ofParts(box.items.length), doneParts + ' ' + ofParts(box.items.length) + ' done');
      if (box.done) {
        out.right = whenText(lastDone(box));
      } else if (fr) {
        out.right = A.t('вышла ' + fr.now + '-я', 'ep. ' + fr.now + ' out');
        out.hot = false;
      } else if (pos > 0) {
        out.right = A.t('с ', 'at ') + A.mmss(pos);
      }
      out.action = A.t('Выбрать часть', 'Choose a part');
      out.actionSub = box.done ? '' : A.t('сейчас: ', 'now: ') + (A.listName(it) || it.title || '');
    } else if (box.done) {
      out.pct = 100;
      out.left = total > 0 ? A.say(total, 'episode') : A.t('Досмотрено', 'Finished');
      out.right = whenText(it.done_at || it.updated_at);
      out.rightTitle = A.t('Досмотрено ', 'Finished ') + whenText(it.done_at || it.updated_at);
      out.action = A.t('Открыть', 'Open');
    } else if (fr) {
      out.pct = known ? seen / total * 100 : null;
      out.left = A.t('Вышла ' + fr.now + '-я серия', 'Episode ' + fr.now + ' is out');
      out.hot = true;
      if (pos > 0) out.right = A.t('с ', 'at ') + A.mmss(pos);
      out.action = A.t('Продолжить', 'Continue');
      out.actionSub = A.t('серия ', 'episode ') + Math.max(1, seen) + (pos > 0 ? ' · ' + A.mmss(pos) : '');
    } else if (seen > 0) {
      out.pct = known ? seen / total * 100 : null;
      if (known) {
        var rest = total - seen;
        out.left = rest > 0 ? leftEps(rest) : A.t('Последняя серия', 'Last episode');
      } else {
        out.left = A.t('Всего серий: ?', 'Total: ?');
      }
      if (pos > 0) out.right = A.t('с ', 'at ') + A.mmss(pos);
      out.action = A.t('Продолжить', 'Continue');
      out.actionSub = A.t('серия ', 'episode ') + seen + (pos > 0 ? ' · ' + A.mmss(pos) : '');
    } else {
      out.pct = 0;
      out.left = known ? A.say(total, 'episode') : A.t('Ещё не смотрели', 'Not watched yet');
      out.action = A.t('Смотреть', 'Watch');
      out.actionSub = A.t('с первой серии', 'from episode 1');
    }
    return out;
  }

  /* Строка «что это» под названием: части, год, источник. */
  function meta0(box, it) {
    var meta = [];
    if (box.items.length > 1) meta.push(A.say(box.items.length, 'part'));
    var years = box.items.map(function (p) { return +p.year || 0; }).filter(Boolean);
    if (years.length) meta.push(String(Math.min.apply(null, years)));
    if (it.source_label) meta.push(it.source_label);
    return meta.join(' · ');
  }

  /* Когда закрыта последняя из частей коробки. */
  function lastDone(box) {
    var best = 0;
    box.items.forEach(function (p) { best = Math.max(best, p.done_at || p.updated_at || 0); });
    return best;
  }

  /* Как назвать часть в окне подтверждения. Если у двух частей одно и то
     же название (один тайтл с двух источников), дописываем источник —
     иначе в списке стояли бы две одинаковые строки и было бы непонятно,
     что их две. */
  function partLabels(items) {
    var seen = {};
    items.forEach(function (it) {
      var n = A.listName(it) || it.title || '—';
      seen[n] = (seen[n] || 0) + 1;
    });
    return items.map(function (it) {
      var n = A.listName(it) || it.title || '—';
      var bits = [];
      if (it.year) bits.push(String(it.year));
      if (seen[n] > 1 && it.source) bits.push(it.source);
      return bits.length ? n + ' (' + bits.join(', ') + ')' : n;
    });
  }

  /* Убрать коробку целиком.

     Окно подтверждения перечисляет каждую часть по названию. Раньше в нём
     стояло только имя коробки и число — «Re: все 3 частей», — и не было
     видно, что под этим именем лежат три разные истории. Теперь человек
     видит ровно то, что пропадёт, до того как нажмёт «Убрать». */
  function removeBox(box) {
    var name = box.title || A.t('этот тайтл', 'this title');
    var many = box.items.length > 1;
    A.ask({
      title: A.t('Убрать из списка?', 'Remove from list?'),
      text: many
        ? A.t('Вместе с отметками о просмотре уберём ' +
              A.say(box.items.length, 'part') + ':',
              A.say(box.items.length, 'part') +
              ' will be removed along with your watch progress:')
        : A.t('«' + name + '» пропадёт из списка вместе с отметками о просмотре.',
              '"' + name + '" will be removed along with your watch progress.'),
      list: many ? partLabels(box.items) : null,
      ok: A.t('Убрать', 'Remove'),
      danger: true
    }).then(function (yes) {
      if (!yes) return;
      /* Каждое удаление ловим по отдельности. С Promise.all первая же
         ошибка обрывала всё: список не перечитывался, и на экране
         оставались части, которых на сервере уже нет. Теперь список
         перечитываем всегда, а о том, что не удалось, говорим прямо. */
      var failed = [];
      var labels = partLabels(box.items);
      return Promise.all(box.items.map(function (it, n) {
        return A.api.del('/api/library/' + encodeURIComponent(it.key))
          .catch(function (err) { failed.push(labels[n] + ' — ' + err.message); });
      })).then(function () {
        return loadLibrary();
      }).then(function () {
        if (!failed.length) return;
        A.tell(A.t('Убралось не всё', 'Not everything was removed'),
               A.t('Эти части остались в списке:\n\n', 'These parts are still in your list:\n\n') +
               failed.join('\n'));
      });
    });
  }

  /* Открытая коробка: сначала то, что уже в списке, потом всё остальное,
     что вышло по этой истории.

     Свои части показываются сразу, без сети: они уже на руках, и ждать
     справочник ради того, чтобы увидеть собственный список, незачем.
     Полный список частей приезжает следом и дописывается снизу. */
  function openBox(box) {
    A.showError($('fr-err'), '');
    $('fr-title').textContent = box.title || '';
    $('fr-sub').textContent = '';
    $('fr-note').textContent = A.say(box.items.length, 'part') +
                               A.t(' у вас в списке', ' in your list');

    frArt(boxPosters(box), box.items[0] || box.last, true, box.title);

    var wrap = $('fr-parts');
    wrap.textContent = '';
    wrap.appendChild(A.el('div', 'mgroup yes', A.t('Вы это смотрите', 'You are watching')));
    box.items.forEach(function (it) { wrap.appendChild(mineNode(it)); });

    vfr.classList.add('show');
    var seq = ++frSeq;

    /* Остальные части — из справочника. Названия своих записей уже
       заняты, поэтому из общего списка показываем только то, чего в
       списке ещё нет: иначе одна и та же часть стояла бы дважды. */
    var mine = {};
    box.items.forEach(function (it) { mine[baseKey(it.title)] = true; });

    A.api.get('/api/related?title=' + encodeURIComponent(box.last.title || box.title))
      .then(function (r) {
        if (seq !== frSeq) return;       // пока ждали, открыли другую коробку
        var rest = ((r && r.items) || []).filter(function (p) {
          return !mine[baseKey(p.title)];
        });
        if (!rest.length) return;
        wrap.appendChild(A.el('div', 'mgroup', A.t('Что ещё есть', 'What else there is')));
        renderParts(wrap, rest);
        $('fr-note').textContent = A.say(box.items.length, 'part') +
                                   A.t(' у вас в списке · ещё ', ' in your list · ') +
                                   rest.length + A.t(' рядом', ' more');
      })
      .catch(function () { /* справочник молчит — свои части всё равно видны */ });
  }

  /* Сравниваем названия так же грубо, как складываем коробки: у
     источника «Наруто Ураганные хроники», у справочника «Наруто:
     Ураганные хроники» — это одно и то же. */
  function baseKey(title) {
    return (title || '').toLowerCase().replace(/ё/g, 'е')
      .replace(/[^\wа-я\s]+/gi, ' ').replace(/\s+/g, ' ').trim();
  }

  /* Строка своей части: ведёт прямо в плеер, с прогрессом. */
  function mineNode(it) {
    var row = A.el('a', 'part mine');
    row.href = watchHref(it);

    var th = A.el('span', 'th');
    var fill = A.el('i');
    setPoster(fill, [it.poster], it, true);
    th.appendChild(fill);
    row.appendChild(th);

    var tx = A.el('span', 'tx');
    var nm = A.el('span', 'nm', A.animeName(it) || '—');
    if (it.status === 'done') {
      nm.appendChild(document.createTextNode(' '));
      nm.appendChild(A.el('span', 'first', A.t('досмотрено', 'finished')));
    }
    tx.appendChild(nm);

    var seen = it.watched_ep || 0;
    var total = it.total_eps || 0;
    var known = total > 0 && total >= seen;
    var bits = [];
    if (it.year) bits.push(String(it.year));
    if (seen > 0) {
      bits.push(known ? A.t('серия ', 'ep. ') + seen + ' / ' + total
                      : A.t('серия ', 'ep. ') + seen);
    } else {
      bits.push(A.t('ещё не начинали', 'not started'));
    }
    if (it.position > 0) bits.push(A.mmss(it.position));
    tx.appendChild(A.el('span', 'm', bits.join(' · ')));
    row.appendChild(tx);

    row.appendChild(A.el('span', 'go', '›'));
    return row;
  }

  /* Строка под названием: полоса и подпись.

     Раньше и то и другое лежало поверх обложки — число плашкой в углу,
     полоса у нижнего края картинки. На светлых кадрах их было не
     видно: под ними не ровный фон, а рисунок, и совпадение по цвету
     оставалось делом везения. Под обложкой фон всегда один, поэтому
     видно всегда.

     Строка рисуется у всех карточек, даже когда показывать нечего:
     иначе карточки в ряду разъезжались бы по высоте. */
  function progressRow(done, seen, total, knownTotal) {
    var row = A.el('div', 'cprog');
    var bar = A.el('div', 'cbar');
    var fill = A.el('i');
    bar.appendChild(fill);

    var подпись;
    if (done) {
      row.classList.add('full');
      fill.style.width = '100%';
      подпись = A.t('Досмотрено', 'Finished');
    } else if (knownTotal && seen > 0) {
      fill.style.width = Math.max(4, Math.min(100, seen / total * 100)) + '%';
      подпись = seen + ' / ' + total;
    } else if (seen > 0) {
      /* Источник отдал меньше серий, чем просмотрено: считать долю не
         от чего, поэтому полосы нет — только номер. */
      row.classList.add('nobar');
      подпись = A.t('серия ', 'ep. ') + seen;
    } else {
      fill.style.width = '0';
      подпись = A.t('не начато', 'not started');
    }

    row.appendChild(bar);
    row.appendChild(A.el('span', 'cnum', подпись));
    return row;
  }

  /* Вкладку выбирает коробка, а не запись.

     Раньше записи сначала делились по вкладкам, а потом складывались в
     коробки — и история, у которой первый сезон досмотрен, а второй нет,
     разваливалась на две карточки: «Re:Zero» в «Закончил» и «Re:Zero» в
     «Не закончил». Теперь история целиком: пока в ней есть недосмотренная
     часть, она в «Не закончил» и на карточке видно «1 из 2 частей». */
  /* «Планирую смотреть» — отдельная полка: там то, что отложено на
     потом и ещё не начато (статус «later»). Такие записи складываются в
     коробки отдельно от остальных. Иначе запланированный второй сезон
     утащил бы досмотренный первый обратно в «Не закончил». Как только
     серию начнут смотреть, плеер пришлёт «watching», и запись сама
     переедет из планов в «Не закончил». */
  function tabBoxes() {
    var sorted = sortItems(state.items);
    var all = boxes(sorted.filter(function (i) { return i.status !== 'later'; }));
    var later = boxes(sorted.filter(function (i) { return i.status === 'later'; }));
    later.forEach(function (b) { b.later = true; });
    return {
      open: all.filter(function (b) { return !b.done; }),
      done: all.filter(function (b) { return b.done; }),
      later: later
    };
  }

  /* Запись в списке с тем же названием, что у части из справочника. */
  /* Запись полки для части из справочника. Сначала по номеру справочника
     (cid): он один у аниме на всех источниках и языках — ровно то, по
     чему сервер держит одну карточку на аниме. Номера нет — по названию,
     русскому или латинскому. */
  function libItemFor(title, cid) {
    if (cid) {
      var byCid = state.items.filter(function (i) { return i.cid === cid; })[0];
      if (byCid) return byCid;
    }
    var k = baseKey(title);
    if (!k) return null;
    return state.items.filter(function (i) {
      return baseKey(i.title) === k || (i.title_en && baseKey(i.title_en) === k);
    })[0] || null;
  }

  function render() {
    repaintMarks();
    var split = tabBoxes();
    var list = split[state.tab] || split.open;
    var grid = $('grid');
    grid.textContent = '';
    list.forEach(function (box) { grid.appendChild(boxNode(box)); });

    /* Сколько в каждой вкладке — прямо на вкладке: видно, есть ли смысл
       туда переключаться, ещё до нажатия. */
    $('tabn-open').textContent = state.loadError || !split.open.length ? '' : String(split.open.length);
    $('tabn-done').textContent = state.loadError || !split.done.length ? '' : String(split.done.length);
    $('tabn-later').textContent = state.loadError || !split.later.length ? '' : String(split.later.length);

    /* Считаем истории, а не записи: четыре части «Ван-Пис» — это один
       тайтл в списке, а не четыре. */
    $('empty').classList.toggle('show', list.length === 0);
    $('empty').classList.toggle('bad', !!state.loadError);
    $('empty-retry').hidden = !state.loadError;
    if (state.loadError) {
      $('list-count').textContent = '';
      $('empty-t').textContent = A.t('Список не загрузился', 'Could not load your list');
      $('empty-s').textContent = state.loadError;
      return;
    }
    $('list-count').textContent = A.say(list.length, 'title');
    var noneAtAll = state.items.length === 0;
    if (state.tab === 'later') {
      $('empty-t').textContent = A.t('Пока ничего не запланировано', 'Nothing planned yet');
      $('empty-s').textContent = A.t('Нажмите «В планы» в списке частей после поиска.',
                                     'Press “Plan” in the list of parts after a search.');
      return;
    }
    $('empty-t').textContent = noneAtAll
      ? A.t('Здесь пока пусто', 'Nothing here yet')
      : (state.tab === 'done'
          ? A.t('Досмотренного пока нет', 'Nothing finished yet')
          : A.t('Всё досмотрено', 'All caught up'));
    $('empty-s').textContent = noneAtAll
      ? A.t('Найдите что-нибудь через поиск наверху.', 'Find something with the search up top.')
      : (state.tab === 'done'
          ? A.t('Как только досмотрите тайтл до конца, он появится здесь.',
                'Once you finish a title, it shows up here.')
          : A.t('Незаконченного не осталось — можно начать новое.',
                'Nothing left unfinished — time to start something new.'));
  }

  /* =============== фильтров больше нет ===============

     Здесь жили жанровые и годовые кнопки. Они отбирали среди того, что
     УЖЕ лежит в вашем списке, — то есть помогали найти своё, но никак
     не помогали найти новое. Ожидание от такой панели ровно обратное:
     раз есть «Жанр» и «Годы», значит по ним можно искать аниме.

     Сделать настоящий поиск по жанрам нечем: все восемь источников
     умеют ровно две вещи — текстовый поиск и список того, что выходит
     сейчас. Выборки по жанру нет ни у одного.

     Панель, которая обещает не то, что делает, хуже отсутствующей,
     поэтому она убрана целиком: разметка, стили, состояние и обработчики.
     На её месте в шапке — рулетка, которая раньше пряталась внутри неё. */

  /* =============== вкладки =============== */
  $('tabs').addEventListener('click', function (e) {
    var a = e.target.closest('a[data-tab]');
    if (!a) return;
    e.preventDefault();
    state.tab = a.dataset.tab;
    $('tabs').querySelectorAll('a').forEach(function (x) {
      x.classList.remove('on');
      x.removeAttribute('aria-current');
    });
    a.classList.add('on');
    a.setAttribute('aria-current', 'true');
    render();
  });

  /* =============== поиск ===============

     Поиск работает в два шага, и это главное, что нужно про него знать.

     Шаг первый — справочник. На «наруто» приходит одна карточка «Наруто»,
     а не двадцать одна строка, где вперемешку лежат второй сезон, фильм
     про Боруто и спешл про спортивный фестиваль. Раньше приходило именно
     второе: запрос уходил прямо на сайт с видео, а тот отвечал тем, что
     сам считает похожим. У RU-A на «наруто» самого «Наруто» нет
     вовсе — только «Ураганные хроники» и «Боруто».

     Шаг второй — по нажатию на карточку открывается список всех частей
     франшизы по годам: с чего начинать, что дальше, что можно пропустить.
     Ссылку на видео ищем только тогда, когда часть выбрана.

     Если справочник молчит, поиск возвращается к старому способу — прямо
     у источников. Хуже, но лучше, чем пустой экран. */
  var searchTimer = null;
  /* Номер последнего поиска. Ответы приходят не в том порядке, в каком
     ушли запросы: справочник на «нар» может ответить позже, чем на
     «наруто», — и тогда старый ответ затирал новый, на экране оказывались
     результаты не того, что написано в строке. Каждый ответ сверяет свой
     номер с текущим и, если он устарел, молча выбрасывается. */
  var searchSeq = 0;
  $('q').addEventListener('input', function () {
    clearTimeout(searchTimer);
    var q = this.value.trim();
    if (q.length < 2) { searchSeq++; $('search-wrap').hidden = true; return; }
    searchTimer = setTimeout(function () { doSearch(q); }, 500);
  });
  /* Enter в строке поиска раньше не делал ничего: поле стоит вне формы,
     и оставалось только ждать полсекунды задержки. Привычка нажимать
     Enter есть у всех, и молчание в ответ выглядит как поломка. */
  $('q').addEventListener('keydown', function (e) {
    if (e.key !== 'Enter') return;
    e.preventDefault();
    clearTimeout(searchTimer);
    var q = this.value.trim();
    if (q.length >= 2) doSearch(q);
  });
  $('search-clear').addEventListener('click', function (e) {
    e.preventDefault();
    searchSeq++;          // запоздавший ответ не должен снова открыть выдачу
    $('q').value = '';
    $('search-wrap').hidden = true;
    searchFoot(null);
  });

  function searchSkeletons() {
    var grid = $('search-grid');
    grid.textContent = '';
    for (var i = 0; i < 4; i++) {
      var sk = A.el('div', 'skeleton');
      sk.style.aspectRatio = '3/4.2';
      grid.appendChild(sk);
    }
    return grid;
  }

  /* Строка под выдачей про хентай. Сервер показывает rx только тот, что
     нашёлся хоть у одного источника — на любом языке: русская озвучка,
     английский дубляж или английские субтитры (fix-21; watchable — список жанра,
     rx_hidden — сколько спрятано из поиска по названию, rx_pending —
     сколько ещё проверяется в фоне: появятся при следующем поиске).
     res = null — убрать строку. */
  function searchFoot(res) {
    var foot = $('search-foot');
    if (!foot) return;
    var show = !!res && (res.watchable === true || res.rx_hidden > 0);
    foot.hidden = !show;
    if (!show) { foot.textContent = ''; return; }
    var tx = A.t('Показан только хентай, который реально есть у источников — на любом языке. ' +
                 'Если на вашем его нет, плеер включит тот язык, где он есть.',
                 'Only hentai that a source actually has is shown — in any language. ' +
                 'If it is not in yours, the player uses the language it is in.');
    if (res.rx_pending > 0) {
      tx += ' ' + A.t('Ещё ' + res.rx_pending + ' проверяется — повторите поиск через минуту.',
                      res.rx_pending + ' more still being checked — search again in a minute.');
    }
    foot.textContent = tx;
  }

  /* Ответы справочника на поиск — с памятью на пару минут: «Смотреть» в
     «Рандоме» ищет выпавшее, и этот поиск делается заранее, пока человек
     читает карточку (см. showPick). */
  var findMemo = {};
  var FIND_MEMO_MS = 2 * 60 * 1000;
  function findFor(q) {
    var k = A.langCode() + '|' + q;
    var hit = findMemo[k];
    if (hit && Date.now() - hit.at < FIND_MEMO_MS) return hit.promise;
    var promise = A.api.get('/api/find?q=' + encodeURIComponent(q) +
                            '&lang=' + encodeURIComponent(A.langCode()));
    findMemo[k] = { at: Date.now(), promise: promise };
    promise.catch(function () { if (findMemo[k] && findMemo[k].promise === promise) delete findMemo[k]; });
    return promise;
  }

  function doSearch(q) {
    var seq = ++searchSeq;
    $('search-wrap').hidden = false;
    $('search-note').textContent = A.t('ищем…', 'searching…');
    searchFoot(null);
    var grid = searchSkeletons();

    findFor(q)
      .then(function (res) {
        if (seq !== searchSeq) return;     // пока ждали, искали уже другое
        var rows = res.items || [];
        /* catalog:false — справочник не ответил. Это не «ничего не
           нашлось»: искать ещё есть где, просто хуже. */
        if (!res.catalog) return doSourceSearch(q, seq);
        grid.textContent = '';
        /* «этти», «ecchi», «18+» — запрос жанра, а не названия. Без
           включённого 18+ сервер списка не даёт, а искать слово «этти» у
           источников бессмысленно — говорим, где включить. То же с
           «хентай»: там нужны оба переключателя (hint: hentai_off). */
        if (res.hint === 'adult_off' || res.hint === 'guest' || res.hint === 'hentai_off') {
          $('search-note').textContent = '';
          grid.appendChild(res.genre === 'hentai'
            ? hentaiHint(res.hint, res.adult === true) : adultHint(res.hint));
          return;
        }
        searchFoot(res);
        /* Хентай был, но ни у одного источника (ни на одном языке) его
           нет — так и говорим. Искать то же название у источников незачем:
           справочник его знает, а источники только что сказали «нет». */
        if (!rows.length && (res.watchable || res.rx_hidden > 0)) {
          $('search-note').textContent = A.t('Сейчас этого нет ни у одного источника',
                                             'No source has this right now');
          return;
        }
        if (res.genre && !rows.length) {
          $('search-note').textContent = A.t('в жанре пока пусто', 'nothing in this genre yet');
          return;
        }
        if (!rows.length) {
          /* Справочник ответил и не знает такого. У источников бывает
             то, чего нет в справочнике, — стоит спросить и их. */
          return doSourceSearch(q, seq);
        }
        $('search-note').textContent = res.genre
          ? (res.genre === 'hentai' ? A.t('Хентай · 18+ · ', 'Hentai · 18+ · ')
                                    : A.t('Этти · 18+ · ', 'Ecchi · 18+ · ')) +
            A.say(rows.length, 'title') +
            A.t(' · самые популярные', ' · most popular first')
          : A.say(rows.length, 'title') +
            A.t(' · нажмите, чтобы увидеть все части',
                ' · tap to see every part');
        rows.slice(0, 24).forEach(function (r) {
          grid.appendChild(franchiseNode(r));
        });
        /* Первая карточка — самая вероятная: её части спрашиваем сразу. */
        frPrefetchLeft = 6;
        if (!res.genre) prefetchFranchise(rows[0]);
      })
      .catch(function (err) {
        if (seq !== searchSeq) return;
        grid.textContent = '';
        $('search-note').textContent = err.message;
      });
  }

  /* Подсказка вместо выдачи на запрос «этти» без включённого 18+.
     Кнопка ведёт прямо в нужный раздел кабинета. Гостю — только текст:
     настроек у него нет. */
  function adultHint(kind) {
    var box = A.el('div', 'adulthint');
    box.appendChild(A.el('span', 'agebadge', '18+'));
    var tx = A.el('div', 'ahtx');
    if (kind === 'guest') {
      tx.appendChild(A.el('b', null, A.t('Гостям 18+ не показывается', 'Guests never see 18+')));
      tx.appendChild(A.el('span', null, A.t(
        'Войдите в свой аккаунт и включите «Показывать 18+» в Настройки → Просмотр.',
        'Sign in to your account and turn on “Show 18+” in Settings → Watching.')));
      box.appendChild(tx);
      return box;
    }
    tx.appendChild(A.el('b', null, A.t('Этти скрыто настройкой', 'Ecchi is hidden by a setting')));
    tx.appendChild(A.el('span', null, A.t(
      'Включите «Показывать 18+» в Настройки → Просмотр — и здесь появится список самых популярных этти.',
      'Turn on “Show 18+” in Settings → Watching and the most popular ecchi titles will appear here.')));
    box.appendChild(tx);
    var go = A.el('button', 'chipbtn primary', A.t('Открыть настройку', 'Open the setting'));
    go.type = 'button';
    go.addEventListener('click', function () { openSet('view'); });
    box.appendChild(go);
    return box;
  }

  /* То же для запроса «хентай». Гостю — что только для вошедших (без
     кнопки: настроек у него нет). Вошедшему — каких переключателей не
     хватает, и кнопка, которая открывает «Просмотр» прямо на «Хентай».
     adultOn — включено ли уже 18+ (сервер присылает в ответе). */
  function hentaiHint(kind, adultOn) {
    var box = A.el('div', 'adulthint');
    box.appendChild(A.el('span', 'agebadge hentai', A.t('18+ · хентай', '18+ · hentai')));
    var tx = A.el('div', 'ahtx');
    if (kind === 'guest') {
      tx.appendChild(A.el('b', null, A.t('Доступно только вошедшим', 'Signed-in users only')));
      tx.appendChild(A.el('span', null, A.t(
        'Гостям хентай не показывается. Войдите в свой аккаунт — переключатель «Хентай» в Настройки → Просмотр.',
        'Guests never see hentai. Sign in to your account — the “Hentai” switch is in Settings → Watching.')));
      box.appendChild(tx);
      return box;
    }
    tx.appendChild(A.el('b', null, A.t('Хентай скрыт настройкой', 'Hentai is hidden by a setting')));
    tx.appendChild(A.el('span', null, adultOn
      ? A.t('Включите «Хентай» в Настройки → Просмотр — и здесь появится список самых популярных.',
            'Turn on “Hentai” in Settings → Watching and the most popular titles will appear here.')
      : A.t('В Настройки → Просмотр включите сначала «18+», потом «Хентай» — и здесь появится список самых популярных.',
            'In Settings → Watching turn on “18+” first, then “Hentai”, and the most popular titles will appear here.')));
    box.appendChild(tx);
    var go = A.el('button', 'chipbtn primary', A.t('Открыть настройку', 'Open the setting'));
    go.type = 'button';
    go.addEventListener('click', hlpSettings('view', adultOn ? 's-hentai' : 's-adult'));
    box.appendChild(go);
    return box;
  }

  /* Карточка франшизы. Внешне это та же карточка, что и в списке, но
     ведёт она не на страницу просмотра, а в список частей: какая из
     двадцати девяти частей «Наруто» нужна — решать не здесь. */
  function franchiseNode(fr) {
    var a = A.el('a', 'card');
    a.href = '#';

    var wrap = A.el('div', 'wrap');
    var pic = A.el('div', 'pic');
    var fill = A.el('i');
    setPoster(fill, [fr.poster], fr, false);
    var frName = A.animeName(fr);
    pic.appendChild(A.el('span', 'noart', (frName || '?').charAt(0).toUpperCase()));
    pic.appendChild(fill);
    if (A.me && A.me.role !== 'guest') pic.appendChild(franchiseMark(fr));
    var frAge = A.ageBadge(fr);
    if (frAge) pic.appendChild(frAge);
    /* Оценки на карточке нет намеренно. Здесь висел бейдж «8.0» — чужой
       средний балл, выставленный людьми, которых вы не знаете. На выбор
       «что посмотреть» он не отвечает, а место занимает и глаз цепляет
       первым. Что это за история и когда вышло — отвечает. */
    wrap.appendChild(pic);
    a.appendChild(wrap);

    a.appendChild(A.el('h3', null, frName || A.t('Без названия', 'Untitled')));

    var bits = [];
    if (fr.year) bits.push(String(fr.year));
    var frKind = A.kindName(fr);
    if (frKind) bits.push(frKind);
    if (bits.length) a.appendChild(A.el('div', 'meta', bits.join(' · ')));

    a.addEventListener('click', function (e) {
      e.preventDefault();
      openFranchise(fr);
    });
    /* Навели (или коснулись) — части спрашиваем заранее. Короткая
       задержка: мышь, проезжающая мимо, ничего не спрашивает. */
    var hoverTimer = null;
    function soon() {
      clearTimeout(hoverTimer);
      hoverTimer = setTimeout(function () { prefetchFranchise(fr); }, 120);
    }
    a.addEventListener('pointerenter', soon);
    a.addEventListener('focus', soon);
    a.addEventListener('touchstart', function () { prefetchFranchise(fr); }, { passive: true });
    a.addEventListener('pointerleave', function () { clearTimeout(hoverTimer); });
    return a;
  }

  /* Старый поиск — прямо у источников. Остаётся запасным путём: им
     находится то, чего нет в справочнике, и им же сайт продолжает
     работать, когда справочник лежит. */
  function doSourceSearch(q, seq) {
    var grid = searchSkeletons();
    $('search-note').textContent = A.t('ищем у источников…', 'searching sources…');
    /* Источники видео — по языку АНИМЕ, не сайта. */
    return A.api.get('/api/search?q=' + encodeURIComponent(q) +
                     '&lang=' + encodeURIComponent(A.animeLang()) +
                     '&source=' + encodeURIComponent(currentSource()))
      .then(function (res) {
        if (seq !== searchSeq) return;     // ответ на устаревший запрос
        var rows = res.items || [];
        var used = res.source || currentSource();
        grid.textContent = '';
        if (!rows.length) {
          $('search-note').textContent = A.t('ничего не нашлось', 'nothing found');
          return;
        }
        /* Показываем, у кого нашлось: источники периодически ложатся,
           и человеку полезно видеть, откуда пришёл ответ.
           Считаем именно показанные карточки, а не всё, что прислал
           источник: раньше в строке стояло «34 результата», а на экране
           лежало двадцать четыре, и разница выглядела как потеря. */
        var shown = rows.slice(0, 24);
        $('search-note').textContent =
          shown.length + ' ' +
          A.plural(shown.length, ['результат', 'результата', 'результатов'],
                   ['result', 'results']) +
          A.t(' · источник: ', ' · source: ') + used;
        shown.forEach(function (r) {
          grid.appendChild(cardNode({
            key: r.key, source: used, title: r.title, poster: r.poster,
            year: r.year, total_eps: r.episodes_total || 0, watched_ep: 0,
            position: 0, status: 'later', genres: r.genres || '', updated_at: 0
          }));
        });
      })
      .catch(function (err) {
        if (seq !== searchSeq) return;
        grid.textContent = '';
        $('search-note').textContent = err.message;
      });
  }

  /* =============== части франшизы =============== */
  var vfr = $('veil-fr');
  /* Номер того, что сейчас открыто в окне частей. Окно одно на всех:
     открыли франшизу А, не дождались, закрыли, открыли Б — и опоздавший
     ответ про А дописывал свои части в окно Б под заголовком Б. Каждый
     ответ теперь сверяет номер и, если окно уже про другое, молчит.
     Esc для этого окна обрабатывает общий обработчик внизу файла —
     он закрывает только самое верхнее окно. */
  var frSeq = 0;

  function closeFranchise() {
    frSeq++;
    vfr.classList.remove('show');
  }

  $('fr-close').addEventListener('click', closeFranchise);
  vfr.addEventListener('click', function (e) { if (e.target === vfr) closeFranchise(); });

  /* Части франшизы — с памятью на странице (fix-21: «время загрузки —
     намного меньше»). Их спрашиваем заранее: у первой карточки выдачи —
     как только выдача пришла, у остальных — когда на карточку навели
     (или коснулись её). К нажатию ответ обычно уже на руках, и окно
     открывается сразу со списком. Ошибка не запоминается. */
  var frMemo = {};
  var FR_MEMO_MS = 3 * 60 * 1000;
  function getFranchise(id) {
    var hit = frMemo[id];
    if (hit && Date.now() - hit.at < FR_MEMO_MS) return hit.promise;
    var promise = A.api.get('/api/franchise?id=' + encodeURIComponent(id));
    frMemo[id] = { at: Date.now(), promise: promise };
    promise.catch(function () { if (frMemo[id] && frMemo[id].promise === promise) delete frMemo[id]; });
    return promise;
  }
  /* Сколько франшиз выдачи спрашивать заранее по наведению: мышь,
     проехавшая по всей сетке, не должна съесть лимит справочника. */
  var frPrefetchLeft = 6;
  function prefetchFranchise(fr) {
    if (!fr || !fr.id || frMemo[fr.id] || frPrefetchLeft <= 0) return;
    frPrefetchLeft--;
    getFranchise(fr.id).catch(function () {});
  }

  var lastFr = null;
  function openFranchise(fr) {
    lastFr = fr;
    A.showError($('fr-err'), '');
    var open = A.animeName(fr);
    $('fr-title').textContent = open;
    /* Во второй строке — второе имя тайтла, какое бы ни было первым. */
    var other = open === fr.title ? (fr.title_en || '') : (fr.title || '');
    $('fr-sub').textContent = other && other !== open ? other : '';
    $('fr-note').textContent = A.t('собираем список…', 'building the list…');

    frArt([fr.poster], fr, false, open);

    var box = $('fr-parts');
    box.textContent = '';
    for (var i = 0; i < 3; i++) {
      var sk = A.el('div', 'skeleton');
      sk.style.height = '58px';
      sk.style.marginBottom = '8px';
      box.appendChild(sk);
    }

    vfr.classList.add('show');
    var seq = ++frSeq;

    getFranchise(fr.id)
      .then(function (res) {
        if (seq !== frSeq) return;       // окно уже показывает другое
        var parts = res.items || [];
        box.textContent = '';
        if (!parts.length) {
          $('fr-note').textContent = A.t('частей не нашлось', 'no parts found');
          return;
        }
        $('fr-note').textContent = partsNote(parts);
        /* Порядок и «начни отсюда» решает сервер: он знает связи между
           частями (что чьё продолжение, что пересказ, что другая
           экранизация). Страница только рисует. */
        renderParts(box, parts);
        /* Ищем «Начни отсюда» у источников заранее, пока человек читает
           список: обычно нажимают именно её, и к нажатию сервер уже знает,
           где она лежит (и прогрел первую серию). Только её одну —
           каждый резолв стоит лимита поиска. */
        prefetchResolve(startPart(parts));
      })
      .catch(function (err) {
        if (seq !== frSeq) return;
        box.textContent = '';
        $('fr-note').textContent = '';
        A.showError($('fr-err'), err.message);
      });
  }

  /* Строка под заголовком окна: сколько в истории основного и сколько
     всего остального. «9 частей основной истории · 6 дополнительных»
     сразу говорит, что смотреть надо девять, а не пятнадцать.

     Если связей между частями сервер не знал (запасной справочник), так
     и пишем: порядок по датам выхода — это догадка, а не порядок
     просмотра. */
  function partsNote(parts) {
    if (!parts[0] || !parts[0].role) {
      /* Ответ старого вида — без порядка просмотра. */
      return A.say(parts.length, 'part') + A.t(' · по порядку выхода', ' · in release order');
    }
    if (parts[0].guess) {
      return A.say(parts.length, 'part') +
        (parts.length > 1
          ? A.t(' · по датам выхода, связи между частями неизвестны',
                ' · by release date, links between parts unknown')
          : '');
    }
    var mainCount = 0;
    parts.forEach(function (p) { if (p.role === 'main') mainCount++; });
    var rest = parts.length - mainCount;
    var text = A.count(mainCount, ['часть', 'части', 'частей'], ['part', 'parts']) +
               A.t(' основной истории', ' of the main story');
    if (rest) {
      text += ' · ' + A.count(rest, ['дополнительная', 'дополнительные', 'дополнительных'],
                              ['extra', 'extras']);
    }
    return text;
  }

  /* Список частей целиком: основная история по шагам, под каждым шагом —
     его дополнения, в конце — «другие версии» отдельной группой.

     Группу «другие версии» отделяем заголовком, а не только видом строк:
     туда попадает, например, OVA «ДжоДжо» 1993 года — старая отдельная
     экранизация. Раньше её предлагали смотреть первой; теперь она внизу,
     и заголовок прямо говорит, что её можно пропустить. */
  function renderParts(box, parts) {
    var headed = false;
    parts.forEach(function (p) {
      if (p.role === 'other' && !headed) {
        headed = true;
        box.appendChild(A.el('div', 'frgroup',
          A.t('Другие версии — можно пропустить', 'Other versions — optional')));
      }
      box.appendChild(partNode(p));
    });
  }

  /* Одна строка списка частей.

     Основная история — с номером шага: «1», «2», «3»… Первый шаг помечен
     «Начни отсюда»: без пометки человек, открывший «Наруто», видит
     двадцать девять строк и снова не знает, какую нажать, — то есть ровно
     ту беду, ради которой всё это делалось.

     Дополнения (фильмы, спешлы, пересказы) стоят под своим шагом со
     сдвигом вправо и с подписью, кем они приходятся истории. Они тише
     основных строк: смотреть их можно, но не обязательно. */
  function partNode(p) {
    /* Строка — это кнопка, а кнопку в кнопку не положишь. Поэтому
       строка и «В планы» лежат рядом в общей обёртке. Сдвиг дополнений
       вправо задаёт обёртка (класс xtra у неё), а не сама строка. */
    var line = A.el('div', 'prow');
    var row = A.el('button', 'part');
    row.type = 'button';
    var role = p.role || '';
    if (role === 'extra') { row.classList.add('xtra'); line.classList.add('xtra'); }
    if (role === 'other') row.classList.add('oth');
    if (p.main) row.classList.add('start');

    if (role === 'main' && p.step) {
      /* Номер для глаз; диктору он читается как «шаг 3». */
      var no = A.el('span', 'stepno', String(p.step));
      no.setAttribute('aria-label', A.t('шаг ', 'step ') + p.step);
      row.appendChild(no);
    }

    var th = A.el('span', 'th');
    var fill = A.el('i');
    setPoster(fill, [p.poster], p, false);
    th.appendChild(fill);
    row.appendChild(th);

    var tx = A.el('span', 'tx');
    var nameRow = A.el('span', 'nm', A.animeName(p) || '—');
    if (p.main) {
      /* Пробел отдельным узлом: без него название и пометка склеиваются
         в «НарутоНачни отсюда» при копировании и в речи экранного
         диктора. Глазами этого не видно — отступ рисует стиль. */
      nameRow.appendChild(document.createTextNode(' '));
      nameRow.appendChild(A.el('span', 'first', A.t('Начни отсюда', 'Start here')));
    }
    var partAge = A.ageBadge(p);
    if (partAge) {
      nameRow.appendChild(document.createTextNode(' '));
      nameRow.appendChild(partAge);
    }
    tx.appendChild(nameRow);

    var meta = A.el('span', 'm');
    var relText = A.t(p.rel_ru || '', p.rel_en || '');
    if (relText && role !== 'main') {
      meta.appendChild(A.el('span', 'plab', relText));
      meta.appendChild(document.createTextNode(' '));
    }
    var bits = [];
    bits.push(p.year ? String(p.year) : A.t('дата неизвестна', 'no date yet'));
    var pKind = A.kindName(p);
    if (pKind) bits.push(pKind);
    /* «Фильм · 1 серия» — не то, как про фильмы говорят. Счётчик нужен
       там, где серий много: у сериала он отвечает на вопрос «надолго ли
       это», у полнометражки не отвечает ни на какой. */
    if (p.episodes > 1 || (p.episodes === 1 && p.ongoing)) {
      bits.push(A.say(p.episodes, 'episode') +
                (p.ongoing ? A.t(' и продолжается', ' and counting') : ''));
    }
    meta.appendChild(document.createTextNode(bits.join(' · ')));
    tx.appendChild(meta);
    row.appendChild(tx);

    var go = A.el('span', 'go', '›');
    row.appendChild(go);

    row.addEventListener('click', function () { openPart(p, row, go); });
    line.appendChild(row);
    if (A.me && A.me.role !== 'guest') line.appendChild(planButton(p));
    return line;
  }

  /* =============== «В планы» ===============

     Кнопка рядом с каждой частью: отложить тайтл на потом, не открывая
     плеер. Под капотом — тот же поиск ссылки у источника, что и по
     нажатию на строку (resolvePart), только вместо перехода в плеер
     запись сохраняется со статусом «later» и нулевым прогрессом. Она
     появляется во вкладке «Планирую смотреть».

     Уже начатое или досмотренное в планы не кладётся — кнопки у такой
     части нет (сервер это тоже охраняет: «later» не затирает прогресс).
     Повторное нажатие на «В планах ✓» убирает из планов. */
  function planButton(p) {
    var b = A.el('button', 'planbtn');
    b.type = 'button';
    paintPlan(b, p);
    b.addEventListener('click', function (e) {
      e.stopPropagation();
      if (b.classList.contains('busy')) return;
      var it = libItemFor(p.title, p.cid);
      b.classList.add('busy');
      A.showError($('fr-err'), '');
      var job = it && it.status === 'later'
        ? A.api.del('/api/library/' + encodeURIComponent(it.key))
        : resolvedTarget(p).then(function (t) {
            if (!t) return;
            return savePlan(t).then(function (res) {
              /* Источник отдал тайтл, который у вас уже смотрится или
                 досмотрен (то же видео под другим названием). Сервер
                 прогресс не тронул — говорим, почему кнопка не
                 переключилась. */
              if (res && res.status && res.status !== 'later') {
                A.showError($('fr-err'), A.t(
                  'У источника это тот же тайтл, что уже есть в вашем списке, — в планы не добавлен.',
                  'The source has this as a title already in your list — not added to plans.'));
              }
            });
          });
      job.then(function () { return loadLibrary(); })
        .catch(function (err) {
          A.showError($('fr-err'), err.status === 404
            ? A.t('У источников этого не нашлось — в планы не добавлено.',
                  'No source has this — not added to plans.')
            : err.message);
        })
        .finally(function () {
          b.classList.remove('busy');
          /* Перерисовываем все кнопки окна: у двух строк может быть одно
             и то же название (одна часть из двух справочников). */
          document.querySelectorAll('#fr-parts .planbtn').forEach(function (x) {
            if (x._part) paintPlan(x, x._part);
          });
        });
    });
    return b;
  }

  /* Начинали ли смотреть запись по-настоящему. Страница просмотра кладёт
     тайтл в список уже при открытии (серия 1, секунда 0) — это ещё не
     «смотрю», и отложить его в планы можно. Сервер считает так же
     (store._started). */
  function startedItem(it) {
    return !!it && it.status !== 'later' &&
      (it.status === 'done' || (it.watched_ep || 0) > 1 || (it.position || 0) > 0);
  }

  function itemByKey(key) {
    return state.items.filter(function (i) { return i.key === key; })[0] || null;
  }

  /* Часть из справочника → что сохранить. Ищем её у источников так же,
     как по нажатию на строку; неточное совпадение — спрашиваем.
     null — человек передумал. */
  function resolvedTarget(p) {
    return resolvePart(p).then(function (r) {
      var t = {
        key: r.key, source: r.source, title: p.title, title_en: p.title_en || '',
        cid: p.cid || '',
        poster: p.poster || r.poster || '', year: +(p.year || r.year) || null,
        genres: r.genres || '', total_eps: p.episodes || r.episodes_total || 0
      };
      if (r.exact) return t;
      return A.ask({
        title: A.t('Точного совпадения нет', 'No exact match'),
        text: A.t('У источника это лежит как «' + r.title + '». Добавить в планы?',
                  'The source has it as "' + r.title + '". Add it to your plans?'),
        ok: A.t('В планы', 'Plan')
      }).then(function (yes) { return yes ? t : null; });
    });
  }

  function savePlan(t) {
    return A.api.post('/api/library/progress', {
      key: t.key, source: t.source, title: t.title, title_en: t.title_en || '',
      cid: t.cid || '',
      poster: t.poster || '', year: t.year || null, genres: t.genres || '',
      total_eps: t.total_eps || 0, watched_ep: 0, position: 0, status: 'later'
    });
  }

  /* С чего начинать франшизу: часть с пометкой «Начни отсюда», иначе
     первый шаг основной истории, иначе первая по списку. */
  function startPart(parts) {
    return parts.filter(function (p) { return p.main; })[0] ||
           parts.filter(function (p) { return p.role === 'main'; })[0] || parts[0] || null;
  }

  /* Закладка «В планы» прямо на обложке в результатах поиска.

     Нажал — тайтл отложен в «Планирую смотреть», окно частей не
     открывается и страница никуда не уходит. У франшизы в планы идёт её
     начало (часть «Начни отсюда»): справочник спрашиваем сами, ссылку у
     источника ищем так же, как окно частей. Повторное нажатие убирает
     из планов. У того, что вы уже смотрите, закладки нет.

     o.find()   — запись в списке, если она есть;
     o.target() — Promise с тем, что сохранить (null — передумали). */
  var BOOKMARK = 'M5 2.6h8a.9.9 0 0 1 .9.9v12L9 12.3l-4.9 3.2v-12a.9.9 0 0 1 .9-.9Z';
  function bookmarkIcon() {
    var NS = 'http://www.w3.org/2000/svg';
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('width', '15');
    svg.setAttribute('height', '15');
    svg.setAttribute('viewBox', '0 0 18 18');
    svg.setAttribute('aria-hidden', 'true');
    var path = document.createElementNS(NS, 'path');
    path.setAttribute('d', BOOKMARK);
    path.setAttribute('stroke', 'currentColor');
    path.setAttribute('stroke-width', '1.7');
    path.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(path);
    return svg;
  }

  function planMark(o) {
    var b = A.el('button', 'planmark');
    b.type = 'button';
    b.appendChild(bookmarkIcon());
    var lbl = A.el('span', 'pmt');
    b.appendChild(lbl);

    b._paint = function () {
      var it = o.find();
      var on = !!(it && it.status === 'later');
      b.hidden = !!(it && !on && startedItem(it));
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
      lbl.textContent = on ? A.t('В планах ✓', 'Planned ✓') : A.t('В планы', 'Plan');
      var tip = on ? A.t('В «Планирую смотреть». Нажмите, чтобы убрать', 'In “Plan to watch”. Press to remove')
                   : A.t('Отложить в «Планирую смотреть»', 'Save to “Plan to watch”');
      b.title = tip;
      b.setAttribute('aria-label', tip);
    };
    b._paint();

    /* pointerdown тоже глушим: иначе карточка под пальцем успевает
       «нажаться» (оттиск, затемнение) до того, как сработает закладка. */
    b.addEventListener('pointerdown', function (e) { e.stopPropagation(); });
    b.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      if (b.classList.contains('busy')) return;
      var it = o.find();
      b.classList.add('busy');
      var job;
      if (it && it.status === 'later') {
        job = A.api.del('/api/library/' + encodeURIComponent(it.key)).then(function () {
          flash(A.t('Убрано из планов', 'Removed from plans'));
        });
      } else {
        job = o.target().then(function (t) {
          if (!t) return;
          return savePlan(t).then(function (res) {
            if (res && res.status && res.status !== 'later') {
              flash(A.t('Это вы уже смотрите — в планы не добавлено',
                        'You are already watching this — not added to plans'));
              return;
            }
            b._key = t.key;
            flash(A.t('Добавлено в «Планирую смотреть»', 'Added to “Plan to watch”'));
          });
        });
      }
      job.then(function () { return loadLibrary(); })
        .catch(function (err) {
          flash(err && err.status === 404
            ? A.t('У источников этого не нашлось — в планы не добавлено',
                  'No source has this — not added to plans')
            : (err && err.message) || A.t('Не получилось', 'Did not work'));
        })
        .finally(function () {
          b.classList.remove('busy');
          repaintMarks();
        });
    });
    return b;
  }

  function repaintMarks() {
    document.querySelectorAll('.planmark').forEach(function (x) { if (x._paint) x._paint(); });
  }

  /* Закладка для карточки франшизы из справочника. */
  function franchiseMark(fr) {
    var b = planMark({
      find: function () {
        /* Первая отрисовка идёт внутри planMark, когда b ещё не присвоена. */
        var seen = b || {};
        return (seen._key && itemByKey(seen._key)) || libItemFor(seen._title || fr.title) ||
               (fr.title_en ? libItemFor(fr.title_en) : null);
      },
      target: function () {
        return getFranchise(fr.id)
          .then(function (res) { return startPart(res.items || []); })
          .catch(function () { return null; })
          .then(function (p) {
            /* Справочник частей не отдал — берём саму франшизу: у неё
               те же имена, год и число серий. */
            p = p || {title: fr.title, title_en: fr.title_en, alt: fr.alt, year: fr.year,
                      episodes: fr.episodes, poster: fr.poster, cid: fr.cid || ''};
            if (!p.poster) p.poster = fr.poster || '';
            b._title = p.title;
            return resolvedTarget(p);
          });
      }
    });
    return b;
  }

  /* Короткое сообщение внизу экрана: «Добавлено», «Убрано». */
  var flashTimer = null;
  function flash(text) {
    var f = $('flash');
    if (!f) return;
    f.textContent = text;
    f.classList.add('show');
    clearTimeout(flashTimer);
    flashTimer = setTimeout(function () { f.classList.remove('show'); }, 2800);
  }

  function paintPlan(b, p) {
    b._part = p;
    var it = libItemFor(p.title, p.cid);
    var on = !!(it && it.status === 'later');
    b.hidden = !!(it && !on && startedItem(it));
    b.classList.toggle('on', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    b.setAttribute('data-ru', on ? 'В планах ✓' : 'В планы');
    b.setAttribute('data-en', on ? 'Planned ✓' : 'Plan');
    b.textContent = on ? A.t('В планах ✓', 'Planned ✓') : A.t('В планы', 'Plan');
    b.title = on ? A.t('Убрать из планов', 'Remove from plans')
                 : A.t('Отложить в «Планирую смотреть»', 'Save to “Plan to watch”');
  }

  /* Часть выбрана — ищем её у источников видео.

     Справочник знает, что «Наруто» существует и когда вышел, но ссылок
     на серии у него нет. Их знают только источники, и знают под своими
     названиями. Поэтому здесь отдельный шаг: название превращается в
     пару «источник + номер тайтла», с которой открывается плеер. */
  var resolveMemo = {};
  var RESOLVE_MEMO_MS = 10 * 60 * 1000;
  function resolveKey(p) {
    return [p.cid || '', p.title || '', p.year || '', A.animeLang(), currentSource()].join('|');
  }
  function prefetchResolve(p) {
    if (!p || !A.me) return;
    resolvePart(p).catch(function () { /* нажмут — спросим снова */ });
  }
  function resolvePart(p) {
    var k = resolveKey(p);
    var hit = resolveMemo[k];
    if (hit && Date.now() - hit.at < RESOLVE_MEMO_MS) return hit.promise;
    var promise = resolvePartNow(p);
    resolveMemo[k] = { at: Date.now(), promise: promise };
    promise.catch(function () { if (resolveMemo[k] && resolveMemo[k].promise === promise) delete resolveMemo[k]; });
    return promise;
  }
  function resolvePartNow(p) {
    /* С какой серии откроется плеер — сервер прогреет именно её. */
    var mine = libItemFor(p.title, p.cid);
    /* Сколько серий обещает справочник — подсказка серверу: у «Ван-Пис»
       один источник выложил семь серий из тысячи с лишним, и без этого
       числа отличить огрызок от полного тайтла нечем. */
    /* alt — другие имена из справочника: у источника тайтл может лежать
       под ними («Старшая школа DxD» = «Демоны старшей школы»). year —
       чтобы из одноимённых сезонов выбрать нужный. Язык — АНИМЕ. */
    return A.api.get('/api/resolve?title=' + encodeURIComponent(p.title) +
              '&title_en=' + encodeURIComponent(p.title_en || '') +
              '&alt=' + encodeURIComponent((p.alt || []).join('|').slice(0, 1200)) +
              '&year=' + encodeURIComponent(p.year || '') +
              '&episodes=' + encodeURIComponent(p.episodes || 0) +
              '&lang=' + encodeURIComponent(A.animeLang()) +
              '&source=' + encodeURIComponent(currentSource()) +
              (mine && mine.watched_ep ? '&ep=' + encodeURIComponent(mine.watched_ep) : '') +
              /* Хентай ищется на любом языке, если на вашем его нет. */
              (p.hentai ? '&rx=1' : ''));
  }

  function openPart(p, row, go) {
    if (row.classList.contains('busy')) return;
    row.classList.add('busy');
    go.textContent = '…';
    A.showError($('fr-err'), '');

    resolvePart(p)
      .then(function (r) {
        var go_ = function () {
          location.href = watchHref({
            key: r.key,
            source: r.source,
            title: p.title,
            /* Обложку берём из справочника: она крупнее и без надписей
               поверх, а у источников бывает и вовсе заглушка. */
            poster: p.poster || r.poster || '',
            year: p.year || r.year || '',
            genres: r.genres || '',
            total_eps: p.episodes || r.episodes_total || 0,
            cid: p.cid || '',
            alang: r.other_lang ? r.alang : ''
          });
        };
        /* Совпало неточно — спрашиваем. Молча открыть похожее название
           хуже, чем не открыть ничего: человек досмотрит полсерии,
           прежде чем поймёт, что это другой тайтл. */
        if (r.exact) return go_();
        return A.ask({
          title: A.t('Точного совпадения нет', 'No exact match'),
          text: A.t('У источника это лежит как «' + r.title + '». Открыть?',
                    'The source has it as "' + r.title + '". Open it?'),
          ok: A.t('Открыть', 'Open')
        }).then(function (yes) { if (yes) go_(); });
      })
      .catch(function (err) {
        A.showError($('fr-err'), err.message);
      })
      .finally(function () {
        row.classList.remove('busy');
        go.textContent = '›';
      });
  }

  /* =============== карточка профиля =============== */
  /* Нажатие на себя в шапке открывает не кабинет сразу, а карточку: кто
     я, сколько смотрю, тема и язык в одно касание, входы в настройки,
     итоги, правила и «Выйти». На широком экране она выпадает из-под
     плашки, на узком выезжает снизу листом. */
  var ucWrap = $('ucard-wrap'), ucard = $('ucard'), whoChip = $('who');
  var ucTimer = null;
  var УЗКО = '(max-width:620px)';

  function narrowScreen() {
    try { return matchMedia(УЗКО).matches; } catch (e) { return innerWidth <= 620; }
  }

  /* Всё, на чём может стоять фокус внутри окна, — для Tab по кругу и
     стрелок. Видимость проверяем по getClientRects: у спрятанного (hidden,
     display:none) прямоугольников нет. */
  function focusables(box) {
    return Array.prototype.filter.call(box.querySelectorAll(
      'a[href],button:not([disabled]),input:not([disabled]):not([type="hidden"]),' +
      'select:not([disabled]),textarea:not([disabled]),summary,[tabindex]:not([tabindex="-1"])'),
    function (n) { return n.getClientRects().length > 0 && n.tabIndex !== -1; });
  }

  /* Tab и Shift+Tab не уходят из открытого окна на страницу под ним. */
  function keepFocusIn(box, e) {
    if (e.key !== 'Tab') return;
    var f = focusables(box);
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && (document.activeElement === first || !box.contains(document.activeElement))) {
      e.preventDefault(); last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault(); first.focus();
    }
  }

  /* Кому что показывать. Гостю — время, язык (и сайта, и аниме) и
     «Войти»; без темы, цифр, настроек и итогов: для них нужен аккаунт. */
  function cardMode(me) {
    var guest = me.role === 'guest';
    $('uc-stats').hidden = guest;
    $('uc-guest').hidden = !guest;
    $('uc-theme-row').hidden = guest;
    $('uc-alang-row').hidden = !guest;
    $('uc-set').hidden = guest;
    $('uc-year').hidden = guest;
    $('uc-login').hidden = !guest;
    $('btn-out').hidden = guest;
    ucard.setAttribute('data-kind', guest ? 'guest' : (me.role === 'admin' ? 'admin' : 'user'));
  }

  function paintGuestLeft() {
    var left = Math.max(0, guestEnds - Date.now());
    var min = Math.ceil(left / 60000);
    $('uc-left').textContent = A.t('осталось ' + min + ' мин', min + ' min left');
    $('uc-left').setAttribute('data-ru', 'осталось ' + min + ' мин');
    $('uc-left').setAttribute('data-en', min + ' min left');
    $('uc-rail').style.width = Math.max(0, Math.min(100, left / guestTotal * 100)) + '%';
    $('uc-guest').classList.toggle('warn', min <= 5);
  }

  function paintCard() {
    if (!A.me) return;
    if (A.me.role === 'guest') {
      paintGuestLeft();
      return;
    }
    var t = tabBoxes();
    $('uc-n-open').textContent = t.open.length;
    $('uc-n-done').textContent = t.done.length;
    markTheme(document.documentElement.getAttribute('data-theme') || 'kak-seychas');
  }

  /* Карточка висит над страницей (position:fixed), а плашка — в шапке.
     Ставим карточку под плашку по её настоящему месту, правым краем к
     правому краю плашки. На узком экране это лист снизу — там место
     задаёт CSS. */
  function placeHole() {
    /* Страница под открытой карточкой слегка притемнена, а плашка в
       шапке — нет: в затемнении для неё оставлено «окошко» по её
       настоящему месту (см. .ucwrap::before в стилях). Так видно, чья
       это карточка и что плашка сейчас нажата. */
    var c = whoChip.getBoundingClientRect();
    ucWrap.style.setProperty('--chip-x', Math.round(c.left) + 'px');
    ucWrap.style.setProperty('--chip-y', Math.round(c.top) + 'px');
    ucWrap.style.setProperty('--chip-w', Math.round(c.width) + 'px');
    ucWrap.style.setProperty('--chip-h', Math.round(c.height) + 'px');
  }

  function placeCard() {
    placeHole();
    if (narrowScreen()) {
      ucard.style.top = ''; ucard.style.right = '';
      return;
    }
    var r = whoChip.getBoundingClientRect();
    ucard.style.top = Math.round(r.bottom + 12) + 'px';
    ucard.style.right = Math.max(12, Math.round(innerWidth - r.right)) + 'px';
  }

  function cardOpen() { return ucWrap.classList.contains('show'); }

  function openCard() {
    if (!A.me) return;
    menu(false);
    setBellOpen(false);
    paintCard();
    placeCard();
    ucWrap.classList.add('show');
    whoChip.setAttribute('aria-expanded', 'true');
    clearInterval(ucTimer);
    if (A.me.role === 'guest') ucTimer = setInterval(paintGuestLeft, 15000);
    /* Фокус — на первый вход, а не на крестик: с клавиатуры сразу
       можно идти по пунктам стрелками. */
    setTimeout(function () {
      var first = ucard.querySelector('.ucgo:not([hidden])');
      if (first) first.focus();
    }, 30);
  }

  function closeCard(back) {
    if (!cardOpen()) return;
    ucWrap.classList.remove('show');
    whoChip.setAttribute('aria-expanded', 'false');
    clearInterval(ucTimer);
    if (back) whoChip.focus();
  }

  whoChip.addEventListener('click', function () {
    if (cardOpen()) closeCard(false); else openCard();
  });
  whoChip.addEventListener('keydown', function (e) {
    if (e.target !== this) return;
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
      e.preventDefault();
      openCard();
    }
  });
  ucWrap.addEventListener('click', function (e) { if (e.target === ucWrap) closeCard(false); });
  $('uc-close').addEventListener('click', function () { closeCard(true); });
  ucard.addEventListener('keydown', function (e) {
    keepFocusIn(ucard, e);
    /* Стрелки вверх-вниз ходят по крупным пунктам, как в обычном меню. */
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    var items = focusables(ucard).filter(function (n) { return n.classList.contains('ucgo'); });
    var at = items.indexOf(document.activeElement);
    if (at < 0) return;
    e.preventDefault();
    var next = items[(at + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length];
    next.focus();
  });
  window.addEventListener('resize', function () { if (cardOpen()) placeCard(); });
  /* Прокрутили страницу под открытой карточкой — «окошко» в затемнении
     должно ехать вместе с плашкой. */
  window.addEventListener('scroll', function () { if (cardOpen()) placeHole(); }, {passive: true});

  $('uc-set').addEventListener('click', function () { closeCard(false); openSet(); });
  $('uc-admin').addEventListener('click', function () { closeCard(false); openSet('admin'); });
  /* Гостю «Войти» — это закрыть пропуск: после него открывается форма
     входа. Ошибка — как у «Выйти»: молча тонуть ей нельзя. */
  $('uc-login').addEventListener('click', function () {
    A.logout().catch(logoutFailed);
  });

  /* Тема из карточки сохраняется сразу: это быстрый выбор, кнопки
     «Сохранить» рядом нет. Не сохранилась — тема остаётся на экране,
     а человеку говорим, что в аккаунт она не попала. */
  document.querySelectorAll('#uc-theme .ucth').forEach(function (b) {
    b.addEventListener('click', function () {
      var th = b.dataset.th;
      markTheme(th);
      A.applyTheme(th);
      if (!A.me || A.me.role === 'guest') return;
      A.api.post('/api/me/settings', { theme: th }).then(function (r) {
        if (r && r.settings && A.me) A.me.settings = r.settings;
      }).catch(function (err) {
        A.tell(A.t('Тема не сохранилась', 'Theme not saved'), err.message);
      });
    });
  });

  /* =============== кабинет =============== */
  /* Разделы кабинета. Открывается тот, с которого пришли: «Настройки» —
     последний открытый, «Админка» — свой, совет про вход по коду —
     «Безопасность». */
  var vset = $('veil-set');
  var cabPanel = vset.querySelector('.panel');
  var cabTabs = [$('cab-t-profile'), $('cab-t-look'), $('cab-t-lang'),
                 $('cab-t-view'), $('cab-t-safe'), $('cab-t-admin')];
  var cabNow = 'profile';

  function secNode(name) { return $(name === 'admin' ? 'admin-grp' : 'cab-' + name); }

  function tabAllowed(tab) {
    /* Раздел «Админка» доступен только в окне админки (adminmode). */
    if (tab.id === 'cab-t-admin') return vset.classList.contains('adminmode') && A.me && A.me.role === 'admin';
    return !tab.hidden && !vset.classList.contains('adminmode');
  }

  function showSec(name, focusTab) {
    var tab = $('cab-t-' + name);
    if (!tab || !tabAllowed(tab)) { name = 'profile'; tab = $('cab-t-profile'); }
    var changed = name !== cabNow;
    cabNow = name;
    cabTabs.forEach(function (t) {
      var on = t === tab;
      t.classList.toggle('on', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      var sec = secNode(t.dataset.sec);
      if (!sec) return;
      sec.hidden = !on;
      sec.classList.toggle('on', on);
    });
    /* Анимация смены раздела — только при настоящей смене: открыть
       кабинет на том же разделе — не повод дёргать содержимое. */
    var cur = secNode(name);
    if (changed && cur) {
      cur.classList.remove('cabin');
      void cur.offsetWidth;
      cur.classList.add('cabin');
    }
    $('pbody').scrollTop = 0;
    /* На телефоне полоса разделов прокручивается вбок: выбранный должен
       быть на виду, а не за краем. */
    if (tab.scrollIntoView && narrowScreen()) {
      try { tab.scrollIntoView({ block: 'nearest', inline: 'nearest' }); } catch (e) { /* старый браузер */ }
    }
    if (focusTab) tab.focus();
  }

  cabTabs.forEach(function (t) {
    t.addEventListener('click', function () { showSec(t.dataset.sec, false); });
  });
  /* Полоса разделов — как вкладки: стрелки, Home и End, без Tab по
     каждой вкладке (Tab уходит сразу в содержимое). */
  $('cab-rail').addEventListener('keydown', function (e) {
    var keys = ['ArrowDown', 'ArrowRight', 'ArrowUp', 'ArrowLeft', 'Home', 'End'];
    if (keys.indexOf(e.key) < 0) return;
    var shown = cabTabs.filter(tabAllowed);
    var at = shown.indexOf(document.activeElement);
    if (at < 0) return;
    e.preventDefault();
    var next = at;
    if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = shown.length - 1;
    else if (e.key === 'ArrowDown' || e.key === 'ArrowRight') next = (at + 1) % shown.length;
    else next = (at - 1 + shown.length) % shown.length;
    showSec(shown[next].dataset.sec, true);
  });

  var setOpener = null;
  function openSet(sec) {
    setOpener = document.activeElement;
    /* Админка — отдельное окно: та же рамка, но без полосы разделов и без
       «Сохранить». Настройки — без админки. */
    vset.classList.toggle('adminmode', sec === 'admin');
    if (sec !== 'admin' && cabNow === 'admin') cabNow = 'profile';
    showSec(sec || cabNow, false);
    vset.classList.add('show');
    setTimeout(function () {
      var tab = vset.classList.contains('adminmode') ? $('set-close') : $('cab-t-' + cabNow);
      if (tab && vset.classList.contains('show')) tab.focus();
    }, 40);
  }
  /* Закрыть кабинет — значит и бросить черновик входа по коду. */
  function closeSet() { vset.classList.remove('show'); dropTwofaDraft(); }
  /* То же, что closeSet, и фокус возвращается туда, откуда кабинет
     открыли, а если это была карточка (её уже нет) — на плашку профиля
     в шапке. Иначе после Esc фокус терялся в начале страницы. */
  function leaveSet() {
    closeSet();
    var back = setOpener && document.body.contains(setOpener) &&
               setOpener.getClientRects().length ? setOpener : whoChip;
    setOpener = null;
    if (back && back.focus) back.focus();
  }
  $('set-close').addEventListener('click', leaveSet);
  vset.addEventListener('click', function (e) { if (e.target === vset) leaveSet(); });
  cabPanel.addEventListener('keydown', function (e) { keepFocusIn(cabPanel, e); });

  /* «Есть несохранённые изменения». Тема, цвет, имя и прочее, что уходит
     по «Сохранить», меняются на экране сразу — и легко закрыть кабинет,
     думая, что всё уже записано. Пометка у кнопки об этом напоминает. */
  function setDirty(on) {
    $('set-dirty').hidden = !on;
    $('set-save').classList.toggle('due', !!on);
  }
  ['s-accent', 's-theme', 's-holiday', 's-logo', 's-ava', 's-mailnew'].forEach(function (id) {
    $(id).addEventListener('click', function (e) {
      if (e.target.closest('button')) setDirty(true);
    });
  });
  ['s-name', 's-email'].forEach(function (id) {
    $(id).addEventListener('input', function () { setDirty(true); });
  });

  function lockForGuest(isGuest) {
    $('pbody').classList.toggle('ro', isGuest);
    $('pfoot').classList.toggle('ro', isGuest);
    $('lockbar').classList.toggle('show', isGuest);
    $('pbody').querySelectorAll('input, select, button').forEach(function (n) {
      if (n.closest('.lockbar')) return;
      /* Язык гостю менять можно: выбор живёт в его браузере. А «скоро» —
         выключено у всех, гость это или нет. */
      if (n.classList.contains('guest-ok')) { n.disabled = false; return; }
      if (n.dataset.soon) { n.disabled = true; return; }
      n.disabled = isGuest;
    });
    if (isGuest) {
      /* Через data-атрибуты: смена языка переписывает текст по ним. */
      var note = $('adult-note');
      note.setAttribute('data-ru', 'Гостю 18+ не показывается.');
      note.setAttribute('data-en', 'Guests never see 18+.');
      note.textContent = A.t(note.getAttribute('data-ru'), note.getAttribute('data-en'));
    }
    /* Общий цикл выше снимает disabled со всех кнопок вошедшего — а у
       «Хентай» он зависит ещё и от 18+, у цвета кружка — от фото. */
    paintHentai();
    paintAvaLock(A.me);
    /* Кнопку «Сохранить» раньше прятал только стиль .pfoot.ro. Прятать —
       не то же самое, что выключать: скрытую кнопку всё ещё можно нажать
       с клавиатуры, и гость получал в ответ короткое «403» без объяснений.
       Сервер его всё равно не пустит, но пугать человека незачем. */
    $('set-save').disabled = isGuest;
  }

  document.querySelectorAll('#s-accent .sc').forEach(function (b) {
    b.addEventListener('click', function () {
      mark('#s-accent .sc', 'a', b.dataset.a);
      document.documentElement.setAttribute('data-accent', b.dataset.a);
    });
  });
  /* Тема. Нажатие перекрашивает сайт сразу, не дожидаясь «Сохранить»:
     иначе выбирать цвет пришлось бы вслепую. В аккаунт значение уходит
     вместе с остальными настройками внешнего вида. */
  function markTheme(ключ) {
    document.querySelectorAll('#s-theme .theme, #uc-theme .ucth').forEach(function (b) {
      var свой = b.dataset.th === ключ;
      b.classList.toggle('on', свой);
      b.setAttribute('aria-pressed', свой ? 'true' : 'false');
    });
    themeNames();
  }

  /* Название выбранной темы рядом с подписью «Тема» в карточке профиля
     («Тема · Ночь») и подсказки на образцах — на языке сайта. Имена
     лежат в data-nm-ru / data-nm-en, а не в data-ru: по data-ru смена
     языка переписывает текст элемента целиком и стёрла бы сам образец. */
  function themeNames() {
    var name = $('uc-theme-name');
    document.querySelectorAll('#uc-theme .ucth').forEach(function (b) {
      var nm = A.t(b.dataset.nmRu, b.dataset.nmEn);
      b.title = nm;
      b.setAttribute('aria-label', A.t('Тема: ', 'Theme: ') + nm);
      if (b.getAttribute('aria-pressed') === 'true') {
        name.setAttribute('data-ru', b.dataset.nmRu);
        name.setAttribute('data-en', b.dataset.nmEn);
        name.textContent = nm;
      }
    });
  }
  document.addEventListener('langchange', themeNames);
  document.querySelectorAll('#s-theme .theme').forEach(function (b) {
    b.addEventListener('click', function () {
      markTheme(b.dataset.th);
      A.applyTheme(b.dataset.th);
    });
  });

  /* Праздник. «По календарю» — пустая строка, «Выключить» — off. */
  function markHoliday(ключ) {
    document.querySelectorAll('#s-holiday button').forEach(function (b) {
      var свой = (b.dataset.h || '') === (ключ || '');
      b.classList.toggle('on', свой);
      b.setAttribute('aria-pressed', свой ? 'true' : 'false');
    });
  }
  document.querySelectorAll('#s-holiday button').forEach(function (b) {
    b.addEventListener('click', function () {
      markHoliday(b.dataset.h || '');
      A.applyHoliday(b.dataset.h || '');
    });
  });

  document.querySelectorAll('#s-logo .logopick').forEach(function (b) {
    b.addEventListener('click', function () {
      mark('#s-logo .logopick', 'l', b.dataset.l);
      document.querySelectorAll('.logo-v').forEach(function (v) {
        v.classList.toggle('on', v.dataset.v === b.dataset.l);
      });
    });
  });
  document.querySelectorAll('#s-ava .sc').forEach(function (b) {
    b.addEventListener('click', function () {
      mark('#s-ava .sc', 'c', b.dataset.c);
      /* Пробуем цвет сразу везде, где виден аватар, — и букву на нём
         перекрашиваем тем же правилом, что при обычной отрисовке. */
      ['ava', 'set-ava', 'uc-ava'].forEach(function (id) {
        $(id).style.background = b.dataset.c;
        $(id).style.color = A.avaInk(b.dataset.c);
      });
    });
  });
  /* Общий обработчик для переключателей, у которых нет своего.

     Раньше он вешался на ВСЕ элементы с классом .sw — включая те, у
     которых уже есть собственный обработчик (вход по коду, письма).
     Щелчок по такому переключателю срабатывал дважды: сначала общий
     переворачивал состояние, потом свой переворачивал обратно. Внешне
     переключатель возвращался в исходное положение, а обработчику
     приходило значение, обратное тому, что человек выбрал: нажимаешь
     «включить», а сайт спрашивает «выключить?».

     Теперь общий обработчик берёт только те, что помечены data-k, —
     то есть настройки, которые просто хранят да/нет. */
  document.querySelectorAll('.sw[data-k]').forEach(function (b) {
    b.addEventListener('click', function () {
      b.setAttribute('aria-checked', b.getAttribute('aria-checked') === 'true' ? 'false' : 'true');
    });
  });
  /* Язык аниме: три варианта, сохраняется сразу. Тот же выбор есть и
     на странице просмотра — в меню с глобусом под плеером. */
  document.querySelectorAll('#s-animelang button, #uc-alang button').forEach(function (b) {
    b.addEventListener('click', function () {
      if (b.disabled) return;
      markAnimeLang(b.dataset.animeLang);
      A.setAnimeLang(b.dataset.animeLang).catch(function (err) {
        A.tell(A.t('Не сохранилось', 'Not saved'), err.message);
      });
    });
  });

  /* 18+. Сохраняется сразу, без «Сохранить»: это не оформление, а то,
     что показывает каталог, — и открытая выдача тут же перечитывается. */
  onSwitch('s-adult', function (on) {
    if (!A.me || A.me.role === 'guest') { setCheck('s-adult', false); paintHentai(); return; }
    /* Выключил 18+ — «Хентай» гаснет и сереет сразу, не дожидаясь
       ответа: сервер выключит его и сам (см. api_settings). */
    if (!on) setCheck('s-hentai', false);
    paintHentai();
    A.api.post('/api/me/settings', { adult: on }).then(function (r) {
      if (r && r.settings && A.me) A.me.settings = r.settings;
      setCheck('s-hentai', !!(r && r.settings && r.settings.hentai === true));
      paintHentai();
      refreshCatalog();
    }).catch(function (err) {
      setCheck('s-adult', !on);
      paintHentai();
      A.tell(A.t('Не сохранилось', 'Not saved'), err.message);
    });
  });

  /* «Хентай» — серый, пока нельзя: гостю никогда («Только для вошедших»),
     вошедшему — пока выключено 18+ («Сначала включи 18+»). Причина
     пишется прямо под описанием, через data-атрибуты — чтобы смена
     языка её переписала. */
  function paintHentai() {
    var sw = $('s-hentai'), why = $('hentai-why');
    if (!sw) return;
    var guestNow = !A.me || A.me.role === 'guest';
    var adultOn = getCheck('s-adult');
    var reason = guestNow ? ['Только для вошедших', 'Signed-in users only']
               : (!adultOn ? ['Сначала включи 18+', 'Turn on 18+ first'] : null);
    sw.disabled = !!reason;
    if (reason) setCheck('s-hentai', false);
    $('opt-hentai').classList.toggle('swlock', !!reason);
    why.hidden = !reason;
    if (reason) {
      why.setAttribute('data-ru', reason[0]);
      why.setAttribute('data-en', reason[1]);
      why.textContent = A.t(reason[0], reason[1]);
    }
  }

  /* Хентай. Сохраняется сразу, как и 18+. Включение — только после
     подтверждения «Мне есть 18 лет» в окне сайта (A.ask), а не в
     window.confirm: тот глушат встроенные браузеры, и он выглядит
     чужим. Пока окно открыто, переключатель стоит выключенным. */
  onSwitch('s-hentai', function (on) {
    if (!A.me || A.me.role === 'guest' || !getCheck('s-adult')) {
      setCheck('s-hentai', false);
      paintHentai();
      return;
    }
    if (!on) { saveHentai(false); return; }
    setCheck('s-hentai', false);
    A.ask({
      title: A.t('Только для взрослых', 'Adults only'),
      text: A.t('Хентай — аниме с откровенными сценами. Включайте, только если вам есть 18 лет. Выключить можно здесь же в любой момент.',
                'Hentai is anime with explicit scenes. Turn it on only if you are 18 or older. You can turn it off here at any time.'),
      ok: A.t('Мне есть 18 лет', 'I am 18 or older'),
      cancel: A.t('Отмена', 'Cancel')
    }).then(function (yes) {
      if (!yes || !getCheck('s-adult')) return;
      setCheck('s-hentai', true);
      saveHentai(true);
    });
  });

  function saveHentai(on) {
    A.api.post('/api/me/settings', { hentai: on }).then(function (r) {
      if (r && r.settings && A.me) A.me.settings = r.settings;
      /* Сервер — последнее слово: если 18+ у него выключено, хентай
         остался выключенным, и переключатель это покажет. */
      setCheck('s-hentai', !!(r && r.settings && r.settings.hentai === true));
      paintHentai();
      refreshCatalog();
    }).catch(function (err) {
      setCheck('s-hentai', !on);
      A.tell(A.t('Не сохранилось', 'Not saved'), err.message);
    });
  }

  /* Точки на фоне. Как и 18+, сохраняются сразу: это одна галочка, и
     ждать «Сохранить» ради неё незачем. Гостю — только на этой странице. */
  onSwitch('s-dots', function (on) {
    document.documentElement.setAttribute('data-dots', on ? 'on' : 'off');
    if (!A.me || A.me.role === 'guest') return;
    A.api.post('/api/me/settings', { dots: on }).then(function (r) {
      if (r && r.settings && A.me) A.me.settings = r.settings;
    }).catch(function (err) {
      setCheck('s-dots', !on);
      document.documentElement.setAttribute('data-dots', on ? 'off' : 'on');
      A.tell(A.t('Не сохранилось', 'Not saved'), err.message);
    });
  });

  /* «Следующая серия сама» — тот же переключатель, что под плеером, и
     сохраняется так же сразу. В «Сохранить» он не входит: у него нет
     data-k, и getSwitch('autonext') его не видит — иначе кнопка
     отправляла бы значение, которое уже записано. */
  onSwitch('s-autonext', function (on) {
    if (!A.me || A.me.role === 'guest') { setCheck('s-autonext', true); return; }
    A.api.post('/api/me/settings', { autonext: on }).then(function (r) {
      if (r && r.settings && A.me) A.me.settings = r.settings;
    }).catch(function (err) {
      setCheck('s-autonext', !on);
      A.tell(A.t('Не сохранилось', 'Not saved'), err.message);
    });
  });

  /* Перечитать то, что сейчас показано из каталога: выдачу поиска и
     открытое окно частей. Нужна после смены настройки 18+ и «Хентай». */
  function refreshCatalog() {
    var q = $('q').value.trim();
    if (!$('search-wrap').hidden && q.length >= 2) doSearch(q);
    if (vfr.classList.contains('show') && lastFr) openFranchise(lastFr);
  }

  $('s-name').addEventListener('input', function () {
    var v = this.value.trim() || (A.me ? A.me.name : '');
    $('whoami').textContent = v;
    $('set-name').textContent = v;
    $('uc-name').textContent = v;
  });

  /* Обработчик кнопок языка переехал в общий код: кнопки теперь есть на
     каждой странице, а не только здесь. */
  /* Смена языка меняет и названия аниме, не только надписи: список
     рисуется заново. */
  document.addEventListener('langchange', function () {
    if (A.me) { render(); renderBell(); paintNotify(); }
    showNews();
  });
  /* Названия карточек следуют и за языком аниме (A.listName). */
  document.addEventListener('animelangchange', function () {
    if (A.me) render();
  });

  /* Возвращает значение выбранной кнопки, а если выбранной нет — запасное.
     Раньше здесь стояло document.querySelector(...).dataset напрямую.
     Достаточно было, чтобы в настройках оказалось значение, которому не
     соответствует ни одна кнопка (сервер, например, принимал любой размер
     обложки от 120 до 280, а кнопок всего три) — и querySelector возвращал
     null, обращение к .dataset падало, и кабинет переставал сохраняться
     совсем, причём молча. Теперь падать нечему. */
  function pickedValue(sel, attr, fallback) {
    var node = document.querySelector(sel);
    var raw = node ? parseInt(node.dataset[attr], 10) : NaN;
    return isNaN(raw) ? fallback : raw;
  }

  /* Кнопка, которая шлёт запрос, на время запроса выключена.

     Раньше двойной щелчок по «Сохранить», «Сохранить пароль» или
     «Создать» уходил на сервер дважды. Чаще всего безобидно, но не
     всегда: второй «Создать» отвечал «Такой логин уже занят» про аккаунт,
     который только что создал первый, а второй «Сохранить пароль» —
     «Текущий пароль не подошёл», потому что пароль уже сменился. Человек
     видел ошибку там, где всё получилось. run() возвращает обещание;
     пока оно не выполнено, кнопка серая и нажатий не принимает. */
  function guarded(btn, run) {
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    var done = function () { btn.disabled = false; };
    var p;
    try { p = run(); } catch (e) { done(); throw e; }
    Promise.resolve(p).then(done, done);
  }

  $('set-save').addEventListener('click', function () {
    var btn = this;
    guarded(btn, saveSettings);
  });

  function saveSettings() {
    var settings = {
      ui_lang: A.langCode(),
      accent: document.documentElement.getAttribute('data-accent'),
      logo: pickedValue('#s-logo .logopick.on', 'l', 1),
      theme: (document.querySelector('#s-theme .theme.on') || {}).dataset
             ? document.querySelector('#s-theme .theme.on').dataset.th : 'kak-seychas',
      holiday: (document.querySelector('#s-holiday button.on') || {}).dataset
             ? (document.querySelector('#s-holiday button.on').dataset.h || '') : ''
    };
    /* Отправляем только то, у чего есть переключатель на этой странице. */
    var autonext = getSwitch('autonext');
    if (autonext !== null) settings.autonext = autonext;
    var colorBtn = document.querySelector('#s-ava .sc.on');
    var mailReq = A.api.post('/api/me/mail', {
      email: $('s-email').value.trim(),
      want: getCheck('s-mailnew')
    });
    return Promise.all([
      mailReq,
      A.api.post('/api/me/settings', settings),
      A.api.post('/api/me/profile', {
        display_name: $('s-name').value.trim(),
        avatar_color: colorBtn ? colorBtn.dataset.c : null
      })
    ]).then(function () {
      return A.loadMe();
    }).then(function (m) {
      if (m) paintMe(m);
      render();
      var s = $('saved');
      s.classList.add('show');
      setTimeout(function () { s.classList.remove('show'); }, 2200);
    }).catch(function (err) { A.tell(A.t('Не сохранилось', 'Not saved'), err.message); });
  }

  $('s-pass').addEventListener('click', function () {
    $('pass-box').hidden = !$('pass-box').hidden;
  });
  $('p-save').addEventListener('click', function () {
    guarded(this, function () {
      A.showError($('p-err'), '');
      return A.api.post('/api/me/password', {
        current: $('p-cur').value, new: $('p-new').value
      }).then(function () {
        /* Пароль сменился — сервер закрыл все входы, и этот тоже: всё,
           что браузер помнил о человеке, больше ни к чему. */
        A.forgetPerson();
        $('p-cur').value = '';
        $('p-new').value = '';
        return A.tell(A.t('Пароль изменён', 'Password changed'),
                      A.t('Войдите заново — на всех устройствах.',
                          'Sign in again — on every device.'));
      }).then(function () {
        location.href = '/';
      }).catch(function (err) { A.showError($('p-err'), err.message); });
    });
  });

  $('s-logoutall').addEventListener('click', function () {
    A.ask({
      title: A.t('Выйти на всех устройствах?', 'Sign out everywhere?'),
      text: A.t('Войти заново придётся везде, включая это устройство.',
                'You will need to sign in again everywhere, including here.'),
      ok: A.t('Выйти везде', 'Sign out'),
      danger: true
    }).then(function (yes) {
      if (!yes) return;
      return A.api.post('/api/auth/logout-all').then(function () {
        A.forgetPerson();
        location.href = '/';
      })
        .catch(logoutFailed);
    });
  });

  /* =============== фото =============== */
  var MAX_MB = 5, OUT = 128, SIZE = 220;
  var st = { base: 1, k: 1, x: 0, y: 0, w: 0, h: 0 };
  var vcrop = $('veil-crop'), stage = $('stage'), cimg = $('cropimg'), zoom = $('zoom');

  $('s-photo-btn').addEventListener('click', function () { $('s-photo').click(); });
  $('s-photo').addEventListener('change', function () {
    var f = this.files && this.files[0];
    this.value = '';
    if (!f) return;
    if (!/^image\/(jpeg|png|webp)$/.test(f.type)) {
      A.tell(A.t('Не тот формат', 'Wrong format'),
             A.t('Подойдут JPG, PNG или WebP.', 'JPG, PNG or WebP will work.'));
      return;
    }
    if (f.size > MAX_MB * 1024 * 1024) {
      A.tell(A.t('Файл великоват', 'File is too big'),
             A.t('Не больше ' + MAX_MB + ' МБ.', 'Up to ' + MAX_MB + ' MB.'));
      return;
    }
    var reader = new FileReader();
    reader.onload = function (e) { openCrop(e.target.result); };
    reader.readAsDataURL(f);
  });

  function draw() {
    var eff = st.base * st.k, dw = st.w * eff, dh = st.h * eff;
    st.x = Math.min(0, Math.max(SIZE - dw, st.x));
    st.y = Math.min(0, Math.max(SIZE - dh, st.y));
    cimg.style.transform = 'translate(' + st.x + 'px,' + st.y + 'px) scale(' + eff + ')';
  }
  function openCrop(src) {
    /* Файл назвался картинкой (расширение, тип), а внутри не картинка:
       раньше onload не приходил никогда, и нажатие «Выбрать файл» просто
       ничего не делало — ни окна обрезки, ни слова. */
    cimg.onerror = function () {
      cimg.removeAttribute('src');
      A.tell(A.t('Не получилось', 'Did not work'),
             A.t('Картинку не удалось прочитать.', 'Could not read the picture.'));
    };
    cimg.onload = function () {
      st.w = cimg.naturalWidth; st.h = cimg.naturalHeight;
      st.base = SIZE / Math.min(st.w, st.h); st.k = 1; zoom.value = 1;
      st.x = (SIZE - st.w * st.base) / 2;
      st.y = (SIZE - st.h * st.base) / 2;
      cimg.style.width = st.w + 'px'; cimg.style.height = st.h + 'px';
      draw();
      $('crophint').textContent = st.w + '×' + st.h + '  →  ' + OUT + '×' + OUT;
      vcrop.classList.add('show');
    };
    cimg.src = src;
  }
  zoom.addEventListener('input', function () {
    var e0 = st.base * st.k;
    var cx = (SIZE / 2 - st.x) / e0, cy = (SIZE / 2 - st.y) / e0;
    st.k = parseFloat(this.value);
    var e1 = st.base * st.k;
    st.x = SIZE / 2 - cx * e1; st.y = SIZE / 2 - cy * e1;
    draw();
  });
  var dragging = false, px = 0, py = 0;
  function down(e) { dragging = true; stage.classList.add('drag');
    var p = e.touches ? e.touches[0] : e; px = p.clientX; py = p.clientY; }
  function move(e) {
    if (!dragging) return;
    e.preventDefault();
    var p = e.touches ? e.touches[0] : e;
    st.x += p.clientX - px; st.y += p.clientY - py;
    px = p.clientX; py = p.clientY; draw();
  }
  function up() { dragging = false; stage.classList.remove('drag'); }
  stage.addEventListener('mousedown', down);
  stage.addEventListener('touchstart', down, { passive: true });
  window.addEventListener('mousemove', move);
  /* Палец — только на самой картинке. Касание всегда приходит туда, где
     началось, поэтому слушать его на всём окне незачем. А обработчик
     touchmove на окне с passive:false заставлял браузер перед КАЖДЫМ
     сдвигом пальца ждать скрипт — на всей странице и всегда, даже когда
     окно обрезки закрыто: прокрутка на телефоне шла рывками. */
  stage.addEventListener('touchmove', move, { passive: false });
  window.addEventListener('mouseup', up);
  window.addEventListener('touchend', up);
  $('crop-cancel').addEventListener('click', function () { vcrop.classList.remove('show'); });
  vcrop.addEventListener('click', function (e) { if (e.target === vcrop) vcrop.classList.remove('show'); });

  $('crop-ok').addEventListener('click', function () {
    var eff = st.base * st.k;
    var cv = document.createElement('canvas');
    cv.width = OUT; cv.height = OUT;
    var ctx = cv.getContext('2d');
    ctx.imageSmoothingQuality = 'high';
    ctx.drawImage(cimg, -st.x / eff, -st.y / eff, SIZE / eff, SIZE / eff, 0, 0, OUT, OUT);
    cv.toBlob(function (blob) {
      A.api.raw('/api/me/avatar', blob).then(function () {
        return A.loadMe();
      }).then(function (m) {
        if (m) paintMe(m);
        vcrop.classList.remove('show');
      }).catch(function (err) {
        A.tell(A.t('Не получилось', 'Did not work'), err.message);
      });
    }, 'image/jpeg', 0.9);
  });

  $('s-photo-clear').addEventListener('click', function () {
    A.api.raw('/api/me/avatar', new Blob([])).then(function () {
      return A.loadMe();
    }).then(function (m) { if (m) paintMe(m); })
      .catch(function (err) {
        /* Без этого кнопка «Убрать фото» при ошибке не делала ничего
           видимого — неотличимо от сломанной. */
        A.tell(A.t('Фото не убралось', 'Photo not removed'), err.message);
      });
  });

  /* =============== баннер =============== */
  /* Обрезки, как у фото, нет: шапка — полоса 3:1, и картинка просто
     обрезается по центру до неё и уменьшается до 960×320 здесь же, в
     браузере. Так уходит файл в десятки килобайт, а не снимок с
     телефона на пять мегабайт. Сервер всё равно проверяет и пересобирает
     её сам (api/main.py, «Баннер профиля»). */
  var BAN_W = 960, BAN_H = 320;
  $('s-banner-btn').addEventListener('click', function () { $('s-banner').click(); });
  $('s-banner').addEventListener('change', function () {
    var f = this.files && this.files[0];
    this.value = '';
    if (!f) return;
    if (!/^image\/(jpeg|png|webp)$/.test(f.type)) {
      A.tell(A.t('Не тот формат', 'Wrong format'),
             A.t('Подойдут JPG, PNG или WebP.', 'JPG, PNG or WebP will work.'));
      return;
    }
    if (f.size > MAX_MB * 1024 * 1024) {
      A.tell(A.t('Файл великоват', 'File is too big'),
             A.t('Не больше ' + MAX_MB + ' МБ.', 'Up to ' + MAX_MB + ' MB.'));
      return;
    }
    var reader = new FileReader();
    reader.onload = function (e) {
      var img = new Image();
      img.onload = function () {
        var w = img.naturalWidth, h = img.naturalHeight;
        var cw = w, ch = Math.round(w * BAN_H / BAN_W);
        if (ch > h) { ch = h; cw = Math.round(h * BAN_W / BAN_H); }
        var k = Math.min(1, BAN_W / cw);
        var cv = document.createElement('canvas');
        cv.width = Math.max(1, Math.round(cw * k));
        cv.height = Math.max(1, Math.round(ch * k));
        var ctx = cv.getContext('2d');
        ctx.imageSmoothingQuality = 'high';
        ctx.drawImage(img, (w - cw) / 2, (h - ch) / 2, cw, ch, 0, 0, cv.width, cv.height);
        cv.toBlob(function (blob) {
          A.api.raw('/api/me/banner', blob).then(function () {
            bannerBust++;
            return A.loadMe();
          }).then(function (m) { if (m) paintMe(m); })
            .catch(function (err) {
              A.tell(A.t('Баннер не поставился', 'Banner not set'), err.message);
            });
        }, 'image/jpeg', 0.88);
      };
      img.onerror = function () {
        A.tell(A.t('Не получилось', 'Did not work'),
               A.t('Картинку не удалось прочитать.', 'Could not read the picture.'));
      };
      img.src = e.target.result;
    };
    reader.readAsDataURL(f);
  });
  $('s-banner-clear').addEventListener('click', function () {
    A.api.raw('/api/me/banner', new Blob([])).then(function () {
      bannerBust++;
      return A.loadMe();
    }).then(function (m) { if (m) paintMe(m); })
      .catch(function (err) {
        A.tell(A.t('Баннер не убрался', 'Banner not removed'), err.message);
      });
  });

  /* =============== администратор =============== */
  /* В README сказано, что аккаунтами можно управлять «командой в консоли
     или в кабинете». В кабинете при этом была только кнопка «создать»:
     ручки выключения, включения и удаления на сервере есть с самого начала,
     а нажать их было негде. Ниже они появляются. */
  function adminAction(label, cls, onClick) {
    var b = A.el('button', 'small' + (cls ? ' ' + cls : ''), label);
    b.type = 'button';
    b.style.cssText = 'padding:4px 10px;font-size:12px';
    b.addEventListener('click', onClick);
    return b;
  }

  /* Ошибки «Включить / Выключить / Удалить» раньше писались в #a-err —
     а он внутри свёрнутой формы «Создать аккаунт», то есть невидим.
     Нажал «Удалить», сервер отказал, на экране ничего. */
  function runAdmin(promise) {
    A.showError($('admin-err'), '');
    return promise.then(loadAdmin).catch(function (err) {
      A.showError($('admin-err'), err.message);
    });
  }

  function loadAdmin() {
    return A.api.get('/api/admin/users').then(function (r) {
      A.showError($('admin-err'), '');
      var box = $('admin-list');
      box.textContent = '';
      r.users.forEach(function (u) {
        var row = A.el('div', 'acc');
        var mine = A.me && u.login === A.me.name;
        /* Аватар — как у самого человека в шапке: его цвет и его буква
           (раньше цвет был по роли). Своё фото видно и здесь; чужие фото
           сервер отдаёт только владельцу, поэтому у остальных — кружок. */
        var av = A.el('span', 'ava');
        A.paintAvatar(av, mine ? A.me : {
          avatar_color: u.avatar_color, display_name: u.display_name || u.login, has_avatar: false
        });
        row.appendChild(av);
        row.appendChild(document.createTextNode(u.login));
        row.appendChild(A.el('span', 'sp'));
        row.appendChild(A.el('em', null,
          u.disabled ? A.t('выключен', 'disabled')
            : (u.role === 'admin' ? A.t('владелец', 'owner')
              : A.t('входов: ' + u.sessions, 'sessions: ' + u.sessions))));

        if (u.disabled) {
          row.appendChild(adminAction(A.t('Включить', 'Enable'), null, function () {
            runAdmin(A.api.post('/api/admin/users/' + u.id + '/enable'));
          }));
        } else if (!mine) {
          row.appendChild(adminAction(A.t('Выключить', 'Disable'), 'warn', function () {
            A.ask({
              title: A.t('Закрыть вход для ' + u.login + '?', 'Block sign-in for ' + u.login + '?'),
              text: A.t('Список и отметки останутся — человек просто не сможет войти.',
                        'The list and progress stay — they just cannot sign in.'),
              ok: A.t('Закрыть вход', 'Block')
            }).then(function (yes) {
              if (yes) runAdmin(A.api.post('/api/admin/users/' + u.id + '/disable'));
            });
          }));
        }
        if (!mine) {
          row.appendChild(adminAction(A.t('Удалить', 'Delete'), 'warn', function () {
            /* Удаление уносит и аккаунт, и все его данные, поэтому просим
               подтвердить логином, а не просто «вы уверены». */
            A.ask({
              title: A.t('Удалить аккаунт ' + u.login + '?', 'Delete account ' + u.login + '?'),
              text: A.t('Пропадёт всё: список, отметки о просмотре, итоги года. Вернуть нельзя.',
                        'Everything goes: the list, watch progress, year stats. No undo.'),
              ok: A.t('Удалить навсегда', 'Delete for good'),
              danger: true
            }).then(function (yes) {
              if (yes) runAdmin(A.api.del('/api/admin/users/' + u.id));
            });
          }));
        }
        box.appendChild(row);
      });
      langText($('guests-now'), 'гостей сейчас: ' + r.guests_now, 'guests now: ' + r.guests_now);
    }).catch(function (err) {
      /* Раньше ошибка глоталась целиком, и администратор видел пустой
         список аккаунтов — как будто их нет. */
      A.showError($('admin-err'),
                  A.t('Список аккаунтов не загрузился: ', 'Could not load accounts: ') + err.message);
    });
  }

  $('a-new').addEventListener('click', function () {
    $('a-form').hidden = !$('a-form').hidden;
  });
  $('a-save').addEventListener('click', function () {
    guarded(this, function () {
      A.showError($('a-err'), '');
      return A.api.post('/api/admin/users', {
        login: $('a-login').value.trim(),
        password: $('a-pass').value,
        role: $('a-admin').checked ? 'admin' : 'user'
      }).then(function () {
        $('a-login').value = ''; $('a-pass').value = ''; $('a-admin').checked = false;
        $('a-form').hidden = true;
        loadAdmin();
      }).catch(function (err) { A.showError($('a-err'), err.message); });
    });
  });

  /* =============== реши за меня =============== */
  /* Раньше рулетка крутила ваш же список — то есть предлагала то, что
     вы и так однажды выбрали. Смысла в этом немного: если хочется
     «решите за меня», то как раз потому, что в своём списке всё надоело.

     Теперь она берёт случайное аниме из открытого каталога AniList
     (бесплатный, без ключей). Запрос идёт через наш сервер, а не прямо
     из браузера: иначе адрес каждого посетителя уходил бы на чужой
     сайт, а ответы нельзя было бы держать в общем кэше. */
  var vroul = $('veil-roul'), spinning = false;

  function openRoulette() {
    $('roul').classList.remove('done');
    $('pickres').textContent = '';
    $('roul-watch').hidden = true;
    A.showError($('roul-err'), '');
    vroul.classList.add('show');
    spin();
  }

  $('btn-roul').addEventListener('click', openRoulette);
  /* Сервер держит наготове несколько случайных карточек (main.RANDOM_POOL).
     Пустой запас (сервер только что запустился, или запас разобрали)
     досыпается заранее — когда кнопку вот-вот нажмут: навели, сфокусировали,
     коснулись. И один раз в простое после загрузки страницы. */
  var roulWarmAt = 0;
  function warmRoulette() {
    if (!A.me || Date.now() - roulWarmAt < 60000) return;
    roulWarmAt = Date.now();
    A.api.get('/api/random?warm=1').catch(function () {});
  }
  ['pointerenter', 'focus', 'touchstart'].forEach(function (ev) {
    $('btn-roul').addEventListener(ev, warmRoulette, ev === 'touchstart' ? { passive: true } : false);
  });
  setTimeout(function () {
    if (window.requestIdleCallback) window.requestIdleCallback(warmRoulette, { timeout: 4000 });
    else warmRoulette();
  }, 2500);
  $('roul-go').addEventListener('click', function () { spin(); });

  function spin() {
    if (spinning) return;
    spinning = true;
    A.showError($('roul-err'), '');
    $('roul').classList.remove('done');
    $('roul-watch').hidden = true;
    $('pickres').textContent = '';
    $('roul-go').disabled = true;
    $('roul-load').hidden = false;

    A.api.get('/api/random').then(function (r) {
      showPick(r);
    }).catch(function (err) {
      A.showError($('roul-err'), err.message);
    }).finally(function () {
      spinning = false;
      $('roul-go').disabled = false;
      $('roul-load').hidden = true;
    });
  }

  /* Английские подписи жанров. Каталог присылает жанры уже по-русски
     (сервер переводит их сам), отдельного английского поля нет. Пока его
     нет, переводим обратно — словарём A.genreName (app.js, общий с
     «Итогами года»); незнакомый русский жанр на английской странице
     просто не показываем: русское слово среди английских выглядит
     поломкой. */
  var CYRILLIC = /[а-яё]/i;

  function pickGenres(it) {
    if (A.langCode() !== 'en') return it.genres || [];
    /* Английские имена жанров присылает сам сервер (genres_en, fix-21);
       словарь — для ответов, где их нет. */
    if (it.genres_en && it.genres_en.length) return it.genres_en;
    return (it.genres || []).map(A.genreName).filter(function (g) { return !!g; });
  }

  /* Описание. Из основного каталога оно приходит по-русски, из
     запасного — по-английски. На английской странице русский абзац не
     показываем: лучше коротко, чем на чужом языке. */
  function pickAbout(it) {
    if (A.langCode() !== 'en') return it.about || '';
    return it.about && !CYRILLIC.test(it.about) ? it.about : '';
  }

  /* Абзац описания. Если оно пришло не на русском (русского ни в одном
     справочнике не нашлось и сервер взял английское — about_lang="en"),
     а сайт открыт по-русски, над абзацем честная подпись: иначе
     английский текст посреди русской страницы выглядит как поломка. */
  function aboutNode(text, lang) {
    var box = A.el('div', 'about');
    if (lang === 'en' && A.langCode() !== 'en') {
      box.appendChild(A.el('span', 'aboutlang', 'Описание на английском'));
    }
    /* Английская страница, а английского описания нет ни у одного
       справочника: лучше по-русски с честной подписью, чем ничего. */
    if (lang !== 'en' && A.langCode() === 'en' && CYRILLIC.test(text)) {
      box.appendChild(A.el('span', 'aboutlang', 'Only in Russian'));
    }
    box.appendChild(document.createTextNode(text));
    return box;
  }

  var pickSeq = 0;           // номер выпавшей карточки — см. showPick
  function showPick(it) {
    pickSeq++;
    var res = $('pickres');
    res.textContent = '';
    /* Название на языке сайта: на английской странице раньше стояло
       русское «Магическая битва», хотя каталог присылает и латинское. */
    var name = A.animeName(it);

    var art = A.el('div', 'cover');
    setPoster(art, [it.poster], it, false, {
      onFail: function () {
        art.appendChild(A.el('span', 'noart', (name || '?').charAt(0).toUpperCase()));
      }
    });
    res.appendChild(art);

    var pickName = A.el('div', 'nm', name || '—');
    var pickAge = A.ageBadge(it);
    if (pickAge) {
      pickName.appendChild(document.createTextNode(' '));
      pickName.appendChild(pickAge);
    }
    res.appendChild(pickName);

    var bits = [];
    if (it.year) bits.push(it.year);
    if (it.episodes) bits.push(A.say(it.episodes, 'episode'));
    if (it.score) bits.push(A.t('оценка ', 'score ') + it.score + '/100');
    if (bits.length) res.appendChild(A.el('div', 'm', bits.join(' · ')));

    var genres = pickGenres(it);
    if (genres.length) {
      var tags = A.el('div', 'rtags');
      genres.slice(0, 4).forEach(function (g) {
        tags.appendChild(A.el('span', 'tag', g));
      });
      res.appendChild(tags);
    }

    var about = pickAbout(it);
    if (about) {
      res.appendChild(aboutNode(about, it.about_lang));
    } else if (it.title) {
      /* Случайная карточка пришла без описания (у справочника его нет
         или подробная карточка не отдалась). Спрашиваем всю цепочку
         справочников — /api/about ищет ещё в двух. Пока ждали, могла
         выпасть другая карточка: тогда ответ уже не про неё. */
      var mark = ++pickSeq;
      A.api.get('/api/about?title=' + encodeURIComponent(String(it.title).slice(0, 300)) +
                '&title_en=' + encodeURIComponent(String(it.title_en || '').slice(0, 300)) +
                '&year=' + encodeURIComponent(it.year || '') +
                '&lang=' + encodeURIComponent(A.langCode()))
        .then(function (r) {
          if (pickSeq !== mark || !r || !r.found) return;
          /* Сервер уже выбрал лучший язык (английский, если он есть хоть
             где-то); русский — только запасной, с подписью. */
          var text = r.about || '';
          if (text) res.appendChild(aboutNode(text, r.about_lang));
        })
        .catch(function () { /* нет так нет */ });
    }

    /* «Смотреть» ищет выпавшее в справочнике — ищем заранее, пока человек
       читает карточку: по нажатию выдача появится сразу. Не мгновенно, а
       через секунду: тот, кто крутит «Ещё раз» подряд, не читает карточки,
       и его поиски только встали бы в очередь к справочнику впереди
       следующей карточки. */
    var findMark = pickSeq;
    if (it.title) {
      setTimeout(function () {
        if (pickSeq === findMark) findFor(it.title).catch(function () {});
      }, 1000);
    }
    /* Кнопка ищет выпавшее у наших источников — каталог знает про аниме,
       но видео у него нет. */
    var go = $('roul-watch');
    go.href = '#';
    go.hidden = false;
    go.onclick = function (e) {
      e.preventDefault();
      vroul.classList.remove('show');
      $('q').value = it.title;
      doSearch(it.title);
      window.scrollTo({top: 0, behavior: 'smooth'});
    };
    $('roul').classList.add('done');
  }

  $('roul-close').addEventListener('click', function () { vroul.classList.remove('show'); });
  vroul.addEventListener('click', function (e) { if (e.target === vroul) vroul.classList.remove('show'); });

  /* =============== новые серии =============== */
  /* Что делает колокольчик.

     Раньше он перечислял всё, что вы смотрите, и горел числом всегда.
     Уведомление, которое приходит постоянно и ни о чём, перестают
     замечать за неделю — это хуже, чем его отсутствие.

     Теперь он показывает ровно одно: у каких сохранённых тайтлов вышла
     НОВАЯ серия с тех пор, как вы их открывали. Нет новых — колокольчик
     пустой и без числа. Считает это сервер: спрашивает у источника номер
     последней серии и сравнивает с тем, что мы запомнили. */
  var bellDrop = $('bell-drop');
  var updates = [];
  var updatesByKey = {};
  var updatesLoaded = false;
  var updatesAt = 0;            // когда последний раз спросили сервер
  var updatesBusy = false;

  /* Как часто колокольчик спрашивает сервер, пока страница открыта.

     Сам сервер обходит источники раз в четверть часа и складывает ответ в
     базу; колокольчик только читает готовое — это быстро и источникам
     ничего не стоит. Десять минут здесь плюс пятнадцать там: новая серия
     видна на открытой странице не позже чем через ~25 минут, обычно
     раньше. Скрытая вкладка не спрашивает вовсе — зато, вернувшись на
     неё, человек видит свежее сразу. */
  var UPDATES_EVERY = 10 * 60 * 1000;
  var UPDATES_FIRST = 1500;     // после загрузки страницы: не мешаем ей открыться
  var UPDATES_AGAIN = 60 * 1000; // по возвращению на вкладку — не чаще раза в минуту
  var UPDATES_CLICK = 15 * 1000; // по нажатию на колокольчик — не чаще раза в 15 секунд

  function setBellOpen(on) {
    bellDrop.classList.toggle('show', on);
    $('btn-bell').setAttribute('aria-expanded', on ? 'true' : 'false');
  }

  $('btn-bell').addEventListener('click', function (e) {
    e.stopPropagation();
    setBellOpen(!bellDrop.classList.contains('show'));
    if (bellDrop.classList.contains('show') &&
        (!updatesLoaded || Date.now() - updatesAt > UPDATES_CLICK)) loadUpdates();
  });
  document.addEventListener('click', function (e) {
    if (!bellDrop.contains(e.target)) setBellOpen(false);
  });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && bellDrop.classList.contains('show')) {
      setBellOpen(false);
      $('btn-bell').focus();
    }
  });

  var updatesStarted = false;
  function startUpdates() {
    if (updatesStarted) return;
    updatesStarted = true;
    setTimeout(loadUpdates, UPDATES_FIRST);
    setInterval(function () {
      if (!document.hidden) loadUpdates();
    }, UPDATES_EVERY);
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden && Date.now() - updatesAt > UPDATES_AGAIN) loadUpdates();
    });
  }

  /* Вернулись со страницы просмотра кнопкой «Назад». Браузер может
     показать главную из своей памяти такой, какой её оставили, — с
     прогрессом до просмотра: «Серия 3 из 12», хотя только что
     досмотрена пятая. Перечитываем список и колокольчик. */
  window.addEventListener('pageshow', function (e) {
    if (!e.persisted || !A.me || $('app').hidden) return;
    loadLibrary();
    loadUpdates();
  });

  function loadUpdates() {
    /* Гостю проверять нечего: его список всегда пуст. */
    if (!A.me || A.me.role === 'guest') { updatesLoaded = true; renderBell(); return; }
    if (updatesBusy) return;
    updatesBusy = true;
    if (!updatesLoaded) renderBell(true);
    return A.api.get('/api/updates').then(function (r) {
      setUpdates(r.items || []);
      updatesLoaded = true;
      updatesAt = Date.now();
      renderBell();
      notifyFresh();
      syncCards();
    }).catch(function () {
      /* Не ответил — оставляем то, что было: пропавшая из-за обрыва связи
         новинка хуже, чем показанная чуть дольше. */
      if (!updatesLoaded) { updatesLoaded = true; renderBell(); }
    }).finally(function () { updatesBusy = false; });
  }

  function setUpdates(list) {
    updates = list;
    updatesByKey = {};
    list.forEach(function (u) { updatesByKey[u.key] = u; });
  }

  /* Новинки дошли до списка.

     Сервер мог вернуть досмотренный тайтл в «Не закончил» (вышла серия у
     онгоинга) — тогда список перечитываем: карточке надо переехать во
     вкладку. В остальных случаях перерисовываем только те карточки, у
     которых новинка появилась или погасла, — без того, чтобы весь ряд
     заново выезжал снизу каждые десять минут. */
  function syncCards() {
    var mine = {};
    state.items.forEach(function (it) { mine[it.key] = it; });
    var moved = updates.some(function (u) { return !mine[u.key] || mine[u.key].status === 'done'; });
    if (moved) { loadLibrary(); return; }
    var grid = $('grid');
    Array.prototype.slice.call(grid.children).forEach(function (card) {
      var box = card._box;
      if (!box) return;
      var was = card.classList.contains('hasnew');
      var now = box.items.some(function (p) { return !!freshOf(p); });
      if (was === now && !now) return;
      var fresh = boxNode(box);
      fresh.classList.add('still');
      grid.replaceChild(fresh, card);
    });
  }

  function renderBell(loading) {
    var list = $('bell-list');
    list.textContent = '';
    paintBellCount();
    $('bell-when').textContent = updatesAt
      ? A.t('проверено ', 'checked ') + agoText(updatesAt) : '';

    if (loading) {
      list.appendChild(A.el('div', 'df', A.t('Смотрим, что вышло…', 'Checking for new episodes…')));
      return;
    }
    if (!updates.length) {
      list.appendChild(A.el('div', 'df', A.t('Новых серий нет. Как только выйдет — покажем здесь.',
                                             'No new episodes. When one comes out, it shows up here.')));
      return;
    }

    updates.forEach(function (u) {
      var row = A.el('a', 'nrow' + (isRecent(u) ? ' fresh' : ''));
      row.href = updateHref(u);
      row.addEventListener('click', function () {
        setBellOpen(false);
        seenUpdate(u);
      });

      var th = A.el('span', 'th');
      var i = A.el('i');
      /* Пока картинки нет (или не нашлась) — ровная подложка. */
      i.style.background = 'linear-gradient(150deg,#6E93A6,#20242E)';
      i.style.backgroundSize = 'cover';
      i.style.backgroundPosition = 'center';
      setPoster(i, [u.poster], u, true);
      th.appendChild(i);
      row.appendChild(th);

      var nb = A.el('span', 'nb');
      nb.appendChild(A.el('b', null, A.animeName(u) || '—'));
      nb.appendChild(A.el('span', null, u.fresh > 1
        ? A.t('вышло ', '') + A.say(u.fresh, 'episode') + A.t(', до ' + u.now + '-й', ' out, up to ' + u.now)
        : A.t('вышла ' + u.now + '-я серия', 'episode ' + u.now + ' is out')));
      row.appendChild(nb);
      row.appendChild(A.el('span', 'when', u.new_at ? agoText(u.new_at * 1000) : A.t('новое', 'new')));
      list.appendChild(row);
    });
  }

  function paintBellCount() {
    var n = updates.length;
    $('bell-count').textContent = n;
    $('bell-count').hidden = !n;
    $('btn-bell').setAttribute('aria-label', A.t('Новые серии', 'New episodes') + (n ? ': ' + n : ''));
  }

  /* Ссылка из колокольчика ведёт прямо на новую серию (&ep=), а не на
     ту, где остановились: ради неё человек и нажал. */
  function updateHref(u) {
    return watchHref({
      key: u.key, source: u.source, title: u.title, poster: u.poster,
      year: u.year, genres: u.genres, total_eps: u.now
    }) + '&ep=' + encodeURIComponent(u.now);
  }

  /* Открыли — значит увидели. Гасим уведомление по этому тайтлу, иначе
     оно горело бы, пока не досмотришь до самой свежей серии. */
  function seenUpdate(u) {
    A.api.post('/api/updates/seen', {key: u.key, source: u.source, title: u.title}).catch(function () {});
  }

  /* Новинка за последние сутки — подсвечена в списке. */
  function isRecent(u) {
    return !!u.new_at && Date.now() - u.new_at * 1000 < 24 * 3600 * 1000;
  }

  function agoText(ms) {
    var min = Math.max(0, Math.round((Date.now() - ms) / 60000));
    if (min < 1) return A.t('только что', 'just now');
    if (min < 60) return A.t(min + ' мин назад', min + ' min ago');
    var h = Math.round(min / 60);
    if (h < 24) return A.t(h + ' ч назад', h + ' h ago');
    return A.t(A.count(Math.round(h / 24), ['день', 'дня', 'дней'], ['day', 'days']) + ' назад',
               A.count(Math.round(h / 24), ['день', 'дня', 'дней'], ['day', 'days']) + ' ago');
  }

  /* ---------- уведомления от браузера ----------

     По желанию: переключатель в самом колокольчике. Работают, пока сайт
     открыт хоть в одной вкладке (можно свёрнутой) — без фоновых служб
     и push-сервера: это отдельный большой механизм с ключами и
     подписками, а здесь хватает простого.

     О каждой серии — один раз. Что уже показали, помним в этом браузере
     по паре «тайтл#серия»; включили переключатель — всё, что горит сейчас,
     считается уже показанным: уведомлять о вчерашнем незачем. */
  var NOTIFY_KEY = 'animedik.notify';
  var NOTIFIED_KEY = 'animedik.notified';
  var NOTIFIED_MAX = 300;

  function canNotify() {
    return typeof window.Notification === 'function';
  }

  function notifyWanted() {
    try { return localStorage.getItem(NOTIFY_KEY) === '1'; } catch (e) { return false; }
  }

  function notified() {
    try {
      var list = JSON.parse(localStorage.getItem(NOTIFIED_KEY) || '[]');
      return Array.isArray(list) ? list : [];
    } catch (e) { return []; }
  }

  function rememberNotified(ids) {
    if (!ids.length) return;
    var list = notified().concat(ids);
    try {
      localStorage.setItem(NOTIFIED_KEY, JSON.stringify(list.slice(-NOTIFIED_MAX)));
    } catch (e) { /* хранилище недоступно — переживём, в худшем случае скажем дважды */ }
  }

  function notifyId(u) { return u.key + '#' + u.now; }

  function notifyFresh() {
    if (!canNotify() || !notifyWanted() || Notification.permission !== 'granted') return;
    var done = notified();
    var fresh = updates.filter(function (u) { return done.indexOf(notifyId(u)) < 0; });
    if (!fresh.length) return;
    rememberNotified(fresh.map(notifyId));
    fresh.slice(0, 3).forEach(function (u) {
      try {
        var note = new Notification(A.t('Новая серия: ', 'New episode: ') + (A.animeName(u) || ''), {
          body: A.t('Вышла ' + u.now + '-я серия', 'Episode ' + u.now + ' is out'),
          icon: '/static/favicon.svg',
          tag: 'animedik-' + u.key
        });
        note.onclick = function () {
          try { window.focus(); } catch (e) { /* не дали — не беда */ }
          seenUpdate(u);
          location.href = updateHref(u);
          note.close();
        };
      } catch (e) {
        /* Некоторые браузеры (Chrome на Android) разрешают уведомления
           только через фоновую службу и бросают ошибку здесь. Колокольчик
           на странице при этом работает как обычно. */
      }
    });
  }

  function paintNotify() {
    var row = $('bell-notify-row');
    row.hidden = !canNotify() || !A.me || A.me.role === 'guest';
    if (row.hidden) return;
    var perm = Notification.permission;
    var on = notifyWanted() && perm === 'granted';
    $('bell-notify').setAttribute('aria-pressed', on ? 'true' : 'false');
    $('bell-notify').classList.toggle('on', on);
    $('bell-notify-s').textContent = perm === 'denied'
      ? A.t('Запрещено в настройках браузера', 'Blocked in browser settings')
      : (on ? A.t('Пока сайт открыт', 'While the site is open') : '');
  }

  $('bell-notify').addEventListener('click', function (e) {
    e.stopPropagation();
    if (!canNotify()) return;
    if (notifyWanted() && Notification.permission === 'granted') {
      try { localStorage.setItem(NOTIFY_KEY, '0'); } catch (err) { /* переживём */ }
      paintNotify();
      return;
    }
    var turnOn = function (perm) {
      if (perm === 'granted') {
        try { localStorage.setItem(NOTIFY_KEY, '1'); } catch (err) { /* переживём */ }
        rememberNotified(updates.map(notifyId));
      }
      paintNotify();
    };
    if (Notification.permission === 'granted' || Notification.permission === 'denied') {
      turnOn(Notification.permission);
      return;
    }
    try {
      var asked = Notification.requestPermission(turnOn);
      if (asked && asked.then) asked.then(turnOn);
    } catch (err) { paintNotify(); }
  });

  /* =============== объявление администратора =============== */
  /* Объявление помним целиком, на обоих языках: при переключении языка
     плашку надо перерисовать, а второй раз ходить на сервер за тем же
     самым незачем. */
  var news = {text: '', text_en: ''};

  function loadNews() {
    return A.api.get('/api/news').then(function (r) {
      news = {text: (r && r.text) || '', text_en: (r && r.text_en) || ''};
      showNews();
    }).catch(function () { news = {text: '', text_en: ''}; showNews(); });
  }

  function showNews() {
    var box = $('sitenews');
    /* На английском показываем английскую версию, если админ её написал.
       Не написал — русскую: своё объявление не на том языке полезнее
       пустой строки, где могло быть «сервер перезапустится в 23:00». */
    var text = (A.langCode() === 'en' && news.text_en) ? news.text_en : news.text;
    if (!text) { box.hidden = true; return; }
    /* textContent, а не разметка: объявление пишет человек, и если бы оно
       вставлялось как HTML, туда можно было бы (пусть и случайно)
       положить код, который выполнится у всех остальных. */
    $('sitenews-text').textContent = text;
    box.hidden = false;
    if ($('news-text')) $('news-text').value = news.text;
    if ($('news-text-en')) $('news-text-en').value = news.text_en;
    paintNewsCount();
  }

  /* Сколько ещё поместится. Ограничение есть и в поле, и на сервере, но
     молчаливый обрыв на трёхсотой букве выглядит как проглоченный текст —
     лучше показать счётчик заранее. */
  var NEWS_MAX = 300;

  function paintNewsCount() {
    [['news-text', 'news-count'], ['news-text-en', 'news-count-en']]
      .forEach(function (pair) {
        var field = $(pair[0]);
        var out = $(pair[1]);
        if (!field || !out) return;
        var left = NEWS_MAX - field.value.length;
        out.textContent = left + A.t(' из ' + NEWS_MAX + ' осталось',
                                     ' of ' + NEWS_MAX + ' left');
        out.classList.toggle('low', left < 40);
      });
  }

  var vnews = $('veil-news');
  function openNews() {
    A.showError($('news-err'), '');
    vnews.classList.add('show');
  }
  $('btn-news').addEventListener('click', openNews);
  /* Тот же редактор — из раздела «Админка» в кабинете. Окно ложится
     поверх кабинета, Esc закрывает сначала его. */
  $('cab-news').addEventListener('click', openNews);
  $('news-x').addEventListener('click', function () { vnews.classList.remove('show'); });
  ['news-text', 'news-text-en'].forEach(function (id) {
    var field = $(id);
    if (field) field.addEventListener('input', paintNewsCount);
  });
  vnews.addEventListener('click', function (e) {
    if (e.target === vnews) vnews.classList.remove('show');
  });

  $('news-save').addEventListener('click', function () {
    guarded(this, function () {
      A.showError($('news-err'), '');
      return A.api.post('/api/admin/news', {
        text: $('news-text').value,
        text_en: $('news-text-en').value
      }).then(function (r) {
        news = {text: r.text || '', text_en: r.text_en || ''};
        showNews();
        var s = $('news-saved');
        s.classList.add('show');
        setTimeout(function () { s.classList.remove('show'); }, 2200);
      }).catch(function (err) { A.showError($('news-err'), err.message); });
    });
  });

  $('news-del').addEventListener('click', function () {
    A.ask({
      title: A.t('Убрать объявление?', 'Remove the announcement?'),
      text: A.t('Плашка исчезнет у всех, кто зайдёт на сайт.',
                'The banner disappears for everyone.'),
      ok: A.t('Убрать', 'Remove'),
      danger: true
    }).then(function (yes) {
      if (!yes) return;
      A.showError($('news-err'), '');
      return A.api.del('/api/admin/news').then(function () {
        $('news-text').value = '';
        $('news-text-en').value = '';
        news = {text: '', text_en: ''};
        showNews();
        vnews.classList.remove('show');
      }).catch(function (err) { A.showError($('news-err'), err.message); });
    });
  });

  /* =============== вход по коду и письма =============== */
  /* Включён ли вход по коду НА САМОМ ДЕЛЕ — по последнему ответу сервера.

     Переключатель сам по себе этого не знает: он встаёт в «вкл» сразу по
     нажатию, ещё до того, как код подтверждён. Раньше человек начинал
     настройку, закрывал кабинет, не дойдя до кода, — и переключатель так
     и оставался включённым, хотя сервер считал защиту выключенной. Это
     хуже, чем просто некрасиво: человек уверен, что защищён, а он нет.

     twofaTry растёт при каждой отмене. Ответ сервера, пришедший уже после
     отмены, сверяется с ним и выбрасывается — иначе окно настройки
     всплывало бы в закрытом кабинете. */
  var twofaReal = false;
  var twofaTry = 0;

  function paintSecurity(me) {
    twofaReal = !!me.totp_on;
    twofaTry++;
    setCheck('s-2fa', twofaReal);
    $('twofa-box').hidden = true;
    setCheck('s-mailnew', !!me.mail_new_eps);
    $('s-email').value = me.email || '';
    $('opt-email').hidden = !me.mail_new_eps;
  }

  onSwitch('s-mailnew', function (on) {
    $('opt-email').hidden = !on;
    if (on) $('s-email').focus();
  });

  /* Настройку бросили: закрыли кабинет, сняли переключатель, черновик
     протух. Переключатель возвращается к тому, что знает сервер, окно
     с ключом прячется, ключ стирается со страницы. */
  function dropTwofaDraft() {
    twofaTry++;
    $('twofa-box').hidden = true;
    $('twofa-code').value = '';
    $('twofa-secret').textContent = '';
    $('twofa-qr').removeAttribute('src');
    A.showError($('twofa-err'), '');
    setCheck('s-2fa', twofaReal);
  }

  onSwitch('s-2fa', function (on) {
    if (on) {
      /* Код показываем только после галочки. Пока он не подтверждён,
         вход по нему не включён — это ещё черновик настройки.

         Сначала пароль — так же, как при выключении. Иначе любой, кто
         подсел за незапертый ноутбук, включил бы код на СВОЁМ телефоне
         и запер бы владельца снаружи. */
      A.showError($('twofa-err'), '');
      $('twofa-code').value = '';
      var my = ++twofaTry;
      A.ask({
        title: A.t('Включить вход по коду?', 'Turn on the sign-in code?'),
        text: A.t('Введите пароль — иначе включить код на своём телефоне и запереть вас ' +
                  'снаружи мог бы любой, кто сядет за ваш компьютер.',
                  'Enter your password — otherwise anyone at your computer could set the ' +
                  'code up on their own phone and lock you out.'),
        ok: A.t('Дальше', 'Next'),
        password: true
      }).then(function (pass) {
        if (my !== twofaTry) return;
        if (pass === null || pass === '') { dropTwofaDraft(); return; }
        return A.api.post('/api/me/2fa/start', {password: pass}).then(function (r) {
          /* Пока ждали ответа, настройку могли бросить — тогда молчим. */
          if (my !== twofaTry) return;
          /* Картинки может не быть: сервер без библиотеки рисования всё
             равно отдаёт ключ. Тогда показываем ключ и просим ввести его
             в приложение руками — это работает точно так же. */
          var img = $('twofa-qr'), hint = $('twofa-manual');
          if (r.qr) {
            img.src = r.qr; img.hidden = false;
            if (hint) hint.hidden = true;
          } else {
            img.removeAttribute('src'); img.hidden = true;
            if (hint) hint.hidden = false;
          }
          $('twofa-secret').textContent = r.secret;
          $('twofa-box').hidden = false;
        }).catch(function (err) {
          if (my !== twofaTry) return;
          dropTwofaDraft();
          A.tell(A.t('Не получилось', 'Did not work'), err.status === 403
            ? A.t('Пароль не подошёл.', 'Wrong password.') : err.message);
        });
      });
      return;
    }
    /* Сняли переключатель посреди настройки, когда код ещё не включён:
       выключать нечего, просто бросаем черновик. Раньше тут спрашивался
       пароль «для выключения» того, что и не было включено. */
    if (!twofaReal) { dropTwofaDraft(); return; }
    /* Выключение спрашивает пароль: иначе защиту снимает любой,
       кто подсел за незапертый ноутбук. */
    A.ask({
      title: A.t('Выключить вход по коду?', 'Turn off the sign-in code?'),
      text: A.t('Введите пароль — иначе защиту снял бы любой, кто сядет за ваш компьютер.',
                'Enter your password — otherwise anyone at your computer could turn it off.'),
      ok: A.t('Выключить', 'Turn off'),
      password: true,
      danger: true
    }).then(function (pass) {
      if (pass === null || pass === '') { setCheck('s-2fa', true); return; }
      return A.api.post('/api/me/2fa/disable', {password: pass}).then(function () {
        $('twofa-box').hidden = true;
        return A.loadMe();
      }).then(function (m) { if (m) paintSecurity(m); })
        .catch(function (err) {
          setCheck('s-2fa', true);
          A.tell(A.t('Не выключилось', 'Not turned off'), err.message);
        });
    });
  });

  /* Двойное нажатие «Подтвердить» раньше уходило на сервер двумя
     запросами. Первый включал вход по коду и показывал запасные коды —
     единственный раз, больше их не увидеть. Второй получал «уже включено»
     (409), и его окно с ошибкой ВЫТЕСНЯЛО окно с кодами: человек оставался
     без запасных кодов, даже не успев их прочитать. Пока запрос в пути,
     кнопка выключена и второе нажатие ничего не шлёт. */
  var twofaSending = false;
  $('twofa-code').addEventListener('keydown', function (e) {
    if (e.key === 'Enter') { e.preventDefault(); $('twofa-confirm').click(); }
  });
  $('twofa-confirm').addEventListener('click', function () {
    if (twofaSending) return;
    twofaSending = true;
    var btn = $('twofa-confirm');
    btn.disabled = true;
    A.showError($('twofa-err'), '');
    A.api.post('/api/me/2fa/enable', {code: $('twofa-code').value}).then(function (r) {
      twofaSending = false;
      btn.disabled = false;
      twofaReal = true;
      $('twofa-box').hidden = true;
      $('twofa-secret').textContent = '';
      /* Включение закрывает все остальные входы: защиту ставят, чтобы
         закрыть дверь, а не только на будущее. Говорим об этом прямо —
         иначе телефон, который выкинуло, выглядит поломкой. */
      return A.tell(
        A.t('Готово, вход по коду включён', 'Done, the code is on'),
        A.t('Запишите запасные коды. Каждый работает один раз и понадобится, ' +
            'если телефон потеряется:\n\n',
            'Save these backup codes. Each works once and will be needed ' +
            'if you lose your phone:\n\n') + (r.backup || []).join('\n') +
        (r.closed ? A.t('\n\nНа других устройствах (' + r.closed + ') вход закрыт — ' +
                        'там нужно войти заново, уже с кодом.',
                        '\n\nYou were signed out on other devices (' + r.closed + ') — ' +
                        'sign in there again, now with the code.') : '')
      ).then(function () { return A.loadMe(); });
    }).then(function (m) { if (m) paintSecurity(m); })
      .catch(function (err) {
        twofaSending = false;
        btn.disabled = false;
        /* 409 — черновик протух или код уже включён в другой вкладке.
           Переключатель должен показать правду, а не «вкл» без защиты. */
        if (err.status === 409) {
          A.loadMe().then(function (m) { if (m) paintSecurity(m); }, function () {});
          dropTwofaDraft();
          A.tell(A.t('Не получилось', 'Did not work'), err.message);
          return;
        }
        A.showError($('twofa-err'), err.message);
      });
  });

  /* =============== про сайт =============== */
  var veil = $('veil');
  function openAbout(e) { if (e) e.preventDefault(); veil.classList.add('show'); }
  function closeAbout() { veil.classList.remove('show'); }
  /* Бургер. Закрывается по второму нажатию, по Esc, по клику мимо и
     сразу после нажатия любой кнопки внутри: меню, которое остаётся
     открытым после выбора, приходится закрывать вручную. */
  var acts = $('acts'), burger = $('btn-burger');
  function menu(открыть) {
    acts.classList.toggle('open', открыть);
    burger.setAttribute('aria-expanded', открыть ? 'true' : 'false');
  }
  burger.addEventListener('click', function (e) {
    e.stopPropagation();
    menu(!acts.classList.contains('open'));
  });
  acts.addEventListener('click', function (e) {
    if (e.target.closest('#btn-bell')) return;   // у колокольчика свой список
    if (e.target.closest('button, a')) menu(false);
  });
  document.addEventListener('click', function (e) {
    if (!acts.contains(e.target) && e.target !== burger) menu(false);
  });

  /* =============== помощник =============== */
  /* Кнопка «?» открывает окошко: короткая справка и чат с помощником.

     Помощник только отвечает текстом. Вместе с ответом сервер может
     прислать кнопки {id, label} из своего белого списка (api/helper.py,
     TARGETS) — здесь они превращаются в «Открыть: …». Нажатие делает
     ровно то же, что человек сделал бы сам руками: открывает раздел
     настроек, вкладку, окно. id, которого нет в HELPER_OPEN, кнопкой не
     становится.

     Текст помощника вставляется только через textContent: это чужой
     текст, разметки в нём быть не может. История — в памяти вкладки,
     в базу не пишется. */
  var hlp = $('hlp'), hlpBtn = $('btn-svyaz'), hlpLog = $('hlp-log'), hlpIn = $('hlp-in');
  var hlpHistory = [];          // [{role, content}] — только эта вкладка
  var hlpBusy = false;
  var hlpGen = 0;               // «Очистить» посреди ответа — опоздавший ответ не нужен
  var HLP_KEEP = 16;            // сколько реплик держим; на сервер уходят последние 8

  /* Раздел кабинета и, если нужно, строка в нём — прокрутить к ней. */
  function hlpSettings(sec, nodeId) {
    return function () {
      openSet(sec);
      if (!nodeId) return;
      setTimeout(function () {
        var n = $(nodeId);
        var grp = n && (n.closest('.grp') || n);
        if (grp && grp.scrollIntoView) {
          try { grp.scrollIntoView({ block: 'start', behavior: 'smooth' }); } catch (e) { grp.scrollIntoView(); }
        }
      }, 80);
    };
  }
  function hlpTab(name) {
    return function () {
      var a = $('tabs').querySelector('a[data-tab="' + name + '"]');
      if (!a) return;
      $('search-wrap').hidden = true;
      a.click();
      $('tabs').scrollIntoView({ block: 'center', behavior: 'smooth' });
      a.focus();
    };
  }
  var HELPER_OPEN = {
    'profile-card': function () { openCard(); },
    'settings-profile': hlpSettings('profile'),
    'settings-look': hlpSettings('look'),
    'settings-lang': hlpSettings('lang'),
    'settings-view': hlpSettings('view'),
    'settings-adult': hlpSettings('view', 's-adult'),
    'settings-hentai': hlpSettings('view', 's-hentai'),
    'settings-mail': hlpSettings('view', 's-mailnew'),
    'settings-safe': hlpSettings('safe'),
    'tab-open': hlpTab('open'),
    'tab-done': hlpTab('done'),
    'tab-later': hlpTab('later'),
    'search': function () { window.scrollTo(0, 0); $('q').focus(); },
    'roulette': function () { openRoulette(); },
    'bell': function () {
      /* На телефоне колокольчик живёт в меню-бургере. */
      if (narrowScreen()) menu(true);
      if (!bellDrop.classList.contains('show')) $('btn-bell').click();
    },
    'stats': function () { location.href = '/stats'; },
    'rules': function () { window.open('/pravila', '_blank', 'noopener'); },
    'about': function () { openAbout(); },
    'admin': function () { if (A.me && A.me.role === 'admin') openSet('admin'); }
  };

  function hlpOpen() { return !hlp.hidden; }

  function hlpShow(on) {
    hlp.hidden = !on;
    hlpBtn.setAttribute('aria-expanded', on ? 'true' : 'false');
    hlpBtn.classList.toggle('on', on);
  }

  /* Что показать под справкой: чат или объяснение, почему его нет. */
  function hlpPaint(state, hint) {
    var can = state === 'ok';
    hlp.classList.toggle('nochat', !can);
    $('hlp-form').hidden = !can;
    $('hlp-foot').hidden = !can;
    $('hlp-clear').hidden = !can || !hlpHistory.length;
    hlpLog.hidden = !can || !hlpLog.firstChild;
    var note = $('hlp-note');
    note.textContent = '';
    note.hidden = can;
    if (can) return;
    if (state === 'guest') {
      /* Гостевой пропуск выдаётся любому, поэтому гостям чат закрыт:
         иначе кто угодно мог бы выжечь бесплатный лимит ключа. */
      note.appendChild(A.el('b', null, A.t('Гости не могут общаться с ИИ-помощником.',
                                           'Guests can\'t chat with the AI helper.')));
      note.appendChild(A.el('span', null, A.t('Так никто не съест бесплатный лимит. Войди в аккаунт — и спрашивай; справка выше доступна всем.',
                                              'This keeps the free limit safe. Sign in to ask; the help above is open to everyone.')));
    } else if (state === 'demo') {
      note.appendChild(A.el('b', null, A.t('В демонстрационном режиме помощник недоступен.',
                                           'The helper is not available in demo mode.')));
    } else if (state === 'off') {
      note.appendChild(A.el('b', null, A.t('Помощник не настроен.', 'The helper is not set up.')));
      if (hint) {
        note.appendChild(A.el('span', null, A.t(
          'Чтобы включить: впишите в файл .env на сервере строку ниже (ключ — с console.groq.com → API Keys) и перезапустите сайт.',
          'To turn it on, add the line below to the .env file on the server (key from console.groq.com → API Keys) and restart the site.')));
        note.appendChild(A.el('code', null, hint));
      }
    } else {
      note.appendChild(A.el('b', null, A.t('Помощник сейчас недоступен.', 'The helper is unavailable right now.')));
    }
  }

  function openHelper() {
    menu(false);
    setBellOpen(false);
    closeCard(false);
    /* Совет про вход по коду висит там же, где на телефоне лежит окно
       помощника, и закрывал бы поле вопроса. Помощник открыт — совет
       уходит (не навсегда: покажется при следующем входе). */
    hideToast(false);
    hlpShow(true);
    hlpPaint(A.me && A.me.role !== 'guest' ? 'ok' : 'guest');
    /* Включён ли помощник, сервер знает лучше: ключ, демо-режим. */
    A.api.get('/api/helper?lang=' + A.langCode()).then(function (r) {
      hlpPaint((r && r.state) || 'off', r && r.hint);
      if (r && r.state === 'ok') hlpIn.focus();
      else $('hlp-close').focus();
    }).catch(function (err) {
      hlpPaint(err.status === 401 ? 'guest' : 'error');
      $('hlp-close').focus();
    });
  }

  function closeHelper(back) {
    if (!hlpOpen()) return;
    hlpShow(false);
    if (back) hlpBtn.focus();
  }

  /* Одна реплика. Кнопки — через DOM, подпись — textContent. */
  function hlpAdd(role, text, chips) {
    var row = A.el('div', 'hlpmsg ' + (role === 'user' ? 'me' : 'bot'));
    row.appendChild(A.el('div', 'hlptx', text));
    (chips || []).forEach(function (c) {
      if (!c || !HELPER_OPEN[c.id]) return;
      if (c.id === 'admin' && !(A.me && A.me.role === 'admin')) return;
      var b = A.el('button', 'hlpchip go', A.t('Открыть: ', 'Open: ') + String(c.label || ''));
      b.type = 'button';
      b.setAttribute('data-go', c.id);
      row.appendChild(b);
    });
    hlpLog.appendChild(row);
    hlpLog.hidden = false;
    $('hlp-body').scrollTop = $('hlp-body').scrollHeight;
    return row;
  }

  var hlpDots = null;           // «…», пока помощник думает
  function hlpTyping(on) {
    if (on && !hlpDots) {
      hlpDots = A.el('div', 'hlpmsg bot typing');
      hlpDots.setAttribute('aria-label', A.t('Помощник пишет', 'The helper is typing'));
      hlpDots.appendChild(A.el('div', 'hlptx', '…'));
      hlpLog.appendChild(hlpDots);
      $('hlp-body').scrollTop = $('hlp-body').scrollHeight;
    } else if (!on && hlpDots) {
      if (hlpDots.parentNode) hlpDots.parentNode.removeChild(hlpDots);
      hlpDots = null;
    }
  }

  function hlpSend() {
    var q = hlpIn.value.trim();
    if (!q || hlpBusy) return;
    if (q.length > 500) {
      hlpAdd('bot', A.t('Слишком длинно — уложитесь в 500 символов.', 'Too long — keep it under 500 characters.'))
        .classList.add('err');
      return;
    }
    hlpBusy = true;
    $('hlp-send').disabled = true;
    hlpIn.value = '';
    hlpGrow();
    hlpAdd('user', q);
    var past = hlpHistory.slice(-HLP_KEEP);
    hlpTyping(true);
    var gen = hlpGen;
    /* lang — язык сайта прямо сейчас: на нём помощник и ответит, и
       напишет, если что-то не так (api/main.py, helper_lang). */
    A.api.post('/api/helper/ask', { message: q, history: past, lang: A.langCode() }).then(function (r) {
      if (gen !== hlpGen) return;
      hlpTyping(false);
      var text = (r && r.reply) || '';
      hlpAdd('bot', text, r && r.chips);
      hlpHistory.push({ role: 'user', content: q });
      hlpHistory.push({ role: 'assistant', content: text });
      hlpHistory = hlpHistory.slice(-HLP_KEEP);
      $('hlp-clear').hidden = false;
    }).catch(function (err) {
      if (gen !== hlpGen) return;
      hlpTyping(false);
      /* Фразы помощника сервер пишет на языке сайта. А «не вошёл» и
         «нельзя» приходят от общих проверок и только по-русски — их
         говорим сами. */
      var msg = err.status === 0 ? A.t('Нет связи с сервером.', 'No connection to the server.')
              : err.status === 401 ? A.t('Вход закончился — войдите снова.', 'You are signed out — sign in again.')
              : err.status === 403 ? A.t('Спрашивать помощника можно только из своего аккаунта.',
                                         'Only signed-in accounts can ask the helper.')
              : err.message;
      hlpAdd('bot', msg).classList.add('err');
      if (err.status === 503 || err.status === 403) {
        A.api.get('/api/helper?lang=' + A.langCode()).then(function (r) { hlpPaint((r && r.state) || 'off', r && r.hint); })
          .catch(function () { /* оставим как есть */ });
      }
    }).then(function () {
      hlpBusy = false;
      $('hlp-send').disabled = false;
      if (hlpOpen() && !$('hlp-form').hidden) hlpIn.focus();
    });
  }

  /* Поле растёт вместе с текстом, но не выше пяти строк. */
  function hlpGrow() {
    hlpIn.style.height = 'auto';
    hlpIn.style.height = Math.min(hlpIn.scrollHeight, 118) + 'px';
  }

  hlpBtn.addEventListener('click', function (e) {
    e.stopPropagation();
    if (hlpOpen()) closeHelper(false); else openHelper();
  });
  $('hlp-close').addEventListener('click', function () { closeHelper(true); });
  $('hlp-clear').addEventListener('click', function () {
    hlpHistory = [];
    hlpGen++;
    hlpTyping(false);
    hlpLog.textContent = '';
    hlpLog.hidden = true;
    $('hlp-clear').hidden = true;
    hlpIn.focus();
  });
  $('hlp-form').addEventListener('submit', function (e) { e.preventDefault(); hlpSend(); });
  $('hlp-send').addEventListener('click', function (e) { e.preventDefault(); hlpSend(); });
  /* Enter — отправить, Shift+Enter — новая строка. */
  hlpIn.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); hlpSend(); }
  });
  hlpIn.addEventListener('input', hlpGrow);
  /* Кнопки быстрых ссылок и «Открыть: …». Действие — после того, как
     этот щелчок доберётся до document: иначе обработчики «щелчок мимо»
     у колокольчика и бургера сразу закрыли бы то, что мы открыли. */
  hlp.addEventListener('click', function (e) {
    e.stopPropagation();
    var b = e.target.closest('[data-go]');
    if (!b) return;
    var go = HELPER_OPEN[b.getAttribute('data-go')];
    if (!go) return;
    closeHelper(false);
    setTimeout(go, 0);
  });
  hlp.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { e.stopPropagation(); closeHelper(true); }
  });
  document.addEventListener('click', function (e) {
    if (hlpOpen() && !hlp.contains(e.target) && !hlpBtn.contains(e.target)) closeHelper(false);
  });

  $('btn-about').addEventListener('click', openAbout);
  $('foot-about').addEventListener('click', openAbout);
  $('btn-close').addEventListener('click', closeAbout);
  $('btn-close2').addEventListener('click', closeAbout);
  veil.addEventListener('click', function (e) { if (e.target === veil) closeAbout(); });
  /* Esc закрывает одно окно — верхнее, а не все разом.

     Раньше одно нажатие закрывало и окно обрезки фото, и кабинет под ним,
     и рулетку, и «Про сайт» — всё, что было открыто. Человек хотел
     отменить обрезку, а терял ещё и несохранённые настройки.

     Сначала — выпадающие меню: они легче окон и лежат поверх. Потом —
     верхнее из открытых окон. Окна в разметке идут по порядку
     наложения (одинаковый z-index, значит выше то, что ниже в коде),
     поэтому верхнее — последнее найденное. Свои диалоги A.ask / A.tell
     перехватывают Esc раньше, в app.js, и сюда он от них не доходит. */
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    /* Карточка профиля — тоже «выпадающее», и самое верхнее из них. */
    if (cardOpen()) { closeCard(true); return; }
    if (hlpOpen()) { closeHelper(true); return; }
    if (acts.classList.contains('open') || bellDrop.classList.contains('show')) {
      menu(false);
      bellDrop.classList.remove('show');
      return;
    }
    var open = document.querySelectorAll('.veil.show:not(.dialog)');
    var top = open[open.length - 1];
    if (!top) return;
    if (top === vfr) closeFranchise();
    else if (top === vset) leaveSet();
    else top.classList.remove('show');
  });

  /* =============== фокус в окнах =============== */
  /* «Про сайт», «Рандом», части франшизы, объявление и обрезка фото.

     Эти окна открывались поверх страницы, а фокус оставался под ними —
     на кнопке, которая окно открыла. С клавиатуры Tab уходил по ссылкам
     затемнённой страницы, а программа чтения с экрана не замечала, что
     открылось окно. Закрыли окно — фокус терялся в начале страницы.

     Теперь у всех пяти одинаково (как уже было у кабинета и карточки
     профиля): открылось — фокус на первой кнопке окна; Tab ходит по кругу
     внутри; закрылось — фокус возвращается туда, откуда окно открыли.

     Открывают и закрывают эти окна разные места кода (кнопки, Esc,
     щелчок мимо), поэтому следим не за ними, а за самим окном: появился
     или пропал у него класс show. */
  function dialogOf(v) { return v.querySelector('[role="dialog"]') || v; }

  function minds(v) {
    if (!v || typeof MutationObserver !== 'function') return;
    var was = v.classList.contains('show');
    var box = dialogOf(v);
    new MutationObserver(function () {
      var now = v.classList.contains('show');
      if (now === was) return;
      was = now;
      if (now) {
        v._opener = document.activeElement;
        setTimeout(function () {
          if (!v.classList.contains('show') || box.contains(document.activeElement)) return;
          var f = focusables(box);
          if (f[0]) f[0].focus();
        }, 40);
        return;
      }
      var back = v._opener;
      v._opener = null;
      var lost = !document.activeElement || document.activeElement === document.body ||
                 box.contains(document.activeElement);
      if (back && back.focus && lost && document.body.contains(back) && back.getClientRects().length) {
        back.focus();
      }
    }).observe(v, { attributes: true, attributeFilter: ['class'] });
    box.addEventListener('keydown', function (e) { keepFocusIn(box, e); });
  }
  [veil, vroul, vfr, vnews, vcrop].forEach(minds);

  /* =============== загрузка и уведомление =============== */
  function boot(done) {
    /* Экран живёт около секунды. Раньше на нём показывали случайный
       факт про анимацию — прочесть его никто не успевал, а глаз всё
       равно за него цеплялся. Осталась одна строка и движение. */
    var el = $('boot');
    var line = $('boot-line');
    /* По-английски сайт зовётся animeDik — так же, как домен animedik.cc. */
    line.textContent = A.t('Загрузка анимеДик', 'Loading animeDik');
    el.classList.add('show');
    var bar = $('boot-bar'), p = 0;
    var t = setInterval(function () {
      p += 18 + Math.random() * 20;
      bar.style.width = Math.min(100, p) + '%';
      if (p >= 100) {
        clearInterval(t);
        setTimeout(function () { el.classList.remove('show'); bar.style.width = '0'; if (done) done(); }, 280);
      }
    }, 200);
  }
  /* Совет включить вход по коду.

     Прежняя плашка сообщала, что сайт собран нейросетью, и всплывала
     при каждом заходе — то есть повторяла уже известное. Эта приходит
     только тому, кому есть что советовать, и только пока совет
     уместен: включил вход по коду — больше не увидит никогда. */
  var СОВЕТ_ЗАКРЫТ = 'animedik.2fa-hint-off';
  var toastTimer = null;

  function showToast() {
    var me = A.me;
    if (!me || me.role === 'guest' || me.totp_on) return;
    try { if (localStorage.getItem(СОВЕТ_ЗАКРЫТ) === '1') return; } catch (e) {}
    var toast = $('toast');
    toast.classList.add('show');
    clearTimeout(toastTimer);
    /* Дольше обычного: тут не уведомление, а текст, который надо
       успеть прочитать и решить. */
    toastTimer = setTimeout(function () { toast.classList.remove('show'); }, 16000);
  }

  function hideToast(навсегда) {
    clearTimeout(toastTimer);
    $('toast').classList.remove('show');
    if (навсегда) { try { localStorage.setItem(СОВЕТ_ЗАКРЫТ, '1'); } catch (e) {} }
  }

  $('toast-x').addEventListener('click', function () { hideToast(true); });
  $('toast-go').addEventListener('click', function () {
    hideToast(true);
    openSet('safe');
    /* Ведём прямо к переключателю, а не «куда-то в настройки». */
    setTimeout(function () {
      var sw = $('s-2fa');
      if (sw) { sw.scrollIntoView({ block: 'center' }); sw.focus(); }
    }, 260);
  });

  /* =============== старт =============== */
  A.boot(function (me) {
    if (me) showApp(me); else showGate();
  });
})();
