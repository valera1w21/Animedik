/* The watch page: episodes, players, saving the second you stopped at. */
(function () {
  'use strict';
  var A = window.App;
  var $ = function (id) { return document.getElementById(id); };

  var params = new URLSearchParams(location.search);
  var KEY = (params.get('key') || '').slice(0, 200);
  /* The source arrives in the page's address. If it is not there, we take
     the one that is the main one for the site's language: a Russian dub on
     an English site (and the other way round) is a substitution, not a
     fallback. */
    var SOURCE = (params.get('source') || '').slice(0, 40);
  var TITLE = (params.get('title') || '').slice(0, 300);
  /* These fields come from the card: if the title is not in the library
     yet, there is nowhere else to take them from, and it would be saved
     without a cover. */
  var POSTER = A.safeUrl(params.get('poster') || '');
  var YEAR = parseInt(params.get('year') || '0', 10) || null;
  var GENRES = (params.get('genres') || '').slice(0, 300);
  var TOTAL = parseInt(params.get('total') || '0', 10) || 0;
  var WANT_EP = parseInt(params.get('ep') || '0', 10) || 0;
  /* Which dub variant to open right away. It arrives when people came here
     for subtitles: those were found at another source, and it is exactly
     those that must be opened, not the first dub that turns up. */
  var WANT_DUB = (params.get('dub') || '').slice(0, 120);

  var st = {
    /* dubs — the dub names only, they arrive for free together with the
       list of players. videos — the links of the one dub being watched
       right now: those have to be fetched from the video host, and that is
       seconds. */
    episodes: [], current: 0, dubs: [], dub: '', videos: [], video: 0,
    related: [], title_en: '', item: null, duration: 0, position: 0
  };

  /* The list of sources for the "Where the video comes from" menu.
     It used to live in window.__sources and was filled by a separate
     request nobody waited for. The menu is drawn right after the players
     load — that is, almost always BEFORE that list arrived, and inside it
     there was one heading and not a single button. The source could not be
     switched until you reloaded the page.
     Now the list lives here, and the menu is redrawn when it arrives. */
  var sources = [];

  /* Which of the sources definitely knows this title. Empty until asked:
     asking while the page loads is not allowed — that is several seconds
     for the sake of a menu the person may never open. */
  var where = { here: [], checked: [], asked: false };

  /* The episode numbers as the source gives them, and the last of them.

     The difference between "how many episodes are in the list" and "what
     number the last one has" seems like nitpicking right up to the first
     source that does not number episodes from one. One of the sources does
     exactly that: "Наруто Ураганные хроники" arrives as episodes 370
     through 500 — one hundred and thirty-one of them. While those two
     quantities were confused, the page wrote "Episode 370 of 131", and
     marking one episode as watched closed the whole title. */
  function ordinals() {
    return st.episodes.map(function (e) { return e.ordinal; });
  }

  function lastOrdinal() {
    var n = ordinals();
    return n.length ? n[n.length - 1] : 0;
  }

  /* ------------------------------------------------------------------ */
  function fail(msg) {
    /* The error text used to be written in place of the title's name, and
       after a failure there was no telling what was open at all. Now it is
       a separate line. */
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

  /* The cover, when there is nowhere to take it from.

     Usually it arrives in the page's address: the card in the search puts
     it into the link. But one can get here without it — from a bookmark,
     from a friend's link, or the title was once saved to the shelf without
     a picture. Then there was NEVER a cover: the watch page asked nobody
     for it, and a grey rectangle with one letter stayed on the shelf
     forever.

     We ask the source in exactly this case — one search by name, and only
     if the picture really is missing. */
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
        /* We put what was found on the shelf, so as not to search again next time */
        if (canSave()) save(false);
      })
      .catch(function () { /* нет обложки — не повод показывать ошибку */ });
  }

  /* The title's description.

     There used to be an excuse under the player: "we do not show a
     description, so as not to spoil the plot". In fact there was simply
     nothing to show — video sources give no descriptions at all.

     Now it is taken from an open catalogue through our server, which trims
     it to the setup and throws out everything after which a retelling of
     the plot usually begins. */
  var aboutLoaded = false;
  /* The language changed — the description and genres have to be read
     again: they arrive from the server already in their language, and will
     not translate themselves. */
  document.addEventListener('langchange', function () {
    aboutLoaded = false;
    loadAbout();
    renderEpisodeMenu();
    /* We redraw the dub menu: its captions are in the site's language. The
       chosen variant we leave alone — the person may have picked it by
       hand, and changing their choice because the language switched would
       be presumptuous. */
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
        /* The year and genres from the catalogue are more accurate than
           the video sources': there they are often empty or dumped into
           one line. */
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

  /* ================= other parts of this story =================

     The episode list in the column answers the question "where am I inside
     the season". This block answers the one that comes next: which season
     this is at all, what came before it and what comes after. There used to
     be no answer anywhere: a title lay on the shelf as a separate record
     with one name, and a second season with three films was something you
     had to remember yourself and search for by hand.

     It loads on its own thread, like the description: it depends on neither
     episodes nor players, and has no reason to fall along with them. If the
     catalogue is silent, or there is only one part, the block simply is not
     there. */
  function loadRelated() {
    var name = (st.item && st.item.title) || TITLE;
    if (!name) return Promise.resolve();
    return A.api.get('/api/related?title=' + encodeURIComponent(name))
      .then(function (r) {
        var parts = (r && r.items) || [];
        /* One part is not a story of several parts, it is an ordinary
           standalone title. A block saying "this story in full" with a
           single row that cannot even be clicked would only take up room in
           the column. */
        st.related = parts;
        /* The Latin name of the open part — so that the library can show
           the name in the site's language. The source does not know it, the
           catalogue does: we take it from here and save it along with the
           mark. */
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
           /* We take the cover from the catalogue: it is larger and has no
              captions over it, while at the sources there is sometimes only
              a stub. */
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

    /* We scroll the open part into view. "Ван-Пис" has seventy-six parts,
       and without this the block would always show 1998, wherever the
       person happened to be. */
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

  /* Another part was clicked — we look for it at the sources and go there.

     The catalogue knows the part exists, but has no links to its episodes.
     Only the sources know those, and they know them under their own names,
     so here is the same separate step as in search: the name turns into a
     pair, "source + the title's number". */
  function openRelated(p, row, go) {
    if (p.current) return;                       // это и так открыто
    if (row.classList.contains('busy')) return;
    row.classList.add('busy');
    go.textContent = '…';
    A.showError($('w-fr-err'), '');

    /* How many episodes the catalogue promises — a hint for the server:
       for "Ван-Пис" one source posted seven episodes out of a thousand and
       something, and without that number there is nothing to tell a stub
       from a complete title by. */
    A.api.get('/api/resolve?title=' + encodeURIComponent(p.title) +
              '&title_en=' + encodeURIComponent(p.title_en || '') +
              '&episodes=' + encodeURIComponent(p.episodes || 0) +
              '&lang=' + encodeURIComponent(A.langCode()) +
              '&source=' + encodeURIComponent(SOURCE))
      .then(function (found) {
        var go_ = function () { location.href = relatedHref(p, found); };
        /* The match was inexact — we ask. Quietly leading a person off to
           a similar name is worse than leading them nowhere: they will
           realise it is a different title in the middle of an episode. */
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
      /* The source numbers episodes its own way: sometimes from zero,
         sometimes with gaps. So we take the number from its own list rather
         than counting ourselves — otherwise we get "there is no such
         episode". */
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
    /* The same name in the strip over the picture: in full screen the page
       header is not visible, and there is nowhere else to tell what is
       open. */
    $('tb-name').textContent = name;
    $('w-side-title').textContent = name;
    /* "of how many" is the LAST EPISODE NUMBER, not their count.
       This used to be st.episodes.length, and at a source that does not
       number episodes from one, nonsense came out: source A gives "Наруто"
       as episodes 370 through 500, and the page honestly wrote "Episode 370
       of 131". */
    var last = lastOrdinal();
    var tail = last ? A.t(' из ', ' of ') + last : '';
    /* Until the episode list has arrived there is no number yet. Showing
       "Episode 0" and "0 / ?" is worse than showing nothing: it looks like
       a breakdown, though it is simply "we do not know yet". */
    var known = st.current > 0;
    $('w-crumb').textContent = known ? A.t('· серия ', '· episode ') + st.current + tail : '';
    $('w-epno').textContent = known
      ? A.t('Серия ', 'Episode ') + st.current + tail
      : A.t('Загружаем серии…', 'Loading episodes…');
    $('w-side-count').textContent = known ? st.current + ' / ' + (last || '?') : '';

    /* The bar and "watched", though, are counted by the episode's PLACE in
       the list: that is "how much of what is shown here is behind me", and
       for a stretch from 370 to 500 it reads correctly too. */
    var total = st.episodes.length || 0;
    var seen = Math.max(0, ordinals().indexOf(st.current));
    $('w-prog').style.width = total ? Math.min(100, seen / total * 100) + '%' : '0%';
    $('w-seen').textContent = A.t('Просмотрено ', 'Watched ') + seen;
    var left = Math.max(0, total - seen);
    $('w-left').textContent = left
      ? A.t('Осталось ', 'Left ') + Math.round(left * 23 / 60) + A.t(' ч', ' h') : '';

    /* The description loads separately (see loadAbout). Until the answer
       comes, what is already there stays: an empty line or the previous
       text. */
    /* The same pair of quantities as above: an episode number can only be
       compared with the last one's number, not with their count. */
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
    /* The "continue from 00:44" chip has been taken out of here. It stood
       in one row with the genres — among "2020", "Сёнэн", "Фэнтези" — and
       read like another genre. The second you stopped at is written under
       the player and on the seek bar itself anyway. */

    var nums = ordinals();
    var at = nums.indexOf(st.current);
    var next = (at >= 0 && at + 1 < nums.length) ? nums[at + 1] : null;
    if (!nums.length) {
      /* There are no episodes at all — so writing "this is the last one"
         is wrong too: the last of what? That is exactly what used to be
         shown in this case. */
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
      /* Handlers have to be removed, not merely the button hidden:
         otherwise on the last episode the "next" card carried you off to an
         episode from a previous viewing. The line `hidden = true` used to
         be here twice. */
      $('w-next').hidden = true;
      $('w-next').onclick = null;
      $('w-nextcard').onclick = function (e) { e.preventDefault(); };
      $('w-nexttitle').textContent = A.t('Это последняя серия', 'That was the last one');
      $('w-nextsub').textContent = '';
    }
  }

  /* The "Episode" menu in the strip above the picture.

     The episode list is in the right-hand column as well, but in full
     screen the column is not visible — while switching an episode or moving
     to another season is exactly what one wants from there, without leaving
     it. So the same choice is duplicated here: both episodes and parts of
     the story. */
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

    /* Parts of the story — the same list as in the "this story in full"
       block, but here it is needed to change season without leaving full
       screen. */
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
    /* There is nothing to choose — the button does not pretend otherwise. */
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
      /* An episode title from the source may be a spoiler — we do not show it */
      b.appendChild(A.el('span', 'tt', A.t('Серия ', 'Episode ') + ep.ordinal));
      b.appendChild(A.el('span', 'dur', ''));
      b.addEventListener('click', function () { pick(ep.ordinal); });
      box.appendChild(b);
    });
    /* We scroll the list to the current episode, otherwise with a hundred
       episodes it ends up somewhere far below and the list seems not to
       work. */
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
  /* The banner above the player.

     It is needed because "no video" happens for different reasons, and it
     matters to the person which one: wait for a dub, switch the source, or
     look for another title. All of that used to have one red line inside
     the player — and even that did not always appear. */
  function notice(title, sub, offerSource) {
    var box = $('w-notice');
    if (!box) return;
    if (!title) { box.hidden = true; return; }
    $('w-notice-t').textContent = title;
    $('w-notice-s').textContent = sub || '';
    /* The button appears where the trouble is cured by changing the
       source. In its place there used to be the phrase "switch the source
       in the panel under the player" — that is, an explanation of where to
       look for the right button. The panel is at the bottom, the banner at
       the top, the player between them: by the time you scroll there you
       have forgotten what you were looking for. */
    $('w-notice-go').hidden = !offerSource;
    box.hidden = false;
    /* The banner explains the same thing more clearly. We put out the red
       line inside the player at the same time: two messages about one thing
       side by side make people look for a difference between them that is
       not there. */
    fail('');
  }

  function noticeShown() {
    var box = $('w-notice');
    return !!box && !box.hidden;
  }

  /* ================= dubs =================

     Every line in the "Dub" menu used to say the word "плеер" — all eight
     lines identical. A choice among eight "players" is no choice. The cause
     was one word: the code asked the source for the `name` field, while the
     dub's name lies in the `title` field. Now the list says whose work it
     is: source A, JAM, a dubbing studio, a large service.

     Links load only for the dub being watched. The names of all the rest
     are known at once — they arrive together with the list of players —
     while the video addresses have to be asked of the video host, and each
     such question is several seconds. "Магическая битва" at source C has
     thirty-seven dubs: asking them all means making a person wait a minute
     in front of an empty player. */
  function loadVideos(dub) {
    notice('');
    $('v-q').textContent = '…';
    $('v-d').textContent = '…';
    var url = '/api/videos?key=' + encodeURIComponent(KEY) +
              '&ordinal=' + encodeURIComponent(st.current) +
              '&source=' + encodeURIComponent(SOURCE) +
              '&title=' + encodeURIComponent(TITLE) +
              '&dub=' + encodeURIComponent(dub || '') +
              /* The language decides what to open first: in English —
                 the original track with text, not the dub. */
              '&lang=' + encodeURIComponent(A.langCode());
    return A.api.get(url).then(function (r) {
      st.dubs = (r && r.dubs) || [];
      st.dub = (r && r.chosen) || '';
      st.videos = (r && r.videos) || [];
      st.video = 0;
      renderMenus();
      if (!st.videos.length) {
        /* The episode is in the list, but it has no video — that happens
           with episodes that have just come out: the dub is not ready
           yet. */
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
      /* A 502 from the source means "it does not have it", not "we are
         broken". A separate banner instead of a red line: it explains what
         to do. */
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

  /* Switching the dub: its links have not been asked for yet, so we go for
     them. The second of waiting is visible from the ellipsis in the
     caption — a player frozen silently would look like a breakdown. */
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

  /* We look for subtitles at other sources and go there.

     Expensive — each source costs a search, an episode list and a player
     list — so only on a click. The answers are cached, so coming back is
     almost free. */
  var subsSearching = false;

  function findSubs() {
    if (subsSearching) return;
    subsSearching = true;
    var name = (st.item && st.item.title) || TITLE;
    notice(A.t('Ищем субтитры', 'Looking for subtitles'),
           A.t('Смотрим у других источников — это несколько секунд.',
               'Checking other sources — this takes a few seconds.'));
    /* The site's language decides where to look: in English the
       English-language source is asked first — only there is the text over
       the Japanese track English. */
    A.api.get('/api/subs?title=' + encodeURIComponent(name) +
              '&title_en=' + encodeURIComponent(st.title_en || '') +
              '&ordinal=' + encodeURIComponent(st.current) +
              '&lang=' + encodeURIComponent(A.langCode()) +
              '&skip=' + encodeURIComponent(SOURCE))
      .then(function (r) {
        if (!r || !r.found) {
          /* There may be none anywhere — and saying so outright is more
             honest than leaving the person guessing. */
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
    /* Whether the menu makes any sense at all.

       No dubs — no choice either: the block hides entirely. One dub and
       nothing else — the block stays, but stops being a menu: its name is
       written next to it anyway, and a list of one row collects clicks for
       nothing.

       "And nothing else" here is not for decoration. When there are no
       subtitles among the variants, a row saying "Original with subtitles"
       is added to the menu, and then opening it becomes essential —
       otherwise at source A, which has exactly one dub, subtitles could not
       be reached at all. That is exactly the mistake that came out: the
       button was switched off while the row under it was left. */
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
      /* "Subtitles" is not another dub, it is another way to watch: the
         Japanese track with text over it. The difference must be visible
         before the click, not after — "as text" did not say that. */
      if (d.sub) {
        /* We write the language of the text too. Every subtitle track the
           sources give is Russian — a live check found not one English
           variant. To a person who switched the site to English it matters
           to know that BEFORE the click, not after the first minute. */
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
    /* This source has no subtitles — we offer to look at others.

       Without this row they were unreachable: the default source, source A,
       gives one dub of its own and never gives subtitles, while next door,
       at source C, they lie for almost everything. A person opening any
       title never saw subtitles in principle — and did not know they exist
       anywhere. */
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

    /* The list of sources.

       All eight used to stand here in a row. Half of them do not know this
       title at all: you switch — and get a banner saying "this anime is not
       at this source". A list where half the rows lead to a dead end forces
       people to go through them by hand to find out what the site can find
       out itself.

       Then such sources simply started disappearing from the list — and
       that turned out no better. A vanished row explains nothing: a person
       sees seven items instead of eight and does not know where the eighth
       went or whether it was ever there. A silent disappearance reads as a
       fault.

       Now the list is laid out in three parts with real headings, and
       nothing vanishes. Where the title exists — ordinary buttons. Where it
       definitely does not — visible, but grey and unclickable, captioned
       "not here". Those we did not get to — separately, honestly. */
    var ss = $('m-s');
    ss.textContent = '';
    ss.appendChild(A.el('div', 'mh', A.t('Откуда берётся видео', 'Video source')));
    if (!sources.length) {
      ss.appendChild(A.el('div', 'mnote', A.t('список загружается…', 'loading…')));
    }

    /* A heading for a part of the list: a line, an icon and a caption. In
       its place there used to be the same class as the "many dubs" caption
       inside the buttons — the heading and the contents looked alike, and
       the division did not read at all. */
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
        /* There is nowhere to click: we have already asked and know it is
           empty there. The button stays visible but switched off — that is
           the answer. */
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
      /* We have not asked yet — we show everything as it is. The check
         will run when the menu is opened: until then nobody needs it, while
         it costs seconds. */
      sources.forEach(function (src) { ss.appendChild(sourceButton(src, false)); });
    } else {
      var here = [], gone = [], unknown = [];
      sources.forEach(function (s) {
        if (where.here.indexOf(s.id) >= 0) here.push(s);
        else if (where.checked.indexOf(s.id) >= 0) gone.push(s);
        else unknown.push(s);
      });
      /* The source open right now cannot be in "not here": we are
         watching from it this very second. Such a discrepancy happens when
         the source has its own name for the title and it did not reach the
         threshold. */
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

  /* Moving to another source: we look for this same title there by name */
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
        /* We take the most similar name, not simply the first */
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

  /* menus in the panel */
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

  /* The button on the banner opens that very menu and starts the check at
     once: the person does not need to know it is called "Source" and lives
     under the player. */
  $('w-notice-go').addEventListener('click', function (e) {
    e.stopPropagation();
    document.querySelectorAll('.menu').forEach(function (m) { m.classList.remove('show'); });
    $('m-s').classList.add('show');
    $('m-s').scrollIntoView({block: 'nearest', behavior: 'smooth'});
    askWhere();
  });

  /* We ask who has the title exactly once, and exactly when the source
     menu is opened. The answer is cached on the server, so opening it again
     costs nothing, but even the first time must not slow the page load
     down: before the menu is opened nobody needs that knowledge. */
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
        /* It did not work out — the menu stays as it was, with every
           source. A full list is worse than a sorted one, but better than
           an empty one. */
        where.asked = false;
        note.remove();
      });
  }
  document.addEventListener('click', function () {
    document.querySelectorAll('.menu').forEach(function (m) { m.classList.remove('show'); });
  });

  /* ------------------------------------------------------------------ */
  /* Saving the second. A guest has nowhere to save — the server will not  */
  /* let them, so we do not even try and do not frighten them with an error. */
  /* ------------------------------------------------------------------ */
  function canSave() { return A.me && A.me.role !== 'guest'; }

  function payload(watched) {
    /* On the shelf, "which episode you stopped at" and "how many there are
       in total" lie side by side, and they must be in the same units. We
       stopped at an episode number — so "in total" must be the last number
       too, not the count of rows in the list.

       The count used to be put here, and for "Наруто" that gave
       total_eps: 131 with watched_ep: 370 — "370/131" on the card and a bar
       that ran off the edge. And the "reached the end" check compared the
       episode number with the count: 370 >= 131, that is, the VERY FIRST
       episode marked closed the whole title as finished. */
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

  /* The "Mark as watched automatically after 90% of an episode" setting
     had been on the account page from the very beginning, was saved to the
     database — and did nothing: nobody in this file read it. Below it
     finally works. markedThisEpisode is needed so the mark goes out once
     per episode rather than on every frame after ninety percent. */
  var markedThisEpisode = false;

  /* The automatic mark at 90% of an episode now simply works. It used to
     be a setting on the account page — but there is no reason to switch it
     off: it is exactly what people expect of a player. A setting nobody
     touches only piles up code and space in the database. */
  function autoMarkEnabled() { return true; }

  function autoNextEnabled() {
    var sw = $('w-autobox') && $('w-autobox').querySelector('.sw');
    return !!sw && sw.getAttribute('aria-checked') === 'true';
  }


  /* ================================================================== */
  /* Actual playback                                                    */
  /* ================================================================== */
  var video = $('video');
  var hls = null;
  var saveTimer = null;
  var started = false;
  /* What we remove the previous address's handlers with — see attach() */
  var mediaAbort = null;

  function showLoading(on) { $('loadwrap').hidden = !on; }

  function chrome(on) {
    /* We hide the splash and the captions once the video has started */
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

  /* Only an address that has already passed safeUrl in currentUrl() gets here */
  function attach(rawSrc, startAt) {
    var safeSrc = A.safeUrl(rawSrc);   // повторная проверка: дешевле, чем ошибка
    if (!safeSrc) { fail(A.t('Плеер не дал ссылку на видео', 'The player returned no video link')); return; }
    showLoading(true);
    fail('');
    /* A full reset: without it, moving from a stream to a plain file
       leaves the previous source in place and the old video plays. */
    if (hls) { hls.destroy(); hls = null; }
    video.pause();
    video.removeAttribute('src');
    try { video.load(); } catch (e) { /* пустой src — это нормально */ }

    /* The previous address's handlers are removed before the new ones are
       set. They used to be hung with {once:true} on every switch of
       episode, dub and quality — and {once:true} removes a handler only
       WHEN IT HAS FIRED. Of the two, one always fired and the second stayed
       hanging forever. Over an evening of a series dozens of them piled up,
       and an old "error" from an address long since discarded could pop up
       over a video that was playing perfectly well. */
    if (mediaAbort) mediaAbort.abort();
    mediaAbort = (typeof AbortController === 'function') ? new AbortController() : null;
    var opt = mediaAbort ? { signal: mediaAbort.signal } : undefined;

    /* Seeking to the saved second.

       There was a mistake here because of which the whole application's
       main idea did not work. The seek stood inside ready() under the
       condition isFinite(video.duration) — and for m3u8 streams that
       condition is almost always false: ready() is called on the "manifest
       parsed" event, and by that moment the browser does not yet know the
       video's length. The condition quietly failed, no seek happened, and
       the episode started from zero. The page meanwhile honestly wrote
       "continue from 08:10" — and lost it right away. The same happened
       when switching quality or dub in the middle of an episode.

       Now we wait for the moment the length becomes known and seek then.
       The condition is kept: without a length a seek is meaningless. */
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
        /* The browser may not allow autoplay — then we simply wait for a click */
        chrome(true);
      });
    }

    var isStream = /\.m3u8(\?|$)/i.test(safeSrc);
    if (isStream && window.Hls && window.Hls.isSupported()) {
      hls = new window.Hls({ maxBufferLength: 30, enableWorker: true });
      hls.loadSource(safeSrc);
      hls.attachMedia(video);
      hls.on(window.Hls.Events.MANIFEST_PARSED, ready);
      /* We count recovery attempts: without a counter, on a dead link the
         player goes into an endless loop and heats the processor for
         nothing. */
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
      /* Safari can do streams itself, plain mp4 plays everywhere */
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

  /* ================= clicks on the picture =================

     A click on the screen pauses and unpauses — that is how any player
     works, and it was exactly what was missing: you click the picture and
     it does not react, and you have to aim at a small button at the bottom.

     A double click expands to full screen and back. So that one does not
     get in the other's way, the pause fires not at once but after a quarter
     of a second: if a second click arrives within that time, there will be
     no pause — only the expansion. */
  var tapTimer = null;

  function flash(paused) {
    var box = $('tapflash');
    var icon = $('flash-icon');
    if (!box || !icon) return;
    /* We show what happened: started — the "play" sign, stopped — the two
       bars of a pause. */
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

  /* ================= the panels hide while watching =================

     While the video runs the panels go away and do not cover the picture.
     Any movement of the mouse brings them back for three seconds. On pause
     they stay: the person stopped deliberately — most likely in order to
     click something. */
  var idleTimer = null;
  var player = $('player');

  function wake() {
    if (!player) return;
    player.classList.remove('idle');
    clearTimeout(idleTimer);
    if (!started || video.paused) return;
    /* We hide the panels only in full screen.

       In the ordinary mode the panel lies UNDER the picture and covers
       nothing — there is nothing to hide it from. And it looked like this:
       you start an episode, three seconds later the buttons and the seek
       bar disappear, and an empty strip is left in their place. Empty space
       where buttons have just been reads as a breakdown. */
    if (!document.fullscreenElement && !player.classList.contains('fs')) return;
    idleTimer = setTimeout(function () {
      /* An open menu is a sign that the person is choosing right now. The
         panel must not be hidden from under their hand. */
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

  /* The icon on the button always shows what it will do. It used to stay
     "play" forever, even while the video was running. */
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
  /* Full screen.

     There used to be a call here with no handling of a refusal:

         else if (box.requestFullscreen) box.requestFullscreen();

     requestFullscreen returns a promise, and it can be rejected — on an
     iPhone only the video element itself can go full screen, and inside an
     embedded window the page policy forbids it outright. The check
     `if (box.requestFullscreen)` does not catch that: the method exists, it
     simply refuses. So the button silently did not work, while an unhandled
     error piled up in the console.

     Now on a refusal we try to show the video itself full screen — exactly
     what works on a phone — and if even that is not allowed, we tell the
     person instead of staying silent. */
  /* Expands and collapses. The button used to be able only to expand,
     while people left with the Escape key — which not everyone knows. */
  function toggleFullscreen() {
    if (document.fullscreenElement) {
      var out = document.exitFullscreen();
      if (out && typeof out.catch === 'function') out.catch(function () {});
      return;
    }
    enterFullscreen();
  }

  function enterFullscreen() {
    /* We expand the whole player, not the picture alone: the seek bar and
       the buttons live outside `#screen`, and in full screen they simply
       would not be there — no pause, no seeking, no way out. */
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

  /* The button's icon shows what it will do: expand or collapse. */
  document.addEventListener('fullscreenchange', function () {
    var icon = $('full-icon');
    var on = !!document.fullscreenElement;
    /* A class, not only the `:fullscreen` pseudo-class. Not everyone
       supports the pseudo-class: on an iPhone the video expands its own
       way, and a layout based on `:fullscreen` would not work there at all.
       A class works the same everywhere. */
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

  /* the time bar follows the video */
  video.addEventListener('timeupdate', function () {
    st.position = Math.round(video.currentTime);
    st.duration = video.duration || 0;
    /* While the bar is being dragged it obeys the finger, not the video.
       Without this check the handle would jerk back on every frame — that
       is, twenty-five times a second it would jump out from under the
       finger to where the video actually is. */
    if (!dragging) {
      paintPosition();
      $('w-cur').textContent = A.mmss(st.position);
    }
    if (st.duration) $('w-total').textContent = A.mmss(st.duration);

    /* We save no more than once every 15 seconds: no reason to bother the server on every frame */
    if (!saveTimer) {
      saveTimer = setTimeout(function () { saveTimer = null; save(false); }, 15000);
    }

    /* The automatic mark at ninety percent — exactly what the account page promises */
    if (!markedThisEpisode && autoMarkEnabled() && st.duration > 0 &&
        video.currentTime / st.duration >= 0.9) {
      markedThisEpisode = true;
      save(true);
    }
  });

  video.addEventListener('play', function () { chrome(false); });
  video.addEventListener('ended', function () {
    /* If the automatic mark has already fired at ninety percent, there is
       no need to write to the watch journal a second time — otherwise one
       episode would count as two and the year's totals would be inflated. */
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
  /* keepalive: without it the browser cancels this request along with the
     tab, and the second the person stopped at is lost.
     We cancel the deferred save at the same time: otherwise a second
     identical request follows the departure from the page — an extra write
     to the database and an extra reason for the rate limiter. */
  window.addEventListener('beforeunload', function () {
    clearTimeout(saveTimer);
    saveTimer = null;
    save(false, { keepalive: true });
  });

  /* ================= seeking on the bar =================

     There used to be one click handler here: poke it and it jumps. Dragging
     the handle was impossible altogether. You press, you move — the bar
     stands still, the time does not change, and the only way to tell where
     you will land is to let go and look. Missed — poke again.

     Now the bar drags. While you hold it, the bar and the time follow the
     finger, while the video itself seeks once — when you let go. Seeking on
     every movement is not allowed: the browser pulls a chunk of the stream
     anew on every such jump, the picture stalls, and seeking turns into
     jerking.

     It works with a mouse and with a finger: pointer events cover both at
     once, and setPointerCapture keeps the pointer on the bar even if the
     finger has gone outside it — otherwise the handle was lost halfway
     through a movement the moment it slid up a little. */
  var track = $('track');
  var dragging = false;

  /* Where the point is aimed. Left of the bar is zero, right is one;
     without holding it that is impossible, and while holding, people take
     the finger past the edge constantly. */
  function trackShare(clientX) {
    var r = track.getBoundingClientRect();
    if (!r.width) return 0;
    return Math.max(0, Math.min(1, (clientX - r.left) / r.width));
  }

  /* Show the position without touching the video. */
  function paintSeek(p) {
    $('pos').style.width = (p * 100) + '%';
    $('knob').style.left = (p * 100) + '%';
    var known = st.duration || 0;
    if (known) $('w-cur').textContent = A.mmss(Math.round(known * p));
  }

  /* Let go — now we move. */
  function applySeek(p) {
    if (started && st.duration) {
      video.currentTime = st.duration * p;
      return;
    }
    /* The video has not been started yet. Then the bar chooses not the
       current time but the place watching will begin from: the duration is
       unknown, so we take an average twenty-three-minute episode. */
    st.position = Math.round((st.duration || 1380) * p);
    paintSeek(p);
    $('w-cur').textContent = A.mmss(st.position);
    save(false);
  }

  if (track) {
    track.addEventListener('pointerdown', function (e) {
      /* The primary button only: the right one calls up the browser's
         menu, and seeking with it is a surprise. */
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
    /* A cancel arrives when the pointer has been taken away: a system
       gesture, switching windows, a call on the phone. Without this line
       the bar would stay "held" forever and stop obeying. */
    track.addEventListener('pointercancel', function () {
      dragging = false;
      track.classList.remove('dragging');
      paintPosition();
    });

    /* The bar is reachable from the keyboard too: arrows for five
       seconds, Home and End to the beginning and the end. It used not to be
       possible even to tab onto it. */
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

  /* The bar's position by the video's current time. A separate function,
     because it is called from two places: after every video frame and after
     a cancelled drag. */
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

    /* The "play the next one automatically" switch always stood in the on
       position, whatever the person chose on the account page: the saved
       setting simply did not reach here. Now it does. */
    var swAuto = $('w-autobox') && $('w-autobox').querySelector('.sw');
    if (swAuto) {
      var wantAuto = !me.settings || me.settings.autonext !== false;
      swAuto.setAttribute('aria-checked', wantAuto ? 'true' : 'false');
    }

    if (me.role === 'guest') {
      $('w-autobox').style.opacity = '.5';
      $('w-autonote').textContent = A.t('гостю не сохраняем', 'not saved for guests');
    }
    /* The source may not have arrived in the address — from a friend's
       link, for instance, or from a bookmark. Then we take the main one for
       the site's language: by this moment the language has been applied,
       while before it, it is unknown. */
    if (!SOURCE) SOURCE = A.defaultSource();

    /* The list of sources arrives by its own request. Having waited for
       it, we redraw the menu — if it has been drawn by then. */
    A.api.get('/api/sources?lang=' + encodeURIComponent(A.langCode()))
      .then(function (rows) {
        sources = rows || [];
        if (st.dubs.length) renderMenus();

        /* A source of another language — we do not open it.

           A title could have landed in the library from an
           English-language source, and the site was later switched to
           Russian. Then a link from the library opened "Магическая битва"
           with the English dub: the site is Russian, the sound is English.
           That is exactly the confusion the sources were split by language
           for.

           We go to a source of our own language. The page will reload with
           the new address — the same title, the same episode. */
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

    /* We load the description on a separate thread rather than in the
       common chain. It depends on neither episodes nor players — while in
       the chain it fell along with them: for a title that is not at the
       source, a dash stayed under the player instead of a description,
       although the catalogue knows about it. */
    loadAbout();
    /* And the list of parts too: it depends on neither episodes nor
       players. In the common chain everything after a source's refusal
       simply did not run — and for a title that is not at the source the
       description went along with it, and everything else the catalogue
       knows about it. */
    loadRelated();

    loadItem()
      .then(function () {
        /* We draw the header at once, before the episode list. We already
           have the name, the cover and the genres — they arrived in the
           page's address or from the shelf. renderHead used to be called
           only inside loadEpisodes, and if the source did not answer the
           person was left on a page with a dash instead of a name: there
           was no telling even what was open. */
        renderHead();
      })
      .then(loadEpisodes)
      .catch(function (err) {
        /* The episode list did not arrive at all. The reason is almost
           always one of two: the title is not at this source, or it has
           only just come out and has not been posted yet. Both are cured by
           changing the source, so we say that outright rather than showing
           a bare error code. */
        notice(A.t('На «' + SOURCE + '» этого аниме нет',
                   'Not on "' + SOURCE + '"'),
               A.t('Либо оно только вышло и его ещё не выложили, либо его тут ' +
                   'просто нет. Посмотрим, у кого оно есть.',
                   'Either it just came out and is not posted yet, or it is not ' +
                   'here at all. Let us see who does have it.'),
               true);
        /* The header would have stayed on "Loading episodes…" forever:
           renderHead is drawn before going for the list, and after a
           refusal nobody redraws it. The word "loading" where loading has
           already ended in failure is a promise that will not come true. */
        $('w-epno').textContent = A.t('Серии не загрузились', 'Episodes did not load');
        /* No episodes means no dubs either. The "Dub" menu never got
           here and went on hanging with a dash: a list that does not exist
           pretended to be empty instead of disappearing. */
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
        /* If the banner has already explained everything — we stay
           silent. Otherwise we show the ordinary error line. */
        if (!noticeShown()) fail(err.message);
      });
  });
})();
