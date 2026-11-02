# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Unit tests for crash / API reporting: redaction, fingerprinting, caps, auth, retention.
# No site or database needed:
#   ../../env/bin/python -W ignore -m unittest upande_mobile.tests.test_monitor

import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import frappe

from upande_mobile import fingerprint as fp
from upande_mobile import redaction as rd
from upande_mobile.api import monitor as mon

NOW = datetime(2026, 11, 1, 12, 0, 0)


def _throw(msg, exc=frappe.ValidationError, *a, **k):
	raise exc(msg)


class TestRedaction(unittest.TestCase):
	def test_sensitive_keys(self):
		for key in ("Authorization", "Cookie", "Set-Cookie", "x-api-key", "apiKey", "api_key", "access_token", "refreshToken",
			"password", "new_password", "pwd", "usr", "sid", "session_id", "otp", "pin", "cvv", "card_number", "client_secret",
			"X-Frappe-CSRF-Token", "private_key"):
			self.assertTrue(rd.is_sensitive_key(key), key)
		for key in ("Content-Type", "Accept", "user-agent", "shipping_address", "inside", "residual", "item_code", "cardinality", "keyboard"):
			self.assertFalse(rd.is_sensitive_key(key), key)

	def test_headers(self):
		out = rd.redact_headers({"Authorization": "token abc:def", "Content-Type": "application/json", "Cookie": "sid=1", "X-Trace": "t1"})
		self.assertEqual(out["Authorization"], rd.REDACTED)
		self.assertEqual(out["Cookie"], rd.REDACTED)
		self.assertEqual(out["Content-Type"], "application/json")
		self.assertEqual(rd.redact_headers([["Set-Cookie", "x"], ["Accept", "*/*"]]), {"Set-Cookie": rd.REDACTED, "Accept": "*/*"})
		self.assertEqual(rd.redact_headers(None), {})
		# a secret hiding in an innocent header is still masked by shape
		self.assertEqual(rd.redact_headers({"X-Debug": "Bearer abcdefghijklmnop"})["X-Debug"], rd.REDACTED)

	def test_extra_keys(self):
		self.assertEqual(rd.redact_headers({"X-Tenant-Code": "42"}, extra_keys=("tenant-code",))["X-Tenant-Code"], rd.REDACTED)

	def test_frappe_login_body(self):
		out = json.loads(rd.redact_body('{"usr": "a@b.co", "pwd": "hunter2", "device": "x"}'))
		self.assertEqual(out, {"usr": rd.REDACTED, "pwd": rd.REDACTED, "device": "x"})
		self.assertNotIn("hunter2", rd.redact_body("usr=a%40b.co&pwd=hunter2&x=1"))
		self.assertIn("x=1", rd.redact_body("usr=a%40b.co&pwd=hunter2&x=1"))

	def test_nested_json_and_depth(self):
		body = {"a": [{"token": "t", "n": 1}], "b": {"c": {"password": "p"}}}
		out = rd.redact_value(body)
		self.assertEqual(out["a"][0], {"token": rd.REDACTED, "n": 1})
		self.assertEqual(out["b"]["c"]["password"], rd.REDACTED)
		deep = cur = {}
		for _ in range(20):
			cur["x"] = {}
			cur = cur["x"]
		self.assertIn("too deep", json.dumps(rd.redact_value(deep)))

	def test_text_patterns(self):
		self.assertNotIn("abcdefgh12345", rd.redact_text("failed with Bearer abcdefgh12345 now"))
		self.assertNotIn("zzzzzzzz", rd.redact_text("Authorization: token abc123xyz:zzzzzzzz"))
		self.assertNotIn("s3cr3t", rd.redact_text('{"api_secret": "s3cr3t"}'))
		self.assertNotIn("s3cr3t", rd.redact_text("password=s3cr3t&x=1"))
		jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdef"
		self.assertNotIn(jwt, rd.redact_text("token was " + jwt))
		self.assertIn("harmless", rd.redact_text("harmless text"))

	def test_url(self):
		url = rd.redact_url("https://user:pw@h.example/api/x?api_key=SECRET&page=2&sid=abc")
		self.assertNotIn("SECRET", url)
		self.assertNotIn("pw@", url)
		self.assertNotIn("abc", url)
		self.assertIn("page=2", url)
		self.assertTrue(url.startswith("https://"))

	def test_body_caps_and_binary(self):
		big = rd.redact_body("x" * 20000, max_bytes=1000)
		self.assertLessEqual(len(big), 1000 + 40)
		self.assertIn("truncated", big)
		self.assertIn("multipart", rd.redact_body("--b\r\nContent-Disposition...", {"Content-Type": "multipart/form-data; boundary=b"}))
		self.assertIn("bytes", rd.redact_body("PNGDATA", {"content-type": "image/png"}))
		self.assertEqual(rd.redact_body(None), "")
		self.assertIn("binary", rd.redact_body("a\x00b\x00c"))


