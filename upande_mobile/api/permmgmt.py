# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Doctype permissions and user permissions for the IT dashboard: the Role Permissions Manager and the User
# Permissions list, for the IT Team role profile instead of System Manager.
#
# Doctype permissions decide which roles can read, write, create ... a kind of document. Because that can be turned
# into more access (give a role write access to Server Script and anyone holding it can run code), these guard rails
# apply:
#
#   * Doctypes that hold code, users, roles or permissions themselves (DENIED_DOCTYPES) cannot be edited from here.
#   * Rules for System Manager, Script Manager, Administrator, Guest and All are shown but cannot be changed, and no
#     rule can be added for them.
#   * Only the permission types listed in PERM_TYPES can be set (`impersonate`, for one, cannot).
#   * Removing the last rule of a doctype is refused, as in Desk.
#
# User permissions only ever narrow what a person sees, so they carry fewer rules: the person must be someone IT may
# manage (not an Administrator or someone with a system role).
#
# Pure helpers are separate from the database code so they are unit tested.

import contextlib

import frappe
from frappe import _
from frappe.utils import cint

from upande_mobile.api import rolemgmt, usermgmt as um

PERM_TYPES = ("select", "read", "write", "create", "delete", "submit", "cancel", "amend", "report", "export", "import", "share", "print", "email", "mask")
SUBMIT_TYPES = ("submit", "cancel", "amend")
DENIED_DOCTYPES = frozenset(
	{
		"Server Script", "Client Script", "Custom Script", "DocType", "DocField", "DocPerm", "Custom DocPerm", "Custom Field",
		"Property Setter", "Customize Form", "Role", "Has Role", "Role Profile", "User Role Profile", "User", "Role Permission for Page and Report",
		"Report", "Page", "Print Format", "Web Page", "Web Form", "Website Settings", "Website Script", "Workflow", "Notification",
		"Webhook", "System Settings", "Scheduled Job Type", "Patch Log", "Module Def", "Installed Applications", "Email Account",
		"Console Log", "OAuth Client", "OAuth Provider Settings", "Social Login Key", "Connected App", "Dashboard Chart Source",
		"Workspace", "Custom HTML Block", "Navbar Settings", "Package", "Package Import", "Data Import", "Deleted Document",
		"Access Log", "Permission Log", "Server Script Log",
	}
)
MAX_ROWS = 1000


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #


def doctype_allowed(name):
	return bool(name) and name not in DENIED_DOCTYPES


def clean_ptype(ptype):
	return ptype if ptype in PERM_TYPES else None


def rule_locked(role):
	"""A rule is read-only here when its role is a system or code role."""
	return not rolemgmt.is_manageable(role)


@contextlib.contextmanager
def _bypass():
	"""Save without the caller's permissions for the length of one change (the caller was already checked)."""
	before = frappe.flags.ignore_permissions
	frappe.flags.ignore_permissions = True
	try:
		yield
	finally:
		frappe.flags.ignore_permissions = before


def _require():
	um._require_admin()


# --------------------------------------------------------------------------- #
# doctype permissions
# --------------------------------------------------------------------------- #


@frappe.whitelist(methods=["GET"])
def doctypes(search=None):
	"""Doctypes whose permissions can be edited from here, for the picker."""
	_require()
	term = (search or "").strip()
	filters = {"istable": 0, "name": ["not in", sorted(DENIED_DOCTYPES)]}
	or_filters = [["name", "like", f"%{term}%"], ["module", "like", f"%{term}%"]] if term else None
	rows = frappe.get_all("DocType", filters=filters, or_filters=or_filters, fields=["name", "module"], order_by="name asc", limit_page_length=60)
	return {"rows": [{"name": r.name, "module": r.module} for r in rows]}


def _rules_for(doctype):
	"""(rows, customised): the doctype's rules, from Custom DocPerm when it has been customised, else the standard ones."""
	rows = frappe.get_all("Custom DocPerm", filters={"parent": doctype}, fields="*", order_by="permlevel asc, role asc", limit_page_length=0)
	if rows:
		return rows, True
	return frappe.get_all("DocPerm", filters={"parent": doctype}, fields="*", order_by="permlevel asc, role asc", limit_page_length=0), False


