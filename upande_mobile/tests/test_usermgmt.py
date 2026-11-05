# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import unittest

from upande_mobile.api import usermgmt as um


class TestLockReason(unittest.TestCase):
	def test_system_accounts_are_locked(self):
		self.assertTrue(um.lock_reason("Administrator", False, True))
		self.assertTrue(um.lock_reason("Guest", False, True))

	def test_people_with_system_roles_are_locked_for_everyone(self):
		self.assertTrue(um.lock_reason("a@b.com", True, True))
		self.assertTrue(um.lock_reason("a@b.com", True, False))

	def test_developer_accounts_need_a_system_manager(self):
		self.assertTrue(um.lock_reason("dev@upande.com", False, False))
		self.assertIsNone(um.lock_reason("dev@upande.com", False, True))

	def test_ordinary_people_are_open(self):
		self.assertIsNone(um.lock_reason("clerk@farm.co.ke", False, False))


class TestProfiles(unittest.TestCase):
	def test_profile_with_a_system_role_is_not_assignable(self):
		self.assertFalse(um.profile_is_assignable(["Stock User", "System Manager"]))
		self.assertFalse(um.profile_is_assignable(["Script Manager"]))
		self.assertTrue(um.profile_is_assignable(["Stock User", "Sales User"]))
		self.assertTrue(um.profile_is_assignable([]))

	def test_profile_carrying_the_user_admin_role_is_not_assignable(self):
		self.assertFalse(um.profile_is_assignable(["IT User Admin"]))

	def test_clean_names(self):
		self.assertEqual(um.clean_names('["A", " B ", "A", ""]'), ["A", "B"])
		self.assertEqual(um.clean_names(["A", None and "x" or "A"]), ["A"])
		self.assertEqual(um.clean_names(""), [])
		self.assertEqual(um.clean_names(None), [])

	def test_split_name(self):
		self.assertEqual(um.split_name("Jane Wanjiru Doe"), ("Jane", "Wanjiru Doe"))
		self.assertEqual(um.split_name("Jane"), ("Jane", ""))
