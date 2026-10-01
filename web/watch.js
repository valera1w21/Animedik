/* Страница просмотра: серии, плееры, сохранение секунды. */
(function () {
  'use strict';
  var A = window.App;
  var $ = function (id) { return document.getElementById(id); };

  var params = new URLSearchParams(location.search);
  var KEY = (params.get('key') || '').slice(0, 200);
  /* Источник приезжает в адресе страницы. Если его там нет, берём тот,
     что для языка сайта основной: русская озвучка на английском сайте
     (и наоборот) — это подмена, а не запасной вариант. */
    var SOURCE = (params.get('source') || '').slice(0, 40);
  var TITLE = (params.get('title') || '').slice(0, 300);
  /* Эти поля приходят из карточки: если тайтла ещё нет в библиотеке,
     взять их больше неоткуда, и он сохранился бы без обложки. */
  var POSTER = A.safeUrl(params.get('poster') || '');
  var YEAR = parseInt(params.get('year') || '0', 10) || null;
  var GENRES = (params.get('genres') || '').slice(0, 300);
  var TOTAL = parseInt(params.get('total') || '0', 10) || 0;
  var WANT_EP = parseInt(params.get('ep') || '0', 10) || 0;
  /* Какой вариант озвучки открыть сразу. Приезжает, когда сюда пришли за
     субтитрами: их нашли у другого источника, и открыть надо именно их,
     а не первую попавшуюся озвучку. */
  var WANT_DUB = (params.get('dub') || '').slice(0, 120);

  var st = {
    /* dubs — только названия озвучек, они приходят бесплатно вместе со
       списком плееров. videos — ссылки той одной озвучки, которую сейчас
       смотрят: за ними надо ходить к видеохостингу, и это секунды. */
    episodes: [], current: 0, dubs: [], dub: '', videos: [], video: 0,
    related: [], title_en: '', item: null, duration: 0, position: 0
  };

  /* Список источников для меню «Откуда берётся видео».
     Раньше он лежал в window.__sources и заполнялся отдельным запросом,
     который никто не ждал. Меню рисуется сразу после загрузки плееров —
     то есть почти всегда РАНЬШЕ, чем приходил этот список, и внутри
     оставался один заголовок без единой кнопки. Переключить источник
     было нельзя, пока не перезагрузишь страницу.
     Теперь список живёт здесь, а меню перерисовывается, когда он придёт. */
  var sources = [];

  /* Кто из источников этот тайтл точно знает. Пусто, пока не спросили:
     спрашивать при загрузке страницы нельзя — это несколько секунд ради
     меню, которое человек может и не открыть. */
  var where = { here: [], checked: [], asked: false };

  /* Номера серий так, как их даёт источник, и последний из них.

     Разница между «сколько серий в списке» и «какой у последней номер»
     кажется придиркой ровно до первого источника, который нумерует
     серии не с единицы. Некоторые так и делают: «Наруто Ураганные
     хроники» приезжают сериями с 370-й по 500-ю — сто тридцать одна
     штука. Пока эти две величины путали, страница писала «Серия 370
     из 131», а отметка о просмотре одной серии закрывала весь тайтл. */
  function ordinals() {
    return st.episodes.map(function (e) { return e.ordinal; });
  }

  function lastOrdinal() {
    var n = ordinals();
    return n.length ? n[n.length - 1] : 0;
  }

  /* ------------------------------------------------------------------ */
  function fail(msg) {
    /* Раньше текст ошибки писался вместо названия тайтла, и после сбоя
       было непонятно, что вообще открыто. Теперь отдельная строка. */
    var box = $('w-err');
    if (!box) return;
    box.textContent = msg || '';
    box.classList.toggle('show', !!msg);
    if (msg) showLoading(false);
  }

  function loadItem() {
    return A.api.get('/api/library').then(function (r) {
      st.item = (r.items || []).filter(function (i) { return i.key === KEY; })[0] || null;
      if (st.item) {
        st.position = st.item.position || 0;
        st.current = st.item.watched_ep || 1;
      }
    }).catch(function () {});
  }

  /* Обложка, если её неоткуда взять.

     Обычно она приезжает в адресе страницы: карточка в поиске кладёт её
     в ссылку. Но попасть сюда можно и без неё — по закладке, по ссылке
     от друга, или тайтл когда-то сохранился в полку без картинки. Тогда
     обложки не было НИКОГДА: страница просмотра её ниоткуда не спрашивала,
     и в полке навсегда оставался серый прямоугольник с одной буквой.

     Спрашиваем у источника ровно в этом случае — один поиск по названию,
     и только если картинки действительно нет. */
  function ensurePoster() {
    if ((st.item && st.item.poster) || POSTER || !TITLE || !KEY) {
      return Promise.resolve();
    }
    return A.api.get('/api/search?q=' + encodeURIComponent(TITLE) +
                     '&source=' + encodeURIComponent(SOURCE) + '&any_source=false')
      .then(function (res) {
        var hit = (res.items || []).filter(function (r) {
          return String(r.key) === String(KEY);
        })[0];
        var found = hit && A.safeUrl(hit.poster);
        if (!found) return;
        POSTER = found;
        renderHead();
        /* Кладём найденное в полку, чтобы в следующий раз не искать снова */
        if (canSave()) save(false);
      })
      .catch(function () { /* нет обложки — не повод показывать ошибку */ });
  }

  /* Описание тайтла.

     Раньше под плеером стояла отговорка: «описание не показываем, чтобы
     не испортить сюжет». На деле показывать было просто нечего —
     источники видео описаний не отдают вовсе.

     Теперь оно берётся из открытого каталога через наш сервер, который
     обрезает его до завязки и выкидывает всё, за чем обычно начинается
     пересказ сюжета. */
  var aboutLoaded = false;
  /* Сменили язык — описание и жанры надо перечитать: они приходят с
     сервера уже на своём языке, сами по себе не переведутся. */
  document.addEventListener('langchange', function () {
    aboutLoaded = false;
    loadAbout();
    renderEpisodeMenu();
    /* Меню озвучек перерисовываем: подписи в нём на языке сайта. Сам
       вариант при этом не трогаем — человек мог выбрать его руками, и
       менять выбор из-за переключения языка было бы самоуправством. */
    renderMenus();
  });

  function loadAbout() {
    var name = (st.item && st.item.title) || TITLE;
    if (aboutLoaded || !name) return Promise.resolve();
    aboutLoaded = true;
    return A.api.get('/api/about?title=' + encodeURIComponent(name) +
                     '&lang=' + encodeURIComponent(A.langCode()))
      .then(function (r) {
        if (!r || !r.found || !r.about) {
          $('w-about').textContent = A.t(
            'Описание для этого тайтла не нашлось.',
            'No description found for this title.');
          return;
        }
        $('w-about').textContent = r.about;
        /* Год и жанры из каталога точнее, чем у источников видео:
           там они часто пустые или свалены в одну строку. */
        var tags = $('w-tags');
        if (tags && r.genres && r.genres.length && !tags.children.length) {
          if (r.year) tags.appendChild(A.el('span', 'tag', r.year));
          r.genres.forEach(function (g) { tags.appendChild(A.el('span', 'tag', g)); });
        }
      })
      .catch(function () {
        $('w-about').textContent = A.t('Описание сейчас недоступно.',
                                       'Description is unavailable right now.');
      });
  }

  /* ================= другие части этой истории =================

     Список серий в колонке отвечает на вопрос «где я внутри сезона».
     Этот блок — на тот, который возникает следом: какой это вообще сезон,
     что было до него и что после. Раньше ответа не было нигде: тайтл
     лежал в полке отдельной записью с одним названием, и про второй
     сезон с тремя фильмами надо было вспомнить самому и поискать руками.

     Грузится своей ниткой, как и описание: от серий и плееров он не
     зависит, и падать вместе с ними ему незачем. Если справочник молчит
     или частей всего одна — блока просто нет. */
  function loadRelated() {
    var name = (st.item && st.item.title) || TITLE;
    if (!name) return Promise.resolve();
    return A.api.get('/api/related?title=' + encodeURIComponent(name))
      .then(function (r) {
        var parts = (r && r.items) || [];
        /* Одна часть — это не история из нескольких частей, а обычный
           одиночный тайтл. Блок «эта история целиком» с единственной
           строкой, на которую и нажать-то нельзя, только занимал бы
           место в колонке. */
        st.related = parts;
        /* Латинское имя открытой части — чтобы список умел показывать
           название на языке сайта. Источник его не знает, справочник
           знает: берём отсюда и сохраняем вместе с отметкой. */
        var now = parts.filter(function (p) { return p.current; })[0];
        if (now && now.title_en) st.title_en = now.title_en;
        renderEpisodeMenu();
        if (parts.length < 2) return;
        renderRelated(parts);
      })
      .catch(function () { /* нет так нет — блок не появится */ });
  }

  function relatedHref(part, found) {
    return '/watch?key=' + encodeURIComponent(found.key) +
           '&source=' + encodeURIComponent(found.source) +
           '&title=' + encodeURIComponent(part.title || '') +
           /* Обложку берём из справочника: она крупнее и без надписей
              поверх, а у источников бывает и вовсе заглушка. */
           '&poster=' + encodeURIComponent(part.poster || found.poster || '') +
           '&year=' + encodeURIComponent(part.year || found.year || '') +
           '&genres=' + encodeURIComponent(found.genres || '') +
           '&total=' + encodeURIComponent(part.episodes || found.episodes_total || 0);
  }

  function renderRelated(parts) {
    var box = $('w-fr-list');
    box.textContent = '';
    parts.forEach(function (p) { box.appendChild(relatedNode(p)); });

    var at = 0;
    parts.forEach(function (p, i) { if (p.current) at = i + 1; });
    $('w-fr-count').textContent = at
      ? at + ' / ' + parts.length
      : A.say(parts.length, 'part');
    $('w-fr-card').hidden = false;

    /* Открытую часть подкручиваем в поле зрения. У «Ван-Пис» частей
       семьдесят шесть, и без этого блок всегда показывал бы 1998 год,
       где бы человек ни находился. */
    var now = box.querySelector('.part.now');
    if (now) box.scrollTop = Math.max(0, now.offsetTop - box.offsetTop - 60);
  }

  function relatedNode(p) {
    var row = A.el('button', 'part');
    row.type = 'button';
    if (p.current) row.classList.add('now');

    var th = A.el('span', 'th');
    var poster = A.safeUrl(p.poster);
    if (poster) {
      var fill = A.el('i');
      fill.style.backgroundImage = 'url("' + poster.replace(/"/g, '%22') + '")';
      th.appendChild(fill);
    }
    row.appendChild(th);

    var tx = A.el('span', 'tx');
    var nm = A.el('span', 'nm', A.animeName(p) || '—');
    if (p.current) {
      nm.appendChild(document.createTextNode(' '));
      nm.appendChild(A.el('span', 'now-tag', A.t('смотрите', 'watching')));
    } else if (p.main) {
      nm.appendChild(document.createTextNode(' '));
      nm.appendChild(A.el('span', 'first', A.t('начало', 'start here')));
    }
    tx.appendChild(nm);

    var bits = [];
    bits.push(p.year ? String(p.year) : A.t('дата неизвестна', 'no date yet'));
    var pKind = A.kindName(p);
    if (pKind) bits.push(pKind);
    if (p.episodes > 1 || (p.episodes === 1 && p.ongoing)) {
      bits.push(A.say(p.episodes, 'episode') +
                (p.ongoing ? A.t(' и продолжается', ' and counting') : ''));
    }
    tx.appendChild(A.el('span', 'm', bits.join(' · ')));
    row.appendChild(tx);

    var go = A.el('span', 'go', p.current ? '•' : '›');
    row.appendChild(go);

    row.addEventListener('click', function () { openRelated(p, row, go); });
    return row;
  }

  /* Нажали на другую часть — ищем её у источников и уходим туда.

     Справочник знает, что часть существует, но ссылок на серии у него
     нет. Их знают только источники, и знают под своими названиями,
     поэтому здесь тот же отдельный шаг, что и в поиске: название
     превращается в пару «источник + номер тайтла». */
  function openRelated(p, row, go) {
    if (p.current) return;                       // это и так открыто
    if (row.classList.contains('busy')) return;
    row.classList.add('busy');
    go.textContent = '…';
    A.showError($('w-fr-err'), '');

    /* Сколько серий обещает справочник — подсказка серверу: у «Ван-Пис»
       один источник выложил семь серий из тысячи с лишним, и без этого
       числа отличить огрызок от полного тайтла нечем. */
    A.api.get('/api/resolve?title=' + encodeURIComponent(p.title) +
              '&title_en=' + encodeURIComponent(p.title_en || '') +
              '&episodes=' + encodeURIComponent(p.episodes || 0) +
              '&lang=' + encodeURIComponent(A.langCode()) +
              '&source=' + encodeURIComponent(SOURCE))
      .then(function (found) {
        var go_ = function () { location.href = relatedHref(p, found); };
        /* Совпало неточно — спрашиваем. Молча увести человека на похожее
           название хуже, чем не увести никуда: он поймёт, что это другой
           тайтл, уже посреди серии. */
        if (found.exact) return go_();
        return A.ask({
          title: A.t('Точного совпадения нет', 'No exact match'),
          text: A.t('У источника это лежит как «' + found.title + '». Открыть?',
                    'The source has it as "' + found.title + '". Open it?'),
          ok: A.t('Открыть', 'Open')
        }).then(function (yes) { if (yes) go_(); });
      })
      .catch(function (err) {
        A.showError($('w-fr-err'), err.message);
      })
      .finally(function () {
        row.classList.remove('busy');
        go.textContent = '›';
      });
  }

  function loadEpisodes() {
    var url = '/api/episodes?key=' + encodeURIComponent(KEY) +
              '&source=' + encodeURIComponent(SOURCE) +
              '&title=' + encodeURIComponent(TITLE);
    return A.api.get(url).then(function (rows) {
      st.episodes = rows || [];
      /* Источник нумерует серии по-своему: бывает с нуля, бывает
         с пропусками. Поэтому берём номер из его же списка,
         а не считаем сами — иначе получаем «такой серии нет». */
      var nums = ordinals();
      if (WANT_EP && nums.indexOf(WANT_EP) !== -1) st.current = WANT_EP;
      if (nums.indexOf(st.current) === -1) {
        st.current = nums.length ? nums[0] : 1;
      }
      renderEpisodes();
      renderHead();
    });
  }

  function renderHead() {
    var poster = (st.item && A.safeUrl(st.item.poster)) || POSTER;
    if (poster) {
      var art = $('bgart');
      art.style.backgroundImage = 'url("' + poster.replace(/"/g, '%22') + '")';
      art.style.backgroundSize = 'cover';
      art.style.backgroundPosition = 'center';
    }
    var name = (st.item && st.item.title) || TITLE || A.t('Без названия', 'Untitled');
    $('w-title').textContent = name;
    $('w-name').textContent = name;
    /* То же название в полосе поверх картинки: в полном экране шапки
       страницы не видно, и понять, что открыто, больше неоткуда. */
    $('tb-name').textContent = name;
    $('w-side-title').textContent = name;
    /* «из скольки» — это ПОСЛЕДНИЙ НОМЕР серии, а не их количество.
       Раньше здесь стояло st.episodes.length, и на источнике, который
       нумерует серии не с единицы, выходила чепуха: источник A отдаёт
       «Наруто» сериями с 370-й по 500-ю, и страница честно писала
       «Серия 370 из 131». */
    var last = lastOrdinal();
    var tail = last ? A.t(' из ', ' of ') + last : '';
    /* Пока список серий не приехал, номера ещё нет. Показывать «Серия 0»
       и «0 / ?» — хуже, чем не показывать ничего: выглядит как поломка,
       хотя это просто «ещё не знаем». */
    var known = st.current > 0;
    $('w-crumb').textContent = known ? A.t('· серия ', '· episode ') + st.current + tail : '';
    $('w-epno').textContent = known
      ? A.t('Серия ', 'Episode ') + st.current + tail
      : A.t('Загружаем серии…', 'Loading episodes…');
    $('w-side-count').textContent = known ? st.current + ' / ' + (last || '?') : '';

    /* А вот полоса и «просмотрено» считаются по МЕСТУ серии в списке:
       это «сколько из показанного здесь уже позади», и для куска
       с 370-й по 500-ю оно тоже читается верно. */
    var total = st.episodes.length || 0;
    var seen = Math.max(0, ordinals().indexOf(st.current));
    $('w-prog').style.width = total ? Math.min(100, seen / total * 100) + '%' : '0%';
    $('w-seen').textContent = A.t('Просмотрено ', 'Watched ') + seen;
    var left = Math.max(0, total - seen);
    $('w-left').textContent = left
      ? A.t('Осталось ', 'Left ') + Math.round(left * 23 / 60) + A.t(' ч', ' h') : '';

    /* Описание подгружается отдельно (см. loadAbout). До ответа тут
       остаётся то, что уже стоит: пустая строка или прошлый текст. */
    /* Та же пара величин, что и выше: номер серии сравнивать можно
       только с номером последней, а не с их количеством. */
    $('w-epnote').textContent = last
      ? A.t('Серия ' + st.current + ' из ' + last + '.',
            'Episode ' + st.current + ' of ' + last + '.')
      : '';

    var tags = $('w-tags');
    tags.textContent = '';
    if (st.item && st.item.year) tags.appendChild(A.el('span', 'tag', st.item.year));
    (((st.item && st.item.genres) || GENRES || '').split(',')).forEach(function (g) {
      g = g.trim();
      if (g) tags.appendChild(A.el('span', 'tag', g));
    });
    /* Плашка «продолжить с 00:44» отсюда убрана. Она стояла в одном
       ряду с жанрами — среди «2020», «Сёнэн», «Фэнтези» — и читалась
       как ещё один жанр. Секунда, на которой остановились, и так
       написана под плеером и на самой дорожке. */

    var nums = ordinals();
    var at = nums.indexOf(st.current);
    var next = (at >= 0 && at + 1 < nums.length) ? nums[at + 1] : null;
    if (!nums.length) {
      /* Серий нет вовсе — значит и «это последняя» писать неправильно:
         последняя из чего? Раньше в этом случае показывалось именно оно. */
      $('w-next').hidden = true;
      $('w-next').onclick = null;
      $('w-nextcard').onclick = function (e) { e.preventDefault(); };
      $('w-nexttitle').textContent = A.t('Список серий не получен',
                                         'Episode list unavailable');
      $('w-nextsub').textContent = '';
    } else if (next !== null) {
      $('w-nexttitle').textContent = A.t('Серия ', 'Episode ') + next;
      $('w-nextsub').textContent = A.t('следующая по списку', 'next in the list');
      $('w-nextcard').href = '#';
      $('w-nextcard').onclick = function (e) { e.preventDefault(); pick(next); };
      $('w-next').onclick = function (e) { e.preventDefault(); pick(next); };
      $('w-next').hidden = false;
    } else {
      /* Обработчики надо снимать, а не только прятать кнопку: иначе на
         последней серии карточка «дальше» уносила на серию из прошлого
         показа. Строка `hidden = true` тут раньше стояла дважды. */
      $('w-next').hidden = true;
      $('w-next').onclick = null;
      $('w-nextcard').onclick = function (e) { e.preventDefault(); };
      $('w-nexttitle').textContent = A.t('Это последняя серия', 'That was the last one');
      $('w-nextsub').textContent = '';
    }
  }

  /* Меню «Серия» в полосе над картинкой.

     Список серий есть и в колонке справа, но в полном экране колонки не
     видно — а переключить серию или уйти в другой сезон хочется именно
     оттуда, не сворачивая. Поэтому тот же выбор продублирован сюда: и
     серии, и части истории. */
  function renderEpisodeMenu() {
    var box = $('m-e');
    if (!box) return;
    box.textContent = '';
    box.appendChild(A.el('div', 'mh', A.t('Серия', 'Episode')));

    if (!st.episodes.length) {
      box.appendChild(A.el('div', 'mnote', A.t('серии не загрузились',
                                               'episodes did not load')));
    } else {
      var grid = A.el('div', 'epgrid');
      st.episodes.forEach(function (ep) {
        var b = A.el('button', null, String(ep.ordinal));
        b.type = 'button';
        if (ep.ordinal === st.current) b.classList.add('on');
        else if (ep.ordinal < st.current) b.classList.add('seen');
        b.addEventListener('click', function () {
          box.classList.remove('show');
          pick(ep.ordinal);
        });
        grid.appendChild(b);
      });
      box.appendChild(grid);
    }

    /* Части истории — тот же список, что в блоке «Эта история целиком»,
       но здесь он нужен, чтобы сменить сезон, не выходя из полного
       экрана. */
    var parts = st.related || [];
    if (parts.length > 1) {
      box.appendChild(A.el('div', 'mgroup', A.t('Части истории', 'Parts')));
      parts.forEach(function (p) {
        var b = A.el('button', p.current ? 'on' : null, A.animeName(p) || '—');
        b.type = 'button';
        b.appendChild(A.el('span', 'sub', p.year ? String(p.year) : ''));
        if (!p.current) {
          b.addEventListener('click', function () {
            box.classList.remove('show');
            openRelated(p, b, b);
          });
        } else {
          b.disabled = true;
        }
        box.appendChild(b);
      });
    }

    var last = st.episodes.length ? st.episodes[st.episodes.length - 1].ordinal : 0;
    $('v-e').textContent = st.current
      ? (st.current + (last ? ' / ' + last : '')) : '—';
    /* Выбирать нечего — кнопка не притворяется, что есть из чего. */
    $('eps-btn').disabled = !st.episodes.length && parts.length < 2;
  }

  function renderEpisodes() {
    var box = $('w-eplist');
    box.textContent = '';
    st.episodes.forEach(function (ep) {
      var b = A.el('button', 'eprow');
      b.type = 'button';
      if (ep.ordinal < st.current) b.classList.add('seen');
      if (ep.ordinal === st.current) b.classList.add('now');
      b.appendChild(A.el('span', 'n', String(ep.ordinal).padStart(2, '0')));
      /* Название серии от источника может быть спойлером — не показываем */
      b.appendChild(A.el('span', 'tt', A.t('Серия ', 'Episode ') + ep.ordinal));
      b.appendChild(A.el('span', 'dur', ''));
      b.addEventListener('click', function () { pick(ep.ordinal); });
      box.appendChild(b);
    });
    /* Подкручиваем список к текущей серии, иначе при сотне серий
       она оказывается где-то далеко внизу и кажется, что список не работает. */
    var now = box.querySelector('.eprow.now');
    if (now && box.scrollHeight > box.clientHeight) {
      box.scrollTop = Math.max(0, now.offsetTop - box.clientHeight / 2);
    }
    renderEpisodeMenu();
  }

  function pick(n) {
    st.current = n;
    st.position = 0;
    st.duration = 0;
    started = false;
    markedThisEpisode = false;
    if (hls) { hls.destroy(); hls = null; }
    video.pause();
    video.removeAttribute('src');
    video.load();
    chrome(true);
    $('pos').style.width = '0%';
    $('knob').style.left = '0%';
    $('w-cur').textContent = '00:00';
    renderEpisodes();
    renderHead();
    loadVideos();
    save(false);
  }

  /* ------------------------------------------------------------------ */
  /* Плашка над плеером.

     Нужна, потому что «нет видео» бывает по разным причинам, и человеку
     важно понимать, по какой именно: ждать озвучку, переключить источник
     или искать другой тайтл. Раньше на всё это была одна красная строка
     внутри плеера — и та не всегда появлялась. */
  function notice(title, sub, offerSource) {
    var box = $('w-notice');
    if (!box) return;
    if (!title) { box.hidden = true; return; }
    $('w-notice-t').textContent = title;
    $('w-notice-s').textContent = sub || '';
    /* Кнопка появляется там, где беда лечится сменой источника. Раньше
       на её месте стояла фраза «переключите источник в панели под
       плеером» — то есть объяснение, где искать нужную кнопку. Панель
       внизу, плашка вверху, между ними плеер: пока доскроллишь, забудешь,
       что искал. */
    $('w-notice-go').hidden = !offerSource;
    box.hidden = false;
    /* Плашка объясняет то же самое, но понятнее. Красную строку внутри
       плеера при этом гасим: два сообщения об одном и том же рядом
       заставляют искать между ними разницу, которой нет. */
    fail('');
  }

  function noticeShown() {
    var box = $('w-notice');
    return !!box && !box.hidden;
  }

  /* ================= озвучки =================

     Раньше в меню «Озвучка» у каждой строки стояло слово «плеер» — все
     строки одинаковые. Выбор из нескольких «плееров» это не выбор.
     Причина была в одной букве: код спрашивал у источника поле `name`,
     а название озвучки лежит в поле `title`. Теперь в списке написано,
     чья это работа.

     Ссылки грузятся только для той озвучки, которую смотрят. Названия
     всех остальных известны сразу — они приезжают вместе со списком
     плееров, — а вот адреса видео приходится спрашивать у видеохостинга,
     и каждый такой вопрос это несколько секунд. У «Магической битвы» на
     некоторых источниках десятки озвучек: спросить все значит заставить
     человека ждать минуту перед пустым плеером. */
  function loadVideos(dub) {
    notice('');
    $('v-q').textContent = '…';
    $('v-d').textContent = '…';
    var url = '/api/videos?key=' + encodeURIComponent(KEY) +
              '&ordinal=' + encodeURIComponent(st.current) +
              '&source=' + encodeURIComponent(SOURCE) +
              '&title=' + encodeURIComponent(TITLE) +
              '&dub=' + encodeURIComponent(dub || '') +
              /* Язык решает, что открыть первым: по-английски —
                 оригинальную дорожку с текстом, а не дубляж. */
              '&lang=' + encodeURIComponent(A.langCode());
    return A.api.get(url).then(function (r) {
      st.dubs = (r && r.dubs) || [];
      st.dub = (r && r.chosen) || '';
      st.videos = (r && r.videos) || [];
      st.video = 0;
      renderMenus();
      if (!st.videos.length) {
        /* Серия в списке есть, а видео у неё нет — так бывает у только
           что вышедших серий: озвучка ещё не готова. */
        notice(A.t('Эту серию ещё не озвучили',
                   'This episode has no dub yet'),
               A.t('«' + SOURCE + '» её показывает, но видео пока не отдаёт. ' +
                   'У других источников свои озвучки — возможно, там она уже есть.',
                   '"' + SOURCE + '" lists it but has no video yet. ' +
                   'Other sources have their own dubs — it may already be there.'),
               true);
      }
    }).catch(function (err) {
      st.dubs = [];
      st.dub = '';
      st.videos = [];
      $('v-q').textContent = '—';
      $('v-d').textContent = A.t('нет', 'none');
      renderMenus();
      /* 502 от источника — это «у него нет», а не «у нас сломалось».
         Отдельная плашка вместо красной строки: она объясняет, что делать. */
      if (err.status === 502 || err.status === 404) {
        notice(A.t('На «' + SOURCE + '» этой серии нет',
                   'Not on "' + SOURCE + '"'),
               A.t('Он не отдал ни одного плеера. У каждого источника свои ' +
                   'озвучки — посмотрим, у кого эта серия есть.',
                   'It returned no player at all. Each source has its own dubs — ' +
                   'let us see who does have this episode.'),
               true);
      } else {
        fail(err.message);
      }
    });
  }

  /* Переключение озвучки: ссылки для неё ещё не спрашивали, идём за ними.
     Секунду ожидания видно по многоточию в подписи — молча замерший
     плеер выглядел бы как поломка. */
  function pickDub(name) {
    if (name === st.dub) return;
    var at = st.position;
    var wasPlaying = started && !video.paused;
    loadVideos(name).then(function () {
      if (!st.videos.length) return;
      if (started) {
        attach(currentUrl(), at);
        if (wasPlaying) play();
      }
    });
  }

  /* Ищем субтитры у других источников и уходим туда.

     Дорого — на каждый источник уходит поиск, список серий и список
     плееров, — поэтому только по нажатию. Ответы кэшируются, так что
     повторный заход почти бесплатен. */
  var subsSearching = false;

  function findSubs() {
    if (subsSearching) return;
    subsSearching = true;
    var name = (st.item && st.item.title) || TITLE;
    notice(A.t('Ищем субтитры', 'Looking for subtitles'),
           A.t('Смотрим у других источников — это несколько секунд.',
               'Checking other sources — this takes a few seconds.'));
    /* Язык сайта решает, где искать: по-английски первым спрашивается
       англоязычный источник — только у него текст поверх японской
       дорожки английский. */
    A.api.get('/api/subs?title=' + encodeURIComponent(name) +
              '&title_en=' + encodeURIComponent(st.title_en || '') +
              '&ordinal=' + encodeURIComponent(st.current) +
              '&lang=' + encodeURIComponent(A.langCode()) +
              '&skip=' + encodeURIComponent(SOURCE))
      .then(function (r) {
        if (!r || !r.found) {
          /* Их может не быть ни у кого — и сказать об этом прямо честнее,
             чем оставить человека гадать. */
          notice(A.t('Субтитров к этой серии не нашлось',
                     'No subtitles for this episode'),
                 A.t('Ни у одного источника нет варианта с оригинальной ' +
                     'озвучкой и текстом. Так бывает у старых тайтлов.',
                     'No source has an original-audio-with-text version. ' +
                     'That happens with older titles.'));
          return;
        }
        location.href = '/watch?key=' + encodeURIComponent(r.key) +
                        '&source=' + encodeURIComponent(r.source) +
                        '&title=' + encodeURIComponent(r.title || name) +
                        '&dub=' + encodeURIComponent(r.dub || '') +
                        '&poster=' + encodeURIComponent(r.poster || POSTER || '') +
                        '&year=' + encodeURIComponent(r.year || YEAR || '') +
                        '&genres=' + encodeURIComponent(r.genres || GENRES || '') +
                        '&total=' + encodeURIComponent(r.episodes_total || 0) +
                        '&ep=' + encodeURIComponent(st.current);
      })
      .catch(function (err) { fail(err.message); })
      .finally(function () { subsSearching = false; });
  }

  function renderMenus() {
    /* Есть ли смысл в самом меню.

       Нет озвучек — нет и выбора: блок прячется целиком. Одна озвучка и
       больше ничего — блок остаётся, но перестаёт быть меню: её название
       и так написано рядом, а список из одной строки собирает нажатия
       впустую.

       «И больше ничего» здесь не для красоты. Когда субтитров среди
       вариантов нет, в меню добавляется строка «Оригинал с субтитрами»,
       и тогда открыть его надо обязательно — иначе у источника, где
       озвучка ровно одна, до субтитров нельзя было бы добраться вовсе.
       Ровно эта ошибка и вышла: кнопку выключили, а строку под ней
       оставили. */
    var offerSubs = st.dubs.length > 0
                 && !st.dubs.some(function (d) { return d.sub; });
    var rowsInMenu = st.dubs.length + (offerSubs ? 1 : 0);

    var selDub = $('sel-dub');
    var dubBtn = $('dub-btn');
    if (selDub) selDub.hidden = !st.dubs.length;
    if (dubBtn) {
      var single = rowsInMenu < 2;
      dubBtn.disabled = single;
      dubBtn.classList.toggle('flat', single);
    }

    var dubs = $('m-d');
    dubs.textContent = '';
    dubs.appendChild(A.el('div', 'mh', A.t('Озвучка', 'Dub')));
    st.dubs.forEach(function (d) {
      var b = A.el('button', d.name === st.dub ? 'on' : null, d.name || '—');
      b.type = 'button';
      /* «Субтитры» это не другая озвучка, а другой способ смотреть:
         японская дорожка и текст поверх. Разница должна быть видна до
         нажатия, а не после — «текстом» этого не говорило. */
      if (d.sub) {
        /* Пишем и язык текста. Все субтитры, что дают источники, русские —
           живая проверка не нашла ни одного английского варианта. Человеку,
           переключившему сайт на английский, важно знать это ДО нажатия,
           а не после первой минуты. */
        b.appendChild(A.el('span', 'sub', d.lang === 'en'
          ? A.t('яп. + англ. текст', 'jp + english text')
          : A.t('яп. + рус. текст', 'jp + russian text')));
      }
      b.addEventListener('click', function () {
        dubs.classList.remove('show');
        pickDub(d.name);
      });
      dubs.appendChild(b);
    });
    /* Субтитров у этого источника нет — предлагаем поискать у других.

       Без этой строки они были недостижимы: источник по умолчанию может
       отдавать одну свою озвучку и субтитров не давать никогда, а рядом,
       у другого источника, они лежат почти у всего. Человек, открывший
       любой тайтл, субтитров не видел в принципе — и не знал, что они
       где-то есть. */
    if (offerSubs) {
      var find = A.el('button', 'findsubs',
                      A.t('Оригинал с субтитрами', 'Original with subtitles'));
      find.type = 'button';
      find.appendChild(A.el('span', 'sub', A.langCode() === 'en'
        ? A.t('поискать у других', 'russian text only')
        : A.t('поискать у других', 'look elsewhere')));
      find.addEventListener('click', function () {
        dubs.classList.remove('show');
        findSubs();
      });
      dubs.appendChild(find);
    }

    $('v-d').textContent = st.dub || '—';

    var qs = $('m-q');
    qs.textContent = '';
    qs.appendChild(A.el('div', 'mh', A.t('Качество', 'Quality')));
    st.videos.forEach(function (v, i) {
      var b = A.el('button', i === st.video ? 'on' : null, (v.quality || '?') + 'p');
      b.type = 'button';
      b.addEventListener('click', function () {
        st.video = i; renderMenus(); qs.classList.remove('show');
        if (started) attach(currentUrl(), st.position);
      });
      qs.appendChild(b);
    });
    $('v-q').textContent = st.videos.length
      ? ((st.videos[st.video] || {}).quality || '?') + 'p' : '—';

    /* Список источников.

       Раньше здесь стояли подряд все восемь. Половина из них этого тайтла
       не знает вовсе: переключаешься — и получаешь плашку «этого аниме
       нет на источнике». Список, где половина строк ведёт в тупик,
       заставляет перебирать их руками, чтобы выяснить то, что сайт может
       выяснить сам.

       Потом такие источники стали просто исчезать из списка — и это
       оказалось не лучше. Пропавшая строка ничего не объясняет: человек
       видит семь пунктов вместо восьми и не знает, куда делся восьмой и
       был ли он вообще. Молчаливое исчезновение читается как сбой.

       Теперь список разложен на три части с настоящими заголовками, и
       ничего не пропадает. Где тайтл есть — обычные кнопки. Где точно
       нет — видны, но серые и не нажимаются, с подписью «тут этого нет».
       До кого не дошли — отдельно, честно. */
    var ss = $('m-s');
    ss.textContent = '';
    ss.appendChild(A.el('div', 'mh', A.t('Откуда берётся видео', 'Video source')));
    if (!sources.length) {
      ss.appendChild(A.el('div', 'mnote', A.t('список загружается…', 'loading…')));
    }

    /* Заголовок части списка: линия, значок и подпись. Раньше на его
       месте стоял тот же класс, что у подписи «много озвучек» внутри
       кнопок, — заголовок и содержимое выглядели одинаково, и разделение
       не читалось вовсе. */
    function groupHead(kind, text) {
      var head = A.el('div', 'mgroup ' + kind);
      head.appendChild(A.el('i', 'dot', kind === 'yes' ? '✓'
                                      : (kind === 'no' ? '✕' : '?')));
      head.appendChild(A.el('span', null, text));
      return head;
    }

    function sourceButton(src, missing) {
      var b = A.el('button', src.id === SOURCE ? 'on' : null, src.label || src.id);
      b.type = 'button';
      if (missing) {
        /* Нажимать некуда: мы уже спросили и знаем, что там пусто.
           Кнопка остаётся видимой, но выключенной — это и есть ответ. */
        b.classList.add('gone');
        b.disabled = true;
        b.appendChild(A.el('span', 'sub', A.t('тут этого нет', 'not here')));
        return b;
      }
      b.appendChild(A.el('span', 'sub', src.dubs === 'many'
        ? A.t('много озвучек', 'many dubs') : A.t('одна озвучка', 'one dub')));
      b.addEventListener('click', function () {
        ss.classList.remove('show');
        if (src.id === SOURCE) return;
        switchSource(src.id);
      });
      return b;
    }

    if (!where.checked.length) {
      /* Ещё не спрашивали — показываем всё как есть. Проверка пойдёт,
         когда меню откроют: до этого она никому не нужна, а секунды
         стоит. */
      sources.forEach(function (src) { ss.appendChild(sourceButton(src, false)); });
    } else {
      var here = [], gone = [], unknown = [];
      sources.forEach(function (s) {
        if (where.here.indexOf(s.id) >= 0) here.push(s);
        else if (where.checked.indexOf(s.id) >= 0) gone.push(s);
        else unknown.push(s);
      });
      /* Открытый сейчас источник не может лежать в «тут этого нет»: мы
         из него прямо в эту секунду смотрим. Такое расхождение бывает,
         когда у источника название тайтла своё и до порога не дотянуло. */
      gone = gone.filter(function (s) {
        if (s.id !== SOURCE) return true;
        here.unshift(s);
        return false;
      });

      if (here.length) {
        ss.appendChild(groupHead('yes', A.t('Тут оно есть', 'Available here')));
        here.forEach(function (src) { ss.appendChild(sourceButton(src, false)); });
      }
      if (unknown.length) {
        ss.appendChild(groupHead('maybe', A.t('Не проверяли', 'Not checked')));
        unknown.forEach(function (src) { ss.appendChild(sourceButton(src, false)); });
      }
      if (gone.length) {
        ss.appendChild(groupHead('no', A.t('Искали, не нашли', 'Looked, not found')));
        gone.forEach(function (src) { ss.appendChild(sourceButton(src, true)); });
      }
    }
    $('v-s').textContent = SOURCE;
  }

  /* Переход на другой источник: ищем этот же тайтл там по названию */
  function switchSource(newSource) {
    var name = (st.item && st.item.title) || TITLE;
    if (!name) return;
    $('v-s').textContent = '…';
    showLoading(true);
    A.api.get('/api/search?q=' + encodeURIComponent(name) +
              '&lang=' + encodeURIComponent(A.langCode()) +
              '&source=' + encodeURIComponent(newSource) + '&any_source=false')
      .then(function (res) {
        var rows = res.items || [];
        if (!rows.length) {
          showLoading(false);
          $('v-s').textContent = SOURCE;
          fail(A.t('На этом источнике такого тайтла нет',
                   'This source does not have that title'));
          return;
        }
        /* Берём наиболее похожее название, а не просто первое */
        var best = rows[0];
        var want = name.toLowerCase();
        rows.forEach(function (r) {
          if ((r.title || '').toLowerCase() === want) best = r;
        });
        var ownPage = '/watch?key=' + encodeURIComponent(best.key) +
                  '&source=' + encodeURIComponent(newSource) +
                  '&title=' + encodeURIComponent(best.title || name) +
                  '&poster=' + encodeURIComponent(best.poster || POSTER || '') +
                  '&year=' + encodeURIComponent(best.year || YEAR || '') +
                  '&genres=' + encodeURIComponent(best.genres || GENRES || '') +
                  '&total=' + encodeURIComponent(best.episodes_total || 0) +
                  '&ep=' + encodeURIComponent(st.current);
        location.href = ownPage;   // свой же адрес, собран из закодированных частей
      })
      .catch(function (err) {
        showLoading(false);
        $('v-s').textContent = SOURCE;
        fail(err.message);
      });
  }

  /* меню в панели */
  document.querySelectorAll('.selbtn').forEach(function (b) {
    b.addEventListener('click', function (e) {
      e.stopPropagation();
      var m = $(b.dataset.menu);
      var open = m.classList.contains('show');
      document.querySelectorAll('.menu').forEach(function (x) { x.classList.remove('show'); });
      if (!open) m.classList.add('show');
      if (!open && b.dataset.menu === 'm-s') askWhere();
    });
  });

  /* Кнопка из плашки открывает то самое меню и сразу запускает проверку:
     человеку не нужно знать, что она называется «Источник» и живёт под
     плеером. */
  $('w-notice-go').addEventListener('click', function (e) {
    e.stopPropagation();
    document.querySelectorAll('.menu').forEach(function (m) { m.classList.remove('show'); });
    $('m-s').classList.add('show');
    $('m-s').scrollIntoView({block: 'nearest', behavior: 'smooth'});
    askWhere();
  });

  /* Спрашиваем, у кого тайтл есть, — ровно один раз и ровно тогда, когда
     меню источников открыли. Ответ на сервере кэшируется, так что
     повторные открытия ничего не стоят, но и первое не должно тормозить
     загрузку страницы: до открытия меню это знание никому не нужно. */
  function askWhere() {
    if (where.asked) return;
    where.asked = true;
    var name = (st.item && st.item.title) || TITLE;
    if (!name) return;
    var note = A.el('div', 'mnote', A.t('смотрим, у кого оно есть…',
                                        'checking who has it…'));
    $('m-s').appendChild(note);
    A.api.get('/api/where?title=' + encodeURIComponent(name) +
              '&lang=' + encodeURIComponent(A.langCode()))
      .then(function (r) {
        where.here = (r && r.here) || [];
        where.checked = (r && r.checked) || [];
        renderMenus();
        $('m-s').classList.add('show');
      })
      .catch(function () {
        /* Не вышло — меню остаётся прежним, со всеми источниками.
           Полный список хуже разобранного, но лучше пустого. */
        where.asked = false;
        note.remove();
      });
  }
  document.addEventListener('click', function () {
    document.querySelectorAll('.menu').forEach(function (m) { m.classList.remove('show'); });
  });

  /* ------------------------------------------------------------------ */
  /* Сохранение секунды. Гостю сохранять некуда — сервер его не пустит,  */
  /* поэтому даже не пробуем и не пугаем ошибкой.                       */
  /* ------------------------------------------------------------------ */
  function canSave() { return A.me && A.me.role !== 'guest'; }

  function payload(watched) {
    /* В полке рядом лежат «на какой серии остановились» и «сколько их
       всего», и они обязаны быть в одних единицах. Остановились мы на
       номере серии — значит и «всего» должно быть последним номером,
       а не количеством строк в списке.

       Раньше сюда клали количество, и на «Наруто» получалось
       total_eps: 131 при watched_ep: 370 — на карточке «370/131»
       и полоса, уехавшая за край. А проверка «дошли до конца»
       сравнивала номер серии с количеством: 370 >= 131, то есть
       ПЕРВАЯ же отмеченная серия закрывала весь тайтл как досмотренный. */
    var last = lastOrdinal();
    var total = last || (st.item && st.item.total_eps) || TOTAL;
    return {
      key: KEY, source: SOURCE,
      title: (st.item && st.item.title) || TITLE,
      title_en: st.title_en || (st.item && st.item.title_en) || '',
      poster: (st.item && st.item.poster) || POSTER,
      year: (st.item && st.item.year) || YEAR,
      genres: (st.item && st.item.genres) || GENRES,
      total_eps: total,
      watched_ep: st.current,
      position: Math.round(st.position),
      status: watched && last && st.current >= last ? 'done' : 'watching'
    };
  }

  function save(asWatched, opts) {
    if (!canSave()) return Promise.resolve();
    var path = asWatched ? '/api/library/watched' : '/api/library/progress';
    return A.api.post(path, payload(asWatched), opts).then(function () {
      $('w-saved').textContent = A.t('сохранено ', 'saved at ') + A.mmss(st.position);
    }).catch(function () {});
  }

  /* Настройка «Отмечать просмотренной автоматически после 90% серии»
     была в кабинете с самого начала, сохранялась в базу — и не делала
     ничего: в этом файле её никто не читал. Ниже она наконец работает.
     markedThisEpisode нужен, чтобы отметка ушла один раз за серию,
     а не на каждом кадре после девяноста процентов. */
  var markedThisEpisode = false;

  /* Автоотметка на 90% серии теперь просто работает. Раньше это была
     настройка в кабинете — но выключать её незачем: это ровно то, чего
     от плеера ждут. Настройка, которую никто не трогает, только копит
     код и место в базе. */
  function autoMarkEnabled() { return true; }

  function autoNextEnabled() {
    var sw = $('w-autobox') && $('w-autobox').querySelector('.sw');
    return !!sw && sw.getAttribute('aria-checked') === 'true';
  }


  /* ================================================================== */
  /* Настоящее воспроизведение                                          */
  /* ================================================================== */
  var video = $('video');
  var hls = null;
  var saveTimer = null;
  var started = false;
  /* Чем снимаем обработчики предыдущего адреса — см. attach() */
  var mediaAbort = null;

  function showLoading(on) { $('loadwrap').hidden = !on; }

  function chrome(on) {
    /* Прячем заставку и надписи, когда видео пошло */
    $('bgart').style.opacity = on ? '1' : '0';
    $('veilg').style.opacity = on ? '1' : '0';
    $('titleover').style.opacity = on ? '1' : '0';
    $('bigplay').hidden = !on;
    paintPlayIcon();
  }

  function currentUrl() {
    if (!st.videos.length) return null;
    var v = st.videos[st.video] || st.videos[0];
    return v ? A.safeUrl(v.url) : null;
  }

  /* Сюда попадает только адрес, уже прошедший safeUrl в currentUrl() */
  function attach(rawSrc, startAt) {
    var safeSrc = A.safeUrl(rawSrc);   // повторная проверка: дешевле, чем ошибка
    if (!safeSrc) { fail(A.t('Плеер не дал ссылку на видео', 'The player returned no video link')); return; }
    showLoading(true);
    fail('');
    /* Полный сброс: без него при переходе с потока на обычный файл
       остаётся прежний источник и играет старое видео. */
    if (hls) { hls.destroy(); hls = null; }
    video.pause();
    video.removeAttribute('src');
    try { video.load(); } catch (e) { /* пустой src — это нормально */ }

    /* Обработчики предыдущего адреса снимаем перед тем, как ставить новые.
       Раньше они вешались с {once:true} на каждое переключение серии,
       озвучки и качества — а {once:true} снимает обработчик только КОГДА
       ОН СРАБОТАЛ. Из двух всегда срабатывал один, второй оставался
       висеть навсегда. За вечер сериала их набирались десятки, и старая
       «ошибка» от давно отброшенного адреса могла выскочить поверх
       нормально идущего видео. */
    if (mediaAbort) mediaAbort.abort();
    mediaAbort = (typeof AbortController === 'function') ? new AbortController() : null;
    var opt = mediaAbort ? { signal: mediaAbort.signal } : undefined;

    /* Перемотка на сохранённую секунду.

       Здесь была ошибка, из-за которой не работала главная задумка всего
       приложения. Перемотка стояла внутри ready() под условием
       isFinite(video.duration) — и для потоков m3u8 это условие почти
       всегда ложно: ready() зовётся по событию «разобран манифест», а
       длину видео браузер к этому моменту ещё не знает. Условие тихо
       не выполнялось, перемотки не происходило, и серия начиналась
       с нуля. Страница при этом честно писала «продолжить с 08:10» —
       и тут же его теряла. То же самое происходило при переключении
       качества и озвучки посреди серии.

       Теперь ждём момент, когда длина станет известна, и перематываем
       тогда. Условие сохранено: без длины перемотка бессмысленна. */
    var seekDone = false;

    function trySeek() {
      if (seekDone || !(startAt > 0)) return;
      if (!isFinite(video.duration) || video.duration <= 1) return;
      seekDone = true;
      video.currentTime = Math.min(startAt, video.duration - 1);
    }

    video.addEventListener('loadedmetadata', trySeek, opt);
    video.addEventListener('durationchange', trySeek, opt);
    video.addEventListener('canplay', trySeek, opt);

    function ready() {
      showLoading(false);
      chrome(false);
      trySeek();
      video.play().catch(function () {
        /* Браузер может не пустить автозапуск — тогда просто ждём клика */
        chrome(true);
      });
    }

    var isStream = /\.m3u8(\?|$)/i.test(safeSrc);
    if (isStream && window.Hls && window.Hls.isSupported()) {
      hls = new window.Hls({ maxBufferLength: 30, enableWorker: true });
      hls.loadSource(safeSrc);
      hls.attachMedia(video);
      hls.on(window.Hls.Events.MANIFEST_PARSED, ready);
      /* Считаем попытки восстановления: без счётчика при мёртвой ссылке
         плеер уходит в бесконечный цикл и греет процессор впустую. */
      var retries = 0;
      hls.on(window.Hls.Events.ERROR, function (e, data) {
        if (!data.fatal) return;
        showLoading(false);
        if (retries < 3 && data.type === window.Hls.ErrorTypes.NETWORK_ERROR) {
          retries++; hls.startLoad(); return;
        }
        if (retries < 3 && data.type === window.Hls.ErrorTypes.MEDIA_ERROR) {
          retries++; hls.recoverMediaError(); return;
        }
        hls.destroy(); hls = null;
        fail(A.t('Не удалось запустить видео. Попробуйте другую озвучку или источник.',
                 'Could not start the video. Try a different dub or source.'));
      });
    } else {
      /* Сафари умеет потоки сам, обычные mp4 играют везде */
      video.addEventListener('loadedmetadata', ready, opt);
      video.addEventListener('error', function () {
        showLoading(false);
        fail(A.t('Видео не открылось. Попробуйте другую озвучку или качество.',
                 'The video did not open. Try another dub or quality.'));
      }, opt);
      video.src = safeSrc;
      video.load();
    }
  }

  function play() {
    var url = currentUrl();
    if (!url) {
      fail(A.t('Для этой серии нет доступного видео', 'No video available for this episode'));
      return;
    }
    started = true;
    attach(url, st.position);
  }

  /* ================= нажатия по картинке =================

     Нажатие по экрану ставит паузу и снимает её — так работает любой
     плеер, и именно этого не было: нажимаешь на картинку, а она не
     реагирует, и приходится целиться в маленькую кнопку внизу.

     Двойное нажатие разворачивает на весь экран и сворачивает обратно.
     Чтобы одно не мешало другому, пауза срабатывает не сразу, а через
     четверть секунды: если за это время придёт второе нажатие, паузы
     не будет — только разворот. */
  var tapTimer = null;

  function flash(paused) {
    var box = $('tapflash');
    var icon = $('flash-icon');
    if (!box || !icon) return;
    /* Показываем то, что произошло: пошло — значок «пуск», встало —
       две палочки паузы. */
    icon.setAttribute('d', paused
      ? 'M7 4h3.4v18H7V4Zm8.6 0H19v18h-3.4V4Z'
      : 'M8.5 4.8v16.4L21 13 8.5 4.8Z');
    box.classList.remove('show');
    void box.offsetWidth;                 // перезапуск анимации
    box.classList.add('show');
  }

  function toggle() {
    if (!started) { play(); return; }
    if (video.paused) {
      video.play();
      flash(false);
    } else {
      video.pause();
      flash(true);
    }
  }

  var tapzone = $('tapzone');
  if (tapzone) {
    tapzone.addEventListener('click', function () {
      clearTimeout(tapTimer);
      tapTimer = setTimeout(toggle, 240);
    });
    tapzone.addEventListener('dblclick', function () {
      clearTimeout(tapTimer);
      toggleFullscreen();
    });
  }

  /* ================= панели прячутся во время просмотра =================

     Пока идёт видео, панели уходят и не закрывают картинку. Любое
     движение мыши возвращает их на три секунды. На паузе они остаются:
     человек остановил специально — скорее всего, чтобы куда-то нажать. */
  var idleTimer = null;
  var player = $('player');

  function wake() {
    if (!player) return;
    player.classList.remove('idle');
    clearTimeout(idleTimer);
    if (!started || video.paused) return;
    /* Прячем панели только в полном экране.

       В обычном режиме панель лежит ПОД картинкой и ничего не
       закрывает — прятать её не от чего. А выглядело это так: включаешь
       серию, через три секунды кнопки и дорожка исчезают, и на их месте
       остаётся пустая полоса. Пустое место там, где только что были
       кнопки, читается как поломка. */
    if (!document.fullscreenElement && !player.classList.contains('fs')) return;
    idleTimer = setTimeout(function () {
      /* Открытое меню — знак, что человек как раз выбирает. Прятать
         панель у него из-под руки нельзя. */
      if (document.querySelector('.menu.show')) { wake(); return; }
      player.classList.add('idle');
    }, 3000);
  }

  if (player) {
    ['mousemove', 'pointerdown', 'keydown', 'touchstart'].forEach(function (ev) {
      player.addEventListener(ev, wake, {passive: true});
    });
    player.addEventListener('mouseleave', function () {
      if (!document.fullscreenElement && !player.classList.contains('fs')) return;
      if (started && !video.paused) player.classList.add('idle');
    });
  }
  video.addEventListener('pause', function () { wake(); paintPlayIcon(); });
  video.addEventListener('playing', function () { wake(); paintPlayIcon(); });

  /* Значок на кнопке всегда показывает, что она сделает. Раньше на ней
     навсегда оставался «пуск», даже когда видео шло. */
  function paintPlayIcon() {
    var icon = $('play-icon');
    if (!icon) return;
    var playing = started && !video.paused;
    icon.setAttribute('d', playing
      ? 'M5.6 3.2h2.7v11.6H5.6V3.2Zm6.1 0h2.7v11.6h-2.7V3.2Z'
      : 'M6 3.6v10.8L14.4 9 6 3.6Z');
    $('c-play').setAttribute('aria-label', playing
      ? A.t('Пауза', 'Pause') : A.t('Пуск', 'Play'));
  }

  $('bigplay').addEventListener('click', play);
  $('c-play').addEventListener('click', toggle);
  $('c-back').addEventListener('click', function () { video.currentTime = Math.max(0, video.currentTime - 10); });
  $('c-fwd').addEventListener('click', function () { video.currentTime += 10; });
  var vol = $('c-vol');
  var lastVol = 1;

  function paintVol() {
    var v = video.muted ? 0 : video.volume;
    vol.value = v;
    vol.style.setProperty('--fill', (v * 100) + '%');
    $('c-mute').style.color = v === 0 ? 'var(--dimmer)' : '';
    var wave = $('vol-wave');
    if (wave) wave.style.opacity = v === 0 ? '0' : '1';
  }

  $('c-mute').addEventListener('click', function () {
    if (video.muted || video.volume === 0) {
      video.muted = false;
      video.volume = lastVol || 1;
    } else {
      lastVol = video.volume;
      video.muted = true;
    }
    paintVol();
  });
  vol.addEventListener('input', function () {
    video.muted = false;
    video.volume = parseFloat(this.value);
    if (video.volume > 0) lastVol = video.volume;
    paintVol();
  });
  video.addEventListener('volumechange', paintVol);
  /* Полный экран.

     Раньше здесь был вызов без обработки отказа:

         else if (box.requestFullscreen) box.requestFullscreen();

     requestFullscreen возвращает обещание, и оно может быть отклонено —
     на айфоне полный экран умеет только сам элемент video, а внутри
     встроенного окна его вообще запрещает политика страницы. Проверка
     `if (box.requestFullscreen)` этого не ловит: метод существует, он
     просто отказывает. Получалось, что кнопка молча не работает,
     а в консоли копится необработанная ошибка.

     Теперь при отказе пробуем показать во весь экран само видео —
     ровно то, что работает на телефоне, — а если и это нельзя,
     говорим об этом человеку вместо тишины. */
  /* Разворачивает и сворачивает. Раньше кнопка умела только разворачивать,
     а обратно выходили клавишей Escape — о которой знают не все. */
  function toggleFullscreen() {
    if (document.fullscreenElement) {
      var out = document.exitFullscreen();
      if (out && typeof out.catch === 'function') out.catch(function () {});
      return;
    }
    enterFullscreen();
  }

  function enterFullscreen() {
    /* Разворачиваем весь плеер, а не одну картинку: дорожка и кнопки
       живут за пределами `#screen`, и в полном экране их просто не было
       бы — ни паузы, ни перемотки, ни выхода. */
    var box = $('player') || $('screen');

    function videoFallback() {
      if (video.webkitEnterFullscreen) {
        try { video.webkitEnterFullscreen(); return true; } catch (e) { /* нельзя */ }
      }
      return false;
    }

    if (!box.requestFullscreen) {
      if (!videoFallback()) {
        fail(A.t('Браузер не пускает в полный экран',
                 'The browser does not allow fullscreen here'));
      }
      return;
    }
    var attempt = box.requestFullscreen();
    if (attempt && typeof attempt.catch === 'function') {
      attempt.catch(function () {
        if (!videoFallback()) {
          fail(A.t('Браузер не пускает в полный экран',
                   'The browser does not allow fullscreen here'));
        }
      });
    }
  }

  $('c-full').addEventListener('click', toggleFullscreen);

  /* Значок кнопки показывает, что она сделает: развернуть или свернуть. */
  document.addEventListener('fullscreenchange', function () {
    var icon = $('full-icon');
    var on = !!document.fullscreenElement;
    /* Класс, а не только псевдокласс `:fullscreen`. Псевдокласс
       поддерживают не все: на iPhone видео разворачивается своим
       способом, и раскладка по `:fullscreen` там не сработала бы
       вовсе. Класс работает одинаково везде. */
    if (player) player.classList.toggle('fs', on);
    if (icon) {
      icon.setAttribute('d', on
        ? 'M2.7 6.6h3.9V2.7M15.3 6.6h-3.9V2.7M15.3 11.4h-3.9v3.9M2.7 11.4h3.9v3.9'
        : 'M6.6 2.7H2.7v3.9M11.4 2.7h3.9v3.9M11.4 15.3h3.9v-3.9M6.6 15.3H2.7v-3.9');
    }
    $('c-full').setAttribute('aria-label', on
      ? A.t('Свернуть', 'Exit fullscreen') : A.t('Во весь экран', 'Fullscreen'));
    wake();
  });

  /* дорожка времени идёт за видео */
  video.addEventListener('timeupdate', function () {
    st.position = Math.round(video.currentTime);
    st.duration = video.duration || 0;
    /* Пока дорожку тянут, она слушается пальца, а не видео. Без этой
       проверки ползунок дёргался бы назад на каждый кадр — то есть
       двадцать пять раз в секунду отпрыгивал из-под пальца туда, где
       видео сейчас на самом деле. */
    if (!dragging) {
      paintPosition();
      $('w-cur').textContent = A.mmss(st.position);
    }
    if (st.duration) $('w-total').textContent = A.mmss(st.duration);

    /* Сохраняем не чаще раза в 15 секунд: незачем дёргать сервер каждый кадр */
    if (!saveTimer) {
      saveTimer = setTimeout(function () { saveTimer = null; save(false); }, 15000);
    }

    /* Автоотметка на девяноста процентах — ровно то, что обещает кабинет */
    if (!markedThisEpisode && autoMarkEnabled() && st.duration > 0 &&
        video.currentTime / st.duration >= 0.9) {
      markedThisEpisode = true;
      save(true);
    }
  });

  video.addEventListener('play', function () { chrome(false); });
  video.addEventListener('ended', function () {
    /* Если автоотметка уже сработала на девяноста процентах, второй раз
       писать в журнал просмотров не нужно — иначе одна серия считалась бы
       за две и итоги года завышались. */
    if (!markedThisEpisode) {
      markedThisEpisode = true;
      save(true);
    } else {
      save(false);
    }
    var nums = ordinals();
    var at = nums.indexOf(st.current);
    var next = (at >= 0 && at + 1 < nums.length) ? nums[at + 1] : null;
    if (next !== null && autoNextEnabled()) {
      pick(next);
      setTimeout(play, 800);
    }
  });
  /* keepalive: без него браузер отменяет этот запрос вместе со вкладкой,
     и секунда, на которой человек остановился, пропадает.
     Отложенное сохранение при этом снимаем: иначе за уходом со страницы
     следом уходит второй такой же запрос — лишняя запись в базу
     и лишний повод для ограничителя частоты. */
  window.addEventListener('beforeunload', function () {
    clearTimeout(saveTimer);
    saveTimer = null;
    save(false, { keepalive: true });
  });

  /* ================= перемотка по дорожке =================

     Раньше здесь был один обработчик click: ткнул — прыгнуло. Тянуть
     ползунок было нельзя вообще. Зажимаешь, ведёшь — полоса стоит на
     месте, время не меняется, и понять, куда попадёшь, можно только
     отпустив и посмотрев. Промахнулся — тыкай снова.

     Теперь дорожка тянется. Пока держишь, полоса и время идут за
     пальцем, а само видео перематывается один раз — когда отпустил.
     Перематывать на каждое движение нельзя: браузер на каждый такой
     скачок заново тянет кусок потока, картинка встаёт, и перемотка
     превращается в рывки.

     Работает и мышью, и пальцем: pointer-события покрывают то и другое
     разом, а setPointerCapture держит указатель за дорожкой, даже если
     палец ушёл за её пределы — иначе ползунок терялся на середине
     движения, стоило чуть съехать вверх. */
  var track = $('track');
  var dragging = false;

  /* Куда указывает точка. Слева от дорожки — ноль, справа — единица;
     без зажима это невозможно, а с зажимом человек уводит палец за край
     постоянно. */
  function trackShare(clientX) {
    var r = track.getBoundingClientRect();
    if (!r.width) return 0;
    return Math.max(0, Math.min(1, (clientX - r.left) / r.width));
  }

  /* Показать положение, не трогая видео. */
  function paintSeek(p) {
    $('pos').style.width = (p * 100) + '%';
    $('knob').style.left = (p * 100) + '%';
    var known = st.duration || 0;
    if (known) $('w-cur').textContent = A.mmss(Math.round(known * p));
  }

  /* Отпустили — вот теперь двигаем. */
  function applySeek(p) {
    if (started && st.duration) {
      video.currentTime = st.duration * p;
      return;
    }
    /* Видео ещё не запускали. Тогда дорожка выбирает не текущее время,
       а место, с которого начнётся просмотр: длительность неизвестна,
       поэтому берём средние двадцать три минуты серии. */
    st.position = Math.round((st.duration || 1380) * p);
    paintSeek(p);
    $('w-cur').textContent = A.mmss(st.position);
    save(false);
  }

  if (track) {
    track.addEventListener('pointerdown', function (e) {
      /* Только основная кнопка: правой вызывают меню браузера, и
         перематывать по ней — неожиданность. */
      if (e.button !== undefined && e.button !== 0) return;
      e.preventDefault();
      dragging = true;
      track.classList.add('dragging');
      if (track.setPointerCapture) {
        try { track.setPointerCapture(e.pointerId); } catch (err) { /* не беда */ }
      }
      paintSeek(trackShare(e.clientX));
    });

    track.addEventListener('pointermove', function (e) {
      if (!dragging) return;
      e.preventDefault();
      paintSeek(trackShare(e.clientX));
    });

    function endDrag(e) {
      if (!dragging) return;
      dragging = false;
      track.classList.remove('dragging');
      applySeek(trackShare(e.clientX));
    }
    track.addEventListener('pointerup', endDrag);
    /* Отмена приходит, когда указатель отобрали: системный жест,
       переключение окна, звонок на телефоне. Без этой строки дорожка
       осталась бы «зажатой» навсегда и перестала слушаться. */
    track.addEventListener('pointercancel', function () {
      dragging = false;
      track.classList.remove('dragging');
      paintPosition();
    });

    /* Дорожка доступна и с клавиатуры: стрелки на пять секунд,
       Home и End — в начало и конец. Раньше на неё нельзя было даже
       встать табом. */
    track.tabIndex = 0;
    track.setAttribute('role', 'slider');
    track.setAttribute('aria-label', A.t('Перемотка', 'Seek'));
    track.addEventListener('keydown', function (e) {
      var step = { ArrowRight: 5, ArrowLeft: -5, ArrowUp: 5, ArrowDown: -5 }[e.key];
      var known = st.duration || 0;
      if (step && known) {
        e.preventDefault();
        applySeek(Math.max(0, Math.min(1, (st.position + step) / known)));
      } else if (e.key === 'Home') {
        e.preventDefault();
        applySeek(0);
      } else if (e.key === 'End' && known) {
        e.preventDefault();
        applySeek(1);
      }
    });
  }

  /* Положение дорожки по текущему времени видео. Отдельной функцией,
     потому что зовётся из двух мест: за каждым кадром видео и после
     отменённого перетаскивания. */
  function paintPosition() {
    var p = st.duration ? video.currentTime / st.duration : 0;
    $('pos').style.width = (p * 100) + '%';
    $('knob').style.left = (p * 100) + '%';
  }


  document.querySelectorAll('.sw').forEach(function (b) {
    b.addEventListener('click', function () {
      b.setAttribute('aria-checked', b.getAttribute('aria-checked') === 'true' ? 'false' : 'true');
    });
  });

  /* ------------------------------------------------------------------ */
  A.boot(function (me) {
    if (!me) { location.href = '/'; return; }
    if (!KEY) { location.href = '/'; return; }

    /* Переключатель «включать следующую самой» всегда стоял во включённом
       положении, что бы человек ни выбрал в кабинете: сохранённая настройка
       сюда просто не доезжала. Теперь доезжает. */
    var swAuto = $('w-autobox') && $('w-autobox').querySelector('.sw');
    if (swAuto) {
      var wantAuto = !me.settings || me.settings.autonext !== false;
      swAuto.setAttribute('aria-checked', wantAuto ? 'true' : 'false');
    }

    if (me.role === 'guest') {
      $('w-autobox').style.opacity = '.5';
      $('w-autonote').textContent = A.t('гостю не сохраняем', 'not saved for guests');
    }
    /* Источник мог не приехать в адресе — например, по ссылке от друга
       или из закладки. Тогда берём основной для языка сайта: язык к
       этому моменту уже применён, а до него он неизвестен. */
    if (!SOURCE) SOURCE = A.defaultSource();

    /* Список источников приходит своим запросом. Дождавшись его,
       перерисовываем меню — если оно к тому моменту уже нарисовано. */
    A.api.get('/api/sources?lang=' + encodeURIComponent(A.langCode()))
      .then(function (rows) {
        sources = rows || [];
        if (st.dubs.length) renderMenus();

        /* Источник другого языка — не открываем.

           В список тайтл мог попасть с англоязычного источника, а сайт
           потом переключили на русский. Тогда по ссылке из списка
           открывалась «Магическая битва» с озвучкой English dub: сайт
           русский, а звук английский. Это ровно та путаница, ради
           которой источники и разделены по языкам.

           Уходим на источник своего языка. Сама страница при этом
           перезагрузится с новым адресом — тот же тайтл, та же серия. */
        var mine = sources.some(function (x) { return x.id === SOURCE; });
        if (!mine && sources.length) {
          notice(A.t('Переключаем на источник вашего языка',
                     'Switching to a source in your language'),
                 A.t('Этот тайтл был открыт на источнике другого языка. Ищем его ' +
                     'там, где озвучка совпадает с языком сайта.',
                     'This title was opened on a source in another language. ' +
                     'Looking for it where the audio matches the site language.'));
          switchSource(A.defaultSource());
        }
      }).catch(function () { sources = []; });

    /* Описание грузим отдельной ниткой, а не в общей цепочке.
       Оно ни от серий, ни от плееров не зависит — а в цепочке падало
       вместе с ними: у тайтла, которого нет на источнике, под плеером
       оставался прочерк вместо описания, хотя каталог о нём знает. */
    loadAbout();
    /* И списком частей тоже: он ни от серий, ни от плееров не зависит.
       Раньше в общей цепочке всё, что после отказа источника, просто не
       выполнялось — и у тайтла, которого нет на источнике, пропадало
       заодно и описание, и всё остальное, что каталог про него знает. */
    loadRelated();

    loadItem()
      .then(function () {
        /* Рисуем шапку сразу, ещё до списка серий. Название, обложка и
           жанры у нас уже есть — они приехали в адресе страницы или из
           полки. Раньше renderHead звался только внутри loadEpisodes,
           и если источник не отвечал, человек оставался на странице
           с прочерком вместо названия: непонятно даже, что открыто. */
        renderHead();
      })
      .then(loadEpisodes)
      .catch(function (err) {
        /* Список серий не пришёл вовсе. Причина почти всегда одна из двух:
           тайтла нет у этого источника, или он только вышел и его ещё не
           выложили. И то и другое лечится сменой источника, поэтому
           говорим об этом прямо, а не показываем голый код ошибки. */
        notice(A.t('На «' + SOURCE + '» этого аниме нет',
                   'Not on "' + SOURCE + '"'),
               A.t('Либо оно только вышло и его ещё не выложили, либо его тут ' +
                   'просто нет. Посмотрим, у кого оно есть.',
                   'Either it just came out and is not posted yet, or it is not ' +
                   'here at all. Let us see who does have it.'),
               true);
        /* Шапка осталась бы на «Загружаем серии…» навсегда: renderHead
           рисуется до похода за списком, а после отказа его никто не
           перерисовывает. Надпись «загружаем» там, где загрузка уже
           кончилась неудачей, — обещание, которое не сбудется. */
        $('w-epno').textContent = A.t('Серии не загрузились', 'Episodes did not load');
        /* Серий нет — значит нет и озвучек. Меню «Озвучка» до сюда
           не доходило и оставалось висеть с прочерком: список, которого
           нет, притворялся пустым вместо того, чтобы исчезнуть. */
        st.dubs = [];
        st.dub = '';
        st.videos = [];
        renderMenus();
        showLoading(false);
        throw err;
      })
      .then(ensurePoster)
      .then(function () {
        if (st.position > 0) {
          $('w-cur').textContent = A.mmss(st.position);
          $('w-saved').textContent = A.t('продолжить с ', 'resume at ') + A.mmss(st.position);
        }
        return loadVideos(WANT_DUB);
      })
      .catch(function (err) {
        /* Если плашка уже всё объяснила — молчим. Иначе показываем
           обычную строку ошибки. */
        if (!noticeShown()) fail(err.message);
      });
  });
})();