class TestFingerprint(unittest.TestCase):
	def test_message_normalisation_groups_variants(self):
		a = fp.fingerprint("App", "error", "Item ITM-0042 not found for order 17", "Error", "at f (app/a.js:10:5)")
		b = fp.fingerprint("App", "error", "Item ITM-0043 not found for order 982", "Error", "at f (app/a.js:99:1)")
		c = fp.fingerprint("App", "error", "Cannot read property of undefined", "TypeError", "at f (app/a.js:10:5)")
		self.assertEqual(a[0], b[0])
		self.assertNotEqual(a[0], c[0])
		self.assertEqual(len(a[0]), 40)

	def test_app_is_part_of_the_key(self):
		self.assertNotEqual(fp.fingerprint("A", "error", "boom")[0], fp.fingerprint("B", "error", "boom")[0])

	def test_api_groups_by_path_template_and_status(self):
		def req(url, status=500, method="GET"):
			return {"method": method, "url": url, "status": status}
		a = fp.fingerprint("App", "api", request=req("https://h/api/resource/Sales%20Invoice/ACC-SINV-2026-0001?x=1"))
		b = fp.fingerprint("App", "api", request=req("https://other/api/resource/Sales%20Invoice/ACC-SINV-2026-0099"))
		c = fp.fingerprint("App", "api", request=req("https://h/api/resource/Sales%20Invoice/ACC-SINV-2026-0001", status=404))
		d = fp.fingerprint("App", "api", request=req("https://h/api/resource/Sales%20Invoice/ACC-SINV-2026-0001", method="POST"))
		self.assertEqual(a[0], b[0])
		self.assertNotEqual(a[0], c[0])
		self.assertNotEqual(a[0], d[0])
		self.assertEqual(a[1], "GET /api/resource/Sales Invoice/:id → 500")

	def test_path_template(self):
		self.assertEqual(fp.path_template("https://h/api/method/a.b.c?x=1"), "/api/method/a.b.c")
		self.assertEqual(fp.path_template("/v1/users/123456/orders/550e8400-e29b-41d4-a716-446655440000"), "/v1/users/:id/orders/:id")

	def test_stack_frames(self):
		stack = "Error: x\n    at foo (http://h/index.bundle?platform=android:1:2)\n    at bar (app/b.js:3:4)\n    at baz (app/c.js:5:6)\n    at qux (app/d.js:7:8)"
		self.assertEqual(fp.stack_frames(stack), ["foo@index.bundle", "bar@b.js", "baz@c.js"])
		self.assertEqual(fp.stack_frames("render@app/Screen.js:10:3\nrun@app/r.js:4:1"), ["render@Screen.js", "run@r.js"])


