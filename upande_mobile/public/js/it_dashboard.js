/* IT dashboard (/mobile-monitor) and app store (/app-store). Plain JS on the shared Upande v2 design (it-v2.css).
 * Everything from the server goes in through textContent, never innerHTML. */
(function () {
  'use strict';
  var BASE = '/api/method/upande_mobile.api.';
  var boot = document.getElementById('itd-boot');
  var DEV = boot.getAttribute('data-developer') === '1';
  var STORE = boot.getAttribute('data-page') === 'store';
  var IT = boot.getAttribute('data-it') === '1';
  var view = document.getElementById('view');
  var main = document.getElementById('ph-main');
  var root = document.getElementById('ph2');
  var timer = null;
  var appName = '';
  var appList = [];
  var SVG = 'http://www.w3.org/2000/svg';

  /* ---------- helpers ---------- */
  function h(tag, attrs, kids) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
      else if (k === 'class') el.className = v;
      else if (k === 'value') el.value = v;
      else el.setAttribute(k, v === true ? '' : v);
    });
    [].concat(kids == null ? [] : kids).forEach(function (c) {
      if (c == null || c === false) return;
      el.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
    });
    return el;
  }
  function icon(paths) {
    var s = document.createElementNS(SVG, 'svg');
    s.setAttribute('class', 'ph-ic');
    s.setAttribute('viewBox', '0 0 24 24');
    paths.forEach(function (d) { var p = document.createElementNS(SVG, 'path'); p.setAttribute('d', d); s.appendChild(p); });
    return s;
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function mount(nodes) { clear(view); [].concat(nodes).forEach(function (n) { if (n) view.appendChild(n); }); }
  function qs(o) { return Object.keys(o || {}).filter(function (k) { return o[k] !== '' && o[k] != null; }).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(o[k]); }).join('&'); }
  function call(fn, params, post) {
    var url = BASE + (fn.indexOf('.') < 0 ? 'itdesk.' : '') + fn;
    var opts = { credentials: 'same-origin', headers: { Accept: 'application/json' } };
    var q = qs(params);
    if (post) {
      opts.method = 'POST';
      opts.headers['Content-Type'] = 'application/x-www-form-urlencoded';
      opts.headers['X-Frappe-CSRF-Token'] = boot.getAttribute('data-csrf') || '';
      opts.body = q;
    } else if (q) url += '?' + q;
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) {
          var msg = 'Something went wrong (' + r.status + ').';
          try { var m = JSON.parse(j._server_messages || '[]'); if (m.length) msg = JSON.parse(m[0]).message || msg; } catch (e) { /* keep */ }
          if (r.status === 401 || r.status === 403) msg = 'You do not have access to this.';
          throw new Error(msg.replace(/<[^>]+>/g, ''));
        }
        return j.message;
      });
    });
  }
  function toast(text, kind) {
    var box = document.querySelector('.ph-toasts');
    if (!box) { box = h('div', { class: 'ph-toasts' }); document.body.appendChild(box); }
    var t = h('div', { class: 'ph-toast' + (kind ? ' ph-toast--' + kind : ''), role: 'status' }, text);
    box.appendChild(t);
    setTimeout(function () { t.classList.add('is-out'); setTimeout(function () { t.remove(); }, 250); }, 3500);
  }
  function fail(e) { toast(e.message || String(e), 'bad'); }
  function parse(ts) { return ts ? new Date(String(ts).replace(' ', 'T')) : null; }
  function ago(ts) {
    var d = parse(ts);
    if (!d || isNaN(d)) return '—';
    var s = Math.max(0, (Date.now() - d.getTime()) / 1000);
    if (s < 90) return 'just now';
    if (s < 3600) return Math.round(s / 60) + ' min ago';
    if (s < 86400) return Math.round(s / 3600) + ' h ago';
    var n = Math.round(s / 86400);
    return n + (n === 1 ? ' day ago' : ' days ago');
  }
  function dateOnly(ts) { var d = parse(ts); return d && !isNaN(d) ? d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—'; }
  function plural(n, one, many) { return n + ' ' + (n === 1 ? one : (many || one + 's')); }
  function days(n) { return n == null ? '—' : (n === 0 ? 'today' : plural(n, 'day')); }
  function dash(v) { return v ? v : h('span', { class: 'ph-muted' }, '—'); }

  /* ---------- design-system components ---------- */
  function badge(text, kind) { return h('span', { class: 'ph-badge' + (kind ? ' ph-badge--' + kind : '') }, text); }
  function head(eyebrow, title, sub, tools) {
    return h('header', { class: 'ph-head' }, [h('div', {}, [h('div', { class: 'ph-head__eyebrow' }, eyebrow), h('h1', { class: 'ph-head__title' }, title), sub ? h('p', { class: 'ph-head__sub' }, sub) : null]), tools ? h('div', { class: 'ph-head__tools' }, tools) : null]);
  }
  function kpis(list) {
    return h('section', { class: 'ph-kpis' }, list.map(function (k) {
      return h('div', { class: 'ph-kpi' + (k.dark ? ' ph-kpi--dark' : '') + (k.onClick ? ' is-link' : '') + (k.active ? ' is-on' : ''), onclick: k.onClick || null }, [
        h('div', { class: 'ph-kpi__label' }, k.label), h('div', { class: 'ph-kpi__value' }, String(k.value)), k.unit ? h('div', { class: 'ph-kpi__unit' }, k.unit) : null
      ]);
    }));
  }
  function pills(options, value, onChange) {
    return h('div', { class: 'ph-pills' }, options.map(function (o) {
      return h('button', { type: 'button', class: o[0] === value ? 'is-on' : '', onclick: function () { onChange(o[0]); } }, [o[1], o[2] != null ? h('span', { class: 'ph-tab__count' }, String(o[2])) : null]);
    }));
  }
  function select(options, value, onChange, label) {
    var s = h('select', { class: 'ph-select', 'aria-label': label || '', onchange: function () { onChange(s.value); } }, options.map(function (o) { return h('option', { value: o[0] }, o[1]); }));
    s.value = value || '';
    return s;
  }
  function appSelect() {
    return select([['', 'All apps']].concat(appList.map(function (a) { return [a, a]; })), appName, function (v) { appName = v; render(); }, 'App');
  }
  function search(placeholder, value, onEnter) {
    var i = h('input', { type: 'search', placeholder: placeholder, value: value || '', onkeydown: function (e) { if (e.key === 'Enter') onEnter(i.value); } });
    return h('label', { class: 'ph-search' }, [icon(['M11 3a8 8 0 1 0 0 16 8 8 0 0 0 0-16z', 'M21 21l-4.35-4.35']), i]);
  }
  function filters(nodes) { return h('div', { class: 'ph-filters' }, nodes); }
  function card(title, meta, body, flush) {
    return h('section', { class: 'ph-card' + (flush ? ' ph-card--flush' : '') }, [
      title ? h('div', { class: 'ph-card__head' }, [h('div', { class: 'ph-card__title' }, title), meta ? h('div', { class: 'ph-card__meta' }, meta) : null]) : null
    ].concat(body));
  }
  function table(cols, rows) {
    return h('div', { class: 'ph-table-wrap' }, h('table', { class: 'ph-table' }, [
      h('thead', {}, h('tr', {}, cols.map(function (c) { return h('th', { class: c.num ? 'is-num' : '' }, c.label); }))),
      h('tbody', {}, rows)
    ]));
  }
  function empty(title, text) {
    return h('div', { class: 'ph-empty' }, [h('div', { class: 'ph-empty__icon' }, icon(['M20 6L9 17l-5-5'])), h('div', { class: 'ph-empty__title' }, title), text ? h('div', { class: 'ph-empty__text' }, text) : null]);
  }
  function errState(e) {
    return h('div', { class: 'ph-empty ph-empty--error' }, [h('div', { class: 'ph-empty__icon' }, icon(['M12 8v5', 'M12 16h.01'])), h('div', { class: 'ph-empty__title' }, 'Couldn’t load this'), h('div', { class: 'ph-empty__text' }, e.message || String(e))]);
  }
  var openDrawer = null;
  function drawer(title, sub, body) {
    if (openDrawer) openDrawer();
    var overlay, onKey = function (e) { if (e.key === 'Escape') close(); };
    function close() { document.removeEventListener('keydown', onKey); if (overlay) overlay.remove(); overlay = null; openDrawer = null; }
    overlay = h('div', { class: 'ph-overlay ph-overlay--drawer', onclick: function (e) { if (e.target === overlay) close(); } },
      h('div', { class: 'ph-modal', role: 'dialog', 'aria-modal': 'true', 'aria-label': title }, [
        h('div', { class: 'ph-modal__head' }, [h('div', { style: 'min-width:0' }, [h('div', { class: 'ph-modal__title' }, title), sub ? h('div', { class: 'ph-modal__sub' }, sub) : null]), h('button', { class: 'ph-iconbtn ph-iconbtn--flat ph-iconbtn--sm', type: 'button', 'aria-label': 'Close', onclick: close }, icon(['M18 6L6 18', 'M6 6l12 12']))]),
        h('div', { class: 'ph-modal__body' }, body)
      ]));
    root.appendChild(overlay);
    document.addEventListener('keydown', onKey);
    openDrawer = close;
    return close;
  }
  function kvList(pairs, monoValues) {
    var kv = h('dl', { class: 'ph-kv' });
    pairs.forEach(function (p) { if (p[1] == null || p[1] === '') return; kv.appendChild(h('dt', {}, p[0])); kv.appendChild(h('dd', monoValues ? { class: 'ph-mono' } : {}, String(p[1]))); });
    return kv;
  }
  function who(name, sub) { return h('div', {}, [h('div', { class: 'is-strong ph-strong' }, name), h('div', { class: 'ph-muted', style: 'font-size:12px' }, sub)]); }
  function progress(pct) { return h('div', { class: 'ph-progress' + (pct >= 80 ? ' ph-progress--good' : pct >= 50 ? ' ph-progress--warn' : ' ph-progress--bad') }, h('i', { style: 'width:' + pct + '%' })); }
  function mono(v) { return v ? h('span', { class: 'ph-mono' }, v) : h('span', { class: 'ph-muted' }, '—'); }
  function markUpdated() {
    var u = document.getElementById('ph-updated');
    if (!u) return;
    u.hidden = false;
    u.textContent = 'Updated ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }

  /* ---------- routing ---------- */
  var TITLES = { overview: 'Overview', updates: 'Updates', devices: 'Devices', crashlytics: 'Crashlytics', support: 'Support', apps: 'Apps', users: 'User management' };
  function route() {
    var p = (location.hash || '#/overview').slice(2).split('?');
    return { parts: p[0].split('/').map(decodeURIComponent), q: new URLSearchParams(p[1] || '') };
  }
  function go(path) { location.hash = '#/' + path; }
  function setCount(key, n) {
    var el = document.getElementById('count-' + key);
    if (!el) return;
    el.textContent = n > 99 ? '99+' : String(n);
    el.hidden = !n;
  }
  function refreshCounts() {
    return call('today').then(function (d) {
      var by = {};
      d.cards.forEach(function (c) { by[c.key] = c.count; });
      ['updates', 'support', 'crashlytics'].forEach(function (k) { setCount(k, by[k] || 0); });
      return d;
    }).catch(function () { /* counts are a convenience */ });
  }
  function render() {
    clearInterval(timer);
    if (openDrawer) openDrawer();
    var r = route();
    var key = TITLES[r.parts[0]] ? r.parts[0] : 'overview';
    [].forEach.call(document.querySelectorAll('.ph-side__item[data-route]'), function (a) {
      var on = a.getAttribute('data-route') === key;
      a.classList.toggle('is-on', on);
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    document.getElementById('crumb').textContent = TITLES[key];
    var pg = document.querySelector('.ph-page');
    if (pg) pg.classList.toggle('ph-page--wide', key === 'support');
    root.classList.remove('is-drawer-open');
    var screens = { overview: overview, updates: updates, devices: devices, crashlytics: crashlytics, support: support, apps: apps, users: users };
    Promise.resolve(screens[key](r)).then(markUpdated).catch(function (e) { mount(errState(e)); });
    if (key !== 'overview') refreshCounts();
  }

  /* ---------- Overview ---------- */
  function cardCopy(c) {
    var d = c.detail || {};
    if (c.key === 'updates') {
      var parts = [];
      if (d.forgot) parts.push(plural(d.forgot, 'person', 'people') + ' just need to reopen the app');
      if (d.cant_update) parts.push(plural(d.cant_update, 'person', 'people') + ' waiting for the new install');
      return ['People on an old version', parts.join(' · ') + (d.oldest_days ? ' · the release is ' + days(d.oldest_days) + ' old' : ''), 'Open the chase list'];
    }
    if (c.key === 'support') return ['Waiting for an answer', 'The longest wait is ' + plural(d.oldest_hours || 0, 'hour') + ' (' + (d.oldest_user || 'someone') + ')', 'Open the inbox'];
    if (c.key === 'crashlytics') return ['Open issues in the last 2 days', plural(d.people || 0, 'person', 'people') + ' affected' + (d.crashes ? ' · ' + plural(d.crashes, 'crash', 'crashes') : ''), 'Open Crashlytics'];
    if (c.key === 'devices') {
      var bits = [];
      if (d.low_battery) bits.push(d.low_battery + ' low battery');
      if (d.low_storage) bits.push(d.low_storage + ' low storage');
      if (d.offline) bits.push(d.offline + ' offline');
      if (d.outdated_os) bits.push(d.outdated_os + ' old system');
      return ['Devices needing a look', bits.join(' · '), 'Open the devices'];
    }
    return ['Not seen for a long time', 'These people have not opened the app in a while', 'See who'];
  }
  function stat(label, value, unit, extra) {
    var k = { label: label, value: value, unit: unit };
    Object.keys(extra || {}).forEach(function (x) { k[x] = extra[x]; });
    return k;
  }
  /* charts: plain SVG / divs drawn with the design tokens, no library */
  function svgEl(tag, attrs) {
    var el = document.createElementNS(SVG, tag);
    Object.keys(attrs || {}).forEach(function (k) { el.setAttribute(k, attrs[k]); });
    return el;
  }
  function legend(items) {
    return h('div', { class: 'ph-flex ph-flex--wrap', style: 'font-size:12px;color:var(--ink-mute);margin-top:var(--sp-3)' }, items.map(function (i) {
      return h('span', { class: 'ph-flex', style: 'gap:6px' }, [h('i', { style: 'width:10px;height:10px;border-radius:3px;background:' + i[1] + ';display:inline-block' }), i[0]]);
    }));
  }
  function hbars(rows) {
    var max = Math.max.apply(null, rows.map(function (r) { return r.value; }).concat([1]));
    return rows.map(function (r) {
      return h('div', { class: 'ph-hb' }, [h('div', { class: 'ph-hb__name' }, r.label), h('div', { class: 'ph-hb__lane' }, h('div', { class: 'ph-hb__act' + (r.cls ? ' ' + r.cls : ''), style: 'width:' + Math.round(r.value * 100 / max) + '%' })), h('div', { class: 'ph-hb__pct' }, String(r.value))]);
    });
  }
  function stackedRows(rows, parts) {
    return h('div', {}, rows.map(function (r) {
      var total = parts.reduce(function (a, p) { return a + (r.segs[p.key] || 0); }, 0);
      return h('div', { class: 'ph-hb', style: 'grid-template-columns:minmax(90px,150px) minmax(0,1fr) 48px' }, [
        h('div', { class: 'ph-hb__name', title: r.label }, r.label),
        h('div', { style: 'display:flex;height:16px;border-radius:var(--r-pill);overflow:hidden;background:var(--tint)' }, parts.map(function (p) {
          var v = r.segs[p.key] || 0;
          return v ? h('span', { title: p.label + ': ' + v, style: 'width:' + (v * 100 / total) + '%;background:' + p.color }) : null;
        })),
        h('div', { class: 'ph-hb__pct' }, String(total))
      ]);
    }));
  }
  function columns(days, parts) {
    var W = 640, H = 190, L = 28, B = 22, T = 8, n = days.length, bw = (W - L) / n;
    var max = Math.max.apply(null, days.map(function (d) { return parts.reduce(function (a, p) { return a + d[p.key]; }, 0); }).concat([1]));
    var svg = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, width: '100%', role: 'img', 'aria-label': 'Events per day', style: 'display:block' });
    [0, 0.5, 1].forEach(function (f) {
      var y = T + (H - B - T) * (1 - f);
      svg.appendChild(svgEl('line', { x1: L, x2: W, y1: y, y2: y, stroke: 'var(--hairline-2)', 'stroke-width': 1 }));
      var t = svgEl('text', { x: L - 6, y: y + 4, 'text-anchor': 'end', 'font-size': 10, fill: 'var(--ink-mute)' });
      t.textContent = String(Math.round(max * f));
      svg.appendChild(t);
    });
    days.forEach(function (d, i) {
      var y = H - B, x = L + i * bw + bw * 0.18, w = bw * 0.64, tip = d.day + ': ';
      parts.forEach(function (p) {
        var v = d[p.key];
        if (!v) return;
        var hh = (H - B - T) * v / max;
        y -= hh;
        var r = svgEl('rect', { x: x, y: y, width: w, height: hh, rx: 2, style: 'fill:' + p.color });
        var title = svgEl('title');
        title.textContent = d.day + ' · ' + p.label + ': ' + v;
        r.appendChild(title);
        svg.appendChild(r);
      });
      if (i % 2 === (n - 1) % 2) {
        var t = svgEl('text', { x: x + w / 2, y: H - 6, 'text-anchor': 'middle', 'font-size': 10, fill: 'var(--ink-mute)' });
        t.textContent = d.day.slice(5);
        svg.appendChild(t);
      }
    });
    return svg;
  }
  function donut(segs, centre, sub) {
    var total = segs.reduce(function (a, x) { return a + x.value; }, 0);
    var svg = svgEl('svg', { viewBox: '0 0 42 42', width: 150, height: 150, role: 'img', 'aria-label': 'Tickets by status' });
    svg.appendChild(svgEl('circle', { cx: 21, cy: 21, r: 15.9155, fill: 'none', stroke: 'var(--tint)', 'stroke-width': 5 }));
    var off = 25;
    segs.forEach(function (x) {
      if (!x.value) return;
      var len = x.value * 100 / total;
      var c = svgEl('circle', { cx: 21, cy: 21, r: 15.9155, fill: 'none', 'stroke-width': 5, 'stroke-dasharray': len + ' ' + (100 - len), 'stroke-dashoffset': off, style: 'stroke:' + x.color });
      var t = svgEl('title'); t.textContent = x.label + ': ' + x.value; c.appendChild(t);
      svg.appendChild(c);
      off -= len;
    });
    var n = svgEl('text', { x: 21, y: 23, 'text-anchor': 'middle', 'font-size': 8, 'font-weight': 600, fill: 'var(--ink)' }); n.textContent = centre; svg.appendChild(n);
    var m = svgEl('text', { x: 21, y: 28.5, 'text-anchor': 'middle', 'font-size': 3.2, fill: 'var(--ink-mute)' }); m.textContent = sub; svg.appendChild(m);
    return svg;
  }
  function overview() {
    return call('today', { app: appName }).then(function (d) {
      var s = d.stats;
      var by = {};
      d.cards.forEach(function (c) { by[c.key] = c.count; });
      ['updates', 'support', 'crashlytics'].forEach(function (k) { setCount(k, by[k] || 0); });
      var mixParts = [{ key: 'current', label: 'Up to date', color: 'var(--good)' }, { key: 'behind', label: 'Behind', color: 'var(--warn)' }, { key: 'away', label: 'Not seen lately', color: 'var(--ink-faint)' }, { key: 'unknown', label: 'Unknown', color: 'var(--hairline-3)' }];
      var mixRows = Object.keys(s.app_mix).sort().map(function (k) { return { label: k, segs: s.app_mix[k] }; });
      var kindParts = [{ key: 'crash', label: 'Crashes', color: 'var(--bad)' }, { key: 'error', label: 'Errors', color: 'var(--warn)' }, { key: 'api', label: 'API failures', color: 'var(--signal)' }, { key: 'log', label: 'Logs', color: 'var(--ink-faint)' }];
      var evTotal = s.events_by_day.reduce(function (a, x) { return a + x.crash + x.error + x.api + x.log; }, 0);
      var tk = s.tickets_by_status;
      var healthRows = [['low_battery', 'Low battery'], ['low_storage', 'Low storage'], ['offline', 'Offline'], ['outdated_os', 'Old system'], ['outdated_app', 'Old app']].map(function (x) { return { label: x[1], value: s.health[x[0]] || 0, cls: x[0] === 'outdated_app' ? 'mid' : 'low' }; });
      mount([
        head('IT dashboard', 'Overview', plural(s.apps_total, 'app') + ' · ' + plural(s.people, 'person', 'people') + ' · ' + plural(s.devices_active, 'device') + '.'),
        filters([appSelect()]),
        kpis([
          stat('People', s.people, s.people_current + ' on the current version'),
          stat('Devices', s.devices_active, s.devices_active_7d + ' used this week · ' + s.devices_new_7d + ' new'),
          stat('Adoption', s.adoption == null ? '—' : s.adoption + '%', 'on the current version', { onClick: function () { go('updates'); } }),
          stat('Open tickets', s.tickets_open, s.tickets_waiting + ' waiting for an answer', { onClick: function () { go('support'); }, dark: s.tickets_waiting > 0 })
        ]),
        h('div', { class: 'ph-row ph-row--2-1' }, [
          card('App versions', 'people by update state', mixRows.length ? [stackedRows(mixRows, mixParts), legend(mixParts.map(function (p) { return [p.label, p.color]; }))] : empty('No people yet')),
          card('Android versions', s.android_versions.length ? plural(s.android_versions.reduce(function (a, x) { return a + x.count; }, 0), 'device') : '', s.android_versions.length ? [h('div', {}, hbars(s.android_versions.map(function (v) { return { label: v.label, value: v.count, cls: v.old ? 'low' : '' }; }))), s.android_versions.some(function (v) { return v.old; }) ? h('div', { class: 'ph-muted', style: 'font-size:12px;margin-top:var(--sp-3)' }, 'Red = older than the supported minimum.') : null] : empty('No Android devices yet'))
        ]),
        h('div', { class: 'ph-row ph-row--2-1' }, [
          card('Events reported', 'last 14 days · ' + evTotal.toLocaleString(), evTotal ? [columns(s.events_by_day, kindParts), legend(kindParts.map(function (p) { return [p.label, p.color]; }))] : empty('Nothing reported', 'No crashes, errors or failed requests in 14 days.')),
          h('div', {}, [
            card('Support tickets', plural(s.tickets_total, 'ticket'), s.tickets_total ? h('div', { class: 'ph-flex', style: 'gap:var(--sp-5)' }, [donut([{ label: 'Waiting', value: tk.waiting, color: 'var(--bad)' }, { label: 'Answered', value: tk.answered, color: 'var(--signal)' }, { label: 'Resolved', value: tk.resolved, color: 'var(--good)' }], String(s.tickets_total), 'tickets'), h('div', { class: 'ph-grow' }, [['Waiting', tk.waiting, 'var(--bad)'], ['Answered', tk.answered, 'var(--signal)'], ['Resolved', tk.resolved, 'var(--good)']].map(function (x) { return h('div', { class: 'ph-flex ph-flex--between', style: 'padding:4px 0' }, [h('span', { class: 'ph-flex', style: 'gap:8px' }, [h('i', { style: 'width:10px;height:10px;border-radius:3px;display:inline-block;background:' + x[2] }), x[0]]), h('b', {}, String(x[1]))]); }))]) : empty('No tickets yet')),
            card('Device health', '', hbars(healthRows))
          ])
        ])
      ]);
    });
  }

  /* ---------- Updates ---------- */
  var BUCKETS = [['', 'Needs chasing'], ['forgot', 'Pending update'], ['cant_update', 'Pending install'], ['away', 'Not seen lately'], ['current', 'Up to date']];
  function updates(r) {
    var bucket = r.q.get('bucket') || '';
    var q = r.q.get('search') || '';
    var selected = {};
    return call('updates', { app: appName, bucket: bucket, search: q }).then(function (d) {
      var chased = (d.counts.forgot || 0) + (d.counts.cant_update || 0);
      var actionable = bucket === '' || bucket === 'forgot' || bucket === 'cant_update';
      var sendBtn = h('button', { class: 'ph-btn ph-btn--primary', type: 'button', disabled: true, onclick: function () { send(Object.keys(selected).map(function (k) { return selected[k]; })); } }, 'Remind selected');
      function sync() {
        var n = Object.keys(selected).length;
        sendBtn.disabled = !n;
        sendBtn.textContent = n ? 'Remind ' + n + ' selected' : 'Remind selected';
      }
      function send(list) {
        sendBtn.disabled = true;
        call('remind', { people_list: JSON.stringify(list.map(function (p) { return { user: p.user, app: p.app }; })) }, true).then(function (res) {
          var t = {};
          res.results.forEach(function (x) { t[x.result] = (t[x.result] || 0) + 1; });
          var bits = [];
          if (t.sent) bits.push(t.sent + ' reminded');
          if (t.no_device) bits.push(t.no_device + ' have not allowed notifications');
          if (t.recent) bits.push(t.recent + ' reminded in the last day');
          if (t.not_behind) bits.push(t.not_behind + ' already up to date');
          toast(bits.join(' · ') || 'Nothing sent', t.sent ? 'good' : 'warn');
          render();
        }).catch(function (e) { fail(e); sync(); });
      }
      var all = h('input', { type: 'checkbox', 'aria-label': 'Select everyone', onchange: function () {
        [].forEach.call(document.querySelectorAll('.pick'), function (cb) { cb.checked = all.checked; cb.dispatchEvent(new Event('change')); });
      } });
      var rows = d.rows.map(function (p) {
        var key = p.user + '|' + p.app;
        var can = p.bucket === 'forgot' || p.bucket === 'cant_update';
        var cb = h('input', { type: 'checkbox', class: 'pick', 'aria-label': 'Select ' + p.full_name, onchange: function (ev) { if (ev.target.checked) selected[key] = p; else delete selected[key]; sync(); } });
        return h('tr', {}, [
          h('td', {}, can ? cb : null),
          h('td', {}, who(p.full_name, p.team ? p.team + ' · ' + p.user : p.user)),
          h('td', {}, p.app),
          h('td', {}, [p.installed || '—', ' → ', p.target || '—']),
          h('td', {}, p.bucket === 'cant_update' ? badge('Pending install', 'bad') : p.bucket === 'forgot' ? badge('Pending update', 'warn') : p.bucket === 'away' ? badge('Not seen lately') : badge('Up to date', 'good')),
          h('td', { class: 'is-num' }, p.bucket === 'current' || p.bucket === 'away' ? '—' : days(p.released_days_ago)),
          h('td', {}, ago(p.last_seen)),
          h('td', {}, p.reminded ? (p.reminded === 1 ? 'Once' : p.reminded + ' times') + ' · ' + ago(p.last_reminded) : h('span', { class: 'ph-muted' }, 'Never')),
          h('td', {}, can ? h('button', { class: 'ph-btn ph-btn--ghost ph-btn--sm', type: 'button', onclick: function () { send([p]); } }, 'Remind') : null)
        ]);
      });
      var cols = [{ label: '' }, { label: 'Person' }, { label: 'App' }, { label: 'Has → needs' }, { label: 'State' }, { label: 'Release age', num: true }, { label: 'Last seen' }, { label: 'Reminders' }, { label: '' }];
      mount([
        head('IT dashboard · Updates', 'Who needs to update', 'Everyone running an older version than the current release. Reminders go out as push notifications.'),
        filters([
          pills(BUCKETS.map(function (b) { return [b[0], b[1], b[0] === '' ? chased : (d.counts[b[0]] || 0)]; }), bucket, function (v) { go('updates' + (v ? '?bucket=' + v : '')); }),
          appSelect(),
          search('Search a name, email or team', q, function (v) { go('updates?' + qs({ bucket: bucket, search: v })); }),
          h('span', { class: 'ph-filters__spacer' }),
          actionable ? sendBtn : null
        ]),
        card('', '', rows.length ? table(cols, rows) : empty('Nobody here', 'Everyone in this group is sorted.'), true)
      ]);
      var first = document.querySelector('.ph-table th');
      if (first && actionable && rows.length) first.appendChild(all);
    });
  }

  /* ---------- Devices ---------- */
  var FLAG_TEXT = { low_battery: 'Low battery', low_storage: 'Low storage', offline: 'Offline', outdated_os: 'Old system', outdated_app: 'Old app' };
  function devices(r) {
    var state = r.q.get('state') || '', health = r.q.get('health') || '', q = r.q.get('search') || '';
    return call('device_list', { app: appName, state: state, health: health, search: q }).then(function (d) {
      function nav(over) { var o = { state: state, health: health, search: q }; Object.keys(over).forEach(function (k) { o[k] = over[k]; }); go('devices?' + qs(o)); }
      var s = d.summary || {};
      var rows = [];
      d.rows.forEach(function (x) {
        function open() { openDevice(x); }
        rows.push(h('tr', { class: 'is-link', tabindex: 0, onclick: open, onkeydown: function (e) { if (e.key === 'Enter') open(); } }, [
          h('td', {}, who(x.owner_name || 'No owner', x.owner)),
          h('td', {}, x.device + (x.os_version ? ' · ' + x.platform + ' ' + x.os_version : '')),
          h('td', {}, [x.app, ' ', h('span', { class: 'ph-muted' }, x.app_version)]),
          h('td', {}, mono(x.serial_number)),
          h('td', {}, mono(x.mac_address)),
          h('td', {}, mono(x.local_ip || x.ip_seen)),
          h('td', {}, mono(x.build_number)),
          h('td', {}, x.state !== 'Active' ? badge(x.state, x.state === 'Lost' ? 'bad' : '') : (x.flags.length ? x.flags.map(function (f) { return badge(FLAG_TEXT[f] || f, f === 'outdated_app' ? 'warn' : 'bad'); }) : badge('Healthy', 'good'))),
          h('td', { class: 'is-num' }, x.battery != null ? x.battery + '%' : '—'),
          h('td', { class: 'is-num' }, x.storage_free_gb != null ? x.storage_free_gb + ' GB' : '—'),
          h('td', {}, ago(x.last_seen))
        ]));
      });
      var cols = [{ label: 'Owner' }, { label: 'Device' }, { label: 'App' }, { label: 'Serial number' }, { label: 'MAC address' }, { label: 'IP address' }, { label: 'Build' }, { label: 'Health' }, { label: 'Battery', num: true }, { label: 'Free', num: true }, { label: 'Last seen' }];
      mount([
        head('IT dashboard · Devices', 'Phones and tablets', plural(d.total, 'device') + '. Click a row to see everything about it, mark it lost or retired, and add a note. Android hides the serial number and MAC address from most apps, so those can be empty.'),
        filters([
          pills([['', 'All', d.total]].concat(Object.keys(FLAG_TEXT).filter(function (k) { return s[k] != null; }).map(function (k) { return [k, FLAG_TEXT[k], s[k]]; })), health, function (v) { nav({ health: v }); }),
          appSelect(),
          select([['', 'Any status'], ['Active', 'Active'], ['Lost', 'Lost'], ['Retired', 'Retired']], state, function (v) { nav({ state: v }); }, 'Device status'),
          search('Search owner, model, serial, MAC or IP', q, function (v) { nav({ search: v }); })
        ]),
        card('', '', rows.length ? table(cols, rows) : empty('No devices match'), true)
      ]);
    });
  }
  function openDevice(x) {
    var notes = h('textarea', { class: 'ph-control', rows: 3, style: 'height:auto;padding:10px 14px', placeholder: 'Who has it, where it is, what was done' }, x.notes);
    var st = select([['Active', 'Active'], ['Lost', 'Lost'], ['Retired', 'Retired']], x.state, function () {}, 'Device status');
    var close;
    var body = h('div', { class: 'ph-stack ph-stack--lg' }, [
      h('div', { class: 'ph-flex ph-flex--wrap' }, x.flags.length ? x.flags.map(function (f) { return badge(FLAG_TEXT[f] || f, f === 'outdated_app' ? 'warn' : 'bad'); }) : [badge('Healthy', 'good')]),
      kvList([['Owner', x.owner_name], ['Email', x.owner], ['App', x.app + ' ' + x.app_version], ['System', x.platform + ' ' + x.os_version], ['Battery', x.battery != null ? x.battery + '%' : ''], ['Free storage', x.storage_free_gb != null ? x.storage_free_gb + ' GB' : ''], ['Network', x.network], ['Last seen', ago(x.last_seen)]]),
      h('div', {}, [h('div', { class: 'ph-label', style: 'margin-bottom:var(--sp-2)' }, 'Identifiers'), kvList([['Serial number', x.serial_number || 'not reported'], ['MAC address', x.mac_address || 'not reported']].concat(x.local_ip && x.ip_seen && x.local_ip !== x.ip_seen ? [['IP on its network', x.local_ip], ['IP the server sees', x.ip_seen]] : [['IP address', x.local_ip || x.ip_seen || 'not reported']]).concat([ ['Build number', x.build_number || 'not reported']]), true)]),
      h('div', {}, [h('div', { class: 'ph-label', style: 'margin-bottom:var(--sp-2)' }, 'People who used this device'), x.users && x.users.length ? h('div', {}, x.users.map(function (u) {
        return h('div', { style: 'padding:var(--sp-3) 0;border-bottom:1px solid var(--hairline)' }, [
          h('div', { class: 'ph-flex ph-flex--between' }, [h('div', { class: 'ph-strong' }, u.name), h('span', { class: 'ph-muted', style: 'font-size:12px' }, u.version ? 'version ' + u.version : '')]),
          u.name !== u.user ? h('div', { class: 'ph-muted', style: 'font-size:12px' }, u.user) : null,
          h('div', { class: 'ph-muted', style: 'font-size:12px' }, 'First used ' + dateOnly(u.first_seen) + ' · last used ' + ago(u.last_seen) + (u.logins ? ' · ' + plural(u.logins, 'sign-in') : ''))
        ]);
      })) : h('div', { class: 'ph-muted' }, 'No sign-ins recorded for this device.')]),
      h('div', { class: 'ph-field' }, [h('label', {}, 'Status'), st, h('div', { class: 'ph-field__hint' }, 'Lost and retired devices stop appearing on the chase list.')]),
      h('div', { class: 'ph-field' }, [h('label', {}, 'Notes'), notes]),
      h('div', {}, h('button', { class: 'ph-btn ph-btn--primary', type: 'button', onclick: function () {
        call('set_device', { id: x.id, state: st.value, notes: notes.value }, true).then(function () { toast('Saved', 'good'); close(); render(); }).catch(fail);
      } }, 'Save'))
    ]);
    if (x.developer) body.appendChild(h('details', { class: 'itd-dev' }, [h('summary', {}, 'Developer details'), kvList([['Install id', x.developer.install_id], ['Runtime', x.developer.runtime], ['First seen', x.developer.first_seen], ['Launches', x.developer.launches]], true)]));
    close = drawer(x.device, x.owner_name || x.owner || '', body);
  }

  /* ---------- Crashlytics ---------- */
  /* ---------- User management ---------- */
  var USERS = boot.getAttribute('data-users') === '1';
  /* a search box that narrows the rows already on the page as you type (no reload, so the cursor stays put);
     Enter asks the server, which also reaches people beyond the rows shown. */
  function liveSearch(placeholder, value, onEnter) {
    var i = h('input', { type: 'search', placeholder: placeholder, value: value || '', oninput: function () {
      var term = i.value.trim().toLowerCase(), shown = 0, all = 0;
      [].forEach.call(view.querySelectorAll('tbody tr[data-find]'), function (tr) {
        all++;
        var hit = !term || tr.getAttribute('data-find').indexOf(term) >= 0;
        tr.hidden = !hit;
        if (hit) shown++;
      });
      var n = document.getElementById('um-shown');
      if (n) n.textContent = term ? shown + ' of ' + plural(all, 'person', 'people') + ' (press Enter to search everyone)' : plural(all, 'person', 'people');
    }, onkeydown: function (e) { if (e.key === 'Enter') onEnter(i.value); } });
    return h('label', { class: 'ph-search' }, [icon(['M11 3a8 8 0 1 0 0 16 8 8 0 0 0 0-16z', 'M21 21l-4.35-4.35']), i]);
  }
  function profileChips(list, locked) {
    return list.length ? list.map(function (p) { return badge(p, locked ? '' : 'signal'); }) : h('span', { class: 'ph-muted' }, 'No role profile');
  }
  function users(r) {
    if (!USERS) return Promise.reject(new Error('User management is for the IT Team and System Managers only.'));
    var status = r.q.get('status') || '', profile = r.q.get('profile') || '', q = r.q.get('search') || '';
    return Promise.all([call('usermgmt.meta'), call('usermgmt.users', { status: status, profile: profile, search: q })]).then(function (res) {
      var m = res[0], d = res[1], s = d.summary;
      function nav(over) { var o = { status: status, profile: profile, search: q }; Object.keys(over).forEach(function (k) { o[k] = over[k]; }); go('users?' + qs(o)); }
      var rows = d.rows.map(function (x) {
        function open() { openUser(x.name, m); }
        return h('tr', { class: 'is-link', tabindex: 0, 'data-find': (x.full_name + ' ' + x.name).toLowerCase(), onclick: open, onkeydown: function (e) { if (e.key === 'Enter') open(); } }, [
          h('td', {}, who(x.full_name, x.name)),
          h('td', {}, h('div', { class: 'ph-flex ph-flex--wrap' }, profileChips(x.profiles, !!x.locked))),
          h('td', {}, x.locked ? badge('Managed in Desk', '') : x.enabled ? badge('Active', 'good') : badge('Disabled', 'bad')),
          h('td', {}, x.last_active ? ago(x.last_active) : h('span', { class: 'ph-muted' }, 'never')),
          h('td', {}, dateOnly(x.created))
        ]);
      });
      var cols = [{ label: 'Person' }, { label: 'Role profiles' }, { label: 'Status' }, { label: 'Last active' }, { label: 'Added' }];
      var add = h('button', { class: 'ph-btn ph-btn--primary', type: 'button', onclick: function () { openAddUser(m); } }, 'Add user');
      mount([
        head('IT dashboard · User management', 'People and access', 'Add people, change their details, turn accounts on or off and give them role profiles. A role profile is a ready-made set of permissions. System roles are never handed out from here.', add),
        filters([
          pills([['', 'All', s.active + s.disabled], ['active', 'Active', s.active], ['disabled', 'Disabled', s.disabled]], status, function (v) { nav({ status: v }); }),
          select([['', 'Any role profile'], ['none', 'No role profile']].concat(m.profiles.map(function (p) { return [p.name, p.name + ' (' + p.users + ')']; })), profile, function (v) { nav({ profile: v }); }, 'Role profile'),
          liveSearch('Search name or email', q, function (v) { nav({ search: v }); })
        ]),
        card('', '', rows.length ? table(cols, rows) : empty('No people match'), true),
        h('div', { class: 'ph-muted', id: 'um-shown', style: 'font-size:12px;margin-top:var(--sp-3)' }, rows.length ? plural(rows.length, 'person', 'people') : '')
      ]);
    });
  }
  function field(label, control, hint) { return h('div', { class: 'ph-field' }, [h('label', {}, label), control, hint ? h('div', { class: 'ph-field__hint' }, hint) : null]); }
  function input(attrs) { return h('input', Object.assign({ class: 'ph-control', autocomplete: 'off' }, attrs)); }
  function profileChecks(m, picked) {
    var boxes = [];
    var wrap = h('div', { class: 'ph-stack', style: 'gap:var(--sp-2)' }, m.profiles.map(function (p) {
      var cb = h('input', { type: 'checkbox', value: p.name, disabled: !p.assignable });
      cb.checked = picked.indexOf(p.name) >= 0;
      boxes.push(cb);
      return h('label', { class: 'ph-flex', style: 'gap:var(--sp-3);align-items:flex-start;' + (p.assignable ? '' : 'opacity:.55') }, [cb, h('span', {}, [h('span', { class: 'ph-strong' }, p.name), h('span', { class: 'ph-muted', style: 'font-size:12px;display:block' }, p.assignable ? (p.roles.join(', ') || 'No roles') : (p.name === 'IT Team' ? 'Decides who can manage users. Only a System Manager can give it, in Desk.' : 'Includes a system role, so it cannot be given from here.'))])]);
    }));
    wrap.value = function () { return boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; }); };
    return wrap;
  }
  function openAddUser(m) {
    var email = input({ type: 'email', placeholder: 'name@company.com' });
    var name = input({ type: 'text', placeholder: 'Full name' });
    var pw = input({ type: 'text', placeholder: 'Temporary password' });
    var checks = profileChecks(m, []);
    var close;
    var go_ = h('button', { class: 'ph-btn ph-btn--primary', type: 'button', onclick: function () {
      go_.disabled = true;
      call('usermgmt.create', { email: email.value, full_name: name.value, password: pw.value, role_profiles: JSON.stringify(checks.value()) }, true).then(function (res) { toast('Added ' + res.name, 'good'); close(); render(); }).catch(function (e) { go_.disabled = false; fail(e); });
    } }, 'Add user');
    close = drawer('Add user', 'They sign in with this password. Ask them to change it.', h('div', { class: 'ph-stack ph-stack--lg' }, [
      field('Email', email), field('Full name', name), field('Password', pw, 'Use at least 8 characters, with letters and numbers.'),
      field('Role profiles', checks, 'Pick what they need to do. You can change this later.'), h('div', {}, go_)
    ]));
  }
  function openUser(id, m) {
    var close = drawer('Loading…', '', h('div', { class: 'ph-muted' }, 'Loading…'));
    call('usermgmt.detail', { name: id }).then(function (u) { close(); showUser(u, m); }).catch(function (e) { close(); fail(e); });
  }
  function showUser(u, m) {
    var close;
    function reopen() { render(); openUser(u.name, m); }
    var body = h('div', { class: 'ph-stack ph-stack--lg' });
    body.appendChild(h('div', { class: 'ph-flex ph-flex--wrap' }, [u.locked ? badge('Managed in Desk', '') : u.enabled ? badge('Active', 'good') : badge('Disabled', 'bad')].concat(u.is_me ? [badge('You', 'signal')] : [])));
    if (u.locked) {
      body.appendChild(h('div', { class: 'ph-muted' }, u.locked));
      body.appendChild(kvList([['Email', u.name], ['Role profiles', u.profiles.join(', ')], ['Roles', u.roles.join(', ')], ['Last active', u.last_active ? ago(u.last_active) : 'never']]));
      close = drawer(u.full_name, u.name, body);
      return;
    }
    var nm = input({ type: 'text', value: u.full_name });
    var en = select([['1', 'Active — can sign in'], ['0', 'Disabled — cannot sign in']], u.enabled ? '1' : '0', function () {}, 'Account status');
    body.appendChild(field('Full name', nm));
    body.appendChild(field('Account', en, u.is_me ? 'You cannot disable your own account.' : ''));
    body.appendChild(h('div', {}, h('button', { class: 'ph-btn ph-btn--primary', type: 'button', onclick: function () {
      call('usermgmt.update', { name: u.name, full_name: nm.value, enabled: en.value }, true).then(function () { toast('Saved', 'good'); close(); reopen(); }).catch(fail);
    } }, 'Save')));
    // role profiles: add one, take one away
    var have = {};
    u.profiles.forEach(function (p) { have[p] = 1; });
    var list = h('div', {}, u.profiles.length ? u.profiles.map(function (p) {
      return h('div', { class: 'ph-flex ph-flex--between', style: 'padding:var(--sp-2) 0;border-bottom:1px solid var(--hairline)' }, [
        h('span', { class: 'ph-strong' }, p),
        u.is_me ? null : h('button', { class: 'ph-btn ph-btn--ghost', type: 'button', onclick: function () {
          if (!confirm('Take ' + p + ' away from ' + u.full_name + '?')) return;
          call('usermgmt.remove_role_profile', { name: u.name, role_profile: p }, true).then(function () { toast('Removed ' + p, 'good'); close(); reopen(); }).catch(fail);
        } }, 'Remove')
      ]);
    }) : h('div', { class: 'ph-muted' }, 'No role profile, so no access yet.'));
    var avail = m.profiles.filter(function (p) { return p.assignable && !have[p.name]; });
    var pick = select([['', 'Add a role profile…']].concat(avail.map(function (p) { return [p.name, p.name]; })), '', function () {}, 'Role profile to add');
    var section = [h('div', { class: 'ph-label' }, 'Role profiles'), list];
    if (!u.is_me) section.push(h('div', { class: 'ph-flex', style: 'margin-top:var(--sp-3);gap:var(--sp-3)' }, [pick, h('button', { class: 'ph-btn', type: 'button', onclick: function () {
      if (!pick.value) return;
      call('usermgmt.add_role_profile', { name: u.name, role_profile: pick.value }, true).then(function () { toast('Added ' + pick.value, 'good'); close(); reopen(); }).catch(fail);
    } }, 'Add')]));
    else section.push(h('div', { class: 'ph-field__hint' }, 'You cannot change your own role profiles.'));
    body.appendChild(h('div', {}, section));
    body.appendChild(h('div', {}, [h('div', { class: 'ph-label', style: 'margin-bottom:var(--sp-2)' }, 'What they can do'), u.roles.length ? h('div', { class: 'ph-flex ph-flex--wrap' }, u.roles.map(function (r) { return badge(r, ''); })) : h('div', { class: 'ph-muted' }, 'No roles.')]));
    body.appendChild(kvList([['Email', u.name], ['Last active', u.last_active ? ago(u.last_active) : 'never'], ['Added', dateOnly(u.created)]]));
    // password
    var pw = input({ type: 'text', placeholder: 'New password' });
    body.appendChild(h('div', {}, [h('div', { class: 'ph-label', style: 'margin-bottom:var(--sp-2)' }, 'Reset password'), h('div', { class: 'ph-flex', style: 'gap:var(--sp-3)' }, [pw, h('button', { class: 'ph-btn', type: 'button', onclick: function () {
      if (!pw.value) return;
      call('usermgmt.reset_password', { name: u.name, password: pw.value }, true).then(function () { toast('Password changed', 'good'); pw.value = ''; }).catch(fail);
    } }, 'Set')])]));
    if (u.history.length) body.appendChild(h('div', {}, [h('div', { class: 'ph-label', style: 'margin-bottom:var(--sp-2)' }, 'What IT changed'), h('div', {}, u.history.map(function (x) { return h('div', { style: 'padding:var(--sp-2) 0;border-bottom:1px solid var(--hairline)' }, [h('div', {}, x.text), h('div', { class: 'ph-muted', style: 'font-size:12px' }, ago(x.at))]); }))]));
    if (!u.is_me) body.appendChild(h('div', { style: 'padding-top:var(--sp-4);border-top:1px solid var(--hairline)' }, [h('button', { class: 'ph-btn ph-btn--danger', type: 'button', onclick: function () {
      if (!confirm('Delete ' + u.full_name + ' (' + u.name + ')? This cannot be undone. To keep their history, disable the account instead.')) return;
      call('usermgmt.delete', { name: u.name }, true).then(function () { toast('Deleted ' + u.name, 'good'); close(); render(); }).catch(fail);
    } }, 'Delete user')]));
    close = drawer(u.full_name, u.name, body);
  }

  var KIND_BADGE = { crash: 'bad', error: 'warn', api: 'signal', log: '' };
  function crashlytics(r) {
    var status = r.q.get('status') || 'Open', kind = r.q.get('kind') || '', q = r.q.get('search') || '';
    return call('problems', { app: appName, status: status, kind: kind, search: q }).then(function (d) {
      function nav(over) { var o = { status: status, kind: kind, search: q }; Object.keys(over).forEach(function (k) { o[k] = over[k]; }); go('crashlytics?' + qs(o)); }
      var sum = function (key) { return d.rows.reduce(function (a, x) { return a + x[key]; }, 0); };
      var rows = [];
      d.rows.forEach(function (p) {
        function open() { openIssue(p); }
        rows.push(h('tr', { class: 'is-link', tabindex: 0, onclick: open, onkeydown: function (e) { if (e.key === 'Enter') open(); } }, [
          h('td', { class: 'itd-issue' }, [h('div', { class: 'ph-strong itd-clamp' }, p.title || '(no title)'), p.culprit ? h('div', { class: 'ph-mono ph-muted ph-truncate' }, p.culprit) : null]),
          h('td', {}, badge(p.kind, KIND_BADGE[p.kind])),
          h('td', {}, p.app),
          h('td', { class: 'is-num' }, String(p.times)),
          h('td', { class: 'is-num' }, String(p.people)),
          h('td', { class: 'is-num' }, String(p.devices)),
          h('td', {}, p.version ? (p.first_version && p.first_version !== p.version ? p.first_version + ' → ' + p.version : p.version) + (p.fixed_in ? ' · fixed in ' + p.fixed_in : '') : '—'),
          h('td', {}, ago(p.last_seen)),
          h('td', {}, badge(p.status, p.status === 'Open' ? 'warn' : p.status === 'Resolved' ? 'good' : ''))
        ]));
      });
      var cols = [{ label: 'Issue' }, { label: 'Kind' }, { label: 'App' }, { label: 'Events', num: true }, { label: 'Users', num: true }, { label: 'Devices', num: true }, { label: 'Versions' }, { label: 'Last seen' }, { label: 'Status' }];
      var bk = d.by_kind || {};
      mount([
        head('IT dashboard · Crashlytics', 'Crashlytics', 'Crashes, errors and failed requests reported by your apps. Click an issue for the details and to resolve it.'),
        filters([
          pills([['Open', 'Open', (d.by_status || {}).Open || 0], ['Resolved', 'Resolved', (d.by_status || {}).Resolved || 0], ['Ignored', 'Ignored', (d.by_status || {}).Ignored || 0]], status, function (v) { nav({ status: v }); }),
          select([['', 'All kinds'], ['crash', 'Crashes (' + (bk.crash || 0) + ')'], ['error', 'Errors (' + (bk.error || 0) + ')'], ['api', 'API failures (' + (bk.api || 0) + ')'], ['log', 'Logs (' + (bk.log || 0) + ')']], kind, function (v) { nav({ kind: v }); }, 'Kind'),
          appSelect(),
          search('Search an error or file', q, function (v) { nav({ search: v }); })
        ]),
        kpis([
          stat('Issues', d.total, status.toLowerCase()),
          stat('Crashes', d.rows.filter(function (x) { return x.kind === 'crash'; }).length, 'the app closed', { dark: d.rows.some(function (x) { return x.kind === 'crash'; }) }),
          stat('Events', sum('times'), 'reported in total'),
          stat('Users affected', sum('people'), 'counted per issue')
        ]),
        card('', '', rows.length ? table(cols, rows) : empty('No issues', 'Nothing matches these filters.'), true)
      ]);
    });
  }
  function openIssue(p) {
    var who_ = h('div', { class: 'ph-muted' }, 'Loading…');
    call('problem_people', { name: p.name }).then(function (res) { who_.textContent = res.rows.length ? res.rows.map(function (x) { return x.name + (x.times > 1 ? ' (' + x.times + '×)' : ''); }).join(', ') : 'No signed-in people recorded.'; }).catch(function () { who_.textContent = ''; });
    var close;
    function setStatus(st) { call('set_problem_status', { name: p.name, status: st }, true).then(function () { toast('Marked ' + st.toLowerCase(), 'good'); close(); render(); }).catch(fail); }
    var body = h('div', { class: 'ph-stack ph-stack--lg' }, [
      h('div', { class: 'ph-flex ph-flex--wrap' }, [badge(p.kind, KIND_BADGE[p.kind]), badge(p.status, p.status === 'Open' ? 'warn' : p.status === 'Resolved' ? 'good' : ''), badge(p.app, 'outline')]),
      p.culprit ? h('div', { class: 'ph-mono ph-muted', style: 'overflow-wrap:anywhere' }, p.culprit) : null,
      kvList([['Events', p.times], ['Users affected', p.people], ['Devices', p.devices], ['First seen', ago(p.first_seen)], ['Last seen', ago(p.last_seen)], ['First version', p.first_version], ['Last version', p.version], ['Fixed in', p.fixed_in]]),
      h('div', {}, [h('div', { class: 'ph-label', style: 'margin-bottom:var(--sp-2)' }, 'Who was affected'), who_]),
      h('div', { class: 'ph-flex ph-flex--wrap' }, [
        p.status === 'Open' ? h('button', { class: 'ph-btn ph-btn--primary', type: 'button', onclick: function () { setStatus('Resolved'); } }, 'Mark resolved') : h('button', { class: 'ph-btn ph-btn--ghost', type: 'button', onclick: function () { setStatus('Open'); } }, 'Reopen'),
        p.status === 'Open' ? h('button', { class: 'ph-btn ph-btn--ghost', type: 'button', onclick: function () { setStatus('Ignored'); } }, 'Ignore') : null
      ])
    ]);
    if (p.developer) body.appendChild(problemDev(p));
    close = drawer(p.title || '(no title)', plural(p.times, 'event') + ' · last seen ' + ago(p.last_seen), body);
  }
  function problemDev(p) {
    var inner = h('div', {}, h('span', { class: 'ph-muted', style: 'font-size:12px' }, 'Open to load.'));
    var loaded = false;
    var d = h('details', { class: 'itd-dev', open: true, ontoggle: function () {
      if (!d.open || loaded) return;
      loaded = true;
      clear(inner);
      var kv = h('dl', { class: 'ph-kv' });
      [['Level', p.level], ['Issue id', p.name], ['Last event id', p.developer.last_event]].forEach(function (x) { kv.appendChild(h('dt', {}, x[0])); kv.appendChild(h('dd', { class: 'ph-mono' }, x[1] || '—')); });
      inner.appendChild(kv);
      call('itdesk.problem_event', { name: p.name }).then(function (res) {
        var ev = res.latest;
        if (ev && (ev.stack || ev.message)) inner.appendChild(h('pre', { class: 'itd-code' }, ev.stack || ev.message));
      }).catch(function () { /* the summary above is enough */ });
    } }, [h('summary', {}, 'Developer details'), inner]);
    return d;
  }

  /* ---------- Support (inbox + chat) ---------- */
  function initials(name) {
    var parts = String(name || '?').replace(/@.*/, '').split(/[\s._-]+/).filter(Boolean);
    return ((parts[0] || '?')[0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase();
  }
  function support(r) {
    var status = r.q.get('status') || '';
    var onlyWaiting = r.q.get('waiting') === '1';
    var open = r.parts[1] || '';
    return call('inbox', { app: appName, status: status }).then(function (d) {
      var rows = onlyWaiting ? d.rows.filter(function (c) { return c.waiting; }) : d.rows;
      if (!open && rows.length && window.innerWidth > 1000) open = rows[0].id;
      function nav(over) { var o = { status: status, waiting: onlyWaiting ? '1' : '' }; Object.keys(over).forEach(function (k) { o[k] = over[k]; }); go('support?' + qs(o)); }
      var list = h('section', { class: 'ph-card itd-inbox' }, [
        h('div', { class: 'itd-pane__head' }, [h('div', { class: 'ph-card__title' }, 'Conversations'), h('div', { class: 'ph-card__meta' }, plural(rows.length, 'conversation'))]),
        h('div', { class: 'itd-pane__body' }, rows.length ? rows.map(function (c) {
          var done = c.status === 'Resolved' || c.status === 'Closed';
          var url = 'support/' + encodeURIComponent(c.id) + '?' + qs({ status: status, waiting: onlyWaiting ? '1' : '' });
          return h('button', { type: 'button', class: 'itd-thread' + (c.id === open ? ' is-on' : ''), onclick: function () { go(url); } }, [
            h('div', { class: 'ph-avatar' }, initials(c.person || c.user)),
            h('div', { class: 'itd-thread__main' }, [
              h('div', { class: 'ph-flex ph-flex--between' }, [h('b', {}, c.person || c.user), h('span', { class: 'ph-muted', style: 'font-size:11px;white-space:nowrap' }, ago(c.last_activity))]),
              h('div', { class: 'itd-thread__email' }, c.email && c.email !== c.person ? c.email : c.app),
              h('p', {}, c.subject + (c.preview ? ' — ' + c.preview : '')),
              h('div', { class: 'ph-flex ph-flex--wrap' }, [c.waiting ? badge('Waiting ' + (c.waiting_hours < 1 ? 'under an hour' : plural(c.waiting_hours, 'hour')), c.waiting_hours >= 24 ? 'bad' : 'warn') : badge(done ? c.status : 'Answered', done ? 'good' : ''), badge(c.app, 'outline')])
            ])
          ]);
        }) : empty('No conversations', onlyWaiting ? 'Nobody is waiting for an answer.' : 'Reports and chats from the apps appear here.'))
      ]);
      var chatSlot = h('div', { class: 'itd-slot' }, card('', '', empty('Pick a conversation', 'Read it and answer here. The answer reaches their phone as a notification.')));
      var infoSlot = h('div', { class: 'itd-slot itd-slot--info' });
      mount([
        filters([
          pills([['', 'All', d.rows.length], ['1', 'Waiting', d.waiting]], onlyWaiting ? '1' : '', function (v) { nav({ waiting: v }); }),
          appSelect(),
          d.statuses.length ? select([['', 'Any status']].concat(d.statuses.map(function (x) { return [x, x]; })), status, function (v) { nav({ status: v }); }, 'Status') : null
        ]),
        h('div', { class: 'itd-msgr' }, [list, chatSlot, infoSlot])
      ]);
      if (open) openConversation(open, chatSlot, infoSlot);
      timer = setInterval(function () {
        var t = document.querySelector('.itd-compose textarea');
        if (!document.hidden && !(t && t.value)) render();
      }, 30000);
    });
  }
  function openConversation(id, chatSlot, infoSlot) {
    return call('conversation', { id: id }).then(function (c) {
      var sendBtn;
      var msgs = h('div', { class: 'itd-msgs' }, c.messages.map(function (m) {
        return h('div', { class: 'itd-msg ' + (m.mine ? 'staff' : m.from_staff ? 'colleague' : 'person') }, [
          m.image ? h('a', { href: m.image, target: '_blank', rel: 'noopener' }, h('img', { src: m.image, alt: 'Screenshot sent with the message', loading: 'lazy', onload: function () { msgs.scrollTop = msgs.scrollHeight; } })) : null,
          h('div', { class: 'itd-msg__text' }, m.text),
          h('small', {}, (m.mine ? 'You' : (m.by || (m.from_staff ? 'Support' : c.person))) + ' · ' + ago(m.sent_at))
        ]);
      }));
      var text = h('textarea', { class: 'ph-control', rows: 1, placeholder: 'Write an answer…', oninput: function () { text.style.height = 'auto'; text.style.height = Math.min(text.scrollHeight, 160) + 'px'; }, onkeydown: function (e) { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendBtn.click(); } } });
      sendBtn = h('button', { class: 'ph-btn ph-btn--primary', type: 'button', onclick: function () {
        var v = text.value.trim();
        if (!v) return;
        sendBtn.disabled = true;
        call('send', { id: id, text: v }, true).then(function () { render(); }).catch(function (e) { fail(e); sendBtn.disabled = false; });
      } }, 'Send');
      var chat = h('section', { class: 'ph-card itd-chat' }, [
        h('div', { class: 'itd-pane__head' }, [
          h('div', { class: 'ph-avatar' }, initials(c.person)),
          h('div', { class: 'ph-grow' }, [h('div', { class: 'ph-card__title' }, c.person), h('div', { class: 'ph-muted', style: 'font-size:12px' }, (c.email && c.email !== c.person ? c.email + ' · ' : '') + c.subject)]),
          c.statuses.length ? select(c.statuses.map(function (x) { return [x, x]; }), c.status, function (v) { call('set_conversation_status', { id: id, status: v }, true).then(function () { toast('Status: ' + v, 'good'); render(); }).catch(fail); }, 'Status') : null
        ]),
        msgs,
        h('div', { class: 'itd-compose' }, [text, sendBtn]),
        h('div', { class: 'itd-hint' }, 'Enter sends · Shift+Enter for a new line · the answer arrives as a notification')
      ]);
      clear(chatSlot);
      chatSlot.appendChild(chat);
      msgs.scrollTop = msgs.scrollHeight;
      var kv = h('dl', { class: 'ph-kv' });
      [['Name', c.person], ['Email', c.email], ['App', c.app], ['App version', c.app_version], ['Device', c.device], ['System', c.os], ['Status', c.status]].forEach(function (p) {
        if (!p[1]) return;
        kv.appendChild(h('dt', {}, p[0]));
        kv.appendChild(h('dd', {}, p[1]));
      });
      var info = h('section', { class: 'ph-card itd-info' }, [h('div', { class: 'ph-card__head' }, h('div', { class: 'ph-card__title' }, 'Details')), kv]);
      if (c.developer) info.appendChild(h('details', { class: 'itd-dev' }, [h('summary', {}, 'Developer details'), h('pre', { class: 'itd-code' }, JSON.stringify(c.developer.diagnostics || {}, null, 2)), h('a', { class: 'ph-link', href: c.developer.desk_url }, 'Open in Desk')]));
      clear(infoSlot);
      infoSlot.appendChild(info);
    }).catch(function (e) { clear(chatSlot); chatSlot.appendChild(card('', '', errState(e))); });
  }

  /* ---------- Apps ---------- */
  function apps() {
    return call('apps').then(function (d) {
      var tot = d.rows.reduce(function (a, x) { a.people += x.people; a.behind += x.behind; a.problems += x.problems; a.tickets += x.open_tickets; return a; }, { people: 0, behind: 0, problems: 0, tickets: 0 });
      var rows = d.rows.map(function (a) {
        return h('tr', {}, [
          h('td', {}, who(a.app, a.version ? 'Version ' + a.version + (a.released_days_ago != null ? ' · released ' + days(a.released_days_ago) + (a.released_days_ago ? ' ago' : '') : '') : 'No release found yet')),
          h('td', { style: 'min-width:160px' }, a.adoption == null ? '—' : h('div', { class: 'ph-flex' }, [h('div', { class: 'ph-grow' }, progress(a.adoption)), h('span', { class: 'ph-strong' }, a.adoption + '%')])),
          h('td', { class: 'is-num' }, String(a.people)),
          h('td', { class: 'is-num' }, a.behind ? badge(String(a.behind), 'warn') : '0'),
          h('td', { class: 'is-num' }, String(a.away)),
          h('td', { class: 'is-num' }, a.problems ? badge(plural(a.problems, 'issue') + ' · ' + a.people_hurt + ' users', 'bad') : '0'),
          h('td', { class: 'is-num' }, String(a.open_tickets))
        ]);
      });
      var cols = [{ label: 'App' }, { label: 'On current version' }, { label: 'People', num: true }, { label: 'Behind', num: true }, { label: 'Away', num: true }, { label: 'Issues', num: true }, { label: 'Open tickets', num: true }];
      mount([
        head('IT dashboard · Apps', 'Your apps', 'Adoption is the share of people on the current version.', [checkButton(render)]),
        kpis([{ label: 'Apps', value: d.rows.length }, { label: 'People', value: tot.people }, { label: 'Behind', value: tot.behind }, { label: 'Open tickets', value: tot.tickets }]),
        card('', '', rows.length ? table(cols, rows) : empty('No apps are registered yet'), true)
      ]);
    });
  }

  /* ---------- App store ---------- */
  var BUCKET_BADGE = { current: ['Up to date', 'good'], forgot: ['Pending update', 'warn'], cant_update: ['Pending install', 'bad'], away: ['Not seen lately', ''], unknown: ['Unknown', ''] };
  function openInstallers(a) {
    var body = h('div', {}, h('div', { class: 'ph-muted' }, 'Loading…'));
    drawer(a.app, 'Who has installed it', body);
    call('store.installers', { app: a.app }).then(function (d) {
      clear(body);
      var behind = d.people.filter(function (p) { return p.bucket === 'forgot' || p.bucket === 'cant_update'; }).length;
      body.appendChild(h('div', { class: 'ph-muted', style: 'margin-bottom:var(--sp-3)' }, plural(d.people.length, 'person', 'people') + (d.latest ? ' · current version ' + d.latest : '') + (behind ? ' · ' + behind + ' behind' : '')));
      if (!d.people.length) { body.appendChild(empty('Nobody yet', 'Nobody has opened this app on a registered device.')); return; }
      d.people.forEach(function (p) {
        var b = BUCKET_BADGE[p.bucket] || BUCKET_BADGE.unknown;
        body.appendChild(h('div', { style: 'padding:var(--sp-3) 0;border-bottom:1px solid var(--hairline)' }, [
          h('div', { class: 'ph-flex ph-flex--between' }, [h('div', { class: 'ph-strong' }, p.name), badge(b[0], b[1])]),
          h('div', { class: 'ph-muted', style: 'font-size:12px' }, (p.name !== p.user ? p.user + ' · ' : '') + (p.team ? p.team + ' · ' : '') + (p.version ? 'version ' + p.version : 'version unknown')),
          h('div', { class: 'ph-muted', style: 'font-size:12px' }, (p.device ? p.device + ' · ' : '') + 'last opened ' + ago(p.last_seen))
        ]));
      });
    }).catch(function (e) { clear(body); body.appendChild(errState(e)); });
  }
  function checkButton(after) {
    var btn = h('button', { class: 'ph-btn ph-btn--primary', type: 'button', onclick: function () {
      var before = {};
      btn.disabled = true;
      btn.textContent = 'Checking…';
      call('store.apps').then(function (d) { d.apps.forEach(function (a) { before[a.app] = a.version; }); }).catch(function () { /* compare against nothing */ }).then(function () {
        return call('store.check_updates', {}, true);
      }).then(function (res) {
        var changed = res.apps.filter(function (a) { return before[a.app] && before[a.app] !== a.version; }).map(function (a) { return a.app + ' ' + a.version; });
        if (!res.refreshed) toast('Checked a moment ago. Try again in ' + res.retry_in + ' s.', 'warn');
        else toast(changed.length ? 'New: ' + changed.join(', ') : 'Everything is up to date.', 'good');
        after();
      }).catch(function (e) { fail(e); btn.disabled = false; btn.textContent = 'Check for updates'; });
    } }, 'Check for updates');
    return btn;
  }
  function store() {
    return call('store.apps').then(function (d) {
      var tiles = d.apps.map(function (a) {
        return h('div', { class: 'ph-tile' }, [
          h('div', { class: 'ph-tile__head' }, [h('div', {}, [h('div', { class: 'ph-tile__name' }, a.app), h('div', { class: 'ph-tile__sub' }, 'Version ' + a.version)]), badge('Android', 'signal')]),
          h('div', { class: 'ph-tile__stats' }, [
            h('div', {}, [h('small', {}, 'Version'), h('b', {}, a.version)]),
            h('div', {}, [h('small', {}, 'Released'), h('b', {}, dateOnly(a.released))]),
            h('div', {}, [h('small', {}, 'Size'), h('b', {}, a.size_mb ? a.size_mb + ' MB' : '—')])
          ]),
          h('div', { class: 'ph-stack' }, [
            h('a', { class: 'ph-btn ph-btn--primary ph-btn--block', href: BASE + 'store.download?' + qs({ app: a.app }), download: a.file }, 'Download APK'),
            IT ? h('button', { class: 'ph-btn ph-btn--ghost ph-btn--block', type: 'button', onclick: function () { openInstallers(a); } }, 'Who installed it') : null
          ])
        ]);
      });
      mount([
        head('Install', 'App store', 'The newest version of every app. Download it on your phone and open the file to install.', [checkButton(function () { store(); })]),
        h('div', { class: 'ph-notice ph-notice--signal' }, 'When Android asks, allow your browser to install apps from this source. Only the newest version is listed; installing over an existing app keeps its data.'),
        tiles.length ? h('div', { class: 'ph-tiles' }, tiles) : card('', '', empty('No apps to install yet', 'An app appears here once it has a published Android build.'))
      ]);
    });
  }

  /* ---------- boot ---------- */
  function wireShell() {
    var collapse = document.getElementById('ph-collapse');
    if (collapse) collapse.addEventListener('click', function () {
      var on = root.classList.toggle('is-collapsed');
      try { localStorage.setItem('ph2SideCollapsed', on ? '1' : '0'); } catch (e) { /* storage blocked */ }
    });
    var menu = document.getElementById('ph-menu-btn');
    if (menu) menu.addEventListener('click', function () { root.classList.add('is-drawer-open'); });
    var scrim = document.getElementById('ph-scrim');
    if (scrim) scrim.addEventListener('click', function () { root.classList.remove('is-drawer-open'); });
    if (main) main.addEventListener('scroll', function () { main.classList.toggle('is-scrolled', main.scrollTop > 4); }, { passive: true });
    var refresh = document.getElementById('ph-refresh');
    if (refresh) refresh.addEventListener('click', render);
  }
  wireShell();
  if (STORE) {
    store().then(markUpdated).catch(function (e) { mount(errState(e)); });
    return;
  }
  call('apps').then(function (d) { appList = d.rows.map(function (a) { return a.app; }); }).catch(function () { /* filter stays on All apps */ }).then(function () {
    window.addEventListener('hashchange', render);
    render();
  });
})();
