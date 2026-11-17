# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Unit tests for support-reply push notifications. No site or database needed:
#   ../../env/bin/python -W ignore -m unittest upande_mobile.tests.test_push

import unittest
from datetime import datetime
from unittest.mock import MagicMock, patch

import frappe

from upande_mobile import push
from upande_mobile.api import support as sup

TOKEN = "ExponentPushToken[abcDEF123_-xyz]"
NOW = datetime(2026, 11, 18, 10, 0, 0)


def _freeze(case):
	"""No site needed: the clock would otherwise read System Settings through the (mocked) database."""
	p = patch.object(push, "now_datetime", return_value=NOW)
	p.start()
	case.addCleanup(p.stop)


def _throw(msg, exc=frappe.ValidationError, *a, **k):
	raise exc(msg)


class TestPushPure(unittest.TestCase):
	def test_only_expo_push_tokens_are_accepted(self):
		self.assertEqual(push.clean_token(f"  {TOKEN} "), TOKEN)
		self.assertEqual(push.clean_token("ExpoPushToken[abcdefgh12]"), "ExpoPushToken[abcdefgh12]")
		for bad in ("", None, 5, "fcm:abc", "ExponentPushToken[]", "ExponentPushToken[a b c d e f g h]", "https://evil/x", "ExponentPushToken[" + "a" * 200 + "]"):
			self.assertEqual(push.clean_token(bad), "")

	def test_messages_carry_the_channel_a_preview_and_the_ids_the_app_needs(self):
		msgs = push.build_messages([TOKEN, "ExpoPushToken[zzzzzzzz99]"], "Reply on Wrong length", "Support: " + "x" * 300, {"kind": "support-reply", "issue": "ISS-1", "app": "Farm"})
		self.assertEqual(len(msgs), 2)
		m = msgs[0]
		self.assertEqual((m["to"], m["channelId"], m["priority"], m["sound"]), (TOKEN, "support", "high", "default"))
		self.assertEqual(len(m["body"]), push.PREVIEW_LENGTH)
		self.assertEqual(m["data"], {"kind": "support-reply", "issue": "ISS-1", "app": "Farm", "upande_mobile": "support"})

	def test_tokens_expo_says_are_gone_are_found_in_order(self):
		tokens = ["a", "b", "c"]
		response = {"data": [{"status": "ok"}, {"status": "error", "details": {"error": "DeviceNotRegistered"}}, {"status": "error", "details": {"error": "MessageTooBig"}}]}
		self.assertEqual(push.dead_tokens(tokens, response), ["b"])
		self.assertEqual(push.dead_tokens(tokens, {"errors": []}), [])
		self.assertEqual(push.dead_tokens(tokens, None), [])


class TestRouteMessages(unittest.TestCase):
	def test_route_is_in_the_payload_and_drops_the_support_tag(self):
		msg = push.build_messages([TOKEN], "t", "b", {"kind": "task-due"}, channel="tasks", route="/task/TASK-1")[0]
		self.assertEqual(msg["data"]["route"], "/task/TASK-1")
		self.assertNotIn("upande_mobile", msg["data"])
		self.assertEqual(msg["channelId"], "tasks")

	def test_without_a_route_the_support_behaviour_is_unchanged(self):
		msg = push.build_messages([TOKEN], "t", "b", {"issue": "ISS-1"})[0]
		self.assertEqual(msg["data"]["upande_mobile"], "support")
		self.assertNotIn("route", msg["data"])
		self.assertEqual(msg["channelId"], "support")


class TestSwitch(unittest.TestCase):
	def test_site_config_can_turn_push_off(self):
		for off in (0, False, "0"):
			with patch.object(push.frappe, "conf", {"upande_mobile_push": off}):
				self.assertFalse(push.enabled())
		with patch.object(push.frappe, "conf", {}):
			self.assertTrue(push.enabled())


