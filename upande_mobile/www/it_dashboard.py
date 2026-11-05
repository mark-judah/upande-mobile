# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# /it-dashboard: the IT dashboard (IT Team role profile and System Manager; anyone else gets a "no access" page). The page is a shell: the data comes from
# upande_mobile.api.itdesk.* over the session cookie, so nothing is rendered on the server beyond the CSRF token.

import frappe

from upande_mobile.api.itdesk import can_use
from upande_mobile.portal import page_context

no_cache = 1


def get_context(context):
	page_context(context, "overview", "Overview", "/it-dashboard")
	context.no_access = 0 if can_use() else 1
	if context.no_access:
		context.itd_title = "Access needed"
	return context
