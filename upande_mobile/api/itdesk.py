# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# The IT dashboard's data (/mobile-monitor). Where `monitor` and `devices` answer developer questions (stack
# traces, install ids), this module answers an IT manager's: who is out of date and for how long, which
# devices need a hand, which problems hurt people, who is waiting for an answer.
#
# Open to System Managers and to the "IT Support" role. Developer details (stack traces, event ids, runtime
# and engine versions) stay behind System Manager: `is_developer` tells the page whether to show them.
#
# Pure helpers (people, buckets, wait time, wording) are separate from the database code so they are unit tested.

import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_datetime, now_datetime

from upande_mobile import push
from upande_mobile.api import devices as dev
from upande_mobile.api import monitor as mon
from upande_mobile.api import support as sup

IT_ROLE = "IT Support"
REMINDER = "Mobile Reminder"
INSTALL = "Mobile App Install"
MOBILE_APP = "Mobile App"
DEFAULT_GRACE_DAYS = 3
REMIND_COOLDOWN_HOURS = 20
AUTO_MAX = 3
AUTO_GAP_DAYS = 3
MAX_ROWS = 500
MAX_REMIND = 200
DEVICE_STATES = ("Active", "Lost", "Retired")
PROBLEM_STATUSES = ("Open", "Resolved", "Ignored")
BEHIND = ("behind_ota", "needs_apk")

PROBLEM_WORDS = {
	"crash": "The app closed unexpectedly",
	"error": "Something went wrong inside the app",
	"api": "The app could not reach the server",
	"log": "A warning the app recorded",
}
# How much a kind hurts the person using the app.
IMPACT = {"crash": "blocking", "error": "slowing", "api": "slowing", "log": "minor"}


# --------------------------------------------------------------------------- #
# access
# --------------------------------------------------------------------------- #


def can_use(roles=None):
	roles = roles if roles is not None else frappe.get_roles()
	return "System Manager" in roles or IT_ROLE in roles


def is_developer(roles=None):
	roles = roles if roles is not None else frappe.get_roles()
	return "System Manager" in roles


def _require_it():
	if not can_use():
		frappe.throw(_("The IT dashboard is visible to IT Support and System Managers only."), frappe.PermissionError)


