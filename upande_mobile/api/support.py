# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# People talking to the team from inside an app that uses @upande/mobile-kit.
#
#   submit_report   POST  "Report a problem": words, a screenshot and diagnostics   (signed in)
#                         Filed as an ERPNext/Helpdesk **Issue** when the site has one (subject "[App] ...", the
#                         screenshot attached to it), else as a Mobile Report.
#   threads         GET   the person's conversations with support (their Issues for this app)  (signed in)
#   thread          GET   one conversation: what they wrote, then the comments on the Issue   (signed in)
#   reply           POST  the person answers on the Issue (a Comment on it)                   (signed in)
#                         Staff reply the way they already do: a Comment on the Issue in Desk. Sites without an
#                         Issue doctype use the Mobile Chat doctypes through `chat` instead.
#   chat            GET/POST  the single-thread chat for sites without Issue                  (signed in)
#   reports / report / set_report_status                                             (System Manager)
#   chat_threads / chat_thread / chat_reply                                          (System Manager)
#
# Everything a phone sends is size-capped and cleaned here; rows are filed under the SESSION user, never one the
# client names. Diagnostics reuse the monitor's device whitelist and the redaction used for crash reports.

import base64
import binascii
import html as htmllib
import json
import re

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from upande_mobile import push
from upande_mobile.api.devices import APP_NAME_PATTERN, INSTALL_ID_PATTERN, _clip, _text
from upande_mobile.api.monitor import _monitored_app, _over_limit, clean_device
from upande_mobile.redaction import redact_text, redact_url

REPORT = "Mobile Report"
ISSUE = "Issue"
ISSUE_STATUSES = ("Open", "Replied", "On Hold", "Resolved", "Closed")
DIAG_DIVIDER = "\u2014 \u2014 \u2014 Diagnostic details (attached automatically) \u2014 \u2014 \u2014"
THREAD = "Mobile Chat Thread"
PUSH = "Mobile Push Token"
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
# the Issue form of a report — pure
# --------------------------------------------------------------------------- #


def issue_subject(app, subject):
	"""`[App] subject`, the convention support already works with."""
	return f"[{app}] {subject}"[:140]


def split_issue_subject(subject):
	"""("App", "subject") from "[App] subject"; (None, subject) when it does not follow the convention."""
	m = re.match(r"^\[([^\]]{1,64})\]\s*(.*)$", subject or "", re.S)
	return (m.group(1), m.group(2)) if m else (None, subject or "")


def issue_description(text, diagnostics):
	"""The Issue's description: what the person wrote, a divider, then the diagnostics as readable JSON."""
	body = htmllib.escape((text or "").strip()) or "(no description provided)"
	blob = htmllib.escape(json.dumps(diagnostics or {}, indent=1, default=str, ensure_ascii=False))
	return (
		f"<p>{body.replace(chr(10), '<br>')}</p><p>{DIAG_DIVIDER}</p>"
		f'<pre class="mobile-diagnostics">{blob}</pre>'
	)


def parse_issue_description(description):
	"""(text, diagnostics dict) back out of an Issue description written by `issue_description` (or by hand)."""
	raw = description or ""
	m = re.search(r"<pre[^>]*>(.*?)</pre>", raw, re.S)
	diagnostics = {}
	if m:
		try:
			parsed = json.loads(htmllib.unescape(re.sub(r"<[^>]+>", "", m.group(1))))
			diagnostics = parsed if isinstance(parsed, dict) else {}
		except ValueError:
			diagnostics = {}
	before = raw.split(DIAG_DIVIDER)[0]
	text = htmllib.unescape(re.sub(r"<[^>]+>", "", before.replace("<br>", "\n").replace("</p>", "\n"))).strip()
	return ("" if text == "(no description provided)" else text), diagnostics


def plain_text(html):
	"""A Comment's HTML as plain text."""
	text = re.sub(r"<(br|/p|/div|/li)\s*/?>", "\n", html or "", flags=re.I)
	return htmllib.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def is_own_issue(raised_by, user, email):
	"""Whether an Issue was raised by this signed-in person (Issues store the email)."""
	return bool(raised_by) and raised_by in (user, email)


