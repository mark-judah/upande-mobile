# Reference

Every endpoint, every site setting, what is stored and how to run the tests.

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
| `upande_mobile.api.store.apps`, `download`, `check_updates` (POST), `installers` (IT only) | GET | any signed-in user | The app store |
| `upande_mobile.api.usermgmt.*` (`meta`, `users`, `detail`, `create`, `update`, `add_role_profile`, `remove_role_profile`, `reset_password`, `delete`) | GET / POST | IT Team role profile | User management |
| `upande_mobile.api.rolemgmt.*` (`roles`, `profiles`, `create_role`, `add_role_to_profile`), `upande_mobile.api.permmgmt.*` (`doctypes`, `rules`, `set_permission`, `add_rule`, `remove_rule`, `reset_rules`, `user_permissions`, `add_user_permission`, `remove_user_permission`, ...) | GET / POST | IT Team role profile | Role, doctype and user permission management |
| `upande_mobile.api.itdesk.*` (`today`, `updates`, `remind`, `reminders`, `device_list`, `set_device`, `problems`, `inbox`, `conversation`, `send`, `apps`) | GET / POST | IT Team role profile | The IT dashboard |
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

## Development

The unit tests need no site:

```bash
cd apps/upande_mobile
../../env/bin/python -W ignore -m unittest discover -s upande_mobile/tests -t .
```

Layout: `upande_mobile/api/` (endpoints: `apps.py`, `devices.py`, `monitor.py`, `ota.py`, `support.py`, and `push.py` beside them), `github.py` (reading GitHub and release notes), `redaction.py`, `fingerprint.py`, `validation.py`, the nine doctypes in `upande_mobile/upande_mobile/doctype/` (Mobile App, Mobile App Install, Mobile App Install User, Mobile Issue, Mobile Event, Mobile Report, Mobile Chat Thread, Mobile Chat Message, Mobile Push Token), the dashboard page in `upande_mobile/www/it-dashboard.html` and `upande_mobile/public/`.
