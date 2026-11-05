# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# What the two portal pages (/it-dashboard and /app-store) share: the sign-in redirect, the sidebar and the
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
	"users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
	"shield": '<path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>',
	"lock": '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>',
	"filter": '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
	"sidebar": '<rect x="3" y="3" width="18" height="18" rx="2"/><line x1="9" y1="3" x2="9" y2="21"/>',
}

DASHBOARD = [
	("overview", "Overview", "/it-dashboard#/overview", "house"),
	("updates", "Updates", "/it-dashboard#/updates", "refresh"),
	("devices", "Devices", "/it-dashboard#/devices", "phone"),
	("crashlytics", "Crashlytics", "/it-dashboard#/crashlytics", "alert"),
	("support", "Support", "/it-dashboard#/support", "chat"),
	("apps", "Apps", "/it-dashboard#/apps", "grid"),
]
USER_MANAGEMENT = [
	("users", "User management", "/it-dashboard#/users", "users"),
	("roles", "Role management", "/it-dashboard#/roles", "shield"),
	("permissions", "Doctype permissions", "/it-dashboard#/permissions", "lock"),
	("userperms", "User permissions", "/it-dashboard#/userperms", "filter"),
]
STORE = [("store", "App store", "/app-store", "download")]


def page_context(context, active, title, path):
	"""Send signed-out visitors to the login page and back; fill the shell's context. Returns the context."""
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?" + urlencode({"redirect-to": path})
		raise frappe.Redirect(302)
	from upande_mobile.api.itdesk import can_use, is_developer
	from upande_mobile.api.usermgmt import can_manage

	it = can_use()
	context.no_cache = 1
	context.show_sidebar = False
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.site_title = frappe.db.get_single_value("System Settings", "app_name") or "Upande"
	context.itd_active = active
	context.itd_title = title
	context.itd_is_it = 1 if it else 0
	context.developer = 1 if is_developer() else 0
	manage = can_manage()
	context.itd_users = 1 if manage else 0
	context.itd_nav = ([("IT dashboard", DASHBOARD + (USER_MANAGEMENT if manage else []))] if it else []) + [("Install", STORE)]
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
