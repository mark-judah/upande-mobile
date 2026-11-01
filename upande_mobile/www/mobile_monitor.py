# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# /mobile-monitor — the crash and API-failure dashboard (System Manager only).
# The page is a shell: the data comes from upande_mobile.api.monitor.* over the
# session cookie, so nothing is rendered on the server beyond the CSRF token.

import frappe
from frappe import _

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/mobile-monitor"
		raise frappe.Redirect
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("The mobile monitor is visible to System Managers only."), frappe.PermissionError)
	context.no_cache = 1
	context.show_sidebar = False
	context.csrf_token = frappe.sessions.get_csrf_token()
	context.site_title = frappe.db.get_single_value("System Settings", "app_name") or "Mobile Monitor"
	return context
