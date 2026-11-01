# Copyright (c) 2026, Upande and contributors
# Pure-helper tests: no site, no database. Run:
#   ../../env/bin/python -m unittest upande_mobile.tests.test_helpers

import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from upande_mobile.api import devices, ota


class FakeCache:
	def __init__(self):
		self.data = {}

	def get_value(self, key):
		return self.data.get(key)

	def set_value(self, key, value, expires_in_sec=None):
		self.data[key] = value


class TestVersions(unittest.TestCase):
	def test_parse_version(self):
		self.assertEqual(devices.parse_version("1.2.3"), (1, 2, 3))
		self.assertEqual(devices.parse_version("v1.2-rc1"), (1, 2, 0))
		self.assertEqual(devices.parse_version("1"), (1, 0, 0))
		self.assertIsNone(devices.parse_version("abc"))
		self.assertIsNone(devices.parse_version(""))
		self.assertIsNone(devices.parse_version(None))

	def test_runtime(self):
		self.assertEqual(devices.parse_runtime("101.2"), (101, 2))
		self.assertIsNone(devices.parse_runtime("101"))
		self.assertEqual(devices.runtime_of("1.2.9"), "1.2")
		with patch.object(devices, "runtime_major_base", return_value=100):
			self.assertEqual(devices.runtime_of("1.2.9"), "101.2")
			self.assertEqual(devices.start_runtimes()[0], "101.0")
		self.assertEqual(devices.runtime_of("nope"), "")

	def test_device_key(self):
		self.assertEqual(devices.device_key_of(""), "")
		self.assertEqual(devices.device_key_of("9774d56d682e549c"), "")
		key = devices.device_key_of("a1b2c3d4e5f60718")
		self.assertEqual(len(key), 64)
		self.assertEqual(devices.device_key_of(key), key)  # already hashed: kept


class TestInstallStatus(unittest.TestCase):
	NOW = datetime(2026, 10, 31, 12, 0, 0)
	LATEST = {"latest_version": "1.2.5", "latest_runtime": "1.2", "latest_apk_version": "1.2.0"}

	def status(self, version, runtime=None, last_seen=None, latest=None, dormant=7):
		return devices.install_status(
			version, runtime, last_seen or self.NOW - timedelta(hours=1), self.LATEST if latest is None else latest, dormant, self.NOW
		)

	def test_current(self):
		self.assertEqual(self.status("1.2.5"), ("current", ""))

	def test_behind_ota(self):
		status, update_to = self.status("1.2.3", "1.2")
		self.assertEqual(status, "behind_ota")
		self.assertIn("1.2.5", update_to)

	def test_needs_apk(self):
		status, update_to = self.status("1.1.9", "1.1")
		self.assertEqual(status, "needs_apk")
		self.assertIn("APK 1.2.0", update_to)
		self.assertIn("1.2.5 over the air", update_to)

	def test_needs_apk_not_published(self):
		latest = dict(self.LATEST, latest_apk_version="")
		self.assertIn("not published", self.status("1.1.9", "1.1", latest=latest)[1])

	def test_ahead(self):
		self.assertEqual(self.status("1.2.9", "101.2")[0], "ahead")
		self.assertEqual(self.status("1.3.0", "101.3")[0], "ahead")

	def test_dormant_and_unknown(self):
		old = self.NOW - timedelta(days=30)
		self.assertEqual(self.status("1.2.5", last_seen=old), ("dormant", ""))
		self.assertEqual(self.status("1.2.5", last_seen=old, dormant=0)[0], "current")
		self.assertEqual(self.status("1.2.5", latest={})[0], "unknown")
		self.assertEqual(devices.install_status("1.2.5", None, self.NOW, None, 7, self.NOW)[0], "unknown")
		self.assertEqual(self.status("garbage")[0], "unknown")