class TestEnqueueOnly(unittest.TestCase):
	"""`notify_user` only writes an outbox row; sending is `drain`'s job (see test_notifications for the DB side)."""

	def setUp(self):
		_freeze(self)

	def _db(self, user_exists=True, duplicate=False):
		db = MagicMock()
		db.exists.side_effect = lambda dt, filters=None, *a, **k: user_exists if dt == "User" else duplicate
		return db

	def test_notify_user_writes_a_row_and_never_calls_expo(self):
		doc = MagicMock()
		with patch.object(push, "enabled", return_value=True), patch.object(push.frappe, "db", self._db()), patch.object(push.frappe, "get_doc", return_value=doc) as get_doc, patch.object(
			push, "http_post"
		) as post:
			n = push.notify_user("Farm", "a@b.c", "t", "b", {"kind": "event"}, route="/deployments", channel="events", kind="deployment", dedupe_key="deployment:D-1:Deployed")
		self.assertEqual(n, 1)
		post.assert_not_called()
		doc.insert.assert_called_once()
		row = get_doc.call_args[0][0]
		self.assertEqual((row["status"], row["route"], row["channel"], row["kind"], row["dedupe_key"]), ("Queued", "/deployments", "events", "deployment", "deployment:D-1:Deployed"))

	def test_old_positional_callers_still_work(self):
		with patch.object(push, "enabled", return_value=True), patch.object(push.frappe, "db", self._db()), patch.object(push.frappe, "get_doc", return_value=MagicMock()) as get_doc:
			self.assertEqual(push.notify_user("Farm", "a@b.c", "Reply on x", "Support: hi", {"kind": "support-reply"}), 1)
		row = get_doc.call_args[0][0]
		self.assertEqual((row["kind"], row["channel"], row["dedupe_key"]), ("event", "support", None))

	def test_unknown_users_guests_and_duplicates_queue_nothing(self):
		for db, user in ((self._db(user_exists=False), "a@b.c"), (self._db(), "Guest"), (self._db(), ""), (self._db(duplicate=True), "a@b.c")):
			with patch.object(push, "enabled", return_value=True), patch.object(push.frappe, "db", db), patch.object(push.frappe, "get_doc") as get_doc:
				self.assertEqual(push.notify_user("Farm", user, "t", "b", dedupe_key="k"), 0)
			get_doc.assert_not_called()

	def test_a_failed_insert_never_breaks_the_caller(self):
		doc = MagicMock()
		doc.insert.side_effect = frappe.ValidationError("boom")
		db = self._db()
		with patch.object(push, "enabled", return_value=True), patch.object(push.frappe, "db", db), patch.object(push.frappe, "get_doc", return_value=doc), patch.object(push.frappe, "log_error") as log:
			self.assertEqual(push.notify_user("Farm", "a@b.c", "t", "b"), 0)
		log.assert_called_once()
		self.assertTrue(db.rollback.call_args.kwargs["save_point"].startswith("mobile_notify_"))

	def test_switched_off_still_writes_the_inbox_row_as_sent(self):
		with patch.object(push, "enabled", return_value=False), patch.object(push.frappe, "db", self._db()), patch.object(push.frappe, "get_doc", return_value=MagicMock()) as get_doc:
			self.assertEqual(push.notify_user("Farm", "a@b.c", "t", "b"), 0)
		row = get_doc.call_args[0][0]
		self.assertEqual(row["status"], "Sent")
		self.assertIsNotNone(row["sent_at"])


class TestOutboxPure(unittest.TestCase):
	def setUp(self):
		_freeze(self)

	def test_backoff_schedule(self):
		self.assertEqual([push.next_attempt_delay(n) for n in range(1, 7)], [1, 5, 15, 60, 360, 360])
		self.assertEqual(push.MAX_ATTEMPTS, 6)

	def test_rows_are_packed_whole_into_chunks_of_at_most_100_messages(self):
		items = [(f"r{i}", None, [{}] * 5) for i in range(41)]
		chunks = list(push._chunks(items))
		self.assertEqual([sum(len(it[2]) for it in c) for c in chunks], [100, 100, 5])

	def test_ticket_outcomes(self):
		row = frappe._dict(name="N1", attempts=1)
		cases = (
			([{"status": "ok", "id": "t1"}], "Sent"),
			([{"status": "error", "details": {"error": "MessageTooBig"}}], "Dead"),
			([{"status": "error", "details": {"error": "MessageRateExceeded"}}], "Failed"),
			([{"status": "error", "details": {"error": "DeviceNotRegistered"}}], "Sent"),
			([], "Failed"),
		)
		for tickets, expected in cases:
			with patch.object(push, "_update"), patch.object(push.frappe, "db", MagicMock()):
				self.assertEqual(push._settle(row, [TOKEN], tickets), expected, tickets)


