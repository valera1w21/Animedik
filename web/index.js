/* The main page: sign-in, the shelf, search, roulette, account page. */
(function () {
  'use strict';
  var A = window.App;
  var $ = function (id) { return document.getElementById(id); };

  var FACTS = [
    ['Кадры рисуют не на каждую секунду: чаще всего 8 или 12 рисунков в секунду, а не 24.', 'как это делается'],
    ['Опенинг длится ровно 90 секунд не случайно — под это время верстают эфирную сетку.', 'почему полторы минуты'],
    ['Синий фильтр на ночных сценах — наследие плёнки: снимали днём и затемняли.', 'приём из кино'],
    ['Один эпизод в 23 минуты — это примерно три тысячи отдельных рисунков.', 'сколько там работы'],
    ['Фоны часто рисуют акварелью на бумаге и только потом сканируют.', 'почему фоны такие']
  ];

  /* The search field has been taken out of here: it was created but never
     read and never written — search results live right in their own section. */
  var state = { items: [], tab: 'open' };
  /* The default source comes from the site's language: Russian and English
     have different ones, and substituting one for the other is not allowed.
     If the chosen one is silent, the server will walk the rest — but only
     those of the same language. */
  function currentSource() { return A.defaultSource(); }

  /* =============== sign in =============== */
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

  /* The switches on the account page.

     These used to be ordinary <input type="checkbox"> boxes. They looked
     alien — the browser draws them in its own style, which suits nothing on
     the site — and behaved differently from the same switch on the watch
     page. Now it is the same everywhere: a button with role="switch", as
     accessibility markup requires. */
  function getCheck(id) {
    var b = $(id);
    return !!b && b.getAttribute('aria-checked') === 'true';
  }

  function setCheck(id, on) {
    var b = $(id);
    if (b) b.setAttribute('aria-checked', on ? 'true' : 'false');
  }

  /* One handler for both switches: a click flips the state and tells
     whoever is subscribed to it. */
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
      /* setMe rather than applyLook: otherwise the appearance would be
         applied while "who am I" stayed empty until the first page reload. */
      A.setMe(r.me);
      showApp(r.me);
    }).catch(function (err) {
      /* The server says "the password is right, the code is needed" with a
         separate message. We show the field and ask only for the code —
         there is no reason to type the password again at this moment. */
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

  /* =============== the shelf =============== */
  function loadLibrary() {
    return A.api.get('/api/library').then(function (r) {
      state.items = r.items || [];
      render();
      fixMissingPosters();
      /* We check for new episodes at once rather than on a click:
         otherwise you learn about them only if you think to press the bell
         yourself. The source's answer is cached for three hours, so repeat
         visits are almost free. */
      loadUpdates();
    }).catch(function () {
      state.items = [];
      render();
    });
  }

  /* Fills in covers for the titles that have none.

     The picture arrives together with the card from the search. But a title
     can land in the library without one — from a link, from a bookmark, or
     saved by an older version. Such a title used to keep a grey stub
     forever: nobody ever asked for the cover again.

     We ask for no more than a few at a time: each is a call to somebody
     else's site, and dumping the whole list on them at once is not allowed.
     The rest will come along on the next visit. */
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

  /* Two tabs: what is unfinished and what is finished.
     Everything except "done" counts as unfinished — including put aside and
     abandoned: from the "watched it or not" point of view they are the same. */
  function visible() {
    return state.items.filter(function (it) {
      return state.tab === 'done' ? it.status === 'done' : it.status !== 'done';
    });
  }

  /* There is one order: what was touched last is on top. The sorting
     setting has been taken off the account page — with one list and no tabs
     it is redundant. */
  function sortItems(list) {
    return list.slice().sort(function (a, b) { return b.updated_at - a.updated_at; });
  }

  /* The card is assembled out of nodes rather than by gluing strings:
     the name arrives from somebody else's site and never gets inside HTML. */
  function watchHref(it) {
    /* We drag the cover, the year and the genres along with us: if the
       title is not in the library yet, the watch page has nowhere else to
       take them from, and the card would later be saved without a picture. */
    return '/watch?key=' + encodeURIComponent(it.key) +
           '&source=' + encodeURIComponent(it.source || '') +
           '&title=' + encodeURIComponent(it.title || '') +
           '&poster=' + encodeURIComponent(it.poster || '') +
           '&year=' + encodeURIComponent(it.year || '') +
           '&genres=' + encodeURIComponent(it.genres || '') +
           '&total=' + encodeURIComponent(it.total_eps || 0);
  }

  /* Removing a title from your own shelf.

     The delete endpoint has been on the server from the very beginning,
     while there was nowhere to press it: there was no way at all to remove
     a title from the interface. Open it once — and it stayed in the library
     forever. The button lives on the card itself, because the decision to
     "remove" comes exactly there, while looking at the list. */
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

  /* inLibrary=false for cards from the search: there is nothing to remove
     from there, and the button would only be confusing. */
  /* A title card.

     The extremes here were swapped twice. At first the card held
     everything at once: a counter, the second you stopped at, a bar, a
     "continue" badge, the line "Watching · 502 episodes" — five numbers on
     one picture, no longer a card but a table. Then it was all taken away,
     and there was no telling where you had stopped.

     What is left is exactly what answers the question "where am I": how
     many episodes out of how many, and a bar under the cover. One number
     and one line.
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

    /* We show "how many in total" only if that number does not contradict
       where you stopped. Sources sometimes hand over fewer episodes than
       have already been watched: the title was re-posted in pieces, or it
       is another title altogether. "2 / 1" on a card looks like a
       breakdown, though it is not the site that is broken. In that case we
       show simply the episode number. */
    var knownTotal = total > 0 && total >= seen;

    if (done) {
      pic.appendChild(A.el('span', 'done-mark', A.t('Досмотрено', 'Finished')));
    } else if (seen > 0) {
      pic.appendChild(A.el('span', 'count', knownTotal ? seen + ' / ' + total
                                                       : A.t('серия ', 'ep. ') + seen));
    }

    /* The bar under the cover. We draw it only when there is something to
       count from: a bar with no known episode count would be guessing. */
    if (!done && knownTotal && seen > 0) {
      var line = A.el('div', 'line');
      var lf = A.el('i');
      lf.style.width = Math.max(3, Math.min(100, seen / total * 100)) + '%';
      line.appendChild(lf);
      pic.appendChild(line);
    }

    wrap.appendChild(pic);
    a.appendChild(wrap);
    a.appendChild(A.el('h3', null, it.title || A.t('Без названия', 'Untitled')));
    return a;
  }

  /* =============== boxes ===============

     The library holds separate records: every part, every source is its own
     row. "Ван-Пис" came out as four in a row: the series from one source,
     the same from another, a film and one more with no cover. Four
     identical names in a row are not a list, they are a mistake that looks
     like a list.

     So records are gathered into a box — one per story. On the outside is
     what you need to know without opening it: where you stopped and whether
     you finished. Inside are all the parts, both your own and those you
     have not watched yet. */

  /* The name without tails, by which parts recognise each other.
     "Ван-Пис", "Ван-Пис. Фильм", "Ван-Пис: Остров Рыболюдей" and
     "Ван-Пис 2" are one story. */
  function baseName(title) {
    var t = (title || '').toLowerCase().replace(/ё/g, 'е');
    t = t.replace(/\[[^\]]*\]|\([^)]*\)/g, ' ');   // счётчики серий в скобках
    t = t.split(' / ')[0];                          // «Наруто / Naruto»
    t = t.split(/\s*[:—–]\s*/)[0];                  // подзаголовок после двоеточия
    t = t.replace(/[^\wа-я\s]+/gi, ' ');
    /* A tail of "2", "season 3", "film", "part 2" is not another story,
       it is its continuation. */
    for (var i = 0; i < 2; i++) {
      t = t.replace(/\s+(?:\d+|[ivx]+)?\s*(?:сезон|season|часть|part|фильм|movie|тв|tv|ova|ona|спешл|special)\s*\d*\s*$/i, '');
      t = t.replace(/\s+\d+\s*$/, '');
    }
    return t.replace(/\s+/g, ' ').trim() || (title || '').toLowerCase();
  }

  /* The story's name from the name of one of its parts: the same cuts as
     in baseName, but keeping the original look. baseName lowercases
     everything and throws out punctuation — for comparison that is just
     right, but "ван пис" must not be shown to a person. */
  function boxTitle(title) {
    var t = (title || '').trim();
    var cut = t.split(/\s*[:—–]\s*/)[0].trim();
    for (var i = 0; i < 2; i++) {
      cut = cut.replace(/[.,]?\s+(?:\d+|[IVX]+)?\s*(?:сезон|season|часть|part|фильм|movie|тв|tv|ova|ona|спешл|special)\s*\d*\s*$/i, '').trim();
      cut = cut.replace(/\s+\d+$/, '').trim();
    }
    /* If nothing meaningful is left after the cuts — we keep what there
       was: a long name beats a stump. */
    return cut.length >= 2 ? cut : t;
  }

  /* Gathers records into boxes, keeping the "what was touched last is on
     top" order. */
  function boxes(list) {
    var order = [];
    var by = {};
    list.forEach(function (it) {
      var k = baseName(it.title);
      if (!by[k]) { by[k] = {key: k, items: [], updated_at: 0}; order.push(k); }
      by[k].items.push(it);
      if (it.updated_at > by[k].updated_at) by[k].updated_at = it.updated_at;
    });
    /* A second pass: a box whose name begins with another's moves into it.

       The first pass cuts tails off by a list of words — "season", "film",
       a number, a subtitle after a colon. But sequels are named otherwise
       too: "Наруто Ураганные хроники" is neither "Наруто 2" nor "Наруто:
       something", and there is no listing every such tail. Something else
       is visible, though: the name begins with the first part's name. That
       is enough.

       We compare at a word boundary, otherwise "Бета" would drag "Бетани"
       in with it, and "Атака" everything starting with those letters. */
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
      /* Parts inside a box are in the order they were touched: what you
         stopped at is on top. */
      box.items.sort(function (a, b) { return b.updated_at - a.updated_at; });
      box.last = box.items[0];
      /* The box's name is the shortest of the names: for "Ван-Пис" and
         "Ван-Пис: Остров Рыболюдей" the story is called by the first. */
      /* The box's name is the shared beginning of the names, not the
         shortest of them.

         The difference shows where the very first part is missing from the
         library. For "Ван-Пис" there lay a film, two OVAs and a couple of
         specials, but not the series itself — and the box was called
         "Ван-Пис. Фильм". Formally that really is the shortest name, while
         by sense the story is called "Ван-Пис". */
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

  /* The box card. */
  function boxNode(box) {
    /* The class is boxcard, not box: the short `box` is already taken by
       the sign-in form, and the card quietly took over its appearance — a
       background, a border, thirty pixels of padding and even a width
       limit. The second such collision in a row after `bar`: short names in
       a shared stylesheet are almost always somebody's already. */
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

    /* How many parts are in the box — right on the cover, like the number
       of discs on the spine of a box set. One part — nothing to write. */
    if (box.items.length > 1) {
      pic.appendChild(A.el('span', 'discs',
                           A.say(box.items.length, 'part')));
    }
    if (box.done) pic.appendChild(A.el('span', 'done-mark', A.t('Досмотрено', 'Finished')));
    wrap.appendChild(pic);
    a.appendChild(wrap);

    a.appendChild(A.el('h3', null, box.title || A.t('Без названия', 'Untitled')));

    /* The caption under the name: where you stopped.

       The bar used to lie on the cover — and that turned out badly. Covers
       are all different: on a light one it was lost, on a dark one it cut
       the eye, on a busy one it simply could not be seen. The same bar
       looked different on every card.

       Now it is at the bottom, on the card's own even background: always
       the same, whatever is drawn on the poster. */
    /* The bottom line: the watch bar and the episode number.

       Pressed to the bottom of the card rather than following straight
       after the name. Names are of different lengths — one on a line,
       another on two — and the line that follows ends up at a different
       height on every card: the row looks scattered. Pressed to the bottom,
       it is on one line for all of them.

       The bar used to lie on the cover, and that was worst of all: covers
       are all different, and on a light one the bar was lost, on a dark one
       it cut the eye, on a busy one it could not be seen at all. */
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
      /* How many episodes there are in total the source does not always
         know. A bar of "unknown out of unknown" would be lying — then just
         the number. */
      foot.appendChild(A.el('span', 'num only', seen > 0
        ? A.t('серия ', 'ep. ') + seen
        : A.t('ещё не начинали', 'not started')));
    }

    a.appendChild(foot);

    /* A click opens the box rather than the player straight away: there
       are several parts in it, and the person should choose. If there is
       one part — there is nothing to open, we go straight to the player. */
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

  /* An open box: first what is already in the library, then everything
     else that came out for this story.

     Your own parts are shown at once, with no network: they are in hand
     already, and there is no reason to wait for the catalogue in order to
     see your own list. The full list of parts arrives afterwards and is
     appended below. */
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

    /* The other parts come from the catalogue. The names of your own
       records are taken already, so out of the full list we show only what
       is not in the library yet: otherwise the same part would stand
       twice. */
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

  /* We compare names as roughly as we gather boxes: at the source it is
     "Наруто Ураганные хроники", at the catalogue "Наруто: Ураганные
     хроники" — one and the same. */
  function baseKey(title) {
    return (title || '').toLowerCase().replace(/ё/g, 'е')
      .replace(/[^\wа-я\s]+/gi, ' ').replace(/\s+/g, ' ').trim();
  }

  /* A row for your own part: leads straight to the player, with progress. */
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

  function render() {
    var list = boxes(sortItems(visible()));
    var grid = $('grid');
    grid.textContent = '';
    list.forEach(function (box) { grid.appendChild(boxNode(box)); });

    /* We count stories, not records: four parts of "Ван-Пис" are one title
       in the library, not four. */
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

  /* =============== there are no filters any more ===============

     Genre and year buttons used to live here. They picked among what is
     ALREADY in your library — that is, they helped find your own, but did
     not help find anything new at all. The expectation from such a panel is
     exactly the opposite: if there is a "Genre" and a "Years", then anime
     can be searched by them.

     There is nothing to make a real genre search with: all eight sources
     can do exactly two things — text search and a list of what is airing
     now. Not one of them has a selection by genre.

     A panel that promises what it does not do is worse than none, so it has
     been removed entirely: markup, styles, state and handlers. In its place
     in the header is the roulette, which used to hide inside it. */

  /* =============== tabs =============== */
  $('tabs').addEventListener('click', function (e) {
    var a = e.target.closest('a[data-tab]');
    if (!a) return;
    e.preventDefault();
    state.tab = a.dataset.tab;
    $('tabs').querySelectorAll('a').forEach(function (x) { x.classList.remove('on'); });
    a.classList.add('on');
    render();
  });

  /* =============== search ===============

     Search works in two steps, and that is the main thing to know about it.

     Step one is the catalogue. For "наруто" one "Наруто" card comes back,
     not twenty-one lines with the second season, a film about Boruto and a
     special about a sports festival all jumbled together. It used to be
     exactly the latter: the query went straight to the video site, and that
     answered with what it considers similar. At source A, for "наруто",
     "Наруто" itself is not there at all — only "Ураганные хроники" and
     "Боруто".

     Step two: clicking a card opens the list of all the franchise's parts
     by year — where to start, what comes next, what can be skipped. We look
     for a link to the video only once a part is chosen.

     If the catalogue is silent, search falls back to the old way — straight
     at the sources. Worse, but better than an empty screen. */
  var searchTimer = null;
  $('q').addEventListener('input', function () {
    clearTimeout(searchTimer);
    var q = this.value.trim();
    if (q.length < 2) { $('search-wrap').hidden = true; return; }
    searchTimer = setTimeout(function () { doSearch(q); }, 500);
  });
  /* Enter in the search box used to do nothing: the field stands outside a
     form, and all one could do was wait out the half-second delay. Everyone
     has the habit of pressing Enter, and silence in reply looks like a
     breakdown. */
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
        /* catalog:false — the catalogue did not answer. That is not
           "nothing found": there is still somewhere to search, only worse. */
        if (!res.catalog) return doSourceSearch(q);
        grid.textContent = '';
        if (!rows.length) {
          /* The catalogue answered and knows no such thing. The sources
             sometimes have what the catalogue does not — worth asking them
             too. */
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

  /* A franchise card. Outwardly it is the same card as in the library, but
     it leads not to the watch page but to a list of parts: which of
     "Наруто"'s twenty-nine parts is wanted is not decided here. */
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
    /* There is deliberately no score on the card. A badge saying "8.0"
       used to hang here — somebody else's average mark, given by people you
       do not know. It does not answer the question "what to watch", while
       it takes up room and catches the eye first. What the story is and
       when it came out does answer it. */
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

  /* The old search — straight at the sources. It stays as a fallback path:
     it finds what the catalogue does not have, and it is what keeps the
     site working when the catalogue is down. */
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
        /* We show who had it: sources go down from time to time, and it
           is useful for a person to see where the answer came from.
           We count exactly the cards shown, not everything the source sent:
           the line used to say "34 results" while twenty-four lay on the
           screen, and the difference looked like a loss. */
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

  /* =============== parts of a franchise =============== */
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
    /* On the second line is the title's second name, whichever was first. */
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
        /* Which part is the "start" is the server's decision: it is not
           always the first row of the list. For "Ван-Пис" an unrelated OVA
           came out before the series, and ordering by year puts it on top. */
        parts.forEach(function (p) { box.appendChild(partNode(p, !!p.main)); });
      })
      .catch(function (err) {
        box.textContent = '';
        $('fr-note').textContent = '';
        A.showError($('fr-err'), err.message);
      });
  }

  /* One row of the list of parts.

     The order in the list is by year, so the first row is where people
     start. It is the one marked: without the mark a person who opened
     "Наруто" sees twenty-nine identical rows and again does not know which
     to press — that is, exactly the trouble all of this was done for. */
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
      /* The space is a separate node: without it the name and the mark
         stick together into "Нарутоstart" when copied and in a screen
         reader's speech. By eye it is invisible — the gap is drawn by the
         style. */
      nameRow.appendChild(document.createTextNode(' '));
      nameRow.appendChild(A.el('span', 'first', A.t('начало', 'start here')));
    }
    tx.appendChild(nameRow);

    var bits = [];
    bits.push(p.year ? String(p.year) : A.t('дата неизвестна', 'no date yet'));
    var pKind = A.kindName(p);
    if (pKind) bits.push(pKind);
    /* "Film · 1 episode" is not how people talk about films. The counter
       is needed where there are many episodes: for a series it answers the
       question "is this for long", for a feature film it answers nothing. */
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

  /* A part is chosen — we look for it at the video sources.

     The catalogue knows that "Наруто" exists and when it came out, but it
     has no links to episodes. Only the sources know those, and they know
     them under their own names. So there is a separate step here: the name
     turns into a pair, "source + the title's number", with which the player
     opens. */
  function openPart(p, row, go) {
    if (row.classList.contains('busy')) return;
    row.classList.add('busy');
    go.textContent = '…';
    A.showError($('fr-err'), '');

    /* How many episodes the catalogue promises — a hint for the server:
       for "Ван-Пис" one source posted seven episodes out of a thousand and
       something, and without that number there is nothing to tell a stub
       from a complete title by. */
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
            /* We take the cover from the catalogue: it is larger and has
               no captions over it, while at the sources there is sometimes
               only a stub. */
            poster: p.poster || r.poster || '',
            year: p.year || r.year || '',
            genres: r.genres || '',
            total_eps: p.episodes || r.episodes_total || 0
          });
        };
        /* The match was inexact — we ask. Quietly opening a similar name
           is worse than opening nothing: a person will watch half an
           episode before realising it is a different title. */
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

  /* =============== account page =============== */
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
    /* The "Save" button used to be hidden by the .pfoot.ro style alone.
       Hiding is not the same as switching off: a hidden button can still be
       pressed from the keyboard, and a guest got a curt "403" with no
       explanation in reply. The server will not let them through anyway,
       but there is no reason to frighten the person. */
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
  /* The common handler for switches that have none of their own.

     It used to be hung on ALL elements with the .sw class — including those
     that already have a handler of their own (sign-in by code, letters). A
     click on such a switch fired twice: first the common one flipped the
     state, then its own flipped it back. Outwardly the switch returned to
     its original position, while the handler received the value opposite to
     what the person chose: you press "switch on", and the site asks "switch
     off?".

     Now the common handler takes only those marked with data-k — that is,
     settings that simply store yes or no. */
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

  /* The language buttons' handler moved to the shared code: the buttons
     are now on every page, not only here. */
  /* Changing the language changes the anime names too, not only the
     captions: the library is drawn again. */
  document.addEventListener('langchange', function () {
    if (A.me) render();
    showNews();
  });

  /* Returns the value of the selected button, and a fallback if none is
     selected. This used to be document.querySelector(...).dataset directly.
     It was enough for the settings to hold a value no button corresponds to
     (the server, for instance, accepted any cover size from 120 to 280,
     while there are only three buttons) — and querySelector returned null,
     the call to .dataset fell over, and the account page stopped saving
     altogether, and silently at that. Now there is nothing to fall over. */
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

  /* =============== photo =============== */
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

  /* =============== administrator =============== */
  /* The README says accounts can be managed "with a console command or on
     the account page". Meanwhile the account page had only a "create"
     button: the switch-off, switch-on and delete endpoints have been on the
     server from the very beginning, while there was nowhere to press them.
     Below they appear. */
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
            /* Deletion takes away the account and all its data, so we
               ask for confirmation by login rather than a plain "are you
               sure". */
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

  /* =============== decide for me =============== */
  /* The roulette used to spin your own library — that is, it offered what
     you had already chosen once. There is not much sense in that: if you
     want someone to "decide for me", it is precisely because everything in
     your own library has grown stale.

     Now it takes a random anime from the open AniList catalogue (free, no
     keys). The request goes through our server rather than straight from
     the browser: otherwise every visitor's address would go to somebody
     else's site, and the answers could not be kept in a shared cache. */
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

    /* The button looks for what came up at our sources — the catalogue
       knows about anime, but has no video. */
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

  /* =============== new episodes =============== */
  /* What the bell does.

     It used to list everything you are watching and always glowed with a
     number. A notification that arrives constantly and about nothing stops
     being noticed within a week — that is worse than not having it.

     Now it shows exactly one thing: which saved titles have had a NEW
     episode since you last opened them. No new ones — the bell is empty and
     without a number. The server counts that: it asks the source for the
     number of the latest episode and compares it with what we remembered. */
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
    /* A guest has nothing to check: their library is always empty. */
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
        /* Opened means seen. We put out the notification for this title,
           otherwise it would glow until you watched up to the very latest
           episode. */
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

  /* =============== the administrator's announcement =============== */
  /* We remember the announcement in full, in both languages: switching
     language means redrawing the banner, and there is no reason to go to
     the server a second time for the same thing. */
  var news = {text: '', text_en: ''};

  function loadNews() {
    return A.api.get('/api/news').then(function (r) {
      news = {text: (r && r.text) || '', text_en: (r && r.text_en) || ''};
      showNews();
    }).catch(function () { news = {text: '', text_en: ''}; showNews(); });
  }

  function showNews() {
    var box = $('sitenews');
    /* In English we show the English version if the admin wrote one. If
       not — the Russian one: your own announcement in the wrong language is
       more use than an empty line where "the server restarts at 23:00"
       could have been. */
    var text = (A.langCode() === 'en' && news.text_en) ? news.text_en : news.text;
    if (!text) { box.hidden = true; return; }
    /* textContent, not markup: an announcement is written by a person, and
       if it were inserted as HTML, code could be put there (even by
       accident) that would run for everyone else. */
    $('sitenews-text').textContent = text;
    box.hidden = false;
    if ($('news-text')) $('news-text').value = news.text;
    if ($('news-text-en')) $('news-text-en').value = news.text_en;
    paintNewsCount();
  }

  /* How much more will fit. The limit exists both in the field and on the
     server, but a silent break at the three hundredth letter looks like
     swallowed text — better to show a counter in advance. */
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

  /* =============== sign-in by code, and letters =============== */
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
      /* We show the code only after the box is ticked. Until it is
         confirmed, sign-in by it is not switched on — this is still a draft
         of the setting. */
      A.showError($('twofa-err'), '');
      $('twofa-code').value = '';
      A.api.post('/api/me/2fa/start').then(function (r) {
        $('twofa-qr').src = r.qr;
        $('twofa-secret').textContent = r.secret;
        $('twofa-box').hidden = false;
      }).catch(function (err) {
        setCheck('s-2fa', false);
        A.tell(A.t('Не получилось', 'Did not work'), err.message);
      });
      return;
    }
    /* Switching off asks for the password: otherwise the protection is
       removed by anyone who sits down at an unlocked laptop. */
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

  /* =============== about the site =============== */
  var veil = $('veil');
  function openAbout(e) { if (e) e.preventDefault(); veil.classList.add('show'); }
  function closeAbout() { veil.classList.remove('show'); }
  $('btn-about').addEventListener('click', openAbout);
  $('foot-about').addEventListener('click', openAbout);
  $('btn-close').addEventListener('click', closeAbout);
  $('btn-close2').addEventListener('click', closeAbout);
  veil.addEventListener('click', function (e) { if (e.target === veil) closeAbout(); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') { closeAbout(); closeSet(); vroul.classList.remove('show');
      vcrop.classList.remove('show'); bellDrop.classList.remove('show'); }
  });

  /* =============== loading and notification =============== */
  function boot(done) {
    var el = $('boot');
    var f = FACTS[Math.floor(Math.random() * FACTS.length)];
    $('boot-q').textContent = f[0];
    $('boot-a').textContent = f[1];
    $('boot-k').textContent = A.t('Пока грузится', 'While it loads');
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
  var toastTimer = null;
  function showToast() {
    var toast = $('toast');
    toast.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { toast.classList.remove('show'); }, 9000);
  }
  $('toast-x').addEventListener('click', function () { $('toast').classList.remove('show'); });

  /* =============== start =============== */
  A.boot(function (me) {
    if (me) showApp(me); else showGate();
  });
})();
