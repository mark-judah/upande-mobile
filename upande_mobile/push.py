# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Push notifications through Expo's push service, with an outbox and a per-person inbox.
#
#   - Phones register their Expo push token with `api.support.register_push`; it is kept per user and app in
#     **Mobile Push Token** (one row per token, so a handed-over phone follows its new owner).
#   - `notify_user` never talks to Expo. It writes one **Mobile Notification** row (recipient + event) in the
#     caller's own transaction, so the notification exists exactly when the caller's save does. That row is both
#     the outbox entry and the person's inbox item (`api.notifications`), so a lost or late push never loses it.
#   - `drain` (every minute) claims due rows atomically, posts them to Expo in chunks, and records the tickets.
#     Network trouble, HTTP 429 / 5xx and transient ticket errors are retried with backoff (1m, 5m, 15m, 1h, 6h);
#     after MAX_ATTEMPTS the row is Dead (still in the inbox). Rows stuck in Sending are released.
#   - `check_receipts` (every 5 minutes) reads Expo's delivery receipts; `cleanup` (daily) drops old rows.
#   - When support adds a **Comment** to a person's Issue, `on_comment` queues `notify_issue_reply`, which
#     notifies that person for the app named in the Issue's subject ("[App] ...").
#   - A token Expo reports as no longer registered is deleted.
#
# Only https://exp.host is ever contacted, and only with the payload built here. Turn sending off with
# `upande_mobile_push: 0` in site_config.json: notifications are then still written to the inbox, never pushed.

import json
import re

import frappe
from frappe.utils import add_to_date, cint, now_datetime

PUSH = "Mobile Push Token"
NOTIFICATION = "Mobile Notification"
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
EXPO_RECEIPTS_URL = "https://exp.host/--/api/v2/push/getReceipts"
TOKEN_PATTERN = re.compile(r"^(Exponent|Expo)PushToken\[[A-Za-z0-9_\-]{8,80}\]$")
MAX_TOKENS_PER_USER_APP = 5
PREVIEW_LENGTH = 120
CHANNEL_ID = "support"

# Outbox tuning.
QUEUED, SENDING, SENT, FAILED, DEAD = "Queued", "Sending", "Sent", "Failed", "Dead"
BATCH_SIZE = 100  # rows one drain run claims
EXPO_CHUNK = 100  # messages per POST (Expo's limit)
RECEIPT_CHUNK = 1000  # ticket ids per getReceipts call (Expo's limit)
RECEIPT_BATCH = 500  # rows one check_receipts run looks at
BACKOFF_MINUTES = (1, 5, 15, 60, 360)
MAX_ATTEMPTS = 6
STUCK_MINUTES = 10
RECEIPT_DELAY_MINUTES = 15
RECEIPT_GIVE_UP_HOURS = 24  # Expo keeps receipts about a day
RETENTION_DAYS = 30
HTTP_TIMEOUT = 10
MAX_TITLE = 140
MAX_BODY = 1000
MAX_FIELD = 140
MAX_ERROR = 1000
# Ticket / receipt errors that will never succeed on a retry. DeviceNotRegistered is handled on its own (token deleted).
PERMANENT_TICKET_ERRORS = {"MessageTooBig"}
NO_DEVICE = "No registered device; kept in the inbox."


def enabled():
	return frappe.conf.get("upande_mobile_push") not in (0, False, "0")


def clean_token(value):
	"""The token when it looks like an Expo push token, else ''."""
	token = value.strip() if isinstance(value, str) else ""
	return token if TOKEN_PATTERN.match(token) else ""


def build_messages(tokens, title, body, data, channel=CHANNEL_ID, route=None):
	"""One Expo push message per token. The payload carries only an id, never the message text beyond the preview.

	With a `route` (a screen the app should open) the payload carries it and is not tagged as a support push; without
	one it is tagged `upande_mobile: support`, exactly as before."""
	payload = dict(data or {})
	if route:
		payload["route"] = route
	else:
		payload["upande_mobile"] = "support"
	return [
		{
			"to": token,
			"title": (title or "")[:100],
			"body": (body or "")[:PREVIEW_LENGTH],
			"sound": "default",
			"channelId": channel or CHANNEL_ID,
			"priority": "high",
			"data": dict(payload),
		}
		for token in tokens
	]


