# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# People talking to the team from inside an app that uses @upande/mobile-kit.
#
#   submit_report   POST  "Report a problem": words, a screenshot and diagnostics   (signed in)
#   chat            GET   the person's conversation with support; `count_only` for the badge (signed in)
#                   POST  one message from the person                                  (signed in)
#   reports / report / set_report_status                                             (System Manager)
#   chat_threads / chat_thread / chat_reply                                          (System Manager)
#
# Everything a phone sends is size-capped and cleaned here; rows are filed under the SESSION user, never one the
# client names. Diagnostics reuse the monitor's device whitelist and the redaction used for crash reports.

import base64
import binascii
import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from upande_mobile.api.devices import APP_NAME_PATTERN, INSTALL_ID_PATTERN, _clip, _text
from upande_mobile.api.monitor import _monitored_app, _over_limit, clean_device
from upande_mobile.redaction import redact_text, redact_url

REPORT = "Mobile Report"
THREAD = "Mobile Chat Thread"
MESSAGE = "Mobile Chat Message"

MAX_SUBJECT = 140
MAX_DESCRIPTION = 5000
MAX_CHAT = 2000
MAX_DIAGNOSTICS_BYTES = 64 * 1024
MAX_SCREENSHOT_B64 = 1_500_000
MAX_CALLS = 20
REPORTS_PER_HOUR = 10
MESSAGES_PER_HOUR = 60
CHAT_PAGE = 100
REPORT_STATUSES = ("Open", "In Progress", "Resolved")
JPEG_MAGIC = b"\xff\xd8\xff"


# --------------------------------------------------------------------------- #
# cleaning — pure, no site needed
# --------------------------------------------------------------------------- #


def _int(value, hi=10**9):
	try:
		number = int(float(value))
	except (TypeError, ValueError):
		return None
	return min(max(number, 0), hi)


def clean_crash(raw):
	"""The remembered crash, redacted and capped; None when there is nothing usable."""
	if not isinstance(raw, dict) or not isinstance(raw.get("message"), str):
		return None
	out = {"message": redact_text(raw.get("message"), 500), "fatal": bool(raw.get("fatal"))}
	for key, limit in (("name", 80), ("stack", 4000), ("source", 20), ("at", 40), ("screen", 200)):
		if isinstance(raw.get(key), str) and raw[key]:
			out[key] = redact_text(raw[key], limit)
	return out


def clean_diagnostics(raw):
	"""What a report may carry: app, user, whitelisted device facts, the last calls (redacted) and the last crash."""
	if isinstance(raw, str):
		if len(raw) > MAX_DIAGNOSTICS_BYTES:
			return {}
		try:
			raw = json.loads(raw)
		except ValueError:
			return {}
	if not isinstance(raw, dict):
		return {}
	out = {}
	if isinstance(raw.get("captured_at"), str):
		out["captured_at"] = _clip(raw["captured_at"], 40)
	app = raw.get("app")
	if isinstance(app, dict):
		out["app"] = {k: _clip(app.get(k), 100) for k in ("name", "version", "runtime_version", "update_id") if app.get(k)}
	user = raw.get("user")
	if isinstance(user, dict):
		out["user"] = {k: _clip(user.get(k), 140) for k in ("id", "full_name") if user.get(k)}
	if isinstance(raw.get("device"), dict):
		device = clean_device(raw["device"])
		device.pop("install_id", None)
		device.pop("device_key_hash", None)
		out["device"] = device
	calls = []
	for item in (raw.get("recent_calls") if isinstance(raw.get("recent_calls"), list) else [])[-MAX_CALLS:]:
		if isinstance(item, dict):
			calls.append(
				{
					"ts": _clip(item.get("ts"), 40),
					"method": _clip(item.get("method"), 10).upper(),
					"url": redact_url(item.get("url") or "", limit=300),
					"status": _int(item.get("status"), 999),
					"ms": _int(item.get("ms")),
				}
			)
	out["recent_calls"] = calls
	crash = clean_crash(raw.get("last_crash"))
	if crash:
		out["last_crash"] = crash
	return out


def decode_screenshot(value):
	"""JPEG bytes from a base64 string, or None when it is missing, oversized or not a JPEG."""
	if not isinstance(value, str) or not value or len(value) > MAX_SCREENSHOT_B64:
		return None
	try:
		data = base64.b64decode(value, validate=True)
	except (binascii.Error, ValueError):
		return None
	return data if data.startswith(JPEG_MAGIC) else None


