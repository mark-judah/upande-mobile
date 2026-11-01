# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Crash / error / API-failure reporting for apps that use @upande/mobile-kit.
#
#   ingest            POST  a batch of events from a device (signed in; guests only
#                           when the app's `accept_reports_before_sign_in` is on)
#   overview          GET   dashboard numbers                  (System Manager)
#   issues / issue    GET   grouped problems and one in detail (System Manager)
#   events            GET   raw events, or one with `name`     (System Manager)
#   devices           GET   fleet health from the last reports (System Manager)
#   set_issue_status  POST  Open / Resolved / Ignored          (System Manager)
#   purge_now         POST  delete expired events now          (System Manager)
#
# Built to be cheap for the app and the server: one batched POST, every value
# redacted and capped before it is stored, one issue row per problem however
# often it happens (a runaway loop stops writing event rows after
# STORM_EVENTS_PER_ISSUE_DAY and only counts), indexed filters, paged reads.
# Rows are filed under the SESSION user and the IP the server saw, never ones
# the client claims.

import json
import re
from datetime import datetime, timedelta, timezone

import frappe
from frappe import _
from frappe.utils import add_days, cint, convert_utc_to_system_timezone, flt, get_datetime, now_datetime

from upande_mobile.api.devices import (
	INSTALL,
	INSTALL_ID_PATTERN,
	MOBILE_APP,
	_clip,
	_cmp,
	_observed_ip,
	_text,
	device_key_of,
	parse_version,
)
from upande_mobile.fingerprint import fingerprint
from upande_mobile.redaction import redact_body, redact_headers, redact_text, redact_url, redact_value

ISSUE = "Mobile Issue"
EVENT = "Mobile Event"

KINDS = ("crash", "error", "api", "log")
LEVELS = ("fatal", "error", "warning", "info")
STATUSES = ("Open", "Resolved", "Ignored")
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

# Caps. Anything beyond them is cut or dropped, never an error for the phone.
MAX_EVENTS = 50
MAX_PAYLOAD_BYTES = 512 * 1024
MAX_MESSAGE = 2000
MAX_STACK = 16000
MAX_BREADCRUMBS = 50
MAX_BREADCRUMBS_BYTES = 24 * 1024
MAX_EXTRA_BYTES = 4 * 1024
MAX_BODY_BYTES = 16 * 1024  # hard ceiling whatever the client asks for
DEFAULT_BODY_BYTES = 8 * 1024
STORM_EVENTS_PER_ISSUE_DAY = 200
MAX_FUTURE = timedelta(hours=1)

# Rate limits (requests per window) — a phone sends about two a minute.
RATE_WINDOW_SECONDS = 60
USER_LIMIT = 30
IP_LIMIT = 120
GUEST_IP_LIMIT = 20

DEFAULT_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 365
PURGE_BATCH = 1000
PURGE_MAX_BATCHES = 200

DEFAULT_FLUSH_MS = 30_000
PAUSED_FLUSH_MS = 3_600_000

# Fleet health thresholds.
LOW_BATTERY = 0.2
LOW_STORAGE_BYTES = 512 * 1024 * 1024
LOW_STORAGE_FRACTION = 0.1
DEFAULT_MIN_OS = {"android": 10, "ios": 16}
SNAPSHOT_FRESH_DAYS = 14

MAX_PAGE = 200

_STR_FACTS = (
	"platform", "os_version", "os_build", "brand", "model", "device_name", "battery_state", "network_type",
	"carrier", "locale", "timezone", "orientation", "app_state", "color_scheme", "js_engine", "build_type",
)
_BOOL_FACTS = ("is_physical_device", "low_power_mode", "is_connected", "is_internet_reachable")
_INT_FACTS = ("storage_free_bytes", "storage_total_bytes", "memory_total_bytes")


# --------------------------------------------------------------------------- #
# cleaning — pure, no site needed
# --------------------------------------------------------------------------- #


def _num(value, lo=None, hi=None):
	try:
		number = float(value)
	except (TypeError, ValueError):
		return None
	if number != number or number in (float("inf"), float("-inf")):
		return None
	if lo is not None:
		number = max(lo, number)
	if hi is not None:
		number = min(hi, number)
	return number


def _bool(value):
	if isinstance(value, bool):
		return value
	if isinstance(value, (int, float)):
		return bool(value)
	if isinstance(value, str) and value.strip().lower() in ("true", "false", "1", "0"):
		return value.strip().lower() in ("true", "1")
	return None


def event_time(value, now_utc):
	"""A naive UTC datetime from an ISO string; the future is pinned to now. None when unreadable."""
	text = _text(value)
	if not text:
		return None
	try:
		parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
	except ValueError:
		return None
	if parsed.tzinfo is not None:
		parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
	if parsed > now_utc + MAX_FUTURE:
		return now_utc
	return parsed