def thread_messages(issue, comments, user, email):
	"""A conversation as chat messages: the person's own words first, then each Comment (not by them = from support)."""
	text, _diag = parse_issue_description(issue.get("description"))
	first = {"id": issue.get("name"), "from_staff": False, "text": text or split_issue_subject(issue.get("subject"))[1], "sent_at": str(issue.get("creation") or "")}
	out = [first]
	for c in comments:
		body = plain_text(c.get("content"))
		if body:
			out.append({"id": c.get("name"), "from_staff": not is_own_issue(c.get("comment_email"), user, email), "text": body, "sent_at": str(c.get("creation") or "")})
	return out


STATUS_WORDS = {
	"Open": "Reopened",
	"Replied": "Replied",
	"Resolved": "Marked resolved",
	"Closed": "Closed",
	"On Hold": "Put on hold",
}


def status_events(issue, versions):
	"""The conversation's status history, oldest first: how it was filed, then each change of `status` recorded in
	the Issue's Version documents. `kind` is "created" or "status"; a status event carries `status` and `previous`."""
	out = [{"id": f"{issue.get('name')}-created", "kind": "created", "status": "Open", "previous": None, "label": "Report sent", "sent_at": str(issue.get("creation") or "")}]
	for v in sorted(versions or [], key=lambda r: str(r.get("creation") or "")):
		try:
			data = json.loads(v.get("data") or "{}")
		except ValueError:
			continue
		for change in data.get("changed") or []:
			if isinstance(change, (list, tuple)) and len(change) >= 3 and change[0] == "status" and change[2]:
				out.append({"id": f"{v.get('name')}-status", "kind": "status", "status": str(change[2]), "previous": change[1] or None, "label": status_words(str(change[2]), change[1] or None), "sent_at": str(v.get("creation") or "")})
	return out


def status_words(status, previous=None):
	"""A status change in plain words: "Replied" is "Replied", back to Open after Resolved is "Reopened"."""
	if status == "Open" and previous in (None, "", "Open"):
		return "Opened"
	return STATUS_WORDS.get(status) or f"Status changed to {status}"


def thread_summary(issue, comments, user, email):
	"""One row of the person's thread list: status, newest activity, and when support last answered."""
	staff = [c for c in comments if not is_own_issue(c.get("comment_email"), user, email) and plain_text(c.get("content"))]
	last = (comments[-1] if comments else None)
	preview = plain_text(last.get("content")) if last else (parse_issue_description(issue.get("description"))[0] or "")
	return {
		"id": issue.get("name"),
		"subject": split_issue_subject(issue.get("subject"))[1],
		"status": issue.get("status"),
		"last_activity": str((last or {}).get("creation") or issue.get("creation") or ""),
		"last_staff_at": str(staff[-1].get("creation")) if staff else None,
		"preview": preview[:120],
	}


def support_doctype():
	"""Where reports go: the site's Issue doctype when it has one, else Mobile Report. `upande_mobile_support_doctype`
	in site_config.json ("Issue" or "Mobile Report") overrides."""
	chosen = frappe.conf.get("upande_mobile_support_doctype")
	has_issue = bool(frappe.db.exists("DocType", ISSUE))
	if chosen == REPORT or not has_issue:
		return REPORT
	return ISSUE


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
	if support_doctype() == ISSUE:
		doc = frappe.get_doc(
			{
				"doctype": ISSUE,
				"subject": issue_subject(app_name, subject),
				"raised_by": frappe.db.get_value("User", user, "email") or user,
				"description": issue_description(redact_text(description, MAX_DESCRIPTION) if description else "", diag),
			}
		).insert(ignore_permissions=True)
	else:
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
			file_doc = {
				"doctype": "File",
				"file_name": f"{doc.name}.jpg",
				"attached_to_doctype": doc.doctype,
				"attached_to_name": doc.name,
				"is_private": 1,
				"content": data,
			}
			if doc.doctype == REPORT:
				file_doc["attached_to_field"] = "screenshot"
			saved = frappe.get_doc(file_doc).insert(ignore_permissions=True)
			if doc.doctype == REPORT:
				doc.db_set("screenshot", saved.file_url, update_modified=False)
		except Exception:
			# The report matters more than its picture.
			frappe.log_error(title="Mobile report: screenshot not saved")
	return {"report": doc.name, "doctype": doc.doctype}


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