class TestCleanEvent(unittest.TestCase):
	def test_basic_and_defaults(self):
		ev = mon.clean_event({"kind": "crash", "message": "boom", "ts": "2026-11-01T11:59:00.000Z", "count": "3"}, NOW)
		self.assertEqual((ev["kind"], ev["level"], ev["fatal"], ev["count"]), ("crash", "fatal", 1, 3))
		self.assertEqual(ev["ts"], datetime(2026, 11, 1, 11, 59))
		self.assertEqual(mon.clean_event({"kind": "weird", "message": "m"}, NOW)["kind"], "log")
		self.assertIsNone(mon.clean_event({"kind": "error"}, NOW))
		self.assertIsNone(mon.clean_event("nope", NOW))

	def test_future_timestamps_are_pinned(self):
		ev = mon.clean_event({"message": "m", "ts": "2030-01-01T00:00:00Z"}, NOW)
		self.assertEqual(ev["ts"], NOW)
		self.assertEqual(mon.clean_event({"message": "m", "ts": "garbage"}, NOW)["ts"], NOW)

	def test_caps(self):
		ev = mon.clean_event({"message": "m" * 5000, "stack": "s" * 40000, "breadcrumbs": [{"ts": "t", "type": "nav", "message": "c" * 900}] * 400}, NOW)
		self.assertLessEqual(len(ev["message"]), mon.MAX_MESSAGE + 40)
		self.assertLessEqual(len(ev["stack"]), mon.MAX_STACK + 40)
		self.assertLessEqual(len(ev["breadcrumbs"]), mon.MAX_BREADCRUMBS)
		self.assertLessEqual(len(json.dumps(ev["breadcrumbs"])), mon.MAX_BREADCRUMBS_BYTES)
		self.assertLessEqual(len(ev["breadcrumbs"][0]["message"]), 340)

	def test_api_event_is_redacted_and_capped(self):
		ev = mon.clean_event(
			{
				"kind": "api",
				"message": "Request failed",
				"request": {
					"method": "post",
					"url": "https://h/api/method/login?pwd=zzz",
					"status": 417,
					"request_headers": {"Authorization": "token a:b", "Content-Type": "application/json"},
					"request_body": '{"usr": "u", "pwd": "p", "big": "' + "x" * 30000 + '"}',
					"response_headers": {"Set-Cookie": "sid=abc"},
					"response_body": '{"exc": "Traceback"}',
					"max_body_bytes": 99999999,
				},
			},
			NOW,
		)
		req = ev["request"]
		self.assertEqual(req["method"], "POST")
		blob = json.dumps(req)
		for secret in ("token a:b", "zzz", "sid=abc", '"p"'):
			self.assertNotIn(secret, blob)
		self.assertLessEqual(len(req["request_body"]), mon.MAX_BODY_BYTES + 60)
		self.assertEqual(req["request_headers"]["Content-Type"], "application/json")

	def test_device_facts_typed_and_key_hashed(self):
		d = mon.clean_device(
			{
				"platform": "android", "os_version": "14", "model": "Pixel 8", "battery_level": 7.5, "storage_free_bytes": "1234",
				"is_connected": "false", "install_id": "abcdef12-3456", "device_key": "0123456789abcdef", "evil": "x",
				"screen": {"width": 1080, "height": 2400, "scale": 2.6, "font_scale": 1, "junk": 1},
			}
		)
		self.assertEqual(d["battery_level"], 1)  # clamped to 0..1
		self.assertEqual(d["storage_free_bytes"], 1234)
		self.assertIs(d["is_connected"], False)
		self.assertNotIn("evil", d)
		self.assertNotIn("device_key", d)
		self.assertEqual(len(d["device_key_hash"]), 64)
		self.assertNotIn("0123456789abcdef", json.dumps(d))
		self.assertEqual(d["screen"], {"width": 1080.0, "height": 2400.0, "scale": 2.6, "font_scale": 1.0})
		self.assertNotIn("install_id", mon.clean_device({"install_id": "bad id!"}))

	def test_network_facts_kept_typed_and_clamped(self):
		d = mon.clean_device(
			{
				"wifi_ssid": "KarenFarm-5G", "wifi_signal_pct": 71, "wifi_link_mbps": "433", "wifi_frequency_mhz": 5180,
				"latency_ms": 85.6, "api_calls": 42, "api_avg_ms": 310, "api_p95_ms": 1200, "api_error_pct": 250,
				"cellular_generation": "4g", "bogus_speed": 1,
			}
		)
		self.assertEqual(d["wifi_ssid"], "KarenFarm-5G")
		self.assertEqual((d["wifi_link_mbps"], d["wifi_frequency_mhz"], d["latency_ms"]), (433, 5180, 85))
		self.assertEqual((d["api_calls"], d["api_avg_ms"], d["api_p95_ms"]), (42, 310, 1200))
		self.assertEqual(d["api_error_pct"], 100)  # clamped
		self.assertEqual(d["cellular_generation"], "4g")
		self.assertNotIn("bogus_speed", d)
		self.assertNotIn("api_error_pct", mon.clean_device({"api_error_pct": ""}))

	def test_missing_modules_kept_and_sanitised(self):
		d = mon.clean_device({"missing_modules": ["expo-battery", "@react-native-async-storage/async-storage", "<script>", 7]})
		self.assertEqual(d["missing_modules"], ["expo-battery", "@react-native-async-storage/async-storage"])
		self.assertNotIn("missing_modules", mon.clean_device({"missing_modules": "expo-battery"}))
		self.assertEqual(len(mon.clean_device({"missing_modules": ["x" * 200]})["missing_modules"][0]), 60)

	def test_helpers(self):
		self.assertEqual(mon.os_major("Android 14"), 14)
		self.assertEqual(mon.os_major("17.2.1"), 17)
		self.assertEqual(mon.os_major(""), 0)
		self.assertEqual(mon.connectivity_of({"is_connected": False}), "offline")
		self.assertEqual(mon.connectivity_of({"is_connected": True, "is_internet_reachable": True}), "online")
		self.assertEqual(mon.connectivity_of({}), "")
		self.assertTrue(mon.newer_version("1.2.4", "1.2.3"))
		self.assertFalse(mon.newer_version("1.2.3", "1.2.3"))
		self.assertFalse(mon.newer_version("", "1.2.3"))


