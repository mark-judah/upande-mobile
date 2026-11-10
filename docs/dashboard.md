# The IT dashboard

The IT dashboard, user and permission management, the app store and reminders.

**`https://your.site/it-dashboard`** is an IT dashboard for the person who looks after the apps: who is out of date, which devices need a hand, what is crashing and who is waiting for an answer. It is written in plain language; the developer view is folded away.

- **Who can open it:** Only people on the **IT Team** role profile (nothing to create or migrate). A System Manager who is not on the profile is turned away too, and can add the profile to their own account in Desk. Everyone else who opens `/it-dashboard` sees a page explaining that the dashboard is for the IT Team, with a link to the App store. Developer details (stack traces, install ids, runtime, event ids, diagnostics) are shown to System Managers only. Signed-out visitors are sent to the login page and back. There is no Desk menu entry, so bookmark the address.
- **Before it works:** the page loads its CSS and JavaScript from `/assets/upande_mobile/` (run `bench build --app upande_mobile` once after installing), and an app must be registered.
- **The app selector** at the top filters every screen to one app. Everything from a device is shown as plain text, never as HTML.

| Screen | Shows |
|---|---|
| Overview | Four headline numbers (people, devices, adoption, open tickets), then charts: people by update state per app, **Android versions** (older than the supported minimum in red), events reported per day for 14 days by kind, support tickets by status, device health. The app filter applies to all of it |
| Updates | The chase list. One row per person and app: version they have and need, how long ago the release came out, last seen, team (from Employee, when present), how many reminders they had. Groups: pending update (reopening the app is enough), pending install (an APK), not seen lately, up to date. Select people and press **Remind** to send a push notification |
| Devices | Inventory with serial number, MAC address, device IP, the IP the server saw and build number as columns, plus owner, model, app, health, battery, storage and last seen. Open a row to mark the device Active, Lost or Retired and add a note. Lost and retired devices drop off the chase list |
| Crashlytics | The issues the apps reported (crashes, errors, API failures, logs), most users affected first: title, file, kind, events, users, devices, versions, last seen, status. Filter by status, kind, app and search; open an issue to see who hit it and mark it resolved or ignored. Stack traces and event ids sit in a folded Developer details panel (System Managers) |
| Support | The inbox (full name first, then email), longest wait on top, with **chat**; the screenshot a person sent shows inside their first message, like a chat app: read the conversation and answer in the page. The answer is stored on the conversation (a Comment on the Issue, so Desk still shows it) and pushed to the person's phone, so nobody has to open the Issue to reply. Change the status from the same page |
| Apps | One row per app: share of people on the current version, behind, away, problems, open tickets |

## User management

A sixth screen, **User management**, lets the IT team add, edit, disable and delete people and give them role profiles without being System Managers (a System Manager can also see the Server Scripts, Client Scripts and the rest of the code, which is why that role should not go to IT). It is shown only to people who hold the **IT Team** role profile and to System Managers. There is nothing to create or migrate: add a person to the IT Team profile in Desk and the screen appears for them. That profile cannot be handed out from this screen, so only a System Manager decides who can manage users, and every change is checked on the server, not just hidden in the page.

- **What you can do:** add a person with a password and role profiles; rename them; turn the account on or off; add or remove one role profile at a time; reset the password; delete. Every change is written to the person's record as an "IT:" note, shown under "What IT changed".
- **Roles come from role profiles.** In v16 a person's roles are rebuilt from their role profiles on every save, so this screen never sets individual roles. Removing a person's last profile removes all their roles.
- **Guard rails:** System Manager, Script Manager, Administrator and Guest can never be given from here, and a role profile that contains one of them cannot be assigned from here either. People who already hold one of those roles, and the Administrator and Guest accounts, are read-only here (manage them in Desk). Someone who is not a System Manager cannot create or change accounts on the `upande` domain. Nobody can disable, delete or change the role profiles of their own account. Deleting is refused when records point at the person; disable them instead.
- **Not here:** creating or editing role profiles themselves (that changes access for everyone who holds them), and system roles. Do those in Desk.

## Role management, Doctype permissions and User permissions

Three more screens under the same IT Team gate (and the same rule that nothing is checked only in the page: the server refuses too).

- **Role management:** list the roles and the role profiles. Add a role, or add a role to a role profile (everyone on the profile gets it; Frappe updates them in the background, so it can take a moment). Removing a role from a profile and deleting roles are not offered. System Manager, Script Manager, Administrator, Guest and All are never listed, created or added.
- **Doctype permissions:** the Role Permissions Manager. Search a doctype, see which roles can select, read, write, create, delete, submit, cancel, amend, report, export, import, share, print, email and mask it, tick or untick any of them, add a role (starting with read), remove a rule, or reset the doctype to its shipped rules. Rules for System Manager and Script Manager are shown but locked, and doctypes that hold code, users, roles or permissions (Server Script, Client Script, DocType, User, Role, Custom DocPerm, System Settings ...) cannot be edited here, because giving a role write access to them would be a way round the guard rails. Your first change copies the shipped rules into Custom DocPerm, as in Desk.
- **User permissions:** list, add and remove user permissions (limit a person to one Company, Farm, Warehouse ...). You cannot set them for people who hold a system role or for the Administrator.
- **Toasts:** every create, change and delete in the dashboard shows a toast. (Toasts used to be placed outside the dashboard wrapper, where the colours were not defined, so they were invisible; they now live inside it.)

## App store

**`https://your.site/app-store`** lists every enabled app that has a published APK, for any signed-in user (the dashboard sidebar links to it). The page never shows the GitHub repository: **Download** goes through `upande_mobile.api.store.download`, which fetches the newest APK from GitHub (only github.com and GitHub's download hosts) into the site's private files on first use, serves it from there afterwards and deletes older copies. Public repositories only: a private repo's release link cannot be fetched this way.

**Check for updates.** The App store and Apps screens have a **Check for updates** button that runs the same GitHub check as the hourly job (`upande_mobile.api.store.check_updates`), for any signed-in user, at most once every two minutes for everyone; inside that window it says how long to wait.

## Reminders

- **Remind** sends a push ("Version 2.1.0 is ready. Close and reopen the app to get it", or "needs a fresh install" when an APK is required). Someone reminded in the last 20 hours is skipped, and the page says who has not allowed notifications. Every reminder is logged in **Mobile Reminder** (who, when, from which version to which, who sent it) and shown on the chase list as "reminded twice".
- A device marked Lost or Retired is never chased. Reminders are only sent when someone presses Remind; nothing is sent automatically.