@frappe.whitelist(methods=["GET"])
def rules(doctype):
	"""The permission rules of one doctype, and the roles a new rule could be for."""
	_require()
	_check_doctype(doctype)
	rows, customised = _rules_for(doctype)
	meta = frappe.get_meta(doctype)
	return {
		"doctype": doctype,
		"customised": customised,
		"submittable": bool(meta.is_submittable),
		"types": list(PERM_TYPES),
		"rules": [
			{
				"role": r.role,
				"permlevel": cint(r.permlevel),
				"if_owner": cint(r.if_owner),
				"locked": rule_locked(r.role),
				**{t: cint(r.get(t)) for t in PERM_TYPES},
			}
			for r in rows
		],
		"roles": sorted(r.name for r in frappe.get_all("Role", filters={"disabled": 0}, fields=["name"], limit_page_length=0) if rolemgmt.is_manageable(r.name)),
	}


def _check_doctype(doctype):
	if not doctype or not frappe.db.exists("DocType", doctype):
		frappe.throw(_("Unknown doctype."), frappe.DoesNotExistError)
	if not doctype_allowed(doctype):
		frappe.throw(_("{0} holds code, users or permissions, so its permissions are changed in Desk.").format(doctype), frappe.PermissionError)


def _check_role(role):
	if not role or not frappe.db.exists("Role", role):
		frappe.throw(_("Unknown role."), frappe.DoesNotExistError)
	if rule_locked(role):
		frappe.throw(_("{0} is a system role, so its permissions cannot be changed from here.").format(role), frappe.PermissionError)


def _custom_rule(doctype, role, permlevel, if_owner):
	return frappe.db.get_value("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": cint(permlevel), "if_owner": cint(if_owner)})


def _validate(doctype):
	from frappe.core.doctype.doctype.doctype import validate_permissions_for_doctype

	validate_permissions_for_doctype(doctype)


@frappe.whitelist(methods=["POST"])
def set_permission(doctype, role, permlevel, ptype, value, if_owner=0):
	"""Turn one permission (read, write, ...) on or off for a role on a doctype."""
	_require()
	_check_doctype(doctype)
	_check_role(role)
	ptype = clean_ptype(ptype)
	if not ptype:
		frappe.throw(_("That permission cannot be set from here."), frappe.PermissionError)
	on = 1 if cint(value) else 0
	if_owner = 1 if cint(if_owner) else 0
	if ptype in SUBMIT_TYPES and not frappe.get_meta(doctype).is_submittable:
		frappe.throw(_("{0} cannot be submitted, so it has no {1} permission.").format(doctype, ptype), frappe.ValidationError)
	if ptype == "report" and on and if_owner:
		frappe.throw(_("Report cannot be set on a rule that applies only to the creator."), frappe.ValidationError)
	with _bypass():
		from frappe.permissions import setup_custom_perms

		setup_custom_perms(doctype)
		name = _custom_rule(doctype, role, permlevel, if_owner)
		if not name:
			frappe.throw(_("That rule does not exist."), frappe.DoesNotExistError)
		doc = frappe.get_doc("Custom DocPerm", name)
		doc.set(ptype, on)
		if ptype == "if_owner" and on:
			doc.report = 0
		doc.save(ignore_permissions=True)
		_validate(doctype)
	frappe.logger("it_user_admin").info(f"{frappe.session.user} set {ptype}={on} for {role} on {doctype}")
	return rules(doctype)