class TestHealthFlags(unittest.TestCase):
	def test_flags(self):
		row = {"snapshot_at": "2026-11-01", "battery_level": 0.1, "storage_total_bytes": 64 * 2**30, "storage_free_bytes": 100 * 2**20,
			"connectivity": "offline", "os_major": 8, "platform": "Android", "status": "behind_ota", "snapshot": {"battery_state": "unplugged"}}
		self.assertEqual(mon.health_flags(row), ["low_battery", "low_storage", "offline", "outdated_os", "outdated_app"])
		row["snapshot"] = {"battery_state": "charging"}
		self.assertNotIn("low_battery", mon.health_flags(row))
		healthy = {"snapshot_at": "x", "battery_level": 0.9, "storage_total_bytes": 64 * 2**30, "storage_free_bytes": 20 * 2**30,
			"connectivity": "online", "os_major": 14, "platform": "android", "status": "current"}
		self.assertEqual(mon.health_flags(healthy), [])
		# never reported: no battery / storage verdicts
		self.assertEqual(mon.health_flags({"battery_level": 0, "storage_total_bytes": 0, "status": "unknown"}), [])


class TestRetention(unittest.TestCase):
	def test_cutoff_is_clamped(self):
		self.assertEqual(mon.retention_cutoff(30, NOW), NOW - timedelta(days=30))
		self.assertEqual(mon.retention_cutoff(0, NOW), NOW - timedelta(days=30))  # unset: default
		self.assertEqual(mon.retention_cutoff(-5, NOW), NOW - timedelta(days=1))
		self.assertEqual(mon.retention_cutoff(99999, NOW), NOW - timedelta(days=365))

	def test_purge_deletes_in_batches_and_commits(self):
		db = MagicMock()
		batches = [["a"] * 1000, ["b"] * 1000, ["c"] * 5]
		with (
			patch.object(frappe, "db", db),
			patch.object(frappe, "get_all", side_effect=batches),
			patch.object(mon, "_sys_time", side_effect=lambda t: t),
		):
			deleted = mon._purge_app("My App", NOW, batch=1000)
		self.assertEqual(deleted, 2005)
		self.assertEqual(db.delete.call_count, 3)
		self.assertEqual(db.commit.call_count, 3)

	def test_purge_old_events_uses_each_apps_retention(self):
		rows = [frappe._dict(name="A", event_retention_days=7), frappe._dict(name="B", event_retention_days=90)]
		cutoffs = {}
		with (
			patch.object(frappe, "get_all", return_value=rows),
			patch.object(mon, "_purge_app", side_effect=lambda app, cutoff, **k: cutoffs.setdefault(app, cutoff) and 1),
		):
			result = mon.purge_old_events()
		self.assertEqual(result, {"A": 1, "B": 1})
		self.assertAlmostEqual((cutoffs["A"] - cutoffs["B"]).total_seconds(), 83 * 86400, delta=5)


