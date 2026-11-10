# Upande Mobile

The Frappe backend for [`@upande/mobile-kit`](https://github.com/mark-judah/upande-mobile-kit), the Settings page and update toolkit for Expo apps. One install serves any number of apps. It serves OTA updates to phones, keeps the device register, collects crash and API reports, receives problem reports and chat, and runs the **IT dashboard** at `/it-dashboard`.

**Needs:** Frappe 16 (this app declares Python 3.14, which Frappe 16 requires), a bench you can run commands on, and, for each mobile app, its public GitHub repository with GitHub Pages on (the [kit README](https://github.com/mark-judah/upande-mobile-kit#do-this-once) covers that side).

## Do this once

Everything you must do on the server is on this page. Skip a step and something silently does not work, so the last column says how to check each one.

### 1. Install the app

```bash
bench get-app https://github.com/mark-judah/upande-mobile
bench --site your.site install-app upande_mobile
bench build --app upande_mobile
bench --site your.site migrate
bench restart
```

### 2. Register each mobile app

```bash
bench --site your.site execute upande_mobile.api.register_app --kwargs \
  '{"app_name": "My App", "github_repo": "owner/my-app", "ota_base_url": "https://owner.github.io/my-app/ota", "enabled": 1}'
```

The kit's `init` wizard prints this line with your values filled in. `app_name` must match `appName` in the app's `src/mobile-kit.ts`. It is safe to run again to change anything. Or create a **Mobile App** in Desk: [the fields](docs/apps.md#in-desk).

### 3. Give the IT staff access

The dashboard opens only for people on the **IT Team** role profile, and for no one else, not even a System Manager who is not on it. If the site has no role profile with that exact name, create one in Desk and give it the roles your IT staff need (not System Manager). Then add the people to it.

### 4. Make sure the background jobs run

The hourly update check needs the **scheduler**, and reply notifications need a worker on the `short` queue. On a normal production bench both already run.

### What breaks if you skip one

| Skipped | What you see | How to check |
|---|---|---|
| `bench build --app upande_mobile` | `/it-dashboard` loads unstyled or stays blank | Open it: a plain page means the assets are missing |
| `migrate` (on install or upgrade) | Problem reports and chat fail with a 404 or an unknown doctype error | `bench --site your.site migrate` prints nothing new when it is up to date |
| `bench restart` | `/it-dashboard` is a 404, or old code keeps running | Reload after restarting |
| Registering the app, or **Enabled** off | Phones get **no OTA** (the manifest answers 204) and every device shows `unknown` | Desk > Mobile App: the app exists and Enabled is ticked |
| The **IT Team** role profile | "You cannot access the dashboard" | Desk > User: the person has the role profile |
| The scheduler | Latest versions never refresh; devices stay `unknown` | `bench --site your.site doctor`; run `bench --site your.site execute upande_mobile.api.devices.refresh_latest` once by hand |
| A worker on the `short` queue | No push when support replies | `bench worker --queue short`, or check the worker process |
| GitHub Pages on the app's repo | OTA is served from a fallback address | See the [kit README](https://github.com/mark-judah/upande-mobile-kit#c-in-the-github-repository-settings-the-steps-people-forget) |

## Day to day

| I want to | Do this |
|---|---|
| **Add another app** | Register it ([step 2](#2-register-each-mobile-app)) |
| **Upgrade this backend** | `cd apps/upande_mobile && git pull && cd ../..`, then `bench --site your.site migrate`, `bench build --app upande_mobile`, `bench restart` |
| **Open the dashboard** | `https://your.site/it-dashboard` (bookmark it; it has no Desk menu entry) |
| **Add or remove an IT person** | Add or remove the **IT Team** role profile on their user in Desk |
| **Add a user, role or permission without System Manager** | Use the dashboard's [User management, Role management, Doctype permissions and User permissions](docs/dashboard.md#user-management) |
| **Refresh the latest versions now** | `bench --site your.site execute upande_mobile.api.devices.refresh_latest`, or **Check for updates** in the dashboard |
| **Turn crash reports off for one app** | Untick **Enable Monitoring** on its Mobile App |
| **Turn off reply notifications** | `"upande_mobile_push": 0` in `site_config.json` |
| **Delete old events now** | `bench --site your.site execute upande_mobile.api.monitor.purge_now` |

Optional `site_config.json` settings (a GitHub token to avoid rate limits, extra allowed hosts, redaction, and more) are in the [reference](docs/reference.md#site-settings).

## When it does not work

| Symptom | Check first |
|---|---|
| Phones get no OTA | The app is registered and **Enabled**; the OTA Base URL is right; nothing is published for the phone's runtime. Read the Error Log for "Mobile OTA manifest" |
| Devices show `unknown` | The app is disabled or unregistered, or the hourly check has not run. Run `refresh_latest` and read `Last Error` on the Mobile App |
| The dashboard is unstyled | `bench build --app upande_mobile`, then restart |
| The dashboard says you cannot access it | The user is not on the **IT Team** role profile |
| Reports are paused or refused | **Enable Monitoring** is off, the app is unknown, or guests are not allowed for the app |
| No push on a reply | The reply must be a **Comment** on the Issue; a worker must run on the `short` queue |

More in [Troubleshooting](docs/troubleshooting.md).

## What it does

| Area | Features |
|---|---|
| **Updates and versions** | [OTA manifest proxy](docs/apps.md#how-ota-works) · [app registration](docs/apps.md#in-desk) · [device register](docs/apps.md#device-register) · [version reporting](docs/apps.md#version-reporting) |
| **Dashboard** | [IT dashboard](docs/dashboard.md) · [user, role and permission management](docs/dashboard.md#user-management) · [app store](docs/dashboard.md#app-store) · [reminders](docs/dashboard.md#reminders) |
| **Monitoring** | [Crash and API monitoring](docs/monitoring-and-support.md#monitoring) · [network and performance](docs/monitoring-and-support.md#network-and-performance) |
| **Support** | [Problem reports](docs/monitoring-and-support.md#problem-reports) · [chat with support](docs/monitoring-and-support.md#chat-with-support) · [push notifications](docs/monitoring-and-support.md#push-notifications) |

## More

| Page | What is in it |
|---|---|
| [Apps, OTA and devices](docs/apps.md) | The Mobile App fields, how OTA works, the device register |
| [The IT dashboard](docs/dashboard.md) | Every screen, user management, the app store, reminders |
| [Monitoring and support](docs/monitoring-and-support.md) | Issues, health, retention, problem reports, chat, push |
| [Reference](docs/reference.md) | Every endpoint, every site setting, privacy and security, development |
| [Troubleshooting](docs/troubleshooting.md) | Symptom to fix |

## License

MIT
