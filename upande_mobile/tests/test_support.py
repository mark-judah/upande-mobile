# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Unit tests for problem reports and support chat. No site or database needed:
#   ../../env/bin/python -W ignore -m unittest upande_mobile.tests.test_support

import base64
import json
import unittest
from unittest.mock import MagicMock, patch

import frappe

from upande_mobile.api import support as sup

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 20


def _throw(msg, exc=frappe.ValidationError, *a, **k):
	raise exc(msg)


class TestCleaning(unittest.TestCase):
	def test_diagnostics_keep_only_known_fields_and_redact(self):
		raw = {
			"captured_at": "2026-11-02T10:00:00Z",
			"app": {"name": "Farm", "version": "1.2.3", "runtime_version": "1.2", "evil": "x"},
			"user": {"id": "a@b.c", "full_name": "Ann", "password": "hunter2"},
			"device": {"model": "Pixel", "battery_level": 0.4, "wifi_ssid": "Farm", "latency_ms": 90, "install_id": "abcdef12-3456", "evil": 1},
			"recent_calls": [{"ts": "t", "method": "get", "url": "/api/x?token=SECRET123&a=1", "status": 500, "ms": 120}, "junk"],
			"last_crash": {"message": "boom", "fatal": True, "stack": "at x", "evil": "y"},
			"extra": "dropped",
		}
		d = sup.clean_diagnostics(json.dumps(raw))
		self.assertEqual(d["app"], {"name": "Farm", "version": "1.2.3", "runtime_version": "1.2"})
		self.assertEqual(d["user"], {"id": "a@b.c", "full_name": "Ann"})
		self.assertEqual(d["device"]["wifi_ssid"], "Farm")
		self.assertNotIn("install_id", d["device"])
		self.assertNotIn("evil", d["device"])
		self.assertEqual(len(d["recent_calls"]), 1)
		self.assertEqual(d["recent_calls"][0]["method"], "GET")
		self.assertNotIn("SECRET123", json.dumps(d))
		self.assertEqual(d["last_crash"]["message"], "boom")
		self.assertNotIn("evil", d["last_crash"])
		self.assertNotIn("extra", d)

	def test_diagnostics_reject_garbage_and_oversize(self):
		self.assertEqual(sup.clean_diagnostics("{nope"), {})
		self.assertEqual(sup.clean_diagnostics("[1]"), {})
		self.assertEqual(sup.clean_diagnostics("x" * (sup.MAX_DIAGNOSTICS_BYTES + 1)), {})
		self.assertEqual(sup.clean_diagnostics(None), {})

	def test_only_the_newest_calls_are_kept(self):
		calls = [{"ts": str(i), "method": "GET", "url": f"/u/{i}", "status": 200, "ms": 1} for i in range(50)]
		d = sup.clean_diagnostics({"recent_calls": calls})
		self.assertEqual(len(d["recent_calls"]), sup.MAX_CALLS)
		self.assertEqual(d["recent_calls"][-1]["ts"], "49")

	def test_screenshot_must_be_a_small_jpeg(self):
		self.assertEqual(sup.decode_screenshot(base64.b64encode(JPEG).decode()), JPEG)
		self.assertIsNone(sup.decode_screenshot(base64.b64encode(b"\x89PNG....").decode()))
		self.assertIsNone(sup.decode_screenshot("not base64!!"))
		self.assertIsNone(sup.decode_screenshot("A" * (sup.MAX_SCREENSHOT_B64 + 4)))
		self.assertIsNone(sup.decode_screenshot(None))

	def test_crash_is_redacted_and_needs_a_message(self):
		self.assertIsNone(sup.clean_crash({"stack": "x"}))
		c = sup.clean_crash({"message": "fail Authorization: Bearer abcdefghijklmnop", "fatal": 1})
		self.assertNotIn("abcdefghijklmnop", c["message"])
		self.assertIs(c["fatal"], True)

	def test_chat_text_and_rows(self):
		self.assertEqual(sup.clean_chat_text("  hi  "), "hi")
		self.assertEqual(len(sup.clean_chat_text("x" * 5000)), sup.MAX_CHAT)
		self.assertEqual(sup.clean_chat_text(None), "")
		row = sup.message_row({"name": "m1", "from_staff": 1, "text": None, "sent_at": "2026-11-02 10:00:00"})
		self.assertEqual(row, {"id": "m1", "from_staff": True, "text": "", "sent_at": "2026-11-02 10:00:00"})