class TestRateLimit(unittest.TestCase):
	def test_counts_per_scope_and_expires_the_window(self):
		cache = MagicMock()
		cache.make_key.side_effect = lambda k: "k:" + k
		cache.incrby.side_effect = [1, 2, 3]
		with patch.object(mon, "_cache", return_value=cache):
			self.assertFalse(mon._over_limit("user", "a@b.co", 2))
			self.assertFalse(mon._over_limit("user", "a@b.co", 2))
			self.assertTrue(mon._over_limit("user", "a@b.co", 2))
		cache.expire.assert_called_once()  # only the first hit of a window sets the expiry
		self.assertFalse(mon._over_limit("user", "", 1))  # nothing to key on: never blocks

	def test_guests_use_the_stricter_ip_limit(self):
		seen = []
		with patch.object(mon, "_over_limit", side_effect=lambda scope, ident, limit, *a: seen.append((scope, limit)) or False):
			mon._rate_limit(True, "Guest", "9.9.9.9")
			mon._rate_limit(False, "a@b.co", "9.9.9.9")
		self.assertEqual(seen, [("guest-ip", mon.GUEST_IP_LIMIT), ("user", mon.USER_LIMIT), ("ip", mon.IP_LIMIT)])
		self.assertLess(mon.GUEST_IP_LIMIT, mon.IP_LIMIT)