def _own_issue(name, user):
	"""The Issue, if it is this person's; otherwise a permission error (never reveal other people's)."""
	email = frappe.db.get_value("User", user, "email") or user
	doc = frappe.get_doc(ISSUE, name)
	if not is_own_issue(doc.raised_by, user, email):
		frappe.throw(_("This conversation is not yours."), frappe.PermissionError)
	return doc, email


def _issue_comments(names):
	if not names:
		return {}
	rows = frappe.get_all(
		"Comment",
		filters={"reference_doctype": ISSUE, "reference_name": ["in", list(names)], "comment_type": "Comment"},
		fields=["name", "reference_name", "comment_email", "content", "creation"],
		order_by="creation asc",
		limit_page_length=0,
	)
	out = {}
	for r in rows:
		out.setdefault(r["reference_name"], []).append(r)
	return out


@frappe.whitelist(methods=["GET"])
def threads(app=None):
	"""The person's conversations: their Issues for this app, newest first. `mode` says which chat the site uses."""
	user = _signed_in()
	app_name = _app_row(app)
	if support_doctype() != ISSUE:
		return {"mode": "chat", "threads": []}
	email = frappe.db.get_value("User", user, "email") or user
	issues = frappe.get_all(
		ISSUE,
		filters=[["subject", "like", f"[{app_name}]%"]],
		or_filters=[["raised_by", "=", user], ["raised_by", "=", email]],
		fields=["name", "subject", "status", "creation", "description"],
		order_by="modified desc",
		page_length=30,
	)
	comments = _issue_comments([i["name"] for i in issues])
	return {"mode": "issue", "threads": [thread_summary(i, comments.get(i["name"], []), user, email) for i in issues]}


@frappe.whitelist(methods=["GET"])
def thread(issue):
	"""One conversation, oldest first: `messages` are the words, `events` the status history (created, then each status change)."""
	user = _signed_in()
	doc, email = _own_issue(issue, user)
	comments = _issue_comments([doc.name]).get(doc.name, [])
	versions = frappe.get_all(
		"Version",
		filters={"ref_doctype": ISSUE, "docname": doc.name},
		fields=["name", "creation", "data"],
		order_by="creation asc",
		limit_page_length=0,
	)
	return {
		"thread": {
			"id": doc.name,
			"subject": split_issue_subject(doc.subject)[1],
			"status": doc.status,
			"created": str(doc.creation or ""),
			"has_screenshot": bool(_files_for(ISSUE, [doc.name]).get(doc.name)),
		},
		"messages": thread_messages(doc.as_dict(), comments, user, email),
		"events": status_events(doc.as_dict(), versions),
	}


@frappe.whitelist(methods=["POST"])
def reply(issue, text=None):
	"""The person answers on their Issue. The answer is a Comment on it, so support sees it in the Issue's timeline."""
	user = _signed_in()
	doc, email = _own_issue(issue, user)
	message = clean_chat_text(text)
	if not message:
		frappe.throw(_("Write a message first."), frappe.ValidationError)
	if doc.status == "Closed":
		frappe.throw(_("This conversation is closed. Start a new one."), frappe.ValidationError)
	if _over_limit("chat", user, MESSAGES_PER_HOUR, 3600):
		frappe.throw(_("Too many messages. Try again later."), frappe.TooManyRequestsError)
	comment = doc.add_comment("Comment", message)
	if doc.status in ("Replied", "Resolved"):
		doc.db_set("status", "Open")  # the person wrote back: support has something to answer again
	return {"id": comment.name, "from_staff": False, "text": message, "sent_at": str(comment.creation)}


