# Apps, OTA and devices

How an app is registered, how phones get updates, and how the device register works.

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

## In the app

The kit adds pages and tools inside each Expo app, and this backend feeds them. How they look and behave is in the [kit README](https://github.com/mark-judah/upande-mobile-kit#readme).

| In the app | Served by |
|---|---|
| **Settings** (`/mobile-kit/settings`, for every user): versions, **Check for updates**, fingerprint switch, crash-report switch, the versions of the apps on this site | Frappe's own `frappe.utils.change_log.get_versions` by default; `upande_mobile.api.app_versions` is the same data behind a sign-in (set the kit's `endpoints.versions`) |
| **Devices** (`/mobile-kit/devices`, System Managers only): each device's name, OS, app version, last seen, accounts and a status with the update it needs | `upande_mobile.api.installs` |
| **Report a problem** and **Chat with support** (the Help card) | [`upande_mobile.api.support`](monitoring-and-support.md#support) |
| **Device reports** on launch, sign-in and foreground | `upande_mobile.api.register_install` |
| **Crash and API reports** | `upande_mobile.api.monitor.ingest` |

The in-app Devices page is about versions. Battery, storage, network and each device's crash history are in the [web portal](dashboard.md).