def clean_device(raw):
	"""The whitelisted, typed device facts. The hardware id is hashed here and never stored raw."""
	raw = raw if isinstance(raw, dict) else {}
	out = {}
	for key in _STR_FACTS:
		value = _clip(raw.get(key), 100)
		if value:
			out[key] = value
	for key in _BOOL_FACTS:
		value = _bool(raw.get(key))
		if value is not None:
			out[key] = value
	battery = _num(raw.get("battery_level"), 0, 1)
	if battery is not None and raw.get("battery_level") not in ("", None):
		out["battery_level"] = round(battery, 3)
	for key in _INT_FACTS:
		value = _num(raw.get(key), 0, 1e15)
		if value is not None:
			out[key] = int(value)
	screen = raw.get("screen")
	if isinstance(screen, dict):
		clean = {k: _num(screen.get(k), 0, 100000) for k in ("width", "height", "scale", "font_scale")}
		clean = {k: v for k, v in clean.items() if v is not None}
		if clean:
			out["screen"] = clean
	install_id = _text(raw.get("install_id"))
	if INSTALL_ID_PATTERN.match(install_id):
		out["install_id"] = install_id
	key = device_key_of(raw.get("device_key"))
	if key:
		out["device_key_hash"] = key
	return out


def clean_breadcrumbs(raw):
	"""At most MAX_BREADCRUMBS of the newest crumbs, redacted, within MAX_BREADCRUMBS_BYTES."""
	if not isinstance(raw, list):
		return []
	crumbs = []
	for item in raw[-MAX_BREADCRUMBS:]:
		if not isinstance(item, dict):
			continue
		crumb = {
			"ts": _clip(item.get("ts"), 40),
			"type": _clip(item.get("type"), 30),
			"message": redact_text(item.get("message"), 300),
		}
		if item.get("data") is not None:
			crumb["data"] = redact_value(item.get("data"))
		crumbs.append(crumb)
	while crumbs and len(json.dumps(crumbs, default=str)) > MAX_BREADCRUMBS_BYTES:
		crumbs.pop(0)
	return crumbs


def clean_request(raw, extra_keys=()):
	"""A failed API call as stored: URL, headers and bodies redacted, bodies capped."""
	if not isinstance(raw, dict):
		return None
	cap = int(_num(raw.get("max_body_bytes"), 256, MAX_BODY_BYTES) or DEFAULT_BODY_BYTES)
	cap = min(cap, MAX_BODY_BYTES)
	request_headers = redact_headers(raw.get("request_headers"), extra_keys)
	response_headers = redact_headers(raw.get("response_headers"), extra_keys)
	method = _text(raw.get("method"), "GET").upper()
	status = _num(raw.get("status"), 0, 999)
	duration = _num(raw.get("duration_ms"), 0, 3_600_000)
	return {
		"method": method if method in HTTP_METHODS else "GET",
		"url": redact_url(raw.get("url"), extra_keys),
		"status": int(status) if status is not None else 0,
		"duration_ms": int(duration) if duration is not None else 0,
		"request_headers": request_headers,
		"request_body": redact_body(raw.get("request_body"), request_headers, extra_keys, cap),
		"response_headers": response_headers,
		"response_body": redact_body(raw.get("response_body"), response_headers, extra_keys, cap),
		"truncated": bool(_bool(raw.get("truncated"))),
		"error": redact_text(raw.get("error"), 300),
	}


def clean_event(raw, now_utc, extra_keys=()):
	"""One reported event as the dict that is stored, or None when it is not an event."""
	if not isinstance(raw, dict):
		return None
	kind = _text(raw.get("kind"), "error").lower()
	if kind not in KINDS:
		kind = "log"
	level = _text(raw.get("level")).lower()
	if level not in LEVELS:
		level = "fatal" if kind == "crash" else ("warning" if kind == "api" else "error")
	request = clean_request(raw.get("request"), extra_keys) if kind == "api" or raw.get("request") else None
	message = redact_text(raw.get("message"), MAX_MESSAGE)
	if not message and not request and not raw.get("stack"):
		return None
	ts = event_time(raw.get("ts"), now_utc) or now_utc
	extra = raw.get("extra")
	if isinstance(extra, dict):
		extra = redact_value(extra)
		if len(json.dumps(extra, default=str)) > MAX_EXTRA_BYTES:
			extra = {"truncated": True}
	else:
		extra = None
	return {
		"event_id": _clip(raw.get("id"), 64),
		"kind": kind,
		"level": level,
		"ts": ts,
		"message": message,
		"name": _clip(raw.get("name"), 100),
		"stack": redact_text(raw.get("stack"), MAX_STACK),
		"fatal": 1 if (kind == "crash" or _bool(raw.get("fatal"))) else 0,
		"count": max(1, min(cint(raw.get("count")) or 1, 100000)),
		"screen": _clip(raw.get("screen"), 140),
		"session_id": _clip(raw.get("session_id"), 64),
		"app_version": _clip(raw.get("app_version"), 40),
		"runtime_version": _clip(raw.get("runtime_version"), 40),
		"update_id": _clip(raw.get("update_id"), 64),
		"breadcrumbs": clean_breadcrumbs(raw.get("breadcrumbs")),
		"request": request,
		"device": clean_device(raw.get("device")),
		"extra": extra,
	}


def os_major(os_version):
	"""The leading integer of "13", "Android 14", "17.2.1" — 0 when there is none."""
	m = re.search(r"\d+", _text(os_version))
	return int(m.group(0)) if m else 0


def connectivity_of(device):
	"""'online' / 'offline' / '' (unknown) from the reported network flags."""
	if device.get("is_connected") is False or device.get("is_internet_reachable") is False:
		return "offline"
	if device.get("is_connected") or device.get("is_internet_reachable"):
		return "online"
	return ""


