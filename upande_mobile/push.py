# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Push notifications for support replies, through Expo's push service.
#
#   - Phones register their Expo push token with `api.support.register_push`; it is kept per user and app in
#     **Mobile Push Token** (one row per token, so a handed-over phone follows its new owner).
#   - When support adds a **Comment** to a person's Issue, `on_comment` queues `notify_issue_reply`, which sends a
#     push to that person's devices for the app named in the Issue's subject ("[App] ..."). On a site without Issue,
#     the portal's chat reply sends one the same way (`notify_user`).
#   - A token Expo reports as no longer registered is deleted.
#
# Only https://exp.host is ever contacted, and only with the payload built here. Turn all of it off with
# `upande_mobile_push: 0` in site_config.json.

import re

import frappe

PUSH = "Mobile Push Token"
EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
TOKEN_PATTERN = re.compile(r"^(Exponent|Expo)PushToken\[[A-Za-z0-9_\-]{8,80}\]$")
MAX_TOKENS_PER_USER_APP = 5
PREVIEW_LENGTH = 120
CHANNEL_ID = "support"


def enabled():
	return frappe.conf.get("upande_mobile_push") not in (0, False, "0")


def clean_token(value):
	"""The token when it looks like an Expo push token, else ''."""
	token = value.strip() if isinstance(value, str) else ""
	return token if TOKEN_PATTERN.match(token) else ""


def build_messages(tokens, title, body, data):
	"""One Expo push message per token. The payload carries only an id, never the message text beyond the preview."""
	return [
		{
			"to": token,
			"title": (title or "")[:100],
			"body": (body or "")[:PREVIEW_LENGTH],
			"sound": "default",
			"channelId": CHANNEL_ID,
			"priority": "high",
			"data": dict(data or {}, upande_mobile="support"),
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


def _post(messages):
	import requests  # a dependency of Frappe

	res = requests.post(EXPO_PUSH_URL, json=messages, timeout=10, headers={"Accept": "application/json", "Accept-Encoding": "gzip, deflate"})
	res.raise_for_status()
	return res.json()


def notify_user(app, user, title, body, data=None):
	"""Push to every registered device of `user` for `app`. Never raises: a failed push must not break the reply."""
	if not enabled():
		return 0
	try:
		tokens = tokens_for(app, user)
		if not tokens:
			return 0
		response = _post(build_messages(tokens, title, body, dict(data or {}, app=app)))
		for token in dead_tokens(tokens, response):
			frappe.db.delete(PUSH, {"token": token})
		return len(tokens)
	except Exception:
		frappe.log_error(title="Mobile push: send failed")
		return 0


def on_comment(doc, method=None):
	"""Hook: a Comment was added. Cheap filters first; the work happens in a background job."""
	if not enabled() or doc.reference_doctype != "Issue" or doc.comment_type != "Comment":
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
	return notify_user(app, user, f"Reply on {subject}"[:100], f"{who}: {preview}", {"kind": "support-reply", "issue": issue.name})