def dead_tokens(tokens, response):
	"""Tokens Expo says are gone (the app was uninstalled or notifications revoked): `response` is Expo's JSON answer,
	one ticket per message in the order sent."""
	tickets = (response or {}).get("data") if isinstance(response, dict) else None
	if not isinstance(tickets, list):
		return []
	gone = []
	for token, ticket in zip(tokens, tickets):
		if isinstance(ticket, dict) and ticket.get("status") == "error" and (ticket.get("details") or {}).get("error") == "DeviceNotRegistered":
			gone.append(token)
	return gone


def tokens_for(app, user):
	return frappe.get_all(PUSH, filters={"app": app, "user": user}, pluck="token", order_by="registered_at desc", limit_page_length=MAX_TOKENS_PER_USER_APP)


def http_post(url, payload):
	"""POST JSON to Expo. Returns (status_code, parsed JSON or None); raises on network errors and timeouts.
	`drain` and `check_receipts` take a replacement with the same shape, so tests never touch the network."""
	import requests  # a dependency of Frappe

	headers = {"Accept": "application/json", "Accept-Encoding": "gzip, deflate", "Content-Type": "application/json"}
	access_token = frappe.conf.get("expo_access_token")
	if access_token:
		headers["Authorization"] = f"Bearer {access_token}"
	res = requests.post(url, json=payload, timeout=HTTP_TIMEOUT, headers=headers)
	try:
		body = res.json()
	except ValueError:
		body = None
	return res.status_code, body


def _clip(value, length):
	return value.strip()[:length] if isinstance(value, str) else ""


def _rowcount():
	"""Rows touched by the last UPDATE / DELETE on this connection."""
	return cint(getattr(frappe.db._cursor, "rowcount", 0))


# --------------------------------------------------------------------------- #
# Enqueue
# --------------------------------------------------------------------------- #


def notify_user(app, user, title, body, data=None, route=None, channel=CHANNEL_ID, kind="event", dedupe_key=None):
	"""Queue a notification for `user` in `app`. Never raises: a notification must not break the caller's save.

	The row is written in the caller's transaction (it rolls back with it) and sent later by `drain`. `route` makes
	the push open that screen (and drops the support tag); `channel` is the Android channel; `kind` is the inbox
	category; a `dedupe_key` makes a repeat of the same event for the same person and app a no-op.

	Returns 1 when a notification was queued for sending, 0 when nothing was queued: a duplicate, an invalid
	recipient, or push turned off on this site (the inbox row is still written then, already marked Sent)."""
	message_log = getattr(frappe.local, "message_log", None)
	logged = len(message_log) if isinstance(message_log, list) else 0
	savepoint = None
	try:
		app = _clip(app, MAX_FIELD)
		user = _clip(user, MAX_FIELD)
		if not app or not user or user == "Guest" or not frappe.db.exists("User", user):
			return 0
		dedupe_key = _clip(dedupe_key, MAX_FIELD) or None
		if dedupe_key and frappe.db.exists(NOTIFICATION, {"app": app, "user": user, "dedupe_key": dedupe_key}):
			return 0
		sending = enabled()
		now = now_datetime()
		doc = frappe.get_doc(
			{
				"doctype": NOTIFICATION,
				"app": app,
				"user": user,
				"title": _clip(title, MAX_TITLE),
				"body": _clip(body, MAX_BODY),
				"route": _clip(route, MAX_FIELD) or None,
				"kind": _clip(kind, MAX_FIELD) or "event",
				"channel": _clip(channel, MAX_FIELD) or CHANNEL_ID,
				"data": json.dumps(data if isinstance(data, dict) else {}, default=str, separators=(",", ":")),
				"dedupe_key": dedupe_key,
				"status": QUEUED if sending else SENT,
				"attempts": 0,
				"next_attempt_at": now if sending else None,
				"sent_at": None if sending else now,
			}
		)
		# A savepoint keeps a failed insert (for example the unique dedupe index losing a race) from poisoning the
		# caller's transaction; the caller's own writes are untouched.
		savepoint = "mobile_notify_" + frappe.generate_hash(length=8)
		frappe.db.savepoint(savepoint)
		doc.insert(ignore_permissions=True, ignore_links=True)
		frappe.db.release_savepoint(savepoint)
		return 1 if sending else 0
	except Exception:
		if savepoint:
			try:
				frappe.db.rollback(save_point=savepoint)
			except Exception:
				pass
		# Frappe msgprints unique / link errors; they are not the caller's business.
		if isinstance(message_log, list):
			del message_log[logged:]
		try:
			# Losing the dedupe race to a concurrent insert of the same event is not an error.
			lost_race = bool(dedupe_key) and frappe.db.exists(NOTIFICATION, {"app": app, "user": user, "dedupe_key": dedupe_key})
			if not lost_race:
				frappe.log_error(title="Mobile push: could not queue")
		except Exception:
			pass
		return 0


