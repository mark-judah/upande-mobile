# Upande Mobile

The Frappe half of [`@upande/mobile-kit`](https://www.npmjs.com/package/@upande/mobile-kit): the
server that gives your Expo apps over-the-air (OTA) updates, APK update tracking and a register
of which devices run which version. It works for any app; there is nothing to configure per app
beyond one command.

Requires Frappe 16.

## Quick start

```bash
bench get-app https://github.com/mark-judah/upande-mobile
bench --site your.site install-app upande_mobile
bench --site your.site migrate

# Register your app (idempotent: run it again to change anything)
bench --site your.site execute upande_mobile.api.register_app --kwargs \
  '{"app_name": "My App", "github_repo": "me/my-app", "ota_base_url": "https://me.github.io/my-app/ota"}'
```

Then point the app at the server, using the URL printed by `npx upande-mobile-kit doctor`:

```
https://your.site/api/method/upande_mobile.api.ota.manifest?app=My%20App
```

That is the app's `expo.updates.url`. Phones ask your site for the update manifest, and your site
answers with the headers `expo-updates` insists on (GitHub Pages cannot send them).

## What it provides

| Path | Method | Who | Purpose |
|---|---|---|---|
| `upande_mobile.api.register_app` | POST | System Manager | create or update a Mobile App |
| `upande_mobile.api.register_install` | POST | any signed-in user | a device reports itself (launch / foreground / login) |
| `upande_mobile.api.installs` | GET | System Manager | the device register, with a status per device |
| `upande_mobile.api.refresh_now` | POST | System Manager | re-read each app's latest OTA and APK now |
| `upande_mobile.api.app_versions` | GET | any signed-in user | versions of the apps installed on this site |
| `upande_mobile.api.ota.manifest?app=<name>` | GET | guest | expo-updates manifest proxy |

Doctypes: **Mobile App** (one per app: GitHub repo, OTA base URL, latest published version,
runtime and APK, refreshed hourly), **Mobile App Install** (one row per physical device and app)
and **Mobile App Install User** (the accounts that used a device).

A device's status is one of `current`, `behind_ota` (a JavaScript update is out), `needs_apk` (a
new native build is needed), `ahead`, `dormant` (not seen for a while) or `unknown`.

An app that reports in before it is registered is recorded as a **disabled** Mobile App (at most
20 such records), so nothing is lost; enable it and fill in its repo and OTA URL, or run
`register_app`, to start version checks.

## Crash and API reporting (Mobile Monitor)

Sentry-style reporting for apps that use `@upande/mobile-kit`: crashes, errors and failed API
calls (URL, headers, payload and response), with the device (OS, model, battery, storage,
network, app / runtime / OTA versions) and the signed-in user, grouped into issues on a dashboard.

Open **`https://your.site/mobile-monitor`** as a System Manager. Views: **Overview** (events per
day, top issues, app / model / OS breakdown, fleet health), **Issues** (filter by status, kind,
level, version, search), **Issue detail** (stack, request / response viewer with *Copy as cURL*,
breadcrumbs, device and user panels, resolve / ignore), **Devices** (low battery, low storage,
offline, old OS, old app) and a per-device timeline. Everything from a device is shown as plain
text, never as HTML.

| Path | Method | Who |
|---|---|---|
| `upande_mobile.api.monitor.ingest` | POST | signed-in user (guests only if the app allows it) |
| `upande_mobile.api.monitor.overview` / `issues` / `issue` / `events` / `devices` | GET | System Manager |
| `upande_mobile.api.monitor.set_issue_status` / `purge_now` | POST | System Manager |

`ingest` takes one form field, `payload`: JSON `{"app": "<Mobile App>", "install_id": "...",
"events": [...]}` (at most 50 events and 512 KB per request); it answers
`{"accepted", "dropped", "next_flush_ms"}`. Event and device fields are listed in the design
notes of the kit.

Per **Mobile App** record: `enable_monitoring` (default on), `accept_reports_before_sign_in`
(default off; lets a device with no session report, e.g. login failures; rate limited by IP) and
`event_retention_days` (default 30). Events older than that are deleted by a daily job in batches;
issues are kept. Site config, all optional: `upande_mobile_redact_keys` (extra field names to
mask) and `upande_mobile_min_os` (e.g. `{"android": 11, "ios": 17}`, what "old OS" means).

**Privacy and safety**
- **Redaction before storage.** Authorization, Cookie / Set-Cookie, any header, query or body
  field named like a token, secret, password, API key, session, OTP, PIN or card, Frappe login
  fields (`usr`, `pwd`), and credential-shaped text (Bearer / Basic / JWT / `token key:secret`)
  are masked on the server even if the app already did it. Bodies are capped (16 KB hard limit)
  and binary or multipart bodies are replaced by a size note.
- **Who is who comes from the server.** A report is filed under the session user and the IP the
  server saw; a name or role claimed in the payload is ignored. The raw Android ID is never stored.
- **Cheap on purpose.** One batched request per report, one issue row per problem however often it
  happens (a loop stops writing event rows after 200 a day for one issue and only counts), per-user
  and per-IP rate limits (429), an unknown or switched-off app is answered with a long back-off.
- Crash data can still contain personal data (screen names, ids in URLs). Switch monitoring off per
  app, shorten retention, or add `upande_mobile_redact_keys` to fit your policy.

## Settings

All optional, in `site_config.json`:

| Key | Meaning |
|---|---|
| `upande_mobile_ota_hosts` | extra hosts allowed for an OTA base URL (a list), beyond `*.github.io` and `raw.githubusercontent.com` |
| `upande_mobile_runtime_major_base` | the `runtimeMajorBase` your apps set in `expo.extra.mobileKit` (default 0) |
| `mobile_app_github_token` | a GitHub token for reading releases, to avoid the unauthenticated rate limit |

## Security notes

- **Roles.** Registering apps, reading the register and refreshing are System Manager only.
  Reporting a device needs a signed-in user, and a device is always filed under the session's own
  account, never one the client claims. The manifest is public by design: it only returns the
  already-public manifest from GitHub.
- **Device key.** The Android ID is never stored. The server keeps only a SHA-256 of it (with a
  fixed prefix), which is enough to recognise the same phone after a reinstall. The IP the server
  sees is recorded; no location is.
- **SSRF guard.** The server fetches the OTA base URL and the GitHub repo you register, on a
  schedule and for any caller of the public manifest path, so both are validated: `https` only,
  no credentials, port, query or fragment, a real host name (no IP addresses), and a host that is
  `<owner>.github.io`, `raw.githubusercontent.com` or listed in `upande_mobile_ota_hosts`. The
  check runs in `register_app`, on every save of a Mobile App, and again at fetch time, so an
  edited record cannot widen it. The repo must look like `owner/repo`.
- **Manifest size and failures.** Manifests over 512 KB are refused, and any upstream failure
  answers `204` ("no update") instead of an error, so a broken upstream never breaks every
  phone's start-up.

## Tests

The tests need no site:

```bash
cd apps/upande_mobile
../../env/bin/python -W ignore -m unittest upande_mobile.tests.test_helpers upande_mobile.tests.test_register_app
```

## License

MIT
