# Which fields only System Manager may see in the Mobile doctypes, and that the IT-facing role never gets them.
# Reads the doctype JSON files, so it needs no site:
#   ../../env/bin/python -W ignore -m unittest upande_mobile.tests.test_desk_permissions

import glob
import json
import os
import unittest

import upande_mobile.hooks as hooks

DOCTYPES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "upande_mobile", "doctype")

# Fields that hold a backend address (or text that carries one): the GitHub repo, the OTA and APK addresses, the app's own
# API calls, and the crash payloads that include them.
HIDDEN = {
	"Mobile App": {"github_repo", "ota_base_url", "latest_apk_url", "github_error", "github_data", "last_error"},
	"Mobile Event": {"req_url", "req_error", "request", "breadcrumbs", "stack"},
	"Mobile Issue": {"culprit"},
	"Mobile Report": {"diagnostics", "last_crash"},
}
VIEWABLE = {"Mobile App", "Mobile App Install", "Mobile Issue", "Mobile Event", "Mobile Report", "Mobile Reminder"}


def _doctypes():
	out = {}
	for path in glob.glob(os.path.join(DOCTYPES, "*", "*.json")):
		with open(path) as fh:
			j = json.load(fh)
		if j.get("doctype") == "DocType":
			out[j["name"]] = j
	return out


class TestDeskPermissions(unittest.TestCase):
	def test_every_address_field_is_at_permission_level_1(self):
		dts = _doctypes()
		for name, fields in HIDDEN.items():
			by_name = {f["fieldname"]: f for f in dts[name]["fields"]}
			for f in fields:
				self.assertEqual(by_name[f].get("permlevel"), 1, f"{name}.{f} must be at permission level 1")

	def test_only_system_manager_is_granted_level_1(self):
		for name, j in _doctypes().items():
			level1 = [p for p in j["permissions"] if p.get("permlevel", 0) == 1]
			if name in HIDDEN:
				self.assertEqual([p["role"] for p in level1], ["System Manager"], name)
				self.assertEqual(level1[0]["read"], 1)
			else:
				self.assertEqual(level1, [], name)

	def test_the_viewer_role_reads_but_never_writes_or_sees_level_1(self):
		dts = _doctypes()
		for name in VIEWABLE:
			rows = [p for p in dts[name]["permissions"] if p["role"] == "Mobile Viewer"]
			self.assertEqual(len(rows), 1, name)
			row = rows[0]
			self.assertEqual((row["read"], row["report"]), (1, 1), name)
			self.assertEqual((row["write"], row["create"], row["delete"], row["export"]), (0, 0, 0, 0), name)
			self.assertEqual(row.get("permlevel", 0), 0, name)

	def test_chat_and_push_tokens_are_not_opened_to_the_viewer(self):
		for name in ("Mobile Chat Thread", "Mobile Chat Message", "Mobile Push Token"):
			roles = [p["role"] for p in _doctypes()[name]["permissions"]]
			self.assertNotIn("Mobile Viewer", roles, name)

	def test_the_role_exists_before_the_doctypes_are_imported(self):
		self.assertIn("upande_mobile.setup.ensure_viewer_role", hooks.before_migrate)
		self.assertIn("upande_mobile.setup.give_it_team_the_role", hooks.after_migrate)


if __name__ == "__main__":
	unittest.main()
