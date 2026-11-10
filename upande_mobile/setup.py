# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Who may open the Mobile doctypes in Desk, and what they see.
#
# The IT team should know the doctypes (Mobile App, Mobile App Install, Mobile Issue, Mobile Event, Mobile Report,
# Mobile Reminder), but never the backend addresses inside them: the GitHub repository, the OTA and APK addresses, the
# addresses of the app's own API calls. So the doctypes grant read to the "Mobile Viewer" role at permission level 0, and
# every field that holds such an address sits at permission level 1, which only System Manager is granted. A field at a
# level a user cannot read is left out of the form, the list and report views, the API and exports, not merely hidden.
#
# The role is created before the doctypes are imported (a doctype's permission row links to it), and the IT Team role
# profile is given it afterwards, so nobody has to assign it by hand.

import frappe

VIEWER_ROLE = "Mobile Viewer"
IT_PROFILE = "IT Team"


def ensure_viewer_role():
	"""Create the Mobile Viewer role. Idempotent; runs before the doctypes are imported."""
	if not frappe.db.exists("Role", VIEWER_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": VIEWER_ROLE, "desk_access": 1}).insert(ignore_permissions=True)


def give_it_team_the_role():
	"""Add Mobile Viewer to the IT Team role profile, if the site has one and it lacks it. Idempotent."""
	if not (frappe.db.exists("Role", VIEWER_ROLE) and frappe.db.exists("Role Profile", IT_PROFILE)):
		return
	doc = frappe.get_doc("Role Profile", IT_PROFILE)
	if VIEWER_ROLE in [r.role for r in doc.roles]:
		return
	doc.append("roles", {"role": VIEWER_ROLE})
	doc.flags.ignore_permissions = True
	doc.save()
