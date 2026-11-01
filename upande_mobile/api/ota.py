"""OTA update manifest proxy for every Upande mobile app (expo-updates).

Why this exists
---------------
Apps ship their JavaScript bundles as expo-updates assets published to GitHub
Pages. The expo-updates Android client REQUIRES the manifest response to carry
an ``expo-protocol-version: 1`` header (otherwise "Legacy manifests are no
longer supported" and it falls back to the bundle inside the APK). GitHub Pages
cannot set response headers, so the Frappe site serves the manifest.

One endpoint for all apps: an app's ``updates.url`` is
``https://<site>/api/method/upande_mobile.api.ota.manifest?app=<Mobile App name>``.
The app's ``Mobile App`` record says where it publishes (``ota_base_url``); the
manifest ``<base>/<platform>/<runtime>/manifest.json`` is fetched (cached 60 s
per app + platform + runtime) and returned verbatim with the protocol headers.
Bundle and asset URLs inside it still point at GitHub Pages.

Pages fallback: when the base is ``https://<owner>.github.io/<repo>/...`` and
Pages answers 404 (not enabled yet), the same files on the ``gh-pages`` branch
are read from raw.githubusercontent.com and their URLs rewritten to match.

Failure policy: a fleet of phones polls this on every launch, so it never
answers 500. Unknown/disabled app, nothing published (404) and any upstream
failure are all a 204 with the protocol header ("no update"); failures are
logged once per minute per app + runtime.
"""

import re

import frappe
from werkzeug.wrappers import Response

from upande_mobile.validation import is_safe_base_url

CACHE_SECONDS = 60
FETCH_TIMEOUT = 10

# An expo-updates manifest is a few kB of JSON. Anything larger is not one, and
# caching it would put an arbitrary upstream document into this site's redis.
MAX_MANIFEST_BYTES = 512 * 1024

RUNTIME_RE = re.compile(r"^\d+\.\d+$")
PLATFORMS = ("android", "ios")
PAGES_BASE_RE = re.compile(r"^https://([A-Za-z0-9-]+)\.github\.io/([A-Za-z0-9._-]+)(/.*)?$")

PROTOCOL_HEADERS = {
	"expo-protocol-version": "1",
	"expo-sfv-version": "0",
	"cache-control": "private, max-age=0, no-store",
}


def _cache():
	# frappe.cache is a callable on v15 and the wrapper instance on v16+.
	cache = frappe.cache
	return cache() if callable(cache) else cache


def _header(name, fallback=None):
	request = getattr(frappe.local, "request", None)
	headers = getattr(request, "headers", None) if request is not None else None
	value = None
	if headers is not None:
		try:
			value = headers.get(name)
		except Exception:
			value = None
	return (value or fallback or "").strip()


def base_url_for(app):
	"""The published OTA root of an enabled Mobile App, or "" (unknown, disabled, no URL)."""
	app = (app or "").strip()
	if not app or len(app) > 140:
		return ""
	row = frappe.db.get_value("Mobile App", app, ["enabled", "ota_base_url"], as_dict=True)
	if not row or not row.enabled:
		return ""
	base = (row.ota_base_url or "").strip().rstrip("/")
	# Checked again here: the record can be edited after it was validated, and this is guest-reachable.
	return base if is_safe_base_url(base) else ""


def pages_to_raw(base):
	"""(pages_root, raw_root) for a ``<owner>.github.io/<repo>`` base, else (None, None).

	Pages serves ``https://<owner>.github.io/<repo>/`` from the ``gh-pages`` branch,
	which raw.githubusercontent.com serves as ``.../<owner>/<repo>/gh-pages/``.
	"""
	match = PAGES_BASE_RE.match(base or "")
	if not match:
		return None, None
	owner, repo = match.group(1), match.group(2)
	return (
		f"https://{owner}.github.io/{repo}/",
		f"https://raw.githubusercontent.com/{owner}/{repo}/gh-pages/",
	)


def fetch_upstream(url):
	"""(status_code, body_text) for the published manifest. Split out so tests
	can replace the network call."""
	import requests

	response = requests.get(url, timeout=FETCH_TIMEOUT, headers={"Accept": "application/json"})
	return response.status_code, response.text