def message_row(row):
	return {
		"id": row.get("name"),
		"from_staff": bool(cint(row.get("from_staff"))),
		"text": row.get("text") or "",
		"sent_at": str(row.get("sent_at") or ""),
	}


def clean_chat_text(value):
	return (value or "").strip()[:MAX_CHAT] if isinstance(value, str) else ""


# --------------------------------------------------------------------------- #
# people
# --------------------------------------------------------------------------- #


def _signed_in():
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Sign in first."), frappe.AuthenticationError)
	return user


def _require_manager():
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Support is visible to System Managers only."), frappe.PermissionError)


def _app_row(app):
	app = _text(app)
	row = _monitored_app(app, create=True) if APP_NAME_PATTERN.match(app) else None
	if not row:
		frappe.throw(_("Unknown app."), frappe.ValidationError)
	return row.name if hasattr(row, "name") else app


@frappe.whitelist(methods=["POST"])
def submit_report(app=None, subject=None, description=None, install_id=None, diagnostics=None, screenshot=None):
	"""File a problem report from a phone. Returns {"report": name}."""
	user = _signed_in()
	if _over_limit("report", user, REPORTS_PER_HOUR, 3600):
		frappe.throw(_("Too many reports. Try again later."), frappe.TooManyRequestsError)
	app_name = _app_row(app)
	subject = _clip(subject, MAX_SUBJECT)
	if not subject:
		frappe.throw(_("A subject is required."), frappe.ValidationError)
	diag = clean_diagnostics(diagnostics)
	device = diag.get("device") or {}
	install_id = _text(install_id)
	doc = frappe.get_doc(
		{
			"doctype": REPORT,
			"app": app_name,
			"subject": subject,
			"description": redact_text(description, MAX_DESCRIPTION) if description else "",
			"status": "Open",
			"reporter": user,
			"reporter_name": frappe.db.get_value("User", user, "full_name") or user,
			"install_id": install_id if INSTALL_ID_PATTERN.match(install_id) else "",
			"app_version": (diag.get("app") or {}).get("version") or "",
			"runtime_version": (diag.get("app") or {}).get("runtime_version") or "",
			"device_model": " ".join(x for x in (device.get("brand"), device.get("model")) if x),
			"os_version": " ".join(x for x in (device.get("platform"), device.get("os_version")) if x),
			"diagnostics": json.dumps(diag, default=str) if diag else None,
			"last_crash": json.dumps(diag["last_crash"], default=str) if diag.get("last_crash") else None,
		}
	).insert(ignore_permissions=True)
	data = decode_screenshot(screenshot)
	if data:
		try:
			saved = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": f"{doc.name}.jpg",
					"attached_to_doctype": REPORT,
					"attached_to_name": doc.name,
					"attached_to_field": "screenshot",
					"is_private": 1,
					"content": data,
				}
			).insert(ignore_permissions=True)
			doc.db_set("screenshot", saved.file_url, update_modified=False)
		except Exception:
			# The report matters more than its picture.
			frappe.log_error(title="Mobile report: screenshot not saved")
	return {"report": doc.name}