def retention_cutoff(days, now_utc=None):
	"""The naive datetime before which events are expired, with the days clamped to 1..365."""
	days = cint(days) or DEFAULT_RETENTION_DAYS
	days = max(1, min(days, MAX_RETENTION_DAYS))
	return (now_utc or datetime.utcnow()) - timedelta(days=days)


def newer_version(candidate, base):
	"""True when `candidate` is a strictly newer app version than `base` (both must parse)."""
	a, b = parse_version(candidate), parse_version(base)
	return bool(a and b and _cmp(a, b) > 0)


def health_flags(row, min_os=None, now_dt=None):
	"""Why a device needs attention, from its register row (with the last report's health fields)."""
	min_os = min_os or DEFAULT_MIN_OS
	flags = []
	has_snapshot = bool(row.get("snapshot_at"))
	battery = flt(row.get("battery_level"))
	snapshot = row.get("snapshot") or {}
	if has_snapshot and 0 < battery < LOW_BATTERY and snapshot.get("battery_state") not in ("charging", "full"):
		flags.append("low_battery")
	total, free = cint(row.get("storage_total_bytes")), cint(row.get("storage_free_bytes"))
	if total > 0 and (free < LOW_STORAGE_BYTES or free < total * LOW_STORAGE_FRACTION):
		flags.append("low_storage")
	if row.get("connectivity") == "offline":
		flags.append("offline")
	major = cint(row.get("os_major"))
	if major > 0 and major < cint(min_os.get(_text(row.get("platform")).lower(), 0)):
		flags.append("outdated_os")
	if row.get("status") in ("behind_ota", "needs_apk"):
		flags.append("outdated_app")
	return flags


# --------------------------------------------------------------------------- #
# request plumbing
# --------------------------------------------------------------------------- #


def _cache():
	# frappe.cache is a callable on v15 and the wrapper instance on v16+.
	cache = frappe.cache
	return cache() if callable(cache) else cache


def _over_limit(scope, ident, limit, seconds=RATE_WINDOW_SECONDS):
	"""Count one hit against `scope:ident` for this window; True once the count passes `limit`."""
	if not ident:
		return False
	cache = _cache()
	window = int(datetime.utcnow().timestamp()) // seconds
	key = cache.make_key(f"upande-mobile-monitor:{scope}:{ident}:{window}")
	count = cint(cache.incrby(key, 1))
	if count == 1:
		cache.expire(key, seconds * 2)
	return count > limit


def _rate_limit(guest, user, ip):
	if guest:
		if _over_limit("guest-ip", ip, GUEST_IP_LIMIT):
			frappe.throw(_("Too many reports. Try again later."), frappe.TooManyRequestsError)
		return
	if _over_limit("user", user, USER_LIMIT) or _over_limit("ip", ip, IP_LIMIT):
		frappe.throw(_("Too many reports. Try again later."), frappe.TooManyRequestsError)


