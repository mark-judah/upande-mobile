# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# The person's notification inbox (rows of Mobile Notification written by `push.notify_user`). Every endpoint works on
# the session user's own rows only; nobody can read or mark another person's notifications here.

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import cint, get_system_timezone, now_datetime

from upande_mobile.push import MAX_FIELD, NOTIFICATION, _rowcount

DEFAULT_LIMIT = 50
MAX_LIMIT = 100
MAX_NAMES = 500
MAX_SINCE_LENGTH = 64
# `since` is moved back this much: a row whose transaction committed a little after its `creation` is still picked
# up by the next sync. The app dedupes items by `name`, so the overlap is harmless.
SINCE_OVERLAP = timedelta(minutes=2)
TRUE_VALUES = (True, 1, "1", "true", "True", "TRUE", "yes", "on")


def _signed_in():
	user = frappe.session.user
	if not user or user == "Guest":
		frappe.throw(_("Sign in first."), frappe.AuthenticationError)
	return user


def _app(app):
	value = app.strip() if isinstance(app, str) else ""
	if not value or len(value) > MAX_FIELD:
		frappe.throw(_("Unknown app."), frappe.ValidationError)
	return value


def _limit(limit):
	try:
		value = int(limit) if limit not in (None, "") else DEFAULT_LIMIT
	except (TypeError, ValueError):
		value = DEFAULT_LIMIT
	if value <= 0:
		value = DEFAULT_LIMIT
	return min(value, MAX_LIMIT)


def parse_since(value):
	"""An ISO-8601 timestamp (with or without offset) as a naive datetime in the site's timezone, or None.
	Anything else is refused with a ValidationError."""
	if value is None or value == "":
		return None
	if not isinstance(value, str) or len(value) > MAX_SINCE_LENGTH:
		frappe.throw(_("`since` must be an ISO timestamp."), frappe.ValidationError)
	try:
		parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00").replace("z", "+00:00"))
	except ValueError:
		frappe.throw(_("`since` must be an ISO timestamp."), frappe.ValidationError)
	if parsed.tzinfo is not None:
		parsed = parsed.astimezone(ZoneInfo(get_system_timezone())).replace(tzinfo=None)
	return parsed


def iso(value):
	"""A naive site-timezone datetime as ISO-8601 with its offset, so the app never guesses the zone."""
	if not value:
		return None
	if isinstance(value, str):
		value = datetime.fromisoformat(value)
	return value.replace(tzinfo=ZoneInfo(get_system_timezone())).isoformat()


def _data(raw):
	try:
		value = json.loads(raw) if raw else {}
	except (TypeError, ValueError):
		return {}
	return value if isinstance(value, dict) else {}


def _unread(user, app):
	return cint(frappe.db.count(NOTIFICATION, {"user": user, "app": app, "read_at": ["is", "not set"]}))


def _names(names):
	"""`names` as a clean list of strings: a list, a JSON list, or one name."""
	if names in (None, ""):
		return []
	if isinstance(names, str):
		text = names.strip()
		if text.startswith("["):
			try:
				names = json.loads(text)
			except ValueError:
				frappe.throw(_("`names` must be a list of notification names."), frappe.ValidationError)
		else:
			names = [text]
	if not isinstance(names, list | tuple):
		frappe.throw(_("`names` must be a list of notification names."), frappe.ValidationError)
	clean = []
	for name in names:
		if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_FIELD:
			frappe.throw(_("`names` must be a list of notification names."), frappe.ValidationError)
		clean.append(name.strip())
	if len(clean) > MAX_NAMES:
		frappe.throw(_("Too many names at once."), frappe.ValidationError)
	return sorted(set(clean))


@frappe.whitelist(methods=["GET", "POST"])
def list_notifications(app=None, since=None, limit=DEFAULT_LIMIT):
	"""The session user's notifications for `app`, newest first, optionally only those created after `since`."""
	user = _signed_in()
	app = _app(app)
	since_at = parse_since(since)
	filters = {"user": user, "app": app}
	if since_at:
		filters["creation"] = (">", since_at - SINCE_OVERLAP)
	rows = frappe.get_all(
		NOTIFICATION,
		filters=filters,
		fields=["name", "title", "body", "route", "kind", "data", "creation", "read_at"],
		order_by="creation desc",
		limit_page_length=_limit(limit),
	)
	return {
		"items": [
			{
				"name": r.name,
				"title": r.title or "",
				"body": r.body or "",
				"route": r.route or None,
				"kind": r.kind or "event",
				"data": _data(r.data),
				"created": iso(r.creation),
				"read": bool(r.read_at),
			}
			for r in rows
		],
		"unread": _unread(user, app),
		"server_time": iso(now_datetime()),
	}


@frappe.whitelist(methods=["GET", "POST"])
def unread_count(app=None):
	"""How many of the session user's notifications for `app` are unread."""
	user = _signed_in()
	return {"unread": _unread(user, _app(app))}


@frappe.whitelist(methods=["POST"])
def mark_read(app=None, names=None, all=False):  # `all` is the contract's parameter name
	"""Mark the session user's notifications read: the given `names`, or every unread one with `all`. Names that are
	not the caller's (or not in `app`) are silently left alone."""
	user = _signed_in()
	app = _app(app)
	mark_all = all in TRUE_VALUES
	wanted = [] if mark_all else _names(names)
	if not mark_all and not wanted:
		return {"updated": 0}
	sql = f"update `tab{NOTIFICATION}` set read_at=%(now)s where user=%(user)s and app=%(app)s and read_at is null"
	values = {"now": now_datetime(), "user": user, "app": app}
	if not mark_all:
		sql += " and name in %(names)s"
		values["names"] = tuple(wanted)
	frappe.db.sql(sql, values)
	return {"updated": _rowcount()}
