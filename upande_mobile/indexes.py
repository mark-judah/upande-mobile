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
}


def ensure_indexes():
	for doctype, groups in INDEXES.items():
		if not frappe.db.table_exists(doctype):
			continue
		for fields in groups:
			try:
				frappe.db.add_index(doctype, list(fields))
			except Exception:
				frappe.log_error(title=f"upande_mobile: could not index {doctype} {fields}")