# --------------------------------------------------------------------------- #
# Drain
# --------------------------------------------------------------------------- #


def release_stuck():
	"""Rows left in Sending by a worker that died are retried (or Dead when out of attempts). Returns how many."""
	now = now_datetime()
	cutoff = add_to_date(now, minutes=-STUCK_MINUTES)
	note = "Released after being stuck in Sending."
	frappe.db.sql(
		f"""update `tab{NOTIFICATION}` set status=%s, last_error=%s, modified=%s
		where status=%s and modified < %s and attempts >= %s""",
		(DEAD, note, now, SENDING, cutoff, MAX_ATTEMPTS),
	)
	released = _rowcount()
	frappe.db.sql(
		f"""update `tab{NOTIFICATION}` set status=%s, next_attempt_at=%s, last_error=%s, modified=%s
		where status=%s and modified < %s""",
		(FAILED, now, note, now, SENDING, cutoff),
	)
	return released + _rowcount()


def claim(limit=BATCH_SIZE):
	"""Atomically move up to `limit` due rows to Sending and return their names.

	Each row is taken with a conditional UPDATE and kept only when this connection changed it, so two workers that
	pick the same candidates never both own one (InnoDB re-checks the WHERE under the row lock)."""
	now = now_datetime()
	candidates = frappe.db.sql(
		f"""select name from `tab{NOTIFICATION}`
		where status in (%s, %s) and (next_attempt_at is null or next_attempt_at <= %s)
		order by next_attempt_at asc, creation asc limit %s""",
		(QUEUED, FAILED, now, cint(limit)),
		pluck=True,
	)
	claimed = []
	for name in candidates:
		frappe.db.sql(
			f"""update `tab{NOTIFICATION}` set status=%s, attempts=attempts+1, modified=%s
			where name=%s and status in (%s, %s) and (next_attempt_at is null or next_attempt_at <= %s)""",
			(SENDING, now, name, QUEUED, FAILED, now),
		)
		if _rowcount() == 1:
			claimed.append(name)
	return claimed