class TestOtaManifest(unittest.TestCase):
	BASE = "https://acme.github.io/myapp/ota"

	def setUp(self):
		self.cache = FakeCache()
		self.calls = []

	def run_manifest(self, upstream, app="My App", base=BASE, headers=None, runtime="101.2", platform=None):
		headers = headers or {}

		def fetch(url):
			self.calls.append(url)
			return upstream(url)

		with (
			patch.object(ota, "_cache", return_value=self.cache),
			patch.object(ota, "base_url_for", return_value=base),
			patch.object(ota, "fetch_upstream", side_effect=fetch),
			patch.object(ota, "_header", side_effect=lambda n, f=None: headers.get(n, f or "")),
		):
			return ota.manifest(app=app, runtime=runtime, platform=platform)

	def assert_protocol(self, response):
		self.assertEqual(response.headers["expo-protocol-version"], "1")
		self.assertEqual(response.headers["expo-sfv-version"], "0")
		self.assertIn("no-store", response.headers["cache-control"])

	def test_serves_manifest_with_protocol_headers(self):
		response = self.run_manifest(lambda url: (200, '{"id":"x"}'))
		self.assertEqual(response.status_code, 200)
		self.assert_protocol(response)
		self.assertEqual(response.get_data(as_text=True), '{"id":"x"}')
		self.assertEqual(self.calls, [f"{self.BASE}/android/101.2/manifest.json"])

	def test_cached_for_repeat_calls(self):
		self.run_manifest(lambda url: (200, "{}"))
		self.run_manifest(lambda url: (200, "{}"))
		self.assertEqual(len(self.calls), 1)

	def test_404_is_204_no_update_and_cached(self):
		response = self.run_manifest(lambda url: (404, ""), base="https://example.test/ota")
		self.assertEqual(response.status_code, 204)
		self.assert_protocol(response)
		self.run_manifest(lambda url: (404, ""), base="https://example.test/ota")
		self.assertEqual(len(self.calls), 1)

	def test_pages_404_falls_back_to_raw_branch(self):
		def upstream(url):
			if "raw.githubusercontent.com" in url:
				return 200, '{"u":"https://acme.github.io/myapp/ota/a.js"}'
			return 404, ""

		response = self.run_manifest(upstream)
		self.assertEqual(response.status_code, 200)
		self.assertIn("https://raw.githubusercontent.com/acme/myapp/gh-pages/ota/a.js", response.get_data(as_text=True))
		self.assertEqual(self.calls[1], "https://raw.githubusercontent.com/acme/myapp/gh-pages/ota/android/101.2/manifest.json")

	def test_upstream_error_and_exception_are_204_never_500(self):
		with patch.object(ota, "_log_once"):
			self.assertEqual(self.run_manifest(lambda url: (503, "down")).status_code, 204)
			self.cache.data.clear()

			def boom(url):
				raise OSError("net")

			response = self.run_manifest(boom)
		self.assertEqual(response.status_code, 204)
		self.assert_protocol(response)

	def test_oversize_manifest_is_refused(self):
		with patch.object(ota, "_log_once"):
			response = self.run_manifest(lambda url: (200, "x" * (ota.MAX_MANIFEST_BYTES + 1)))
		self.assertEqual(response.status_code, 204)

	def test_headers_beat_query_and_bad_input_is_204(self):
		self.run_manifest(lambda url: (200, "{}"), headers={"expo-runtime-version": "101.5", "expo-platform": "ios"}, runtime="1.0")
		self.assertEqual(self.calls, [f"{self.BASE}/ios/101.5/manifest.json"])
		for bad in ("../x", "abc", ""):
			self.assertEqual(self.run_manifest(lambda url: (200, "{}"), runtime=bad).status_code, 204)
		self.assertEqual(self.run_manifest(lambda url: (200, "{}"), platform="web").status_code, 204)

	def test_unknown_app_is_204(self):
		before = len(self.calls)
		response = self.run_manifest(lambda url: (200, "{}"), base="")
		self.assertEqual(response.status_code, 204)
		self.assertEqual(len(self.calls), before)

	def test_pages_to_raw(self):
		self.assertEqual(ota.pages_to_raw(self.BASE)[1], "https://raw.githubusercontent.com/acme/myapp/gh-pages/")
		self.assertEqual(ota.pages_to_raw("https://example.test/ota"), (None, None))


if __name__ == "__main__":
	unittest.main()