class TestIssueForm(unittest.TestCase):
	def test_subject_follows_the_bracket_convention_and_round_trips(self):
		self.assertEqual(sup.issue_subject("Mona Shelve", "Wrong length"), "[Mona Shelve] Wrong length")
		self.assertEqual(sup.split_issue_subject("[Mona Shelve] Wrong length"), ("Mona Shelve", "Wrong length"))
		self.assertEqual(sup.split_issue_subject("Plain issue"), (None, "Plain issue"))
		self.assertEqual(len(sup.issue_subject("A", "x" * 500)), 140)

	def test_description_holds_the_words_a_divider_and_the_diagnostics(self):
		diag = {"app": {"name": "Farm", "version": "1.0.6"}, "device": {"brand": "Honeywell", "model": "EDA52"}, "x": "<b>&</b>"}
		html = sup.issue_description("It <froze>\nsecond line", diag)
		self.assertIn("It &lt;froze&gt;<br>second line", html)
		self.assertIn("Diagnostic details (attached automatically)", html)
		self.assertNotIn("<b>", html)  # nothing from the phone is ever live HTML
		text, parsed = sup.parse_issue_description(html)
		self.assertEqual(text, "It <froze>\nsecond line")
		self.assertEqual(parsed, diag)

	def test_an_empty_description_says_so_and_reads_back_empty(self):
		html = sup.issue_description("  ", {})
		self.assertIn("(no description provided)", html)
		self.assertEqual(sup.parse_issue_description(html)[0], "")

	def test_descriptions_written_by_hand_or_damaged_do_not_break_the_reader(self):
		self.assertEqual(sup.parse_issue_description(None), ("", {}))
		self.assertEqual(sup.parse_issue_description("<p>just words</p>"), ("just words", {}))
		self.assertEqual(sup.parse_issue_description("<p>x</p><pre>{broken</pre>")[1], {})

	def test_the_issue_doctype_is_used_when_the_site_has_it(self):
		conf = MagicMock()
		with patch.object(sup.frappe, "conf", conf), patch.object(sup.frappe, "db", MagicMock()) as db:
			conf.get.return_value = None
			db.exists.return_value = "Issue"
			self.assertEqual(sup.support_doctype(), "Issue")
			db.exists.return_value = None
			self.assertEqual(sup.support_doctype(), "Mobile Report")
			db.exists.return_value = "Issue"
			conf.get.return_value = "Mobile Report"
			self.assertEqual(sup.support_doctype(), "Mobile Report")

	def test_an_issue_row_is_shaped_for_the_portal(self):
		desc = sup.issue_description("froze", {"app": {"version": "1.0.6"}, "device": {"brand": "Honeywell", "model": "EDA52", "platform": "android", "os_version": "30"}})
		row = sup._issue_row({"name": "ISS-2026-00214", "subject": "[Mona Shelve] Wrong length", "status": "Open", "raised_by": "a@b.c", "creation": "t", "description": desc}, {"ISS-2026-00214": "/private/files/x.jpg"})
		self.assertEqual((row["app"], row["subject"], row["app_version"], row["device_model"], row["os_version"]), ("Mona Shelve", "Wrong length", "1.0.6", "Honeywell EDA52", "android 30"))
		self.assertEqual(row["screenshot"], "/private/files/x.jpg")
		self.assertTrue(sup._is_issue("ISS-2026-00214"))
		self.assertFalse(sup._is_issue("MREP-00001"))


class TestIssueThreads(unittest.TestCase):
	issue = {"name": "ISS-1", "subject": "[Farm] Wrong length", "status": "Open", "creation": "2026-11-02 09:00:00", "description": sup.issue_description("60cm shows as 80cm", {})}
	comments = [
		{"name": "C1", "comment_email": "boss@x.y", "content": "<div class=\"ql-editor\"><p>Which bucket?</p></div>", "creation": "2026-11-02 09:30:00"},
		{"name": "C2", "comment_email": "a@b.c", "content": "<p>Bucket 12</p>", "creation": "2026-11-02 09:40:00"},
		{"name": "C3", "comment_email": "boss@x.y", "content": "<p>&nbsp;</p>", "creation": "2026-11-02 09:50:00"},
	]

	def test_plain_text_strips_html_and_keeps_line_breaks(self):
		self.assertEqual(sup.plain_text("<p>a &amp; b</p><p>c<br>d</p>"), "a & b\nc\nd")
		self.assertEqual(sup.plain_text(None), "")

	def test_ownership_accepts_the_user_id_or_the_email_only(self):
		self.assertTrue(sup.is_own_issue("a@b.c", "a@b.c", "a@b.c"))
		self.assertTrue(sup.is_own_issue("a@b.c", "ann", "a@b.c"))
		self.assertFalse(sup.is_own_issue("x@y.z", "a@b.c", "a@b.c"))
		self.assertFalse(sup.is_own_issue(None, "a@b.c", "a@b.c"))

	def test_a_conversation_starts_with_the_persons_words_then_each_real_comment(self):
		m = sup.thread_messages(self.issue, self.comments, "ann", "a@b.c")
		self.assertEqual([(x["text"], x["from_staff"]) for x in m], [("60cm shows as 80cm", False), ("Which bucket?", True), ("Bucket 12", False)])  # the empty comment is dropped

	def test_an_issue_with_no_description_falls_back_to_the_subject(self):
		m = sup.thread_messages({"name": "ISS-2", "subject": "[Farm] Slow scan", "description": None, "creation": "t"}, [], "ann", "a@b.c")
		self.assertEqual(m[0]["text"], "Slow scan")

	def test_the_list_row_shows_when_support_last_answered(self):
		row = sup.thread_summary(self.issue, self.comments, "ann", "a@b.c")
		self.assertEqual((row["id"], row["subject"], row["status"]), ("ISS-1", "Wrong length", "Open"))
		self.assertEqual(row["last_staff_at"], "2026-11-02 09:30:00")  # the blank staff comment does not count
		self.assertEqual(row["last_activity"], "2026-11-02 09:50:00")
		none = sup.thread_summary(self.issue, [], "ann", "a@b.c")
		self.assertIsNone(none["last_staff_at"])
		self.assertEqual(none["preview"], "60cm shows as 80cm")


