# Copyright (c) 2026, Upande and contributors
# Validation tests for register_app and the URL/repo guards: no site, no database.
#   ../../env/bin/python -W ignore -m unittest upande_mobile.tests.test_register_app

import unittest
from unittest.mock import MagicMock, patch

import frappe

from upande_mobile import validation as v
from upande_mobile.api import apps, ota


class TestValidation(unittest.TestCase):
	def test_repo(self):
		self.assertEqual(v.validate_github_repo("acme/my-app"), "acme/my-app")
		self.assertEqual(v.validate_github_repo(""), "")
		self.assertEqual(v.validate_github_repo(None), "")
		for bad in ("acme", "acme/", "/x", "a/b/c", "acme/x y", "acme/app.git", "-a/b", "acme/..", "https://github.com/a/b"):
			with self.assertRaises(v.InvalidSource, msg=bad):
				v.validate_github_repo(bad)

	def test_url_ok(self):
		for url in (
			"https://acme.github.io/my-app/ota",
			"https://acme.github.io/my-app/ota/",
			"https://raw.githubusercontent.com/acme/my-app/gh-pages/ota",
		):
			self.assertEqual(v.validate_ota_base_url(url, extra_hosts=()), url.rstrip("/"))
		self.assertEqual(v.validate_ota_base_url("", extra_hosts=()), "")

	def test_url_rejected(self):
		bad = (
			"http://acme.github.io/x",
			"ftp://acme.github.io/x",
			"https://github.io/x",
			"https://evil.example.com/ota",
			"https://acme.github.io.evil.com/ota",
			"https://evilgithub.io/ota",
			"https://127.0.0.1/ota",
			"https://169.254.169.254/latest",
			"https://[::1]/ota",
			"https://localhost/ota",
			"https://user:pw@acme.github.io/ota",
			"https://acme.github.io:8443/ota",
			"https://acme.github.io/ota?x=1",
			"https://acme.github.io/ota#frag",
			"https://acme.github.io/" + "a" * 300,
			"//acme.github.io/ota",
			"acme.github.io/ota",
		)
		for url in bad:
			with self.assertRaises(v.InvalidSource, msg=url):
				v.validate_ota_base_url(url, extra_hosts=())

	def test_allow_list(self):
		url = "https://cdn.example.org/my-app/ota"
		with self.assertRaises(v.InvalidSource):
			v.validate_ota_base_url(url, extra_hosts=())
		self.assertEqual(v.validate_ota_base_url(url, extra_hosts=("CDN.example.org",)), url)
		# a listed host never admits its look-alikes
		with self.assertRaises(v.InvalidSource):
			v.validate_ota_base_url("https://cdn.example.org.evil.com/x", extra_hosts=("cdn.example.org",))

	def test_is_safe(self):
		self.assertTrue(v.is_safe_base_url("https://acme.github.io/a"))
		self.assertFalse(v.is_safe_base_url("https://evil.example.com/a"))
		self.assertFalse(v.is_safe_base_url(""))


class TestRegisterApp(unittest.TestCase):
	def call(self, roles=("System Manager",), existing=None, **kwargs):
		db = MagicMock()
		db.get_value.return_value = existing
		doc = MagicMock()
		doc.name = kwargs.get("app_name")
		doc.app_name = existing and kwargs.get("app_name")
		doc.enabled = 1
		doc.github_repo = None
		doc.ota_base_url = None
		with (
			patch.object(frappe, "get_roles", return_value=list(roles)),
			patch.object(frappe, "db", db),
			patch.object(frappe, "get_doc", return_value=doc),
			patch.object(frappe, "new_doc", return_value=doc),
			patch.object(apps, "_", side_effect=lambda m: m),  # translation needs a site cache
			# frappe.throw logs to the bench's log dir, which a siteless test run lacks
			patch.object(frappe, "throw", side_effect=lambda msg, exc=frappe.ValidationError, *a, **k: (_ for _ in ()).throw(exc(msg))),
			patch.object(v, "configured_hosts", return_value=()),
		):
			return apps.register_app(**kwargs), doc

	def test_requires_system_manager(self):
		with self.assertRaises(frappe.PermissionError):
			self.call(roles=("Guest",), app_name="My App")

	def test_bad_inputs(self):
		for kwargs in (
			{"app_name": "x"},
			{"app_name": "My App", "github_repo": "nope"},
			{"app_name": "My App", "ota_base_url": "http://acme.github.io/ota"},
			{"app_name": "My App", "ota_base_url": "https://evil.example.com/ota"},
		):
			with self.assertRaises(frappe.ValidationError, msg=str(kwargs)):
				self.call(**kwargs)

	def test_create(self):
		result, doc = self.call(
			app_name="My App", github_repo="acme/my-app", ota_base_url="https://acme.github.io/my-app/ota/"
		)
		self.assertTrue(result["created"])
		self.assertEqual(doc.ota_base_url, "https://acme.github.io/my-app/ota")
		self.assertEqual(doc.github_repo, "acme/my-app")
		doc.insert.assert_called_once()
		doc.save.assert_not_called()

	def test_update_is_idempotent_and_keeps_omitted_fields(self):
		result, doc = self.call(existing="My App", app_name="My App", enabled=0)
		self.assertFalse(result["created"])
		self.assertIsNone(doc.github_repo)  # not passed: untouched
		self.assertEqual(doc.enabled, 0)
		doc.save.assert_called_once()
		doc.insert.assert_not_called()


class TestFetchGuards(unittest.TestCase):
	def test_base_url_for_refuses_unsafe_stored_url(self):
		row = MagicMock(enabled=1, ota_base_url="https://evil.example.com/ota")
		db = MagicMock()
		db.get_value.return_value = row
		with patch.object(frappe, "db", db), patch.object(v, "configured_hosts", return_value=()):
			self.assertEqual(ota.base_url_for("My App"), "")
		row.ota_base_url = "https://acme.github.io/my-app/ota/"
		with patch.object(frappe, "db", db), patch.object(v, "configured_hosts", return_value=()):
			self.assertEqual(ota.base_url_for("My App"), "https://acme.github.io/my-app/ota")


if __name__ == "__main__":
	unittest.main()