def ensure_role():
	"""Create the IT Support role (idempotent, run after migrate)."""
	if not frappe.db.exists("Role", IT_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": IT_ROLE, "desk_access": 1}).insert(ignore_permissions=True)


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #


def days_between(earlier, later):
	"""Whole days from `earlier` to `later`; None when either is missing."""
	if not earlier or not later:
		return None
	delta = get_datetime(later) - get_datetime(earlier)
	return max(delta.days, 0)


def person_bucket(status, days_behind, grace_days):
	"""Where a person sits on the chase list.

	forgot      reopening the app would be enough (behind_ota), past the grace period
	cant_update a new install is needed (needs_apk), past the grace period
	grace       behind, but the release is still inside the grace period
	away        not seen for a long time (dormant)
	current     up to date (or ahead)
	unknown     nothing to compare against
	"""
	if status == "dormant":
		return "away"
	if status in ("current", "ahead"):
		return "current"
	if status not in BEHIND:
		return "unknown"
	if days_behind is not None and days_behind < cint(grace_days):
		return "grace"
	return "forgot" if status == "behind_ota" else "cant_update"


def wait_state(status, last_by_reporter_at, last_staff_at, created):
	"""(waiting, since) for a support conversation: waiting means the person wrote last and it is not closed."""
	if status in ("Resolved", "Closed"):
		return False, None
	if last_staff_at and (not last_by_reporter_at or get_datetime(last_staff_at) >= get_datetime(last_by_reporter_at)):
		return False, None
	return True, last_by_reporter_at or created


def reminder_text(app, bucket, target):
	"""The push a person gets: what to do, in one sentence."""
	if bucket == "cant_update":
		return f"New version of {app}", f"{app} {target or ''} needs a fresh install. Please ask IT for the download link.".replace("  ", " ")
	return f"Update {app}", f"Version {target or 'the latest'} is ready. Close and reopen {app} to get it."


def reminder_allowed(last_at, now_dt, cooldown_hours=REMIND_COOLDOWN_HOURS):
	return not last_at or (get_datetime(now_dt) - get_datetime(last_at)).total_seconds() >= cooldown_hours * 3600


def problem_view(row, developer):
	"""An issue row in the words an IT manager reads; developer-only fields only for developers."""
	kind = row.get("kind") or "error"
	out = {
		"name": row["name"],
		"app": row.get("app"),
		"what": PROBLEM_WORDS.get(kind, PROBLEM_WORDS["error"]),
		"impact": IMPACT.get(kind, "slowing"),
		"people": cint(row.get("users_affected")),
		"devices": cint(row.get("devices_affected")),
		"times": cint(row.get("event_count")),
		"last_seen": str(row.get("last_seen") or ""),
		"first_seen": str(row.get("first_seen") or ""),
		"status": row.get("status"),
		"version": row.get("last_version") or "",
		"fixed_in": row.get("resolved_in_version") or "",
	}
	if developer:
		out["developer"] = {"title": row.get("title") or "", "culprit": row.get("culprit") or "", "kind": kind, "level": row.get("level") or "", "last_event": row.get("last_event") or ""}
	return out


# --------------------------------------------------------------------------- #
# people (the chase list)
# --------------------------------------------------------------------------- #


def _app_rows():
	return {
		r.name: r
		for r in frappe.get_all(
			MOBILE_APP,
			filters={"enabled": 1},
			fields=["name", "latest_version", "latest_runtime", "latest_apk_version", "published_at", "dormant_days", "grace_days", "nudge_after_days"],
		)
	}


def _grace(app_row):
	value = app_row.get("grace_days")
	return DEFAULT_GRACE_DAYS if value is None else cint(value)


def _teams(users):
	"""{user: department} from Employee when the site has it; empty otherwise."""
	if not users or not frappe.db.table_exists("Employee"):
		return {}
	try:
		rows = frappe.get_all("Employee", filters={"user_id": ["in", list(users)]}, fields=["user_id", "department"], limit_page_length=0)
	except Exception:
		return {}
	return {r.user_id: r.department for r in rows if r.department}


def _reminder_stats(since=None):
	"""{(user, app): {count, last_at}} for reminders sent (for the current release when `since` is given)."""
	filters = {"sent_at": [">=", since]} if since else {}
	stats = {}
	for r in frappe.get_all(REMINDER, filters=filters, fields=["user", "app", "sent_at"], order_by="sent_at asc", limit_page_length=0):
		s = stats.setdefault((r.user, r.app), {"count": 0, "last_at": None})
		s["count"] += 1
		s["last_at"] = r.sent_at
	return stats


def _device_states():
	return {r.install_id: (r.device_state or "Active") for r in frappe.get_all(INSTALL, fields=["install_id", "device_state"], limit_page_length=0)}


def people(app=None):
	"""One row per person and app, from the account's own last report on its most recent device."""
	apps = _app_rows()
	rows = dev.install_rows(app=app or None, include_dormant=True)
	states = _device_states()
	now_dt = now_datetime()
	best = {}
	for r in rows:
		if states.get(r.install_id, "Active") != "Active" or r.app not in apps:
			continue
		app_row = apps[r.app]
		accounts = r.users or ([{"user": r.user, "app_version": r.app_version, "runtime_version": r.runtime_version, "last_seen": r.last_seen, "full_name": r.full_name}] if r.user else [])
		for a in accounts:
			if not a.get("user"):
				continue
			status, update_to = dev.install_status(
				a.get("app_version") or r.app_version,
				a.get("runtime_version") or r.runtime_version,
				a.get("last_seen") or r.last_seen,
				app_row,
				cint(app_row.dormant_days),
				now_dt,
			)
			key = (a["user"], r.app)
			seen = a.get("last_seen") or r.last_seen
			if key in best and best[key]["_seen"] and seen and get_datetime(best[key]["_seen"]) >= get_datetime(seen):
				continue
			days_behind = days_between(app_row.published_at, now_dt)
			best[key] = {
				"_seen": seen,
				"user": a["user"],
				"full_name": a.get("full_name") or r.full_name or a["user"],
				"app": r.app,
				"installed": a.get("app_version") or r.app_version or "",
				"target": app_row.latest_version or "",
				"status": status,
				"update_to": update_to,
				"bucket": person_bucket(status, days_behind, _grace(app_row)),
				"released_days_ago": days_behind,
				"last_seen": str(seen or ""),
				"days_unseen": days_between(seen, now_dt),
				"device": " ".join(x for x in (r.device_brand, r.device_model) if x),
			}
	out = list(best.values())
	teams = _teams({p["user"] for p in out})
	stats = _reminder_stats()
	for p in out:
		p.pop("_seen", None)
		p["team"] = teams.get(p["user"], "")
		s = stats.get((p["user"], p["app"]), {"count": 0, "last_at": None})
		p["reminded"] = s["count"]
		p["last_reminded"] = str(s["last_at"] or "")
	return out


@frappe.whitelist(methods=["GET"])
def updates(app=None, bucket=None, search=None):
	"""The chase list: people behind, longest first. `counts` covers every bucket whatever the filter."""
	_require_it()
	everyone = people(mon._text(app) or None)
	counts = {}
	for p in everyone:
		counts[p["bucket"]] = counts.get(p["bucket"], 0) + 1
	rows = everyone
	if bucket:
		rows = [p for p in rows if p["bucket"] == bucket]
	else:
		rows = [p for p in rows if p["bucket"] in ("forgot", "cant_update", "grace")]
	term = mon._text(search).lower()[:60]
	if term:
		rows = [p for p in rows if term in (p["full_name"] + " " + p["user"] + " " + p["team"]).lower()]
	rows.sort(key=lambda p: (-(p["released_days_ago"] or 0), p["full_name"].lower()))
	return {"rows": rows[:MAX_ROWS], "total": len(rows), "counts": counts}


def _send_reminder(person, sent_by, kind, now_dt):
	text = reminder_text(person["app"], person["bucket"], person["target"])
	reached = push.notify_user(person["app"], person["user"], text[0], text[1], {"kind": "update-reminder"})
	frappe.get_doc(
		{
			"doctype": REMINDER,
			"user": person["user"],
			"app": person["app"],
			"kind": kind,
			"sent_by": sent_by,
			"sent_at": now_dt,
			"delivered": reached,
			"installed_version": person["installed"],
			"target_version": person["target"],
			"message": text[1],
		}
	).insert(ignore_permissions=True)
	return reached


@frappe.whitelist(methods=["POST"])
def remind(people_list=None, force=0):
	"""Push a reminder to the chosen people ([{"user", "app"}]). Anyone reminded in the last day is skipped unless
	`force`. Returns what happened per person so the page can say "2 reminded, 1 has no notifications"."""
	_require_it()
	try:
		wanted = json.loads(people_list) if isinstance(people_list, str) else (people_list or [])
	except ValueError:
		wanted = []
	wanted = [w for w in wanted if isinstance(w, dict)][:MAX_REMIND]
	if not wanted:
		frappe.throw(_("Choose at least one person."), frappe.ValidationError)
	current = {(p["user"], p["app"]): p for p in people()}
	now_dt = now_datetime()
	results = []
	for w in wanted:
		key = (mon._text(w.get("user")), mon._text(w.get("app")))
		person = current.get(key)
		item = {"user": key[0], "app": key[1]}
		if not person or person["bucket"] not in ("forgot", "cant_update", "grace"):
			item["result"] = "not_behind"
		elif not cint(force) and not reminder_allowed(person["last_reminded"] or None, now_dt):
			item["result"] = "recent"
		else:
			item["result"] = "sent" if _send_reminder(person, frappe.session.user, "manual", now_dt) else "no_device"
		results.append(item)
	return {"results": results, "sent": sum(1 for r in results if r["result"] == "sent")}


@frappe.whitelist(methods=["GET"])
def reminders(user=None, app=None, limit=100):
	_require_it()
	filters = {}
	if user:
		filters["user"] = mon._text(user)
	if app:
		filters["app"] = mon._text(app)
	rows = frappe.get_all(
		REMINDER,
		filters=filters,
		fields=["user", "app", "kind", "sent_by", "sent_at", "delivered", "installed_version", "target_version"],
		order_by="sent_at desc",
		limit_page_length=min(max(cint(limit), 1), 300),
	)
	for r in rows:
		r["sent_at"] = str(r["sent_at"] or "")
	return {"rows": rows}


def auto_nudge():
	"""Daily: remind people still behind `nudge_after_days` after a release, up to AUTO_MAX times, AUTO_GAP_DAYS apart."""
	apps = _app_rows()
	now_dt = now_datetime()
	sent = 0
	for p in people():
		app_row = apps.get(p["app"])
		if not app_row or not cint(app_row.nudge_after_days) or p["bucket"] not in ("forgot", "cant_update"):
			continue
		if (p["released_days_ago"] or 0) < cint(app_row.nudge_after_days) or p["reminded"] >= AUTO_MAX:
			continue
		if p["last_reminded"] and not reminder_allowed(p["last_reminded"], now_dt, AUTO_GAP_DAYS * 24):
			continue
		_send_reminder(p, None, "auto", now_dt)
		sent += 1
	return sent


# --------------------------------------------------------------------------- #
# today and apps
# --------------------------------------------------------------------------- #


@frappe.whitelist(methods=["GET"])
def today():
	"""What needs attention now, as cards. Each card: key, count, detail, route, severity (high/medium/low)."""
	_require_it()
	everyone = people()
	forgot = [p for p in everyone if p["bucket"] == "forgot"]
	cant = [p for p in everyone if p["bucket"] == "cant_update"]
	cards = []
	if forgot or cant:
		oldest = max([p["released_days_ago"] or 0 for p in forgot + cant] or [0])
		cards.append({"key": "updates", "count": len(forgot) + len(cant), "route": "updates", "severity": "high" if oldest >= 7 else "medium", "detail": {"forgot": len(forgot), "cant_update": len(cant), "oldest_days": oldest}})
	convo = [c for c in _inbox_rows(None, None) if c["waiting"]]
	if convo:
		oldest = max(convo, key=lambda c: c["waiting_hours"])
		cards.append({"key": "support", "count": len(convo), "route": "support", "severity": "high" if oldest["waiting_hours"] >= 24 else "medium", "detail": {"oldest_hours": oldest["waiting_hours"], "oldest_user": oldest["person"]}})
	problems = [p for p in _problem_rows(None, "Open") if p["people"] > 0 and days_between(p["last_seen"], now_datetime()) is not None and days_between(p["last_seen"], now_datetime()) <= 2]
	if problems:
		hurt = sum(p["people"] for p in problems)
		cards.append({"key": "problems", "count": len(problems), "route": "problems", "severity": "high" if any(p["impact"] == "blocking" for p in problems) else "medium", "detail": {"people": hurt, "blocking": sum(1 for p in problems if p["impact"] == "blocking")}})
	health = mon._health_counts(None)
	device_issues = {k: health.get(k, 0) for k in ("low_battery", "low_storage", "offline", "outdated_os")}
	if any(device_issues.values()):
		cards.append({"key": "devices", "count": sum(device_issues.values()), "route": "devices", "severity": "low", "detail": device_issues})
	away = [p for p in everyone if p["bucket"] == "away"]
	if away:
		cards.append({"key": "away", "count": len(away), "route": "updates?bucket=away", "severity": "low", "detail": {}})
	return {
		"cards": cards,
		"people": len(everyone),
		"devices": health.get("devices", 0),
		"stats": _overview_stats(everyone, _inbox_rows(None, None), _problem_rows(None, "Open")),
		"developer": is_developer(),
		"server_time": str(now_datetime()),
	}


def _overview_stats(everyone, convo, open_problems):
	"""Headline numbers for the Overview: apps, people, adoption, devices, support and problems."""
	now_dt = now_datetime()
	week = add_days(now_dt, -7)
	app_rows = _app_rows()
	devices = frappe.get_all(INSTALL, fields=["platform", "device_state", "first_seen", "last_seen"], limit_page_length=0)
	active = [d for d in devices if (d.device_state or "Active") == "Active"]
	platforms = {}
	for d in active:
		name = (d.platform or "unknown").lower()
		platforms[name] = platforms.get(name, 0) + 1
	current = sum(1 for p in everyone if p["bucket"] == "current")
	reachable = sum(1 for p in everyone if p["bucket"] != "unknown")
	open_convo = [c for c in convo if c["status"] not in ("Resolved", "Closed")]
	return {
		"apps_total": frappe.db.count(MOBILE_APP),
		"apps_live": len(app_rows),
		"apps_with_apk": frappe.db.count(MOBILE_APP, {"enabled": 1, "latest_apk_url": ["is", "set"]}),
		"people": len(everyone),
		"people_current": current,
		"adoption": round(current * 100 / reachable) if reachable else None,
		"people_behind": sum(1 for p in everyone if p["bucket"] in ("forgot", "cant_update", "grace")),
		"people_away": sum(1 for p in everyone if p["bucket"] == "away"),
		"devices": len(devices),
		"devices_active": len(active),
		"devices_active_7d": sum(1 for d in active if d.last_seen and get_datetime(d.last_seen) >= week),
		"devices_new_7d": sum(1 for d in devices if d.first_seen and get_datetime(d.first_seen) >= week),
		"devices_lost": sum(1 for d in devices if d.device_state == "Lost"),
		"devices_retired": sum(1 for d in devices if d.device_state == "Retired"),
		"platforms": platforms,
		"tickets_open": len(open_convo),
		"tickets_waiting": sum(1 for c in open_convo if c["waiting"]),
		"tickets_total": len(convo),
		"problems_open": len(open_problems),
		"problems_blocking": sum(1 for p in open_problems if p["impact"] == "blocking"),
		"people_hurt": sum(p["people"] for p in open_problems),
		"reminders_7d": frappe.db.count(REMINDER, {"sent_at": [">=", week]}),
	}


@frappe.whitelist(methods=["GET"])
def apps():
	"""One row per app: adoption, problems, open conversations."""
	_require_it()
	app_rows = _app_rows()
	everyone = people()
	problems = _problem_rows(None, "Open")
	convo = _inbox_rows(None, None)
	out = []
	for name, a in sorted(app_rows.items()):
		mine = [p for p in everyone if p["app"] == name]
		current = sum(1 for p in mine if p["bucket"] == "current")
		behind = sum(1 for p in mine if p["bucket"] in ("forgot", "cant_update", "grace"))
		out.append(
			{
				"app": name,
				"version": a.latest_version or "",
				"released_days_ago": days_between(a.published_at, now_datetime()),
				"people": len(mine),
				"current": current,
				"behind": behind,
				"away": sum(1 for p in mine if p["bucket"] == "away"),
				"adoption": round(current * 100 / len(mine)) if mine else None,
				"problems": sum(1 for p in problems if p["app"] == name),
				"people_hurt": sum(p["people"] for p in problems if p["app"] == name),
				"open_tickets": sum(1 for c in convo if c["app"] == name and c["status"] not in ("Resolved", "Closed")),
				"grace_days": _grace(a),
				"nudge_after_days": cint(a.nudge_after_days),
			}
		)
	return {"rows": out}


@frappe.whitelist(methods=["POST"])
def set_app_policy(app, grace_days=None, nudge_after_days=None):
	"""Per-app reminder settings. Grace 0..60 days; auto-remind 0 (off) or 1..60 days."""
	_require_it()
	if not frappe.db.exists(MOBILE_APP, app):
		frappe.throw(_("Unknown app."), frappe.DoesNotExistError)
	values = {}
	if grace_days is not None:
		values["grace_days"] = max(0, min(cint(grace_days), 60))
	if nudge_after_days is not None:
		values["nudge_after_days"] = max(0, min(cint(nudge_after_days), 60))
	if values:
		frappe.db.set_value(MOBILE_APP, app, values)
	return values


# --------------------------------------------------------------------------- #
# devices
# --------------------------------------------------------------------------- #


@frappe.whitelist(methods=["GET"])
def device_list(app=None, state=None, health=None, search=None):
	"""Inventory: one row per device with its owner, health flags, state and notes."""
	_require_it()
	min_os = mon._min_os()
	developer = is_developer()
	rows = dev.install_rows(app=mon._text(app) or None, include_dormant=True)
	meta = {
		r.install_id: r
		for r in frappe.get_all(
			INSTALL,
			fields=["install_id", "device_state", "it_notes", "battery_level", "storage_free_bytes", "storage_total_bytes", "connectivity", "os_major", "snapshot_at", "network_type", "platform"],
			limit_page_length=0,
		)
	}
	out = []
	term = mon._text(search).lower()[:60]
	for r in rows:
		m = meta.get(r.install_id)
		if not m:
			continue
		device_state = m.device_state or "Active"
		if state and device_state != state:
			continue
		health_row = {**m, "status": r.status}
		flags = mon.health_flags(health_row, min_os)
		if health and health not in flags:
			continue
		text = " ".join(str(x or "") for x in (r.user, r.full_name, r.device_brand, r.device_model, r.device_name, r.serial_number, r.mac_address, r.local_ip))
		if term and term not in text.lower():
			continue
		item = {
			"id": r.install_id,
			"app": r.app,
			"owner": r.user or "",
			"owner_name": r.full_name or r.user or "",
			"device": " ".join(x for x in (r.device_brand, r.device_model) if x) or "Unknown device",
			"platform": r.platform,
			"os_version": r.os_version,
			"app_version": r.app_version,
			"status": r.status,
			"flags": flags,
			"state": device_state,
			"notes": m.it_notes or "",
			"last_seen": str(r.last_seen or ""),
			"battery": round(float(m.battery_level or 0) * 100) if m.snapshot_at and m.battery_level else None,
			"storage_free_gb": round(cint(m.storage_free_bytes) / 1024**3, 1) if cint(m.storage_total_bytes) else None,
			"network": m.network_type or "",
			"serial_number": r.serial_number or "",
			"mac_address": r.mac_address or "",
			"local_ip": r.local_ip or "",
			"build_number": r.build_number or "",
			"ip_seen": r.ip_address or "",
		}
		if developer:
			item["developer"] = {"install_id": r.install_id, "runtime": r.runtime_version, "first_seen": str(r.first_seen or ""), "launches": r.launches}
		out.append(item)
	out.sort(key=lambda d: (d["state"] != "Active", -len(d["flags"])))
	return {"rows": out[:MAX_ROWS], "total": len(out), "summary": mon._health_counts(mon._text(app) or None), "developer": developer}


@frappe.whitelist(methods=["POST"])
def set_device(id, state=None, notes=None):
	"""Mark a device Active, Lost or Retired and keep a note about it (who has it, where it went)."""
	_require_it()
	name = frappe.db.get_value(INSTALL, {"install_id": mon._text(id)}, "name")
	if not name:
		frappe.throw(_("Unknown device."), frappe.DoesNotExistError)
	values = {}
	if state is not None:
		if state not in DEVICE_STATES:
			frappe.throw(_("Unknown device state."), frappe.ValidationError)
		values["device_state"] = state
	if notes is not None:
		values["it_notes"] = str(notes).strip()[:1000]
	if values:
		frappe.db.set_value(INSTALL, name, values, update_modified=False)
	return values


# --------------------------------------------------------------------------- #
# problems
# --------------------------------------------------------------------------- #


def _problem_rows(app, status):
	filters = {}
	if app:
		filters["app"] = app
	if status:
		filters["status"] = status
	rows = frappe.get_all(
		mon.ISSUE,
		filters=filters,
		fields=["name", "title", "culprit", "app", "kind", "level", "status", "event_count", "users_affected", "devices_affected", "first_seen", "last_seen", "last_version", "resolved_in_version", "last_event"],
		order_by="users_affected desc, last_seen desc",
		limit_page_length=MAX_ROWS,
	)
	developer = is_developer()
	return [problem_view(r, developer) for r in rows]


@frappe.whitelist(methods=["GET"])
def problems(app=None, status="Open"):
	"""Problems grouped by how much they hurt: blocking first, then slowing people down, then minor."""
	_require_it()
	rows = _problem_rows(mon._text(app) or None, status if status in PROBLEM_STATUSES else None)
	groups = {"blocking": [], "slowing": [], "minor": []}
	for r in rows:
		groups.setdefault(r["impact"], []).append(r)
	return {"groups": groups, "total": len(rows), "developer": is_developer()}


@frappe.whitelist(methods=["GET"])
def problem_people(name):
	"""Who hit a problem (names, not ids), most recent first."""
	_require_it()
	rows = frappe.db.sql(
		f"SELECT e.user, MAX(e.ts) AS last, COUNT(*) AS n, u.full_name FROM `tab{mon.EVENT}` e LEFT JOIN `tabUser` u ON u.name = e.user "  # noqa: S608
		"WHERE e.issue = %s AND e.user IS NOT NULL AND e.user != '' GROUP BY e.user, u.full_name ORDER BY last DESC LIMIT 25",
		(name,),
		as_dict=True,
	)
	return {"rows": [{"user": r.user, "name": r.full_name or r.user, "times": cint(r.n), "last": str(r["last"])} for r in rows]}


@frappe.whitelist(methods=["POST"])
def set_problem_status(name, status):
	"""Mark a problem Resolved, Ignored (known and accepted) or Open again."""
	_require_it()
	if status not in PROBLEM_STATUSES:
		frappe.throw(_("Unknown status."), frappe.ValidationError)
	if not frappe.db.exists(mon.ISSUE, name):
		frappe.throw(_("Unknown problem."), frappe.DoesNotExistError)
	frappe.db.set_value(mon.ISSUE, name, "status", status)
	return {"name": name, "status": status}


# --------------------------------------------------------------------------- #
# support inbox and chat
# --------------------------------------------------------------------------- #

CHAT_PREFIX = "chat:"


def _display(user_ids):
	if not user_ids:
		return {}
	return {u.name: u.full_name for u in frappe.get_all("User", filters={"name": ["in", list(user_ids)]}, fields=["name", "full_name"])}


def _inbox_rows(app, status):
	"""Conversations as one list whichever doctype the site uses; awaiting-answer first, longest wait on top."""
	now_dt = now_datetime()
	rows = []
	if sup.support_doctype() == sup.ISSUE:
		apps = [app] if app else frappe.get_all(MOBILE_APP, pluck="name")
		if not apps:
			return []
		filters = [["status", "=", status]] if status else []
		issues = frappe.get_all(
			sup.ISSUE,
			filters=filters,
			or_filters=[["subject", "like", f"[{a}]%"] for a in apps],
			fields=["name", "subject", "status", "raised_by", "creation", "description"],
			order_by="creation desc",
			limit_page_length=MAX_ROWS,
		)
		comments = sup._issue_comments([i.name for i in issues])
		names = _display({i.raised_by for i in issues if i.raised_by})
		for i in issues:
			app_name, subject = sup.split_issue_subject(i.subject)
			cs = [c for c in comments.get(i.name, []) if sup.plain_text(c.get("content"))]
			mine = [c for c in cs if sup.is_own_issue(c.get("comment_email"), i.raised_by, i.raised_by)]
			theirs = [c for c in cs if c not in mine]
			waiting, since = wait_state(i.status, mine[-1].creation if mine else None, theirs[-1].creation if theirs else None, i.creation)
			last = cs[-1] if cs else None
			rows.append(
				{
					"id": i.name,
					"app": app_name or "",
					"subject": subject,
					"status": i.status,
					"person": names.get(i.raised_by) or i.raised_by or "",
					"user": i.raised_by or "",
					"email": i.raised_by or "",
					"waiting": waiting,
					"waiting_hours": int((now_dt - get_datetime(since)).total_seconds() // 3600) if waiting and since else 0,
					"last_activity": str((last or {}).get("creation") or i.creation),
					"preview": (sup.plain_text(last.get("content")) if last else (sup.parse_issue_description(i.description)[0] or ""))[:120],
					"messages": len(cs) + 1,
				}
			)
	else:
		filters = {"app": app} if app else {}
		for t in frappe.get_all(sup.THREAD, filters=filters, fields=["name", "app", "user", "user_full_name", "last_message_at", "last_preview", "unread_staff"], order_by="last_message_at desc", limit_page_length=MAX_ROWS):
			waiting = cint(t.unread_staff) > 0
			rows.append(
				{
					"id": CHAT_PREFIX + t.name,
					"app": t.app,
					"subject": "Chat",
					"status": "Open",
					"person": t.user_full_name or t.user,
					"user": t.user,
					"email": frappe.db.get_value("User", t.user, "email") or t.user,
					"waiting": waiting,
					"waiting_hours": int((now_dt - get_datetime(t.last_message_at)).total_seconds() // 3600) if waiting and t.last_message_at else 0,
					"last_activity": str(t.last_message_at or ""),
					"preview": t.last_preview or "",
					"messages": 0,
				}
			)
	rows.sort(key=lambda r: (str(r["last_activity"])), reverse=True)
	rows.sort(key=lambda r: (not r["waiting"], -r["waiting_hours"]))
	return rows


@frappe.whitelist(methods=["GET"])
def inbox(app=None, status=None, search=None):
	"""Conversations awaiting an answer first (longest wait on top), then the rest."""
	_require_it()
	rows = _inbox_rows(mon._text(app) or None, mon._text(status) or None)
	term = mon._text(search).lower()[:60]
	if term:
		rows = [r for r in rows if term in (r["subject"] + " " + r["person"] + " " + r["preview"]).lower()]
	return {"rows": rows, "waiting": sum(1 for r in rows if r["waiting"]), "statuses": list(sup.ISSUE_STATUSES) if sup.support_doctype() == sup.ISSUE else []}


@frappe.whitelist(methods=["GET"])
def conversation(id):
	"""One conversation as chat: the person's words and each answer, who wrote it, plus what the report carried."""
	_require_it()
	if id.startswith(CHAT_PREFIX):
		thread = id[len(CHAT_PREFIX) :]
		head = frappe.db.get_value(sup.THREAD, thread, ["app", "user", "user_full_name"], as_dict=True)
		if not head:
			frappe.throw(_("Unknown conversation."), frappe.DoesNotExistError)
		msgs = frappe.get_all(sup.MESSAGE, filters={"thread": thread}, fields=["name", "from_staff", "text", "sent_at", "sender"], order_by="sent_at asc", limit_page_length=300)
		frappe.db.set_value(sup.THREAD, thread, "unread_staff", 0, update_modified=False)
		names = _display({m.sender for m in msgs if m.sender})
		return {
			"id": id,
			"app": head.app,
			"subject": "Chat",
			"status": "Open",
			"person": head.user_full_name or head.user,
			"user": head.user,
			"email": frappe.db.get_value("User", head.user, "email") or head.user,
			"messages": [{"id": m.name, "from_staff": bool(cint(m.from_staff)), "by": names.get(m.sender) or m.sender or "", "text": m.text, "sent_at": str(m.sent_at)} for m in msgs],
			"statuses": [],
		}
	doc = frappe.get_doc(sup.ISSUE, id)
	app_name, subject = sup.split_issue_subject(doc.subject)
	text, diag = sup.parse_issue_description(doc.description)
	comments = sup._issue_comments([doc.name]).get(doc.name, [])
	names = _display({doc.raised_by} | {c.get("comment_email") for c in comments if c.get("comment_email")})
	# Staff comments carry the staff user's address in comment_email; show the person's name.
	by_email = {u.email: u.full_name for u in frappe.get_all("User", filters={"email": ["in", [c.get("comment_email") for c in comments if c.get("comment_email")] or [""]]}, fields=["email", "full_name"])}
	files = sup._files_for(sup.ISSUE, [doc.name])
	messages = [{"id": doc.name, "from_staff": False, "by": names.get(doc.raised_by) or doc.raised_by, "text": text or subject, "sent_at": str(doc.creation), "image": files.get(doc.name)}]
	for c in comments:
		body = sup.plain_text(c.get("content"))
		if not body:
			continue
		own = sup.is_own_issue(c.get("comment_email"), doc.raised_by, doc.raised_by)
		who = c.get("comment_email")
		messages.append({"id": c.get("name"), "from_staff": not own, "by": names.get(who) or by_email.get(who) or who or "", "text": body, "sent_at": str(c.get("creation"))})
	device = diag.get("device") or {}
	out = {
		"id": doc.name,
		"app": app_name or "",
		"subject": subject,
		"status": doc.status,
		"person": names.get(doc.raised_by) or doc.raised_by,
		"user": doc.raised_by,
		"email": doc.raised_by,
		"messages": messages,
		"statuses": list(sup.ISSUE_STATUSES),
		"device": " ".join(x for x in (device.get("brand"), device.get("model")) if x),
		"os": " ".join(x for x in (device.get("platform"), device.get("os_version")) if x),
		"app_version": (diag.get("app") or {}).get("version") or "",
	}
	if is_developer():
		out["developer"] = {"diagnostics": diag or None, "desk_url": f"/app/issue/{doc.name}"}
	return out


@frappe.whitelist(methods=["POST"])
def send(id, text=None):
	"""Answer in the portal, as the signed-in staff member. The reply is stored with the conversation (a Comment on
	the Issue, so Desk shows it too) and pushed to the person's phone; no trip to Desk is needed."""
	_require_it()
	message = sup.clean_chat_text(text)
	if not message:
		frappe.throw(_("Write a message first."), frappe.ValidationError)
	if id.startswith(CHAT_PREFIX):
		thread = id[len(CHAT_PREFIX) :]
		if not frappe.db.exists(sup.THREAD, thread):
			frappe.throw(_("Unknown conversation."), frappe.DoesNotExistError)
		row = sup.message_row(sup._add_message(thread, frappe.session.user, message, True).as_dict())
		head = frappe.db.get_value(sup.THREAD, thread, ["app", "user"], as_dict=True)
		if head:
			push.notify_user(head.app, head.user, "Reply from support", message, {"kind": "support-reply", "thread": thread})
		return row
	doc = frappe.get_doc(sup.ISSUE, id)
	comment = doc.add_comment("Comment", message)  # the Comment hook pushes it to the reporter
	if doc.status in ("Open", "On Hold"):
		doc.status = "Replied"
		doc.save(ignore_permissions=True)
	return {"id": comment.name, "from_staff": True, "by": frappe.db.get_value("User", frappe.session.user, "full_name") or frappe.session.user, "text": message, "sent_at": str(comment.creation), "status": doc.status}


@frappe.whitelist(methods=["POST"])
def set_conversation_status(id, status):
	_require_it()
	if id.startswith(CHAT_PREFIX):
		frappe.throw(_("Chat conversations have no status."), frappe.ValidationError)
	if status not in sup.ISSUE_STATUSES:
		frappe.throw(_("Unknown status."), frappe.ValidationError)
	doc = frappe.get_doc(sup.ISSUE, id)
	doc.status = status
	doc.save(ignore_permissions=True)
	return {"id": id, "status": status}
