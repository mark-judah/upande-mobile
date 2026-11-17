# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Composite indexes for the monitor's dashboard queries. Idempotent: `add_index`
# does nothing when the index exists, and a failure here never fails a migrate.

import frappe

INDEXES = {
	"Mobile Event": (("issue", "ts"), ("app", "ts"), ("install_id", "ts"), ("user", "ts"), ("app", "event_id")),
	"Mobile Issue": (("app", "status", "last_seen"),),
	"Mobile App Install": (("app", "last_seen"),),
	"Mobile Notification": (("user", "app", "creation"), ("status", "next_attempt_at")),
}

# At most one notification per (app, user, dedupe_key); NULL keys never collide.
UNIQUE = {"Mobile Notification": (("app", "user", "dedupe_key"),)}


def ensure_indexes():
	for doctype, groups in INDEXES.items():
		if not frappe.db.table_exists(doctype):
			continue
		for fields in groups:
			try:
				frappe.db.add_index(doctype, list(fields))
			except Exception:
				frappe.log_error(title=f"upande_mobile: could not index {doctype} {fields}")
	for doctype, groups in UNIQUE.items():
		if not frappe.db.table_exists(doctype):
			continue
		for fields in groups:
			try:
				frappe.db.add_unique(doctype, list(fields), "unique_" + "_".join(fields))
			except Exception:
				frappe.log_error(title=f"upande_mobile: could not add unique {doctype} {fields}")
