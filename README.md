# Upande Mobile

The Frappe backend for [`@upande/mobile-kit`](https://github.com/mark-judah/upande-mobile-kit), a Settings page and update toolkit for Expo apps. One install serves any number of apps; adding an app is one command, not a code change.

**Jump to:** [Features](#features) · [Install](#install) · [Register an app](#register-an-app) · [Endpoints](#endpoints) · [Site settings](#site-settings) · [Troubleshooting](#troubleshooting)

## Features

Every feature name links to its section.

**Updates and versions**

| Feature | What it does |
|---|---|
| [OTA manifest proxy](#how-ota-works) | Serves the update manifest `expo-updates` needs; the bundles themselves live on GitHub Pages |
| [App registration](#register-an-app) | One command or one Desk record per app |
| [Device register](#device-register) | Which phones run which build of which app, and which are behind |
| [Version reporting](#version-reporting) | This backend's own version, shown in each app's Settings, and the kit version each device runs |

**Monitoring**

| Feature | What it does |
|---|---|
| [Web portal](#the-web-portal) | An IT dashboard for updates, devices, problems and support chat, at `/mobile-monitor` |
| [Crash and API monitoring](#monitoring) | Errors and failed requests with headers, payload and response, grouped into issues |
| [Network and performance](#network-and-performance) | Wi-Fi, latency and how long each device's API calls take |

**Support**

| Feature | What it does |
|---|---|
| [Problem reports](#problem-reports) | "Report a problem" from the app, filed as an Issue (with the screenshot attached) where the site has one: device facts, the last requests and the last crash |
| [Push notifications](#push-notifications) | A push to the person's phone when support comments on their Issue |
| [Chat with support](#chat-with-support) | The person's conversations with your team: their Issues, answered with a Comment on the Issue in Desk |

**Operations**

| Feature | What it does |
|---|---|
| [Endpoints](#endpoints) | Every whitelisted method, who may call it |
| [Site settings](#site-settings) | Optional `site_config.json` keys |
| [Privacy and security](#privacy-and-security) | Redaction, who can read what, restricted fetches |

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
| GitHub Checked At, GitHub Error | Read only. When the repo's releases and workflow runs were last read, and why that failed, if it did. They keep the GitHub data of the app's recent releases; a hidden field next to them caches it. |
| Enable Monitoring | Accept crash and error reports from this app. Default on. |
| Accept Reports Before Sign-in | Also accept reports from devices with no signed-in user, for example login failures. Default off. Rate limited by IP. |
| Event Retention (Days) | Events older than this are deleted daily. Default 30, between 1 and 365. Issues are kept. |

**The OTA Base URL is fetched by your server**, so it is restricted: `https` only; no credentials, port, query or fragment; a real host name (no IP addresses); and the host must be `<owner>.github.io`, `raw.githubusercontent.com`, or listed in `upande_mobile_ota_hosts` in `site_config.json`. The repo must look like `owner/repo`.

## In the app

The kit adds pages and tools inside each Expo app, and this backend feeds them. How they look and behave is in the [kit README](https://github.com/mark-judah/upande-mobile-kit#readme).

| In the app | Served by |
|---|---|
| **Settings** (`/mobile-kit/settings`, for every user): versions, **Check for updates**, fingerprint switch, crash-report switch, the versions of the apps on this site | Frappe's own `frappe.utils.change_log.get_versions` by default; `upande_mobile.api.app_versions` is the same data behind a sign-in (set the kit's `endpoints.versions`) |
| **Devices** (`/mobile-kit/devices`, System Managers only): each device's name, OS, app version, last seen, accounts and a status with the update it needs | `upande_mobile.api.installs` |
| **Report a problem** and **Chat with support** (the Help card) | [`upande_mobile.api.support`](#support) |
| **Device reports** on launch, sign-in and foreground | `upande_mobile.api.register_install` |
| **Crash and API reports** | `upande_mobile.api.monitor.ingest` |

The in-app Devices page is about versions. Battery, storage, network and each device's crash history are in the [web portal](#the-web-portal).

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

The hourly job (`upande_mobile.api.devices.refresh_latest`) reads each enabled app's newest OTA manifest and newest GitHub Release with an APK, stores them on the Mobile App, and re-rates every device. It also reads the app's recent releases and workflow runs. Run it now with `bench --site your.site execute upande_mobile.api.devices.refresh_latest`. Failures are kept in the record's `Last Error`.

| Status | Meaning |
|---|---|
| `current` | Running the latest version |
| `behind_ota` | A newer JavaScript update exists; reopening the app applies it |
| `needs_apk` | The latest runtime is newer than the device's: a new APK is needed |
| `ahead` | Newer than the published latest |
| `dormant` | Not seen for the app's dormant days |
| `unknown` | The app is not registered and enabled, or no latest version is known yet (for example before the first hourly check) |

## Version reporting

- **This backend's version** is `__version__` in `upande_mobile/__init__.py`. Frappe exposes it through `frappe.utils.change_log.get_versions`, so each app's Settings shows it as **Upande Mobile Kit (server)** next to the Frappe and ERPNext versions ("not installed" on a site without this app).
- **The kit version each device runs** is reported with its device snapshot (`kit_version`) and shown as "Mobile Kit" on the Device page and in event device panels, so you can see which phones are on an old kit.

## The web portal

**`https://your.site/mobile-monitor`** is an IT dashboard for the person who looks after the apps: who is out of date, which devices need a hand, what is crashing and who is waiting for an answer. It is written in plain language; the developer view is folded away.

- **Who can open it:** System Managers and the **IT Support** role (created on `bench migrate`; give it to your IT staff). Developer details (stack traces, install ids, runtime, event ids, diagnostics) are shown to System Managers only. Signed-out visitors are sent to the login page and back. There is no Desk menu entry, so bookmark the address.
- **Before it works:** the page loads its CSS and JavaScript from `/assets/upande_mobile/` (run `bench build --app upande_mobile` once after installing), and an app must be registered.
- **The app selector** at the top filters every screen to one app. Everything from a device is shown as plain text, never as HTML.

| Screen | Shows |
|---|---|
| Overview | Four headline numbers (people, devices, adoption, open tickets), then charts: people by update state per app, **Android versions** (older than the supported minimum in red), events reported per day for 14 days by kind, support tickets by status, device health, and attention cards that open their screen. The app filter applies to all of it |
| Updates | The chase list. One row per person and app: version they have and need, how long ago the release came out, last seen, team (from Employee, when present), how many reminders they had. Groups: forgot to update (reopening the app is enough), needs a new install (an APK), not seen lately, up to date. Select people and press **Remind** to send a push notification |
| Devices | Inventory with serial number, MAC address, device IP, the IP the server saw and build number as columns, plus owner, model, app, health, battery, storage and last seen. Open a row to mark the device Active, Lost or Retired and add a note. Lost and retired devices drop off the chase list |
| Crashlytics | The issues the apps reported (crashes, errors, API failures, logs), most users affected first: title, file, kind, events, users, devices, versions, last seen, status. Filter by status, kind, app and search; open an issue to see who hit it and mark it resolved or ignored. Stack traces and event ids sit in a folded Developer details panel (System Managers) |
| Support | The inbox (full name first, then email), longest wait on top, with **chat**; the screenshot a person sent shows inside their first message, like a chat app: read the conversation and answer in the page. The answer is stored on the conversation (a Comment on the Issue, so Desk still shows it) and pushed to the person's phone, so nobody has to open the Issue to reply. Change the status from the same page |
| Apps | One row per app: share of people on the current version, behind, away, problems, open tickets |

### App store

**`https://your.site/app-store`** lists every enabled app that has a published APK, for any signed-in user (the dashboard sidebar links to it). The page never shows the GitHub repository: **Download** goes through `upande_mobile.api.store.download`, which fetches the newest APK from GitHub (only github.com and GitHub's download hosts) into the site's private files on first use, serves it from there afterwards and deletes older copies. Public repositories only: a private repo's release link cannot be fetched this way.

### Reminders

- **Remind** sends a push ("Version 2.1.0 is ready. Close and reopen the app to get it", or "needs a fresh install" when an APK is required). Someone reminded in the last 20 hours is skipped, and the page says who has not allowed notifications. Every reminder is logged in **Mobile Reminder** (who, when, from which version to which, who sent it) and shown on the chase list as "reminded twice".
- A device marked Lost or Retired is never chased. Reminders are only sent when someone presses Remind; nothing is sent automatically.

## Monitoring

### Issues

Events with the same error are grouped into one **Mobile Issue**. Messages and URLs are normalised first (ids, numbers and hashes removed), so "Item ITM-0042 not found" and "Item ITM-0043 not found" are one issue. An issue is Open, Resolved or Ignored. A resolved issue reopens when it happens again on a newer app version. A runaway loop stops writing event rows after 200 per issue per day and only counts.

### Health

Low battery is under 20%. Low storage is under 512 MB free or under 10% free. Old OS is below Android 10 or iOS 16 by default (change with `upande_mobile_min_os`). Old app is a device with status `behind_ota` or `needs_apk`.

### Network and performance

Each device's latest report carries, in its stored snapshot, how the network treats the app: connection type, **latency to the site** (a tiny `GET /api/method/ping` the phone makes at most once a minute), **how long its own API calls take** (count, average and 95th percentile in milliseconds, share failed, over its last 100 calls) and, when the app has `@react-native-community/netinfo`, the **Wi-Fi name, signal, link speed and band** and the cellular generation. The Devices view shows them in the Connection column; the Device page and every event's device panel show the full set. All values are whitelisted, typed and range-checked before they are stored, and none of them adds a column to the register: they live in the device snapshot.

**Device identifiers.** A device also reports its **build number**, its own **device IP** (the address it has on its network, separate from `IP Address`, the one the server saw), and, when the phone or the app can supply them, its **MAC address** and **serial number**. Android 10+ and iOS hide the last two from apps, so they are usually empty: placeholders (`02:00:00:00:00:00`, `unknown`) are dropped here as well as on the phone. All four are typed and checked, kept in the snapshot, and copied to the register row (`build_number`, `local_ip`, `mac_address`, `serial_number`), so the Devices view, the device page and Desk show them.

### Retention

A daily job at 02:30 deletes events older than each app's Event Retention (default 30 days). Issues are kept. Delete now with `upande_mobile.api.monitor.purge_now`, optionally with `days` and `app`.

### Limits on reports

`ingest` accepts up to 50 events and 512 KB per request, and answers `{accepted, dropped, next_flush_ms, paused, reason}`. Rate limits per minute: 30 per user and 120 per IP, or 20 per IP for guests; beyond that it answers 429. A signed-in device reporting for an unregistered app creates it as a disabled Mobile App, so the first report is kept. A guest never creates an app. `paused` is true with `reason: monitoring_off` when the app's Enable Monitoring is off, or `reason: unknown_app` when the name is invalid or the cap of 20 auto-created apps is reached. The app's Enabled flag does not affect monitoring.

## Support

Problem reports and chat from the app's **Help** card, in the portal under **Support** (`/mobile-monitor#/support`). They need the doctypes below, so run `bench --site your.site migrate` after installing or upgrading.

### Problem reports

**Where a report goes.** On a site that has the **Issue** doctype (ERPNext or Helpdesk) a report is filed as an ordinary **Issue**, so it enters the workflow your support team already uses: numbering (`ISS-2026-00214`), SLA, assignment, status changes and replies by email. On a site without Issue it is filed as a **Mobile Report** instead. Set `upande_mobile_support_doctype` in `site_config.json` to `"Mobile Report"` to use that one even where Issue exists.

**What an Issue contains.**

| Part | How it is filled |
|---|---|
| Subject | `[App Name] what the person wrote`, for example `[Mona Shelve] Wrong length when traced`. The bracket is how the portal tells which app an Issue came from |
| Raised By | The signed-in user's email |
| Description | What the person wrote, then a divider line (`Diagnostic details (attached automatically)`), then the diagnostics as readable JSON |
| Attachment | The **private screenshot** of the screen they were on, attached to the Issue (it shows in the Issue's attachments). It must be a real JPEG of at most 1.5 MB; anything else is dropped and the Issue is still created |
| Everything else | Left to the Issue doctype's own defaults (status Open, SLA, series) |

The **diagnostics** are whitelisted device facts (including Wi-Fi and latency), the last 15 requests (addresses and status only, redacted) and **the last crash** (message, stack, whether it was fatal, the screen). Everything from the phone is HTML-escaped before it goes into the description.

**In the portal.** The Support page lists the Issues whose subject starts with a registered app's `[name]`, with the device, OS and app version read back from the diagnostics, the screenshot, and the Issue's own statuses (**Open, Replied, On Hold, Resolved, Closed**). Changing the status there saves the Issue through its document, so its own rules run. Every report links to the Issue in Desk. A Mobile Report keeps its own statuses (Open, In Progress, Resolved).

Limits: 10 reports per user per hour; subject 140 characters, details 5000; diagnostics 64 KB.

### Chat with support

**On a site with Issues, a conversation is an Issue.** There is nothing new for your team to learn:

- `threads` lists the signed-in person's Issues whose subject starts with `[App Name]` (newest first, 30), with the status, the newest activity, when support last answered, and a preview. `thread` returns one conversation: `thread` (id, subject, status, created, `has_screenshot`), `messages` (what the person wrote, read back from the description, then every **Comment** on the Issue, each marked from support or from the person) and `events`, the status history: a `created` event, then one `status` event per change of the Issue's status read from its Version documents (`status`, `previous`, a plain-words `label` such as "Replied", "Marked resolved" or "Reopened", and `sent_at`). The app merges `events` and `messages` by time into one timeline. `reply` adds the person's answer as a Comment on the Issue; an Issue that was *Replied* or *Resolved* goes back to *Open*, and a *Closed* one refuses (the person starts a new one).
- **Support replies the way they already do: a Comment on the Issue in Desk.** Only Comments appear in the app (an email reply or an internal note does not). The portal's Chat tab explains this instead of listing threads.
- A person can only read and answer **their own** Issues (`raised_by` is their user or email); anything else is a permission error. Limits: 60 messages per user per hour, 2000 characters each.
- Replies reach the phone as [push notifications](#push-notifications).

**On a site without the Issue doctype** the old single-thread chat is used: one **Mobile Chat Thread** per person per app holding **Mobile Chat Message** rows, answered from the portal's Chat tab (`chat`, `chat_threads`, `chat_thread`, `chat_reply`). The app learns which mode applies from `threads` (`mode: "issue"` or `"chat"`).

### Push notifications

A reply is pushed to the person's phone the moment support comments.

- **Tokens.** The app calls `register_push` with its Expo push token; it is stored in **Mobile Push Token** (one row per token, filed under the signed-in user and the app, `System Manager` access only). A token that moves to another person (a handed-over phone) follows its new owner, a person keeps their newest 5 devices per app, and `unregister_push` removes the caller's own token (the app does this on sign-out). 20 registrations per user per hour.
- **The trigger.** A `doc_events` hook on **Comment** (`after_insert`) queues `upande_mobile.push.notify_issue_reply` on the `short` queue, so saving the comment never waits for the push. The job sends only when the Comment is a real comment (not an attachment or info entry) on an Issue whose subject starts with `[App]` of a registered Mobile App, written by someone other than the person who raised it, with some text. The person is found from the Issue's `raised_by`. On a site without Issue, a reply from the portal's Chat tab pushes the same way.
- **The message.** Title `Reply on <subject>`, body `<who>: <start of the reply>` (120 characters), channel `support`, high priority, and a small data payload (`upande_mobile: support`, the Issue id, the app) the kit uses to open the conversation when tapped.
- **Only Expo is contacted**: `https://exp.host/--/api/v2/push/send`, from a fixed constant. Tokens Expo answers `DeviceNotRegistered` for are deleted. A failed push is logged ("Mobile push: send failed") and never breaks the reply.
- **Turn it off** with `"upande_mobile_push": 0` in `site_config.json`.
- **If an app already sends its own push on Issue comments** (Harvest has a Server Script that does), disable that script once the app registers tokens with the kit, or people get two notifications.

### How it is protected

- Only signed-in users can submit or chat, and every row is filed under the session user, never one claimed in the request.
- Diagnostics pass the same device whitelist and redaction as crash reports; free text in the details is redacted for token-shaped text.
- The portal shows reports to System Managers only. An Issue keeps the permissions your Issue doctype already has; a Mobile Report, a chat thread and its messages are readable by System Managers only.

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
| `upande_mobile.api.monitor.app_overview?app=<name>` | GET | System Manager | One app's version, adoption, releases and activity, in one call |
| `upande_mobile.api.monitor.refresh_app` (`app`) | POST | System Manager | Re-read one app's latest OTA, APK and GitHub data now. At most once a minute per app; inside that minute it answers `{refreshed: false, retry_in}` |
| `upande_mobile.api.support.submit_report` | POST | signed-in user | "Report a problem": subject, details, a base64 JPEG `screenshot` (at most 1.5 MB), `diagnostics` JSON. 10 per hour per user. Creates an Issue, or a Mobile Report where the site has no Issue, and returns `{report, doctype}` |
| `upande_mobile.api.support.threads` (`app`) | GET | signed-in user | The person's conversations with support (their Issues for the app) and the chat `mode` |
| `upande_mobile.api.support.thread` (`issue`) | GET | signed-in user | One conversation: `thread`, `messages` (their words, then the Comments) and `events` (created, then each status change) |
| `upande_mobile.api.support.reply` (`issue`, `text`) | POST | signed-in user | The person answers on their Issue (adds a Comment). 60 per hour |
| `upande_mobile.api.support.register_push` (`app`, `token`, `install_id`, `platform`) | POST | signed-in user | Offer this phone's Expo push token for support replies. 20 per hour |
| `upande_mobile.api.support.unregister_push` (`token`) | POST | signed-in user | Forget the caller's own token |
| `upande_mobile.api.support.chat` | GET / POST | signed-in user | The single-thread chat for sites without an Issue doctype. `GET` returns messages (and marks replies read), `?count_only=1` the unread count; `POST` sends `text` |
| `upande_mobile.api.support.reports`, `report`, `set_report_status`, `chat_threads`, `chat_thread`, `chat_reply` | GET / POST | System Manager | Support data for Desk and integrations |
| `upande_mobile.api.store.apps`, `download` | GET | any signed-in user | The app store |
| `upande_mobile.api.itdesk.*` (`today`, `updates`, `remind`, `reminders`, `device_list`, `set_device`, `problems`, `inbox`, `conversation`, `send`, `apps`) | GET / POST | System Manager or IT Support | The IT dashboard |
| `upande_mobile.api.monitor.set_issue_status`, `purge_now` | POST | System Manager | Resolve or ignore an issue; delete expired events now |

## Site settings

All optional, in `site_config.json`.

| Key | Meaning |
|---|---|
| `upande_mobile_ota_hosts` | Extra hosts allowed for an OTA Base URL (a list or a comma-separated string), beyond `*.github.io` and `raw.githubusercontent.com` |
| `upande_mobile_runtime_major_base` | The `runtimeMajorBase` your apps set in `expo.extra.mobileKit` (default 0). Needed to rate devices correctly when an app uses an offset |
| `upande_mobile_github_token` | A GitHub token for reading releases and workflow runs, to avoid the unauthenticated rate limit (60 per hour per IP). Read-only is enough. The older name `mobile_app_github_token` is still read when this one is not set |
| `upande_mobile_support_doctype` | Where problem reports go: `"Issue"` or `"Mobile Report"`. Default: Issue when the site has it, else Mobile Report |
| `upande_mobile_push` | `0` turns support-reply push notifications off. Default on |
| `upande_mobile_redact_keys` | Extra field names to redact in reports (a list or a comma-separated string) |
| `upande_mobile_min_os` | What counts as an old OS, for example `{"android": 11, "ios": 17}` (defaults 10 and 16) |

## Privacy and security

- **Secrets are redacted again on the server**, even though the phone already did it. Any header, query or body field whose name contains `token`, `secret`, `password`, `passwd`, `pwd`, `apikey`, `authorization`, `cookie`, `credential`, `csrf`, `session`, `privatekey` or `signature`, or has a word such as `sid`, `otp`, `pin`, `cvv`, `cvc`, `card`, `auth`, `usr`, `passcode` or `ssn`, is replaced with `[redacted]`. So is credential-shaped text: Bearer and Basic values, `token key:secret`, and JWTs. Add field names with `upande_mobile_redact_keys`.
- Redaction works by name and by shape. A secret with another name, in free text, can still be stored. Crash data can contain personal data (screen names, ids in URLs). Turn monitoring off per app, shorten retention, or add redact keys to fit your policy.
- Bodies are capped (16 KB hard limit) and binary or multipart bodies are replaced by a size note.
- Reports are filed under the session user and the IP address the server saw. A name or role claimed in the payload is ignored.
- Only System Managers can read the register, issues, events and the dashboard. Mobile App, Mobile App Install, Mobile Issue, Mobile Event, Mobile Report, Mobile Chat Thread, Mobile Chat Message and Mobile Push Token grant access to System Manager only (Mobile App Install User is a child table of the install).
- **GitHub is read by the server only.** The only host requested is `api.github.com`, over https, for a repo that passed the `owner/repo` check; a redirect is followed only within that host. The token (if set) goes only to that host, is never sent to a browser or a phone, and is removed from any error text that is stored. Release notes are never treated as HTML, and every link in them or in the cached data is checked again when the page is built.
- **Fetches are restricted.** The OTA Base URL and the GitHub repo are fetched by the hourly job and, for the OTA URL, by the public manifest endpoint. They are validated in `register_app`, on every save of a Mobile App and again at fetch time, so an edited record cannot point the server at an internal host.

## Upgrade

```bash
cd apps/upande_mobile && git pull && cd ../..
bench --site your.site migrate   # creates new doctypes, for example the support ones
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
| Latest APK is empty | The repo name is wrong or private, or GitHub rate limited the server | Fix `github_repo`; set `upande_mobile_github_token` |
| Refresh from GitHub shows a countdown | One refresh per app per minute | Wait for it to finish |
| `register_app` says the host is not allowed | The OTA Base URL host is not `*.github.io` or `raw.githubusercontent.com` | Use a Pages URL, or add the host to `upande_mobile_ota_hosts` |
| Reports are answered with `paused: true` | `monitoring_off`: Enable Monitoring is off. `unknown_app`: an invalid name, or 20 disabled auto-created apps already exist | Turn Enable Monitoring on; register the app or delete unused disabled ones |
| Reports from before sign-in are refused | Guests are rejected unless the app allows them | Turn on Accept Reports Before Sign-in for that app |
| Reports are answered with 429 | Rate limit | Wait a minute; phones back off by themselves |
| A report is not an Issue | The site has no Issue doctype (no ERPNext or Helpdesk), or `upande_mobile_support_doctype` is `"Mobile Report"` | Install ERPNext/Helpdesk, or remove the setting |
| An Issue is missing from the Support page | Its subject does not start with `[App Name]` of a registered app | Issues the app files always do; edit the subject, or register the app |
| No push arrives | The app has not registered a token (the person did not turn notifications on, or the build lacks `expo-notifications`), the queue worker is not running, the comment was not a plain Comment, or the Issue subject has no `[App]` prefix | Check **Mobile Push Token** for the user; run a worker (`bench worker --queue short`); read the Error Log for "Mobile push" |
| The person does not see a reply | The reply was an email or an internal note, not a **Comment** on the Issue | Add a Comment on the Issue in Desk |
| "Report a problem" or chat fails with a 404 or an unknown doctype error | The backend was updated without `bench migrate`, or is older than 0.2.0 | Update the app and run `bench --site your.site migrate`, then `bench restart` |
| A problem report has no screenshot | The phone sent none (the kit needs `react-native-view-shot`), or it was not a JPEG or over 1.5 MB | Install `react-native-view-shot` in the app and make a new native build |
| Support page is empty | Nobody has reported or written in yet, or the app selector filters to another app | Choose **All apps** |

## Development

The unit tests need no site:

```bash
cd apps/upande_mobile
../../env/bin/python -W ignore -m unittest discover -s upande_mobile/tests -t .
```

Layout: `upande_mobile/api/` (endpoints: `apps.py`, `devices.py`, `monitor.py`, `ota.py`, `support.py`, and `push.py` beside them), `github.py` (reading GitHub and release notes), `redaction.py`, `fingerprint.py`, `validation.py`, the nine doctypes in `upande_mobile/upande_mobile/doctype/` (Mobile App, Mobile App Install, Mobile App Install User, Mobile Issue, Mobile Event, Mobile Report, Mobile Chat Thread, Mobile Chat Message, Mobile Push Token), the dashboard page in `upande_mobile/www/mobile-monitor.html` and `upande_mobile/public/`.

## License

MIT
