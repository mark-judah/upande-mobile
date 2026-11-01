# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Registering an app with the server in one call (System Manager only).

import frappe
from frappe import _

from upande_mobile.validation import InvalidSource, validate_github_repo, validate_ota_base_url

MOBILE_APP = "Mobile App"


def clean_app_name(app_name):
	from upande_mobile.api.devices import APP_NAME_PATTERN, _text

	name = _text(app_name)
	if not APP_NAME_PATTERN.match(name):
		raise InvalidSource("App name must be 2-64 characters: letters, digits, space, dot, dash, underscore.")
	return name


@frappe.whitelist(methods=["POST"])
def register_app(app_name, github_repo=None, ota_base_url=None, enabled=1):
	"""Create or update a Mobile App record. Idempotent: calling it again updates the same record.

	`github_repo` is `owner/repo` (APK releases are read from it). `ota_base_url`
	is where the app's OTA bundles are published, https on `<owner>.github.io`,
	raw.githubusercontent.com or a host listed in site_config
	`upande_mobile_ota_hosts`. Fields left out (None) are kept as they are.

	From a shell:
	    bench --site <site> execute upande_mobile.api.register_app \\
	        --kwargs '{"app_name": "My App", "github_repo": "me/my-app", "ota_base_url": "https://me.github.io/my-app/ota"}'
	"""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only a System Manager can register an app."), frappe.PermissionError)
	try:
		name = clean_app_name(app_name)
		repo = validate_github_repo(github_repo) if github_repo is not None else None
		url = validate_ota_base_url(ota_base_url) if ota_base_url is not None else None
	except InvalidSource as exc:
		frappe.throw(_(str(exc)), frappe.ValidationError)
	from upande_mobile.api.devices import _flag

	existing = frappe.db.get_value(MOBILE_APP, name, "name")
	doc = frappe.get_doc(MOBILE_APP, existing) if existing else frappe.new_doc(MOBILE_APP)
	doc.app_name = doc.app_name or name
	if repo is not None:
		doc.github_repo = repo
	if url is not None:
		doc.ota_base_url = url
	doc.enabled = _flag(enabled)
	doc.save(ignore_permissions=True) if existing else doc.insert(ignore_permissions=True)
	frappe.db.commit()
	return {
		"app_name": doc.name,
		"enabled": doc.enabled,
		"github_repo": doc.github_repo,
		"ota_base_url": doc.ota_base_url,
		"created": not existing,
	}