@frappe.whitelist(methods=["POST"])
def add_rule(doctype, role, permlevel=0):
	"""Give a role a new rule on a doctype (read only to begin with, at the permission level given)."""
	_require()
	_check_doctype(doctype)
	_check_role(role)
	permlevel = cint(permlevel)
	if not 0 <= permlevel <= 9:
		frappe.throw(_("The permission level must be between 0 and 9."), frappe.ValidationError)
	with _bypass():
		from frappe.permissions import setup_custom_perms

		setup_custom_perms(doctype)
		if _custom_rule(doctype, role, permlevel, 0):
			frappe.throw(_("{0} already has a rule at level {1} on {2}.").format(role, permlevel, doctype), frappe.ValidationError)
		rule = frappe.new_doc("Custom DocPerm")
		rule.update({"parent": doctype, "parenttype": "DocType", "parentfield": "permissions", "role": role, "permlevel": permlevel, "read": 1})
		rule.insert(ignore_permissions=True)
		_validate(doctype)
	frappe.logger("it_user_admin").info(f"{frappe.session.user} added a rule for {role} on {doctype}")
	return rules(doctype)


@frappe.whitelist(methods=["POST"])
def remove_rule(doctype, role, permlevel=0, if_owner=0):
	"""Take a role's rule away from a doctype."""
	_require()
	_check_doctype(doctype)
	_check_role(role)
	with _bypass():
		from frappe.permissions import setup_custom_perms

		setup_custom_perms(doctype)
		name = _custom_rule(doctype, role, permlevel, if_owner)
		if not name:
			frappe.throw(_("That rule does not exist."), frappe.DoesNotExistError)
		if frappe.db.count("Custom DocPerm", {"parent": doctype}) <= 1:
			frappe.throw(_("A doctype needs at least one permission rule."), frappe.ValidationError)
		frappe.delete_doc("Custom DocPerm", name, ignore_permissions=True, force=True)
		frappe.clear_cache(doctype=doctype)
	frappe.logger("it_user_admin").info(f"{frappe.session.user} removed the rule for {role} on {doctype}")
	return rules(doctype)


@frappe.whitelist(methods=["POST"])
def reset_rules(doctype):
	"""Put a doctype's rules back to the ones it shipped with. Rules for system roles go back too."""
	_require()
	_check_doctype(doctype)
	with _bypass():
		from frappe.permissions import reset_perms

		reset_perms(doctype)
		frappe.clear_cache(doctype=doctype)
	frappe.logger("it_user_admin").info(f"{frappe.session.user} reset the rules of {doctype}")
	return rules(doctype)


# --------------------------------------------------------------------------- #
# user permissions
# --------------------------------------------------------------------------- #


def _manageable_user(user):
	"""Raise unless IT may change this person's user permissions."""
	if not user or not frappe.db.exists("User", user):
		frappe.throw(_("Unknown user."), frappe.DoesNotExistError)
	reason = um.lock_reason(user, user in um._forbidden_holders([user]), um._is_sm())
	if reason:
		frappe.throw(_(reason), frappe.PermissionError)


def _perm_row(r, names, locked_users):
	return {
		"name": r.name,
		"user": r.user,
		"full_name": names.get(r.user) or r.user,
		"allow": r.allow,
		"for_value": r.for_value,
		"all_doctypes": cint(r.apply_to_all_doctypes),
		"applicable_for": r.applicable_for or "",
		"is_default": cint(r.is_default),
		"hide_descendants": cint(r.hide_descendants),
		"locked": r.user in locked_users,
	}


@frappe.whitelist(methods=["GET"])
def user_permissions(search=None, allow=None):
	"""The user permissions on the site, newest first. `search` matches the person, the doctype or the value."""
	_require()
	filters = {"allow": allow} if allow else {}
	term = (search or "").strip()[:100]
	or_filters = [["user", "like", f"%{term}%"], ["allow", "like", f"%{term}%"], ["for_value", "like", f"%{term}%"]] if term else None
	rows = frappe.get_all(
		"User Permission", filters=filters, or_filters=or_filters,
		fields=["name", "user", "allow", "for_value", "apply_to_all_doctypes", "applicable_for", "is_default", "hide_descendants", "creation"],
		order_by="creation desc", limit_page_length=MAX_ROWS,
	)
	users = list({r.user for r in rows})
	names = {u.name: u.full_name for u in frappe.get_all("User", filters={"name": ["in", users or [""]]}, fields=["name", "full_name"], limit_page_length=0)}
	holders = um._forbidden_holders(users)
	caller_sm = um._is_sm()
	locked = {u for u in users if um.lock_reason(u, u in holders, caller_sm)}
	return {"rows": [_perm_row(r, names, locked) for r in rows], "total": len(rows)}


