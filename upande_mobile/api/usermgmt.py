# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# User management for the IT dashboard: add, edit, disable and delete users, and give them role profiles,
# WITHOUT the person being a System Manager.
#
# Why this exists: managing users and roles in Desk needs System Manager, and System Manager also opens Server
# Scripts, Client Scripts and the rest of the code. So the IT team gets the narrow role "IT User Admin" instead,
# and every change goes through this module, which checks the caller itself and then saves with
# `ignore_permissions`. The guard rails below are what make that safe:
#
#   * Access is System Manager or "IT User Admin", nothing else.
#   * Roles that open code or run the system (System Manager, Script Manager, Administrator ...) can never be
#     granted from here, and a role profile that contains one cannot be assigned from here either (a profile is
#     only a bundle of roles, so assigning it would grant them).
#   * A person who already holds one of those roles is read-only here, for everyone: they are managed in Desk.
#   * Someone who is not a System Manager also cannot touch accounts on the Upande domain (the developers).
#   * You cannot disable or delete your own account, nor Administrator or Guest.
#
# In v16 a user's roles come from their role profiles (Frappe rebuilds the role list from them on every save), so
# this module only ever sets profiles, never individual roles.
#
# Pure helpers are separate from the database code so they are unit tested.

import frappe
from frappe import _
from frappe.utils import cint

USER_ADMIN_ROLE = "IT User Admin"
FORBIDDEN_ROLES = ("System Manager", "Script Manager", "Administrator", "Guest", "All")
PROTECTED_USERS = ("Administrator", "Guest")
DEVELOPER_DOMAIN = "upande"
MAX_ROWS = 1000


# --------------------------------------------------------------------------- #
# pure helpers
# --------------------------------------------------------------------------- #


def lock_reason(user, holds_forbidden, caller_is_sm, caller=None):
	"""Why `user` cannot be changed from here, or None when it can. `holds_forbidden`: the person already has a
	code or system role."""
	if user in PROTECTED_USERS:
		return "This is a system account."
	if holds_forbidden:
		return "This person holds a system role. Manage them in Desk."
	if not caller_is_sm and DEVELOPER_DOMAIN in (user or "").lower():
		return "Developer accounts can only be changed by a System Manager."
	return None


def profile_is_assignable(roles):
	"""A role profile can be handed out from here only when none of its roles is a code or system role."""
	return not any(r in FORBIDDEN_ROLES for r in roles or [])


def clean_names(values):
	"""A list of names from a JSON string or list: stripped, no blanks, no repeats, order kept."""
	if isinstance(values, str):
		values = frappe.parse_json(values) if values.strip() else []
	return list(dict.fromkeys(str(v).strip() for v in (values or []) if str(v).strip()))


def split_name(full_name):
	parts = (full_name or "").strip().split(" ", 1)
	return parts[0], (parts[1].strip() if len(parts) > 1 else "")


# --------------------------------------------------------------------------- #
# access
# --------------------------------------------------------------------------- #


def can_manage(roles=None):
	roles = roles if roles is not None else frappe.get_roles()
	return "System Manager" in roles or USER_ADMIN_ROLE in roles


def _is_sm():
	return "System Manager" in frappe.get_roles()


def _require_admin():
	if not can_manage():
		frappe.throw(_("User management is for IT User Admins and System Managers only."), frappe.PermissionError)


def ensure_role():
	"""Create the IT User Admin role (idempotent, run after migrate)."""
	if not frappe.db.exists("Role", USER_ADMIN_ROLE):
		frappe.get_doc({"doctype": "Role", "role_name": USER_ADMIN_ROLE, "desk_access": 1}).insert(ignore_permissions=True)


# --------------------------------------------------------------------------- #
# reading
# --------------------------------------------------------------------------- #