def drain(post=None, limit=BATCH_SIZE):
	"""Scheduler (every minute): send due notifications. Safe to run in several workers at once.

	`post(url, payload) -> (status_code, json)` replaces the HTTP call (tests). Returns a small summary."""
	summary = {"released": 0, "claimed": 0, SENT: 0, FAILED: 0, DEAD: 0}
	if not enabled():
		return summary
	post = post or http_post
	summary["released"] = release_stuck()
	names = claim(limit)
	# Commit the claim before talking to Expo, so other workers see these rows as taken.
	frappe.db.commit()
	summary["claimed"] = len(names)
	if not names:
		return summary
	rows = frappe.get_all(
		NOTIFICATION,
		filters={"name": ["in", names], "status": SENDING},
		fields=["name", "app", "user", "title", "body", "route", "kind", "channel", "data", "attempts"],
		order_by="creation asc",
		limit_page_length=0,
	)
	try:
		outcome = _send_rows(rows, post)
	except Exception:
		frappe.log_error(title="Mobile push: drain failed")
		outcome = {}
		for row in rows:
			outcome[row.name] = _retry(row, "Unexpected error while sending; see Error Log.")
	frappe.db.commit()
	for status in outcome.values():
		summary[status] = summary.get(status, 0) + 1
	return summary


def _payload(row):
	try:
		data = json.loads(row.data) if row.data else {}
	except ValueError:
		data = {}
	payload = dict(data) if isinstance(data, dict) else {}
	payload["app"] = row.app
	payload["notification"] = row.name
	payload.setdefault("kind", row.kind)
	return payload


def _valid_tokens(app, user):
	"""This person's tokens for the app. A stored string that is not an Expo token can never be sent: it is
	dropped (and deleted) so it cannot spoil a batch."""
	good = []
	for token in tokens_for(app, user):
		if clean_token(token):
			good.append(token)
		else:
			frappe.db.delete(PUSH, {"token": token})
	return good


def _send_rows(rows, post):
	outcome = {}
	tokens_by_person = {}
	by_app = {}
	for row in rows:
		person = (row.app, row.user)
		if person not in tokens_by_person:
			tokens_by_person[person] = _valid_tokens(*person)
		tokens = tokens_by_person[person]
		if not tokens:
			outcome[row.name] = _mark_sent(row, [], NO_DEVICE)
			continue
		messages = build_messages(tokens, row.title, row.body, _payload(row), channel=row.channel, route=row.route)
		by_app.setdefault(row.app, []).append((row, tokens, messages))
	# One app's tokens belong to one Expo project; Expo refuses a request that mixes projects.
	for items in by_app.values():
		for chunk in _chunks(items):
			_send_chunk(chunk, post, outcome)
	return outcome


def _chunks(items):
	"""Whole rows packed into requests of at most EXPO_CHUNK messages (a row never straddles two requests)."""
	chunk, size = [], 0
	for item in items:
		if chunk and size + len(item[2]) > EXPO_CHUNK:
			yield chunk
			chunk, size = [], 0
		chunk.append(item)
		size += len(item[2])
	if chunk:
		yield chunk


def _send_chunk(items, post, outcome):
	messages = [m for _row, _tokens, msgs in items for m in msgs]
	try:
		status, body = post(EXPO_PUSH_URL, messages)
	except Exception as e:  # timeout, connection refused, DNS...
		for row, _tokens, _msgs in items:
			outcome[row.name] = _retry(row, f"Expo unreachable: {type(e).__name__}: {e}")
		return
	status = cint(status)
	if status == 429 or status >= 500 or status == 0:
		for row, _tokens, _msgs in items:
			outcome[row.name] = _retry(row, f"Expo HTTP {status}: {_snippet(body)}")
		return
	if status >= 400:
		# The whole request was refused. Try rows one by one so a single bad row cannot sink the rest.
		if len(items) > 1:
			for item in items:
				_send_chunk([item], post, outcome)
			return
		row = items[0][0]
		outcome[row.name] = _mark_dead(row, f"Expo refused the request (HTTP {status}): {_snippet(body)}")
		return
	tickets = body.get("data") if isinstance(body, dict) else None
	if not isinstance(tickets, list):
		for row, _tokens, _msgs in items:
			outcome[row.name] = _retry(row, f"Expo answered without tickets: {_snippet(body)}")
		return
	# Tickets come back in the order the messages were sent.
	position = 0
	for row, tokens, msgs in items:
		outcome[row.name] = _settle(row, tokens, tickets[position : position + len(msgs)])
		position += len(msgs)