def _fetch_manifest(base, platform, runtime):
	"""(status, body, url); on a Pages 404 the raw `gh-pages` copy, with URLs moved there too."""
	url = f"{base}/{platform}/{runtime}/manifest.json"
	status, body = fetch_upstream(url)
	if status != 404:
		return status, body, url
	pages_root, raw_root = pages_to_raw(base)
	if not pages_root or not url.startswith(pages_root):
		return status, body, url
	raw_url = url.replace(pages_root, raw_root, 1)
	raw_status, raw_body = fetch_upstream(raw_url)
	if raw_status != 200:
		return status, body, url
	return raw_status, (raw_body or "").replace(pages_root, raw_root), raw_url


def _no_update():
	response = Response(status=204)
	for key, value in PROTOCOL_HEADERS.items():
		response.headers[key] = value
	return response


def _log_once(key, title, message):
	"""One Error Log per minute per app + runtime, not one per phone."""
	try:
		cache = _cache()
		flag = f"mobile_ota_error:{key}"
		if cache.get_value(flag):
			return
		cache.set_value(flag, 1, expires_in_sec=CACHE_SECONDS)
		frappe.log_error(title=title, message=message)
		# Runs inside a guest GET, which Frappe rolls back unless told otherwise.
		frappe.db.commit()  # nosemgrep: frappe-manual-commit, whitelisted-side-effect-on-get -- keeps the Error Log of a guest GET
	except Exception:
		# Logging the failure must not become the failure.
		pass


# nosemgrep: guest-whitelisted-method -- expo-updates fetches the manifest before login; the response is a public build artefact
@frappe.whitelist(allow_guest=True, methods=["GET"])
def manifest(app: str | None = None, runtime: str | None = None, platform: str | None = None):
	"""Serve the expo-updates manifest of `app` for the caller's runtime version.

	Reads ``expo-runtime-version`` and ``expo-platform`` from the request headers
	(as the expo-updates client sends them); ``runtime`` / ``platform`` query
	parameters are accepted as fallbacks for manual checks.

	Guest is allowed on purpose and on this method only: expo-updates fetches the
	manifest before the app has a session, and the response is a public build
	artefact. The whole body is guarded: whatever it could raise comes back as
	"no update", never a 500 the client reports as a broken app.
	"""
	try:
		return _manifest(app, runtime, platform)
	except Exception:
		_log_once("manifest", "Mobile OTA manifest failed", frappe.get_traceback())
		return _no_update()


def _manifest(app, runtime, platform):
	runtime = _header("expo-runtime-version", runtime)
	platform = _header("expo-platform", platform).lower() or "android"
	if not RUNTIME_RE.match(runtime) or platform not in PLATFORMS:
		return _no_update()
	base = base_url_for(app)
	if not base:
		return _no_update()

	key = f"{app}:{platform}:{runtime}"
	cache = _cache()
	cache_key = f"mobile_ota_manifest:{key}"
	cached = cache.get_value(cache_key)
	if not isinstance(cached, dict) or cached.get("base") != base:
		url = f"{base}/{platform}/{runtime}/manifest.json"
		try:
			status, body, url = _fetch_manifest(base, platform, runtime)
		except Exception:
			_log_once(key, "Mobile OTA manifest fetch failed", frappe.get_traceback() + f"\n\nURL: {url}")
			return _no_update()
		if status == 200 and len(body or "") > MAX_MANIFEST_BYTES:
			_log_once(key, "Mobile OTA manifest too large", f"{len(body)} bytes from {url}")
			return _no_update()
		if status == 200:
			cached = {"status": 200, "body": body, "base": base}
		elif status == 404:
			cached = {"status": 404, "body": "", "base": base}
		else:
			_log_once(key, "Mobile OTA manifest upstream error", f"HTTP {status} from {url}\n\n{body[:2000]}")
			return _no_update()
		cache.set_value(cache_key, cached, expires_in_sec=CACHE_SECONDS)

	if cached["status"] != 200:
		return _no_update()

	response = Response(cached["body"], status=200, content_type="application/json; charset=utf-8")
	for name, value in PROTOCOL_HEADERS.items():
		response.headers[name] = value
	return response
