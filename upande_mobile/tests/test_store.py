# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Unit tests for the app store's pure helpers. No site needed:
#   ../../env/bin/python -W ignore -m unittest upande_mobile.tests.test_store

import unittest

from upande_mobile.api import store


class TestStore(unittest.TestCase):
	def test_download_hosts(self):
		self.assertTrue(store.allowed_download_url("https://github.com/o/r/releases/download/v1/app.apk"))
		self.assertTrue(store.allowed_download_url("https://objects.githubusercontent.com/x/app.apk"))
		self.assertFalse(store.allowed_download_url("http://github.com/o/r/app.apk"))
		self.assertFalse(store.allowed_download_url("https://evil.example/app.apk"))
		self.assertFalse(store.allowed_download_url("https://github.com.evil.example/app.apk"))
		self.assertFalse(store.allowed_download_url("https://user@github.com/o/r/app.apk"))
		self.assertFalse(store.allowed_download_url("https://github.com:8443/o/r/app.apk"))
		self.assertFalse(store.allowed_download_url(""))

	def test_filename(self):
		self.assertEqual(store.apk_filename("Upande Sales", "1.4.2"), "Upande-Sales-1.4.2.apk")
		self.assertEqual(store.apk_filename("../../etc", "1/2"), "etc-1-2.apk")
		self.assertEqual(store.apk_filename("App", ""), "App.apk")


if __name__ == "__main__":
	unittest.main()