def _settle(row, tokens, tickets):
	"""Decide a row's fate from its own tickets (one per token, same order)."""
	ok, ids, errors = 0, [], []
	permanent = transient = False
	for index, token in enumerate(tokens):
		ticket = tickets[index] if index < len(tickets) else None
		if not isinstance(ticket, dict):
			transient = True
			errors.append("no ticket")
			continue
		if ticket.get("status") == "ok":
			ok += 1
			if ticket.get("id"):
				ids.append(str(ticket["id"]))
			continue
		error = (ticket.get("details") or {}).get("error") or ticket.get("message") or "unknown error"
		errors.append(str(error)[:200])
		if error == "DeviceNotRegistered":
			frappe.db.delete(PUSH, {"token": token})
		elif error in PERMANENT_TICKET_ERRORS:
			permanent = True
		else:
			transient = True
	note = "; ".join(errors)
	if ok:
		return _mark_sent(row, ids, note)
	if permanent:
		return _mark_dead(row, note)
	if transient:
		return _retry(row, note)
	# Every device has gone: nothing left to push to, the inbox keeps it.
	return _mark_sent(row, [], f"{NO_DEVICE} {note}".strip())


def _snippet(body):
	try:
		text = json.dumps(body, default=str) if body is not None else ""
	except Exception:
		text = str(body)
	return text[:300]


def _update(name, values):
	frappe.db.set_value(NOTIFICATION, name, values, update_modified=True)


def _mark_sent(row, ticket_ids, note=""):
	_update(
		row.name,
		{
			"status": SENT,
			"sent_at": now_datetime(),
			"next_attempt_at": None,
			"expo_ticket_ids": json.dumps(ticket_ids) if ticket_ids else None,
			"delivered_checked_at": None,
			"last_error": (note or "")[:MAX_ERROR] or None,
		},
	)
	return SENT


def _mark_dead(row, error):
	_update(row.name, {"status": DEAD, "next_attempt_at": None, "last_error": (error or "")[:MAX_ERROR]})
	return DEAD


def next_attempt_delay(attempts):
	"""Minutes to wait after the `attempts`-th failed try (1m, 5m, 15m, 1h, 6h)."""
	return BACKOFF_MINUTES[max(0, min(cint(attempts) - 1, len(BACKOFF_MINUTES) - 1))]


def _retry(row, error):
	attempts = cint(row.attempts)
	if attempts >= MAX_ATTEMPTS:
		return _mark_dead(row, f"Gave up after {attempts} attempts: {error}")
	_update(
		row.name,
		{
			"status": FAILED,
			"next_attempt_at": add_to_date(now_datetime(), minutes=next_attempt_delay(attempts)),
			"last_error": (error or "")[:MAX_ERROR],
		},
	)
	return FAILED


# --------------------------------------------------------------------------- #
# Receipts and cleanup
# --------------------------------------------------------------------------- #


def _ticket_ids(raw):
	try:
		value = json.loads(raw) if raw else []
	except ValueError:
		return []
	return [str(v) for v in value if isinstance(v, str) and v] if isinstance(value, list) else []


