# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Role management for the IT dashboard: add roles, and add a role to a role profile. Like user management, it is for
# the IT Team role profile only and saves with `ignore_permissions` after its own checks, so the IT team never needs
# System Manager (which would also open Server Scripts, Client Scripts and the rest of the code).
#
# Guard rails: System Manager, Script Manager, Administrator, Guest and All are never listed, created or handed out
# from here, and a role cannot be added to a role profile if it is one of those. Removing roles from a profile and
# deleting roles are not offered.
#
# Pure helpers are separate from the database code so they are unit tested.

import frappe
from frappe import _
from frappe.utils import cint

from upande_mobile.api import usermgmt as um

HIDDEN_ROLES = tuple(um.FORBIDDEN_ROLES)
MAX_NAME = 60


def is_manageable(role):
	"""A role the IT team may see and hand out: anything but the system and code roles."""
	return bool(role) and role not in HIDDEN_ROLES


def clean_role_name(name):
	"""A new role's name, trimmed, or None when it is blank, too long or has characters that break links."""
	name = " ".join((name or "").split())
	if not name or len(name) > MAX_NAME or any(c in name for c in '<>"\\/'):
		return None
	return name


def _require():
	um._require_admin()


@frappe.whitelist(methods=["GET"])
def roles(search=None):
	"""Every role the IT team can manage, with who holds it and which role profiles include it."""
	_require()
	term = (search or "").strip().lower()
	people = {r.role: r.n for r in frappe.db.sql("SELECT role, COUNT(*) AS n FROM `tabHas Role` WHERE parenttype = 'User' GROUP BY role", as_dict=True)}
	in_profiles = {}
	for r in frappe.get_all("Has Role", filters={"parenttype": "Role Profile"}, fields=["parent", "role"], order_by="parent asc", limit_page_length=0):
		in_profiles.setdefault(r.role, []).append(r.parent)
	rows = []
	for r in frappe.get_all("Role", fields=["name", "desk_access", "disabled"], order_by="name asc", limit_page_length=0):
		if not is_manageable(r.name) or (term and term not in r.name.lower()):
			continue
		rows.append(
			{
				"name": r.name,
				"desk_access": cint(r.desk_access),
				"disabled": cint(r.disabled),
				"people": people.get(r.name, 0),
				"profiles": in_profiles.get(r.name, []),
			}
		)
	return {"rows": rows, "total": len(rows)}


@frappe.whitelist(methods=["GET"])
def profiles(search=None):
	"""Every role profile with its roles. The system roles inside a profile are shown as a count, not by name."""
	_require()
	term = (search or "").strip().lower()
	held = {}
	for r in frappe.get_all("Has Role", filters={"parenttype": "Role Profile"}, fields=["parent", "role"], limit_page_length=0):
		held.setdefault(r.parent, []).append(r.role)
	people = {r.role_profile: r.n for r in frappe.db.sql("SELECT role_profile, COUNT(*) AS n FROM `tabUser Role Profile` GROUP BY role_profile", as_dict=True)}
	rows = []
	for name in frappe.get_all("Role Profile", pluck="name", order_by="name asc", limit_page_length=0):
		if term and term not in name.lower():
			continue
		mine = held.get(name, [])
		rows.append(
			{
				"name": name,
				"roles": sorted(r for r in mine if is_manageable(r)),
				"system_roles": sum(1 for r in mine if not is_manageable(r)),
				"people": people.get(name, 0),
			}
		)
	return {"rows": rows, "total": len(rows)}


@frappe.whitelist(methods=["POST"])
def create_role(role_name, desk_access=1):
	"""Add a role. It does nothing until it is put in a role profile and given permissions."""
	_require()
	name = clean_role_name(role_name)
	if not name:
		frappe.throw(_("Give the role a name of up to {0} characters, without symbols like < > / \\.").format(MAX_NAME), frappe.ValidationError)
	if not is_manageable(name):
		frappe.throw(_("That role name is reserved."), frappe.PermissionError)
	if frappe.db.exists("Role", name):
		frappe.throw(_("A role called {0} already exists.").format(name), frappe.ValidationError)
	doc = frappe.get_doc({"doctype": "Role", "role_name": name, "desk_access": 1 if cint(desk_access) else 0})
	doc.insert(ignore_permissions=True)
	frappe.logger("it_user_admin").info(f"{frappe.session.user} created role {name}")
	return {"name": doc.name}


@frappe.whitelist(methods=["POST"])
def add_role_to_profile(role_profile, role):
	"""Add a role to a role profile. Everyone on that profile gets it (Frappe updates them in the background)."""
	_require()
	if not frappe.db.exists("Role Profile", role_profile):
		frappe.throw(_("Unknown role profile."), frappe.DoesNotExistError)
	if not is_manageable(role) or not frappe.db.exists("Role", role):
		frappe.throw(_("{0} cannot be given from here.").format(role or _("That role")), frappe.PermissionError)
	if frappe.db.get_value("Role", role, "disabled"):
		frappe.throw(_("{0} is disabled.").format(role), frappe.ValidationError)
	doc = frappe.get_doc("Role Profile", role_profile)
	if role in [r.role for r in doc.roles]:
		frappe.throw(_("{0} already includes {1}.").format(role_profile, role), frappe.ValidationError)
	doc.append("roles", {"role": role})
	doc.flags.ignore_permissions = True
	doc.save()
	people = frappe.db.count("User Role Profile", {"role_profile": role_profile})
	frappe.logger("it_user_admin").info(f"{frappe.session.user} added role {role} to profile {role_profile}")
	return {"role_profile": role_profile, "role": role, "people": people}
