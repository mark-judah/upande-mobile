# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# /app-store: every app's newest APK, for any signed-in user. The list and the download come from
# upande_mobile.api.store.*; the GitHub repository behind an app is never sent to the page.

from upande_mobile.portal import page_context

no_cache = 1


def get_context(context):
	return page_context(context, "store", "App store", "/app-store")
