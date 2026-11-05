# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# The app store: one page (`/app-store`) listing every enabled app that has a published APK, and a download that
# goes through this site, so people never see (or need access to) the GitHub repository behind it.
#
#   - `apps()` lists name, APK version and release date. No repository, no GitHub URL.
#   - `download(app)` streams the newest APK. The first request for a version fetches it from GitHub (only from
#     github.com and GitHub's own download hosts, redirects checked) into the site's private files; later requests
#     are served from there, and older copies of the same app are deleted.
#
# Any signed-in user may list and download: installing an app is not an IT-only job.

import os
import re
from urllib.parse import urlsplit

import frappe
import requests
from frappe import _
from frappe.utils import cint

MOBILE_APP = "Mobile App"
FOLDER = "mobile_apks"
MAX_APK_BYTES = 300 * 1024 * 1024
ALLOWED_HOSTS = ("github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com")
MAX_HOPS = 5
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def allowed_download_url(url):
	"""Whether `url` is an https URL on one of GitHub's own download hosts."""
	parts = urlsplit(str(url or ""))
	try:
		port = parts.port
	except ValueError:
		return False
	host = (parts.hostname or "").lower()
	return parts.scheme == "https" and host in ALLOWED_HOSTS and not parts.username and port in (None, 443)


def apk_filename(app, version):
	"""A safe file name such as `Upande-Sales-1.4.2.apk`."""
	base = _SAFE.sub("-", str(app or "app")).strip("-.") or "app"
	ver = _SAFE.sub("-", str(version or "")).strip("-.")
	return f"{base}-{ver}.apk" if ver else f"{base}.apk"


def _signed_in():
	if frappe.session.user == "Guest":
		frappe.throw(_("Sign in to use the app store."), frappe.AuthenticationError)


def _rows():
	return frappe.get_all(
		MOBILE_APP,
		filters={"enabled": 1, "latest_apk_url": ["is", "set"], "latest_apk_version": ["is", "set"]},
		fields=["name", "latest_apk_version", "latest_apk_url", "latest_apk_published_at", "latest_apk_size", "platform"],
		order_by="name asc",
		limit_page_length=0,
	)


def _folder():
	path = frappe.get_site_path("private", "files", FOLDER)
	os.makedirs(path, exist_ok=True)
	return path


@frappe.whitelist(methods=["GET"])
def apps():
	"""The apps people can install: name, newest APK version and when it came out. Never a repository or URL."""
	_signed_in()
	folder = os.path.join(frappe.get_site_path("private", "files"), FOLDER)
	out = []
	for r in _rows():
		if not allowed_download_url(r.latest_apk_url):
			continue
		path = os.path.join(folder, apk_filename(r.name, r.latest_apk_version))
		size = os.path.getsize(path) if os.path.exists(path) else cint(r.latest_apk_size)
		out.append(
			{
				"app": r.name,
				"version": r.latest_apk_version,
				"released": str(r.latest_apk_published_at or ""),
				"platform": r.platform or "android",
				"size_mb": round(size / 1024 / 1024, 1) if size else None,
				"file": apk_filename(r.name, r.latest_apk_version),
			}
		)
	return {"apps": out}


def _fetch(url, dest):
	"""Download `url` to `dest` (through a temp file), following redirects only to allowed hosts."""
	tmp = dest + ".part"
	try:
		for _hop in range(MAX_HOPS + 1):
			response = requests.get(url, stream=True, timeout=30, allow_redirects=False, headers={"User-Agent": "upande-mobile"})
			if response.status_code in (301, 302, 303, 307, 308):
				url = response.headers.get("Location") or ""
				response.close()
				if not allowed_download_url(url):
					raise ValueError("redirect")
				continue
			break
		else:
			raise ValueError("redirects")
		response.raise_for_status()
		size = 0
		with open(tmp, "wb") as fh:
			for chunk in response.iter_content(1024 * 256):
				size += len(chunk)
				if size > MAX_APK_BYTES:
					raise ValueError("too large")
				fh.write(chunk)
		if size < 1024:
			raise ValueError("empty")
		os.replace(tmp, dest)
	except Exception:
		if os.path.exists(tmp):
			os.remove(tmp)
		raise


@frappe.whitelist(methods=["GET"])
def download(app=None):
	"""The newest APK of `app` as a file download."""
	_signed_in()
	row = next((r for r in _rows() if r.name == app), None)
	if not row or not allowed_download_url(row.latest_apk_url):
		frappe.throw(_("There is no download for this app yet."), frappe.DoesNotExistError)
	name = apk_filename(row.name, row.latest_apk_version)
	dest = os.path.join(_folder(), name)
	if not os.path.exists(dest):
		try:
			_fetch(row.latest_apk_url, dest)
		except Exception:
			frappe.log_error(title="Mobile app store: download failed")
			frappe.throw(_("The download is not available right now. Try again in a few minutes."), frappe.ValidationError)
		prefix = apk_filename(row.name, "")[: -len(".apk")]
		for other in os.listdir(_folder()):
			if other != name and re.fullmatch(re.escape(prefix) + r"-\d+(\.\d+)*\.apk", other):
				try:
					os.remove(os.path.join(_folder(), other))
				except OSError:
					pass
	with open(dest, "rb") as fh:
		frappe.response["filecontent"] = fh.read()
	frappe.response["filename"] = name
	frappe.response["type"] = "download"
	frappe.response["content_type"] = "application/vnd.android.package-archive"


@frappe.whitelist(methods=["GET"])
def installers(app=None):
	"""Everyone who has installed `app`, with the version they run and when they last opened it. IT staff only: it lists
	people, which the public list of apps does not."""
	from upande_mobile.api import itdesk

	if not itdesk.can_use():
		frappe.throw(_("Only IT Support and System Managers can see who installed an app."), frappe.PermissionError)
	if not frappe.db.exists(MOBILE_APP, app):
		frappe.throw(_("Unknown app."), frappe.DoesNotExistError)
	rows = itdesk.people(app)
	rows.sort(key=lambda p: str(p["last_seen"] or ""), reverse=True)
	return {
		"app": app,
		"people": [
			{"user": p["user"], "name": p["full_name"], "team": p["team"], "version": p["installed"], "bucket": p["bucket"], "device": p["device"], "last_seen": p["last_seen"]}
			for p in rows
		],
		"latest": rows[0]["target"] if rows else "",
	}
