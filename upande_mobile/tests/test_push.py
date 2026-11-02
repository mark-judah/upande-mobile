# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Unit tests for support-reply push notifications. No site or database needed:
#   ../../env/bin/python -W ignore -m unittest upande_mobile.tests.test_push

import unittest
from unittest.mock import MagicMock, patch

import frappe

from upande_mobile import push
from upande_mobile.api import support as sup

TOKEN = "ExponentPushToken[abcDEF123_-xyz]"


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


class TestSwitch(unittest.TestCase):
	def test_site_config_can_turn_push_off(self):
		for off in (0, False, "0"):
			with patch.object(push.frappe, "conf", {"upande_mobile_push": off}):
				self.assertFalse(push.enabled())
		with patch.object(push.frappe, "conf", {}):
			self.assertTrue(push.enabled())


class TestSending(unittest.TestCase):
	def setUp(self):
		for target, value in (("enabled", True),):
			p = patch.object(push, target, return_value=value)
			p.start()
			self.addCleanup(p.stop)

	def test_sends_one_push_per_device_and_forgets_dead_ones(self):
		db = MagicMock()
		with patch.object(push.frappe, "db", db), patch.object(push, "tokens_for", return_value=[TOKEN, "ExpoPushToken[zzzzzzzz99]"]), patch.object(
			push, "_post", return_value={"data": [{"status": "ok"}, {"status": "error", "details": {"error": "DeviceNotRegistered"}}]}
		) as post:
			sent = push.notify_user("Farm", "a@b.c", "Reply on x", "Support: hi", {"kind": "support-reply"})
		self.assertEqual(sent, 2)
		self.assertEqual([m["to"] for m in post.call_args[0][0]], [TOKEN, "ExpoPushToken[zzzzzzzz99]"])
		self.assertEqual(post.call_args[0][0][0]["data"]["app"], "Farm")
		db.delete.assert_called_once_with("Mobile Push Token", {"token": "ExpoPushToken[zzzzzzzz99]"})

	def test_no_tokens_means_nothing_is_sent(self):
		with patch.object(push, "tokens_for", return_value=[]), patch.object(push, "_post") as post:
			self.assertEqual(push.notify_user("Farm", "a@b.c", "t", "b"), 0)
		post.assert_not_called()

	def test_a_failed_push_never_breaks_the_reply(self):
		with patch.object(push, "tokens_for", return_value=[TOKEN]), patch.object(push, "_post", side_effect=OSError("down")), patch.object(push.frappe, "log_error") as log:
			self.assertEqual(push.notify_user("Farm", "a@b.c", "t", "b"), 0)
		log.assert_called_once()

	def test_switched_off_by_site_config(self):
		with patch.object(push, "enabled", return_value=False), patch.object(push, "tokens_for") as t:
			self.assertEqual(push.notify_user("Farm", "a@b.c", "t", "b"), 0)
		t.assert_not_called()


class TestIssueReplies(unittest.TestCase):
	def _comment(self, **kw):
		c = MagicMock()
		c.reference_doctype = kw.get("reference_doctype", "Issue")
		c.comment_type = kw.get("comment_type", "Comment")
		c.comment_email = kw.get("comment_email", "boss@x.y")
		c.comment_by = kw.get("comment_by", "Boss")
		c.content = kw.get("content", "<p>Which bucket?</p>")
		c.reference_name = "ISS-1"
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