def _parse_payload(payload):
	"""The request's JSON object, size-capped. Raises ValidationError when unusable."""
	if payload is None:
		payload = (getattr(frappe.local, "form_dict", None) or {}).get("payload")
	if isinstance(payload, (bytes, bytearray)):
		payload = bytes(payload).decode("utf-8", "replace")
	if isinstance(payload, str):
		if len(payload) > MAX_PAYLOAD_BYTES:
			frappe.throw(_("Report too large (limit {0} KB).").format(MAX_PAYLOAD_BYTES // 1024), frappe.ValidationError)
		try:
			payload = json.loads(payload)
		except ValueError:
			frappe.throw(_("The report is not valid JSON."), frappe.ValidationError)
	if not isinstance(payload, dict):
		frappe.throw(_("The report must be a JSON object."), frappe.ValidationError)
	return payload


def _extra_redact_keys():
	try:
		value = frappe.conf.get("upande_mobile_redact_keys")
	except Exception:
		return ()
	if isinstance(value, str):
		value = value.split(",")
	return tuple(str(k).strip().lower() for k in (value or []) if str(k).strip())


def _min_os():
	try:
		configured = frappe.conf.get("upande_mobile_min_os")
	except Exception:
		configured = None
	return {**DEFAULT_MIN_OS, **(configured if isinstance(configured, dict) else {})}


def _manager():
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("The mobile monitor is visible to System Managers only."), frappe.PermissionError)


def _json_field(value):
	if isinstance(value, str):
		try:
			return json.loads(value)
		except ValueError:
			return value
	return value


def _sys_time(utc_naive):
	"""A naive UTC datetime as the site's own timezone (how Datetime fields are stored)."""
	converted = convert_utc_to_system_timezone(utc_naive)
	return converted.replace(tzinfo=None) if getattr(converted, "tzinfo", None) else converted


# --------------------------------------------------------------------------- #
# ingest
# --------------------------------------------------------------------------- #


def _find_device(app, install_id, key_hash, memo):
	token = (install_id, key_hash)
	if token in memo:
		return memo[token]
	name = None
	if install_id:
		name = frappe.db.get_value(INSTALL, {"app": app, "install_id": install_id}, "name")
	if not name and key_hash:
		name = frappe.db.get_value(INSTALL, {"app": app, "device_key": key_hash}, "name")
	memo[token] = name
	return name


def _issue_state(fp, app, ev, title, culprit, states):
	"""The in-memory state of an issue for this batch, creating the row on first sight."""
	if fp in states:
		return states[fp]
	row = frappe.db.get_value(
		ISSUE,
		fp,
		["status", "last_version", "resolved_in_version", "last_seen", "level"],
		as_dict=True,
	)
	if not row:
		doc = frappe.get_doc(
			{
				"doctype": ISSUE,
				"fingerprint": fp,
				"app": app,
				"kind": ev["kind"],
				"level": ev["level"],
				"status": "Open",
				"title": title,
				"culprit": culprit,
				"event_count": 0,
				"users_affected": 0,
				"devices_affected": 0,
				"first_seen": _sys_time(ev["ts"]),
				"last_seen": _sys_time(ev["ts"]),
				"first_version": ev["app_version"],
				"last_version": ev["app_version"],
			}
		)
		try:
			doc.insert(ignore_permissions=True, ignore_links=True)
		except frappe.DuplicateEntryError:
			pass  # another request created it a moment ago; read it back below
		row = frappe.db.get_value(
			ISSUE, fp, ["status", "last_version", "resolved_in_version", "last_seen", "level"], as_dict=True
		) or frappe._dict(status="Open", last_version="", resolved_in_version="", last_seen=None, level=ev["level"])
	state = {
		"status": row.status or "Open",
		"last_version": row.last_version or "",
		"resolved_in_version": row.resolved_in_version or "",
		"last_seen": row.last_seen,
		"level": row.level or ev["level"],
		"reopened": False,
		"n": 0,
		"users": 0,
		"devices": 0,
		"newest": None,
		"newest_version": "",
		"newest_event": "",
	}
	states[fp] = state
	return state


def _flush_issue(fp, state):
	if not state["n"] and not state["reopened"]:
		return
	values = {"n": state["n"], "u": state["users"], "d": state["devices"], "fp": fp, "mod": now_datetime()}
	sets = [
		"event_count = event_count + %(n)s",
		"users_affected = users_affected + %(u)s",
		"devices_affected = devices_affected + %(d)s",
		"modified = %(mod)s",
	]
	if state["newest"] is not None:
		sets += ["last_seen = %(seen)s", "last_version = %(ver)s", "last_event = %(evt)s", "level = %(lvl)s"]
		values.update(seen=_sys_time(state["newest"]), ver=state["newest_version"], evt=state["newest_event"], lvl=state["level"])
	if state["reopened"]:
		sets += ["status = 'Open'", "resolved_at = NULL"]
	frappe.db.sql(f"UPDATE `tab{ISSUE}` SET {', '.join(sets)} WHERE name = %(fp)s", values)  # noqa: S608 — fixed fragments only


def _store_event(app, ev, ctx):
	"""File one cleaned event. Returns True when a row was written."""
	# A retry of an event already stored (the phone resends when a response was lost) changes nothing.
	if ev["event_id"] and frappe.db.exists(EVENT, {"app": app, "event_id": ev["event_id"]}):
		return False
	fp, title, culprit = fingerprint(app, ev["kind"], ev["message"], ev["name"], ev["stack"], ev["request"])
	state = _issue_state(fp, app, ev, title, culprit, ctx["states"])

	# A resolved issue that recurs on a newer version is open again.
	if state["status"] == "Resolved" and newer_version(ev["app_version"], state["resolved_in_version"] or state["last_version"]):
		state["status"] = "Open"
		state["reopened"] = True

	user, install_id = ctx["user"], ev["device"].get("install_id") or ctx["install_id"]
	new_user = bool(user and user != "Guest" and (fp, user) not in ctx["seen_users"] and not frappe.db.exists(EVENT, {"issue": fp, "user": user}))
	new_device = bool(install_id and (fp, install_id) not in ctx["seen_devices"] and not frappe.db.exists(EVENT, {"issue": fp, "install_id": install_id}))
	if user and user != "Guest":
		ctx["seen_users"].add((fp, user))
	if install_id:
		ctx["seen_devices"].add((fp, install_id))

	state["n"] += ev["count"]
	state["users"] += 1 if new_user else 0
	state["devices"] += 1 if new_device else 0
	if state["newest"] is None or ev["ts"] >= state["newest"]:
		state["newest"], state["newest_version"] = ev["ts"], ev["app_version"]
		state["level"] = ev["level"]

	# An ignored issue is only counted; a storm is only counted past the daily cap.
	if state["status"] == "Ignored":
		return False
	day = ev["ts"].strftime("%Y%m%d")
	if _over_limit(f"storm:{fp}", day, STORM_EVENTS_PER_ISSUE_DAY, 86400):
		return False

	device = ev["device"]
	req = ev["request"] or {}
	row_name = _find_device(app, install_id, device.get("device_key_hash"), ctx["devices"])
	if row_name:
		newest = ctx["snapshots"].get(row_name)
		if not newest or ev["ts"] >= newest[0]:
			ctx["snapshots"][row_name] = (ev["ts"], device)
	frappe.get_doc(
		{
			"doctype": EVENT,
			"issue": fp,
			"app": app,
			"kind": ev["kind"],
			"level": ev["level"],
			"event_id": ev["event_id"],
			"ts": _sys_time(ev["ts"]),
			"received_at": ctx["received_at"],
			"message": ev["message"],
			"error_name": ev["name"],
			"stack": ev["stack"],
			"fatal": ev["fatal"],
			"count": ev["count"],
			"screen": ev["screen"],
			"session_id": ev["session_id"],
			"app_version": ev["app_version"],
			"runtime_version": ev["runtime_version"],
			"update_id": ev["update_id"],
			"build_type": device.get("build_type") or "",
			"user": user,
			"user_full_name": ctx["full_name"],
			"install_id": install_id or "",
			"device": row_name,
			"ip_address": ctx["ip"],
			"platform": device.get("platform") or "",
			"os_version": device.get("os_version") or "",
			"device_brand": device.get("brand") or "",
			"device_model": device.get("model") or "",
			"battery_level": device.get("battery_level") or 0,
			"network_type": device.get("network_type") or "",
			"storage_free_bytes": device.get("storage_free_bytes") or 0,
			"storage_total_bytes": device.get("storage_total_bytes") or 0,
			"req_method": req.get("method") or "",
			"req_url": req.get("url") or "",
			"req_status": req.get("status") or 0,
			"req_duration_ms": req.get("duration_ms") or 0,
			"req_error": req.get("error") or "",
			"request": json.dumps(req, default=str) if req else None,
			"breadcrumbs": json.dumps(ev["breadcrumbs"], default=str) if ev["breadcrumbs"] else None,
			"device_info": json.dumps(device, default=str) if device else None,
			"user_info": ctx["user_info_json"],
			"extra": json.dumps(ev["extra"], default=str) if ev["extra"] else None,
		}
	).insert(ignore_permissions=True, ignore_links=True)
	state["newest_event"] = ev["event_id"] or ""
	return True


def _write_snapshots(snapshots):
	"""The newest device facts per register row, so Devices health needs no event scan."""
	for row_name, (ts, device) in snapshots.items():
		current = frappe.db.get_value(INSTALL, row_name, "snapshot_at")
		if current and get_datetime(current) >= get_datetime(_sys_time(ts)):
			continue
		values = {"snapshot_at": _sys_time(ts), "device_snapshot": json.dumps(device, default=str)}
		if "battery_level" in device:
			values["battery_level"] = device["battery_level"]
		for key in ("storage_free_bytes", "storage_total_bytes"):
			if key in device:
				values[key] = device[key]
		if device.get("network_type"):
			values["network_type"] = device["network_type"]
		connectivity = connectivity_of(device)
		if connectivity:
			values["connectivity"] = connectivity
		major = os_major(device.get("os_version"))
		if major:
			values["os_major"] = major
		frappe.db.set_value(INSTALL, row_name, values, update_modified=False)


def _reply(accepted, dropped, flush_ms=DEFAULT_FLUSH_MS):
	return {"accepted": accepted, "dropped": dropped, "next_flush_ms": flush_ms}


@frappe.whitelist(allow_guest=True, methods=["POST"])
def ingest(payload=None):
	"""Receive a batch of events from a device. Never raises for a bad event: it is dropped and counted."""
	user = frappe.session.user
	guest = user == "Guest"
	ip = _observed_ip()
	_rate_limit(guest, user, ip)

	data = _parse_payload(payload)
	app_name = _text(data.get("app"))
	app = (
		frappe.db.get_value(
			MOBILE_APP,
			app_name,
			["name", "enabled", "enable_monitoring", "accept_reports_before_sign_in", "event_retention_days"],
			as_dict=True,
		)
		if app_name
		else None
	)
	if guest and not (app and cint(app.accept_reports_before_sign_in)):
		frappe.throw(_("Sign in to report."), frappe.AuthenticationError)

	raw_events = data.get("events")
	raw_events = raw_events if isinstance(raw_events, list) else []
	total = len(raw_events)
	if not app or not cint(app.enabled) or not cint(app.enable_monitoring):
		return _reply(0, total, PAUSED_FLUSH_MS)

	now_utc = datetime.utcnow()
	oldest = retention_cutoff(app.event_retention_days, now_utc)
	extra_keys = _extra_redact_keys()
	install_id = _text(data.get("install_id"))
	install_id = install_id if INSTALL_ID_PATTERN.match(install_id) else ""

	full_name, roles = "", []
	if not guest:
		full_name = frappe.db.get_value("User", user, "full_name") or ""
		roles = [r for r in frappe.get_roles(user) if r not in ("All", "Guest", "Desk User")][:30]
	user_info = {"id": user, "full_name": full_name, "roles": roles} if not guest else None

	ctx = {
		"user": "" if guest else user,
		"install_id": install_id,
		"full_name": full_name,
		"ip": ip,
		"received_at": now_datetime(),
		"states": {},
		"devices": {},
		"snapshots": {},
		"seen_users": set(),
		"seen_devices": set(),
		"user_info_json": json.dumps(user_info) if user_info else None,
	}
	accepted = 0
	dropped = max(0, total - MAX_EVENTS)
	for raw in raw_events[:MAX_EVENTS]:
		try:
			ev = clean_event(raw, now_utc, extra_keys)
			if ev is None or ev["ts"] < oldest:
				dropped += 1
				continue
			_store_event(app.name, ev, ctx)
			accepted += 1
		except Exception:
			dropped += 1
			frappe.log_error(title="Mobile monitor: event dropped")
	for fp, state in ctx["states"].items():
		_flush_issue(fp, state)
	_write_snapshots(ctx["snapshots"])
	frappe.db.commit()
	return _reply(accepted, dropped, DEFAULT_FLUSH_MS if not dropped else DEFAULT_FLUSH_MS * 2)


# --------------------------------------------------------------------------- #
# dashboard
# --------------------------------------------------------------------------- #


def _page(start, page_length, default=25):
	return max(0, cint(start)), max(1, min(cint(page_length) or default, MAX_PAGE))


def _since(days):
	days = max(1, min(cint(days) or 7, 90))
	return days, add_days(now_datetime(), -days)


@frappe.whitelist(methods=["GET"])
def overview(app=None, days=7):
	"""Numbers for the front page: events per day, impact, top issues, versions, models, fleet health."""
	_manager()
	days, since = _since(days)
	app = _text(app)
	cond, vals = (" AND app = %(app)s", {"app": app}) if app else ("", {})
	vals["since"] = since

	by_day = {}
	for r in frappe.db.sql(
		f"SELECT DATE(ts) AS day, kind, COUNT(*) AS n FROM `tab{EVENT}` WHERE ts >= %(since)s{cond} GROUP BY DATE(ts), kind",  # noqa: S608
		vals,
		as_dict=True,
	):
		by_day.setdefault(str(r.day), {})[r.kind] = cint(r.n)
	series = []
	for i in range(days - 1, -1, -1):
		day = str(add_days(now_datetime(), -i).date())
		counts = by_day.get(day, {})
		series.append({"day": day, **{k: counts.get(k, 0) for k in KINDS}})

	totals = frappe.db.sql(
		f"SELECT COUNT(*) AS events, COUNT(DISTINCT install_id) AS devices, COUNT(DISTINCT user) AS users, "  # noqa: S608
		f"SUM(kind = 'crash') AS crashes, SUM(kind = 'api') AS api_errors FROM `tab{EVENT}` WHERE ts >= %(since)s{cond}",
		vals,
		as_dict=True,
	)[0]
	issue_cond, issue_vals = (" WHERE app = %(app)s", {"app": app}) if app else ("", {})
	status_counts = {
		r.status: cint(r.n)
		for r in frappe.db.sql(
			f"SELECT status, COUNT(*) AS n FROM `tab{ISSUE}`{issue_cond} GROUP BY status", issue_vals, as_dict=True  # noqa: S608
		)
	}
	top = frappe.db.sql(
		f"SELECT issue, COUNT(*) AS n FROM `tab{EVENT}` WHERE ts >= %(since)s{cond} AND issue IS NOT NULL GROUP BY issue ORDER BY n DESC LIMIT 10",  # noqa: S608
		vals,
		as_dict=True,
	)
	top_issues = []
	for r in top:
		row = frappe.db.get_value(
			ISSUE, r.issue, ["name", "title", "status", "kind", "level", "app", "users_affected", "devices_affected", "event_count", "last_seen", "last_version"], as_dict=True
		)
		if row:
			top_issues.append({**row, "events_in_range": cint(r.n)})
	versions = frappe.db.sql(
		f"SELECT app_version AS label, COUNT(*) AS n FROM `tab{EVENT}` WHERE ts >= %(since)s{cond} AND app_version != '' GROUP BY app_version ORDER BY n DESC LIMIT 8",  # noqa: S608
		vals,
		as_dict=True,
	)
	models = frappe.db.sql(
		f"SELECT CONCAT_WS(' ', device_brand, device_model) AS label, COUNT(*) AS n FROM `tab{EVENT}` WHERE ts >= %(since)s{cond} AND device_model != '' GROUP BY device_brand, device_model ORDER BY n DESC LIMIT 8",  # noqa: S608
		vals,
		as_dict=True,
	)
	os_versions = frappe.db.sql(
		f"SELECT CONCAT_WS(' ', platform, os_version) AS label, COUNT(*) AS n FROM `tab{EVENT}` WHERE ts >= %(since)s{cond} AND os_version != '' GROUP BY platform, os_version ORDER BY n DESC LIMIT 8",  # noqa: S608
		vals,
		as_dict=True,
	)
	apps = frappe.get_all(MOBILE_APP, fields=["name", "enabled", "enable_monitoring"], order_by="name asc")
	return {
		"days": days,
		"app": app,
		"apps": apps,
		"totals": {k: cint(totals.get(k)) for k in ("events", "devices", "users", "crashes", "api_errors")},
		"issues_by_status": {s: status_counts.get(s, 0) for s in STATUSES},
		"series": series,
		"top_issues": top_issues,
		"versions": versions,
		"models": models,
		"os_versions": os_versions,
		"health": _health_counts(app),
		"server_time": str(now_datetime()),
	}


_ISSUE_LIST_FIELDS = [
	"name", "title", "culprit", "app", "kind", "level", "status", "event_count", "users_affected",
	"devices_affected", "first_seen", "last_seen", "first_version", "last_version", "resolved_in_version",
]
_ISSUE_ORDER = {"last_seen": "last_seen desc", "events": "event_count desc", "users": "users_affected desc", "first_seen": "first_seen desc"}


@frappe.whitelist(methods=["GET"])
def issues(app=None, status=None, version=None, level=None, kind=None, search=None, order_by="last_seen", start=0, page_length=25):
	"""Grouped problems, newest first. Filters: app, status, version (last seen on), level, kind, search."""
	_manager()
	filters = {}
	for key, value, allowed in (("app", app, None), ("status", status, STATUSES), ("level", level, LEVELS), ("kind", kind, KINDS)):
		value = _text(value)
		if value and (allowed is None or value in allowed):
			filters[key] = value
	if _text(version):
		filters["last_version"] = _text(version)
	or_filters = None
	term = _text(search)[:100]
	if term:
		like = f"%{term}%"
		or_filters = [["title", "like", like], ["culprit", "like", like]]
	start, page_length = _page(start, page_length)
	rows = frappe.get_all(
		ISSUE,
		filters=filters,
		or_filters=or_filters,
		fields=_ISSUE_LIST_FIELDS,
		order_by=_ISSUE_ORDER.get(_text(order_by), _ISSUE_ORDER["last_seen"]),
		limit_start=start,
		limit_page_length=page_length,
	)
	total = frappe.get_all(ISSUE, filters=filters, or_filters=or_filters, fields=[{"COUNT": "name", "as": "n"}])[0].n
	return {"rows": rows, "total": cint(total), "start": start, "page_length": page_length}


_EVENT_LIST_FIELDS = [
	"name", "issue", "app", "kind", "level", "ts", "message", "app_version", "user", "user_full_name",
	"install_id", "platform", "os_version", "device_brand", "device_model", "battery_level", "network_type",
	"req_method", "req_url", "req_status", "req_duration_ms", "screen", "count",
]


def _event_full(name):
	doc = frappe.db.get_value(EVENT, name, "*", as_dict=True)
	if not doc:
		frappe.throw(_("Event not found."), frappe.DoesNotExistError)
	for key in ("request", "breadcrumbs", "device_info", "user_info", "extra"):
		doc[key] = _json_field(doc.get(key))
	for key in ("owner", "modified_by", "docstatus", "idx", "creation", "modified"):
		doc.pop(key, None)
	return doc


@frappe.whitelist(methods=["GET"])
def issue(name, events=20):
	"""One issue with its latest events, the newest event in full, and version / model breakdowns."""
	_manager()
	row = frappe.db.get_value(ISSUE, _text(name), "*", as_dict=True)
	if not row:
		frappe.throw(_("Issue not found."), frappe.DoesNotExistError)
	for key in ("owner", "modified_by", "docstatus", "idx", "creation", "modified"):
		row.pop(key, None)
	latest = frappe.get_all(
		EVENT, filters={"issue": row.name}, fields=_EVENT_LIST_FIELDS, order_by="ts desc", limit_page_length=max(1, min(cint(events) or 20, 100))
	)
	by_version = frappe.db.sql(
		f"SELECT app_version AS label, COUNT(*) AS n FROM `tab{EVENT}` WHERE issue = %s GROUP BY app_version ORDER BY n DESC LIMIT 10",  # noqa: S608
		(row.name,),
		as_dict=True,
	)
	by_model = frappe.db.sql(
		f"SELECT CONCAT_WS(' ', device_brand, device_model) AS label, COUNT(*) AS n FROM `tab{EVENT}` WHERE issue = %s GROUP BY device_brand, device_model ORDER BY n DESC LIMIT 10",  # noqa: S608
		(row.name,),
		as_dict=True,
	)
	return {
		"issue": row,
		"events": latest,
		"latest": _event_full(latest[0].name) if latest else None,
		"by_version": by_version,
		"by_model": by_model,
	}


@frappe.whitelist(methods=["GET"])
def events(app=None, issue=None, install_id=None, user=None, kind=None, name=None, start=0, page_length=25):
	"""Raw events newest first (filter by app / issue / device / user / kind), or one in full with `name`."""
	_manager()
	if _text(name):
		return {"event": _event_full(_text(name))}
	filters = {}
	for key, value in (("app", app), ("issue", issue), ("install_id", install_id), ("user", user)):
		if _text(value):
			filters[key] = _text(value)
	if _text(kind) in KINDS:
		filters["kind"] = _text(kind)
	start, page_length = _page(start, page_length)
	rows = frappe.get_all(
		EVENT, filters=filters, fields=_EVENT_LIST_FIELDS, order_by="ts desc", limit_start=start, limit_page_length=page_length
	)
	total = frappe.get_all(EVENT, filters=filters, fields=[{"COUNT": "name", "as": "n"}])[0].n
	return {"rows": rows, "total": cint(total), "start": start, "page_length": page_length}


def _health_sql(min_os):
	"""WHERE fragments (and their values) for each health filter on the register table."""
	fresh = {"fresh": add_days(now_datetime(), -SNAPSHOT_FRESH_DAYS)}
	return {
		"low_battery": ("battery_level > 0 AND battery_level < %(lb)s AND snapshot_at IS NOT NULL", {"lb": LOW_BATTERY}),
		"low_storage": (
			"storage_total_bytes > 0 AND (storage_free_bytes < %(ls)s OR storage_free_bytes < storage_total_bytes * %(lf)s)",
			{"ls": LOW_STORAGE_BYTES, "lf": LOW_STORAGE_FRACTION},
		),
		"offline": ("connectivity = 'offline' AND snapshot_at >= %(fresh)s", fresh),
		"outdated_os": (
			"os_major > 0 AND ((LOWER(platform) = 'android' AND os_major < %(oa)s) OR (LOWER(platform) = 'ios' AND os_major < %(oi)s))",
			{"oa": cint(min_os.get("android")), "oi": cint(min_os.get("ios"))},
		),
		"outdated_app": ("status IN ('behind_ota', 'needs_apk')", {}),
	}


def _health_counts(app=None):
	min_os = _min_os()
	vals = {}
	cond = ""
	if app:
		cond, vals = " AND app = %(app)s", {"app": app}
	counts = {}
	for key, (where, extra) in _health_sql(min_os).items():
		n = frappe.db.sql(f"SELECT COUNT(*) FROM `tab{INSTALL}` WHERE {where}{cond}", {**vals, **extra})[0][0]  # noqa: S608
		counts[key] = cint(n)
	counts["devices"] = cint(frappe.db.sql(f"SELECT COUNT(*) FROM `tab{INSTALL}` WHERE 1=1{cond}", vals)[0][0])  # noqa: S608
	return counts


@frappe.whitelist(methods=["GET"])
def devices(app=None, health=None, search=None, start=0, page_length=25):
	"""Registered devices with their last reported health. `health`: low_battery, low_storage, offline, outdated_os, outdated_app."""
	_manager()
	min_os = _min_os()
	where, vals = ["1=1"], {}
	if _text(app):
		where.append("app = %(app)s")
		vals["app"] = _text(app)
	fragments = _health_sql(min_os)
	if _text(health) in fragments:
		sql, extra = fragments[_text(health)]
		where.append(sql)
		vals.update(extra)
	term = _text(search)[:100]
	if term:
		where.append("(install_id LIKE %(q)s OR user LIKE %(q)s OR device_model LIKE %(q)s OR device_brand LIKE %(q)s OR device_name LIKE %(q)s)")
		vals["q"] = f"%{term}%"
	start, page_length = _page(start, page_length)
	clause = " AND ".join(where)
	total = cint(frappe.db.sql(f"SELECT COUNT(*) FROM `tab{INSTALL}` WHERE {clause}", vals)[0][0])  # noqa: S608
	rows = frappe.db.sql(
		f"SELECT name, install_id, app, user, platform, device_brand, device_model, device_name, os_version, app_version, "  # noqa: S608
		f"runtime_version, status, last_seen, battery_level, storage_free_bytes, storage_total_bytes, network_type, "
		f"connectivity, os_major, snapshot_at, device_snapshot FROM `tab{INSTALL}` WHERE {clause} "
		f"ORDER BY last_seen DESC LIMIT %(limit)s OFFSET %(offset)s",
		{**vals, "limit": page_length, "offset": start},
		as_dict=True,
	)
	names = {r.user for r in rows if r.user}
	full_names = (
		{u.name: u.full_name for u in frappe.get_all("User", filters={"name": ["in", list(names)]}, fields=["name", "full_name"])}
		if names
		else {}
	)
	for r in rows:
		r.snapshot = _json_field(r.pop("device_snapshot", None)) or {}
		r.full_name = full_names.get(r.user, "")
		r.flags = health_flags(r, min_os)
		for key in ("last_seen", "snapshot_at"):
			r[key] = str(r[key]) if r.get(key) else None
	return {"rows": rows, "total": total, "start": start, "page_length": page_length, "summary": _health_counts(_text(app)), "min_os": min_os}


@frappe.whitelist(methods=["POST"])
def set_issue_status(name, status, resolved_in_version=None):
	"""Open, resolve or ignore an issue. Resolving records the version it was fixed in (default: last seen on)."""
	_manager()
	status = _text(status).capitalize()
	if status not in STATUSES:
		frappe.throw(_("Status must be one of: {0}.").format(", ".join(STATUSES)), frappe.ValidationError)
	current = frappe.db.get_value(ISSUE, _text(name), ["name", "last_version"], as_dict=True)
	if not current:
		frappe.throw(_("Issue not found."), frappe.DoesNotExistError)
	values = {"status": status}
	if status == "Resolved":
		values["resolved_in_version"] = _clip(resolved_in_version, 40) or current.last_version or ""
		values["resolved_at"] = now_datetime()
	elif status == "Open":
		values["resolved_at"] = None
	frappe.db.set_value(ISSUE, current.name, values)
	frappe.db.commit()
	return {"name": current.name, **{k: (str(v) if v is not None else None) for k, v in values.items()}}


# --------------------------------------------------------------------------- #
# retention
# --------------------------------------------------------------------------- #


def _purge_app(app, cutoff, batch=PURGE_BATCH, max_batches=PURGE_MAX_BATCHES):
	"""Delete one app's events older than `cutoff` in batches, committing between them."""
	deleted = 0
	for _i in range(max_batches):
		names = frappe.get_all(EVENT, filters={"app": app, "ts": ["<", _sys_time(cutoff)]}, pluck="name", limit_page_length=batch)
		if not names:
			break
		frappe.db.delete(EVENT, {"name": ["in", names]})
		frappe.db.commit()
		deleted += len(names)
		if len(names) < batch:
			break
	return deleted


def purge_old_events(days=None, app=None):
	"""Delete events older than each app's `event_retention_days` (or `days`). Run daily by the scheduler."""
	filters = {"name": app} if app else {}
	deleted = {}
	for row in frappe.get_all(MOBILE_APP, filters=filters, fields=["name", "event_retention_days"]):
		cutoff = retention_cutoff(days or row.event_retention_days)
		deleted[row.name] = _purge_app(row.name, cutoff)
	return deleted


@frappe.whitelist(methods=["POST"])
def purge_now(days=None, app=None):
	"""Delete expired events now. `days` overrides the apps' retention for this run (minimum 1)."""
	_manager()
	deleted = purge_old_events(days=cint(days) or None, app=_text(app) or None)
	return {"deleted": deleted, "total": sum(deleted.values())}
