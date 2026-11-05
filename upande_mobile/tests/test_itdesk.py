# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Unit tests for the IT dashboard's pure helpers (buckets, waiting, wording, roles). No site needed:
#   ../../env/bin/python -W ignore -m unittest upande_mobile.tests.test_itdesk

import unittest
from datetime import datetime

from upande_mobile.api import itdesk as it

NOW = datetime(2026, 11, 5, 12, 0, 0)


class TestBuckets(unittest.TestCase):
	def test_behind_ota_is_forgot(self):
		self.assertEqual(it.person_bucket("behind_ota"), "forgot")

	def test_needs_apk_is_cant_update(self):
		self.assertEqual(it.person_bucket("needs_apk"), "cant_update")

	def test_current_ahead_dormant_unknown(self):
		self.assertEqual(it.person_bucket("current"), "current")
		self.assertEqual(it.person_bucket("ahead"), "current")
		self.assertEqual(it.person_bucket("dormant"), "away")
		self.assertEqual(it.person_bucket("unknown"), "unknown")


class TestWaiting(unittest.TestCase):
	def test_person_wrote_last(self):
		self.assertEqual(it.wait_state("Open", "2026-11-04 10:00:00", "2026-11-03 10:00:00", "2026-11-01 00:00:00"), (True, "2026-11-04 10:00:00"))

	def test_staff_answered_last(self):
		self.assertEqual(it.wait_state("Replied", "2026-11-03 10:00:00", "2026-11-04 10:00:00", "2026-11-01 00:00:00"), (False, None))

	def test_never_answered_waits_since_creation(self):
		self.assertEqual(it.wait_state("Open", None, None, "2026-11-01 00:00:00"), (True, "2026-11-01 00:00:00"))

	def test_resolved_never_waits(self):
		self.assertEqual(it.wait_state("Resolved", "2026-11-04 10:00:00", None, "2026-11-01"), (False, None))


class TestWording(unittest.TestCase):
	def test_reminder_text(self):
		title, text = it.reminder_text("Sales", "forgot", "2.1.0")
		self.assertIn("Sales", title)
		self.assertIn("2.1.0", text)
		self.assertIn("reopen", text)
		self.assertIn("install", it.reminder_text("Sales", "cant_update", "2.1.0")[1])

	def test_cooldown(self):
		self.assertTrue(it.reminder_allowed(None, NOW))
		self.assertFalse(it.reminder_allowed("2026-11-05 03:00:00", NOW))
		self.assertTrue(it.reminder_allowed("2026-11-04 03:00:00", NOW))
		self.assertTrue(it.reminder_allowed("2026-11-01 03:00:00", NOW, 72))

	def test_days_between(self):
		self.assertEqual(it.days_between("2026-11-01 00:00:00", NOW), 4)
		self.assertIsNone(it.days_between(None, NOW))

	def test_problem_view_hides_developer_fields(self):
		row = {"name": "x", "app": "A", "kind": "crash", "users_affected": 3, "title": "TypeError", "culprit": "f.js"}
		plain = it.problem_view(row, False)
		self.assertNotIn("developer", plain)
		self.assertEqual(plain["impact"], "blocking")
		self.assertEqual(plain["what"], "The app closed unexpectedly")
		self.assertEqual(it.problem_view(row, True)["developer"]["culprit"], "f.js")


class TestRoles(unittest.TestCase):
	def test_access(self):
		self.assertTrue(it.can_use(["System Manager"]))
		self.assertTrue(it.can_use(["IT Support"]))
		self.assertFalse(it.can_use(["Employee"]))
		self.assertTrue(it.is_developer(["System Manager"]))
		self.assertFalse(it.is_developer(["IT Support"]))


if __name__ == "__main__":
	unittest.main()
