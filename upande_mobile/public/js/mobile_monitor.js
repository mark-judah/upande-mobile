/* Mobile Monitor dashboard — vanilla JS, no dependencies.
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
    var url = (fn.indexOf('support.') === 0 ? '/api/method/upande_mobile.api.' : API) + fn, opts = { credentials: 'same-origin', headers: { 'Accept': 'application/json' } };
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
  function appLink(name) { return name ? h('a', { href: '#/app/' + encodeURIComponent(name) }, name) : '—'; }
  function setApp(name) {
    state.app = name || '';
    try { localStorage.setItem('mm-app', state.app); } catch (e) { /* ignore */ }
    appSel.value = state.app;
  }
  /* Only plain http(s) links are ever made clickable, always opened without access to this page. */
  function safeHref(u) { return /^https?:\/\/[^\s]+$/i.test(String(u || '')) ? String(u) : null; }
  function extLink(label, url) { var u = safeHref(url); return u ? h('a', { href: u, target: '_blank', rel: 'noopener noreferrer' }, label) : null; }
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
          h('td', {}, [h('a', { class: 'title', href: '#/issue/' + encodeURIComponent(r.name) }, r.title || r.name), h('div', { class: 'sub' }, [badge(r.kind), ' ', badge(r.level), ' ', r.culprit || '', r.app ? ' · ' : null, r.app ? appLink(r.app) : null])]),
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
        d.apps && d.apps.length ? card('Apps', appChips(d.apps)) : null,
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

  // Connection details a device reported: Wi-Fi name, signal, link speed and band; round trip to the server; how long its calls take.
  function wifiText(d) {
    return [d.wifi_ssid, d.wifi_signal_pct != null ? d.wifi_signal_pct + '% signal' : '', d.wifi_link_mbps != null ? d.wifi_link_mbps + ' Mbps' : '',
      d.wifi_frequency_mhz != null ? (d.wifi_frequency_mhz >= 4900 ? '5 GHz' : '2.4 GHz') : '', d.cellular_generation ? d.cellular_generation.toUpperCase() : ''].filter(Boolean).join(' · ');
  }
  function apiText(d) {
    if (d.api_avg_ms == null) return '';
    return 'avg ' + d.api_avg_ms + ' ms · p95 ' + d.api_p95_ms + ' ms · ' + (d.api_error_pct || 0) + '% failed (last ' + Math.min(d.api_calls || 0, 100) + ' calls)';
  }
  function connCell(r) {
    var s = r.snapshot || {};
    if (!r.snapshot_at) return '—';
    var top = [s.wifi_ssid || r.connectivity || r.network_type, s.latency_ms != null ? s.latency_ms + ' ms' : ''].filter(Boolean).join(' · ');
    return h('div', {}, [top || '—', s.api_avg_ms != null ? h('div', { class: 'sub' }, 'API avg ' + s.api_avg_ms + ' ms') : null]);
  }

  function devicePanel(ev) {
    var d = ev.device_info || {}, s = d.screen || {};
    return card('Device', [kv([
      ['Model', [ev.device_brand, ev.device_model].filter(Boolean).join(' ') || d.model], ['Name', d.device_name], ['OS', [ev.platform, ev.os_version].filter(Boolean).join(' ') + (d.os_build ? ' (' + d.os_build + ')' : '')],
      ['Physical device', d.is_physical_device == null ? '' : d.is_physical_device ? 'yes' : 'no (emulator)'],
      ['Battery', d.battery_level != null ? pct(d.battery_level) + (d.battery_state ? ' · ' + d.battery_state : '') + (d.low_power_mode ? ' · low power mode' : '') : ''],
      ['Storage free', d.storage_free_bytes != null ? bytes(d.storage_free_bytes) + ' of ' + bytes(d.storage_total_bytes) : ''], ['Memory', d.memory_total_bytes ? bytes(d.memory_total_bytes) : ''],
      ['Network', [d.network_type, d.is_connected === false ? 'not connected' : '', d.is_internet_reachable === false ? 'no internet' : '', d.carrier].filter(Boolean).join(' · ')],
      ['Wi-Fi', wifiText(d)], ['Server latency', d.latency_ms != null ? d.latency_ms + ' ms' : ''], ['API response time', apiText(d)],
      ['Screen', s.width ? s.width + '×' + s.height + ' @' + s.scale + 'x, font ' + s.font_scale : ''], ['Orientation', d.orientation], ['Locale', [d.locale, d.timezone].filter(Boolean).join(' · ')],
      ['Missing in this build', (d.missing_modules || []).join(', ')], ['Theme', d.color_scheme], ['App state', d.app_state], ['JS engine', d.js_engine], ['Mobile Kit', d.kit_version ? 'v' + d.kit_version : ''], ['Build', d.build_type], ['Install id', ev.install_id], ['IP seen', ev.ip_address]
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
        h('div', { class: 'actions' }, [badge(i.status), badge(i.kind), badge(i.level), h('span', { class: 'muted' }, [appLink(i.app), ' · ', num(i.event_count), ' events · ', num(i.users_affected), ' users · ', num(i.devices_affected), ' devices · first ', when(i.first_seen), ' · last ', when(i.last_seen)]),
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
        h('thead', {}, h('tr', {}, ['Device', 'User', 'OS', 'App', 'Battery', 'Storage free', 'Connection', 'Last seen', 'Health'].map(function (t) { return h('th', {}, t); }))),
        h('tbody', {}, d.rows.map(function (r) {
          return h('tr', {}, [
            h('td', {}, [h('a', { class: 'title', href: '#/device/' + encodeURIComponent(r.install_id) }, [r.device_brand, r.device_model].filter(Boolean).join(' ') || r.device_name || r.install_id.slice(0, 8)), h('div', { class: 'sub' }, appLink(r.app))]),
            h('td', {}, r.full_name || r.user || '—'),
            h('td', {}, [r.platform, r.os_version].filter(Boolean).join(' ') || '—'),
            h('td', {}, [r.app_version || '—', h('div', { class: 'sub' }, r.status || '')]),
            h('td', { title: r.snapshot_at ? 'Reported ' + full(r.snapshot_at) : 'No health report yet' }, r.snapshot_at ? pct(r.battery_level) : '—'),
            h('td', {}, r.snapshot_at ? bytes(r.storage_free_bytes) : '—'),
            h('td', {}, connCell(r)),
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
          card('Last known state', [kv([['User', dev.full_name || dev.user], ['App', [appLink(dev.app), ' ' + (dev.app_version || '')]], ['Register status', dev.status], ['OS', [dev.platform, dev.os_version].join(' ')], ['Last seen', full(dev.last_seen)],
            ['Battery', dev.snapshot_at ? pct(dev.battery_level) + (snap.battery_state ? ' · ' + snap.battery_state : '') : ''], ['Storage free', dev.snapshot_at ? bytes(dev.storage_free_bytes) + ' of ' + bytes(dev.storage_total_bytes) : ''],
            ['Mobile Kit', snap.kit_version ? 'v' + snap.kit_version : ''], ['Network', [dev.network_type, dev.connectivity].filter(Boolean).join(' · ')], ['Wi-Fi', wifiText(snap)], ['Server latency', snap.latency_ms != null ? snap.latency_ms + ' ms' : ''], ['API response time', apiText(snap)], ['Snapshot', full(dev.snapshot_at)], ['Install id', dev.install_id]]),
            dev.flags.length ? h('div', { class: 'flags', style: 'margin-top:8px' }, dev.flags.map(function (k) { return badge(FLAG_TEXT[k], 'warn'); })) : null]),
          card('Reported device facts', h('pre', { class: 'code' }, pretty(snap) || 'No health report yet.'))
        ]) : h('p', { class: 'muted' }, 'This device is not in the register (events only).'),
        card('Timeline (' + num(ev.total) + ' events)', eventTable(ev.rows))
      ]);
    });
  }


  /* ---------- one app: what is going on, what version, who has it, what is new ---------- */
  function appChips(apps) {
    return h('div', { class: 'chips' }, apps.map(function (a) {
      return h('a', { class: 'chip', href: '#/app/' + encodeURIComponent(a.name) }, [a.name, a.enabled ? null : ' (disabled)', a.enable_monitoring ? null : ' (monitoring off)']);
    }));
  }
  function inlineNodes(nodes) {
    return (nodes || []).map(function (n) {
      if (n.k === 'b') return h('strong', {}, n.v);
      if (n.k === 'code') return h('code', {}, n.v);
      if (n.k === 'a') { var u = safeHref(n.href); return u ? h('a', { href: u, target: '_blank', rel: 'noopener noreferrer' }, n.v) : String(n.v); }
      return String(n.v == null ? '' : n.v);
    });
  }
  /* Release notes arrive as a small block structure and are built from text nodes: GitHub's text is never parsed as HTML. */
  function renderNotes(blocks) {
    if (!blocks || !blocks.length) return h('p', { class: 'muted' }, 'This release has no notes.');
    return h('div', { class: 'notes' }, blocks.map(function (b) {
      if (b.t === 'h') return h('h' + Math.min(Math.max(b.level || 4, 3), 6), {}, inlineNodes(b.inline));
      if (b.t === 'ul') return h('ul', {}, (b.items || []).map(function (it) { return h('li', {}, inlineNodes(it)); }));
      return h('p', {}, inlineNodes(b.inline));
    }));
  }
  function kindBadge(r) { return badge(r.has_apk ? 'APK' : 'OTA', r.has_apk ? 'api' : 'ok'); }
  function releaseLine(r) {
    return h('div', { class: 'actions' }, [kindBadge(r), h('span', { class: 'muted', title: full(r.published_at) }, r.published_at ? 'published ' + when(r.published_at) : ''),
      r.has_apk && r.apk_size ? h('span', { class: 'muted' }, 'APK ' + bytes(r.apk_size)) : null, extLink('Open on GitHub', r.url)]);
  }
  var RUN_BADGE = { success: 'ok', failure: 'bad', timed_out: 'bad', startup_failure: 'bad', cancelled: 'warn', action_required: 'warn' };
  function runBadge(r) {
    if (r.status && r.status !== 'completed') return badge(r.status.replace(/_/g, ' '), 'warn');
    return badge((r.conclusion || 'unknown').replace(/_/g, ' '), RUN_BADGE[r.conclusion] || 'info');
  }
  function meters(rows, key, unknownLabel) {
    if (!rows || !rows.length) return empty('No devices registered yet.');
    return h('div', {}, rows.map(function (r) {
      var label = r[key] || unknownLabel;
      return h('div', { class: 'dist' }, [
        h('div', { class: 'dist-top' }, [h('span', { class: r.latest ? 'latest' : '' }, [label, r.latest ? ' (latest)' : '']), h('span', { class: 'muted' }, num(r.devices) + ' · ' + r.percent + '%')]),
        h('div', { class: 'meter' + (r.latest ? ' latest' : ''), role: 'img', 'aria-label': label + ': ' + r.percent + ' percent of devices' }, h('i', { style: 'width:' + Math.max(r.percent, r.devices ? 1 : 0) + '%' }))
      ]);
    }));
  }
  function refreshButton(name, cooldown, reload) {
    var btn = h('button', { type: 'button', class: 'small' }, 'Refresh from GitHub');
    function reset() { btn.disabled = false; btn.textContent = 'Refresh from GitHub'; }
    function lock(sec) {
      var left = Math.max(1, Number(sec) || cooldown || 60);
      btn.disabled = true; btn.textContent = 'Refresh again in ' + left + ' s';
      var t = setInterval(function () {
        left -= 1;
        if (left <= 0 || !document.body.contains(btn)) { clearInterval(t); reset(); } else btn.textContent = 'Refresh again in ' + left + ' s';
      }, 1000);
    }
    btn.addEventListener('click', function () {
      btn.disabled = true; btn.textContent = 'Refreshing…';
      call('refresh_app', { app: name }, 'POST').then(function (r) {
        if (r.refreshed) { toast('Refreshed from GitHub'); reload(); } else { toast('Refreshed a moment ago'); lock(r.retry_in); }
      }).catch(function (e) { toast(e.message); reset(); });
    });
    return btn;
  }
  function appStat(name, label, value, cls, href) {
    var el = stat(label, value, cls, href);
    if (href) el.addEventListener('click', function () { setApp(name); });
    return el;
  }

  function renderApp(name) {
    name = name || state.app;
    if (!name) {
      return call('overview', { days: 1 }).then(function (d) {
        fillApps(d.apps);
        mount([h('h1', {}, 'Apps'), card(null, d.apps && d.apps.length ? appChips(d.apps) : empty('No apps yet. Register one with upande_mobile.api.register_app, or let a device report.'))]);
      });
    }
    return call('app_overview', { app: name }).then(function (d) {
      var a = d.app, v = d.version, ad = d.adoption, g = d.github, act = d.activity, L = d.links, hl = act.health || {};
      var known = !!v.latest_version;
      var nodes = [];

      nodes.push(h('a', { class: 'back', href: '#/overview' }, '← Overview'));
      nodes.push(h('div', { class: 'actions' }, [
        h('h1', { style: 'margin:0;word-break:break-word' }, a.name),
        badge(a.enabled ? 'enabled' : 'disabled', a.enabled ? 'ok' : 'info'),
        badge(a.enable_monitoring ? 'monitoring on' : 'monitoring off', a.enable_monitoring ? 'ok' : 'warn'),
        h('span', { style: 'flex:1' }),
        a.github_repo ? refreshButton(a.name, d.refresh_cooldown, route_) : null
      ]));
      if (a.github_repo && g.error) {
        nodes.push(h('div', { class: 'err', role: 'alert' }, [h('strong', {}, g.stale ? 'GitHub unreachable (showing data from ' + when(g.fetched_at) + ')' : 'GitHub could not be read'), h('div', { class: 'sub', style: 'white-space:pre-wrap' }, g.error)]));
      }
      nodes.push(card(null, [
        kv([
          ['Repository', a.github_repo ? (extLink(a.github_repo, L.repo) || a.github_repo) : 'not set'],
          ['OTA base URL', a.ota_base_url], ['Platform', a.platform],
          ['Version check', a.checked_at ? when(a.checked_at) + ' (' + full(a.checked_at) + ')' : 'not yet'],
          ['GitHub data', a.github_repo ? (g.checked_at ? 'checked ' + when(g.checked_at) : 'not yet') : ''],
          ['Version check error', a.last_error]
        ]),
        h('div', { class: 'actions', style: 'margin:10px 0 0' }, [extLink('Repository', L.repo), extLink('Releases', L.releases), extLink('Actions', L.actions), extLink('Pages', L.pages)])
      ]));

      /* version now */
      nodes.push(h('div', { class: 'grid two' }, [
        card('Version now', [
          h('div', { class: 'bigver' }, v.latest_overall ? 'v' + v.latest_overall : '—'),
          h('p', {}, v.summary),
          kv([['Latest OTA', v.latest_version ? 'v' + v.latest_version : ''], ['Runtime', v.latest_runtime], ['Newest APK', v.latest_apk_version ? 'v' + v.latest_apk_version : ''],
            ['Published', v.published_at ? when(v.published_at) + ' (' + full(v.published_at) + ')' : '']]),
          known ? null : h('p', { class: 'muted' }, 'No OTA manifest was found yet. Check the OTA base URL and that GitHub Pages is serving it.')
        ]),
        card('Adoption', [
          h('div', { class: 'grid cards', style: 'margin:0' }, [
            stat('Devices registered', ad.total),
            stat(known ? 'On latest · ' + ad.on_latest_percent + '%' : 'On latest', ad.on_latest, ad.on_latest ? '' : ''),
            stat('Users on latest', ad.users_on_latest),
            stat('Need an APK', ad.needs_apk, ad.needs_apk ? 'bad' : ''),
            stat('Behind (OTA)', ad.behind_ota, ad.behind_ota ? 'warn' : ''),
            stat('Dormant', ad.dormant)
          ]),
          h('p', { class: 'muted' }, known
            ? num(ad.users_total) + ' accounts have used this app. ' + ad.on_latest_percent_active + '% of the ' + num(ad.active) + ' active devices are on the latest version.'
            : 'The latest version is not known yet, so devices cannot be compared with it.')
        ])
      ]));
      nodes.push(h('div', { class: 'grid two' }, [
        card('Devices by version', meters(ad.by_version, 'version', 'unknown')),
        card('Devices by runtime', meters(ad.by_runtime, 'runtime', 'unknown'))
      ]));
      nodes.push(card('Not updated yet', ad.behind && ad.behind.length ? h('div', { class: 'table-wrap' }, h('table', {}, [
        h('thead', {}, h('tr', {}, ['Device', 'User', 'Version', 'Needs', 'Last seen'].map(function (t) { return h('th', {}, t); }))),
        h('tbody', {}, ad.behind.map(function (r) {
          return h('tr', {}, [
            h('td', {}, h('a', { class: 'title', href: '#/device/' + encodeURIComponent(r.install_id) }, [r.device_brand, r.device_model].filter(Boolean).join(' ') || r.device_name || r.install_id.slice(0, 8))),
            h('td', {}, r.full_name || r.user || '—'),
            h('td', {}, [r.app_version || '—', h('div', { class: 'sub' }, r.runtime_version ? 'runtime ' + r.runtime_version : '')]),
            h('td', {}, [badge(r.status, r.status === 'needs_apk' ? 'bad' : 'warn'), h('div', { class: 'sub' }, r.update_to || '')]),
            h('td', { title: full(r.last_seen) }, when(r.last_seen))
          ]);
        }))
      ])) : empty('Every active device is up to date.')));

      /* what's new */
      if (!g.repo_set) {
        nodes.push(card("What's new", h('p', { class: 'muted' }, 'No GitHub repo is set for this app. Set GitHub Repo on its Mobile App record to see releases and workflow runs here.')));
      } else if (!g.releases.length) {
        nodes.push(card("What's new", empty(g.error ? 'No releases could be read yet.' : 'No releases yet.')));
      } else {
        var cur = g.releases.filter(function (r) { return r.tag === g.whats_new; })[0] || g.releases[0];
        nodes.push(card("What's new in " + cur.tag, [
          releaseLine(cur),
          !g.whats_new_exact && known ? h('p', { class: 'muted' }, 'There is no release for v' + v.latest_version + ' yet, so this is the newest release.') : null,
          renderNotes(cur.notes)
        ]));
        var earlier = g.releases.filter(function (r) { return r !== cur; });
        if (earlier.length) {
          nodes.push(card('Earlier releases', earlier.map(function (r) {
            return h('details', { class: 'release' }, [h('summary', {}, [h('strong', {}, r.tag), ' ', kindBadge(r), ' ', h('span', { class: 'muted' }, r.published_at ? when(r.published_at) : '')]), releaseLine(r), renderNotes(r.notes)]);
          })));
        }
      }

      /* what's going on */
      nodes.push(h('div', { class: 'grid two' }, [
        card('GitHub Actions', !g.repo_set ? h('p', { class: 'muted' }, 'No GitHub repo set.') : g.runs.length ? h('div', { class: 'table-wrap' }, h('table', {}, [
          h('thead', {}, h('tr', {}, ['Run', 'Result', 'Started'].map(function (t) { return h('th', {}, t); }))),
          h('tbody', {}, g.runs.map(function (r) {
            return h('tr', {}, [
              h('td', {}, [extLink(r.title || r.name || ('#' + r.id), r.url) || (r.title || r.name), h('div', { class: 'sub' }, [r.name, r.branch ? ' · ' + r.branch : '', r.event ? ' · ' + r.event : ''].join(''))]),
              h('td', {}, runBadge(r)),
              h('td', { title: full(r.started_at) }, when(r.started_at))
            ]);
          }))
        ])) : empty(g.error ? 'No workflow runs could be read yet.' : 'No workflow runs yet.')),
        card('Events per day (last ' + act.days + ' days)', chart(act.series))
      ]));
      nodes.push(card('Open issues', act.open_issues && act.open_issues.length ? issueTable(act.open_issues) : empty('No open issues for this app.')));
      nodes.push(h('div', { class: 'grid two' }, [
        card('Fleet health', [
          h('div', { class: 'grid cards', style: 'margin:0' }, [
            appStat(a.name, 'Low battery', hl.low_battery, hl.low_battery ? 'warn' : '', '#/devices?health=low_battery'),
            appStat(a.name, 'Low storage', hl.low_storage, hl.low_storage ? 'warn' : '', '#/devices?health=low_storage'),
            appStat(a.name, 'Old OS', hl.outdated_os, hl.outdated_os ? 'warn' : '', '#/devices?health=outdated_os'),
            appStat(a.name, 'Offline', hl.offline, '', '#/devices?health=offline')
          ]),
          h('p', { class: 'muted' }, num(hl.devices) + ' registered devices.')
        ]),
        h('div', { class: 'grid' }, [card('Device models', listRows(act.models)), card('OS versions', listRows(act.os_versions))])
      ]));
      mount(nodes);
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

  /* ---------- support: problem reports and chat ---------- */
  var REPORT_BADGE = { 'Open': 'bad', 'In Progress': 'warn', 'On Hold': 'warn', 'Replied': 'info', 'Resolved': 'ok', 'Closed': 'ok' };

  function renderSupport(q) {
    var tab = q.get('tab') === 'chat' ? 'chat' : 'reports', start = Number(q.get('start')) || 0, status = q.get('status') || '';
    var tabs = h('div', { class: 'tabs' }, [['reports', 'Reports'], ['chat', 'Chat']].map(function (t) {
      return h('button', { class: tab === t[0] ? 'on' : '', type: 'button', onclick: function () { go('support', { tab: t[0] }); } }, t[1]);
    }));
    if (tab === 'chat') {
      return call('support.chat_threads', { app: state.app, start: start, page_length: 25 }).then(function (d) {
        d.start = start; d.page_length = 25;
        var table = d.rows.length ? h('div', { class: 'table-wrap' }, h('table', {}, [
          h('thead', {}, h('tr', {}, ['Person', 'App', 'Last message', 'When', ''].map(function (t) { return h('th', {}, t); }))),
          h('tbody', {}, d.rows.map(function (r) {
            return h('tr', {}, [
              h('td', {}, h('a', { class: 'title', href: '#/chat/' + encodeURIComponent(r.name) }, r.user_full_name || r.user)),
              h('td', {}, appLink(r.app)), h('td', {}, r.last_preview || '—'), h('td', { title: full(r.last_message_at) }, when(r.last_message_at)),
              h('td', {}, r.unread_staff ? badge(r.unread_staff + ' new', 'bad') : '')
            ]);
          }))
        ])) : empty('No one has written in yet.');
        mount([h('h1', {}, 'Support'), tabs, card(null, [table, pager(d, function (st) { go('support', { tab: 'chat', start: st }); })])]);
      });
    }
    return call('support.reports', { app: state.app, status: status, start: start, page_length: 25 }).then(function (d) {
      d.start = start; d.page_length = 25;
      var filters = h('div', { class: 'tabs' }, [['', 'All']].concat((d.statuses || []).map(function (st) { return [st, st]; })).map(function (t) {
        return h('button', { class: status === t[0] ? 'on' : '', type: 'button', onclick: function () { go('support', { tab: 'reports', status: t[0] }); } }, t[1]);
      }));
      var table = d.rows.length ? h('div', { class: 'table-wrap' }, h('table', {}, [
        h('thead', {}, h('tr', {}, ['Report', 'From', 'App', 'Device', 'Status', 'When'].map(function (t) { return h('th', {}, t); }))),
        h('tbody', {}, d.rows.map(function (r) {
          return h('tr', {}, [
            h('td', {}, [h('a', { class: 'title', href: '#/report/' + encodeURIComponent(r.name) }, r.subject), h('div', { class: 'sub' }, r.name + (r.doctype === 'Issue' ? ' · Issue' : '') + (r.screenshot ? ' · screenshot' : ''))]),
            h('td', {}, r.reporter_name || r.reporter), h('td', {}, [r.app ? appLink(r.app) : '—', h('div', { class: 'sub' }, r.app_version || '')]),
            h('td', {}, [r.device_model || '—', h('div', { class: 'sub' }, r.os_version || '')]),
            h('td', {}, badge(r.status, REPORT_BADGE[r.status])), h('td', { title: full(r.creation) }, when(r.creation))
          ]);
        }))
      ])) : empty('No reports.');
      mount([h('h1', {}, 'Support'), tabs, h('div', { class: 'filters' }, [filters]), card(null, [table, pager(d, function (st) { go('support', { tab: 'reports', status: status, start: st }); })])]);
    });
  }

  function renderReport(name) {
    return call('support.report', { name: name }).then(function (r) {
      var dg = r.diagnostics || {}, dev = dg.device || {}, calls = dg.recent_calls || [], crash = r.last_crash;
      function setStatus(st) { call('support.set_report_status', { name: name, status: st }, 'POST').then(function () { toast('Marked ' + st); route_(); }).catch(function (e) { toast(e.message); }); }
      var actions = h('div', { class: 'filters' }, (r.statuses || []).map(function (st) {
        return h('button', { type: 'button', class: r.status === st ? 'primary' : '', onclick: function () { setStatus(st); } }, st);
      }));
      mount([
        h('a', { class: 'back', href: '#/support' }, '← Support'),
        h('h1', {}, r.subject),
        h('p', {}, [badge(r.status, REPORT_BADGE[r.status]), ' ', r.name, ' · ', when(r.creation)]),
        actions,
        r.desk_url ? h('p', {}, h('a', { href: r.desk_url, target: '_blank', rel: 'noopener' }, 'Open the ' + (r.doctype === 'Issue' ? 'Issue' : 'report') + ' in Desk')) : null,
        h('div', { class: 'grid two' }, [
          card('Report', [kv([['From', r.reporter_name || r.reporter], ['App', [r.app ? appLink(r.app) : '—', ' ' + (r.app_version || '')]], ['Runtime', r.runtime_version], ['Device', r.device_model], ['OS', r.os_version], ['Install id', r.install_id]]),
            r.description ? h('p', { style: 'white-space:pre-wrap;margin-top:10px' }, r.description) : null]),
          card('Screenshot', r.screenshot ? h('a', { href: r.screenshot, target: '_blank', rel: 'noopener' }, h('img', { src: r.screenshot, alt: 'Screenshot from the phone', class: 'shot' })) : empty('No screenshot was attached.'))
        ]),
        crash ? card('Last crash' + (crash.fatal ? ' (fatal)' : ''), [h('p', {}, [h('b', {}, crash.message), crash.at ? ' · ' + crash.at : '', crash.screen ? ' · ' + crash.screen : '']), crash.stack ? h('pre', { class: 'code' }, crash.stack) : null]) : null,
        card('Device when reported', [kv([
          ['Battery', dev.battery_level != null ? pct(dev.battery_level) : ''], ['Storage free', dev.storage_free_bytes != null ? bytes(dev.storage_free_bytes) + ' of ' + bytes(dev.storage_total_bytes) : ''],
          ['Network', dev.network_type], ['Wi-Fi', wifiText(dev)], ['Server latency', dev.latency_ms != null ? dev.latency_ms + ' ms' : ''], ['API response time', apiText(dev)],
          ['Memory', dev.memory_total_bytes ? bytes(dev.memory_total_bytes) : ''], ['Locale', [dev.locale, dev.timezone].filter(Boolean).join(' · ')], ['Missing in this build', (dev.missing_modules || []).join(', ')]
        ])]),
        card('Last requests', calls.length ? h('div', { class: 'table-wrap' }, h('table', {}, [
          h('thead', {}, h('tr', {}, ['Time', 'Request', 'Status', 'ms'].map(function (t) { return h('th', {}, t); }))),
          h('tbody', {}, calls.map(function (c) { return h('tr', {}, [h('td', {}, when(c.ts)), h('td', { class: 'mono' }, c.method + ' ' + c.url), h('td', {}, c.status == null ? '—' : badge(String(c.status), c.status >= 400 ? 'bad' : 'ok')), h('td', {}, c.ms)]); }))
        ])) : empty('None recorded.'))
      ]);
    });
  }

  function renderChat(thread) {
    return call('support.chat_thread', { thread: thread }).then(function (d) {
      var box = h('div', { class: 'chat' }, d.messages.length ? d.messages.map(function (m) {
        return h('div', { class: 'msg ' + (m.from_staff ? 'staff' : 'them') }, [h('div', {}, m.text), h('div', { class: 'sub' }, (m.from_staff ? 'Support' : (d.thread.user_full_name || d.thread.user)) + ' · ' + when(m.sent_at))]);
      }) : [empty('No messages.')]);
      var input = h('textarea', { rows: 3, placeholder: 'Reply…', 'aria-label': 'Reply' });
      var send = h('button', { type: 'button', class: 'primary', onclick: function () {
        var text = input.value.trim(); if (!text) return;
        send.disabled = true;
        call('support.chat_reply', { thread: thread, text: text }, 'POST').then(function () { route_(); }).catch(function (e) { send.disabled = false; toast(e.message); });
      } }, 'Send reply');
      mount([h('a', { class: 'back', href: '#/support?tab=chat' }, '← Support'), h('h1', {}, d.thread.user_full_name || d.thread.user), h('p', { class: 'muted' }, [appLink(d.thread.app), ' · ', d.thread.user]),
        card(null, [box, h('div', { class: 'reply' }, [input, send])])]);
      box.scrollTop = box.scrollHeight;
    });
  }

  function route_() {
    var r = route(), p = r.parts[0] || 'overview';
    [].forEach.call(document.querySelectorAll('#nav a'), function (a) { a.className = a.getAttribute('data-route') === (p === 'issue' ? 'issues' : p === 'device' ? 'devices' : p === 'report' || p === 'chat' ? 'support' : p) ? 'on' : ''; });
    mount(h('p', { class: 'muted' }, 'Loading…'));
    var job = p === 'issues' ? renderIssues(r.q) : p === 'issue' ? renderIssue(r.parts[1] || '') : p === 'devices' ? renderDevices(r.q) : p === 'device' ? renderDevice(r.parts[1] || '') : p === 'app' ? renderApp(r.parts[1] || '') : p === 'support' ? renderSupport(r.q) : p === 'report' ? renderReport(r.parts[1] || '') : p === 'chat' ? renderChat(r.parts[1] || '') : renderOverview(r.q);
    job.catch(function (e) { mount(errBox(e)); });
  }
  window.addEventListener('hashchange', route_);
  call('overview', { days: 1 }).then(function (d) { fillApps(d.apps); }).catch(function () { /* the view shows the error */ }).then(route_);
})();
