# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# What the two portal pages (/mobile-monitor and /app-store) share: the sign-in redirect, the sidebar and the
# template context for the dashboard shell (templates/includes/shell_start.html).

import os
from urllib.parse import urlencode

import frappe
import frappe.sessions

_PUBLIC = os.path.join(os.path.dirname(__file__), "public")

ICONS = {
	"house": '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-5.999a2 2 0 0 1 2.582 0l7 5.999A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
	"refresh": '<polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>',
	"phone": '<rect x="5" y="2" width="14" height="20" rx="2"/><line x1="12" y1="18" x2="12.01" y2="18"/>',
	"alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/><path d="M12 9v4"/><path d="M12 17h.01"/>',
	"chat": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
	"grid": '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/>',
	"download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
	"menu": '<line x1="3" y1="6" x2="21" y2="6"/><line x1="3" y1="12" x2="21" y2="12"/><line x1="3" y1="18" x2="21" y2="18"/>',
	"sidebar": '<rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/>',
}

DASHBOARD = [
	("overview", "Overview", "/mobile-monitor#/overview", "house"),
	("updates", "Updates", "/mobile-monitor#/updates", "refresh"),
	("devices", "Devices", "/mobile-monitor#/devices", "phone"),
	("crashlytics", "Crashlytics", "/mobile-monitor#/crashlytics", "alert"),
	("support", "Support", "/mobile-monitor#/support", "chat"),
	("apps", "Apps", "/mobile-monitor#/apps", "grid"),
]
STORE = [("store", "App store", "/app-store", "download")]


def page_context(context, active, title, path):
	"""Send signed-out visitors to the login page and back; fill the shell's context. Returns the context."""
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?" + urlencode({"redirect-to": path})
		raise frappe.Redirect(302)
	from upande_mobile.api.itdesk import can_use, is_developer

	it = can_use()
	context.no_cache = 1
	context.show_sidebar = False
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.site_title = frappe.db.get_single_value("System Settings", "app_name") or "Upande"
	context.itd_active = active
	context.itd_title = title
	context.itd_is_it = 1 if it else 0
	context.developer = 1 if is_developer() else 0
	context.itd_nav = ([("IT dashboard", DASHBOARD)] if it else []) + [("Install", STORE)]
	context.itd_icons = ICONS
	user = frappe.session.user
	full = frappe.utils.get_fullname(user)
	context.itd_user = user
	context.itd_user_name = full or user.split("@")[0]
	context.itd_v = _asset_version()
	return context


def _asset_version():
	try:
		return str(int(max(os.path.getmtime(os.path.join(_PUBLIC, "css", "it-v2.css")), os.path.getmtime(os.path.join(_PUBLIC, "js", "it_dashboard.js")))))
	except OSError:
		return "1"