@frappe.whitelist(methods=["POST"])
def register_push(app=None, token=None, install_id=None, platform=None):
	"""A phone offers its Expo push token so support replies can reach it. Filed under the session user; a token that
	moved to another person (a handed-over phone) follows its new owner."""
	user = _signed_in()
	app_name = _app_row(app)
	clean = push.clean_token(token)
	if not clean:
		frappe.throw(_("That is not an Expo push token."), frappe.ValidationError)
	if _over_limit("push", user, 20, 3600):
		frappe.throw(_("Too many requests. Try again later."), frappe.TooManyRequestsError)
	install_id = _text(install_id)
	values = {
		"app": app_name,
		"user": user,
		"platform": _clip(platform, 20),
		"install_id": install_id if INSTALL_ID_PATTERN.match(install_id) else "",
		"registered_at": now_datetime(),
		"last_error": "",
	}
	existing = frappe.db.get_value(PUSH, {"token": clean}, "name")
	if existing:
		frappe.db.set_value(PUSH, existing, values, update_modified=False)
	else:
		frappe.get_doc(dict(values, doctype=PUSH, token=clean)).insert(ignore_permissions=True)
	# Keep a person's newest few devices per app; older tokens are stale.
	stale = frappe.get_all(PUSH, filters={"app": app_name, "user": user}, pluck="name", order_by="registered_at desc", limit_start=push.MAX_TOKENS_PER_USER_APP, limit_page_length=0)
	for name in stale:
		frappe.delete_doc(PUSH, name, ignore_permissions=True, force=True)
	return {"registered": True}


@frappe.whitelist(methods=["POST"])
def unregister_push(token=None):
	"""Forget this phone's token (notifications turned off, or the person signed out). Only the person's own."""
	user = _signed_in()
	clean = push.clean_token(token)
	if clean:
		frappe.db.delete(PUSH, {"token": clean, "user": user})
	return {"registered": False}


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


def _is_issue(name):
	return not str(name or "").startswith("MREP-")


def _files_for(doctype, names):
	"""Names (of `names`) that have a file attached, and the first image url per name."""
	if not names:
		return {}
	rows = frappe.get_all(
		"File",
		filters={"attached_to_doctype": doctype, "attached_to_name": ["in", list(names)]},
		fields=["attached_to_name", "file_url"],
		order_by="creation asc",
	)
	out = {}
	for r in rows:
		out.setdefault(r["attached_to_name"], r["file_url"])
	return out


def _issue_row(row, files):
	app, subject = split_issue_subject(row.get("subject"))
	text, diag = parse_issue_description(row.get("description"))
	device = diag.get("device") or {}
	return {
		"name": row["name"],
		"doctype": ISSUE,
		"app": app,
		"subject": subject,
		"status": row.get("status"),
		"reporter": row.get("raised_by"),
		"reporter_name": row.get("raised_by"),
		"app_version": (diag.get("app") or {}).get("version") or "",
		"device_model": " ".join(x for x in (device.get("brand"), device.get("model")) if x),
		"os_version": " ".join(x for x in (device.get("platform"), device.get("os_version")) if x),
		"creation": row.get("creation"),
		"screenshot": files.get(row["name"]),
	}


@frappe.whitelist(methods=["GET"])
def reports(app=None, status=None, search=None, start=0, page_length=25):
	_require_manager()
	page = min(max(cint(page_length) or 25, 1), 100)
	start = max(cint(start), 0)
	if support_doctype() == ISSUE:
		apps = [_text(app)] if app else frappe.get_all("Mobile App", pluck="name")
		if not apps:
			return {"rows": [], "total": 0, "doctype": ISSUE, "statuses": list(ISSUE_STATUSES)}
		or_filters = [["subject", "like", f"[{a}]%"] for a in apps]
		filters = []
		if status in ISSUE_STATUSES:
			filters.append(["status", "=", status])
		if search:
			filters.append(["subject", "like", f"%{_text(search)[:60]}%"])
		total = len(frappe.get_all(ISSUE, filters=filters, or_filters=or_filters, pluck="name", limit_page_length=0))
		rows = frappe.get_all(
			ISSUE,
			filters=filters,
			or_filters=or_filters,
			fields=["name", "subject", "status", "raised_by", "creation", "description"],
			order_by="creation desc",
			start=start,
			page_length=page,
		)
		files = _files_for(ISSUE, [r["name"] for r in rows])
		return {"rows": [_issue_row(r, files) for r in rows], "total": total, "doctype": ISSUE, "statuses": list(ISSUE_STATUSES)}

	filters = {}
	if app:
		filters["app"] = _text(app)
	if status in REPORT_STATUSES:
		filters["status"] = status
	or_filters = [["subject", "like", f"%{_text(search)[:60]}%"], ["reporter_name", "like", f"%{_text(search)[:60]}%"]] if search else None
	rows = frappe.get_all(
		REPORT,
		filters=filters,
		or_filters=or_filters,
		fields=["name", "app", "subject", "status", "reporter", "reporter_name", "app_version", "device_model", "os_version", "creation", "screenshot"],
		order_by="creation desc",
		start=start,
		page_length=page,
	)
	return {"rows": rows, "total": frappe.db.count(REPORT, filters=filters), "doctype": REPORT, "statuses": list(REPORT_STATUSES)}