class TestAuthAndCaps(unittest.TestCase):
	def setUp(self):
		for name in ("throw",):
			patcher = patch.object(frappe, name, side_effect=_throw)
			patcher.start()
			self.addCleanup(patcher.stop)
		translate = patch.object(mon, "_", side_effect=lambda m: m)
		translate.start()
		self.addCleanup(translate.stop)

	def test_dashboard_endpoints_need_system_manager(self):
		calls = (
			lambda: mon.overview(),
			lambda: mon.issues(),
			lambda: mon.issue("x"),
			lambda: mon.events(),
			lambda: mon.devices(),
			lambda: mon.set_issue_status("x", "Resolved"),
			lambda: mon.purge_now(),
		)
		with patch.object(frappe, "get_roles", return_value=["Guest"]):
			for call in calls:
				with self.assertRaises(frappe.PermissionError):
					call()
		with patch.object(frappe, "get_roles", return_value=["All", "Desk User"]):
			with self.assertRaises(frappe.PermissionError):
				mon.overview()

	def test_set_issue_status_rejects_unknown_status(self):
		with patch.object(frappe, "get_roles", return_value=["System Manager"]):
			with self.assertRaises(frappe.ValidationError):
				mon.set_issue_status("x", "Deleted")

	def _ingest(self, payload, user="Guest", app=None, rate_limited=False, created=None, ensure=None):
		"""`app`: the Mobile App row found first; `created`: the row found after `_ensure_app` made it."""
		db = MagicMock()
		rows = [app, created if created is not None else app]

		def get_value(doctype, *a, **k):
			if doctype == mon.MOBILE_APP:
				return rows.pop(0) if len(rows) > 1 else rows[0]
			return ""

		db.get_value.side_effect = get_value
		self.ensure = ensure if ensure is not None else MagicMock(return_value=None)
		session = MagicMock(user=user)
		with (
			patch.object(frappe, "session", session),
			patch.object(frappe, "db", db),
			patch.object(mon, "_ensure_app", self.ensure),
			patch.object(mon, "_observed_ip", return_value="10.0.0.1"),
			patch.object(mon, "_over_limit", return_value=rate_limited),
			patch.object(mon, "now_datetime", return_value=NOW),
			patch.object(mon, "_store_event", return_value=True) as store,
			patch.object(mon, "_write_snapshots"),
			patch.object(mon, "_flush_issue"),
			patch.object(frappe, "get_roles", return_value=["All", "Desk User"]),
		):
			return mon.ingest(payload), store

	def _app(self, **kw):
		base = dict(name="My App", enabled=1, enable_monitoring=1, accept_reports_before_sign_in=0, event_retention_days=30)
		base.update(kw)
		return frappe._dict(base)

	def test_guest_is_refused_unless_the_app_allows_it(self):
		payload = json.dumps({"app": "My App", "events": [{"message": "m"}]})
		for app in (None, self._app()):
			with self.assertRaises(frappe.AuthenticationError):
				self._ingest(payload, "Guest", app)
		reply, store = self._ingest(payload, "Guest", self._app(accept_reports_before_sign_in=1))
		self.assertEqual(reply["accepted"], 1)
		store.assert_called_once()

	def test_rate_limited(self):
		with self.assertRaises(frappe.TooManyRequestsError):
			self._ingest("{}", "a@b.co", self._app(), rate_limited=True)

	def test_payload_must_be_small_json_object(self):
		for bad in ("not json", "[1,2]", "x" * (mon.MAX_PAYLOAD_BYTES + 1)):
			with self.assertRaises(frappe.ValidationError):
				self._ingest(bad, "a@b.co", self._app())

	def test_more_than_the_cap_is_dropped_not_stored(self):
		events = [{"message": f"m{i}"} for i in range(mon.MAX_EVENTS + 7)]
		reply, store = self._ingest(json.dumps({"app": "My App", "events": events}), "a@b.co", self._app())
		self.assertEqual(reply["accepted"], mon.MAX_EVENTS)
		self.assertEqual(reply["dropped"], 7)
		self.assertEqual(store.call_count, mon.MAX_EVENTS)

	def test_unknown_app_that_cannot_be_created_is_paused_with_a_reason(self):
		payload = json.dumps({"app": "Nope", "events": [{"message": "m"}] * 3})
		reply, store = self._ingest(payload, "a@b.co", None)  # _ensure_app refuses (cap reached): returns None
		self.assertEqual((reply["accepted"], reply["dropped"]), (0, 3))
		self.assertEqual((reply["paused"], reply["reason"]), (True, "unknown_app"))
		self.assertGreaterEqual(reply["next_flush_ms"], 600_000)
		store.assert_not_called()
		# a name that is not a valid app name never reaches _ensure_app
		reply, _ = self._ingest(json.dumps({"app": "<x>", "events": [{"message": "m"}]}), "a@b.co", None)
		self.assertEqual(reply["reason"], "unknown_app")
		self.ensure.assert_not_called()

	def test_monitoring_off_pauses_with_its_reason(self):
		payload = json.dumps({"app": "My App", "events": [{"message": "m"}] * 2})
		reply, store = self._ingest(payload, "a@b.co", self._app(enable_monitoring=0))
		self.assertEqual((reply["accepted"], reply["paused"], reply["reason"]), (0, True, "monitoring_off"))
		store.assert_not_called()

	def test_a_signed_in_first_report_for_a_new_app_creates_it_and_is_stored(self):
		payload = json.dumps({"app": "Brand New", "events": [{"message": "a"}, {"message": "b"}]})
		ensure = MagicMock(return_value="Brand New")
		reply, store = self._ingest(payload, "a@b.co", None, created=self._app(name="Brand New", enabled=0), ensure=ensure)
		ensure.assert_called_once_with("Brand New")
		self.assertEqual((reply["accepted"], reply["paused"], reply["reason"]), (2, False, None))
		self.assertEqual(store.call_count, 2)

	def test_a_disabled_version_check_does_not_pause_monitoring(self):
		# `enabled` governs version checks only; apps created on the fly start with it off.
		payload = json.dumps({"app": "My App", "events": [{"message": "m"}]})
		reply, store = self._ingest(payload, "a@b.co", self._app(enabled=0))
		self.assertEqual((reply["accepted"], reply["paused"]), (1, False))

	def test_a_guest_never_creates_an_app(self):
		payload = json.dumps({"app": "Brand New", "events": [{"message": "m"}]})
		ensure = MagicMock(return_value="Brand New")
		with self.assertRaises(frappe.AuthenticationError):
			self._ingest(payload, "Guest", None, ensure=ensure)
		ensure.assert_not_called()

	def test_a_race_on_creation_still_finds_the_app(self):
		payload = json.dumps({"app": "Brand New", "events": [{"message": "m"}]})
		ensure = MagicMock(side_effect=frappe.DuplicateEntryError("Mobile App", "Brand New", Exception()))
		reply, _ = self._ingest(payload, "a@b.co", None, created=self._app(name="Brand New"), ensure=ensure)
		self.assertEqual(reply["accepted"], 1)

	def test_events_older_than_retention_and_bad_events_are_dropped(self):
		old = (datetime.utcnow() - timedelta(days=40)).isoformat() + "Z"
		events = [{"message": "old", "ts": old}, "junk", {"kind": "error"}, {"message": "fine"}]
		reply, store = self._ingest(json.dumps({"app": "My App", "events": events}), "a@b.co", self._app())
		self.assertEqual((reply["accepted"], reply["dropped"]), (1, 3))
		store.assert_called_once()

	def test_a_failing_event_does_not_fail_the_batch(self):
		payload = json.dumps({"app": "My App", "events": [{"message": "a"}, {"message": "b"}]})
		with (
			patch.object(frappe, "session", MagicMock(user="a@b.co")),
			patch.object(frappe, "db", MagicMock(get_value=MagicMock(return_value=self._app()))),
			patch.object(mon, "_observed_ip", return_value="1.1.1.1"),
			patch.object(mon, "_over_limit", return_value=False),
			patch.object(mon, "now_datetime", return_value=NOW),
			patch.object(mon, "_store_event", side_effect=[RuntimeError("db"), True]),
			patch.object(mon, "_write_snapshots"),
			patch.object(mon, "_flush_issue"),
			patch.object(frappe, "get_roles", return_value=[]),
			patch.object(frappe, "log_error"),
		):
			reply = mon.ingest(payload)
		self.assertEqual((reply["accepted"], reply["dropped"]), (1, 1))