def _thread_of(app_name, user, create):
	name = frappe.db.get_value(THREAD, {"app": app_name, "user": user}, "name")
	if name or not create:
		return name
	return (
		frappe.get_doc(
			{
				"doctype": THREAD,
				"app": app_name,
				"user": user,
				"user_full_name": frappe.db.get_value("User", user, "full_name") or user,
				"unread_staff": 0,
				"unread_user": 0,
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _add_message(thread, sender, text, from_staff):
	"""Store one message and keep the thread's counters and preview current."""
	now = now_datetime()
	doc = frappe.get_doc(
		{"doctype": MESSAGE, "thread": thread, "sender": sender, "from_staff": 1 if from_staff else 0, "text": text, "sent_at": now}
	).insert(ignore_permissions=True)
	counter = "unread_user" if from_staff else "unread_staff"
	frappe.db.set_value(
		THREAD,
		thread,
		{
			"last_message_at": now,
			"last_preview": text[:120],
			counter: cint(frappe.db.get_value(THREAD, thread, counter)) + 1,
		},
		update_modified=False,
	)
	return doc


@frappe.whitelist(methods=["GET", "POST"])
def chat(app=None, text=None, since=None, count_only=None):
	"""The person's conversation with support. POST sends `text`; GET returns messages (after `since`) and marks the
	replies read. `count_only` returns just how many replies are unread."""
	user = _signed_in()
	app_name = _app_row(app)
	if frappe.request and frappe.request.method == "POST":
		message = clean_chat_text(text)
		if not message:
			frappe.throw(_("Write a message first."), frappe.ValidationError)
		if _over_limit("chat", user, MESSAGES_PER_HOUR, 3600):
			frappe.throw(_("Too many messages. Try again later."), frappe.TooManyRequestsError)
		thread = _thread_of(app_name, user, create=True)
		return message_row(_add_message(thread, user, message, False).as_dict())

	thread = _thread_of(app_name, user, create=False)
	if not thread:
		return {"messages": [], "unread": 0}
	if cint(count_only):
		return {"unread": cint(frappe.db.get_value(THREAD, thread, "unread_user"))}
	filters = {"thread": thread}
	if since:
		filters["sent_at"] = (">=", _text(since))
	rows = frappe.get_all(
		MESSAGE, filters=filters, fields=["name", "from_staff", "text", "sent_at"], order_by="sent_at asc", limit_page_length=CHAT_PAGE
	)
	if any(cint(r.get("from_staff")) for r in rows):
		frappe.db.set_value(THREAD, thread, "unread_user", 0, update_modified=False)
	return {"messages": [message_row(r) for r in rows], "unread": 0}


# --------------------------------------------------------------------------- #
# staff
# --------------------------------------------------------------------------- #


@frappe.whitelist(methods=["GET"])
def reports(app=None, status=None, search=None, start=0, page_length=25):
	_require_manager()
	filters = {}
	if app:
		filters["app"] = _text(app)
	if status in REPORT_STATUSES:
		filters["status"] = status
	or_filters = [["subject", "like", f"%{_text(search)[:60]}%"], ["reporter_name", "like", f"%{_text(search)[:60]}%"]] if search else None
	page = min(max(cint(page_length) or 25, 1), 100)
	rows = frappe.get_all(
		REPORT,
		filters=filters,
		or_filters=or_filters,
		fields=["name", "app", "subject", "status", "reporter", "reporter_name", "app_version", "device_model", "os_version", "creation", "screenshot"],
		order_by="creation desc",
		start=max(cint(start), 0),
		page_length=page,
	)
	return {"rows": rows, "total": frappe.db.count(REPORT, filters=filters)}


@frappe.whitelist(methods=["GET"])
def report(name):
	_require_manager()
	doc = frappe.get_doc(REPORT, name)
	out = doc.as_dict()
	for key in ("diagnostics", "last_crash"):
		try:
			out[key] = json.loads(doc.get(key)) if doc.get(key) else None
		except ValueError:
			out[key] = None
	return out


@frappe.whitelist(methods=["POST"])
def set_report_status(name, status):
	_require_manager()
	if status not in REPORT_STATUSES:
		frappe.throw(_("Unknown status."), frappe.ValidationError)
	frappe.db.set_value(REPORT, name, "status", status)
	return {"name": name, "status": status}


@frappe.whitelist(methods=["GET"])
def chat_threads(app=None, start=0, page_length=25):
	_require_manager()
	filters = {"app": _text(app)} if app else {}
	rows = frappe.get_all(
		THREAD,
		filters=filters,
		fields=["name", "app", "user", "user_full_name", "last_message_at", "last_preview", "unread_staff"],
		order_by="last_message_at desc",
		start=max(cint(start), 0),
		page_length=min(max(cint(page_length) or 25, 1), 100),
	)
	return {"rows": rows, "total": frappe.db.count(THREAD, filters=filters), "unread": cint(frappe.db.sql("select coalesce(sum(unread_staff),0) from `tabMobile Chat Thread`")[0][0])}


@frappe.whitelist(methods=["GET"])
def chat_thread(thread):
	_require_manager()
	head = frappe.db.get_value(THREAD, thread, ["name", "app", "user", "user_full_name"], as_dict=True)
	if not head:
		frappe.throw(_("Unknown conversation."), frappe.DoesNotExistError)
	rows = frappe.get_all(MESSAGE, filters={"thread": thread}, fields=["name", "from_staff", "text", "sent_at"], order_by="sent_at asc", limit_page_length=200)
	frappe.db.set_value(THREAD, thread, "unread_staff", 0, update_modified=False)
	return {"thread": head, "messages": [message_row(r) for r in rows]}


@frappe.whitelist(methods=["POST"])
def chat_reply(thread, text=None):
	_require_manager()
	message = clean_chat_text(text)
	if not message:
		frappe.throw(_("Write a message first."), frappe.ValidationError)
	if not frappe.db.exists(THREAD, thread):
		frappe.throw(_("Unknown conversation."), frappe.DoesNotExistError)
	return message_row(_add_message(thread, frappe.session.user, message, True).as_dict())