def check_receipts(post=None):
	"""Scheduler (every 5 minutes): read Expo's receipts for rows sent at least 15 minutes ago.

	DeviceNotRegistered deletes that token; any other error requeues the row once; otherwise the row is marked
	checked. A receipt Expo does not have yet is asked for again next run, for up to a day."""
	if not enabled():
		return {"checked": 0}
	post = post or http_post
	now = now_datetime()
	rows = frappe.db.sql(
		f"""select name, expo_ticket_ids, sent_at, receipt_retried from `tab{NOTIFICATION}`
		where status=%s and delivered_checked_at is null and expo_ticket_ids is not null and expo_ticket_ids != ''
		and sent_at <= %s order by sent_at asc limit %s""",
		(SENT, add_to_date(now, minutes=-RECEIPT_DELAY_MINUTES), RECEIPT_BATCH),
		as_dict=True,
	)
	ids_by_row = {r.name: _ticket_ids(r.expo_ticket_ids) for r in rows}
	all_ids = [i for ids in ids_by_row.values() for i in ids]
	receipts = {}
	for start in range(0, len(all_ids), RECEIPT_CHUNK):
		try:
			status, body = post(EXPO_RECEIPTS_URL, {"ids": all_ids[start : start + RECEIPT_CHUNK]})
		except Exception:
			continue  # unreachable: ask again next run
		data = body.get("data") if isinstance(body, dict) else None
		if 200 <= cint(status) < 300 and isinstance(data, dict):
			receipts.update(data)
	give_up_before = add_to_date(now, hours=-RECEIPT_GIVE_UP_HOURS)
	checked = 0
	for row in rows:
		ids = ids_by_row[row.name]
		pending, errors = False, []
		for ticket_id in ids:
			receipt = receipts.get(ticket_id)
			if not isinstance(receipt, dict):
				pending = True
				continue
			if receipt.get("status") != "error":
				continue
			details = receipt.get("details") or {}
			error = details.get("error") or receipt.get("message") or "unknown error"
			if error == "DeviceNotRegistered":
				token = clean_token(details.get("expoPushToken"))
				if token:
					frappe.db.delete(PUSH, {"token": token})
			else:
				errors.append(str(error)[:200])
		if errors and not cint(row.receipt_retried):
			_update(
				row.name,
				{
					"status": QUEUED,
					"next_attempt_at": now,
					"expo_ticket_ids": None,
					"receipt_retried": 1,
					"last_error": ("Receipt error, sending again: " + "; ".join(errors))[:MAX_ERROR],
				},
			)
		elif pending and ids and row.sent_at and row.sent_at > give_up_before:
			continue
		else:
			values = {"delivered_checked_at": now}
			if errors:
				values["last_error"] = ("Receipt error: " + "; ".join(errors))[:MAX_ERROR]
			_update(row.name, values)
		checked += 1
	frappe.db.commit()
	return {"checked": checked}


def cleanup():
	"""Scheduler (daily): forget notifications older than RETENTION_DAYS."""
	frappe.db.delete(NOTIFICATION, {"creation": ("<", add_to_date(now_datetime(), days=-RETENTION_DAYS))})
	frappe.db.commit()


# --------------------------------------------------------------------------- #
# Support replies
# --------------------------------------------------------------------------- #


def on_comment(doc, method=None):
	"""Hook: a Comment was added. Cheap filters first; the work happens in a background job."""
	if doc.reference_doctype != "Issue" or doc.comment_type != "Comment":
		return
	frappe.enqueue("upande_mobile.push.notify_issue_reply", comment=doc.name, queue="short", enqueue_after_commit=True)


def notify_issue_reply(comment):
	"""Tell the person who raised an Issue that support answered it."""
	from upande_mobile.api.support import plain_text, split_issue_subject

	doc = frappe.get_doc("Comment", comment)
	if doc.reference_doctype != "Issue" or doc.comment_type != "Comment":
		return 0
	issue = frappe.get_doc("Issue", doc.reference_name)
	app, subject = split_issue_subject(issue.subject)
	if not app or not issue.raised_by or not frappe.db.exists("Mobile App", app):
		return 0
	# The person's own comments (and anything written under their address) never notify them.
	if doc.comment_email and doc.comment_email == issue.raised_by:
		return 0
	user = issue.raised_by if frappe.db.exists("User", issue.raised_by) else frappe.db.get_value("User", {"email": issue.raised_by}, "name")
	if not user:
		return 0
	preview = plain_text(doc.content)
	if not preview:
		return 0
	who = doc.comment_by or "Support"
	return notify_user(
		app, user, f"Reply on {subject}"[:100], f"{who}: {preview}", {"kind": "support-reply", "issue": issue.name}, kind="support", dedupe_key=f"support:{doc.name}"
	)