class TestDeviceInfoOnRegister(unittest.TestCase):
	"""register_install(device_info=...) fills the register row's health from the first launch."""

	def _call(self, device_info, core_result=None, **kw):
		from upande_mobile.api import devices as dev

		core = MagicMock(return_value=core_result or {"install_id": "row-1", "status": "unknown"})
		writes = []
		with (
			patch.object(dev, "_register_install", core),
			patch.object(mon, "_write_snapshots", side_effect=lambda snaps: writes.append(snaps)),
			patch.object(frappe, "db", MagicMock()) as db,
			patch.object(frappe, "log_error"),
		):
			result = dev.register_install(install_id="abcdef12-3456", app="My App", device_info=device_info, **kw)
		return result, writes, core, db

	def test_facts_are_cleaned_and_written_to_the_register_row(self):
		info = {"battery_level": 0.03, "battery_state": "unplugged", "low_power_mode": True, "storage_free_bytes": 1000,
			"storage_total_bytes": 5000, "network_type": "cellular", "os_build": "B1", "device_name": "Nord", "evil": "x",
			"device_key": "0123456789abcdef", "install_id": "abcdef12-3456", "missing_modules": ["expo-battery"]}
		result, writes, core, db = self._call(json.dumps(info))
		self.assertEqual(result["install_id"], "row-1")
		(snaps,) = writes
		ts, device = snaps["row-1"]
		self.assertEqual(device["battery_level"], 0.03)
		self.assertEqual(device["storage_free_bytes"], 1000)
		self.assertIs(device["low_power_mode"], True)
		self.assertEqual(device["missing_modules"], ["expo-battery"])
		self.assertNotIn("evil", device)
		self.assertNotIn("device_key_hash", device)  # the register row already has its own key
		self.assertNotIn("install_id", device)
		db.set_value.assert_called_once()  # device_name filled in from the facts
		self.assertEqual(db.set_value.call_args[0][2], {"device_name": "Nord"})

	def test_a_dict_is_accepted_and_a_plain_report_still_works(self):
		_, writes, _, _ = self._call({"battery_level": 0.5})
		self.assertEqual(len(writes), 1)
		_, writes, core, _ = self._call(None)
		self.assertEqual(writes, [])
		core.assert_called_once()

	def test_malformed_device_info_is_ignored_and_never_fails_the_registration(self):
		for bad in ("not json", "[1,2]", "7", {"junk": 1}, {}, "x" * 20000):
			result, writes, _, _ = self._call(bad)
			self.assertEqual(result["install_id"], "row-1")
			self.assertEqual(writes, [], bad if isinstance(bad, dict) else str(bad)[:20])

	def test_an_error_while_storing_facts_does_not_fail_registration(self):
		from upande_mobile.api import devices as dev

		with (
			patch.object(dev, "_register_install", MagicMock(return_value={"install_id": "row-1"})),
			patch.object(mon, "_write_snapshots", side_effect=RuntimeError("db")),
			patch.object(frappe, "db", MagicMock()),
			patch.object(frappe, "log_error") as log,
		):
			result = dev.register_install(install_id="abcdef12-3456", app="My App", device_info={"battery_level": 0.1})
		self.assertEqual(result["install_id"], "row-1")
		log.assert_called_once()


if __name__ == "__main__":
	unittest.main()
