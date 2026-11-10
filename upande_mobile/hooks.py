app_name = "upande_mobile"
app_title = "Upande Mobile"
app_publisher = "Upande"
app_description = "Device register, OTA manifest proxy and app version tracking for every Expo app that uses @upande/mobile-kit"
app_email = "dev@upande.com"
app_license = "mit"

# Latest published OTA / APK of every enabled Mobile App, then re-rate every install.
scheduler_events = {
	"cron": {
		"0 * * * *": ["upande_mobile.api.devices.refresh_latest"],
		# Delete crash / API events past each app's retention (issues are kept).
		"30 2 * * *": ["upande_mobile.api.monitor.purge_old_events"],
	},
}

# A Comment on an Issue is a support reply: push it to the person who raised the Issue.
doc_events = {"Comment": {"after_insert": "upande_mobile.push.on_comment"}}

# Composite indexes the dashboard's filters rely on (idempotent).
after_migrate = ["upande_mobile.indexes.ensure_indexes"]
