# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from upande_mobile.validation import InvalidSource, validate_github_repo, validate_ota_base_url


class MobileApp(Document):
	"""One mobile app: where it publishes, and its latest published OTA / APK (refreshed hourly)."""

	def validate(self):
		# The URL is fetched by the server, so it is checked on every save, not just in register_app.
		try:
			self.github_repo = validate_github_repo(self.github_repo) or None
			self.ota_base_url = validate_ota_base_url(self.ota_base_url) or None
		except InvalidSource as exc:
			frappe.throw(_(str(exc)))
		# Monitoring retention: at least a day, at most a year.
		self.event_retention_days = max(1, min(self.event_retention_days or 30, 365))