@frappe.whitelist(methods=["GET"])
def linkable_doctypes(search=None):
	"""Doctypes a user permission can be set on (anything that can be linked to), for the picker."""
	_require()
	term = (search or "").strip()
	or_filters = [["name", "like", f"%{term}%"]] if term else None
	rows = frappe.get_all("DocType", filters={"istable": 0, "issingle": 0}, or_filters=or_filters, fields=["name", "module"], order_by="name asc", limit_page_length=60)
	return {"rows": [{"name": r.name, "module": r.module} for r in rows]}


@frappe.whitelist(methods=["GET"])
def link_values(doctype, search=None):
	"""Records of a doctype to choose from as the value of a user permission."""
	_require()
	if not doctype or not frappe.db.exists("DocType", doctype) or frappe.get_meta(doctype).istable:
		frappe.throw(_("Unknown doctype."), frappe.DoesNotExistError)
	term = (search or "").strip()
	or_filters = [["name", "like", f"%{term}%"]] if term else None
	names = frappe.get_all(doctype, or_filters=or_filters, pluck="name", order_by="modified desc", limit_page_length=30)
	return {"rows": names}


@frappe.whitelist(methods=["GET"])
def applicable_doctypes(allow):
	"""The doctypes a user permission on `allow` can be limited to: itself and every doctype that links to it."""
	_require()
	if not allow or not frappe.db.exists("DocType", allow):
		frappe.throw(_("Unknown doctype."), frappe.DoesNotExistError)
	linked = frappe.db.sql(
		"SELECT DISTINCT parent FROM `tabDocField` WHERE fieldtype = 'Link' AND options = %s "
		"UNION SELECT DISTINCT dt FROM `tabCustom Field` WHERE fieldtype = 'Link' AND options = %s",
		(allow, allow),
		pluck=True,
	)
	names = sorted({allow, *[d for d in linked if d and frappe.db.exists("DocType", d) and not frappe.get_meta(d).istable]})
	return {"rows": names}


@frappe.whitelist(methods=["POST"])
def add_user_permission(user, allow, for_value, apply_to_all_doctypes=1, applicable_for=None, is_default=0):
	"""Limit a person to one record (a company, a farm ...) of a doctype."""
	_require()
	_manageable_user(user)
	if not allow or not frappe.db.exists("DocType", allow):
		frappe.throw(_("Choose the doctype to restrict."), frappe.ValidationError)
	if not for_value or not frappe.db.exists(allow, for_value):
		frappe.throw(_("{0} does not have a record called {1}.").format(allow, for_value or ""), frappe.ValidationError)
	all_doctypes = 1 if cint(apply_to_all_doctypes) else 0
	if not all_doctypes and not applicable_for:
		frappe.throw(_("Choose the doctype it applies to, or apply it to all doctypes."), frappe.ValidationError)
	doc = frappe.get_doc(
		{
			"doctype": "User Permission", "user": user, "allow": allow, "for_value": for_value,
			"apply_to_all_doctypes": all_doctypes, "applicable_for": None if all_doctypes else applicable_for,
			"is_default": 1 if cint(is_default) else 0,
		}
	)
	doc.insert(ignore_permissions=True)
	frappe.logger("it_user_admin").info(f"{frappe.session.user} set a user permission {allow}={for_value} for {user}")
	return {"name": doc.name}


@frappe.whitelist(methods=["POST"])
def remove_user_permission(name):
	"""Remove a user permission."""
	_require()
	row = frappe.db.get_value("User Permission", name, ["user", "allow", "for_value"], as_dict=True)
	if not row:
		frappe.throw(_("Unknown user permission."), frappe.DoesNotExistError)
	_manageable_user(row.user)
	frappe.delete_doc("User Permission", name, ignore_permissions=True, force=True)
	frappe.logger("it_user_admin").info(f"{frappe.session.user} removed the user permission {row.allow}={row.for_value} of {row.user}")
	return {"removed": name}
