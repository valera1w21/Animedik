/* Итоги года: всё считается из настоящих записей просмотра. */
(function () {
  'use strict';
  var A = window.App;
  var $ = function (id) { return document.getElementById(id); };

  var MON = ['янв', 'фев', 'мар', 'апр', 'май', 'июн',
             'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];
  var MON_EN = ['jan', 'feb', 'mar', 'apr', 'may', 'jun',
                'jul', 'aug', 'sep', 'oct', 'nov', 'dec'];

  function daysInMonth(y, m) { return new Date(y, m + 1, 0).getDate(); }

  function plural(n, one, few, many) {
    var a = Math.abs(n) % 100, b = a % 10;
    if (a > 10 && a < 20) return many;
    if (b > 1 && b < 5) return few;
    if (b === 1) return one;
    return many;
  }

  function render(d) {
    var year = new Date().getFullYear();
    /* Раньше здесь всегда было «2026-й» — русское окончание вылезало
       и в английской версии страницы. */
    $('st-year').textContent = A.t(year + '-й', String(year));
    $('st-since').textContent = A.t('С 1 января по сегодня', 'From 1 January to today');
    $('st-foot').textContent = A.t('Данные с 1 января ' + year + ' года',
                                   'Data since 1 January ' + year);

    var hours = Math.round(d.seconds / 3600);
    $('st-hours').textContent = hours;
    $('st-hours-sub').textContent = hours >= 24
      ? A.t('это ' + Math.floor(hours / 24) + ' ' +
            plural(Math.floor(hours / 24), 'полные сутки', 'полных суток', 'полных суток'),
            "that's " + Math.floor(hours / 24) + ' full days')
      : A.t('пока немного', 'not much yet');

    $('st-titles').textContent = d.finished;
    $('st-titles-sub').textContent = A.t('закрытых тайтлов', 'titles finished');

    $('st-eps').textContent = d.episodes;
    var dayCount = Object.keys(d.days).length;
    $('st-eps-sub').textContent = dayCount
      ? A.t('в среднем ' + (d.episodes / dayCount).toFixed(1) + ' за день',
            (d.episodes / dayCount).toFixed(1) + ' per active day')
      : '';

    $('st-days').textContent = dayCount;
    var passed = Math.round((Date.now() - new Date(year, 0, 1)) / 864e5);
    $('st-days-sub').textContent = A.t('из ' + passed + ' прошедших',
                                       'of ' + passed + ' days so far');

    /* --- календарь года --- */
    var box = $('year');
    box.textContent = '';
    var max = 1;
    Object.keys(d.days).forEach(function (k) { max = Math.max(max, d.days[k]); });
    for (var m = 0; m < 12; m++) {
      var mon = A.el('div', 'mon');
      mon.appendChild(A.el('div', 'mn', A.t(MON[m], MON_EN[m])));
      var days = A.el('div', 'days');
      for (var dd = 1; dd <= daysInMonth(year, m); dd++) {
        var key = year + '-' + String(m + 1).padStart(2, '0') + '-' + String(dd).padStart(2, '0');
        var n = d.days[key] || 0;
        var lvl = 0;
        if (n > 0) lvl = 1;
        if (n > max * 0.3) lvl = 2;
        if (n > max * 0.6) lvl = 3;
        if (n >= max) lvl = 4;
        var cell = A.el('span', 'd' + (lvl ? ' l' + lvl : ''));
        cell.title = key + ': ' + n;
        days.appendChild(cell);
      }
      mon.appendChild(days);
      box.appendChild(mon);
    }

    /* --- жанры --- */
    var gen = $('st-genres');
    gen.textContent = '';
    var colors = ['var(--mint)', 'var(--sky)', 'var(--lilac)', 'var(--sand)', 'var(--dimmer)', 'var(--line)'];
    var top = d.genres.length ? d.genres[0][1] : 1;
    if (!d.genres.length) {
      gen.appendChild(A.el('div', 'cap', A.t('Пока нечего показать — жанры появятся, когда наберётся статистика.',
                                             'Nothing to show yet — genres appear as you watch.')));
    }
    d.genres.forEach(function (row, i) {
      var g = A.el('div', 'g');
      g.appendChild(A.el('span', 'nm', row[0]));
      var track = A.el('span', 'track');
      var fill = A.el('i');
      fill.style.width = Math.max(4, row[1] / top * 100) + '%';
      fill.style.background = colors[i % colors.length];
      track.appendChild(fill);
      g.appendChild(track);
      g.appendChild(A.el('span', 'v', Math.round(row[1] / 3600) + A.t(' ч', ' h')));
      gen.appendChild(g);
    });

    /* --- рекорды --- */
    var rec = $('st-records');
    rec.textContent = '';
    var best = 0, bestDay = '';
    Object.keys(d.days).forEach(function (k) {
      if (d.days[k] > best) { best = d.days[k]; bestDay = k; }
    });
    var streak = longestStreak(Object.keys(d.days));
    [[best || 0, A.t('серий за один день', 'episodes in one day'), bestDay || '—'],
     [streak, A.t('дней подряд', 'days in a row'), A.t('без пропусков', 'no gaps')]
    ].forEach(function (r) {
      var row = A.el('div', 'rec');
      row.style.marginBottom = '20px';
      row.appendChild(A.el('span', 'num', r[0]));
      var tx = A.el('span', 'tx');
      tx.appendChild(A.el('b', null, r[1]));
      tx.appendChild(A.el('span', null, r[2]));
      row.appendChild(tx);
      rec.appendChild(row);
    });

    /* --- тайтлы года --- */
    var top1 = $('st-top');
    top1.textContent = '';
    if (!d.titles.length) {
      top1.appendChild(A.el('div', 'cap', A.t('Появится, когда что-нибудь посмотрите.',
                                              'Shows up once you watch something.')));
    }
    d.titles.slice(0, 3).forEach(function (row, i) {
      var box2 = A.el('div', 'top1');
      if (i) box2.style.marginTop = '16px';
      var cv = A.el('span', 'cv');
      var ii = A.el('i');
      ii.style.background = 'linear-gradient(155deg,#3E5A6B,#6E93A6 52%,#243440)';
      cv.appendChild(ii);
      box2.appendChild(cv);
      var tx = A.el('span', 'tx');
      tx.appendChild(A.el('b', null, row[0] || '—'));
      tx.appendChild(A.el('span', null, Math.round(row[1] / 3600) + A.t(' ч просмотра', ' h watched')));
      box2.appendChild(tx);
      top1.appendChild(box2);
    });

    /* --- по месяцам --- */
    var months = $('st-months');
    months.textContent = '';
    var perMonth = new Array(12).fill(0);
    Object.keys(d.days).forEach(function (k) {
      var m = parseInt(k.slice(5, 7), 10) - 1;
      if (m >= 0 && m < 12) perMonth[m] += d.days[k];
    });
    var maxM = Math.max.apply(null, perMonth) || 1;
    perMonth.forEach(function (n, m) {
      var row = A.el('div', 'm' + (n === maxM && n > 0 ? ' best' : ''));
      row.appendChild(A.el('span', 'mm', A.t(MON[m], MON_EN[m])));
      var tr = A.el('span', 'tr');
      var f = A.el('i');
      f.style.width = (n / maxM * 100) + '%';
      tr.appendChild(f);
      row.appendChild(tr);
      row.appendChild(A.el('span', 'vv', n));
      months.appendChild(row);
    });
  }

  function longestStreak(days) {
    if (!days.length) return 0;
    var sorted = days.slice().sort();
    var best = 1, run = 1;
    for (var i = 1; i < sorted.length; i++) {
      var prev = new Date(sorted[i - 1]).getTime();
      var cur = new Date(sorted[i]).getTime();
      run = (cur - prev === 864e5) ? run + 1 : 1;
      best = Math.max(best, run);
    }
    return best;
  }

  $('st-share').addEventListener('click', function () {
    window.print();
  });

  A.boot(function (me) {
    if (!me) { location.href = '/'; return; }
    if (me.role === 'guest') { location.href = '/'; return; }
    /* Сообщаем серверу свой часовой пояс: getTimezoneOffset возвращает
       сдвиг с обратным знаком, поэтому минус. */
    var tz = -new Date().getTimezoneOffset();
    A.api.get('/api/stats/year?tz=' + tz).then(render).catch(function (err) {
      $('st-since').textContent = err.message;
    });
  });
})();
