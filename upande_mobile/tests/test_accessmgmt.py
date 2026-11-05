# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt

import unittest

from upande_mobile.api import permmgmt as pm
from upande_mobile.api import rolemgmt as rm


class TestRoles(unittest.TestCase):
	def test_system_and_code_roles_are_not_manageable(self):
		for role in ("System Manager", "Script Manager", "Administrator", "Guest", "All"):
			self.assertFalse(rm.is_manageable(role))
		self.assertTrue(rm.is_manageable("Stock User"))
		self.assertFalse(rm.is_manageable(""))
		self.assertFalse(rm.is_manageable(None))

	def test_clean_role_name(self):
		self.assertEqual(rm.clean_role_name("  Stores   Clerk "), "Stores Clerk")
		self.assertIsNone(rm.clean_role_name(""))
		self.assertIsNone(rm.clean_role_name("a" * 61))
		self.assertIsNone(rm.clean_role_name("Bad<Role>"))
		self.assertIsNone(rm.clean_role_name("a/b"))


class TestDoctypePermissions(unittest.TestCase):
	def test_code_and_permission_doctypes_are_denied(self):
		for dt in ("Server Script", "Client Script", "DocType", "Custom DocPerm", "User", "Role", "Has Role", "Role Profile", "System Settings"):
			self.assertFalse(pm.doctype_allowed(dt), dt)
		self.assertTrue(pm.doctype_allowed("Sales Order"))
		self.assertFalse(pm.doctype_allowed(""))

	def test_only_listed_permission_types_can_be_set(self):
		self.assertEqual(pm.clean_ptype("write"), "write")
		self.assertEqual(pm.clean_ptype("mask"), "mask")
		self.assertIsNone(pm.clean_ptype("impersonate"))
		self.assertIsNone(pm.clean_ptype("if_owner"))
		self.assertIsNone(pm.clean_ptype("role"))

	def test_rules_for_system_roles_are_locked(self):
		self.assertTrue(pm.rule_locked("System Manager"))
		self.assertTrue(pm.rule_locked("Script Manager"))
		self.assertFalse(pm.rule_locked("Stock User"))