class _Base(unittest.TestCase):
	def setUp(self):
		translate = patch.object(sup, "_", side_effect=lambda m: m)
		translate.start()
		self.addCleanup(translate.stop)


class TestAccess(_Base):
	def test_guests_cannot_report_or_chat(self):
		with patch.object(sup.frappe, "session", MagicMock(user="Guest")), patch.object(sup.frappe, "throw", side_effect=_throw):
			with self.assertRaises(frappe.AuthenticationError):
				sup.submit_report(app="Farm", subject="x")
			with self.assertRaises(frappe.AuthenticationError):
				sup.chat(app="Farm")

	def test_staff_tools_need_system_manager(self):
		with patch.object(sup.frappe, "get_roles", return_value=["Guest"]), patch.object(sup.frappe, "throw", side_effect=_throw):
			for call in (lambda: sup.reports(), lambda: sup.report("MREP-1"), lambda: sup.chat_threads(), lambda: sup.chat_thread("t"), lambda: sup.chat_reply("t", "hi"), lambda: sup.set_report_status("MREP-1", "Resolved")):
				with self.assertRaises(frappe.PermissionError):
					call()

	def test_a_report_needs_a_subject_and_a_known_app(self):
		with patch.object(sup.frappe, "session", MagicMock(user="a@b.c")), patch.object(sup.frappe, "throw", side_effect=_throw), patch.object(sup, "_over_limit", return_value=False):
			with patch.object(sup, "_monitored_app", return_value=None):
				with self.assertRaises(frappe.ValidationError):
					sup.submit_report(app="Farm", subject="x")
			with patch.object(sup, "_monitored_app", return_value=MagicMock(name="Farm")):
				with self.assertRaises(frappe.ValidationError):
					sup.submit_report(app="Farm", subject="   ")

	def test_the_report_rate_limit_applies(self):
		with patch.object(sup.frappe, "session", MagicMock(user="a@b.c")), patch.object(sup.frappe, "throw", side_effect=_throw), patch.object(sup, "_over_limit", return_value=True):
			with self.assertRaises(frappe.TooManyRequestsError):
				sup.submit_report(app="Farm", subject="x")

	def test_status_must_be_known(self):
		with patch.object(sup.frappe, "get_roles", return_value=["System Manager"]), patch.object(sup.frappe, "throw", side_effect=_throw):
			with self.assertRaises(frappe.ValidationError):
				sup.set_report_status("MREP-1", "Banana")
			with self.assertRaises(frappe.ValidationError):
				sup.set_report_status("ISS-2026-00001", "In Progress")  # not an Issue status


class TestChatFlow(_Base):
	def test_a_person_message_counts_for_support_and_a_reply_for_the_person(self):
		db = MagicMock()
		db.get_value.return_value = 0
		doc = MagicMock()
		doc.insert.return_value = MagicMock(as_dict=lambda: {"name": "m1"})
		with patch.object(sup.frappe, "db", db), patch.object(sup.frappe, "get_doc", return_value=doc), patch.object(sup, "now_datetime", return_value="NOW"):
			sup._add_message("T1", "a@b.c", "hello", False)
			self.assertEqual(db.set_value.call_args[0][2]["unread_staff"], 1)
			self.assertEqual(db.set_value.call_args[0][2]["last_preview"], "hello")
			sup._add_message("T1", "boss@x.y", "reply", True)
			self.assertEqual(db.set_value.call_args[0][2]["unread_user"], 1)


if __name__ == "__main__":
	unittest.main()
