# Monitoring and support

Crash and API monitoring, problem reports, chat and push notifications.

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

Problem reports and chat from the app's **Help** card, in the portal under **Support** (`/it-dashboard#/support`). They need the doctypes below, so run `bench --site your.site migrate` after installing or upgrading.

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