def _profile_roles():
	"""{profile: [roles]} for every role profile."""
	out = {}
	for r in frappe.get_all("Has Role", filters={"parenttype": "Role Profile"}, fields=["parent", "role"], limit_page_length=0):
		out.setdefault(r.parent, []).append(r.role)
	return out


def _forbidden_holders(users=None):
	filters = {"parenttype": "User", "role": ["in", list(FORBIDDEN_ROLES)]}
	if users is not None:
		filters["parent"] = ["in", users or [""]]
	return {r.parent for r in frappe.get_all("Has Role", filters=filters, fields=["parent"], limit_page_length=0)}


def _profiles_of(users):
	out = {}
	if not users:
		return out
	for r in frappe.get_all("User Role Profile", filters={"parenttype": "User", "parent": ["in", users]}, fields=["parent", "role_profile"], order_by="idx asc", limit_page_length=0):
		out.setdefault(r.parent, []).append(r.role_profile)
	return out


def _row(u, profiles, locked):
	return {
		"name": u.name,
		"full_name": u.full_name or u.name,
		"enabled": cint(u.enabled),
		"user_type": u.user_type,
		"profiles": profiles.get(u.name, []),
		"last_active": str(u.last_active or ""),
		"last_login": str(u.last_login or ""),
		"created": str(u.creation or ""),
		"locked": locked,
	}


@frappe.whitelist(methods=["GET"])
def meta():
	"""What the forms need: the role profiles (with their roles, how many people hold each, whether they can be
	assigned from here) and who the caller is."""
	_require_admin()
	roles = _profile_roles()
	counts = {}
	for r in frappe.get_all("User Role Profile", filters={"parenttype": "User"}, fields=["role_profile"], limit_page_length=0):
		counts[r.role_profile] = counts.get(r.role_profile, 0) + 1
	profiles = [
		{"name": p, "roles": sorted(roles.get(p, [])), "users": counts.get(p, 0), "assignable": profile_is_assignable(roles.get(p, []))}
		for p in frappe.get_all("Role Profile", pluck="name", order_by="name asc", limit_page_length=0)
	]
	return {"profiles": profiles, "me": frappe.session.user, "is_system_manager": _is_sm()}


@frappe.whitelist(methods=["GET"])
def users(search=None, status=None, profile=None, user_type="System User"):
	"""The people list. `status`: active | disabled. `profile`: only people with that role profile ('none' for no
	profile)."""
	_require_admin()
	filters = {"name": ["not in", list(PROTECTED_USERS)]}
	if user_type in ("System User", "Website User"):
		filters["user_type"] = user_type
	if status == "active":
		filters["enabled"] = 1
	elif status == "disabled":
		filters["enabled"] = 0
	term = (search or "").strip()[:100]
	or_filters = [["name", "like", f"%{term}%"], ["full_name", "like", f"%{term}%"]] if term else None
	rows = frappe.get_all("User", filters=filters, or_filters=or_filters, fields=["name", "full_name", "enabled", "user_type", "last_active", "last_login", "creation"], order_by="full_name asc", limit_page_length=MAX_ROWS)
	names = [r.name for r in rows]
	profiles = _profiles_of(names)
	forbidden = _forbidden_holders(names)
	caller_sm = _is_sm()
	out = []
	for r in rows:
		mine = profiles.get(r.name, [])
		if profile == "none" and mine:
			continue
		if profile and profile != "none" and profile not in mine:
			continue
		out.append(_row(r, profiles, lock_reason(r.name, r.name in forbidden, caller_sm)))
	return {
		"rows": out,
		"total": len(out),
		"summary": {
			"active": sum(1 for r in out if r["enabled"]),
			"disabled": sum(1 for r in out if not r["enabled"]),
			"no_profile": sum(1 for r in out if not r["profiles"]),
		},
	}


