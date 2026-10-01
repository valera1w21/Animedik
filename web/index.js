/* Главная страница: вход, полка, поиск, рулетка, кабинет. */
(function () {
  'use strict';
  var A = window.App;
  var $ = function (id) { return document.getElementById(id); };

  /* Поле search отсюда убрано: оно заводилось, но никогда не читалось
     и не записывалось — результаты поиска живут прямо в своей секции. */
  var state = { items: [], tab: 'open' };
  /* Источник по умолчанию берётся от языка сайта: у русского и
     английского он разный, и подменять один другим нельзя. Если
     выбранный молчит, сервер переберёт остальные — но только того же
     языка. */
  function currentSource() { return A.defaultSource(); }

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
    } else {
      $('guestbar').classList.remove('show');
    }
    loadLibrary();
    var isAdmin = me.role === 'admin';
    $('admin-grp').hidden = !isAdmin;
    $('btn-news').hidden = !isAdmin;
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
    lockForGuest(me.role === 'guest');
    boot(showToast);
  }

  function paintMe(me) {
    $('whoami').textContent = me.display_name || me.name;
    $('set-name').textContent = me.display_name || me.name;
    A.paintAvatar($('ava'), me);
    A.paintAvatar($('set-ava'), me);
    var roles = {
      admin: ['Администратор', 'Administrator'],
      user: ['Пользователь', 'User'],
      guest: ['Гость', 'Guest']
    };
    var kind = roles[me.role] ? me.role : 'user';
    var r = $('set-role');
    r.setAttribute('data-kind', kind);
    r.setAttribute('data-ru', roles[kind][0]);
    r.setAttribute('data-en', roles[kind][1]);
    r.textContent = A.t(roles[kind][0], roles[kind][1]);

    if (me.role !== 'guest') {
      $('s-name').value = me.display_name || me.name;
      $('sess-note').textContent = A.t(
        'Активных входов: ' + (me.sessions || 1),
        'Active sessions: ' + (me.sessions || 1));
      var s = me.settings || {};
      mark('#s-depth button', 'set', s.depth || 'deep');
      mark('#s-accent .sc', 'a', s.accent || 'mint');
      markTheme(s.theme || 'kak-seychas');
      markHoliday(s.holiday || '');
      mark('#s-logo .logopick', 'l', String(s.logo || 1));
      mark('#s-ava .sc', 'c', me.avatar_color || '#84CBB6');
      $('s-photo-clear').hidden = !me.has_avatar;
    }
  }

  function mark(sel, attr, value) {
    document.querySelectorAll(sel).forEach(function (b) {
      b.classList.toggle('on', b.dataset[attr] === value);
    });
  }
  function getSwitch(key) {
    var b = document.querySelector('.sw[data-k="' + key + '"]');
    return b ? b.getAttribute('aria-checked') === 'true' : true;
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

  $('login-form').addEventListener('submit', function (e) {
    e.preventDefault();
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
    }).finally(function () { A.busy(box, false); });
  });

  $('btn-guest').addEventListener('click', function () {
    var box = $('gate').querySelector('.box');
    A.showError($('gate-err'), '');
    A.busy(box, true);
    A.api.post('/api/auth/guest').then(function (r) {
      A.setMe(r.me);
      showApp(r.me);
    }).catch(function (err) {
      A.showError($('gate-err'), err.message);
    }).finally(function () { A.busy(box, false); });
  });

  $('btn-out').addEventListener('click', function (e) {
    e.stopPropagation();
    A.logout();
  });

  /* =============== полка =============== */
  function loadLibrary() {
    return A.api.get('/api/library').then(function (r) {
      state.items = r.items || [];
      render();
      fixMissingPosters();
      /* Проверяем новые серии сразу, а не по нажатию: иначе о них узнаёшь,
         только если сам догадаешься нажать колокольчик. Ответ источника
         кэшируется на три часа, поэтому повторные заходы почти бесплатны. */
      loadUpdates();
    }).catch(function () {
      state.items = [];
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
     и брошенное: с точки зрения «досмотрел или нет» это одно и то же. */
  function visible() {
    return state.items.filter(function (it) {
      return state.tab === 'done' ? it.status === 'done' : it.status !== 'done';
    });
  }

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
           '&total=' + encodeURIComponent(it.total_eps || 0);
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
    var poster = A.safeUrl(it.poster);
    if (poster) {
      fill.style.backgroundImage = 'url("' + poster.replace(/"/g, '%22') + '")';
      var probe = new Image();
      probe.onerror = function () { fill.style.backgroundImage = ''; };
      probe.src = poster;
    }
    pic.appendChild(A.el('span', 'noart', (it.title || '?').charAt(0).toUpperCase()));
    pic.appendChild(fill);

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
  function baseName(title) {
    var t = (title || '').toLowerCase().replace(/ё/g, 'е');
    t = t.replace(/\[[^\]]*\]|\([^)]*\)/g, ' ');   // счётчики серий в скобках
    t = t.split(' / ')[0];                          // «Наруто / Naruto»
    t = t.split(/\s*[:—–]\s*/)[0];                  // подзаголовок после двоеточия
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
    var cut = t.split(/\s*[:—–]\s*/)[0].trim();
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
       «Бетани», а «Атака» — всё, что начинается на эти буквы. */
    order.slice().sort(function (a, b) { return a.length - b.length; })
      .forEach(function (k) {
        if (!by[k]) return;
        var host = order.filter(function (other) {
          return by[other] && other !== k && other.length < k.length
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
      box.title = boxTitle(A.animeName(shortest) || shortest.title || box.last.title);
      box.title_en = shortest.title_en || '';
      box.poster = (box.items.filter(function (i) { return i.poster; })[0] || {}).poster || '';
      box.done = box.items.every(function (i) { return i.status === 'done'; });
      return box;
    }).sort(function (a, b) { return b.updated_at - a.updated_at; });
  }

  /* Карточка-коробка. */
  function boxNode(box) {
    /* Класс boxcard, а не box: короткое `box` уже занято формой входа,
       и карточка молча забирала её оформление — фон, рамку, отступы в
       тридцать пикселей и даже ограничение ширины. Второе такое
       столкновение подряд после `bar`: короткие имена в общем файле
       стилей почти всегда уже чьи-то. */
    var a = A.el('a', 'card boxcard');
    var last = box.last;
    a.href = watchHref(last);

    var wrap = A.el('div', 'wrap');
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
      wrap.appendChild(del);
    }

    var pic = A.el('div', 'pic');
    var fill = A.el('i');
    var poster = A.safeUrl(box.poster);
    if (poster) {
      fill.style.backgroundImage = 'url("' + poster.replace(/"/g, '%22') + '")';
      var probe = new Image();
      probe.onerror = function () { fill.style.backgroundImage = ''; };
      probe.src = poster;
    }
    pic.appendChild(A.el('span', 'noart', (box.title || '?').charAt(0).toUpperCase()));
    pic.appendChild(fill);

    var seen = box.last.watched_ep || 0;
    var total = box.last.total_eps || 0;
    var known = total > 0 && total >= seen;

    /* Сколько частей в коробке — прямо на обложке, как число дисков на
       торце коробки. Одна часть — писать нечего. */
    if (box.items.length > 1) {
      pic.appendChild(A.el('span', 'discs',
                           A.say(box.items.length, 'part')));
    }
    if (box.done) pic.appendChild(A.el('span', 'done-mark', A.t('Досмотрено', 'Finished')));
    wrap.appendChild(pic);
    a.appendChild(wrap);

    a.appendChild(A.el('h3', null, box.title || A.t('Без названия', 'Untitled')));

    /* Подпись под названием: где остановились.

       Полоса раньше лежала на обложке — и это оказалось плохо. Обложки
       у всех разные: на светлой она терялась, на тёмной резала глаз, на
       пёстрой её просто не было видно. Одна и та же полоса выглядела
       по-разному на каждой карточке.

       Теперь она внизу, на ровном фоне самой карточки: одинаковая
       всегда, независимо от того, что нарисовано на постере. */
    /* Нижняя строка: полоса просмотра и номер серии.

       Прижата к низу карточки, а не идёт сразу за названием. Названия
       разной длины — одно в строку, другое в две, — и строка, идущая
       следом, у каждой карточки оказывается на своей высоте: ряд
       выглядит рассыпанным. Прижатая к низу, она у всех на одной линии.

       Полоса раньше лежала на обложке, и это было хуже всего: обложки
       у всех разные, и на светлой полоса терялась, на тёмной резала
       глаз, на пёстрой её не было видно вовсе. */
    var foot = A.el('div', 'foot');

    if (known && total > 0) {
      var line = A.el('div', 'seenbar' + (box.done ? ' full' : ''));
      var fill = A.el('i');
      var pct = box.done ? 100 : (seen > 0 ? seen / total * 100 : 0);
      fill.style.width = Math.max(0, Math.min(100, pct)) + '%';
      line.appendChild(fill);
      foot.appendChild(line);
      foot.appendChild(A.el('span', 'num', seen + ' / ' + total));
    } else {
      /* Сколько всего серий, источник знает не всегда. Полоса «неизвестно
         из неизвестного» врала бы — тогда просто номер. */
      foot.appendChild(A.el('span', 'num only', seen > 0
        ? A.t('серия ', 'ep. ') + seen
        : A.t('ещё не начинали', 'not started')));
    }

    a.appendChild(foot);

    /* Нажатие открывает коробку, а не сразу плеер: в ней несколько
       частей, и выбрать должен человек. Если часть одна — открывать
       нечего, идём прямо в плеер. */
    if (box.items.length > 1) {
      a.addEventListener('click', function (e) {
        e.preventDefault();
        openBox(box);
      });
    }
    return a;
  }

  function removeBox(box) {
    var name = box.title || A.t('этот тайтл', 'this title');
    var many = box.items.length > 1;
    A.ask({
      title: A.t('Убрать из списка?', 'Remove from list?'),
      text: many
        ? A.t('«' + name + '» пропадёт целиком — все ' + box.items.length +
              ' частей вместе с отметками о просмотре.',
              '"' + name + '" will be removed entirely — all ' + box.items.length +
              ' parts along with your watch progress.')
        : A.t('«' + name + '» пропадёт из списка вместе с отметками о просмотре.',
              '"' + name + '" will be removed along with your watch progress.'),
      ok: A.t('Убрать', 'Remove'),
      danger: true
    }).then(function (yes) {
      if (!yes) return;
      return Promise.all(box.items.map(function (it) {
        return A.api.del('/api/library/' + encodeURIComponent(it.key));
      })).then(loadLibrary)
        .catch(function (err) { A.tell(A.t('Не получилось', 'Did not work'), err.message); });
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

    var art = $('fr-art');
    art.textContent = '';
    art.style.backgroundImage = '';
    var poster = A.safeUrl(box.poster);
    if (poster) art.style.backgroundImage = 'url("' + poster.replace(/"/g, '%22') + '")';
    else art.appendChild(A.el('span', 'noart', (box.title || '?').charAt(0).toUpperCase()));

    var wrap = $('fr-parts');
    wrap.textContent = '';
    wrap.appendChild(A.el('div', 'mgroup yes', A.t('Вы это смотрите', 'You are watching')));
    box.items.forEach(function (it) { wrap.appendChild(mineNode(it)); });

    vfr.classList.add('show');
    document.addEventListener('keydown', franchiseKeys);

    /* Остальные части — из справочника. Названия своих записей уже
       заняты, поэтому из общего списка показываем только то, чего в
       списке ещё нет: иначе одна и та же часть стояла бы дважды. */
    var mine = {};
    box.items.forEach(function (it) { mine[baseKey(it.title)] = true; });

    A.api.get('/api/related?title=' + encodeURIComponent(box.last.title || box.title))
      .then(function (r) {
        var rest = ((r && r.items) || []).filter(function (p) {
          return !mine[baseKey(p.title)];
        });
        if (!rest.length) return;
        wrap.appendChild(A.el('div', 'mgroup', A.t('Что ещё есть', 'What else there is')));
        rest.forEach(function (p) { wrap.appendChild(partNode(p, !!p.main)); });
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
    var poster = A.safeUrl(it.poster);
    if (poster) {
      var fill = A.el('i');
      fill.style.backgroundImage = 'url("' + poster.replace(/"/g, '%22') + '")';
      th.appendChild(fill);
    }
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

  function render() {
    var list = boxes(sortItems(visible()));
    var grid = $('grid');
    grid.textContent = '';
    list.forEach(function (box) { grid.appendChild(boxNode(box)); });

    /* Считаем истории, а не записи: четыре части «Ван-Пис» — это один
       тайтл в списке, а не четыре. */
    $('list-count').textContent = A.say(list.length, 'title');
    $('empty').classList.toggle('show', list.length === 0);
    var noneAtAll = state.items.length === 0;
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
    $('tabs').querySelectorAll('a').forEach(function (x) { x.classList.remove('on'); });
    a.classList.add('on');
    render();
  });

  /* =============== поиск ===============

     Поиск работает в два шага, и это главное, что нужно про него знать.

     Шаг первый — справочник. На «наруто» приходит одна карточка «Наруто»,
     а не двадцать одна строка, где вперемешку лежат второй сезон, фильм
     про Боруто и спешл про спортивный фестиваль. Раньше приходило именно
     второе: запрос уходил прямо на источник видео, а тот отвечал тем,
     что сам считает похожим. У некоторых источников на «наруто» самого
     «Наруто» нет вовсе — только «Ураганные хроники» и «Боруто».

     Шаг второй — по нажатию на карточку открывается список всех частей
     франшизы по годам: с чего начинать, что дальше, что можно пропустить.
     Ссылку на видео ищем только тогда, когда часть выбрана.

     Если справочник молчит, поиск возвращается к старому способу — прямо
     у источников. Хуже, но лучше, чем пустой экран. */
  var searchTimer = null;
  $('q').addEventListener('input', function () {
    clearTimeout(searchTimer);
    var q = this.value.trim();
    if (q.length < 2) { $('search-wrap').hidden = true; return; }
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
    $('q').value = '';
    $('search-wrap').hidden = true;
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

  function doSearch(q) {
    $('search-wrap').hidden = false;
    $('search-note').textContent = A.t('ищем…', 'searching…');
    var grid = searchSkeletons();

    A.api.get('/api/find?q=' + encodeURIComponent(q) +
              '&lang=' + encodeURIComponent(A.langCode()))
      .then(function (res) {
        var rows = res.items || [];
        /* catalog:false — справочник не ответил. Это не «ничего не
           нашлось»: искать ещё есть где, просто хуже. */
        if (!res.catalog) return doSourceSearch(q);
        grid.textContent = '';
        if (!rows.length) {
          /* Справочник ответил и не знает такого. У источников бывает
             то, чего нет в справочнике, — стоит спросить и их. */
          return doSourceSearch(q);
        }
        $('search-note').textContent =
          A.say(rows.length, 'title') +
          A.t(' · нажмите, чтобы увидеть все части',
              ' · tap to see every part');
        rows.slice(0, 24).forEach(function (r) {
          grid.appendChild(franchiseNode(r));
        });
      })
      .catch(function (err) {
        grid.textContent = '';
        $('search-note').textContent = err.message;
      });
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
    var poster = A.safeUrl(fr.poster);
    if (poster) {
      fill.style.backgroundImage = 'url("' + poster.replace(/"/g, '%22') + '")';
      var probe = new Image();
      probe.onerror = function () { fill.style.backgroundImage = ''; };
      probe.src = poster;
    }
    var frName = A.animeName(fr);
    pic.appendChild(A.el('span', 'noart', (frName || '?').charAt(0).toUpperCase()));
    pic.appendChild(fill);
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
    return a;
  }

  /* Старый поиск — прямо у источников. Остаётся запасным путём: им
     находится то, чего нет в справочнике, и им же сайт продолжает
     работать, когда справочник лежит. */
  function doSourceSearch(q) {
    var grid = searchSkeletons();
    $('search-note').textContent = A.t('ищем у источников…', 'searching sources…');
    return A.api.get('/api/search?q=' + encodeURIComponent(q) +
                     '&lang=' + encodeURIComponent(A.langCode()) +
                     '&source=' + encodeURIComponent(currentSource()))
      .then(function (res) {
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
        grid.textContent = '';
        $('search-note').textContent = err.message;
      });
  }

  /* =============== части франшизы =============== */
  var vfr = $('veil-fr');

  function closeFranchise() {
    vfr.classList.remove('show');
    document.removeEventListener('keydown', franchiseKeys);
  }
  function franchiseKeys(e) { if (e.key === 'Escape') closeFranchise(); }

  $('fr-close').addEventListener('click', closeFranchise);
  vfr.addEventListener('click', function (e) { if (e.target === vfr) closeFranchise(); });

  function openFranchise(fr) {
    A.showError($('fr-err'), '');
    var open = A.animeName(fr);
    $('fr-title').textContent = open;
    /* Во второй строке — второе имя тайтла, какое бы ни было первым. */
    var other = open === fr.title ? (fr.title_en || '') : (fr.title || '');
    $('fr-sub').textContent = other && other !== open ? other : '';
    $('fr-note').textContent = A.t('собираем список…', 'building the list…');

    var art = $('fr-art');
    art.textContent = '';
    art.style.backgroundImage = '';
    var poster = A.safeUrl(fr.poster);
    if (poster) art.style.backgroundImage = 'url("' + poster.replace(/"/g, '%22') + '")';
    else art.appendChild(A.el('span', 'noart', (open || '?').charAt(0).toUpperCase()));

    var box = $('fr-parts');
    box.textContent = '';
    for (var i = 0; i < 3; i++) {
      var sk = A.el('div', 'skeleton');
      sk.style.height = '58px';
      sk.style.marginBottom = '8px';
      box.appendChild(sk);
    }

    vfr.classList.add('show');
    document.addEventListener('keydown', franchiseKeys);

    A.api.get('/api/franchise?id=' + encodeURIComponent(fr.id))
      .then(function (res) {
        var parts = res.items || [];
        box.textContent = '';
        if (!parts.length) {
          $('fr-note').textContent = A.t('частей не нашлось', 'no parts found');
          return;
        }
        $('fr-note').textContent =
          A.say(parts.length, 'part') +
          A.t(' · по порядку выхода', ' · in release order');
        /* Какая часть «начало» — решает сервер: это не всегда первая
           строка списка. У «Ван-Пис» раньше сериала вышла посторонняя
           OVA, и порядок по годам ставит её наверх. */
        parts.forEach(function (p) { box.appendChild(partNode(p, !!p.main)); });
      })
      .catch(function (err) {
        box.textContent = '';
        $('fr-note').textContent = '';
        A.showError($('fr-err'), err.message);
      });
  }

  /* Одна строка списка частей.

     Порядок в списке — по годам, поэтому первая строка это то, с чего
     начинают. Она и помечена: без пометки человек, открывший «Наруто»,
     видит двадцать девять одинаковых строк и снова не знает, какую
     нажать, — то есть ровно ту беду, ради которой всё это делалось. */
  function partNode(p, isMain) {
    var row = A.el('button', 'part');
    row.type = 'button';

    var th = A.el('span', 'th');
    var poster = A.safeUrl(p.poster);
    if (poster) {
      var fill = A.el('i');
      fill.style.backgroundImage = 'url("' + poster.replace(/"/g, '%22') + '")';
      th.appendChild(fill);
    }
    row.appendChild(th);

    var tx = A.el('span', 'tx');
    var nameRow = A.el('span', 'nm', A.animeName(p) || '—');
    if (isMain) {
      /* Пробел отдельным узлом: без него название и пометка склеиваются
         в «Нарутоначало» при копировании и в речи экранного диктора.
         Глазами этого не видно — отступ рисует стиль. */
      nameRow.appendChild(document.createTextNode(' '));
      nameRow.appendChild(A.el('span', 'first', A.t('начало', 'start here')));
    }
    tx.appendChild(nameRow);

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
    tx.appendChild(A.el('span', 'm', bits.join(' · ')));
    row.appendChild(tx);

    var go = A.el('span', 'go', '›');
    row.appendChild(go);

    row.addEventListener('click', function () { openPart(p, row, go); });
    return row;
  }

  /* Часть выбрана — ищем её у источников видео.

     Справочник знает, что «Наруто» существует и когда вышел, но ссылок
     на серии у него нет. Их знают только источники, и знают под своими
     названиями. Поэтому здесь отдельный шаг: название превращается в
     пару «источник + номер тайтла», с которой открывается плеер. */
  function openPart(p, row, go) {
    if (row.classList.contains('busy')) return;
    row.classList.add('busy');
    go.textContent = '…';
    A.showError($('fr-err'), '');

    /* Сколько серий обещает справочник — подсказка серверу: у «Ван-Пис»
       один источник выложил семь серий из тысячи с лишним, и без этого
       числа отличить огрызок от полного тайтла нечем. */
    A.api.get('/api/resolve?title=' + encodeURIComponent(p.title) +
              '&title_en=' + encodeURIComponent(p.title_en || '') +
              '&episodes=' + encodeURIComponent(p.episodes || 0) +
              '&lang=' + encodeURIComponent(A.langCode()) +
              '&source=' + encodeURIComponent(currentSource()))
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
            total_eps: p.episodes || r.episodes_total || 0
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

  /* =============== кабинет =============== */
  var vset = $('veil-set');
  function openSet() { vset.classList.add('show'); }
  function closeSet() { vset.classList.remove('show'); }
  $('who').addEventListener('click', function (e) {
    if (e.target.id === 'btn-out') return;
    openSet();
  });
  $('who').addEventListener('keydown', function (e) {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openSet(); }
  });
  $('set-close').addEventListener('click', closeSet);
  vset.addEventListener('click', function (e) { if (e.target === vset) closeSet(); });

  function lockForGuest(isGuest) {
    $('pbody').classList.toggle('ro', isGuest);
    $('pfoot').classList.toggle('ro', isGuest);
    $('lockbar').classList.toggle('show', isGuest);
    $('pbody').querySelectorAll('input, select, button').forEach(function (n) {
      if (n.closest('.lockbar')) return;
      n.disabled = isGuest;
    });
    /* Кнопку «Сохранить» раньше прятал только стиль .pfoot.ro. Прятать —
       не то же самое, что выключать: скрытую кнопку всё ещё можно нажать
       с клавиатуры, и гость получал в ответ короткое «403» без объяснений.
       Сервер его всё равно не пустит, но пугать человека незачем. */
    $('set-save').disabled = isGuest;
  }

  document.querySelectorAll('#s-depth button').forEach(function (b) {
    b.addEventListener('click', function () {
      mark('#s-depth button', 'set', b.dataset.set);
      document.documentElement.setAttribute('data-depth', b.dataset.set);
    });
  });
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
    document.querySelectorAll('#s-theme .theme').forEach(function (b) {
      var свой = b.dataset.th === ключ;
      b.classList.toggle('on', свой);
      b.setAttribute('aria-pressed', свой ? 'true' : 'false');
    });
  }
  document.querySelectorAll('#s-theme .theme').forEach(function (b) {
    b.addEventListener('click', function () {
      markTheme(b.dataset.th);
      A.applyTheme(b.dataset.th);
    });
  });

  /* Праздник. «По калевдарю» — пустая строка, «Выключить» — off. */
  function markHoliday(ключ) {
    document.querySelectorAll('#s-holiday button').forEach(function (b) {
      b.classList.toggle('on', (b.dataset.h || '') === (ключ || ''));
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
      $('ava').style.background = b.dataset.c;
      $('set-ava').style.background = b.dataset.c;
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
  $('s-name').addEventListener('input', function () {
    var v = this.value.trim() || (A.me ? A.me.name : '');
    $('whoami').textContent = v;
    $('set-name').textContent = v;
  });

  /* Обработчик кнопок языка переехал в общий код: кнопки теперь есть на
     каждой странице, а не только здесь. */
  /* Смена языка меняет и названия аниме, не только надписи: список
     рисуется заново. */
  document.addEventListener('langchange', function () {
    if (A.me) render();
    showNews();
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

  $('set-save').addEventListener('click', function () {
    var settings = {
      lang: document.documentElement.dataset.lang,
      depth: document.documentElement.getAttribute('data-depth'),
      accent: document.documentElement.getAttribute('data-accent'),
      logo: pickedValue('#s-logo .logopick.on', 'l', 1),
      theme: (document.querySelector('#s-theme .theme.on') || {}).dataset
             ? document.querySelector('#s-theme .theme.on').dataset.th : 'kak-seychas',
      holiday: (document.querySelector('#s-holiday button.on') || {}).dataset
             ? (document.querySelector('#s-holiday button.on').dataset.h || '') : '',
      autonext: getSwitch('autonext')
    };
    var colorBtn = document.querySelector('#s-ava .sc.on');
    var mailReq = A.api.post('/api/me/mail', {
      email: $('s-email').value.trim(),
      want: getCheck('s-mailnew')
    });
    Promise.all([
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
  });

  $('s-pass').addEventListener('click', function () {
    $('pass-box').hidden = !$('pass-box').hidden;
  });
  $('p-save').addEventListener('click', function () {
    A.showError($('p-err'), '');
    A.api.post('/api/me/password', {
      current: $('p-cur').value, new: $('p-new').value
    }).then(function () {
      return A.tell(A.t('Пароль изменён', 'Password changed'),
                    A.t('Войдите заново — на всех устройствах.',
                        'Sign in again — on every device.'));
    }).then(function () {
      location.href = '/';
    }).catch(function (err) { A.showError($('p-err'), err.message); });
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
      return A.api.post('/api/auth/logout-all').then(function () { location.href = '/'; });
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
  window.addEventListener('touchmove', move, { passive: false });
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
    }).then(function (m) { if (m) paintMe(m); });
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

  function runAdmin(promise) {
    A.showError($('a-err'), '');
    return promise.then(loadAdmin).catch(function (err) {
      A.showError($('a-err'), err.message);
    });
  }

  function loadAdmin() {
    return A.api.get('/api/admin/users').then(function (r) {
      var box = $('admin-list');
      box.textContent = '';
      r.users.forEach(function (u) {
        var row = A.el('div', 'acc');
        var av = A.el('span', 'ava', (u.display_name || u.login).charAt(0).toUpperCase());
        av.style.background = u.role === 'admin' ? '#84CBB6' : '#8DB5DF';
        row.appendChild(av);
        row.appendChild(document.createTextNode(u.login));
        row.appendChild(A.el('span', 'sp'));
        row.appendChild(A.el('em', null,
          u.disabled ? A.t('выключен', 'disabled')
            : (u.role === 'admin' ? A.t('владелец', 'owner')
              : A.t('входов: ' + u.sessions, 'sessions: ' + u.sessions))));

        var mine = A.me && u.login === A.me.name;
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
      $('guests-now').textContent = A.t('гостей сейчас: ', 'guests now: ') + r.guests_now;
    }).catch(function () {});
  }

  $('a-new').addEventListener('click', function () {
    $('a-form').hidden = !$('a-form').hidden;
  });
  $('a-save').addEventListener('click', function () {
    A.showError($('a-err'), '');
    A.api.post('/api/admin/users', {
      login: $('a-login').value.trim(),
      password: $('a-pass').value,
      role: $('a-admin').checked ? 'admin' : 'user'
    }).then(function () {
      $('a-login').value = ''; $('a-pass').value = ''; $('a-admin').checked = false;
      $('a-form').hidden = true;
      loadAdmin();
    }).catch(function (err) { A.showError($('a-err'), err.message); });
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

  function showPick(it) {
    var res = $('pickres');
    res.textContent = '';

    var art = A.el('div', 'cover');
    var poster = A.safeUrl(it.poster);
    if (poster) {
      art.style.backgroundImage = 'url("' + poster.replace(/"/g, '%22') + '")';
    } else {
      art.appendChild(A.el('span', 'noart', (it.title || '?').charAt(0).toUpperCase()));
    }
    res.appendChild(art);

    res.appendChild(A.el('div', 'nm', it.title || '—'));

    var bits = [];
    if (it.year) bits.push(it.year);
    if (it.episodes) bits.push(A.say(it.episodes, 'episode'));
    if (it.score) bits.push(A.t('оценка ', 'score ') + it.score + '/100');
    if (bits.length) res.appendChild(A.el('div', 'm', bits.join(' · ')));

    if (it.genres && it.genres.length) {
      var tags = A.el('div', 'rtags');
      it.genres.slice(0, 4).forEach(function (g) {
        tags.appendChild(A.el('span', 'tag', g));
      });
      res.appendChild(tags);
    }

    if (it.about) res.appendChild(A.el('div', 'about', it.about));

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
  var updatesLoaded = false;

  $('btn-bell').addEventListener('click', function (e) {
    e.stopPropagation();
    bellDrop.classList.toggle('show');
    if (bellDrop.classList.contains('show') && !updatesLoaded) loadUpdates();
  });
  document.addEventListener('click', function (e) {
    if (!bellDrop.contains(e.target)) bellDrop.classList.remove('show');
  });

  function loadUpdates() {
    /* Гостю проверять нечего: его список всегда пуст. */
    if (!A.me || A.me.role === 'guest') { updatesLoaded = true; renderBell(); return; }
    renderBell(true);
    return A.api.get('/api/updates').then(function (r) {
      updates = r.items || [];
      updatesLoaded = true;
      renderBell();
    }).catch(function () {
      updates = [];
      updatesLoaded = true;
      renderBell();
    });
  }

  function renderBell(loading) {
    var list = $('bell-list');
    list.textContent = '';

    if (loading) {
      list.appendChild(A.el('div', 'df', A.t('Смотрим, что вышло…', 'Checking for new episodes…')));
      return;
    }
    if (!updates.length) {
      list.appendChild(A.el('div', 'df', updatesLoaded
        ? A.t('Новых серий нет. Как только выйдет — покажем здесь.',
              'No new episodes. When one comes out, it shows up here.')
        : A.t('Нажмите, чтобы проверить новые серии.',
              'Click to check for new episodes.')));
      $('bell-count').hidden = true;
      return;
    }

    updates.forEach(function (it, n) {
      var row = A.el('a', 'nrow' + (n === 0 ? ' fresh' : ''));
      row.href = watchHref({
        key: it.key, source: it.source, title: it.title, poster: it.poster,
        year: it.year, genres: it.genres, total_eps: it.now
      }) + '&ep=' + encodeURIComponent(it.now);
      row.addEventListener('click', function () {
        bellDrop.classList.remove('show');
        /* Открыли — значит увидели. Гасим уведомление по этому тайтлу,
           иначе оно горело бы, пока не досмотришь до самой свежей серии. */
        A.api.post('/api/updates/seen',
                   {key: it.key, source: it.source, title: it.title}).catch(function () {});
      });

      var th = A.el('span', 'th');
      var i = A.el('i');
      var p = A.safeUrl(it.poster);
      if (p) { i.style.backgroundImage = 'url("' + p.replace(/"/g, '%22') + '")'; i.style.backgroundSize = 'cover'; }
      else i.style.background = 'linear-gradient(150deg,#6E93A6,#20242E)';
      th.appendChild(i);
      row.appendChild(th);

      var nb = A.el('span', 'nb');
      nb.appendChild(A.el('b', null, it.title || '—'));
      nb.appendChild(A.el('span', null, it.fresh > 1
        ? A.t('вышло ', '') + A.say(it.fresh, 'episode') + A.t(', до ' + it.now + '-й', ' out, up to ' + it.now)
        : A.t('вышла ' + it.now + '-я серия', 'episode ' + it.now + ' is out')));
      row.appendChild(nb);
      row.appendChild(A.el('span', 'when', A.t('новое', 'new')));
      list.appendChild(row);
    });
    $('bell-count').textContent = updates.length;
    $('bell-count').hidden = false;
  }

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
  $('btn-news').addEventListener('click', function () {
    A.showError($('news-err'), '');
    vnews.classList.add('show');
  });
  $('news-x').addEventListener('click', function () { vnews.classList.remove('show'); });
  ['news-text', 'news-text-en'].forEach(function (id) {
    var field = $(id);
    if (field) field.addEventListener('input', paintNewsCount);
  });
  vnews.addEventListener('click', function (e) {
    if (e.target === vnews) vnews.classList.remove('show');
  });

  $('news-save').addEventListener('click', function () {
    A.showError($('news-err'), '');
    A.api.post('/api/admin/news', {
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
  function paintSecurity(me) {
    setCheck('s-2fa', !!me.totp_on);
    $('twofa-box').hidden = true;
    setCheck('s-mailnew', !!me.mail_new_eps);
    $('s-email').value = me.email || '';
    $('opt-email').hidden = !me.mail_new_eps;
  }

  onSwitch('s-mailnew', function (on) {
    $('opt-email').hidden = !on;
    if (on) $('s-email').focus();
  });

  onSwitch('s-2fa', function (on) {
    if (on) {
      /* Код показываем только после галочки. Пока он не подтверждён,
         вход по нему не включён — это ещё черновик настройки. */
      A.showError($('twofa-err'), '');
      $('twofa-code').value = '';
      A.api.post('/api/me/2fa/start').then(function (r) {
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
        setCheck('s-2fa', false);
        A.tell(A.t('Не получилось', 'Did not work'), err.message);
      });
      return;
    }
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

  $('twofa-confirm').addEventListener('click', function () {
    A.showError($('twofa-err'), '');
    A.api.post('/api/me/2fa/enable', {code: $('twofa-code').value}).then(function (r) {
      $('twofa-box').hidden = true;
      return A.tell(
        A.t('Готово, вход по коду включён', 'Done, the code is on'),
        A.t('Запишите запасные коды. Каждый работает один раз и понадобится, ' +
            'если телефон потеряется:\n\n',
            'Save these backup codes. Each works once and will be needed ' +
            'if you lose your phone:\n\n') + (r.backup || []).join('\n')
      ).then(function () { return A.loadMe(); });
    }).then(function (m) { if (m) paintSecurity(m); })
      .catch(function (err) { A.showError($('twofa-err'), err.message); });
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

  /* Кнопка связи. Способа связи пока нет — и она об этом честно
     говорит. Кнопка, не отвечающая на нажатие ничем, неотличима от
     сломанной, а таких на этом сайте уже хватило. */
  $('btn-svyaz').addEventListener('click', function () {
    A.tell(A.t('Связаться', 'Get in touch'),
           A.t('Способ связи здесь появится позже. Пока по любым вопросам — '
               + 'напрямую к администратору сайта.',
               'A contact method will appear here later. For now, reach the '
               + 'site administrator directly.'));
  });

  $('btn-about').addEventListener('click', openAbout);
  $('foot-about').addEventListener('click', openAbout);
  $('btn-close').addEventListener('click', closeAbout);
  $('btn-close2').addEventListener('click', closeAbout);
  veil.addEventListener('click', function (e) { if (e.target === veil) closeAbout(); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { menu(false); closeAbout(); closeSet(); vroul.classList.remove('show');
      vcrop.classList.remove('show'); bellDrop.classList.remove('show'); }
  });

  /* =============== загрузка и уведомление =============== */
  function boot(done) {
    /* Экран живёт около секунды. Раньше на нём показывали случайный
       факт про анимацию — прочесть его никто не успевал, а глаз всё
       равно за него цеплялся. Осталась одна строка и движение. */
    var el = $('boot');
    var line = $('boot-line');
    line.textContent = A.t('Загрузка анимеДик', 'Loading анимеДик');
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
    openSet();
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