@frappe.whitelist(methods=["GET"])
def report(name):
	_require_manager()
	if not _is_issue(name):
		doc = frappe.get_doc(REPORT, name)
		out = doc.as_dict()
		for key in ("diagnostics", "last_crash"):
			try:
				out[key] = json.loads(doc.get(key)) if doc.get(key) else None
			except ValueError:
				out[key] = None
		out.update({"doctype": REPORT, "statuses": list(REPORT_STATUSES), "desk_url": f"/app/mobile-report/{name}"})
		return out
	doc = frappe.get_doc(ISSUE, name)
	app, subject = split_issue_subject(doc.subject)
	text, diag = parse_issue_description(doc.description)
	device = diag.get("device") or {}
	files = _files_for(ISSUE, [name])
	return {
		"name": name,
		"doctype": ISSUE,
		"app": app,
		"subject": subject,
		"status": doc.status,
		"statuses": list(ISSUE_STATUSES),
		"reporter": doc.raised_by,
		"reporter_name": doc.raised_by,
		"creation": doc.creation,
		"description": text,
		"app_version": (diag.get("app") or {}).get("version") or "",
		"runtime_version": (diag.get("app") or {}).get("runtime_version") or "",
		"device_model": " ".join(x for x in (device.get("brand"), device.get("model")) if x),
		"os_version": " ".join(x for x in (device.get("platform"), device.get("os_version")) if x),
		"screenshot": files.get(name),
		"diagnostics": diag or None,
		"last_crash": diag.get("last_crash"),
		"desk_url": f"/app/issue/{name}",
	}


@frappe.whitelist(methods=["POST"])
def set_report_status(name, status):
	_require_manager()
	if _is_issue(name):
		if status not in ISSUE_STATUSES:
			frappe.throw(_("Unknown status."), frappe.ValidationError)
		# Through the document, so the Issue's own rules (resolution date, SLA) run.
		doc = frappe.get_doc(ISSUE, name)
		doc.status = status
		doc.save(ignore_permissions=True)
		return {"name": name, "status": status}
	if status not in REPORT_STATUSES:
		frappe.throw(_("Unknown status."), frappe.ValidationError)
	frappe.db.set_value(REPORT, name, "status", status)
	return {"name": name, "status": status}


@frappe.whitelist(methods=["GET"])
def chat_threads(app=None, start=0, page_length=25):
	_require_manager()
	if support_doctype() == ISSUE:
		# Conversations are Issues: support answers as a Comment on the Issue in Desk, not here.
		return {"mode": "issue", "rows": [], "total": 0, "unread": 0}
	filters = {"app": _text(app)} if app else {}
	rows = frappe.get_all(
		THREAD,
		filters=filters,
		fields=["name", "app", "user", "user_full_name", "last_message_at", "last_preview", "unread_staff"],
		order_by="last_message_at desc",
		start=max(cint(start), 0),
		page_length=min(max(cint(page_length) or 25, 1), 100),
	)
	return {"mode": "chat", "rows": rows, "total": frappe.db.count(THREAD, filters=filters), "unread": cint(frappe.db.sql("select coalesce(sum(unread_staff),0) from `tabMobile Chat Thread`")[0][0])}


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
	row = message_row(_add_message(thread, frappe.session.user, message, True).as_dict())
	head = frappe.db.get_value(THREAD, thread, ["app", "user"], as_dict=True)
	if head:
		push.notify_user(head.app, head.user, "Reply from support", message, {"kind": "support-reply", "thread": thread})
	return row
