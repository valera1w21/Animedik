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
  var WANT_DUB = (params.get('dub') || '').slice(0, 200);
  /* Номер тайтла в справочнике («mal:5114»). Одно аниме — одна карточка в
     списке, на каком бы языке и с какого источника его ни смотрели: по
     этому номеру сервер склеивает записи (store.is_twin), а страница
     находит в полке прогресс, сохранённый с другого источника. Не пришёл
     в адресе — возьмём из полки или из «эта история целиком». */
  var CID = /^(?:mal|al):\d{1,12}$/.test(params.get('cid') || '') ? params.get('cid') : '';
  function cidParam() { return CID ? '&cid=' + encodeURIComponent(CID) : ''; }
  /* Хентай не на вашем языке (fix-21): «не обязательно на каком-то языке —
     просто чтобы был». rx=1 и alang приходят из окна частей, когда тайтл
     нашёлся только на другом языке; тогда плеер работает на alang, а
     настройку не трогает (A.setPageAnimeLang). Для обычных тайтлов
     ничего не меняется — строгое правило языка (fix-20). */
  var RX = params.get('rx') === '1';
  var ALANG = (params.get('alang') || '').slice(0, 8);
  function hentaiAllowed() {
    var me = A.me;
    return !!me && me.role !== 'guest' && !!me.settings &&
           me.settings.adult === true && me.settings.hentai === true;
  }
  function currentPart() {
    return (st.related || []).filter(function (p) { return p.current; })[0] || null;
  }
  /* Хентай ли открыт: по адресу или по «эта история целиком». */
  function isRx() {
    var now = currentPart();
    return RX || !!(now && (now.hentai === true || now.rating === 'rx'));
  }
  function rxParam() { return isRx() && hentaiAllowed() ? '&rx=1' : ''; }
  /* Найдено на другом языке — в адрес новой страницы тот язык. */
  function langParam(found) {
    var code = (found && found.other_lang && found.alang) || A.pageAnimeLang();
    return code && isRx() ? '&rx=1&alang=' + encodeURIComponent(code) : '';
  }
  /* Подпись «Есть только на английском / русском» у названия. */
  function paintLangOnly() {
    var h = $('w-side-title');
    if (!h) return;
    var old = h.querySelector('.langonly');
    if (old) old.remove();
    var code = A.pageAnimeLang();
    if (!code) return;
    var saved = (A.me && A.me.settings && A.me.settings.anime_lang) || 'ru';
    if (saved === 'en') saved = 'ensub';
    var ru = code === 'ru' ? 'Есть только на русском' : 'Есть только на английском';
    var en = code === 'ru' ? 'Only available in Russian' : 'Only available in English';
    /* Английский выбран и так, просто другой вариант. */
    if (saved !== 'ru' && code !== 'ru') {
      ru = code === 'ensub' ? 'Есть только с английскими субтитрами' : 'Есть только с английской озвучкой';
      en = code === 'ensub' ? 'Only with English subtitles' : 'Only with an English dub';
    }
    var tag = A.el('span', 'langonly', A.t(ru, en));
    tag.setAttribute('data-ru', ru);
    tag.setAttribute('data-en', en);
    tag.title = A.t('На языке, выбранном в настройках, этого нет — играет тот, на котором есть.',
                    'Not available in your anime language — playing the language it has.');
    h.appendChild(document.createTextNode(' '));
    h.appendChild(tag);
  }

  /* Сколько символов сервер принимает в названии и в имени озвучки.

     Название в адресе режется до 300, но дальше оно уходило в запросы
     целиком — в том числе туда, где сервер ждёт меньше. Длинное название
     (у сборников и спешлов такое бывает) получало в ответ 422, и вместо
     описания или списка серий человек видел ошибку проверки. Теперь всё,
     что уходит на сервер, режется здесь по одним правилам:
       300 — поиск, описание, серии и видео;
       200 — справочник частей, «у кого есть», субтитры, поиск части;
       200 — имя озвучки. */
  var TITLE_MAX = 300;
  var TITLE_MAX_SHORT = 200;
  var DUB_MAX = 200;

  function cut(text, max) { return String(text || '').slice(0, max); }

  var st = {
    /* dubs — только названия озвучек, они приходят бесплатно вместе со
       списком плееров. videos — ссылки той одной озвучки, которую сейчас
       смотрят: за ними надо ходить к видеохостингу, и это секунды.
       videosFor — номер серии, которой принадлежат videos: без него
       нельзя отличить ссылки новой серии от ссылок прошлой.
       badDubs — озвучки этой серии, которые не ответили.
       savedEp/savedPos — где остановились по записи в полке: секунда
       относится только к своей серии. */
    episodes: [], current: 0, dubs: [], dub: '', videos: [], video: 0,
    /* deadDubs — озвучки этой серии, которые ссылки отдали, но ни одно их
       качество так и не открылось (см. allQualitiesFailed). */
    videosFor: 0, badDubs: [], deadDubs: [], savedEp: 0, savedPos: 0,
    related: [], title_en: '', item: null, duration: 0, position: 0,
    /* Другие имена тайтла из справочника (синонимы). Источник может знать
       тайтл только под ними: «Старшая школа DxD» = «Демоны старшей
       школы». Приходят с частями истории (loadRelated); пока не пришли —
       сервер спросит справочник сам. */
    alt: [],
    /* Субтитры отдельными файлами (ответ /api/videos, поле subs) — к той
       серии и озвучке, что сейчас в videos. hlsSubs — дорожки текста
       внутри самого потока (их находит hls.js). cc — какая включена:
       'x:<номер>' (наш файл), 'h:<номер>' (из потока) или '' — никакая.
       ccWant — что человек выбрал сам: язык ('en', 'ru') или 'off'.
       Пустое — не выбирал, решает язык аниме (см. pickCc). */
    subs: [], hlsSubs: [], cc: '', ccWant: ''
  };

  function altParam() {
    return '&alt=' + encodeURIComponent((st.alt || []).join('|').slice(0, 1200));
  }

  /* Список источников для меню «Откуда берётся видео».
     Раньше он лежал в window.__sources и заполнялся отдельным запросом,
     который никто не ждал. Меню рисуется сразу после загрузки плееров —
     то есть почти всегда РАНЬШЕ, чем приходил этот список, и внутри
     оставался один заголовок без единой кнопки. Переключить источник
     было нельзя, пока не перезагрузишь страницу.
     Теперь список живёт здесь, а меню перерисовывается, когда он придёт. */
  var sources = [];

  /* Кто из источников этот тайтл знает (ответ /api/where).

     Раньше спрашивали, только когда меню «Источник» открыли: меню
     показывало все источники подряд, через пару секунд приходил ответ,
     и список на глазах перестраивался в группы «Тут оно есть» / «Не
     проверяли» / «Искали, не нашли» — строки прыгали под рукой.

     Теперь спрашиваем заранее, в фоне, когда серии и видео уже
     загрузились (см. scheduleWhere), — обычно ответ готов раньше, чем
     до меню дотянется рука. state: idle — ещё не спрашивали, pending —
     спросили, ждём, done — ответ есть, failed — не вышло (спросим
     снова, когда меню откроют). */
  var where = { state: 'idle', here: [], checked: [], silent: [] };
  /* Порядок строк в открытом меню. Пока меню открыто, он не меняется:
     пришедший ответ только дописывает в строки «есть / нет». */
  var srcOrder = [];

  /* Номера серий так, как их даёт источник, и последний из них.

     Разница между «сколько серий в списке» и «какой у последней номер»
     кажется придиркой ровно до первого источника, который нумерует
     серии не с единицы. RU-A так и делает: «Наруто Ураганные
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
      var items = r.items || [];
      /* Своя запись — по ключу источника, а нет её — по номеру
         справочника: тайтл могли смотреть на другом языке и с другого
         источника, и тогда в полке он лежит под чужим ключом. */
      st.item = items.filter(function (i) { return i.key === KEY; })[0] ||
                (CID ? items.filter(function (i) { return i.cid === CID; })[0] : null) || null;
      if (st.item && !CID && st.item.cid) CID = st.item.cid;
      if (st.item) {
        st.current = st.item.watched_ep || 1;
        /* Секунда в полке — это место внутри серии watched_ep, и только
           её. Запоминаем пару целиком: какая серия откроется на самом
           деле, станет известно после списка серий (см. loadEpisodes). */
        st.savedEp = st.current;
        st.savedPos = st.item.position || 0;
        st.position = st.savedPos;
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
    return A.api.get('/api/search?q=' + encodeURIComponent(cut(TITLE, TITLE_MAX)) +
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
    st.aboutTags = null;
    st.enTitle = '';
    loadAbout();
    renderEpisodeMenu();
    /* Меню озвучек перерисовываем: подписи в нём на языке сайта. Сам
       вариант при этом не трогаем — человек мог выбрать его руками, и
       менять выбор из-за переключения языка было бы самоуправством.
       Заодно — меню языка и субтитров (их рисует renderMenus). */
    renderMenus();
    /* Подсказки у ‹ › и «Серия N из M» — тоже на языке сайта. */
    if (st.episodes.length) renderHead();
    /* Подпись «Пуск / Пауза» ставит скрипт, а не разметка. */
    paintPlayIcon();
  });

  function loadAbout() {
    var name = (st.item && st.item.title) || TITLE;
    if (aboutLoaded || !name) return Promise.resolve();
    aboutLoaded = true;
    /* Латинское имя и год помогают справочнику не спутать тайтл с
       однофамильцем: «Наруто» 2002 года и анонс «Наруто» — разные вещи. */
    var en = st.title_en || (st.item && st.item.title_en) || '';
    var year = (st.item && st.item.year) || YEAR || '';
    return A.api.get('/api/about?title=' + encodeURIComponent(cut(name, TITLE_MAX)) +
                     (en ? '&title_en=' + encodeURIComponent(cut(en, TITLE_MAX)) : '') +
                     (year ? '&year=' + encodeURIComponent(year) : '') +
                     '&lang=' + encodeURIComponent(A.langCode()))
      .then(function (r) {
        if (!r || !r.found || !r.about) {
          $('w-about').textContent = A.t(
            'Описание для этого тайтла не нашлось.',
            'No description found for this title.');
          return;
        }
        var box = $('w-about');
        box.textContent = r.about;
        /* Английское название из справочника — в шапку (только латиница:
           русская карточка-запаска английским названием не считается). */
        if (A.langCode() === 'en' && r.title && !/[а-яё]/i.test(r.title)) {
          st.enTitle = r.title;
          renderHead();
        }
        /* Английская страница, а английского описания нет ни у кого —
           показываем русское (лучше, чем ничего) и честно подписываем. */
        if (A.langCode() === 'en' && r.about_lang !== 'en' && /[а-яё]/i.test(r.about)) {
          box.insertBefore(A.el('span', 'aboutlang', 'Only in Russian'), box.firstChild);
        }
        /* По-русски описание есть не у всех тайтлов. Тогда сервер отдаёт
           английское (AniList, MyAnimeList) — лучше так, чем пусто, но
           честно подписываем, почему текст на другом языке. */
        if (r.about_lang === 'en' && A.langCode() !== 'en') {
          box.insertBefore(A.el('span', 'aboutlang', 'Описание на английском'), box.firstChild);
        }
        /* Год и жанры из каталога точнее, чем у источников видео:
           там они часто пустые или свалены в одну строку. */
        /* Жанры справочника — на языке сайта (fix-21: английская страница
           больше не показывает «Сёнэн, Этти»). Запоминаем: шапка,
           перерисовываясь, берёт их, а не русские жанры источника. */
        if (r.genres && r.genres.length) {
          st.aboutTags = { year: r.year || '', genres: r.genres.slice() };
          paintTags();
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
  /* Значок «18+» или «18+ · хентай» у названия в боковой колонке — по
     открытой сейчас части из «Эта история целиком» (у самого источника
     видео рейтинга нет). Кому значок не положен, A.ageBadge вернёт null. */
  function paintSideAge() {
    var h = $('w-side-title');
    var old = h.querySelector('.agebadge');
    if (old) old.remove();
    var now = (st.related || []).filter(function (p) { return p.current; })[0];
    var age = now ? A.ageBadge(now) : null;
    if (!age) return;
    h.appendChild(document.createTextNode(' '));
    h.appendChild(age);
  }

  function loadRelated() {
    var name = (st.item && st.item.title) || TITLE;
    if (!name) return Promise.resolve();
    return A.api.get('/api/related?title=' + encodeURIComponent(cut(name, TITLE_MAX_SHORT)))
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
        if (now && now.alt) st.alt = now.alt;
        if (now && now.cid && !CID) CID = now.cid;
        paintSideAge();
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
           /* У выходящей части справочник вместо «всего» отдаёт «сколько
              вышло», и по такому числу последняя вышедшая серия выглядела
              бы финальной (см. isFinalEpisode). Тогда «всего» не знаем. */
           '&total=' + encodeURIComponent(part.ongoing ? 0
                                          : (part.episodes || found.episodes_total || 0)) +
           (part.cid ? '&cid=' + encodeURIComponent(part.cid) : '') + langParam(found);
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
    scrollPartsToNow();
  }

  /* Открытую часть подкручиваем в поле зрения. У «Ван-Пис» частей
     семьдесят шесть, и без этого список всегда показывал бы 1998 год,
     где бы человек ни находился. Пока вкладка закрыта, мерить нечего —
     тогда это делает showTab, когда её откроют. */
  function scrollPartsToNow() {
    var box = $('w-fr-list');
    var now = box && box.querySelector('.part.now');
    if (!now || !box.clientHeight) return;
    var top = now.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
    box.scrollTop = Math.max(0, top - 60);
  }

  /* ================= вкладки правой колонки =================

     Серии и части истории раньше были двумя карточками друг под другом
     (плюс третья, «Дальше»), и колонка выходила длиннее плеера. Теперь
     одна карточка и две вкладки. «Вся история» появляется, только когда
     частей больше одной (renderRelated). */
  function showTab(name) {
    var fr = name === 'fr' && !$('w-fr-card').hidden;
    $('w-tab-eps').classList.toggle('on', !fr);
    $('w-tab-eps').setAttribute('aria-selected', fr ? 'false' : 'true');
    $('w-tab-eps').tabIndex = fr ? -1 : 0;
    $('w-fr-card').classList.toggle('on', fr);
    $('w-fr-card').setAttribute('aria-selected', fr ? 'true' : 'false');
    $('w-fr-card').tabIndex = fr ? 0 : -1;
    $('w-pan-eps').hidden = fr;
    $('w-pan-fr').hidden = !fr;
    if (fr) scrollPartsToNow();
    else scrollToCurrent(!epListPlaced);
  }

  (function () {
    var tabs = [$('w-tab-eps'), $('w-fr-card')];
    tabs[0].addEventListener('click', function () { showTab('eps'); });
    tabs[1].addEventListener('click', function () { showTab('fr'); });
    /* Стрелки между вкладками — как у любых вкладок. Своим обработчиком
       и с preventDefault: иначе общий обработчик клавиш перемотал бы
       видео на пять секунд. */
    tabs.forEach(function (t, i) {
      t.addEventListener('keydown', function (e) {
        if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
        e.preventDefault();
        var other = tabs[1 - i];
        if (other.hidden) return;
        showTab(other === tabs[1] ? 'fr' : 'eps');
        other.focus();
      });
    });
  })();

  function relatedNode(p) {
    var row = A.el('button', 'part');
    row.type = 'button';
    if (p.current) row.classList.add('now');

    /* Обложка части — через A.bg: битая ссылка не оставляет пустую
       рамку. Не нашлось картинки — в рамке первая буква названия.
       У сервера (/api/poster) спрашиваем только про часть, у которой
       ссылка БЫЛА, но не открылась: у «Ван-Пис» частей семьдесят шесть,
       и вопрос про каждую безобложечную был бы лишней сотней запросов. */
    var th = A.el('span', 'th');
    var fill = A.el('i');
    th.appendChild(fill);
    var poster = A.safeUrl(p.poster);
    function letter() {
      if (th.querySelector('b')) return;
      th.appendChild(A.el('b', null, (A.animeName(p) || '?').trim().charAt(0).toUpperCase()));
    }
    if (poster) {
      /* Букву ставим сразу, а не после неудачи: поиск обложки в
         справочниках может занять пару секунд, и всё это время рамка
         стояла бы пустой. Нашлась картинка — буква уходит. */
      letter();
      A.bg(fill, [poster], {
        lookup: p.title ? { title: p.title, title_en: p.title_en || '', year: p.year || '' } : null,
        onOk: function () { var b = th.querySelector('b'); if (b) th.removeChild(b); },
        onFail: letter
      });
    } else {
      letter();
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
    var age = A.ageBadge(p);
    if (age) {
      nm.appendChild(document.createTextNode(' '));
      nm.appendChild(age);
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
    /* go — значок «›» у строки части. У пункта меню «Серия» значка нет
       (go = null): раньше туда передавался сам пункт, и его название
       заменялось на «…», а после ответа — на «›». Отменил «Открыть?»
       или источник не нашёл часть — в меню вместо «Наруто: Ураганные
       хроники» оставался одинокий «›». */
    if (go) go.textContent = '…';
    A.showError($('w-fr-err'), '');

    /* Сколько серий обещает справочник — подсказка серверу: у «Ван-Пис»
       один источник выложил семь серий из тысячи с лишним, и без этого
       числа отличить огрызок от полного тайтла нечем. */
    A.api.get('/api/resolve?title=' + encodeURIComponent(cut(p.title, TITLE_MAX_SHORT)) +
              '&title_en=' + encodeURIComponent(cut(p.title_en, TITLE_MAX_SHORT)) +
              '&alt=' + encodeURIComponent((p.alt || []).join('|').slice(0, 1200)) +
              '&year=' + encodeURIComponent(p.year || '') +
              '&episodes=' + encodeURIComponent(p.episodes || 0) +
              '&lang=' + encodeURIComponent(A.animeLang()) +
              '&source=' + encodeURIComponent(usableSource()) +
              (p.hentai && hentaiAllowed() ? '&rx=1' : ''))
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
        if (go) go.textContent = '›';
      });
  }

  function episodesUrl() {
    return '/api/episodes?key=' + encodeURIComponent(KEY) +
           '&source=' + encodeURIComponent(SOURCE) +
           '&title=' + encodeURIComponent(cut(TITLE, TITLE_MAX));
  }

  /* Список серий спрашиваем сразу при открытии, вместе с полкой и списком
     источников, а не после них (fix-21: «время загрузки — намного
     меньше»). Источник в адресе почти всегда тот, что и откроется; если
     нет (переезд на другой) — ответ просто не пригодится. */
  var earlyEps = null;
  function startEpisodesEarly() {
    var url = episodesUrl();
    earlyEps = { url: url, promise: A.api.get(url) };
    earlyEps.promise.catch(function () { /* разберётся loadEpisodes */ });
  }

  function loadEpisodes() {
    var url = episodesUrl();
    var req = earlyEps && earlyEps.url === url ? earlyEps.promise : A.api.get(url);
    earlyEps = null;
    return req.then(function (rows) {
      st.episodes = rows || [];
      /* Источник нумерует серии по-своему: бывает с нуля, бывает
         с пропусками. Поэтому берём номер из его же списка,
         а не считаем сами — иначе получаем «такой серии нет». */
      var nums = ordinals();
      if (WANT_EP && nums.indexOf(WANT_EP) !== -1) st.current = WANT_EP;
      if (nums.indexOf(st.current) === -1) {
        st.current = nums.length ? nums[0] : 1;
      }
      /* Секунда из полки принадлежит той серии, на которой остановились.
         Раньше она доставалась любой открывшейся: пришли по ссылке
         «&ep=3», а в полке «серия 1, 20:00» — и третья начиналась
         с двадцатой минуты. Если же там стояло почти до конца, третья
         тут же отмечалась просмотренной, хотя её не включали. */
      st.position = (st.current === st.savedEp) ? st.savedPos : 0;
      renderEpisodes();
      renderHead();
    });
  }

  /* Обложка тайтла — для заставки плеера и для карточки «Дальше».

     Раньше она ставилась одной строкой в style.backgroundImage. Битая
     ссылка (у источников такое бывает) оставляла вместо картинки пустое
     место: в плеере — чёрный прямоугольник вместо цветной заставки, в
     карточке «Дальше» — тёмную коробку рядом с «Серия 2».

     Теперь через A.bg: он сперва пробует ссылки по очереди, потом
     спрашивает сервер (/api/poster) по названию. Не вышло ничего —
     заставка остаётся цветной (это её собственный фон из стилей), а в
     карточке «Дальше» крупно виден номер серии.

     Зовётся из renderHead, то есть на каждую смену серии, — поэтому
     помним, с какими ссылками уже ходили, и не дёргаем картинку зря. */
  var artFor = null;

  function paintArt() {
    var urls = [st.item && st.item.poster, POSTER];
    var name = (st.item && st.item.title) || TITLE;
    var key = urls.join('|') + '|' + name;
    if (key === artFor) return;
    artFor = key;
    var lookup = name ? {
      title: name,
      title_en: st.title_en || (st.item && st.item.title_en) || '',
      year: (st.item && st.item.year) || YEAR || ''
    } : null;
    var art = $('bgart');
    A.bg(art, urls, {
      lookup: lookup,
      onOk: function () {
        art.style.backgroundSize = 'cover';
        art.style.backgroundPosition = 'center';
      }
    });
    var thumb = $('w-nextthumb');
    A.bg(thumb.querySelector('i'), urls, {
      lookup: lookup,
      onOk: function () { thumb.classList.add('art'); },
      onFail: function () {
        thumb.classList.remove('art');
        thumb.querySelector('i').style.backgroundImage = '';
      }
    });
  }

  /* Название на языке сайта (fix-21). Источники и полка знают его
     по-русски; на английской странице — английское: из справочника
     (/api/about по-английски — st.enTitle), иначе латинское имя части. */
  function headName() {
    var ru = (st.item && st.item.title) || TITLE;
    if (A.langCode() === 'en') {
      var en = st.enTitle || st.title_en || (st.item && st.item.title_en) || '';
      if (en) return en;
    }
    return ru || A.t('Без названия', 'Untitled');
  }

  /* Год и жанры под названием. Из справочника (st.aboutTags) — они на
     языке сайта; пока его нет — то, что знает источник или полка, но на
     английской странице русские слова не показываем. */
  function paintTags() {
    var tags = $('w-tags');
    if (!tags) return;
    tags.textContent = '';
    var at = st.aboutTags;
    var year = (at && at.year) || (st.item && st.item.year) || '';
    var list = at ? at.genres
                  : ((st.item && st.item.genres) || GENRES || '').split(',');
    if (year) tags.appendChild(A.el('span', 'tag', year));
    list.forEach(function (g) {
      g = String(g || '').trim();
      if (g && !(A.langCode() === 'en' && /[а-яё]/i.test(g))) tags.appendChild(A.el('span', 'tag', g));
    });
  }

  function renderHead() {
    paintArt();
    var name = headName();
    $('w-title').textContent = name;
    $('w-name').textContent = name;
    /* То же название в полосе поверх картинки: в полном экране шапки
       страницы не видно, и понять, что открыто, больше неоткуда. */
    $('tb-name').textContent = name;
    $('w-side-title').textContent = name;
    paintSideAge();
    paintLangOnly();
    /* «из скольки» — это ПОСЛЕДНИЙ НОМЕР серии, а не их количество.
       Раньше здесь стояло st.episodes.length, и на источнике, который
       нумерует серии не с единицы, выходила чепуха: RU-A отдаёт
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
    /* Оценка: по 23 минуты на серию. Меньше часа — пишем минуты: на
       последней серии раньше стояло «Осталось 0 ч». */
    var mins = left * 23;
    $('w-left').textContent = !left ? ''
      : mins < 60 ? A.t('Осталось ~', 'Left ~') + mins + A.t(' мин', ' min')
      : A.t('Осталось ~', 'Left ~') + Math.round(mins / 60) + A.t(' ч', ' h');

    /* Описание подгружается отдельно (см. loadAbout). До ответа тут
       остаётся то, что уже стоит: пустая строка или прошлый текст. */
    /* Та же пара величин, что и выше: номер серии сравнивать можно
       только с номером последней, а не с их количеством. */
    $('w-epnote').textContent = last
      ? A.t('Серия ' + st.current + ' из ' + last + '.',
            'Episode ' + st.current + ' of ' + last + '.')
      : '';

    paintTags();
    /* Плашка «продолжить с 00:44» отсюда убрана. Она стояла в одном
       ряду с жанрами — среди «2020», «Сёнэн», «Фэнтези» — и читалась
       как ещё один жанр. Секунда, на которой остановились, и так
       написана под плеером и на самой дорожке. */

    var nums = ordinals();
    var at = nums.indexOf(st.current);
    var next = (at >= 0 && at + 1 < nums.length) ? nums[at + 1] : null;
    /* На картинке карточки «Дальше» — номер следующей серии. Если
       обложка загрузится, он станет маленькой меткой в углу; если нет —
       останется крупно на цветной подложке. Пустой тёмной коробки, как
       раньше, не бывает ни в каком случае. */
    $('w-nextnum').textContent = next !== null ? String(next)
      : (nums.length ? '✓' : '—');
    /* «‹» в панели под плеером — предыдущая серия. Кнопки ‹ и › не
       прячутся, а гаснут: пропавшая кнопка сдвигала бы номер серии между
       ними, и под пальцем оказывалось бы не то. */
    var prev = (at > 0) ? nums[at - 1] : null;
    $('w-prev').disabled = prev === null;
    $('w-prev').onclick = prev === null ? null : function (e) { e.preventDefault(); pick(prev); };
    $('w-next').hidden = false;
    paintNavTitles(prev, next);
    if (!nums.length) {
      /* Серий нет вовсе — значит и «это последняя» писать неправильно:
         последняя из чего? Раньше в этом случае показывалось именно оно. */
      $('w-next').disabled = true;
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
      $('w-next').disabled = false;
    } else {
      /* Обработчики надо снимать, а не только гасить кнопку: иначе на
         последней серии карточка «дальше» уносила на серию из прошлого
         показа. */
      $('w-next').disabled = true;
      $('w-next').onclick = null;
      $('w-nextcard').onclick = function (e) { e.preventDefault(); };
      $('w-nexttitle').textContent = A.t('Это последняя серия', 'That was the last one');
      $('w-nextsub').textContent = '';
    }
  }

  /* Подсказки у ‹ и ›: какая серия откроется. */
  function paintNavTitles(prev, next) {
    var p = prev === null || prev === undefined ? A.t('Предыдущей серии нет', 'No previous episode')
      : A.t('Серия ', 'Episode ') + prev;
    var n = next === null || next === undefined ? A.t('Следующей серии нет', 'No next episode')
      : A.t('Серия ', 'Episode ') + next;
    $('w-prev').title = p;
    $('w-prev').setAttribute('aria-label', A.t('Предыдущая серия', 'Previous episode'));
    $('w-next').title = n;
    $('w-next').setAttribute('aria-label', A.t('Следующая серия', 'Next episode'));
  }

  /* Меню «Серия» в панели под картинкой.

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
            openRelated(p, b, null);
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
    scrollToCurrent(!epListPlaced);
    renderEpisodeMenu();
  }

  /* Подкручиваем список к текущей серии, иначе при сотне серий она
     оказывается где-то далеко внизу и кажется, что список не работает.

     Раньше считалось от now.offsetTop — а это отступ не от начала
     списка, а от ближайшего «позиционированного» предка. Им оказывалась
     вся правая колонка (.side липкая, а значит, позиционированная), и к
     отступу прибавлялись карточка «Дальше» и шапка блока. На первой
     серии «Наруто» список уезжал на четыре строки вниз и начинался
     с пятой. Теперь меряем от самого списка, по экранным координатам.

     Первый раз ставим текущую серию наверх, а над ней оставляем
     предыдущую — видно, откуда пришёл. Дальше (выбрали другую серию)
     двигаем, только если текущая ушла из виду: прыгающий под рукой
     список, по которому только что щёлкнули, раздражает. */
  var epListPlaced = false;

  function scrollToCurrent(first) {
    var box = $('w-eplist');
    var now = box && box.querySelector('.eprow.now');
    if (!now) return;
    if (box.scrollHeight <= box.clientHeight + 1) { epListPlaced = true; return; }
    var top = box.getBoundingClientRect().top;
    var pad = parseFloat(getComputedStyle(box).paddingTop) || 0;
    var rowTop = now.getBoundingClientRect().top - top + box.scrollTop;
    var rowBottom = rowTop + now.offsetHeight;
    if (!first && rowTop - pad >= box.scrollTop &&
        rowBottom <= box.scrollTop + box.clientHeight) return;
    var prev = now.previousElementSibling;
    var from = prev ? prev.getBoundingClientRect().top - top + box.scrollTop : rowTop;
    box.scrollTop = Math.max(0, from - pad);
    epListPlaced = true;
  }

  /* Шрифт Nunito догружается позже списка, и строки после него чуть
     меняют высоту. Поправляем положение ещё раз, когда он приехал, — но
     только если человек сам ещё не прокручивал список. */
  var epListTouched = false;
  (function () {
    var box = $('w-eplist');
    if (!box) return;
    ['wheel', 'touchstart', 'keydown', 'pointerdown'].forEach(function (ev) {
      box.addEventListener(ev, function () { epListTouched = true; }, { passive: true });
    });
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function () {
        if (epListPlaced && !epListTouched) scrollToCurrent(true);
      });
    }
  })();

  function pick(n) {
    /* Смотрели и выбрали другую серию (кнопкой «Следующая», в списке, в
       меню) — значит, хотят её смотреть. Раньше выбор останавливал
       просмотр, и новую серию приходилось включать вторым нажатием на ▶. */
    var resume = started && !userPaused && !video.paused;
    st.current = n;
    st.position = 0;
    st.duration = 0;
    /* Ссылки прошлой серии выбрасываем сразу, не дожидаясь ответа за
       новыми. Раньше они оставались лежать, и ▶, нажатый в эту секунду
       (или автопереход к следующей серии), включал ПРОШЛУЮ серию под
       подписью «Серия 2» — а потом её секунда сохранялась как секунда
       второй. */
    st.videos = [];
    st.video = 0;
    st.videosFor = 0;
    st.badDubs = [];
    st.deadDubs = [];
    /* Субтитры прошлой серии к этой не подходят */
    st.subs = [];
    st.hlsSubs = [];
    mountSubs();
    started = false;
    markedThisEpisode = false;
    stopPlayer();
    paintBar($('pos'), 0);
    paintKnob(0);
    paintBuffered();
    $('w-cur').textContent = '00:00';
    /* Длина прошлой серии оставалась висеть справа от времени, пока
       новая не начнёт играть: «00:00 / 23:40» у серии, которую ещё даже
       не открыли. */
    $('w-total').textContent = '--:--';
    renderEpisodes();
    renderHead();
    loadVideos();
    save(false);
    if (resume) play();
  }

  /* ------------------------------------------------------------------ */
  /* Плашка над плеером.

     Нужна, потому что «нет видео» бывает по разным причинам, и человеку
     важно понимать, по какой именно: ждать озвучку, переключить источник
     или искать другой тайтл. Раньше на всё это была одна красная строка
     внутри плеера — и та не всегда появлялась. */
  function notice(title, sub, offerSource, offerRu) {
    var box = $('w-notice');
    if (!box) return;
    if (!title) { box.hidden = true; return; }
    if ($('w-notice-ru')) $('w-notice-ru').hidden = !offerRu;
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
     восемь строк одинаковые. Выбор из восьми «плееров» это не выбор.
     Причина была в одной букве: код спрашивал у источника поле `name`,
     а название озвучки лежит в поле `title`. Теперь в списке написано,
     чья это работа: RU-A, Yozh, KotoDub, Lis Project, Luna.

     Ссылки грузятся только для той озвучки, которую смотрят. Названия
     всех остальных известны сразу — они приезжают вместе со списком
     плееров, — а вот адреса видео приходится спрашивать у видеохостинга,
     и каждый такой вопрос это несколько секунд. У «Магической битвы» на
     RU-B тридцать семь озвучек: спросить все значит заставить
     человека ждать минуту перед пустым плеером. */
  /* Номер последнего запроса за видео и сам этот запрос, пока он идёт.

     Ответы приходят не по порядку. Щёлкнули вторую серию, сразу третью —
     а хостинг второй ответил позже: раньше её ссылки молча ложились
     поверх ссылок третьей, и ▶ включал вторую. Теперь у каждого запроса
     свой номер, и ответ, который уже не последний, просто выбрасывается.

     videosPending нужен ▶: пока ссылки новой серии в пути, включать
     нечего — надо дождаться, а не хватать то, что лежит с прошлой. */
  var videoSeq = 0;
  var videosPending = null;

  /* Озвучка не ответила — помечаем её в меню, а не прячем всё меню.
     Остальные озвучки от этого не сломались, и выбрать другую человек
     должен иметь возможность, не перезагружая страницу. */
  function markBadDub(name) {
    if (name && st.badDubs.indexOf(name) < 0) st.badDubs.push(name);
  }

  function dubNotice(asked, got) {
    notice(A.t('Озвучка «' + asked + '» сейчас не отвечает',
               'The "' + asked + '" dub is not responding'),
           got
             ? A.t('Включили «' + got + '». Другую можно выбрать в меню «Озвучка».',
                   'Switched to "' + got + '". Pick another one in the Dub menu.')
             : A.t('Остальные озвучки на месте — выберите другую в меню «Озвучка».',
                   'The other dubs are still there — pick another one in the Dub menu.'));
  }

  /* keepCurrent: озвучку переключают посреди серии. Если просимая не
     ответила, а сервер подставил вместо неё какую-то третью, менять
     то, что уже идёт, на неё не надо — об этом не просили. */
  function loadVideos(dub, keepCurrent) {
    var seq = ++videoSeq;
    var ep = st.current;
    var asked = cut(dub, DUB_MAX);
    notice('');
    $('v-q').textContent = '…';
    $('v-d').textContent = '…';
    var url = '/api/videos?key=' + encodeURIComponent(KEY) +
              '&ordinal=' + encodeURIComponent(ep) +
              '&source=' + encodeURIComponent(SOURCE) +
              '&title=' + encodeURIComponent(cut(TITLE, TITLE_MAX)) +
              '&dub=' + encodeURIComponent(asked) +
              /* Язык решает, что открыть первым: по-английски —
                 оригинальную дорожку с текстом, а не дубляж. */
              /* Язык АНИМЕ: по нему решается, что ставить первым —
                 озвучку или субтитры. Не язык меню сайта. */
              '&lang=' + encodeURIComponent(A.animeLang());
    var p = A.api.get(url).then(function (r) {
      /* Пока ответ шёл, попросили другое — этот уже никому не нужен */
      if (seq !== videoSeq || ep !== st.current) return false;
      var chosen = (r && r.chosen) || '';
      /* Сервер отдаёт ту озвучку, что на самом деле ответила. Не совпала
         с просимой — значит, просимая молчит. */
      var missed = !!asked && chosen !== asked;
      st.dubs = (r && r.dubs) || [];
      if (missed) markBadDub(asked);
      if (missed && keepCurrent && st.videos.length && st.videosFor === ep) {
        renderMenus();
        dubNotice(asked, '');
        return false;
      }
      st.badDubs = st.badDubs.filter(function (n) { return n !== chosen; });
      st.dub = chosen;
      st.videos = (r && r.videos) || [];
      st.video = 0;
      st.videosFor = ep;
      /* Субтитры отдельными файлами — к этой озвучке (см. mountSubs). */
      st.subs = (r && r.subs) || [];
      st.hlsSubs = [];
      renderMenus();
      mountSubs();
      /* Сразу, не дожидаясь ▶, проверяем, какие качества вообще
         открываются, — и прячем те, что нет (см. probeQualities). */
      probeQualities();
      if (missed && st.videos.length) {
        dubNotice(asked, chosen);
      } else if (!st.videos.length) {
        /* Серия в списке есть, а видео у неё нет — так бывает у только
           что вышедших серий: озвучка ещё не готова. */
        notice(A.t('Эту серию ещё не озвучили',
                   'This episode has no dub yet'),
               A.t('«' + sourceLabel(SOURCE) + '» её показывает, но видео пока не отдаёт. ' +
                   'У других источников свои озвучки — возможно, там она уже есть.',
                   '"' + sourceLabel(SOURCE) + '" lists it but has no video yet. ' +
                   'Other sources have their own dubs — it may already be there.'),
               true);
      } else {
        /* Просили английский дубляж или английский текст, а у серии его
           нет — сказать сразу, а не через минуту по звуку. */
        langMismatch();
      }
      return true;
    }).catch(function (err) {
      if (seq !== videoSeq || ep !== st.current) return false;
      /* Не ответила одна выбранная озвучка, а список озвучек у нас уже
         есть. Раньше здесь обнулялось всё: меню «Озвучка» пропадало,
         появлялась плашка «на источнике этой серии нет» — неправда,
         серия там есть, — а прежнее видео продолжало играть. Выбрать
         другую озвучку можно было только перезагрузив страницу. */
      if (asked && st.dubs.length) {
        markBadDub(asked);
        renderMenus();
        dubNotice(asked, '');
        return false;
      }
      st.dubs = [];
      st.dub = '';
      st.videos = [];
      st.subs = [];
      st.hlsSubs = [];
      $('v-q').textContent = '—';
      renderMenus();
      mountSubs();
      $('v-d').textContent = A.t('нет', 'none');
      /* 412 — источник не того языка: язык аниме английский, а тайтл
         открыт с русского источника. Сервер такое не отдаёт (раньше здесь
         начинала играть русская озвучка) — ищем тайтл у английских. */
      if (err.status === 412) {
        if (canSave() && started) save(false);
        stopPlayer();
        started = false;
        relocate({ otherLang: true, label: sourceLabel(SOURCE) });
        return false;
      }
      /* Английского варианта у этой серии нет ни в каком виде. */
      if (err.status === 404 && A.animeSrcLang() === 'en') {
        noEnglish();
        return false;
      }
      /* 502 от источника — это «у него нет», а не «у нас сломалось».
         Отдельная плашка вместо красной строки: она объясняет, что делать. */
      if (err.status === 502 || err.status === 404) {
        notice(A.t('На «' + sourceLabel(SOURCE) + '» этой серии нет',
                   'Not on "' + sourceLabel(SOURCE) + '"'),
               A.t('Он не отдал ни одного плеера. У каждого источника свои ' +
                   'озвучки — посмотрим, у кого эта серия есть.',
                   'It returned no player at all. Each source has its own dubs — ' +
                   'let us see who does have this episode.'),
               true);
      } else {
        fail(err.message);
      }
      return false;
    }).then(function (ok) {
      if (seq === videoSeq) videosPending = null;
      return ok;
    });
    videosPending = p;
    return p;
  }

  /* Переключение озвучки: ссылки для неё ещё не спрашивали, идём за ними.
     Секунду ожидания видно по многоточию в подписи — молча замерший
     плеер выглядел бы как поломка. */
  function pickDub(name) {
    if (name === st.dub && st.videosFor === st.current) return;
    var at = st.position;
    var wasPlaying = started && !video.paused;
    var ep = st.current;
    loadVideos(name, true).then(function (fresh) {
      /* fresh = false: ответ устарел или озвучка не ответила — то, что
         играет, не трогаем. */
      if (!fresh || !st.videos.length || st.current !== ep) return;
      /* Раньше здесь после attach() ещё звался play(), а он делает
         attach() второй раз: новая озвучка грузилась дважды подряд.
         И наоборот, на паузе attach() сам включал видео — хотя человек
         его остановил. Теперь один attach, и пуск — только если шло. */
      if (started) attach(currentUrl(), at, wasPlaying);
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
    /* Где искать, решает язык АНИМЕ (не сайта): при английском первым
       спрашивается англоязычный источник, потом RU-E. */
    A.api.get('/api/subs?title=' + encodeURIComponent(cut(name, TITLE_MAX_SHORT)) +
              '&title_en=' + encodeURIComponent(cut(st.title_en, TITLE_MAX_SHORT)) +
              '&ordinal=' + encodeURIComponent(st.current) +
              altParam() +
              '&lang=' + encodeURIComponent(A.animeLang()) +
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
                        '&ep=' + encodeURIComponent(st.current) + cidParam();
      })
      .catch(function (err) { fail(err.message); })
      .finally(function () { subsSearching = false; });
  }

  /* Как назвать вариант в меню и на кнопке «Озвучка».

     У англоязычного источника варианты называются по-английски и
     одинаково для всех тайтлов: «English dub», «Subtitles (English)».
     Человеку важно не это, а что он услышит: «Озвучка EN» — английский
     дубляж, «Субтитры EN» — японская речь и английский текст. Имена
     русских студий (KotoDub, RU-A) остаются как есть — это и есть
     ответ на вопрос «чья озвучка». */
  var EN_DUB_NAME = /^\s*(?:english(?:\s+dub)?|eng(?:\s+dub)?|английский(?:\s+дубляж)?)\s*$/i;

  function dubLabel(d) {
    var name = (d && d.name) || '—';
    if (!d) return name;
    if (d.sub && d.lang === 'en' && /^subtitles\b/i.test(name)) {
      return A.t('Субтитры EN', 'Subtitles EN');
    }
    if (!d.sub && d.audio === 'en' && EN_DUB_NAME.test(name)) {
      return A.t('Озвучка EN', 'English dub');
    }
    return name;
  }

  function renderMenus() {
    /* Есть ли смысл в самом меню.

       Нет озвучек — нет и выбора: блок прячется целиком. Одна озвучка и
       больше ничего — блок остаётся, но перестаёт быть меню: её название
       и так написано рядом, а список из одной строки собирает нажатия
       впустую.

       «И больше ничего» здесь не для красоты. Когда субтитров среди
       вариантов нет, в меню добавляется строка «Оригинал с субтитрами»,
       и тогда открыть его надо обязательно — иначе на RU-A, где
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
      var b = A.el('button', d.name === st.dub ? 'on' : null, dubLabel(d));
      b.type = 'button';
      /* «Субтитры» это не другая озвучка, а другой способ смотреть:
         японская дорожка и текст поверх. Разница должна быть видна до
         нажатия, а не после — «текстом» этого не говорило. */
      if (d.sub) {
        /* Пишем и язык текста: у русских источников он русский, у
           EN-D — английский. Человеку важно знать это ДО нажатия, а не
           после первой минуты. */
        b.appendChild(A.el('span', 'sub', d.lang === 'en'
          ? A.t('яп. + англ. текст', 'jp + english text')
          : A.t('яп. + рус. текст', 'jp + russian text')));
      }
      /* Не ответила — пишем об этом прямо в строке. Нажать всё равно
         можно: хостинг мог ожить, и повторить попытку — честный выбор. */
      if (st.badDubs.indexOf(cut(d.name, DUB_MAX)) >= 0) {
        b.appendChild(A.el('span', 'sub', A.t('не отвечает', 'not responding')));
      } else if (st.deadDubs.indexOf(cut(d.name, DUB_MAX)) >= 0) {
        /* Ссылки отдала, но видео по ним не открылось ни в одном качестве */
        b.appendChild(A.el('span', 'sub', A.t('видео не открылось', 'video did not open')));
      }
      b.addEventListener('click', function () {
        dubs.classList.remove('show');
        pickDub(cut(d.name, DUB_MAX));
      });
      dubs.appendChild(b);
    });
    /* Субтитров у этого источника нет — предлагаем поискать у других.

       Без этой строки они были недостижимы: источник по умолчанию
       RU-A отдаёт одну свою озвучку и субтитров не даёт никогда,
       а рядом, у RU-B, они лежат почти у всего. Человек, открывший
       любой тайтл, субтитров не видел в принципе — и не знал, что они
       где-то есть. */
    if (offerSubs) {
      var find = A.el('button', 'findsubs',
                      A.t('Оригинал с субтитрами', 'Original with subtitles'));
      find.type = 'button';
      find.appendChild(A.el('span', 'sub', A.t('поискать у других', 'look elsewhere')));
      find.addEventListener('click', function () {
        dubs.classList.remove('show');
        findSubs();
      });
      dubs.appendChild(find);
    }

    var cur = st.dubs.filter(function (d) { return d.name === st.dub; })[0];
    $('v-d').textContent = cur ? dubLabel(cur) : (st.dub || '—');
    renderCc();
    renderLangMenu();

    var qs = $('m-q');
    qs.textContent = '';
    qs.appendChild(A.el('div', 'mh', A.t('Качество', 'Quality')));
    st.videos.forEach(function (v, i) {
      var b = A.el('button', i === st.video ? 'on' : null, (v.quality || '?') + 'p');
      b.type = 'button';
      /* Качество ищем по адресу в момент нажатия, а не по номеру из
         замыкания: пока меню открыто, проверка могла выкинуть из списка
         неработающее качество, номера сдвинулись — и нажатие «480p»
         включило бы 360p. */
      var url = v.url;
      b.addEventListener('click', function () {
        qs.classList.remove('show');
        var at = qualityIndex(A.safeUrl(url));
        if (at < 0 || at === st.video) return;
        st.video = at;
        renderMenus();
        if (started) attach(currentUrl(), st.position, !userPaused);
      });
      qs.appendChild(b);
    });
    $('v-q').textContent = st.videos.length
      ? ((st.videos[st.video] || {}).quality || '?') + 'p' : '—';

    renderSourceMenu();
    paintSource();
  }

  /* ================= язык: кнопка с глобусом =================

     В одном маленьком меню — обе настройки языка, и обе действуют сразу.

     Язык сайта — только надписи. Его меняют те же кнопки, что в кабинете
     (класс lang и data-set-lang): нажатие ловит app.js, применяет язык и
     сохраняет его в аккаунт.

     Язык аниме — что звучит и чем подписано: русские озвучки, английский
     дубляж или японская речь с английским текстом. После него страница
     ищет эту же серию там, где такой язык есть (switchAnimeLang). */
  var ANIME_OPTS = [
    { code: 'ru', ru: 'Русский', en: 'Russian',
      sru: 'русские озвучки', sen: 'russian dubs' },
    { code: 'endub', ru: 'English — озвучка', en: 'English — dub',
      sru: 'английский дубляж', sen: 'english dub' },
    { code: 'ensub', ru: 'English — субтитры', en: 'English — subtitles',
      sru: 'японская речь, англ. текст', sen: 'japanese audio, english text' }
  ];

  function langShort() {
    var m = A.animeLang();
    return m === 'ru' ? 'RU' : (m === 'endub' ? 'EN' : 'EN·SUB');
  }

  function renderLangMenu() {
    var box = $('m-l');
    if (!box) return;
    box.textContent = '';
    box.appendChild(A.el('div', 'mh', A.t('Язык сайта', 'Site language')));
    [['ru', 'Русский'], ['en', 'English']].forEach(function (x) {
      var b = A.el('button', 'lang' + (A.langCode() === x[0] ? ' on' : ''), x[1]);
      b.type = 'button';
      b.setAttribute('data-set-lang', x[0]);
      box.appendChild(b);
    });
    box.appendChild(A.el('div', 'mgroup', A.t('Язык аниме', 'Anime language')));
    var now = A.animeLang();
    ANIME_OPTS.forEach(function (o) {
      var b = A.el('button', o.code === now ? 'on' : null, A.t(o.ru, o.en));
      b.type = 'button';
      b.setAttribute('data-alang', o.code);
      b.appendChild(A.el('span', 'sub', A.t(o.sru, o.sen)));
      b.addEventListener('click', function () {
        box.classList.remove('show');
        switchAnimeLang(o.code);
      });
      box.appendChild(b);
    });
    box.appendChild(A.el('div', 'mnote', A.t(
      'Язык аниме сохраняется — как в настройках.',
      'The anime language is saved — same as in settings.')));
    $('v-l').textContent = langShort();
    $('lang-btn').title = A.t('Язык сайта и аниме', 'Site and anime language');
  }

  /* Сменили язык аниме прямо здесь.

     Сначала спрашиваем список источников под новый язык. Открытый
     источник в нём есть (EN-D при смене «English — озвучка» на
     «English — субтитры») — остаёмся и просто включаем нужный вариант
     этой же серии. Нет (русский источник, а выбрали английский) — тот же
     переезд, что и для тайтла, открытого с источника другого языка
     (relocate): ищем тайтл там, где этот язык есть, и открываем ту же
     серию. Секунду, на которой стояли, перед этим сохраняем. */
  function switchAnimeLang(code) {
    if (code === A.animeLang() && !A.pageAnimeLang()) return;
    A.setAnimeLang(code).catch(function (err) {
      notice(A.t('Язык аниме не сохранился', 'The anime language was not saved'),
             (err && err.message) || '');
    });
    st.ccWant = '';
    renderLangMenu();
    paintLangOnly();
    $('v-s').textContent = '…';
    A.api.get('/api/sources?lang=' + encodeURIComponent(A.animeLang()))
      .then(function (rows) {
        sources = rows || [];
        where = { state: 'idle', here: [], checked: [], silent: [] };
        srcOrder = [];
        paintSource();
        renderSourceMenu();
        if (!sources.length || isAvailable(SOURCE)) {
          retrack();
          scheduleWhere();
          return;
        }
        var label = sourceLabel(SOURCE);
        if (canSave() && started) save(false);
        stopPlayer();
        started = false;
        relocate({ otherLang: true, label: label });
      })
      .catch(function (err) {
        paintSource();
        fail(err.message);
      });
  }

  /* Тот же источник, другой выбор: включаем вариант, который просили, —
     английский дубляж или текст поверх оригинала. Нет такого у этой
     серии — говорим об этом (langMismatch) и оставляем то, что есть. */
  function retrack() {
    var want = A.animeTrack();
    var en = A.animeSrcLang() === 'en';
    var hit = st.dubs.filter(function (d) {
      if (want === 'sub') return d.sub && (!en || d.lang === 'en' || hasSoft('en'));
      return !d.sub && (!en || d.audio === 'en');
    })[0];
    if (hit && hit.name !== st.dub) {
      pickDub(cut(hit.name, DUB_MAX));
      return;
    }
    pickCc();
    langMismatch();
  }

  /* Просили английский, а у серии его нет в нужном виде. Молча включить
     другое — значит человек поймёт это через минуту, по звуку. */
  function langMismatch() {
    if (A.animeSrcLang() !== 'en' || !st.dubs.length || noticeShown()) return;
    var cur = st.dubs.filter(function (d) { return d.name === st.dub; })[0];
    if (!cur) return;
    if (A.animeTrack() === 'dub' && cur.audio !== 'en') {
      notice(A.t('Английской озвучки у этой серии нет', 'This episode has no English dub'),
             A.t('Включили японскую речь с английскими субтитрами. Озвучка может появиться позже.',
                 'Playing the Japanese audio with English subtitles instead. A dub may appear later.'));
    } else if (A.animeTrack() === 'sub' && cur.sub && cur.lang !== 'en' && !hasSoft('en')) {
      notice(A.t('Английских субтитров здесь нет', 'No English subtitles here'),
             A.t('У «' + sourceLabel(SOURCE) + '» к этой серии текст только на другом языке. ' +
                 'Попробуйте другой английский источник.',
                 '"' + sourceLabel(SOURCE) + '" only has subtitles in another language for this ' +
                 'episode. Try another English source.'),
             true);
    }
  }

  /* ================= субтитры отдельными файлами =================

     Текст бывает вшит в картинку (EN-D, русские сайты) — тогда
     переключать нечего, и кнопки CC нет. А бывает отдельными файлами:
     RU-E в «Оригинале (+субтитры)» отдаёт их по языкам, и среди них
     бывает английский. Такие файлы сервер переделывает в WebVTT и
     отдаёт со своего адреса (/api/subtitle/…): чужой сервер не пустил бы
     файл в плеер без заголовка CORS. Здесь они становятся дорожками
     <track>, и браузер рисует текст сам — и в полном экране тоже.

     Ещё один вид — дорожки текста внутри самого потока (m3u8 с
     субтитрами): их находит hls.js, они тоже попадают в это меню. */
  function hasSoft(lang) {
    return st.subs.some(function (x) { return x.lang === lang; }) ||
           st.hlsSubs.some(function (x) { return x.lang === lang; });
  }

  function ccTracks() {
    var out = st.subs.map(function (x) {
      return { id: 'x:' + x.id, label: x.label, lang: x.lang || '' };
    });
    st.hlsSubs.forEach(function (x) {
      out.push({ id: 'h:' + x.i, label: x.label, lang: x.lang || '' });
    });
    return out;
  }

  /* Какую дорожку включить, если человек сам не выбирал.
     Выбрал сам — тот же язык и в следующей серии. Не выбирал — текст
     включается только к варианту «с субтитрами» (у озвучки он мешал бы)
     и на языке аниме: английский при «English — субтитры», иначе —
     русский. */
  function pickCc() {
    var list = ccTracks();
    var cur = st.dubs.filter(function (d) { return d.name === st.dub; })[0];
    var want = st.ccWant;
    if (!want) {
      if (!cur || !cur.sub) want = 'off';
      else want = A.animeSrcLang() === 'en' ? 'en' : 'ru';
    }
    var hit = want === 'off' ? null : (list.filter(function (t) { return t.lang === want; })[0] ||
              (st.ccWant ? null : list[0]));
    setCc(hit ? hit.id : '');
  }

  /* Включить дорожку (id) или выключить все (''). */
  function setCc(id) {
    st.cc = id || '';
    var own = video.querySelectorAll('track[data-own]');
    Array.prototype.forEach.call(own, function (t) {
      if (t.track) t.track.mode = (st.cc === 'x:' + t.getAttribute('data-id')) ? 'showing' : 'disabled';
    });
    if (hls && st.hlsSubs.length) {
      var h = st.cc.indexOf('h:') === 0 ? parseInt(st.cc.slice(2), 10) : -1;
      try {
        hls.subtitleDisplay = h >= 0;
        hls.subtitleTrack = h;
      } catch (e) { /* старый hls.js — переживём */ }
    }
    renderCc();
  }

  /* Дорожки-файлы в <video>: старые прочь, новые на место. Зовётся,
     когда пришли ссылки новой серии или озвучки. */
  function mountSubs() {
    Array.prototype.forEach.call(video.querySelectorAll('track[data-own]'), function (t) {
      t.parentNode.removeChild(t);
    });
    st.subs.forEach(function (x) {
      var t = document.createElement('track');
      t.kind = 'subtitles';
      t.label = x.label || x.lang || '?';
      if (x.lang) t.srclang = x.lang;
      t.src = A.safeUrl(x.src) || '';
      t.setAttribute('data-own', '1');
      t.setAttribute('data-id', x.id);
      video.appendChild(t);
    });
    pickCc();
  }

  function renderCc() {
    var list = ccTracks();
    var sel = $('sel-cc');
    if (!sel) return;
    sel.hidden = !list.length;
    var btn = $('cc-btn');
    var on = list.filter(function (t) { return t.id === st.cc; })[0];
    btn.classList.toggle('on', !!on);
    btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    btn.title = A.t('Субтитры (C)', 'Subtitles (C)');
    $('v-c').textContent = on ? (on.lang || on.label).toUpperCase().slice(0, 3) : A.t('выкл', 'off');
    var box = $('m-c');
    box.textContent = '';
    box.appendChild(A.el('div', 'mh', A.t('Субтитры', 'Subtitles')));
    var off = A.el('button', on ? null : 'on', A.t('Выключить', 'Off'));
    off.type = 'button';
    off.addEventListener('click', function () {
      box.classList.remove('show');
      st.ccWant = 'off';
      setCc('');
    });
    box.appendChild(off);
    list.forEach(function (t) {
      var b = A.el('button', t.id === st.cc ? 'on' : null, t.label || t.lang || '?');
      b.type = 'button';
      if (t.lang) b.appendChild(A.el('span', 'sub', t.lang.toUpperCase()));
      b.addEventListener('click', function () {
        box.classList.remove('show');
        st.ccWant = t.lang || 'off';
        setCc(t.id);
      });
      box.appendChild(b);
    });
  }

  /* C (С в русской раскладке) — включить или выключить текст. */
  function toggleCc() {
    var list = ccTracks();
    if (!list.length) return;
    if (st.cc) {
      st.ccWant = 'off';
      setCc('');
      return;
    }
    var want = A.animeSrcLang() === 'en' ? 'en' : 'ru';
    var hit = list.filter(function (t) { return t.lang === want; })[0] || list[0];
    st.ccWant = hit.lang || '';
    setCc(hit.id);
  }

  /* ================= меню «Источник» =================

     Раньше здесь стояли подряд все восемь, потом их стали делить на
     группы с заголовками («Тут оно есть», «Не проверяли», «Искали, не
     нашли»). Группы появлялись, только когда приходил ответ сервера, —
     и если меню уже было открыто, список на глазах перестраивался:
     строки менялись местами, появлялись заголовки, меню вырастало.

     Теперь у каждой строки своё место и своя пометка справа:
     «смотрите», «есть», «нет», «проверяю…», «не ответил». Ответ сервера
     меняет только пометки, на тех же местах; строки не переезжают и
     меню не меняет высоту. Открытый сейчас источник всегда первый.

     Порядок «сначала те, где есть» включается, только когда меню
     открывают с уже готовым ответом, — тогда перестраивать на глазах
     нечего. */
  function srcState(id) {
    if (id === SOURCE) return 'now';
    if (where.state === 'idle' || where.state === 'pending') return 'wait';
    if (where.state === 'failed') return 'unknown';
    if (where.here.indexOf(id) >= 0) return 'yes';
    if (where.silent.indexOf(id) >= 0) return 'silent';
    if (where.checked.indexOf(id) >= 0) return 'no';
    return 'unknown';
  }

  var SRC_STATE_TEXT = {
    now:     ['смотрите', 'watching'],
    wait:    ['проверяю…', 'checking…'],
    yes:     ['есть', 'has it'],
    no:      ['тут этого нет', 'not here'],
    silent:  ['не ответил', 'no reply'],
    unknown: ['не проверяли', 'not checked']
  };

  /* Источник только для хентая (rx_only) в меню — только на
     хентай-тайтле (или если серия уже открыта с него). На обычном
     тайтле искать там нечего. */
  function inMenu(x) { return !x.rx_only || x.id === SOURCE || isRx(); }

  function srcOrderNow() {
    var ids = sources.filter(inMenu).map(function (x) { return x.id; });
    var menu = $('m-s');
    var open = menu && menu.classList.contains('show');
    /* Меню открыто и состав тот же — порядок не трогаем. */
    if (open && srcOrder.length === ids.length &&
        ids.every(function (id) { return srcOrder.indexOf(id) >= 0; })) {
      return srcOrder;
    }
    var rank = { now: 0, yes: 1, wait: 1, silent: 2, unknown: 2, no: 3 };
    srcOrder = ids.map(function (id, i) { return { id: id, i: i, r: rank[srcState(id)] }; })
      .sort(function (x, y) { return (x.r - y.r) || (x.i - y.i); })
      .map(function (x) { return x.id; });
    return srcOrder;
  }

  function sourceRow(src) {
    var b = A.el('button', 'srcrow');
    b.type = 'button';
    b.setAttribute('data-src', src.id);
    var name = A.el('span', 'srcname');
    name.appendChild(A.el('b', null, src.label || src.id));
    name.appendChild(A.el('small', null, ''));
    b.appendChild(name);
    b.appendChild(A.el('span', 'srcst', ''));
    b.addEventListener('click', function () {
      var state = srcState(src.id);
      if (state === 'no') return;
      $('m-s').classList.remove('show');
      if (src.id === SOURCE) return;
      switchSource(src.id);
    });
    paintSourceRow(b, src, false);
    return b;
  }

  /* Пометка строки. fresh — пометка сменилась с «проверяю…» на ответ:
     она мягко проявляется, а не выскакивает. */
  function paintSourceRow(b, src, fresh) {
    var state = srcState(src.id);
    var was = b.getAttribute('data-state');
    b.setAttribute('data-state', state);
    b.classList.toggle('on', state === 'now');
    /* Источник, где тайтла точно нет, не прячется: пропавшая строка
       ничего не объясняет. Видна, но выключена — это и есть ответ. */
    b.classList.toggle('gone', state === 'no');
    b.disabled = state === 'no';
    b.querySelector('small').textContent = src.dubs === 'many'
      ? A.t('много озвучек', 'many dubs') : A.t('одна озвучка', 'one dub');
    var st_ = b.querySelector('.srcst');
    var words = SRC_STATE_TEXT[state];
    st_.textContent = A.t(words[0], words[1]);
    st_.className = 'srcst is-' + state;
    if (fresh && was === 'wait' && state !== 'wait') {
      /* Перезапуск анимации: снять класс, дать браузеру заметить, вернуть. */
      void st_.offsetWidth;
      st_.classList.add('fresh');
    }
  }

  function renderSourceMenu() {
    var ss = $('m-s');
    if (!ss) return;
    var order = srcOrderNow();
    var rows = ss.querySelectorAll('button.srcrow');
    var same = rows.length === order.length && order.length > 0 &&
      Array.prototype.every.call(rows, function (r, i) {
        return r.getAttribute('data-src') === order[i];
      });
    if (same) {
      /* Строки на месте — только обновляем пометки. */
      Array.prototype.forEach.call(rows, function (r, i) {
        var src = sources.filter(function (x) { return x.id === order[i]; })[0];
        if (src) paintSourceRow(r, src, true);
      });
      ss.querySelector('.mh').textContent = A.t('Откуда берётся видео', 'Video source');
      return;
    }
    ss.textContent = '';
    ss.appendChild(A.el('div', 'mh', A.t('Откуда берётся видео', 'Video source')));
    if (!sources.length) {
      ss.appendChild(A.el('div', 'mnote', A.t('список загружается…', 'loading…')));
      return;
    }
    order.forEach(function (id) {
      var src = sources.filter(function (x) { return x.id === id; })[0];
      if (src) ss.appendChild(sourceRow(src));
    });
  }


  /* Подпись источника для человека: «Мой источник», а не «moy_istochnik».
     Раньше в поле «Источник» под плеером стоял внутренний номер, а в
     меню рядом — нормальное название, и выглядело это как два разных
     источника. Пока список не пришёл (или такого источника в нём нет),
     показываем номер: это лучше пустоты. */
  function sourceLabel(id) {
    var hit = sources.filter(function (x) { return x.id === id; })[0];
    return (hit && hit.label) || id || '—';
  }

  function paintSource() { $('v-s').textContent = sourceLabel(SOURCE); }

  /* Есть ли источник в списке тех, что сейчас работают.

     Список зависит от режима сайта и от языка: в демо-режиме источник
     один («demo»), в обычном — настоящие сайты своего языка. Карточка в
     полке помнит источник, на котором её сохранили, и он может оказаться
     вне списка: тайтл добавили, пока сайт работал в демо-режиме, а потом
     режим сменили. Тогда сервер на всё отвечал «Неизвестный источник»,
     и страница оставалась сломанной. */
  function isAvailable(id) {
    return sources.some(function (x) { return x.id === id; });
  }

  /* Источник, который можно смело назвать серверу. Свой, если он в
     списке; иначе основной для языка сайта, если он есть; иначе первый
     из тех, что есть. */
  function usableSource() {
    if (!sources.length || isAvailable(SOURCE)) return SOURCE;
    var def = A.defaultSource();
    return isAvailable(def) ? def : sources[0].id;
  }

  /* Адрес этой же страницы для найденного тайтла — с той же серией.
     Пока список серий не пришёл (а при переезде с недоступного
     источника он не придёт вовсе), действует то же правило, что в
     loadEpisodes: серия из адреса страницы важнее серии из полки. */
  function hrefFor(found, sourceId, name, keepName) {
    var ep = st.episodes.length ? st.current : (WANT_EP || st.current);
    return '/watch?key=' + encodeURIComponent(found.key) +
           '&source=' + encodeURIComponent(sourceId) +
           '&title=' + encodeURIComponent(keepName ? name : (found.title || name)) +
           '&poster=' + encodeURIComponent(found.poster || POSTER || '') +
           '&year=' + encodeURIComponent(found.year || YEAR || '') +
           '&genres=' + encodeURIComponent(found.genres || GENRES || '') +
           '&total=' + encodeURIComponent(found.episodes_total || 0) +
           (ep ? '&ep=' + encodeURIComponent(ep) : '') + cidParam() + langParam(found);
  }

  /* Переход на другой источник: ищем этот же тайтл там.

     Раньше это был голый поиск по названию с заглавной страницы тайтла.
     Если источник знает тайтл под другим именем («Старшая школа DxD» у
     RU-D — «Демоны старшей школы»), он отвечал «такого нет», хотя
     тайтл там лежит. Теперь — тот же резолв, что и при выборе части, но
     только у выбранного источника (only=1): он пробует синонимы из
     справочника, различает сезоны и проверяет, что серии открываются. */
  function switchSource(newSource) {
    var name = (st.item && st.item.title) || TITLE;
    if (!name) return;
    $('v-s').textContent = '…';
    showLoading(true);
    var en = st.title_en || (st.item && st.item.title_en) || '';
    var year = (st.item && st.item.year) || YEAR || '';
    A.api.get('/api/resolve?title=' + encodeURIComponent(cut(name, TITLE_MAX_SHORT)) +
              '&title_en=' + encodeURIComponent(cut(en, TITLE_MAX_SHORT)) +
              altParam() +
              '&year=' + encodeURIComponent(year) +
              '&lang=' + encodeURIComponent(A.animeLang()) +
              '&source=' + encodeURIComponent(newSource) + '&only=1' + rxParam())
      .then(function (found) {
        /* Источник берём из ответа, а не из вопроса: сервер может искать
           не там, где просили (в демо-режиме он всегда ищет в «demo»).
           Со старым адресом страница открывалась снова на источнике,
           которого нет, — и снова уходила искать, по кругу. Название —
           то, под которым тайтл знает справочник: по нему ищутся
           описание и части истории. */
        var go_ = function () { location.href = hrefFor(found, found.source || newSource, name, true); };
        if (found.exact) return go_();
        showLoading(false);
        paintSource();
        return A.ask({
          title: A.t('Точного совпадения нет', 'No exact match'),
          text: A.t('У источника это лежит как «' + found.title + '». Открыть?',
                    'The source has it as "' + found.title + '". Open it?'),
          ok: A.t('Открыть', 'Open')
        }).then(function (yes) { if (yes) go_(); });
      })
      .catch(function (err) {
        showLoading(false);
        paintSource();
        fail(err.status === 404
          ? A.t('На этом источнике такого тайтла нет',
                'This source does not have that title')
          : err.message);
      });
  }

  /* Тайтл открыт на источнике, которого сейчас нет в списке.

     Ищем тот же тайтл у тех, что есть, — тем же поиском по названию, что
     и у блока «эта история целиком»: он перебирает источники и проверяет,
     что серии у найденного действительно открываются. Нашлось точно —
     уходим туда с той же серией. Похожее — спрашиваем. Ничего — говорим
     об этом прямо, вместо страницы, где ничего не работает. */
  /* Английского нет нигде: говорим прямо, без тихой подмены русским, и
     даём одну кнопку — смотреть на русском. */
  function noEnglish() {
    showLoading(false);
    notice(A.t('На английском этого пока нигде нет', 'Not available in English yet'),
           A.t('Ни один английский источник не отдал этот тайтл (или эту серию) ' +
               (A.animeLang() === 'ensub' ? 'с английскими субтитрами' : 'на английском') +
               '. Русскую озвучку сайт сам не включает — только если вы попросите.',
               'No English source has this title (or this episode) ' +
               (A.animeLang() === 'ensub' ? 'with English subtitles' : 'in English') +
               '. The site never switches to Russian on its own — only if you ask.'),
           false, true);
  }

  function relocate(why) {
    var name = (st.item && st.item.title) || TITLE;
    var was = why.label || SOURCE;
    if (why.otherLang) {
      notice(A.t('Переключаем на источник вашего языка',
                 'Switching to a source in your anime language'),
             A.t('Этот тайтл был открыт на источнике другого языка. Ищем его ' +
                 'там, где озвучка на выбранном языке аниме (кабинет → «Язык аниме»).',
                 'This title was opened on a source in another language. ' +
                 'Looking for it where the audio matches your anime language ' +
                 '(settings → “Anime language”).'));
    } else {
      notice(A.t('Источник «' + was + '» сейчас недоступен — ищу этот тайтл на других',
                 'The "' + was + '" source is not available right now — ' +
                 'looking for this title elsewhere'),
             A.t('Тайтл сохранился с источника, который сейчас не работает. ' +
                 'Это несколько секунд.',
                 'The title was saved from a source that is not running now. ' +
                 'This takes a few seconds.'));
    }
    $('w-epno').textContent = A.t('Ищем на других источниках…', 'Looking on other sources…');
    st.dubs = [];
    st.dub = '';
    st.videos = [];
    renderMenus();
    showLoading(true);

    function nothing(text, status) {
      showLoading(false);
      $('w-epno').textContent = A.t('Серии не загрузились', 'Episodes did not load');
      if (status === 404 && A.animeSrcLang() === 'en') { noEnglish(); return; }
      notice(A.t('Этот тайтл сейчас посмотреть негде',
                 'This title cannot be watched right now'), text);
    }

    if (!name) {
      nothing(A.t('У записи нет названия — искать нечего.',
                  'The entry has no title, so there is nothing to search for.'));
      return;
    }
    var en = st.title_en || (st.item && st.item.title_en) || '';
    A.api.get('/api/resolve?title=' + encodeURIComponent(cut(name, TITLE_MAX_SHORT)) +
              '&title_en=' + encodeURIComponent(cut(en, TITLE_MAX_SHORT)) +
              altParam() +
              '&lang=' + encodeURIComponent(A.animeLang()) +
              '&source=' + encodeURIComponent(usableSource()) + rxParam())
      .then(function (found) {
        /* Название — то, под которым тайтл знает справочник (как и при
           смене источника), а не имя у источника («Jujutsu Kaisen (TV)»):
           по нему ищутся описание и части истории, и с ним запись в
           списке остаётся той же карточкой. */
        var go_ = function () { location.href = hrefFor(found, found.source, name, true); };
        if (found.exact) return go_();
        showLoading(false);
        return A.ask({
          title: A.t('Точного совпадения нет', 'No exact match'),
          text: A.t('У источника это лежит как «' + found.title + '». Открыть?',
                    'The source has it as "' + found.title + '". Open it?'),
          ok: A.t('Открыть', 'Open')
        }).then(function (yes) {
          if (yes) return go_();
          nothing(A.t('Похожее нашлось, но вы его не открыли. Найти тайтл можно ' +
                      'поиском на главной.',
                      'Something similar was found, but you did not open it. ' +
                      'You can look the title up with the search on the home page.'));
        });
      })
      .catch(function (err) {
        nothing(err.status === 404
          ? A.t('Источник «' + was + '» не работает, а у остальных такого тайтла ' +
                'нет. Попробуйте поиск на главной — возможно, там он называется иначе.',
                'The "' + was + '" source is not running, and the others do not ' +
                'have this title. Try the search on the home page — it may be ' +
                'listed under another name.')
          : err.message, err.status);
      });
  }

  /* меню в панели */
  document.querySelectorAll('.selbtn').forEach(function (b) {
    b.addEventListener('click', function (e) {
      e.stopPropagation();
      var m = $(b.dataset.menu);
      var open = m.classList.contains('show');
      document.querySelectorAll('.menu').forEach(function (x) { x.classList.remove('show'); });
      if (!open && b.dataset.menu === 'm-s') {
        /* Перед показом — свежий порядок: пока меню было закрыто, мог
           прийти ответ, и строки «есть» встанут наверх уже сейчас, а не
           на глазах. */
        renderSourceMenu();
        ensureWhere();
      }
      if (!open) m.classList.add('show');
    });
  });

  /* Кнопка из плашки открывает то самое меню и сразу запускает проверку:
     человеку не нужно знать, что она называется «Источник» и живёт под
     плеером. */
  /* «Смотреть на русском»: язык аниме → ru (сохраняется, как в
     настройках), дальше — тот же переезд, что при смене языка в меню. */
  if ($('w-notice-ru')) {
    $('w-notice-ru').addEventListener('click', function (e) {
      e.stopPropagation();
      notice('');
      switchAnimeLang('ru');
    });
  }

  $('w-notice-go').addEventListener('click', function (e) {
    e.stopPropagation();
    document.querySelectorAll('.menu').forEach(function (m) { m.classList.remove('show'); });
    renderSourceMenu();
    ensureWhere();
    $('m-s').classList.add('show');
    $('m-s').scrollIntoView({block: 'nearest', behavior: 'smooth'});
  });

  /* Спрашиваем, у кого тайтл есть, — один раз на страницу.

     wide=1 просит сервер проверить и узкие источники (RU-E, RU-F
     и т. п.), а не только четыре основных: иначе половина меню так и
     висела бы «не проверяли». Сервер сам решает, укладывается ли это
     в лимит обращений к источникам; не укладывается — проверит только
     основные. Ответ у него кэшируется, так что повторно это дёшево.

     Ответ меню не открывает и строки не переставляет — только меняет
     пометки (см. renderSourceMenu). Раньше здесь же меню принудительно
     открывалось: закрыл его, не дождавшись, — через секунду оно
     выскакивало само. */
  function ensureWhere() {
    if (where.state === 'pending' || where.state === 'done') return;
    var name = (st.item && st.item.title) || TITLE;
    if (!name) {
      /* Спрашивать не по чему — честное «не проверяли», а не вечное
         «проверяю…». */
      where.state = 'failed';
      renderSourceMenu();
      return;
    }
    where.state = 'pending';
    renderSourceMenu();
    var en = st.title_en || (st.item && st.item.title_en) || '';
    A.api.get('/api/where?title=' + encodeURIComponent(cut(name, TITLE_MAX_SHORT)) +
              (en ? '&title_en=' + encodeURIComponent(cut(en, TITLE_MAX_SHORT)) : '') +
              altParam() +
              '&lang=' + encodeURIComponent(A.animeLang()) + '&wide=1')
      .then(function (answer) {
        answer = answer || {};
        where.here = answer.here || [];
        where.checked = answer.checked || [];
        /* silent — источники, которые не ответили вовсе (поле добавлено
           в /api/where вместе с wide=1). */
        where.silent = answer.silent || [];
        where.state = 'done';
      })
      .catch(function () {
        /* Не вышло (например, упёрлись в лимит) — строки получают
           «не проверяли», а при следующем открытии меню спросим снова. */
        where.state = 'failed';
      })
      .then(function () { renderSourceMenu(); });
  }

  /* Фоновая проверка «у кого есть»: когда страница уже загрузила серии
     и видео и браузеру больше нечем заняться. Не раньше — она идёт к
     чужим сайтам и не должна отнимать время у того, ради чего человек
     пришёл. */
  function scheduleWhere() {
    if (where.state !== 'idle') return;
    if (window.requestIdleCallback) {
      window.requestIdleCallback(ensureWhere, { timeout: 2500 });
    } else {
      setTimeout(ensureWhere, 800);
    }
  }
  document.addEventListener('click', function () {
    document.querySelectorAll('.menu').forEach(function (m) { m.classList.remove('show'); });
  });

  /* ------------------------------------------------------------------ */
  /* Сохранение секунды. Гостю сохранять некуда — сервер его не пустит,  */
  /* поэтому даже не пробуем и не пугаем ошибкой.                       */
  /* ------------------------------------------------------------------ */
  /* Демо-аккаунту — тоже: сервер отклоняет любую запись (A.canEdit). */
  function canSave() { return A.canEdit(); }

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
      key: KEY, source: SOURCE, cid: CID || (st.item && st.item.cid) || '',
      title: (st.item && st.item.title) || TITLE,
      title_en: st.title_en || (st.item && st.item.title_en) || '',
      poster: (st.item && st.item.poster) || POSTER,
      year: (st.item && st.item.year) || YEAR,
      genres: (st.item && st.item.genres) || GENRES,
      total_eps: total,
      watched_ep: st.current,
      position: Math.round(st.position),
      status: watched && last && st.current >= last && isFinalEpisode(last)
        ? 'done' : 'watching'
    };
  }

  /* Последняя серия в списке — это последняя ВЫШЕДШАЯ, а не последняя
     вообще.

     Раньше «досмотрел последнюю из списка» сразу значило «закончил»: у
     сериала, который ещё выходит (5 серий из 12), пятая переносила его в
     «Закончил смотреть». А о новых сериях сайт сообщает только про то,
     что в «Смотрю», — и про шестую человек так и не узнавал.

     Теперь «закончил» ставится, только когда точно известно, что серий
     больше не будет: справочник не называет тайтл выходящим и знает,
     сколько их всего, и последняя в списке до этого числа дотянула.
     Справочник молчит — берём число из адреса страницы (его кладёт
     карточка). Не знаем ни того ни другого — честно оставляем «Смотрю»:
     лишний тайтл в «Смотрю» человек уберёт сам, а пропущенную серию
     ему никто не покажет. */
  function isFinalEpisode(last) {
    var now = (st.related || []).filter(function (p) { return p.current; })[0];
    if (now && now.ongoing) return false;
    var total = (now && now.episodes) || TOTAL;
    return total > 0 && last >= total;
  }

  function save(asWatched, opts) {
    if (!canSave()) return Promise.resolve();
    /* Тайтл в планах (или только что убран из них), а смотреть его ещё
       не начинали — ничего не пишем. Иначе само открытие страницы
       (выбор первой серии, уход со страницы) записало бы «смотрю» с
       первой серией, и план пропал бы, хотя ни секунды не посмотрено.
       Как только видео пошло (st.touched) — сохраняем как обычно, и
       тайтл сам переезжает из планов в «Не закончил». */
    if (holdSaves() && !st.touched && !asWatched) return Promise.resolve();
    var path = asWatched ? '/api/library/watched' : '/api/library/progress';
    return A.api.post(path, payload(asWatched), opts).then(function () {
      $('w-saved').textContent = A.t('сохранено ', 'saved at ') + A.mmss(st.position);
    }).catch(function () {});
  }

  /* ================= «В планы» =================
     Кнопка под плеером: отложить этот тайтл в «Планирую смотреть».
     Начатое (досмотрено, вторая серия и дальше, хоть одна секунда — как
     считает и сервер, store._started) в планы не кладётся: тогда кнопка
     неактивна и говорит «Уже смотрите». */
  function planned() { return !!(st.item && st.item.status === 'later'); }
  function holdSaves() { return planned() || st.unplanned === true; }

  function startedHere() {
    var it = st.item;
    return st.touched === true || st.serverStarted === true || (!!it && it.status !== 'later' &&
      (it.status === 'done' || (it.watched_ep || 0) > 1 || (it.position || 0) > 0));
  }

  function paintPlan() {
    var b = $('w-plan');
    if (!b) return;
    if (!canSave()) { b.hidden = true; return; }
    var started = startedHere();
    var on = !started && planned();
    b.hidden = false;
    b.disabled = started;
    b.classList.toggle('planon', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    var ru = started ? 'Уже смотрите' : (on ? 'В планах ✓' : 'В планы');
    var en = started ? 'Already watching' : (on ? 'Planned ✓' : 'Plan to watch');
    $('w-plan-t').setAttribute('data-ru', ru);
    $('w-plan-t').setAttribute('data-en', en);
    $('w-plan-t').textContent = A.t(ru, en);
    b.title = started ? A.t('Вы уже начали смотреть этот тайтл', 'You have already started this title')
                      : (on ? A.t('Нажмите, чтобы убрать из планов', 'Press to remove from plans')
                            : A.t('Отложить в «Планирую смотреть»', 'Save to “Plan to watch”'));
  }

  (function () {
    var b = $('w-plan');
    if (!b) return;
    b.addEventListener('click', function () {
      if (b.disabled || b.classList.contains('busy') || startedHere()) return;
      b.classList.add('busy');
      var job;
      if (planned()) {
        job = A.api.del('/api/library/' + encodeURIComponent((st.item && st.item.key) || KEY)).then(function () {
          st.item = null;
          st.unplanned = true;     // уход со страницы не должен вернуть его в «смотрю»
        });
      } else {
        var body = payload(false);
        body.watched_ep = 0;
        body.position = 0;
        body.status = 'later';
        job = A.api.post('/api/library/progress', body).then(function (res) {
          if (res && res.status && res.status !== 'later') {
            /* Сервер отказал: тот же тайтл уже начат (возможно, с другого
               источника). Показываем это на кнопке. */
            st.serverStarted = true;
            return;
          }
          var it = st.item || {};
          for (var k in body) if (Object.prototype.hasOwnProperty.call(body, k)) it[k] = body[k];
          st.item = it;
          st.unplanned = false;
        });
      }
      job.catch(function (err) {
        $('w-saved').textContent = (err && err.message) || A.t('Не получилось', 'Did not work');
      }).finally(function () {
        b.classList.remove('busy');
        paintPlan();
      });
    });
  })();

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
    /* Ссылки другой серии — не ссылки этой, даже если других нет */
    if (!st.videos.length || st.videosFor !== st.current) return null;
    var v = st.videos[st.video] || st.videos[0];
    return v ? A.safeUrl(v.url) : null;
  }

  /* Остановить и выгрузить то, что играло. Без src плеер чёрный, поэтому
     возвращаем заставку и большую кнопку — пустой чёрный прямоугольник
     читается как «сломалось», а не как «нажми ▶». */
  function stopPlayer() {
    if (mediaAbort) { mediaAbort.abort(); mediaAbort = null; }
    holdPosition = false;
    if (hls) { hls.destroy(); hls = null; }
    attachedUrl = '';
    video.pause();
    video.removeAttribute('src');
    try { video.load(); } catch (e) { /* пустой src — это нормально */ }
    showLoading(false);
    chrome(true);
  }

  /* Какой адрес сейчас стоит в плеере. Ошибка, пришедшая от другого
     (уже сменённого) адреса, ничего не решает — см. qualityFailed. */
  var attachedUrl = '';
  /* Адреса, по которым уже показали хоть один кадр, и те из них, что
     после сбоя посреди просмотра уже открывали заново. Качество, которое
     честно играло десять минут и споткнулось на обрыве сети, не
     выкидываем сразу — сперва даём ему второй шанс. */
  var played = {};
  var retried = {};
  /* Человек сам поставил паузу. После автоматической смены качества или
     озвучки видео не должно включаться у него за спиной. */
  var userPaused = false;
  /* Новый адрес ещё не встал на свою секунду — см. timeupdate. */
  var holdPosition = false;

  /* Наша прокладка (/api/hls): поток с хостинга, который пускает только
     со «своей» страницы (английские источники). Адрес без .m3u8, но это
     плейлист HLS, и ходить к нему надо с нашей cookie входа. */
  function isOurHls(url) {
    try {
      var p = new URL(String(url || ''), location.origin);
      return p.origin === location.origin && p.pathname === '/api/hls';
    } catch (e) { return false; }
  }
  function isStreamUrl(url) { return /\.m3u8(\?|$)/i.test(url || '') || isOurHls(url); }

  /* Сюда попадает только адрес, уже прошедший safeUrl в currentUrl().
     autoplay: false — открыть на нужной секунде и ждать (было на паузе). */
  function attach(rawSrc, startAt, autoplay) {
    var safeSrc = A.safeUrl(rawSrc);   // повторная проверка: дешевле, чем ошибка
    if (!safeSrc) { fail(A.t('Плеер не дал ссылку на видео', 'The player returned no video link')); return; }
    if (autoplay === undefined) autoplay = true;
    if (autoplay) userPaused = false;
    showLoading(true);
    fail('');
    /* Полный сброс: без него при переходе с потока на обычный файл
       остаётся прежний источник и играет старое видео. */
    if (hls) { hls.destroy(); hls = null; }
    video.pause();
    video.removeAttribute('src');
    try { video.load(); } catch (e) { /* пустой src — это нормально */ }
    attachedUrl = safeSrc;

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
    holdPosition = startAt > 0;

    function trySeek() {
      if (seekDone || !(startAt > 0)) return;
      if (!isFinite(video.duration) || video.duration <= 1) return;
      seekDone = true;
      video.currentTime = Math.min(startAt, video.duration - 1);
      holdPosition = false;
      /* Продолжаем с места, где уже было за девяносто процентов, — значит,
         отметка о просмотре этой серии уже ушла в прошлый раз (её ставит
         тот же обработчик, что и здесь, на тех же девяноста). Раньше флаг
         жил только до перезагрузки, и первый же кадр после перемотки
         отмечал серию снова: в итогах года одна серия считалась два,
         три раза — по разу за каждый заход. */
      if (startAt / video.duration >= 0.9) markedThisEpisode = true;
    }

    video.addEventListener('loadedmetadata', trySeek, opt);
    /* Новый адрес в плеере сбрасывает выбор дорожки текста — ставим
       выбранную обратно. */
    video.addEventListener('loadedmetadata', function () { setCc(st.cc); }, opt);
    video.addEventListener('durationchange', trySeek, opt);
    video.addEventListener('canplay', trySeek, opt);
    /* Первый показанный кадр — доказательство, что качество рабочее. */
    video.addEventListener('loadeddata', function () {
      played[safeSrc] = true;
      st.deadDubs = st.deadDubs.filter(function (n) { return n !== st.dub; });
    }, opt);

    var readyOnce = false;
    function ready() {
      if (readyOnce) return;
      readyOnce = true;
      chrome(false);
      trySeek();
      if (!autoplay) { showLoading(false); paintPlayIcon(); return; }
      /* Значок загрузки снимает 'playing' (см. ниже), а не этот момент:
         манифест разобран — это ещё не картинка, и раньше между «значок
         пропал» и «пошло видео» стоял чёрный экран на несколько секунд. */
      var go = video.play();
      if (go && typeof go.catch === 'function') {
        go.catch(function (err) {
          /* Браузер может не пустить автозапуск — тогда просто ждём клика.
             Ждать при этом надо с кнопкой, а не с вечно крутящимся значком.
             Отказ «прервано паузой» — это человек сам нажал паузу, пока
             грузилось: заставку поверх кадра тогда не возвращаем. */
          showLoading(false);
          if (!err || err.name !== 'AbortError') chrome(true);
          else paintPlayIcon();
        });
      }
    }

    if (isStreamUrl(safeSrc) && window.Hls && window.Hls.isSupported()) {
      var mine = new window.Hls({ maxBufferLength: 30, enableWorker: true });
      hls = mine;
      mine.loadSource(safeSrc);
      mine.attachMedia(video);
      mine.on(window.Hls.Events.MANIFEST_PARSED, function () {
        /* Дорожки текста внутри потока — в меню CC вместе с файлами. */
        if (hls === mine) {
          st.hlsSubs = (mine.subtitleTracks || []).map(function (t, i) {
            return { i: i, label: t.name || t.lang || ('#' + (i + 1)),
                     lang: String(t.lang || '').slice(0, 2).toLowerCase() };
          });
          pickCc();
        }
        ready();
      });
      /* Повторные попытки по сети hls.js делает сам (по нескольку на
         манифест и на каждый кусок), и ошибка с fatal приходит, когда
         они уже кончились. Раньше здесь вызывался ещё и hls.startLoad() —
         но после того, как не скачался сам манифест, он не делает ничего:
         ни новой попытки, ни новой ошибки. Плеер так и оставался чёрным,
         без значка и без единого слова. Именно это видели на JoJo: 720p
         первой серии у источника нет, а плеер выбирал его первым.
         Теперь сетевой отказ — это отказ качества: берём следующее.
         Сбой декодера один раз лечим штатным recoverMediaError. */
      var mediaRetry = 0;
      mine.on(window.Hls.Events.ERROR, function (e, data) {
        if (!data || !data.fatal || hls !== mine) return;
        if (data.type === window.Hls.ErrorTypes.MEDIA_ERROR && mediaRetry < 1) {
          mediaRetry++;
          mine.recoverMediaError();
          return;
        }
        mine.destroy();
        if (hls === mine) hls = null;
        qualityFailed(safeSrc);
      });
    } else {
      /* Сафари умеет потоки сам, обычные mp4 играют везде. Проверить mp4
         заранее нельзя (см. probeQualities), поэтому о негодном качестве
         узнаём только здесь, по ошибке самого плеера. */
      video.addEventListener('loadedmetadata', ready, opt);
      video.addEventListener('error', function () {
        qualityFailed(safeSrc);
      }, opt);
      video.src = safeSrc;
      video.load();
    }
  }

  /* ================= качества, которые не открываются =================

     Источник отдаёт список качеств, а есть ли за каждым видео — не
     проверяет. У JoJo (OVA 1993, RU-B) в первой серии 720p нет
     вовсе: ссылка ведёт в пустоту. Плеер брал лучшее, то есть его, и
     человек видел чёрный экран — хотя 480p и 360p рядом исправны.

     Теперь неработающее качество убирается из меню совсем (для этой
     серии и этой озвучки — у следующей серии оно может и быть), а плеер
     сам переходит на следующее ниже с той же секунды. Не осталось ни
     одного — пробуем другую озвучку (allQualitiesFailed). */
  function qualityIndex(url) {
    for (var i = 0; i < st.videos.length; i++) {
      if (A.safeUrl(st.videos[i].url) === url) return i;
    }
    return -1;
  }

  /* Плеер не смог открыть адрес. */
  function qualityFailed(url) {
    if (url !== attachedUrl) return;   // ошибка от адреса, который уже сменили
    showLoading(false);
    if (played[url] && !retried[url]) {
      /* Играло и споткнулось — скорее всего, сеть. Одна попытка с того
         же места, прежде чем считать качество негодным. */
      retried[url] = true;
      attach(url, st.position, !userPaused);
      return;
    }
    if (qualityIndex(url) < 0) {
      fail(A.t('Видео не открылось. Попробуйте другую озвучку или источник.',
               'The video did not open. Try another dub or source.'));
      chrome(true);
      return;
    }
    dropQuality(url);
  }

  /* Убрать качество из меню. Если стояло именно оно — переключиться на
     следующее ниже (список идёт от лучшего к худшему), а если ниже нет —
     на ближайшее выше: любое рабочее лучше чёрного экрана. */
  function dropQuality(url) {
    var i = qualityIndex(url);
    if (i < 0) return;
    var wasChosen = i === st.video;
    st.videos.splice(i, 1);
    if (i < st.video) st.video--;
    else if (wasChosen) st.video = Math.max(0, Math.min(i, st.videos.length - 1));
    renderMenus();
    if (!wasChosen) return;
    if (!st.videos.length) { allQualitiesFailed(); return; }
    /* В плеере стоит выкинутый адрес — меняем его, с той же секунды.
       Ещё не включали — ничего не грузим: подпись на кнопке уже
       показывает то качество, которое включится. */
    if (started && attachedUrl === url) attach(currentUrl(), st.position, !userPaused);
  }

  /* Проверка качеств заранее, сразу после ответа /api/videos.

     Список .m3u8 — крошечный текстовый файл, скачать его — доли секунды,
     и hls.js сделал бы ровно то же самое. Не скачался (нет файла, нет
     разрешения CORS, ошибка сервера) — значит, и плеер его не откроет:
     убираем качество до того, как человек его выберет. Плеер при этом
     никого не ждёт: ▶ включает лучшее из списка сразу, и если его
     проверка провалится — оно сменится на следующее само.

     mp4 так не проверить: обычному <video> разрешение CORS не нужно,
     а fetch без него падает — и мы выкинули бы рабочее видео. Для mp4
     остаётся только ошибка самого плеера (см. attach). */
  function probeQualities() {
    var list = st.videos;
    if (typeof window.fetch !== 'function' || !list.length) return;
    if (st.deadDubs.indexOf(st.dub) >= 0) return;   // эту уже бросили, сейчас ищем другую
    list.slice().forEach(function (v) {
      var url = A.safeUrl(v.url);
      if (!url || !isStreamUrl(url) || played[url]) return;
      probeStream(url).then(function (alive) {
        /* null — не дождались ответа: это ещё не «не работает».
           Пока шла проверка, сменилась серия или озвучка — ответ
           относится к списку, которого уже нет. */
        if (alive !== false || st.videos !== list || played[url]) return;
        dropQuality(url);
      });
    });
  }

  function fetchText(url, ms) {
    var ctl = (typeof AbortController === 'function') ? new AbortController() : null;
    var timer = ctl ? setTimeout(function () { ctl.abort(); }, ms) : null;
    return fetch(url, { mode: 'cors', credentials: isOurHls(url) ? 'same-origin' : 'omit',
                        referrerPolicy: 'no-referrer',
                        signal: ctl ? ctl.signal : undefined })
      .then(function (resp) {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.text();
      })
      .finally(function () { clearTimeout(timer); });
  }

  /* true — открывается, false — точно нет, null — не успели узнать. */
  function probeStream(url) {
    var M3U = /^\s*#EXTM3U/;
    return fetchText(url, 12000).then(function (text) {
      if (!M3U.test(text)) return false;
      /* Главный список ведёт на вложенные — проверяем первый из них:
         бывает, что главный на месте, а за ним пусто. */
      var m = /#EXT-X-STREAM-INF[^\n]*\n\s*([^#\s][^\r\n]*)/.exec(text);
      if (!m) return true;
      var inner = A.safeUrl(new URL(m[1].trim(), url).href);
      if (!inner) return true;
      return fetchText(inner, 12000).then(function (t2) { return M3U.test(t2); });
    }).catch(function (err) {
      return err && err.name === 'AbortError' ? null : false;
    });
  }

  /* Ни одно качество озвучки не открылось. Молча оставлять чёрный плеер
     нельзя: говорим, что случилось, и сами пробуем следующую озвучку из
     тех, что ещё не подводили. Не осталось ни одной — предлагаем другой
     источник. */
  var rescuing = false;

  function allQualitiesFailed() {
    if (rescuing) return;
    var dead = st.dub;
    var ep = st.current;
    var resume = started;
    if (dead && st.deadDubs.indexOf(dead) < 0) st.deadDubs.push(dead);
    stopPlayer();
    started = false;
    var next = st.dubs.map(function (d) { return cut(d.name, DUB_MAX); })
      .filter(function (n) {
        return n && n !== dead && st.deadDubs.indexOf(n) < 0 && st.badDubs.indexOf(n) < 0;
      })[0];
    st.videos = [];
    renderMenus();
    if (!next) {
      notice(A.t('Видео этой серии не открывается', 'This episode will not play'),
             A.t('Ни в одной озвучке «' + sourceLabel(SOURCE) + '» видео не открылось. ' +
                 'Возможно, у другого источника с этой серией всё в порядке.',
                 'No dub on "' + sourceLabel(SOURCE) + '" would open. ' +
                 'Another source may have this episode working.'),
             true);
      return;
    }
    rescuing = true;
    notice(A.t('Видео в озвучке «' + dead + '» не открывается',
               'The "' + dead + '" dub will not play'),
           A.t('Пробуем «' + next + '»…', 'Trying "' + next + '"…'));
    loadVideos(next).then(function (fresh) {
      rescuing = false;
      if (st.current !== ep) return;
      if (!fresh) {
        /* Просимая не ответила вовсе (она теперь «не отвечает») — идём
           к следующей. Если же за это время человек сам выбрал что-то
           другое, его выбор не перебиваем. */
        if (!st.videos.length && !videosPending) allQualitiesFailed();
        return;
      }
      /* Сервер мог подставить вместо просимой ту, что уже подвела, —
         тогда ищем дальше (просимая при этом помечена «не отвечает»,
         так что круг не замкнётся). */
      if (st.deadDubs.indexOf(st.dub) >= 0 || !st.videos.length) {
        allQualitiesFailed();
        return;
      }
      notice(A.t('Видео в озвучке «' + dead + '» не открылось',
                 'The "' + dead + '" dub would not play'),
             A.t('Включили «' + st.dub + '». Другую можно выбрать в меню «Озвучка».',
                 'Switched to "' + st.dub + '". Pick another one in the Dub menu.'));
      if (resume) play();
    });
  }

  /* ▶ нажали, пока ссылки этой серии ещё в пути. */
  var playWaiting = false;

  function play() {
    /* Ссылки новой серии ещё не пришли — ждём их, а не включаем то, что
       лежит. Раньше автопереход звал play через 800 мс «на удачу»: если
       хостинг отвечал дольше, играла ПРОШЛАЯ серия под подписью новой. */
    if (st.videosFor !== st.current && videosPending) {
      if (playWaiting) return;
      playWaiting = true;
      showLoading(true);
      var ep = st.current;
      videosPending.then(function () {
        playWaiting = false;
        showLoading(false);
        /* Пока ждали, человек выбрал другую серию — её он включит сам */
        if (st.current === ep) play();
      });
      return;
    }
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
      userPaused = false;
      /* play() возвращает обещание, и оно бывает отклонено (видео ещё
         не готово, браузер против). Без catch каждый такой раз ложился
         в консоль необработанной ошибкой. */
      var go = video.play();
      if (go && typeof go.catch === 'function') go.catch(function () {});
      flash(false);
    } else {
      userPaused = true;
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

  /* ================= клавиши =================

     Пробел ставил на паузу только тогда, когда фокус случайно стоял на
     кнопке ▶ внизу. В остальное время он прокручивал страницу вниз — и
     плеер уезжал за край экрана посреди сцены. Теперь, как в любом
     плеере: пробел или K — пауза, F — полный экран, M — звук, C —
     субтитры (если они отдельными файлами), стрелки влево-вправо — пять
     секунд. Русская раскладка тоже считается: там
     на тех же клавишах Л, А, Ь и С.

     Не трогаем: поля ввода, открытые окна, а пробел — на кнопках и
     ссылках (там он и так нажимает то, на чём стоит фокус). Стрелки на
     дорожке и на громкости обрабатывают они сами. */
  document.addEventListener('keydown', function (e) {
    if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
    if (document.querySelector('.veil.dialog')) return;
    /* Esc закрывает открытое меню под плеером («Серия», «Озвучка»,
       «Источник»…) и возвращает фокус на его кнопку. Раньше меню
       закрывалось только щелчком мимо — с клавиатуры из него было не
       выйти. В полном экране Esc по-прежнему сворачивает экран: это
       делает сам браузер. */
    if (e.key === 'Escape') {
      var openMenu = document.querySelector('.menu.show');
      if (openMenu) {
        e.preventDefault();
        openMenu.classList.remove('show');
        var owner = document.querySelector('.selbtn[data-menu="' + openMenu.id + '"]');
        if (owner) owner.focus();
      }
      return;
    }
    var t = e.target || {};
    var tag = t.tagName || '';
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t.isContentEditable) return;
    var onButton = tag === 'BUTTON' || tag === 'A';
    var onSlider = t.getAttribute && t.getAttribute('role') === 'slider';
    var k = (e.key || '').toLowerCase();
    if (k === ' ' || k === 'k' || k === 'л') {
      if (onButton && k === ' ') return;
      e.preventDefault();
      toggle();
    } else if (k === 'f' || k === 'а') {
      e.preventDefault();
      toggleFullscreen();
    } else if (k === 'm' || k === 'ь') {
      e.preventDefault();
      $('c-mute').click();
    } else if (k === 'c' || k === 'с') {
      /* Субтитры: включить или выключить (если они отдельными файлами) */
      e.preventDefault();
      toggleCc();
    } else if ((k === 'arrowleft' || k === 'arrowright') && !onSlider) {
      e.preventDefault();
      nudge(k === 'arrowleft' ? -5 : 5);
    }
  });

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
  video.addEventListener('pause', function () { wake(); paintPlayIcon(); showLoading(false); });
  /* Значок загрузки — пока видео ждёт данных: в начале, после перемотки,
     на медленной сети. Раньше он гас уже по «манифест разобран», и
     между этим и первым кадром стоял чёрный экран без единого признака
     жизни, а подвисание посреди серии не показывалось вовсе. */
  video.addEventListener('waiting', function () {
    if (started && attachedUrl && !video.paused) showLoading(true);
  });
  video.addEventListener('playing', function () { showLoading(false); });
  video.addEventListener('seeked', function () {
    if (video.paused || video.readyState >= 3) showLoading(false);
  });
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
  /* ±10 секунд. Раньше «вперёд» прибавлял к времени без оглядки на
     конец (у конца серии это мгновенный «конец» и автопереход), а до
     запуска обе кнопки не делали ничего, хотя дорожка в это время уже
     умеет выбирать, откуда начать. */
  function nudge(sec) {
    if (started && st.duration) {
      var to = Math.max(0, Math.min(st.duration - 0.5, video.currentTime + sec));
      video.currentTime = to;
      st.position = Math.round(to);
      paintPosition();
      $('w-cur').textContent = A.mmss(st.position);
      return;
    }
    if (started) return;                 // ещё грузится — длина неизвестна
    st.position = Math.max(0, st.position + sec);
    $('w-cur').textContent = A.mmss(st.position);
  }
  $('c-back').addEventListener('click', function () { nudge(-10); });
  $('c-fwd').addEventListener('click', function () { nudge(10); });
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
    /* data-ru-aria / data-en-aria — тоже: по ним смена языка
       переписывает подпись, и в полном экране она не должна вернуться
       к «Во весь экран». */
    $('c-full').setAttribute('data-ru-aria', on ? 'Свернуть' : 'Во весь экран');
    $('c-full').setAttribute('data-en-aria', on ? 'Exit fullscreen' : 'Fullscreen');
    $('c-full').setAttribute('aria-label', on
      ? A.t('Свернуть', 'Exit fullscreen') : A.t('Во весь экран', 'Fullscreen'));
    wake();
  });

  /* дорожка времени идёт за видео */
  video.addEventListener('timeupdate', function () {
    /* Пока в плеер ставится новый адрес, время честно сбрасывается в
       ноль, и это событие тоже приходит. Раньше этот ноль записывался
       в st.position: смена качества, озвучки или запасное качество
       после сбоя начинали серию сначала, а через 15 секунд ноль
       уходил на сервер вместо «продолжить с 08:10». Ждём, пока новый
       адрес встанет на свою секунду. */
    if (!attachedUrl || holdPosition || video.readyState < 1) return;
    st.position = Math.round(video.currentTime);
    st.duration = isFinite(video.duration) ? video.duration : 0;
    paintBuffered();
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

  video.addEventListener('play', function () {
    chrome(false);
    /* Пошло видео — это уже просмотр: планы кончились (см. save). */
    if (!st.touched) { st.touched = true; st.unplanned = false; paintPlan(); }
  });
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
    /* На картинке карточки «Дальше» — номер следующей серии. Если
       обложка загрузится, он станет маленькой меткой в углу; если нет —
       останется крупно на цветной подложке. Пустой тёмной коробки, как
       раньше, не бывает ни в каком случае. */
    $('w-nextnum').textContent = next !== null ? String(next)
      : (nums.length ? '✓' : '—');
    if (next !== null && autoNextEnabled()) {
      pick(next);
      /* play сам дождётся ссылок именно этой серии */
      play();
    }
  });
  /* keepalive: без него браузер отменяет этот запрос вместе со вкладкой,
     и секунда, на которой человек остановился, пропадает.
     Отложенное сохранение при этом снимаем: иначе за уходом со страницы
     следом уходит второй такой же запрос — лишняя запись в базу
     и лишний повод для ограничителя частоты. */
  /* Уход со страницы на телефоне. Safari на айфоне beforeunload не
     присылает вовсе, Chrome на Android — не всегда: вкладку сворачивают
     в другое приложение, и система её потом молча выгружает. Последние
     до пятнадцати секунд просмотра при этом пропадали. pagehide и
     «вкладку спрятали» (visibilitychange) приходят и там. Чтобы на
     компьютере при закрытии вкладки не уходило два-три одинаковых
     запроса, повтор в течение полутора секунд пропускаем.

     И ещё одно: тайтл, у которого источник так и не отдал список серий
     (неверный ключ в ссылке, источник лежит), при уходе в список не
     пишем — его там никто не смотрел. Раньше любая ссылка вида
     /watch?key=…&title=… при уходе со страницы молча добавляла в список
     запись с тем названием и обложкой, что были в ссылке. */
  var leftAt = 0;
  function saveOnLeave() {
    clearTimeout(saveTimer);
    saveTimer = null;
    if (Date.now() - leftAt < 1500) return;
    if (!st.episodes.length && !st.touched) return;
    leftAt = Date.now();
    save(false, { keepalive: true });
  }
  window.addEventListener('beforeunload', saveOnLeave);
  window.addEventListener('pagehide', saveOnLeave);
  document.addEventListener('visibilitychange', function () {
    if (document.hidden) saveOnLeave();
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
    /* Центр кружка ходит не от края до края, а от kr до «ширина − kr»
       (см. --kr в app.css: так кружок не обрезается у краёв). Нажатие
       переводим во время по той же мерке — тогда кружок встаёт ровно
       под палец, а видео попадает туда, где он стоит. Нажали в самом
       краю, левее центра кружка на нуле, — это ноль. */
    var kr = knobRadius();
    var span = r.width - 2 * kr;
    if (span <= 0) return 0;
    return Math.max(0, Math.min(1, (clientX - r.left - kr) / span));
  }

  /* Радиус самого крупного кружка — тот же --kr, что в стилях. */
  function knobRadius() {
    var v = parseFloat(getComputedStyle(track).getPropertyValue('--kr'));
    return isFinite(v) ? v : 0;
  }

  /* Где на дорожке доля p. Пройденная полоса кончается там же, где
     центр кружка, — иначе между зелёной полосой и кружком у краёв
     светилась бы серая щель. На нуле полосы нет вовсе (смотреть ещё
     не начали), на единице она доходит до самого края. */
  function trackAt(p) {
    return 'calc(var(--kr) + (100% - 2 * var(--kr)) * ' + p + ')';
  }

  function paintBar(node, p) {
    if (!node) return;
    p = Math.max(0, Math.min(1, p || 0));
    node.style.width = p <= 0 ? '0%' : (p >= 1 ? '100%' : trackAt(p));
  }

  function paintKnob(p) {
    $('knob').style.left = trackAt(Math.max(0, Math.min(1, p || 0)));
  }

  /* Показать положение, не трогая видео. */
  function paintSeek(p) {
    paintBar($('pos'), p);
    paintKnob(p);
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
    /* Выбрал место, с которого смотреть, — это уже начало просмотра. */
    if (!st.touched) { st.touched = true; st.unplanned = false; paintPlan(); }
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

    /* Выражение, а не объявление функции: объявлять функцию внутри
       блока if в строгом режиме ES5 нельзя (это разрешил только ES2015). */
    var endDrag = function (e) {
      if (!dragging) return;
      dragging = false;
      track.classList.remove('dragging');
      applySeek(trackShare(e.clientX));
    };
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
    /* Подпись на обоих языках — выберет applyLang. Раньше она ставилась
       через A.t в момент загрузки скрипта, когда язык сайта ещё не
       известен, и на английской странице оставалась «Перемотка». */
    track.setAttribute('aria-label', 'Перемотка');
    track.setAttribute('data-ru-aria', 'Перемотка');
    track.setAttribute('data-en-aria', 'Seek');
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
  /* Сколько уже скачано. Полоса .buf была нарисована в стилях раз и
     навсегда — на 74%, у любой серии и до самого запуска. */
  function paintBuffered() {
    var buf = document.querySelector('#track .buf');
    if (!buf) return;
    var end = 0;
    var d = st.duration || video.duration || 0;
    try {
      for (var i = 0; i < video.buffered.length; i++) {
        if (video.buffered.start(i) <= video.currentTime + 0.5) end = Math.max(end, video.buffered.end(i));
      }
    } catch (e) { end = 0; }
    paintBar(buf, d && isFinite(d) ? end / d : 0);
  }
  video.addEventListener('progress', paintBuffered);
  video.addEventListener('emptied', paintBuffered);

  function paintPosition() {
    var p = st.duration ? video.currentTime / st.duration : 0;
    paintBar($('pos'), p);
    paintKnob(p);
  }


  document.querySelectorAll('.sw').forEach(function (b) {
    b.addEventListener('click', function () {
      b.setAttribute('aria-checked', b.getAttribute('aria-checked') === 'true' ? 'false' : 'true');
    });
  });

  /* «Следующая сама» под плеером — сохраняется в аккаунт сразу.

     Раньше переключатель здесь только перекидывался на экране: в
     аккаунт ничего не уходило, хотя в кабинете написано «то же, что
     переключатель под плеером», и читала его эта же страница при
     загрузке. Выключил автопереход под плеером, открыл следующую серию
     или вернулся завтра — он снова включён. Теперь так же, как в
     кабинете: сразу на сервер, а не сохранилось — переключатель
     возвращается и плашка говорит почему. Гостю — только на эту
     страницу (подпись «гостю не сохраняем»). Обработчик стоит после
     общего, поэтому видит уже новое положение. */
  (function () {
    var sw = $('w-autobox') && $('w-autobox').querySelector('.sw');
    if (!sw) return;
    sw.addEventListener('click', function () {
      if (!canSave()) return;
      var on = sw.getAttribute('aria-checked') === 'true';
      A.api.post('/api/me/settings', { autonext: on }).then(function (r) {
        if (r && r.settings && A.me) A.me.settings = r.settings;
      }).catch(function (err) {
        sw.setAttribute('aria-checked', on ? 'false' : 'true');
        notice(A.t('Настройка не сохранилась', 'The setting was not saved'),
               (err && err.message) || '');
      });
    });
  })();

  /* Кнопки меню под плеером говорят, открыто ли их меню (aria-expanded).
     Меню закрывают из многих мест (выбор пункта, щелчок мимо, Esc),
     поэтому следим за самим меню, а не за каждым местом. */
  document.querySelectorAll('.selbtn[data-menu]').forEach(function (b) {
    var m = $(b.dataset.menu);
    if (!m || typeof MutationObserver !== 'function') return;
    new MutationObserver(function () {
      b.setAttribute('aria-expanded', m.classList.contains('show') ? 'true' : 'false');
    }).observe(m, { attributes: true, attributeFilter: ['class'] });
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
      $('w-autonote').setAttribute('data-ru', 'гостю не сохраняем');
      $('w-autonote').setAttribute('data-en', 'not saved for guests');
      $('w-autonote').textContent = A.t('гостю не сохраняем', 'not saved for guests');
    } else if (me.demo_account) {
      $('w-autobox').style.opacity = '.5';
      $('w-autonote').setAttribute('data-ru', 'в демо-аккаунте не сохраняем');
      $('w-autonote').setAttribute('data-en', 'not saved in the demo account');
      $('w-autonote').textContent = A.t('в демо-аккаунте не сохраняем', 'not saved in the demo account');
    }
    /* Хентай, найденный только на другом языке: плеер — на том языке,
       настройка не меняется (см. RX выше). Кому хентай не положен, тому
       и обходить язык незачем. */
    if (RX && ALANG && hentaiAllowed() && A.ANIME_LANGS.indexOf(ALANG) >= 0 &&
        ALANG !== A.animeLang()) {
      A.setPageAnimeLang(ALANG);
    }
    /* Кнопка языка и CC в панели под плеером: подписи и меню. */
    renderLangMenu();
    renderCc();
    /* «Пуск» у кнопки под картинкой — уже на языке сайта: раньше он
       ставился только после первого пуска, и до него на английской
       странице диктор читал «Пуск». */
    paintPlayIcon();
    /* Источник мог не приехать в адресе — например, по ссылке от друга
       или из закладки. Тогда берём основной для языка сайта: язык к
       этому моменту уже применён, а до него он неизвестен. */
    var guessedSource = !SOURCE;
    if (!SOURCE) SOURCE = A.defaultSource();
    if (!guessedSource) startEpisodesEarly();

    /* Список источников приходит своим запросом. Дождавшись его,
       перерисовываем меню — если оно к тому моменту уже нарисовано.

       Ответ — решение: null, если источник страницы в списке есть и
       можно грузить серии, или описание того, почему его нет. Раньше
       серии запрашивались, не дожидаясь списка, и у источника вне списка
       сервер отвечал «Неизвестный источник» — поверх плашки о переезде
       ложилась вторая, «этого аниме тут нет». */
    var sourcesReady = A.api.get('/api/sources?lang=' + encodeURIComponent(A.animeLang()))
      .then(function (rows) {
        sources = rows || [];
        /* Источник не пришёл в адресе, а основной для языка в этом режиме
           не работает (в демо-режиме источник один — «demo»). Догадку
           молча поправляем: человек этот источник не выбирал, и плашка
           «он недоступен» его бы только запутала. */
        if (guessedSource) SOURCE = usableSource();
        paintSource();
        if (st.dubs.length) renderMenus();
        if (!sources.length || isAvailable(SOURCE)) return null;

        /* Источника в списке нет. Причин две, и человеку важно знать
           какая.

           Источник другого языка: в список тайтл мог попасть с
           англоязычного источника, а сайт потом переключили на русский.
           Открывать его нельзя — получится «Магическая битва» с English
           dub на русском сайте, ровно та путаница, ради которой
           источники и разделены по языкам.

           Источник, которого в этом режиме нет вовсе: тайтл сохранили,
           пока сайт работал в демо-режиме, а теперь режим обычный (или
           наоборот). Чтобы отличить одно от другого, спрашиваем список
           второго языка — это один короткий запрос и только в этом
           редком случае. */
        var other = A.animeSrcLang() === 'en' ? 'ru' : 'ensub';
        return A.api.get('/api/sources?lang=' + other)
          .then(function (rows2) {
            var hit = (rows2 || []).filter(function (x) { return x.id === SOURCE; })[0];
            return { otherLang: !!hit, label: (hit && hit.label) || SOURCE };
          })
          .catch(function () { return { otherLang: false, label: SOURCE }; });
      }).catch(function () { sources = []; return null; });

    /* Описание грузим отдельной ниткой, а не в общей цепочке.
       Оно ни от серий, ни от плееров не зависит — а в цепочке падало
       вместе с ними: у тайтла, которого нет на источнике, под плеером
       оставался прочерк вместо описания, хотя каталог о нём знает. */
    loadAbout();
    /* И списком частей тоже: он ни от серий, ни от плееров не зависит.
       Раньше в общей цепочке всё, что после отказа источника, просто не
       выполнялось — и у тайтла, которого нет на источнике, пропадало
       заодно и описание, и всё остальное, что каталог про него знает. */
    var relatedReady = loadRelated();

    /* Тайтл сохранён с источника другого языка. Обычный — переезжает к
       источникам вашего языка (relocate). Хентай — остаётся, где лежит
       (fix-21: «просто чтобы был»): язык страницы становится языком
       источника, с подписью «Есть только на …». Хентай ли это, говорит
       «эта история целиком» — ждём её, только когда переезд и впрямь
       понадобился. */
    function rxStay(why) {
      if (!why || !why.otherLang || !hentaiAllowed()) return Promise.resolve(false);
      return relatedReady.then(function () {
        if (!isRx()) return false;
        var code = A.animeSrcLang() === 'ru' ? 'endub' : 'ru';
        A.setPageAnimeLang(code);
        return A.api.get('/api/sources?lang=' + encodeURIComponent(code)).then(function (rows) {
          if (!(rows || []).some(function (x) { return x.id === SOURCE; })) {
            A.setPageAnimeLang('');
            return false;
          }
          sources = rows || [];
          paintSource();
          renderLangMenu();
          paintLangOnly();
          return true;
        });
      }).catch(function () { A.setPageAnimeLang(''); return false; });
    }

    /* Метка «уходим на другой источник»: дальше по цепочке грузить
       серии и плееры незачем, и ошибкой это не является. */
    var RELOCATING = {};

    var itemReady = loadItem()
      .then(function () {
        /* Рисуем шапку сразу, ещё до списка серий. Название, обложка и
           жанры у нас уже есть — они приехали в адресе страницы или из
           полки. Раньше renderHead звался только внутри loadEpisodes,
           и если источник не отвечал, человек оставался на странице
           с прочерком вместо названия: непонятно даже, что открыто. */
        renderHead();
        paintPlan();
      });

    Promise.all([itemReady, sourcesReady])
      .then(function (both) {
        /* Ищем на других после шапки: нужна серия из полки, чтобы
           открыть на новом источнике ту же. */
        if (both[1]) {
          return rxStay(both[1]).then(function (stay) {
            if (stay) return;
            relocate(both[1]);
            throw RELOCATING;
          });
        }
      })
      .then(loadEpisodes)
      .catch(function (err) {
        if (err === RELOCATING) throw err;
        /* Список серий не пришёл вовсе. Причина почти всегда одна из двух:
           тайтла нет у этого источника, или он только вышел и его ещё не
           выложили. И то и другое лечится сменой источника, поэтому
           говорим об этом прямо, а не показываем голый код ошибки. */
        notice(A.t('На «' + sourceLabel(SOURCE) + '» этого аниме нет',
                   'Not on "' + sourceLabel(SOURCE) + '"'),
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
      .then(function () {
        /* Обложку ищем в фоне: видео её не ждёт (раньше ждало — поиск у
           источника перед первой серией, когда в адресе не было &poster=). */
        ensurePoster();
        if (st.position > 0) {
          $('w-cur').textContent = A.mmss(st.position);
          $('w-saved').textContent = A.t('продолжить с ', 'resume at ') + A.mmss(st.position);
        }
        return loadVideos(WANT_DUB);
      })
      .catch(function (err) {
        if (err === RELOCATING) return err;
        /* Если плашка уже всё объяснила — молчим. Иначе показываем
           обычную строку ошибки. */
        if (!noticeShown()) fail(err.message);
      })
      .then(function (r) {
        /* Серии и видео загружены (или не загрузились — тогда плашка
           как раз предлагает сменить источник). Теперь, в фоне, узнаём,
           у кого тайтл есть, — к открытию меню ответ обычно уже готов.
           При переезде на другой источник незачем: страница уходит. */
        if (r !== RELOCATING) scheduleWhere();
      });
  });
})();