@frappe.whitelist(methods=["GET"])
def detail(name):
	"""One person for the drawer: their profiles, the roles those give, and what IT changed on the account."""
	_require_admin()
	doc = _get_user(name, allow_locked=True)
	reason = lock_reason(doc.name, doc.name in _forbidden_holders([doc.name]), _is_sm())
	notes = frappe.get_all(
		"Comment",
		filters={"reference_doctype": "User", "reference_name": doc.name, "comment_type": "Info", "content": ["like", "IT: %"]},
		fields=["content", "owner", "creation"],
		order_by="creation desc",
		limit_page_length=15,
	)
	return {
		"name": doc.name,
		"full_name": doc.full_name or doc.name,
		"enabled": cint(doc.enabled),
		"user_type": doc.user_type,
		"profiles": [r.role_profile for r in doc.role_profiles],
		"roles": sorted({r.role for r in doc.roles if r.role not in ("All", "Guest", "Desk User")}),
		"last_active": str(doc.last_active or ""),
		"last_login": str(doc.last_login or ""),
		"created": str(doc.creation or ""),
		"locked": reason,
		"is_me": doc.name == frappe.session.user,
		"history": [{"text": n.content[4:], "by": n.owner, "at": str(n.creation)} for n in notes],
	}


# --------------------------------------------------------------------------- #
# changing
# --------------------------------------------------------------------------- #


def _get_user(name, allow_locked=False):
	if not name or not frappe.db.exists("User", name):
		frappe.throw(_("Unknown user."), frappe.DoesNotExistError)
	doc = frappe.get_doc("User", name)
	if not allow_locked:
		reason = lock_reason(doc.name, doc.name in _forbidden_holders([doc.name]), _is_sm())
		if reason:
			frappe.throw(_(reason), frappe.PermissionError)
	return doc


def _check_profiles(names, keep=()):
	"""The profiles, after checking they exist and can be handed out from here. Ones the person already has (`keep`)
	are left as they are."""
	names = clean_names(names)
	roles = _profile_roles()
	for p in names:
		if not frappe.db.exists("Role Profile", p):
			frappe.throw(_("Unknown role profile: {0}").format(p), frappe.ValidationError)
		if p not in keep and not profile_is_assignable(roles.get(p, [])):
			frappe.throw(_("{0} includes a system role, so it cannot be given from here.").format(p), frappe.PermissionError)
	return names


def _set_profiles(doc, names):
	doc.set("role_profiles", [])
	for p in names:
		doc.append("role_profiles", {"role_profile": p})
	if not names:
		# Frappe leaves the roles alone when there are no profiles, so clear them: no profile, no access.
		doc.set("roles", [])


def _note(doc, text):
	doc.add_comment("Info", f"IT: {text}")


def _save(doc):
	doc.flags.ignore_permissions = True
	doc.save()


@frappe.whitelist(methods=["POST"])
def create(email, full_name, password, role_profiles=None):
	"""Add a person. They sign in with the password given here; the role profiles decide what they can do."""
	_require_admin()
	email = (email or "").strip().lower()
	full_name = (full_name or "").strip()
	if not email or not full_name or not password:
		frappe.throw(_("Email, full name and password are required."), frappe.ValidationError)
	if not frappe.utils.validate_email_address(email):
		frappe.throw(_("That email address does not look right."), frappe.ValidationError)
	if lock_reason(email, False, _is_sm()):
		frappe.throw(_("You cannot create this user."), frappe.PermissionError)
	if frappe.db.exists("User", email):
		frappe.throw(_("A user with this email already exists."), frappe.ValidationError)
	profiles = _check_profiles(role_profiles)
	first, last = split_name(full_name)
	doc = frappe.new_doc("User")
	doc.update({"email": email, "first_name": first, "last_name": last, "enabled": 1, "user_type": "System User", "send_welcome_email": 0, "new_password": password})
	for p in profiles:
		doc.append("role_profiles", {"role_profile": p})
	doc.flags.ignore_permissions = True
	doc.insert()
	_note(doc, f"created by {frappe.session.user}" + (f" with {', '.join(profiles)}" if profiles else ""))
	return {"name": doc.name}


