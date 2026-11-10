# Troubleshooting

Find the symptom, read the fix.

| Symptom | Cause | Fix |
|---|---|---|
| `/it-dashboard` is a 404 | The app is not installed on this site, or the bench was not restarted | `bench --site your.site install-app upande_mobile`, then `bench restart` |
| `/it-dashboard` loads without styling or stays blank | The assets were not built | `bench build --app upande_mobile`, then restart |
| `/it-dashboard` says you cannot access it | The user is not on the **IT Team** role profile | Add the profile to the user in Desk |
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