class TestIssueReplies(unittest.TestCase):
	def _comment(self, **kw):
		c = MagicMock()
		c.reference_doctype = kw.get("reference_doctype", "Issue")
		c.comment_type = kw.get("comment_type", "Comment")
		c.comment_email = kw.get("comment_email", "boss@x.y")
		c.comment_by = kw.get("comment_by", "Boss")
		c.content = kw.get("content", "<p>Which bucket?</p>")
		c.reference_name = "ISS-1"
		c.name = "C1"
		return c

	def _run(self, comment, issue_subject="[Farm] Wrong length", raised_by="a@b.c", app_exists=True):
		issue = MagicMock()
		issue.subject = issue_subject
		issue.raised_by = raised_by
		issue.name = "ISS-1"
		db = MagicMock()
		db.exists.side_effect = lambda dt, name=None: (app_exists if dt == "Mobile App" else True)
		with patch.object(push.frappe, "get_doc", side_effect=lambda dt, n=None: comment if dt == "Comment" else issue), patch.object(push.frappe, "db", db), patch.object(push, "notify_user", return_value=1) as notify:
			return push.notify_issue_reply("C1"), notify

	def test_support_comment_notifies_the_person_who_raised_the_issue(self):
		n, notify = self._run(self._comment())
		self.assertEqual(n, 1)
		app, user, title, body, data = notify.call_args[0]
		self.assertEqual((app, user, title, body), ("Farm", "a@b.c", "Reply on Wrong length", "Boss: Which bucket?"))
		self.assertEqual(data, {"kind": "support-reply", "issue": "ISS-1"})
		self.assertEqual((notify.call_args.kwargs["kind"], notify.call_args.kwargs["dedupe_key"]), ("support", "support:C1"))

	def test_the_persons_own_comment_other_doctypes_blank_text_and_unknown_apps_do_not_notify(self):
		for comment, kw in (
			(self._comment(comment_email="a@b.c"), {}),
			(self._comment(reference_doctype="ToDo"), {}),
			(self._comment(comment_type="Info"), {}),
			(self._comment(content="<p>&nbsp;</p>"), {}),
			(self._comment(), {"issue_subject": "No prefix here"}),
			(self._comment(), {"app_exists": False}),
			(self._comment(), {"raised_by": None}),
		):
			n, notify = self._run(comment, **kw)
			self.assertEqual(n, 0, kw)
			notify.assert_not_called()

	def test_the_hook_queues_a_job_only_for_comments_on_issues(self):
		with patch.object(push, "enabled", return_value=True), patch.object(push.frappe, "enqueue") as enqueue:
			push.on_comment(self._comment())
			push.on_comment(self._comment(reference_doctype="ToDo"))
			push.on_comment(self._comment(comment_type="Attachment"))
		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args[1]["enqueue_after_commit"], True)


class TestRegistration(unittest.TestCase):
	def setUp(self):
		t = patch.object(sup, "_", side_effect=lambda m: m)
		t.start()
		self.addCleanup(t.stop)

	def test_guests_and_junk_tokens_are_refused(self):
		with patch.object(sup.frappe, "throw", side_effect=_throw):
			with patch.object(sup.frappe, "session", MagicMock(user="Guest")):
				with self.assertRaises(frappe.AuthenticationError):
					sup.register_push(app="Farm", token=TOKEN)
			with patch.object(sup.frappe, "session", MagicMock(user="a@b.c")), patch.object(sup, "_app_row", return_value="Farm"):
				with self.assertRaises(frappe.ValidationError):
					sup.register_push(app="Farm", token="not-a-token")

	def test_unregister_only_removes_the_callers_own_token(self):
		db = MagicMock()
		with patch.object(sup.frappe, "session", MagicMock(user="a@b.c")), patch.object(sup.frappe, "db", db):
			sup.unregister_push(token=TOKEN)
			sup.unregister_push(token="junk")
		db.delete.assert_called_once_with("Mobile Push Token", {"token": TOKEN, "user": "a@b.c"})


if __name__ == "__main__":
	unittest.main()
