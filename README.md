# Upande Mobile

The Frappe backend for [`@upande/mobile-kit`](https://github.com/mark-judah/upande-mobile-kit), a Settings page and update toolkit for Expo apps.

It gives every app that uses the kit:

- **OTA updates.** Your site serves the update manifest that `expo-updates` needs. The bundles themselves live on GitHub Pages.
- **A device register.** Which phones run which build of which app, and which are behind.
- **Crash and API monitoring.** Errors, failed requests (with headers, payload and response), device details and the signed-in user, grouped into issues on a dashboard at `/mobile-monitor`.

One install serves any number of apps. Adding an app is one command, not a code change.

## The web portal

The app ships a web portal for IT admins and developers: **`https://your.site/mobile-monitor`**.

- **Who can open it:** System Managers. Open the address in a browser while signed in to your Frappe site. If you are signed out you are sent to the login page and back; any other user gets a permission error. There is no Desk menu entry, so bookmark the address.
- **What it gives you:**
  - an overview of events per day, top issues, app versions, device models and OS versions in use, and fleet health;
  - every crash, error and failed API request grouped into issues, each with the stack, the request and response (headers and payload, with secrets removed), breadcrumbs, the device (OS, model, battery, storage, network, versions) and the signed-in user;
  - a Devices view of every registered phone, with filters for low battery, low storage, offline, old OS and old app.
- **Before it works:** run `bench build --app upande_mobile` once after installing (the page loads its CSS and JavaScript from `/assets/upande_mobile/`), and have an app registered and sending reports. See [Monitoring dashboard](#monitoring-dashboard) for the details.

## Requirements

- Frappe 16 (this app declares Python 3.14, which is what Frappe 16 requires).
- A bench you can run commands on, with `bench build` available.
- For OTA and version tracking: your app's GitHub repository and its GitHub Pages site (see the [kit README](https://github.com/mark-judah/upande-mobile-kit#github-setup)).

## Install

Run on the server that hosts the site:

```bash
bench get-app https://github.com/mark-judah/upande-mobile
bench --site your.site install-app upande_mobile
bench build --app upande_mobile
bench --site your.site migrate
bench restart
```

`bench build` matters: the monitor dashboard loads its CSS and JavaScript from `/assets/upande_mobile/`, which only exists after a build.

Then register each app that will use the site.

## Register an app

An app must be registered **and enabled**, with an OTA URL, before phones receive updates.

An app that reports in before it is registered is recorded automatically as a **disabled** Mobile App (at most 20 such records). That keeps its first device and crash reports, but it serves no OTA and its devices show status `unknown` until you register it.

### With a command

```bash
bench --site your.site execute upande_mobile.api.register_app --kwargs \
  '{"app_name": "My App", "github_repo": "owner/my-app", "ota_base_url": "https://owner.github.io/my-app/ota"}'
```

It is idempotent: run it again to change anything. Fields you leave out keep their value. `enabled` defaults to `1`. The kit's `init` wizard prints this command with your values filled in.

### In Desk

Open the **Mobile App** list (System Manager only) and create or edit a record:

| Field | Meaning |
|---|---|
| App Name | The name the app reports. Must match `appName` in the app's `src/mobile-kit.ts`. 2 to 64 characters: letters, digits, space, dot, dash, underscore. |
| Enabled | Switches on OTA serving and the hourly version check. Monitoring has its own switch below. |
| Dormant After (Days) | A device not seen for this long is shown as `dormant`. Default 7. |
| GitHub Repo | `owner/repo`. The newest release with an `.apk` file is the latest APK. |
| OTA Base URL | Where the app's OTA files are published, for example `https://owner.github.io/my-app/ota`. See the rules below. |
| Platform | `android`. |
| Latest Version, Latest Runtime, Latest APK Version, Latest APK URL, Published At, Checked At, Last Error | Read only. Filled by the hourly check. |
| Enable Monitoring | Accept crash and error reports from this app. Default on. |
| Accept Reports Before Sign-in | Also accept reports from devices with no signed-in user, for example login failures. Default off. Rate limited by IP. |
| Event Retention (Days) | Events older than this are deleted daily. Default 30, between 1 and 365. Issues are kept. |

**The OTA Base URL is fetched by your server**, so it is restricted: `https` only; no credentials, port, query or fragment; a real host name (no IP addresses); and the host must be `<owner>.github.io`, `raw.githubusercontent.com`, or listed in `upande_mobile_ota_hosts` in `site_config.json`. The repo must look like `owner/repo`.

## Endpoints

All paths are under `/api/method/`.

| Path | Method | Who | Purpose |
|---|---|---|---|
| `upande_mobile.api.ota.manifest?app=<name>` | GET | anyone | The `expo-updates` manifest for the caller's runtime |
| `upande_mobile.api.register_install` | POST | signed-in user | A device reports itself (on launch, sign-in, returning to the app). Optional `device_info` carries battery, storage, memory, network, locale and similar facts |
| `upande_mobile.api.installs` | GET | System Manager | The device register with a status per device |
| `upande_mobile.api.register_app` | POST | System Manager | Create or update a Mobile App |
| `upande_mobile.api.refresh_now` | POST | System Manager | Re-read every enabled app's latest OTA and APK now (runs in the background) |
| `upande_mobile.api.app_versions` | GET | signed-in user | Versions of the apps installed on this site |
| `upande_mobile.api.monitor.ingest` | POST | signed-in user (guests only if the app allows it) | A batch of crash and API events |
| `upande_mobile.api.monitor.overview`, `issues`, `issue`, `events`, `devices` | GET | System Manager | Dashboard data |
| `upande_mobile.api.monitor.set_issue_status`, `purge_now` | POST | System Manager | Resolve or ignore an issue; delete expired events now |

## How OTA works

1. The app's `expo.updates.url` is `https://your.site/api/method/upande_mobile.api.ota.manifest?app=My%20App`. The kit's `init` sets it.
2. On launch the phone asks that URL. It sends its runtime in the `expo-runtime-version` header and its platform in `expo-platform`.
3. The site fetches `<ota_base_url>/<platform>/<runtime>/manifest.json` and returns it with `expo-protocol-version: 1`, `expo-sfv-version: 0` and `cache-control: private, max-age=0, no-store`. GitHub Pages cannot send those headers, and `expo-updates` refuses a manifest without them. That is why the site is in the middle.
4. The bundle and assets are downloaded from GitHub Pages directly. Only the small manifest passes through your site.

The manifest is cached for 60 seconds per app, platform and runtime. If Pages answers 404 (not enabled yet) for a `<owner>.github.io/<repo>` base, the site reads the same files from the repo's `gh-pages` branch on `raw.githubusercontent.com` and rewrites the URLs to match.

The endpoint never answers with a server error, because every phone calls it on every launch. In all of these cases it answers **204 with the protocol headers**, which means "no update":

- the app name is unknown, or the Mobile App is disabled or has no OTA Base URL;
- the runtime is not of the form `major.minor`, or the platform is not `android` or `ios`;
- nothing is published for that runtime (404);
- the upstream fails, answers with an error status, or returns a manifest over 512 KB;
- the saved URL no longer passes the host rules.

Upstream failures are written to the Error Log, at most once a minute per app and runtime.

## Device register

One **Mobile App Install** row is one physical device for one app. It holds the latest report: who reported, app, runtime and OS version, brand and model, first and last seen, launches, IP address seen by the server, and the last known health (battery, storage, network). **Mobile App Install User** rows list every account that used the device, each with its own version and last seen.

A device reports a random install id and a hardware key. The server stores the hardware key only as a SHA-256 hash, and uses it to recognise the same phone after a reinstall. The raw Android ID is never stored. Rows are always filed under the signed-in user, never one the client claims.

A same-account launch within 5 minutes only refreshes `last_seen`. A new version, a sign-in or a different account is always recorded.

The hourly job (`upande_mobile.api.devices.refresh_latest`) reads each enabled app's newest OTA manifest and newest GitHub Release with an APK, stores them on the Mobile App, and re-rates every device. Run it now with `bench --site your.site execute upande_mobile.api.devices.refresh_latest`. Failures are kept in the record's `Last Error`.

| Status | Meaning |
|---|---|
| `current` | Running the latest version |
| `behind_ota` | A newer JavaScript update exists; reopening the app applies it |
| `needs_apk` | The latest runtime is newer than the device's: a new APK is needed |
| `ahead` | Newer than the published latest |
| `dormant` | Not seen for the app's dormant days |
| `unknown` | The app is not registered and enabled, or no latest version is known yet (for example before the first hourly check) |

## Monitoring dashboard

The portal is at **`https://your.site/mobile-monitor`**. Open it in a browser while signed in as a System Manager. A guest is sent to the login page; any other user gets a permission error. The app selector at the top filters every view to one app.

| View | Shows |
|---|---|
| Overview | Events per day, top issues, breakdown by app version, model and OS, fleet health counts |
| Issues | Grouped problems. Filter by app, status, kind, level, version, and search |
| Issue detail | Stack, request and response viewer with Copy as cURL, breadcrumbs, device and user panels, and Resolve or Ignore |
| Devices | Health of every registered device, with filters for low battery, low storage, offline, old OS and old app |
| Device | One device's timeline |

Everything from a device is shown as plain text, never as HTML.

**Issues.** Events with the same error are grouped into one **Mobile Issue**. Messages and URLs are normalised first (ids, numbers and hashes removed), so "Item ITM-0042 not found" and "Item ITM-0043 not found" are one issue. An issue is Open, Resolved or Ignored. A resolved issue reopens when it happens again on a newer app version. A runaway loop stops writing event rows after 200 per issue per day and only counts.

**Health.** Low battery is under 20%. Low storage is under 512 MB free or under 10% free. Old OS is below Android 10 or iOS 16 by default (change with `upande_mobile_min_os`). Old app is a device with status `behind_ota` or `needs_apk`.

**Retention.** A daily job at 02:30 deletes events older than each app's Event Retention (default 30 days). Issues are kept. Delete now with `upande_mobile.api.monitor.purge_now`, optionally with `days` and `app`.

**Limits on reports.** `ingest` accepts up to 50 events and 512 KB per request, and answers `{accepted, dropped, next_flush_ms, paused, reason}`. Rate limits per minute: 30 per user and 120 per IP, or 20 per IP for guests; beyond that it answers 429. A signed-in device reporting for an unregistered app creates it as a disabled Mobile App, so the first report is kept. A guest never creates an app. `paused` is true with `reason: monitoring_off` when the app's Enable Monitoring is off, or `reason: unknown_app` when the name is invalid or the cap of 20 auto-created apps is reached. The app's Enabled flag does not affect monitoring.

## Privacy and security

- **Secrets are redacted again on the server**, even though the phone already did it. Any header, query or body field whose name contains `token`, `secret`, `password`, `passwd`, `pwd`, `apikey`, `authorization`, `cookie`, `credential`, `csrf`, `session`, `privatekey` or `signature`, or has a word such as `sid`, `otp`, `pin`, `cvv`, `cvc`, `card`, `auth`, `usr`, `passcode` or `ssn`, is replaced with `[redacted]`. So is credential-shaped text: Bearer and Basic values, `token key:secret`, and JWTs. Add field names with `upande_mobile_redact_keys`.
- Redaction works by name and by shape. A secret with another name, in free text, can still be stored. Crash data can contain personal data (screen names, ids in URLs). Turn monitoring off per app, shorten retention, or add redact keys to fit your policy.
- Bodies are capped (16 KB hard limit) and binary or multipart bodies are replaced by a size note.
- Reports are filed under the session user and the IP address the server saw. A name or role claimed in the payload is ignored.
- Only System Managers can read the register, issues, events and the dashboard. Mobile App, Mobile App Install, Mobile Issue and Mobile Event grant access to System Manager only (Mobile App Install User is a child table of the install).
- **Fetches are restricted.** The OTA Base URL and the GitHub repo are fetched by the hourly job and, for the OTA URL, by the public manifest endpoint. They are validated in `register_app`, on every save of a Mobile App and again at fetch time, so an edited record cannot point the server at an internal host.

## Site settings

All optional, in `site_config.json`.

| Key | Meaning |
|---|---|
| `upande_mobile_ota_hosts` | Extra hosts allowed for an OTA Base URL (a list or a comma-separated string), beyond `*.github.io` and `raw.githubusercontent.com` |
| `upande_mobile_runtime_major_base` | The `runtimeMajorBase` your apps set in `expo.extra.mobileKit` (default 0). Needed to rate devices correctly when an app uses an offset |
| `mobile_app_github_token` | A GitHub token for reading releases, to avoid the unauthenticated rate limit (60 per hour per IP) |
| `upande_mobile_redact_keys` | Extra field names to redact in reports (a list or a comma-separated string) |
| `upande_mobile_min_os` | What counts as an old OS, for example `{"android": 11, "ios": 17}` (defaults 10 and 16) |

## Upgrade

```bash
cd apps/upande_mobile && git pull && cd ../..
bench --site your.site migrate
bench build --app upande_mobile
bench restart
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `/mobile-monitor` is a 404 | The app is not installed on this site, or the bench was not restarted | `bench --site your.site install-app upande_mobile`, then `bench restart` |
| `/mobile-monitor` loads without styling or stays blank | The assets were not built | `bench build --app upande_mobile`, then restart |
| `/mobile-monitor` says permission denied | The user is not a System Manager | Sign in as one |
| Phones get no OTA (the manifest answers 204) | The app is not registered and enabled, has no OTA Base URL, or nothing is published for the phone's runtime | Run `register_app`; check the runtime of the latest release equals the app's `runtimeVersion`; read the Error Log for "Mobile OTA manifest" entries |
| Devices show `unknown` | The app is disabled or not registered, or the hourly check has not run or found no manifest | Run `register_app`, then `bench --site your.site execute upande_mobile.api.devices.refresh_latest`, then read `Last Error` on the Mobile App |
| Latest APK is empty | The repo name is wrong or private, or GitHub rate limited the server | Fix `github_repo`; set `mobile_app_github_token` |
| `register_app` says the host is not allowed | The OTA Base URL host is not `*.github.io` or `raw.githubusercontent.com` | Use a Pages URL, or add the host to `upande_mobile_ota_hosts` |
| Reports are answered with `paused: true` | `monitoring_off`: Enable Monitoring is off. `unknown_app`: an invalid name, or 20 disabled auto-created apps already exist | Turn Enable Monitoring on; register the app or delete unused disabled ones |
| Reports from before sign-in are refused | Guests are rejected unless the app allows them | Turn on Accept Reports Before Sign-in for that app |
| Reports are answered with 429 | Rate limit | Wait a minute; phones back off by themselves |

## Development

The unit tests need no site:

```bash
cd apps/upande_mobile
../../env/bin/python -W ignore -m unittest discover -s upande_mobile/tests -t .
```

Layout: `upande_mobile/api/` (endpoints: `apps.py`, `devices.py`, `monitor.py`, `ota.py`), `redaction.py`, `fingerprint.py`, `validation.py`, the five doctypes in `upande_mobile/upande_mobile/doctype/`, the dashboard page in `upande_mobile/www/mobile-monitor.html` and `upande_mobile/public/`.

## License

MIT
