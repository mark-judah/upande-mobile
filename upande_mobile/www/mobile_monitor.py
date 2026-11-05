# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# /mobile-monitor: the IT dashboard (IT Support and System Manager). The page is a shell: the data comes from
# upande_mobile.api.itdesk.* over the session cookie, so nothing is rendered on the server beyond the CSRF token.

import frappe
from frappe import _

from upande_mobile.api.itdesk import can_use
from upande_mobile.portal import page_context

no_cache = 1


def get_context(context):
	page_context(context, "overview", "Overview", "/mobile-monitor")
	if not can_use():
		frappe.throw(_("The IT dashboard is visible to IT Support and System Managers only."), frappe.PermissionError)
	return context
