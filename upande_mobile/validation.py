# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Validation of the two values an admin supplies that the server later FETCHES:
# a GitHub repo ("owner/repo") and an OTA base URL. The URL is requested by the
# hourly check and by the guest-readable manifest proxy, so it must never point
# somewhere the site could be used to probe (internal hosts, metadata services,
# arbitrary sites). Pure functions: no site or database needed.

import re
from urllib.parse import urlsplit

GITHUB_REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
# A DNS name only: letters, digits, dashes, dots. No IP literals (needs a letter in the last label).
HOST_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]$")

# Hosts always allowed: GitHub Pages sites and the raw gh-pages fallback.
RAW_HOST = "raw.githubusercontent.com"
PAGES_SUFFIX = ".github.io"
MAX_URL_LENGTH = 300


class InvalidSource(ValueError):
	"""The repo or URL is not acceptable."""


def configured_hosts():
	"""Extra hosts the site owner allowed in site_config `upande_mobile_ota_hosts` (list or comma string)."""
	try:
		import frappe

		value = frappe.conf.get("upande_mobile_ota_hosts")
	except Exception:
		return ()
	if isinstance(value, str):
		value = value.split(",")
	return tuple(str(h).strip().lower() for h in (value or []) if str(h).strip())


def validate_github_repo(value):
	"""`owner/repo`, or "" when empty. Raises InvalidSource."""
	value = (value or "").strip()
	if not value:
		return ""
	if not GITHUB_REPO_RE.match(value) or value.endswith(".git") or ".." in value:
		raise InvalidSource("GitHub repo must look like owner/repo.")
	return value


def validate_ota_base_url(value, extra_hosts=None):
	"""A cleaned https base URL (no trailing slash), or "" when empty. Raises InvalidSource.

	Allowed: https, no credentials/port/query/fragment, and a host that is
	`<name>.github.io`, `raw.githubusercontent.com`, or listed in
	`upande_mobile_ota_hosts` (site_config) / `extra_hosts`.
	"""
	value = (value or "").strip()
	if not value:
		return ""
	if len(value) > MAX_URL_LENGTH:
		raise InvalidSource("OTA base URL is too long.")
	parts = urlsplit(value)
	if parts.scheme != "https":
		raise InvalidSource("OTA base URL must use https.")
	if parts.username or parts.password or parts.query or parts.fragment:
		raise InvalidSource("OTA base URL must not contain credentials, a query or a fragment.")
	try:
		port = parts.port
	except ValueError:
		raise InvalidSource("OTA base URL has an invalid port.") from None
	if port not in (None, 443):
		raise InvalidSource("OTA base URL must use the default https port.")
	host = (parts.hostname or "").lower()
	if not HOST_RE.match(host):
		raise InvalidSource("OTA base URL must use a host name.")
	allowed = {RAW_HOST, *(h.lower() for h in (extra_hosts if extra_hosts is not None else configured_hosts()))}
	if not (host in allowed or (host.endswith(PAGES_SUFFIX) and host.count(".") >= 2)):
		raise InvalidSource(
			f"Host {host} is not allowed. Use <owner>.github.io, raw.githubusercontent.com, "
			"or add the host to upande_mobile_ota_hosts in site_config.json."
		)
	return value.rstrip("/")


def is_safe_base_url(value):
	"""True when `value` passes validate_ota_base_url (used at fetch time as a second guard)."""
	try:
		return bool(validate_ota_base_url(value))
	except InvalidSource:
		return False