@frappe.whitelist(methods=["POST"])
def update(name, full_name=None, enabled=None, role_profiles=None):
	"""Change a person's name, whether they can sign in, and/or the complete list of their role profiles."""
	_require_admin()
	doc = _get_user(name)
	changes = []
	if full_name is not None and full_name.strip() and full_name.strip() != (doc.full_name or ""):
		doc.first_name, doc.last_name = split_name(full_name)
		changes.append(f"name changed to {full_name.strip()}")
	if enabled is not None:
		on = 1 if cint(enabled) else 0
		if not on and doc.name == frappe.session.user:
			frappe.throw(_("You cannot disable your own account."), frappe.ValidationError)
		if on != cint(doc.enabled):
			doc.enabled = on
			changes.append("enabled" if on else "disabled")
	if role_profiles is not None:
		have = [r.role_profile for r in doc.role_profiles]
		wanted = _check_profiles(role_profiles, keep=have)
		if wanted != have:
			if doc.name == frappe.session.user:
				frappe.throw(_("You cannot change your own role profiles."), frappe.ValidationError)
			_set_profiles(doc, wanted)
			changes.append("role profiles: " + (", ".join(wanted) or "none"))
	if changes:
		_save(doc)
		_note(doc, f"{'; '.join(changes)} by {frappe.session.user}")
	return detail(doc.name)


@frappe.whitelist(methods=["POST"])
def add_role_profile(name, role_profile):
	"""Give a person one more role profile, keeping the ones they have."""
	_require_admin()
	doc = _get_user(name)
	if doc.name == frappe.session.user:
		frappe.throw(_("You cannot change your own role profiles."), frappe.ValidationError)
	have = [r.role_profile for r in doc.role_profiles]
	wanted = _check_profiles(have + [role_profile], keep=have) if role_profile not in have else have
	if wanted != have:
		_set_profiles(doc, wanted)
		_save(doc)
		_note(doc, f"added role profile {role_profile} by {frappe.session.user}")
	return detail(doc.name)


@frappe.whitelist(methods=["POST"])
def remove_role_profile(name, role_profile):
	"""Take one role profile away from a person."""
	_require_admin()
	doc = _get_user(name)
	if doc.name == frappe.session.user:
		frappe.throw(_("You cannot change your own role profiles."), frappe.ValidationError)
	have = [r.role_profile for r in doc.role_profiles]
	if role_profile in have:
		_set_profiles(doc, [p for p in have if p != role_profile])
		_save(doc)
		_note(doc, f"removed role profile {role_profile} by {frappe.session.user}")
	return detail(doc.name)


@frappe.whitelist(methods=["POST"])
def reset_password(name, password):
	"""Set a new password for a person."""
	_require_admin()
	if not password:
		frappe.throw(_("Enter the new password."), frappe.ValidationError)
	doc = _get_user(name)
	doc.new_password = password
	_save(doc)
	_note(doc, f"password reset by {frappe.session.user}")
	return {"name": doc.name, "reset": True}


@frappe.whitelist(methods=["POST"])
def delete(name):
	"""Delete a person. Frappe refuses when records point at them; the message then suggests disabling instead."""
	_require_admin()
	doc = _get_user(name)
	if doc.name == frappe.session.user:
		frappe.throw(_("You cannot delete your own account."), frappe.ValidationError)
	try:
		frappe.delete_doc("User", doc.name, ignore_permissions=True)
	except frappe.LinkExistsError:
		frappe.db.rollback()
		frappe.throw(_("{0} has records linked to them, so they cannot be deleted. Disable the account instead.").format(doc.full_name or doc.name), frappe.LinkExistsError)
	frappe.logger("it_user_admin").info(f"{frappe.session.user} deleted user {doc.name}")
	return {"deleted": doc.name}
