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
