xxx/* Mobile Monitor dashboard — vanilla JS, no dependencies.
 * Every value from the server is put on the page with textContent (via h()), never innerHTML,
 * so a hostile request body or stack trace cannot run script in an admin's session. */
(function () {
  'use strict';

  var API = '/api/method/upande_mobile.api.monitor.';
  var view = document.getElementById('view');
  var appSel = document.getElementById('app-filter');
  var state = { app: '' };
  try { state.app = localStorage.getItem('mm-app') || ''; } catch (e) { /* storage may be blocked */ }

  /* ---------- tiny DOM helper: text only ---------- */
  function h(tag, attrs, kids) {
    var el = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === 'class') el.className = v;
      else if (k.slice(0, 2) === 'on') el.addEventListener(k.slice(2), v);
      else el.setAttribute(k, v === true ? '' : v);
    });
    (Array.isArray(kids) ? kids : kids == null ? [] : [kids]).forEach(function (c) {
      if (c == null || c === false) return;
      el.appendChild(typeof c === 'object' ? c : document.createTextNode(String(c)));
    });
    return el;
  }
  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function mount(nodes) { clear(view); [].concat(nodes).forEach(function (n) { if (n) view.appendChild(n); }); window.scrollTo(0, 0); }

  /* ---------- API ---------- */
  function csrf() { return document.body.getAttribute('data-csrf') || ''; }
  function call(fn, params, method) {
    var url = API + fn, opts = { credentials: 'same-origin', headers: { 'Accept': 'application/json' } };
    var qs = Object.keys(params || {}).filter(function (k) { return params[k] !== '' && params[k] != null; })
      .map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(params[k]); }).join('&');
    if (method === 'POST') {
      opts.method = 'POST';
      opts.headers['Content-Type'] = 'application/x-www-form-urlencoded';
      opts.headers['X-Frappe-CSRF-Token'] = csrf();
      opts.body = qs;
    } else if (qs) url += '?' + qs;
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) {
          var msg = 'Request failed (' + r.status + ')';
          try { var m = JSON.parse(j._server_messages || '[]'); if (m.length) msg = JSON.parse(m[0]).message || msg; } catch (e) { /* keep default */ }
          if (r.status === 401 || r.status === 403) msg = 'You need to be a signed-in System Manager to see this.';
          throw new Error(msg);
        }
        return j.message;
      });
    });
  }

  /* ---------- formatting ---------- */
  function num(n) { return Number(n || 0).toLocaleString(); }
  function when(ts) {
    if (!ts) return '—';
    var d = new Date(String(ts).replace(' ', 'T'));
    if (isNaN(d)) return String(ts);
    var s = (Date.now() - d.getTime()) / 1000;
    if (s < 0) s = 0;
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + ' min ago';
    if (s < 86400) return Math.floor(s / 3600) + ' h ago';
    if (s < 86400 * 30) return Math.floor(s / 86400) + ' d ago';
    return d.toISOString().slice(0, 10);
  }
  function full(ts) { return ts ? String(ts) : '—'; }
  function bytes(n) {
    n = Number(n || 0); if (!n) return '—';
    var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0;
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
    return n.toFixed(i > 1 ? 1 : 0) + ' ' + u[i];
  }
  function pct(x) { return x ? Math.round(Number(x) * 100) + '%' : '—'; }
  function badge(text, cls) { return h('span', { class: 'badge ' + (cls || text) }, text); }
  function stat(label, value, cls, href) {
    return h(href ? 'a' : 'div', { class: 'card stat ' + (cls || ''), href: href }, [h('div', { class: 'n' }, num(value)), h('div', { class: 'l' }, label)]);
  }
  function card(title, body) { return h('section', { class: 'card' }, [title ? h('h2', {}, title) : null].concat(body)); }
  function empty(text) { return h('p', { class: 'empty' }, text); }
  function errBox(e) { return h('div', { class: 'err', role: 'alert' }, e.message || String(e)); }
  function toast(text) {
    var t = h('div', { class: 'toast', role: 'status' }, text); document.body.appendChild(t);
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, 2200);
  }
  function route() { var p = (location.hash || '#/overview').slice(2).split('?'); return { parts: p[0].split('/').map(decodeURIComponent), q: new URLSearchParams(p[1] || '') }; }
  function go(path, q) { var s = Object.keys(q || {}).filter(function (k) { return q[k] !== '' && q[k] != null; }).map(function (k) { return encodeURIComponent(k) + '=' + encodeURIComponent(q[k]); }).join('&'); location.hash = '#/' + path + (s ? '?' + s : ''); }
  function pretty(v) { if (v == null || v === '') return ''; if (typeof v === 'string') { try { return JSON.stringify(JSON.parse(v), null, 2); } catch (e) { return v; } } return JSON.stringify(v, null, 2); }

  function kv(pairs) {
    var dl = h('dl', { class: 'kv' });
    pairs.forEach(function (p) { if (p[1] == null || p[1] === '') return; dl.appendChild(h('dt', {}, p[0])); dl.appendChild(h('dd', {}, p[1])); });
    return dl;
  }
  function listRows(rows, label) {
    if (!rows || !rows.length) return empty('Nothing yet.');
    return h('div', {}, rows.map(function (r) { return h('div', { class: 'list-row' }, [h('span', {}, r.label || '—'), h('span', { class: 'muted' }, num(r.n))]); }));
  }
  function pager(data, onPage) {
    var from = data.total ? data.start + 1 : 0, to = Math.min(data.start + data.page_length, data.total);
    return h('div', { class: 'pager' }, [
      h('span', {}, from + '–' + to + ' of ' + num(data.total)),
      h('button', { class: 'small', disabled: data.start <= 0, onclick: function () { onPage(Math.max(0, data.start - data.page_length)); } }, 'Prev'),
      h('button', { class: 'small', disabled: to >= data.total, onclick: function () { onPage(data.start + data.page_length); } }, 'Next')
    ]);
  }

  /* ---------- secrets for copy-as-curl ---------- */
  var SENSITIVE = /(token|secret|passw|pwd|apikey|api[-_]?key|authorization|cookie|credential|csrf|session|signature)/i;
  function shq(s) { return "'" + String(s).replace(/'/g, "'\\''") + "'"; }
  function curlOf(req) {
    if (!req || !req.url) return '';
    var parts = ['curl -X ' + (req.method || 'GET') + ' ' + shq(req.url)];
    var hs = req.request_headers || {};
    Object.keys(hs).forEach(function (k) {
      var v = SENSITIVE.test(k) || /\[redacted\]/i.test(String(hs[k])) ? '<REDACTED>' : hs[k];
      if (/^(content-length|host|connection)$/i.test(k)) return;
      parts.push('-H ' + shq(k + ': ' + v));
    });
    if (req.request_body && !/^\[.* body, \d+/.test(req.request_body)) parts.push('--data-raw ' + shq(req.request_body));
    return parts.join(' \\\n  ');
  }
  function copy(text) {
    function done() { toast('Copied'); }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, function () { toast('Copy failed'); });
    else { var ta = h('textarea', { style: 'position:fixed;opacity:0' }); ta.value = text; document.body.appendChild(ta); ta.select(); try { document.execCommand('copy'); done(); } catch (e) { toast('Copy failed'); } document.body.removeChild(ta); }
  }

  /* ---------- views ---------- */
  function chart(series) {
    var max = 1;
    series.forEach(function (d) { max = Math.max(max, d.crash + d.error + d.api + d.log); });
    var cols = series.map(function (d) {
      var total = d.crash + d.error + d.api + d.log;
      var col = h('div', { class: 'col', title: d.day + ': ' + total + ' events' });
      ['log', 'error', 'api', 'crash'].forEach(function (k) { if (d[k]) col.appendChild(h('div', { class: 'seg ' + k, style: 'height:' + (d[k] / max * 100).toFixed(1) + '%' })); });
      return col;
    });
    var show = series.length > 14 ? 7 : 1;
    return h('div', {}, [
      h('div', { class: 'bars' }, cols),
      h('div', { class: 'bars-x' }, series.map(function (d, i) { return h('span', {}, i % show === 0 ? d.day.slice(5) : ''); })),
      h('div', { class: 'legend' }, [['crash', 'Crashes'], ['api', 'API errors'], ['error', 'Errors'], ['log', 'Logs']].map(function (k) {
        return h('span', {}, [h('i', { class: 'seg ' + k[0], style: 'background:var(--' + ({ crash: 'bad', api: 'warn', error: 'accent', log: 'muted' }[k[0]]) + ')' }), k[1]]);
      }))
    ]);
  }

  function issueTable(rows) {
    if (!rows.length) return empty('No issues match.');
    return h('div', { class: 'table-wrap' }, h('table', {}, [
      h('thead', {}, h('tr', {}, ['Issue', 'Status', 'Events', 'Users', 'Last seen', 'Version'].map(function (t, i) { return h('th', { class: i >= 2 && i <= 3 ? 'num' : '' }, t); }))),
      h('tbody', {}, rows.map(function (r) {
        return h('tr', {}, [
          h('td', {}, [h('a', { class: 'title', href: '#/issue/' + encodeURIComponent(r.name) }, r.title || r.name), h('div', { class: 'sub' }, [badge(r.kind), ' ', badge(r.level), ' ', r.culprit || '', r.app ? ' · ' + r.app : ''])]),
          h('td', {}, badge(r.status)),
          h('td', { class: 'num' }, num(r.event_count || r.events_in_range)),
          h('td', { class: 'num' }, num(r.users_affected)),
          h('td', { title: full(r.last_seen) }, when(r.last_seen)),
          h('td', {}, r.last_version || '—')
        ]);
      }))
    ]));
  }

  function renderOverview(q) {
    var days = Number(q.get('days')) || 7;
    return call('overview', { app: state.app, days: days }).then(function (d) {
      fillApps(d.apps);
      var t = d.totals, hl = d.health || {};
      var range = h('select', { 'aria-label': 'Range', onchange: function (e) { go('overview', { days: e.target.value }); } }, [1, 7, 14, 30, 90].map(function (n) { return h('option', { value: n, selected: n === d.days }, 'Last ' + (n === 1 ? '24 hours' : n + ' days')); }));
      mount([
        h('div', { class: 'actions' }, [h('h1', { style: 'margin:0;flex:1' }, 'Overview'), range]),
        h('div', { class: 'grid cards' }, [
          stat('Events', t.events), stat('Crashes', t.crashes, t.crashes ? 'bad' : ''), stat('API errors', t.api_errors, t.api_errors ? 'warn' : ''),
          stat('Devices affected', t.devices), stat('Users affected', t.users),
          stat('Open issues', d.issues_by_status.Open, d.issues_by_status.Open ? 'bad' : '', '#/issues?status=Open')
        ]),
        h('div', { class: 'grid two' }, [
          card('Events per day', chart(d.series)),
          card('Fleet health', [
            h('div', { class: 'grid cards', style: 'margin:0' }, [
              stat('Low battery', hl.low_battery, hl.low_battery ? 'warn' : '', '#/devices?health=low_battery'),
              stat('Low storage', hl.low_storage, hl.low_storage ? 'warn' : '', '#/devices?health=low_storage'),
              stat('Offline', hl.offline, '', '#/devices?health=offline'),
              stat('Old OS', hl.outdated_os, hl.outdated_os ? 'warn' : '', '#/devices?health=outdated_os'),
              stat('Old app', hl.outdated_app, '', '#/devices?health=outdated_app')
            ]),
            h('p', { class: 'muted' }, num(hl.devices) + ' registered devices.')
          ])
        ]),
        card('Top issues', issueTable(d.top_issues)),
        h('div', { class: 'grid two', style: 'margin-top:12px' }, [
          card('App versions', listRows(d.versions)), card('Device models', listRows(d.models)), card('OS versions', listRows(d.os_versions))
        ])
      ]);
    });
  }

  function renderIssues(q) {
    var f = { status: q.get('status') || '', kind: q.get('kind') || '', level: q.get('level') || '', version: q.get('version') || '', search: q.get('search') || '', start: Number(q.get('start')) || 0 };
    function sel(name, label, opts) {
      return h('select', { 'aria-label': label, onchange: function (e) { var n = Object.assign({}, f, { start: 0 }); n[name] = e.target.value; go('issues', n); } },
        [h('option', { value: '' }, label)].concat(opts.map(function (o) { return h('option', { value: o, selected: f[name] === o }, o); })));
    }
    var search = h('input', { type: 'search', placeholder: 'Search title or location…', value: f.search, 'aria-label': 'Search' });
    search.addEventListener('keydown', function (e) { if (e.key === 'Enter') go('issues', Object.assign({}, f, { search: search.value.trim(), start: 0 })); });
    return call('issues', { app: state.app, status: f.status, kind: f.kind, level: f.level, version: f.version, search: f.search, start: f.start, page_length: 25 }).then(function (d) {
      mount([
        h('h1', {}, 'Issues'),
        h('div', { class: 'filters' }, [search, sel('status', 'Any status', ['Open', 'Resolved', 'Ignored']), sel('kind', 'Any kind', ['crash', 'error', 'api', 'log']), sel('level', 'Any level', ['fatal', 'error', 'warning', 'info']),
          h('input', { type: 'text', placeholder: 'Version', value: f.version, size: 8, 'aria-label': 'Version', onchange: function (e) { go('issues', Object.assign({}, f, { version: e.target.value.trim(), start: 0 })); } })]),
        card(null, [issueTable(d.rows), pager(d, function (s) { go('issues', Object.assign({}, f, { start: s })); })])
      ]);
    });
  }

  function requestViewer(req) {
    if (!req) return null;
    var tab = 'response', box = h('pre', { class: 'code' }), tabs = h('div', { class: 'tabs' });
    var views = {
      request: function () { return [req.method + ' ' + req.url, '', pretty(req.request_headers), '', pretty(req.request_body)].join('\n').trim(); },
      response: function () { return ['HTTP ' + (req.status || '—') + (req.error ? ' — ' + req.error : '') + (req.duration_ms ? '  (' + req.duration_ms + ' ms)' : ''), '', pretty(req.response_headers), '', pretty(req.response_body)].join('\n').trim(); }
    };
    function draw() {
      box.textContent = views[tab]();
      clear(tabs);
      ['request', 'response'].forEach(function (k) { tabs.appendChild(h('button', { class: k === tab ? 'on' : '', type: 'button', onclick: function () { tab = k; draw(); } }, k === 'request' ? 'Request' : 'Response')); });
      tabs.appendChild(h('button', { type: 'button', class: 'small', onclick: function () { copy(curlOf(req)); } }, 'Copy as cURL'));
    }
    draw();
    return card('API request' + (req.truncated ? ' (bodies truncated)' : ''), [h('p', {}, [badge(req.method || 'GET', 'api'), ' ', badge(String(req.status || 'error'), req.status >= 500 || !req.status ? 'bad' : 'warn'), ' ', h('span', { class: 'mono' }, req.url || '')]), tabs, box,
      h('p', { class: 'muted' }, 'Secrets are removed before storage; copied cURL keeps the placeholders.')]);
  }

  function devicePanel(ev) {
    var d = ev.device_info || {}, s = d.screen || {};
    return card('Device', [kv([
      ['Model', [ev.device_brand, ev.device_model].filter(Boolean).join(' ') || d.model], ['Name', d.device_name], ['OS', [ev.platform, ev.os_version].filter(Boolean).join(' ') + (d.os_build ? ' (' + d.os_build + ')' : '')],
      ['Physical device', d.is_physical_device == null ? '' : d.is_physical_device ? 'yes' : 'no (emulator)'],
      ['Battery', d.battery_level != null ? pct(d.battery_level) + (d.battery_state ? ' · ' + d.battery_state : '') + (d.low_power_mode ? ' · low power mode' : '') : ''],
      ['Storage free', d.storage_free_bytes != null ? bytes(d.storage_free_bytes) + ' of ' + bytes(d.storage_total_bytes) : ''], ['Memory', d.memory_total_bytes ? bytes(d.memory_total_bytes) : ''],
      ['Network', [d.network_type, d.is_connected === false ? 'not connected' : '', d.is_internet_reachable === false ? 'no internet' : '', d.carrier].filter(Boolean).join(' · ')],
      ['Screen', s.width ? s.width + '×' + s.height + ' @' + s.scale + 'x, font ' + s.font_scale : ''], ['Orientation', d.orientation], ['Locale', [d.locale, d.timezone].filter(Boolean).join(' · ')],
      ['Missing in this build', (d.missing_modules || []).join(', ')], ['Theme', d.color_scheme], ['App state', d.app_state], ['JS engine', d.js_engine], ['Build', d.build_type], ['Install id', ev.install_id], ['IP seen', ev.ip_address]
    ])]);
  }
  function userPanel(ev) {
    var u = ev.user_info || {};
    return card('User and build', [kv([
      ['User', u.id || ev.user || 'not signed in'], ['Name', u.full_name || ev.user_full_name], ['Roles', (u.roles || []).join(', ')],
      ['App version', ev.app_version], ['Runtime', ev.runtime_version], ['OTA update', ev.update_id], ['Screen', ev.screen], ['Session', ev.session_id], ['Occurred', full(ev.ts)], ['Received', full(ev.received_at)]
    ])]);
  }
  function crumbPanel(ev) {
    var c = ev.breadcrumbs;
    if (!c || !c.length) return null;
    return card('Breadcrumbs (oldest first)', h('ul', { class: 'crumbs' }, c.map(function (b) {
      return h('li', {}, [h('span', { class: 't' }, String(b.ts || '').slice(11, 23)), h('span', {}, badge(b.type || '·')), h('span', {}, [b.message || '', b.data ? h('div', { class: 'mono muted' }, JSON.stringify(b.data)) : null])]);
    })));
  }

  function renderIssue(name) {
    return call('issue', { name: name }).then(function (d) {
      var i = d.issue, ev = d.latest;
      function setStatus(s) {
        var v = s === 'Resolved' ? (prompt('Fixed in which app version? Leave empty for ' + (i.last_version || 'the last seen version') + '.', '') || '') : '';
        call('set_issue_status', { name: i.name, status: s, resolved_in_version: v }, 'POST').then(function () { toast('Marked ' + s); route_(); }).catch(function (e) { toast(e.message); });
      }
      mount([
        h('a', { class: 'back', href: '#/issues' }, '← Issues'),
        h('h1', { style: 'word-break:break-word' }, i.title || i.name),
        h('div', { class: 'actions' }, [badge(i.status), badge(i.kind), badge(i.level), h('span', { class: 'muted' }, [i.app, ' · ', num(i.event_count), ' events · ', num(i.users_affected), ' users · ', num(i.devices_affected), ' devices · first ', when(i.first_seen), ' · last ', when(i.last_seen)]),
          h('span', { style: 'flex:1' }),
          i.status !== 'Resolved' ? h('button', { class: 'primary', type: 'button', onclick: function () { setStatus('Resolved'); } }, 'Resolve') : h('button', { type: 'button', onclick: function () { setStatus('Open'); } }, 'Reopen'),
          i.status !== 'Ignored' ? h('button', { type: 'button', onclick: function () { setStatus('Ignored'); } }, 'Ignore') : null]),
        ev ? h('div', { class: 'grid', style: 'gap:12px' }, [
          ev.stack ? card('Stack trace', h('pre', { class: 'code' }, ev.stack)) : (ev.message ? card('Message', h('pre', { class: 'code' }, ev.message)) : null),
          requestViewer(ev.request),
          h('div', { class: 'grid two', style: 'margin:0' }, [devicePanel(ev), userPanel(ev)]),
          crumbPanel(ev),
          ev.extra ? card('Extra', h('pre', { class: 'code' }, pretty(ev.extra))) : null
        ]) : empty('No stored events (older events were purged, or the issue is ignored).'),
        h('div', { class: 'grid two', style: 'margin-top:12px' }, [card('By app version', listRows(d.by_version)), card('By device model', listRows(d.by_model))]),
        card('Recent events', eventTable(d.events))
      ]);
    });
  }

  function eventTable(rows) {
    if (!rows.length) return empty('No events.');
    return h('div', { class: 'table-wrap' }, h('table', {}, [
      h('thead', {}, h('tr', {}, ['When', 'Event', 'User', 'Device', 'Version'].map(function (t) { return h('th', {}, t); }))),
      h('tbody', {}, rows.map(function (r) {
        return h('tr', {}, [
          h('td', { title: full(r.ts) }, when(r.ts)),
          h('td', {}, [h('div', { class: 'title' }, r.kind === 'api' ? (r.req_method + ' ' + r.req_url + ' → ' + (r.req_status || 'error')) : (r.message || '').slice(0, 160)), h('div', { class: 'sub' }, [badge(r.kind), ' ', r.screen || ''])]),
          h('td', {}, r.user_full_name || r.user || '—'),
          h('td', {}, r.install_id ? h('a', { href: '#/device/' + encodeURIComponent(r.install_id) }, [r.device_brand, r.device_model].filter(Boolean).join(' ') || r.install_id.slice(0, 8)) : '—'),
          h('td', {}, [r.app_version || '—', h('div', { class: 'sub' }, [r.platform, r.os_version].filter(Boolean).join(' '))])
        ]);
      }))
    ]));
  }

  var FLAG_TEXT = { low_battery: 'Low battery', low_storage: 'Low storage', offline: 'Offline', outdated_os: 'Old OS', outdated_app: 'Old app' };
  function renderDevices(q) {
    var f = { health: q.get('health') || '', search: q.get('search') || '', start: Number(q.get('start')) || 0 };
    var search = h('input', { type: 'search', placeholder: 'Search user, model or install id…', value: f.search, 'aria-label': 'Search devices' });
    search.addEventListener('keydown', function (e) { if (e.key === 'Enter') go('devices', { health: f.health, search: search.value.trim() }); });
    return call('devices', { app: state.app, health: f.health, search: f.search, start: f.start, page_length: 25 }).then(function (d) {
      var s = d.summary || {};
      var tabs = h('div', { class: 'tabs' }, [['', 'All (' + num(s.devices) + ')']].concat(Object.keys(FLAG_TEXT).map(function (k) { return [k, FLAG_TEXT[k] + ' (' + num(s[k]) + ')']; })).map(function (t) {
        return h('button', { class: f.health === t[0] ? 'on' : '', type: 'button', onclick: function () { go('devices', { health: t[0], search: f.search }); } }, t[1]);
      }));
      var table = d.rows.length ? h('div', { class: 'table-wrap' }, h('table', {}, [
        h('thead', {}, h('tr', {}, ['Device', 'User', 'OS', 'App', 'Battery', 'Storage free', 'Network', 'Last seen', 'Health'].map(function (t) { return h('th', {}, t); }))),
        h('tbody', {}, d.rows.map(function (r) {
          return h('tr', {}, [
            h('td', {}, [h('a', { class: 'title', href: '#/device/' + encodeURIComponent(r.install_id) }, [r.device_brand, r.device_model].filter(Boolean).join(' ') || r.device_name || r.install_id.slice(0, 8)), h('div', { class: 'sub' }, r.app)]),
            h('td', {}, r.full_name || r.user || '—'),
            h('td', {}, [r.platform, r.os_version].filter(Boolean).join(' ') || '—'),
            h('td', {}, [r.app_version || '—', h('div', { class: 'sub' }, r.status || '')]),
            h('td', { title: r.snapshot_at ? 'Reported ' + full(r.snapshot_at) : 'No health report yet' }, r.snapshot_at ? pct(r.battery_level) : '—'),
            h('td', {}, r.snapshot_at ? bytes(r.storage_free_bytes) : '—'),
            h('td', {}, r.snapshot_at ? (r.connectivity || r.network_type || '—') : '—'),
            h('td', { title: full(r.last_seen) }, when(r.last_seen)),
            h('td', {}, r.flags.length ? h('div', { class: 'flags' }, r.flags.map(function (k) { return badge(FLAG_TEXT[k], 'warn'); })) : badge('OK', 'ok'))
          ]);
        }))
      ])) : empty('No devices match.');
      mount([h('h1', {}, 'Devices'), h('div', { class: 'filters' }, [search]), tabs,
        card(null, [table, pager(d, function (st) { go('devices', { health: f.health, search: f.search, start: st }); })]),
        h('p', { class: 'muted' }, 'Health comes from each device’s latest report. “Old OS” means below Android ' + d.min_os.android + ' / iOS ' + d.min_os.ios + '.')]);
    });
  }

  function renderDevice(id) {
    return Promise.all([call('devices', { search: id, page_length: 5 }), call('events', { install_id: id, page_length: 50 })]).then(function (res) {
      var dev = (res[0].rows || []).filter(function (r) { return r.install_id === id; })[0], ev = res[1];
      var snap = dev && dev.snapshot ? dev.snapshot : {};
      mount([
        h('a', { class: 'back', href: '#/devices' }, '← Devices'),
        h('h1', {}, dev ? ([dev.device_brand, dev.device_model].filter(Boolean).join(' ') || id) : id),
        dev ? h('div', { class: 'grid two' }, [
          card('Last known state', [kv([['User', dev.full_name || dev.user], ['App', dev.app + ' ' + (dev.app_version || '')], ['Register status', dev.status], ['OS', [dev.platform, dev.os_version].join(' ')], ['Last seen', full(dev.last_seen)],
            ['Battery', dev.snapshot_at ? pct(dev.battery_level) + (snap.battery_state ? ' · ' + snap.battery_state : '') : ''], ['Storage free', dev.snapshot_at ? bytes(dev.storage_free_bytes) + ' of ' + bytes(dev.storage_total_bytes) : ''],
            ['Network', [dev.network_type, dev.connectivity].filter(Boolean).join(' · ')], ['Snapshot', full(dev.snapshot_at)], ['Install id', dev.install_id]]),
            dev.flags.length ? h('div', { class: 'flags', style: 'margin-top:8px' }, dev.flags.map(function (k) { return badge(FLAG_TEXT[k], 'warn'); })) : null]),
          card('Reported device facts', h('pre', { class: 'code' }, pretty(snap) || 'No health report yet.'))
        ]) : h('p', { class: 'muted' }, 'This device is not in the register (events only).'),
        card('Timeline (' + num(ev.total) + ' events)', eventTable(ev.rows))
      ]);
    });
  }

  /* ---------- shell ---------- */
  function fillApps(apps) {
    if (!apps || appSel.options.length > 1) return;
    apps.forEach(function (a) { appSel.appendChild(h('option', { value: a.name, selected: a.name === state.app }, a.name + (a.enable_monitoring ? '' : ' (monitoring off)'))); });
    appSel.value = state.app;
  }
  appSel.addEventListener('change', function () {
    state.app = appSel.value;
    try { localStorage.setItem('mm-app', state.app); } catch (e) { /* ignore */ }
    route_();
  });
  document.getElementById('theme').addEventListener('click', function () {
    var cur = document.documentElement.getAttribute('data-theme') || (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    var next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('mm-theme', next); } catch (e) { /* ignore */ }
  });
  try { var th = localStorage.getItem('mm-theme'); if (th) document.documentElement.setAttribute('data-theme', th); } catch (e) { /* ignore */ }

  function route_() {
    var r = route(), p = r.parts[0] || 'overview';
    [].forEach.call(document.querySelectorAll('#nav a'), function (a) { a.className = a.getAttribute('data-route') === (p === 'issue' ? 'issues' : p === 'device' ? 'devices' : p) ? 'on' : ''; });
    mount(h('p', { class: 'muted' }, 'Loading…'));
    var job = p === 'issues' ? renderIssues(r.q) : p === 'issue' ? renderIssue(r.parts[1] || '') : p === 'devices' ? renderDevices(r.q) : p === 'device' ? renderDevice(r.parts[1] || '') : renderOverview(r.q);
    job.catch(function (e) { mount(errBox(e)); });
  }
  window.addEventListener('hashchange', route_);
  call('overview', { days: 1 }).then(function (d) { fillApps(d.apps); }).catch(function () { /* the view shows the error */ }).then(route_);
})();
