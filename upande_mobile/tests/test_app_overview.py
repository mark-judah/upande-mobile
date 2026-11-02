# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Unit tests for the per-app page: GitHub fetching (mocked HTTP), the token and host guards, release-note
# parsing, adoption math, permissions and the refresh cooldown. No site or database needed:
#   ../../env/bin/python -W ignore -m unittest upande_mobile.tests.test_app_overview

import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
import requests
from requests.structures import CaseInsensitiveDict

from upande_mobile import github as gh
from upande_mobile.api import devices as dev
from upande_mobile.api import monitor as mon

SECRET = "ghp_SECRETSECRETSECRET1234567890"


def _throw(msg, exc=frappe.ValidationError, *a, **k):
	raise exc(msg)


class FakeResponse:
	def __init__(self, status=200, body=None, headers=None, raw=None):
		self.status_code = status
		self.content = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
		self.headers = CaseInsensitiveDict(headers or {})

	def raise_for_status(self):
		if self.status_code >= 400:
			raise requests.exceptions.HTTPError(f"{self.status_code}")

	def json(self):
		return json.loads(self.content)


def rel(tag, body="", draft=False, pre=False, apk=False, url=None):
	return {
		"tag_name": tag, "name": f"Release {tag}", "body": body, "draft": draft, "prerelease": pre,
		"published_at": "2026-11-01T10:00:00Z", "created_at": "2026-11-01T09:00:00Z",
		"html_url": url or f"https://github.com/acme/my-app/releases/tag/{tag}",
		"assets": [{"name": f"my_app_{tag}.apk", "size": 12345}, {"name": "notes.txt", "size": 1}] if apk else [],
	}


def run(run_id, conclusion="success", status="completed", url=None, title="ship it"):
	return {
		"id": run_id, "name": "Release", "status": status, "conclusion": conclusion, "head_branch": "main",
		"display_title": title, "event": "push", "run_started_at": "2026-11-01T10:00:00Z", "updated_at": "2026-11-01T10:05:00Z",
		"html_url": url or f"https://github.com/acme/my-app/actions/runs/{run_id}",
	}


def router(routes):
	"""A stand-in for requests.get that answers by URL suffix and records every call."""
	calls = []

	def fake(url, headers=None, params=None, timeout=None, allow_redirects=None):
		calls.append({"url": url, "headers": dict(headers or {}), "params": params, "allow_redirects": allow_redirects})
		for suffix, answer in routes.items():
			if url.endswith(suffix):
				answer = answer(len(calls)) if callable(answer) else answer
				if isinstance(answer, Exception):
					raise answer
				return answer
		raise AssertionError(f"unexpected URL {url}")

	return fake, calls


class TestClipBody(unittest.TestCase):
	def test_short_text_is_unchanged(self):
		self.assertEqual(gh.clip_body("## Fixes\n- one"), "## Fixes\n- one")
		self.assertEqual(gh.clip_body(None), "")

	def test_long_text_is_cut_to_the_byte_limit_with_a_marker(self):
		out = gh.clip_body("line of notes\n" * 2000)
		self.assertLessEqual(len(out.encode()), gh.MAX_BODY_BYTES)
		self.assertTrue(out.endswith(gh.TRUNCATED_MARK))

	def test_never_cuts_a_multibyte_character_in_half(self):
		out = gh.clip_body("é" * 20000, limit=1000)
		self.assertLessEqual(len(out.encode()), 1000)
		out.encode("utf-8")  # valid UTF-8, no half characters
		self.assertIn("é", out)

	def test_nul_bytes_are_removed(self):
		self.assertEqual(gh.clip_body("a\x00b"), "ab")


class TestNotesToBlocks(unittest.TestCase):
	ALLOWED_KEYS = {"t", "level", "inline", "items", "k", "v", "href"}

	def keys(self, value, found=None):
		found = set() if found is None else found
		if isinstance(value, dict):
			found.update(value)
			for v in value.values():
				self.keys(v, found)
		elif isinstance(value, list):
			for v in value:
				self.keys(v, found)
		return found

	def test_structure(self):
		blocks = gh.notes_to_blocks("## What's new\n- **Fast** start\n- uses `expo-updates`\n\nSee [the docs](https://example.com/docs) now.")
		self.assertEqual([b["t"] for b in blocks], ["h", "ul", "p"])
		self.assertEqual(blocks[0]["level"], 4)
		self.assertEqual(blocks[1]["items"][0], [{"k": "b", "v": "Fast"}, {"k": "text", "v": " start"}])
		self.assertEqual(blocks[1]["items"][1][1], {"k": "code", "v": "expo-updates"})
		link = [n for n in blocks[2]["inline"] if n["k"] == "a"][0]
		self.assertEqual((link["v"], link["href"]), ("the docs", "https://example.com/docs"))

	def test_html_is_plain_text_never_markup(self):
		for evil in ("<script>alert(1)</script>", "<img src=x onerror=alert(1)>", "<a href='javascript:alert(1)'>x</a>", "</p><iframe src=//x>"):
			blocks = gh.notes_to_blocks(evil)
			self.assertEqual(blocks, [{"t": "p", "inline": [{"k": "text", "v": evil}]}])

	def test_nested_markup_stays_text(self):
		blocks = gh.notes_to_blocks("**<b>x</b>** and `<i>y</i>` and [<u>z</u>](https://ok.example/a)")
		inline = blocks[0]["inline"]
		self.assertEqual(inline[0], {"k": "b", "v": "<b>x</b>"})
		self.assertEqual(inline[2], {"k": "code", "v": "<i>y</i>"})
		self.assertEqual(inline[4], {"k": "a", "v": "<u>z</u>", "href": "https://ok.example/a"})

	def test_only_http_and_https_links_survive(self):
		for href in (
			"javascript:alert(1)", "JaVaScRiPt:alert(1)", "data:text/html;base64,PHNjcmlwdD4=", "vbscript:x", "file:///etc/passwd",
			"//evil.example/x", "ftp://example.com/x", "https://user:pw@evil.example/x", "https:///nohost", "https://" + "a" * 520 + ".example",
		):
			blocks = gh.notes_to_blocks(f"[click]({href})")
			kinds = {n["k"] for n in blocks[0]["inline"]}
			self.assertEqual(kinds, {"text"}, href)
			self.assertEqual(blocks[0]["inline"][0]["v"], f"[click]({href})")
		ok = gh.notes_to_blocks("[go](http://example.com/a?b=1)")[0]["inline"][0]
		self.assertEqual(ok["href"], "http://example.com/a?b=1")
		paren = gh.notes_to_blocks("see [wiki](https://example.com/Foo_(bar)) here")[0]["inline"]
		self.assertEqual([n["k"] for n in paren], ["text", "a", "text"])
		self.assertEqual(paren[1]["href"], "https://example.com/Foo_(bar)")
		self.assertEqual(paren[2]["v"], " here")

	def test_a_link_with_a_title_or_space_is_not_a_link(self):
		blocks = gh.notes_to_blocks('[a](https://ok.example "title")')
		self.assertEqual({n["k"] for n in blocks[0]["inline"]}, {"text"})

	def test_output_only_ever_has_the_known_keys(self):
		body = "# H\n<script>x</script>\n- [a](javascript:x) **b** `c`\n```\ncode line\n```\n<img onerror=x>"
		self.assertLessEqual(self.keys(gh.notes_to_blocks(body)), self.ALLOWED_KEYS)

	def test_code_fence_markers_are_dropped_but_the_lines_stay(self):
		blocks = gh.notes_to_blocks("```js\nrun()\n```")
		self.assertEqual(blocks, [{"t": "p", "inline": [{"k": "text", "v": "run()"}]}])

	def test_block_count_is_capped(self):
		blocks = gh.notes_to_blocks("\n\n".join(f"paragraph {i}" for i in range(1000)))
		self.assertLessEqual(len(blocks), gh.MAX_BLOCKS)

	def test_empty(self):
		self.assertEqual(gh.notes_to_blocks(""), [])
		self.assertEqual(gh.notes_to_blocks(None), [])


class TestNormalize(unittest.TestCase):
	def test_unpublishable_releases_are_skipped(self):
		for raw in (rel("v1.0.0", draft=True), rel("v1.0.0", pre=True), rel("legacy-1.0"), rel("Legacy.2"), {"tag_name": ""}, "junk", None):
			self.assertIsNone(gh.normalize_release(raw, "acme/my-app"), raw)

	def test_release_fields_and_apk_detection(self):
		r = gh.normalize_release(rel("v1.2.0", body="## Hi", apk=True), "acme/my-app")
		self.assertEqual((r["tag"], r["version"], r["has_apk"], r["apk_size"]), ("v1.2.0", "1.2.0", True, 12345))
		self.assertEqual(r["apk_name"], "my_app_v1.2.0.apk")
		self.assertEqual(r["url"], "https://github.com/acme/my-app/releases/tag/v1.2.0")
		plain = gh.normalize_release(rel("v1.2.1"), "acme/my-app")
		self.assertFalse(plain["has_apk"])
		self.assertIsNone(plain["apk_size"])

	def test_urls_must_be_inside_the_repo_on_github(self):
		fallback = "https://github.com/acme/my-app/releases/tag/v1.0.0"
		for bad in (
			"https://evil.example/acme/my-app/releases/tag/v1.0.0", "http://github.com/acme/my-app/releases", "javascript:alert(1)",
			"https://github.com/other/repo/releases/tag/v1.0.0", "https://user:pw@github.com/acme/my-app/x",
			"https://github.com.evil.example/acme/my-app/x", "",
		):
			self.assertEqual(gh.normalize_release(rel("v1.0.0", url=bad), "acme/my-app")["url"], fallback, bad)
		good = "https://github.com/acme/my-app/releases/tag/v1.0.0"
		self.assertEqual(gh.normalize_release(rel("v1.0.0", url=good), "acme/my-app")["url"], good)

	def test_body_is_clipped(self):
		r = gh.normalize_release(rel("v1.0.0", body="x" * 50000), "acme/my-app")
		self.assertLessEqual(len(r["body"].encode()), gh.MAX_BODY_BYTES)

	def test_releases_list_is_limited_and_filtered(self):
		data = [rel("v1.0.%d" % i) for i in range(30)]
		data.insert(2, rel("v9.9.9", draft=True))
		out = gh.normalize_releases(data, "acme/my-app")
		self.assertEqual(len(out), gh.MAX_RELEASES)
		self.assertNotIn("v9.9.9", [r["tag"] for r in out])
		self.assertEqual(gh.normalize_releases({"not": "a list"}, "acme/my-app"), [])

	def test_run_fields_and_whitelists(self):
		r = gh.normalize_run(run(7, conclusion="success"), "acme/my-app")
		self.assertEqual((r["id"], r["status"], r["conclusion"], r["branch"], r["title"]), (7, "completed", "success", "main", "ship it"))
		weird = gh.normalize_run(run(8, conclusion="<script>", status="whatever", title="first line\nsecond"), "acme/my-app")
		self.assertEqual((weird["status"], weird["conclusion"], weird["title"]), ("", "", "first line"))
		self.assertIsNone(gh.normalize_run({"name": "no id"}, "acme/my-app"))
		self.assertEqual(gh.normalize_run(run(9, url="https://evil.example/x"), "acme/my-app")["url"], "https://github.com/acme/my-app/actions/runs/9")

	def test_runs_are_limited(self):
		data = {"workflow_runs": [run(i) for i in range(1, 20)]}
		self.assertEqual(len(gh.normalize_runs(data, "acme/my-app")), gh.MAX_RUNS)
		self.assertEqual(gh.normalize_runs([], "acme/my-app"), [])


class TestApiGet(unittest.TestCase):
	def get(self, fake, *args, token=None, **kwargs):
		with patch.object(gh, "github_token", return_value=token), patch.object(gh.requests, "get", side_effect=fake):
			return gh.api_get(*args, **kwargs)

	def test_only_the_api_host_is_requested(self):
		fake, calls = router({"/repos/acme/my-app/releases": FakeResponse(200, [], {"ETag": 'W/"a"'})})
		status, data, etag = self.get(fake, "/repos/acme/my-app/releases", params={"per_page": 15})
		self.assertEqual((status, data, etag), (200, [], 'W/"a"'))
		self.assertEqual(calls[0]["url"], "https://api.github.com/repos/acme/my-app/releases")
		self.assertIs(calls[0]["allow_redirects"], False)
		self.assertNotIn("Authorization", calls[0]["headers"])

	def test_token_is_sent_as_a_bearer_header_and_an_etag_as_if_none_match(self):
		fake, calls = router({"/x": FakeResponse(304, headers={"ETag": '"e1"'})})
		status, data, etag = self.get(fake, "/x", etag='"e1"', token=SECRET)
		self.assertEqual((status, data, etag), (304, None, '"e1"'))
		self.assertEqual(calls[0]["headers"]["Authorization"], f"Bearer {SECRET}")
		self.assertEqual(calls[0]["headers"]["If-None-Match"], '"e1"')

	def test_rate_limit_is_its_own_error_with_the_reset_time(self):
		fake, _ = router({"/x": FakeResponse(403, {"message": "API rate limit exceeded"}, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1793536800"})})
		with self.assertRaises(gh.GitHubRateLimited) as ctx:
			self.get(fake, "/x")
		self.assertEqual(ctx.exception.reset, 1793536800)
		self.assertIn("token", str(ctx.exception))
		fake, _ = router({"/x": FakeResponse(429)})
		with self.assertRaises(gh.GitHubRateLimited):
			self.get(fake, "/x", token=SECRET)

	def test_other_failures(self):
		cases = ((403, "answered 403"), (404, "not found"), (401, "rejected the configured token"), (500, "answered 500"))
		for status, text in cases:
			fake, _ = router({"/x": FakeResponse(status)})
			with self.assertRaises(gh.GitHubError) as ctx:
				self.get(fake, "/x", token=SECRET)
			self.assertIn(text, str(ctx.exception))
			self.assertNotIn(SECRET, str(ctx.exception))

	def test_a_redirect_to_another_host_is_refused(self):
		for location in ("https://evil.example/steal", "http://api.github.com/x", "//api.github.com/x", "https://api.github.com.evil.example/x", "https://user@api.github.com/x", "/relative"):
			fake, calls = router({"/x": FakeResponse(302, headers={"Location": location})})
			with self.assertRaises(gh.GitHubError, msg=location):
				self.get(fake, "/x", token=SECRET)
			self.assertEqual(len(calls), 1)

	def test_a_redirect_within_the_api_host_is_followed_without_extra_params(self):
		fake, calls = router({
			"/repos/acme/old/releases": FakeResponse(301, headers={"Location": "https://api.github.com/repositories/42/releases"}),
			"/repositories/42/releases": FakeResponse(200, [{"ok": 1}]),
		})
		status, data, _ = self.get(fake, "/repos/acme/old/releases", params={"per_page": 15})
		self.assertEqual((status, data), (200, [{"ok": 1}]))
		self.assertEqual(calls[0]["params"], {"per_page": 15})
		self.assertIsNone(calls[1]["params"])

	def test_redirect_loops_stop(self):
		fake, calls = router({"/x": FakeResponse(302, headers={"Location": "https://api.github.com/x"})})
		with self.assertRaises(gh.GitHubError):
			self.get(fake, "/x")
		self.assertEqual(len(calls), gh.MAX_REDIRECTS + 1)

	def test_network_errors_are_messages_without_the_token(self):
		for exc, text in (
			(requests.exceptions.Timeout("timed out"), "did not answer"),
			(requests.exceptions.ConnectionError("dns"), "Could not reach"),
			(requests.exceptions.RequestException(f"boom while sending Bearer {SECRET}"), "request failed"),
		):
			fake, _ = router({"/x": exc})
			with self.assertRaises(gh.GitHubError) as ctx:
				self.get(fake, "/x", token=SECRET)
			self.assertIn(text, str(ctx.exception))
			self.assertNotIn(SECRET, str(ctx.exception))

	def test_oversized_and_unreadable_answers(self):
		fake, _ = router({"/x": FakeResponse(200, raw=b"[" + b" " * (gh.MAX_RESPONSE_BYTES + 1) + b"]")})
		with self.assertRaises(gh.GitHubError):
			self.get(fake, "/x")
		fake, _ = router({"/x": FakeResponse(200, raw=b"<html>not json</html>")})
		with self.assertRaises(gh.GitHubError):
			self.get(fake, "/x")


class TestTokenHandling(unittest.TestCase):
	def test_scrub_removes_the_configured_token_and_token_shapes(self):
		self.assertNotIn(SECRET, gh.scrub(f"failed with {SECRET}", SECRET))
		self.assertEqual(gh.scrub("x ghp_abcdefghijklmnop y", "other"), "x [token] y")
		self.assertEqual(gh.scrub("github_pat_11ABCDEFG0abcdefghijkl_xyz", None), "[token]")

	def test_token_lookup_prefers_the_new_key_and_still_reads_the_old_one(self):
		with patch.object(frappe, "conf", {"upande_mobile_github_token": "new", "mobile_app_github_token": "old"}):
			self.assertEqual(gh.github_token(), "new")
		with patch.object(frappe, "conf", {"mobile_app_github_token": "old"}):
			self.assertEqual(gh.github_token(), "old")
		with patch.object(frappe, "conf", {}):
			self.assertIsNone(gh.github_token())

	def test_fetches_refuse_a_bad_or_missing_repo_before_any_request(self):
		with patch.object(gh.requests, "get", side_effect=AssertionError("no request expected")):
			with self.assertRaises(gh.InvalidSource):
				gh.fetch_releases("evil/../x")
			with self.assertRaises(gh.GitHubError):
				gh.fetch_runs("")


class TestRefreshGithub(unittest.TestCase):
	def doc(self, **kw):
		base = dict(github_repo="acme/my-app", github_data=None, github_error=None, github_checked_at=None, latest_version="1.2.7", latest_apk_version="1.2.0")
		base.update(kw)
		return SimpleNamespace(**base)

	def refresh(self, doc, fake, token=None):
		with (
			patch.object(gh, "github_token", return_value=token),
			patch.object(gh.requests, "get", side_effect=fake),
			patch.object(dev, "now", return_value="2026-11-01 12:00:00"),
		):
			dev._refresh_github(doc)
		return doc

	def ok_routes(self, releases=None, runs=None, rel_headers=None, run_headers=None):
		return {
			"/releases": FakeResponse(200, releases if releases is not None else [rel("v1.2.7", "## Notes\n- a"), rel("v1.2.0", apk=True)], rel_headers or {"ETag": '"r1"'}),
			"/actions/runs": FakeResponse(200, {"workflow_runs": runs if runs is not None else [run(1), run(2, "failure")]}, run_headers or {"ETag": '"w1"'}),
		}

	def test_stores_releases_runs_etags_and_times(self):
		fake, calls = router(self.ok_routes())
		doc = self.refresh(self.doc(), fake)
		data = gh.load_data(doc.github_data)
		self.assertEqual([r["tag"] for r in data["releases"]], ["v1.2.7", "v1.2.0"])
		self.assertEqual([r["id"] for r in data["runs"]], [1, 2])
		self.assertEqual(data["etags"], {"releases": '"r1"', "runs": '"w1"'})
		self.assertEqual(data["fetched_at"], "2026-11-01 12:00:00")
		self.assertEqual(doc.github_checked_at, "2026-11-01 12:00:00")
		self.assertIsNone(doc.github_error)
		self.assertEqual(len(calls), 2)

	def test_an_unchanged_answer_keeps_the_data_and_sends_the_etag(self):
		fake, _ = router(self.ok_routes())
		doc = self.refresh(self.doc(), fake)
		before = gh.load_data(doc.github_data)
		fake, calls = router({"/releases": FakeResponse(304, headers={"ETag": '"r1"'}), "/actions/runs": FakeResponse(304, headers={"ETag": '"w1"'})})
		self.refresh(doc, fake)
		after = gh.load_data(doc.github_data)
		self.assertEqual(after["releases"], before["releases"])
		self.assertEqual(after["runs"], before["runs"])
		self.assertEqual([c["headers"].get("If-None-Match") for c in calls], ['"r1"', '"w1"'])
		self.assertIsNone(doc.github_error)

	def test_a_rate_limit_keeps_old_releases_and_still_updates_the_runs(self):
		fake, _ = router(self.ok_routes())
		doc = self.refresh(self.doc(), fake)
		old_releases = gh.load_data(doc.github_data)["releases"]
		fake, _ = router({
			"/releases": FakeResponse(403, {"message": "rate limit"}, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1793536800"}),
			"/actions/runs": FakeResponse(200, {"workflow_runs": [run(3)]}, {"ETag": '"w2"'}),
		})
		self.refresh(doc, fake)
		data = gh.load_data(doc.github_data)
		self.assertEqual(data["releases"], old_releases)
		self.assertEqual([r["id"] for r in data["runs"]], [3])
		self.assertIn("Releases:", doc.github_error)
		self.assertIn("rate limit", doc.github_error)
		self.assertIn("resets at", doc.github_error)

	def test_when_both_fail_everything_stays_and_fetched_at_is_not_moved(self):
		fake, _ = router(self.ok_routes())
		doc = self.refresh(self.doc(), fake)
		before = gh.load_data(doc.github_data)
		fake, _ = router({"/releases": requests.exceptions.ConnectionError("down"), "/actions/runs": requests.exceptions.Timeout("slow")})
		with patch.object(dev, "now", return_value="2026-11-01 13:00:00"):
			self.refresh(doc, fake)
		after = gh.load_data(doc.github_data)
		self.assertEqual((after["releases"], after["runs"]), (before["releases"], before["runs"]))
		self.assertEqual(after["fetched_at"], before["fetched_at"])
		self.assertEqual(doc.github_checked_at, "2026-11-01 12:00:00")  # this call's `now`
		self.assertIn("Releases:", doc.github_error)
		self.assertIn("Actions runs:", doc.github_error)

	def test_the_token_never_reaches_the_stored_error(self):
		boom = requests.exceptions.RequestException(f"HTTPSConnectionPool failed: Authorization: Bearer {SECRET}")
		fake, calls = router({"/releases": boom, "/actions/runs": boom})
		doc = self.refresh(self.doc(), fake, token=SECRET)
		self.assertEqual(calls[0]["headers"]["Authorization"], f"Bearer {SECRET}")  # sent to GitHub only
		self.assertNotIn(SECRET, doc.github_error)
		self.assertNotIn(SECRET, doc.github_data)

	def test_an_unexpected_exception_is_contained_and_scrubbed(self):
		def explode(*a, **k):
			raise RuntimeError(f"bug near {SECRET}")

		doc = self.doc()
		with (
			patch.object(gh, "github_token", return_value=SECRET),
			patch.object(gh, "fetch_releases", side_effect=explode),
			patch.object(gh, "fetch_runs", return_value=([], None)),
			patch.object(dev, "now", return_value="2026-11-01 12:00:00"),
		):
			dev._refresh_github(doc)
		self.assertNotIn(SECRET, doc.github_error)
		self.assertIn("Releases:", doc.github_error)

	def test_no_repo_means_no_requests_and_no_error(self):
		doc = self.doc(github_repo=None, github_error="old")
		with patch.object(gh.requests, "get", side_effect=AssertionError("no request expected")):
			dev._refresh_github(doc)
		self.assertIsNone(doc.github_error)

	def test_an_invalid_repo_is_an_error_and_no_request(self):
		doc = self.doc(github_repo="evil/../x")
		with patch.object(gh.requests, "get", side_effect=AssertionError("no request expected")):
			dev._refresh_github(doc)
		self.assertIn("owner/repo", doc.github_error)

	def test_the_ota_and_apk_fields_are_never_touched(self):
		fake, _ = router({"/releases": requests.exceptions.ConnectionError("down"), "/actions/runs": requests.exceptions.ConnectionError("down")})
		doc = self.refresh(self.doc(), fake)
		self.assertEqual((doc.latest_version, doc.latest_apk_version), ("1.2.7", "1.2.0"))

	def test_apk_lookup_uses_the_same_token_helper(self):
		seen = {}

		def fake(url, headers=None, timeout=None):
			seen["headers"] = headers
			return FakeResponse(200, [])

		with patch.object(gh, "github_token", return_value=SECRET), patch.object(dev.requests, "get", side_effect=fake):
			self.assertEqual(dev.latest_apk({"github_repo": "acme/my-app"}), (None, None))
		self.assertEqual(seen["headers"]["Authorization"], f"Bearer {SECRET}")


class TestAdoptionMath(unittest.TestCase):
	ROWS = [
		("1.2.7", "1.2", "current", 40),
		("v1.2.7", "1.2", "dormant", 4),
		("1.2.6", "1.2", "behind_ota", 8),
		("1.1.0", "1.1", "needs_apk", 2),
		("", "", "unknown", 1),
	]

	def test_distribution(self):
		d = mon.version_distribution(self.ROWS, "1.2.7")
		self.assertEqual((d["total"], d["active"], d["on_latest"]), (55, 51, 44))
		self.assertEqual(d["on_latest_percent"], 80.0)
		self.assertEqual(d["on_latest_percent_active"], 78.4)  # 40 of 51 active; the dormant one is left out of both sides
		versions = d["by_version"]
		self.assertEqual([v["version"] for v in versions][-3:], ["1.2.6", "1.1.0", ""])  # newest first, unreadable last
		self.assertTrue(all(v["latest"] for v in versions[:2]))
		self.assertFalse(any(v["latest"] for v in versions[2:]))
		self.assertEqual(sum(v["devices"] for v in versions), 55)
		self.assertEqual([r["runtime"] for r in d["by_runtime"]], ["1.2", "1.1", ""])
		self.assertEqual(d["by_runtime"][0]["devices"], 52)
		self.assertEqual(d["by_status"], {"current": 40, "dormant": 4, "behind_ota": 8, "needs_apk": 2, "unknown": 1})

	def test_empty_and_unknown_latest(self):
		d = mon.version_distribution([], "1.2.7")
		self.assertEqual((d["total"], d["on_latest"], d["on_latest_percent"], d["by_version"]), (0, 0, 0.0, []))
		d = mon.version_distribution(self.ROWS, None)
		self.assertEqual(d["on_latest"], 0)
		self.assertFalse(any(v["latest"] for v in d["by_version"]))
		d = mon.version_distribution([("1.0.0", "1.0", None, 0), ("1.0.0", "1.0", None, 3)], "9.9.9")
		self.assertEqual((d["total"], d["by_status"]), (3, {"unknown": 3}))

	def test_percentages_round_to_one_decimal(self):
		d = mon.version_distribution([("1.0.0", "1.0", "current", 1), ("0.9.0", "0.9", "current", 2)], "1.0.0")
		self.assertEqual([v["percent"] for v in d["by_version"]], [33.3, 66.7])

	def test_summary_text(self):
		self.assertEqual(mon.version_summary("1.2.7", "1.2", "1.2.0"), "OTA 1.2.7 on runtime 1.2; newest APK 1.2.0")
		self.assertEqual(mon.version_summary("1.2.7", "", ""), "OTA 1.2.7")
		self.assertEqual(mon.version_summary("", "", ""), "No published version found yet")

	def test_newest_version_and_pages_url(self):
		self.assertEqual(mon._newest_version("1.2.7", "1.3.0"), "1.3.0")
		self.assertEqual(mon._newest_version("junk", "1.0.0"), "1.0.0")
		self.assertEqual(mon._newest_version("", None), "")
		with patch("upande_mobile.validation.configured_hosts", return_value=()):
			self.assertEqual(mon._pages_url("https://acme.github.io/my-app/ota"), "https://acme.github.io/my-app/")
			self.assertEqual(mon._pages_url("https://evil.example.com/ota"), "")
			self.assertEqual(mon._pages_url(""), "")


class TestGithubView(unittest.TestCase):
	def row(self, **kw):
		releases = [gh.normalize_release(rel("v1.2.8", "## New\n- x"), "acme/my-app"), gh.normalize_release(rel("v1.2.7", "**Latest** one `code`", apk=False), "acme/my-app"), gh.normalize_release(rel("v1.2.0", "<script>alert(1)</script>", apk=True), "acme/my-app")]
		base = {
			"github_repo": "acme/my-app", "github_error": None, "github_checked_at": "2026-11-01 12:00:00",
			"github_data": gh.dump_data({"releases": releases, "runs": [gh.normalize_run(run(1), "acme/my-app")], "fetched_at": "2026-11-01 11:00:00"}),
		}
		base.update(kw)
		return base

	def test_marks_the_release_for_the_latest_version_and_renders_notes_as_blocks(self):
		view = mon._github_view(self.row(), "1.2.7")
		self.assertEqual(view["whats_new"], "v1.2.7")
		self.assertTrue(view["whats_new_exact"])
		self.assertEqual([r["latest"] for r in view["releases"]], [False, True, False])
		self.assertEqual([r["kind"] for r in view["releases"]], ["ota", "ota", "apk"])
		self.assertEqual(view["releases"][1]["notes"][0]["inline"][0], {"k": "b", "v": "Latest"})
		self.assertEqual(view["releases"][2]["notes"], [{"t": "p", "inline": [{"k": "text", "v": "<script>alert(1)</script>"}]}])
		self.assertNotIn("body", view["releases"][0])
		self.assertEqual(view["runs"][0]["id"], 1)

	def test_falls_back_to_the_newest_release_when_none_matches(self):
		view = mon._github_view(self.row(), "9.0.0")
		self.assertEqual((view["whats_new"], view["whats_new_exact"]), ("v1.2.8", False))
		view = mon._github_view(self.row(), "")
		self.assertEqual(view["whats_new"], "v1.2.8")

	def test_stale_data_is_flagged_when_there_is_an_error(self):
		view = mon._github_view(self.row(github_error="Releases: GitHub rate limit reached."), "1.2.7")
		self.assertTrue(view["stale"])
		self.assertEqual(view["fetched_at"], "2026-11-01 11:00:00")
		fresh = mon._github_view(self.row(), "1.2.7")
		self.assertFalse(fresh["stale"])

	def test_no_repo_shows_nothing_and_says_so(self):
		view = mon._github_view(self.row(github_repo=""), "1.2.7")
		self.assertEqual((view["repo_set"], view["releases"], view["runs"], view["whats_new"]), (False, [], [], ""))

	def test_damaged_blob_is_an_empty_view(self):
		view = mon._github_view(self.row(github_data="{not json"), "1.2.7")
		self.assertEqual((view["releases"], view["runs"]), ([], []))

	def test_urls_are_revalidated_when_read(self):
		data = {"releases": [{"tag": "v1", "version": "1", "name": "v1", "body": "", "url": "javascript:alert(1)"}], "runs": [{"id": 5, "url": "https://evil.example/x"}]}
		view = mon._github_view(self.row(github_data=gh.dump_data(data)), "1")
		self.assertEqual(view["releases"][0]["url"], "https://github.com/acme/my-app/releases")
		self.assertEqual(view["runs"][0]["url"], "https://github.com/acme/my-app/actions/runs/5")


class TestPermissionsAndCooldown(unittest.TestCase):
	def setUp(self):
		for patcher in (
			patch.object(frappe, "throw", side_effect=_throw),
			patch.object(mon, "_", side_effect=lambda m: m),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_both_endpoints_need_a_system_manager(self):
		for roles in (["Guest"], ["All", "Desk User"], []):
			with patch.object(frappe, "get_roles", return_value=roles):
				with self.assertRaises(frappe.PermissionError):
					mon.app_overview("My App")
				with self.assertRaises(frappe.PermissionError):
					mon.refresh_app("My App")

	def test_unknown_app_is_not_found(self):
		with patch.object(frappe, "get_roles", return_value=["System Manager"]), patch.object(frappe, "db", MagicMock(get_value=MagicMock(return_value=None))):
			with self.assertRaises(frappe.DoesNotExistError):
				mon.app_overview("Nope")
			with self.assertRaises(frappe.DoesNotExistError):
				mon.refresh_app("Nope")

	def refresh(self, cache, checked_at=None):
		db = MagicMock()
		db.get_value.side_effect = lambda doctype, name, field=None, **k: checked_at if field == "checked_at" else "My App"
		doc = MagicMock(checked_at="2026-11-01 12:00:00", github_error=None, last_error=None)
		with (
			patch.object(frappe, "get_roles", return_value=["System Manager"]),
			patch.object(frappe, "db", db),
			patch.object(frappe, "get_doc", return_value=doc),
			patch.object(mon, "_cooldown_cache", return_value=cache) if not isinstance(cache, Exception) else patch.object(mon, "_cooldown_cache", side_effect=cache),
			patch.object(mon, "_refresh_app") as refresh_app,
			patch.object(mon, "recompute_statuses") as recompute,
		):
			result = mon.refresh_app("My App")
		return result, refresh_app, recompute, doc

	def test_first_refresh_runs_and_starts_the_cooldown(self):
		cache = MagicMock()
		cache.get_value.return_value = None
		result, refresh_app, recompute, doc = self.refresh(cache)
		self.assertTrue(result["refreshed"])
		self.assertEqual(result["retry_in"], mon.REFRESH_COOLDOWN_SECONDS)
		refresh_app.assert_called_once_with(doc)
		recompute.assert_called_once_with(app="My App")
		doc.save.assert_called_once()
		args, kwargs = cache.set_value.call_args
		self.assertEqual(args[0], "upande_mobile:refresh_app:My App")
		self.assertEqual(kwargs["expires_in_sec"], mon.REFRESH_COOLDOWN_SECONDS)

	def test_a_second_click_inside_sixty_seconds_does_nothing(self):
		cache = MagicMock()
		cache.get_value.return_value = time.time() - 10
		result, refresh_app, recompute, doc = self.refresh(cache)
		self.assertFalse(result["refreshed"])
		self.assertTrue(48 <= result["retry_in"] <= 51)
		refresh_app.assert_not_called()
		recompute.assert_not_called()
		doc.save.assert_not_called()

	def test_after_the_cooldown_it_refreshes_again(self):
		cache = MagicMock()
		cache.get_value.return_value = time.time() - (mon.REFRESH_COOLDOWN_SECONDS + 1)
		result, refresh_app, _, _ = self.refresh(cache)
		self.assertTrue(result["refreshed"])
		refresh_app.assert_called_once()

	def test_without_a_cache_the_record_checked_time_is_the_fallback(self):
		from datetime import datetime, timedelta

		recent = datetime.now() - timedelta(seconds=5)
		result, refresh_app, _, _ = self.refresh(RuntimeError("redis down"), checked_at=recent)
		self.assertFalse(result["refreshed"])
		refresh_app.assert_not_called()
		old = datetime.now() - timedelta(hours=2)
		result, refresh_app, _, _ = self.refresh(RuntimeError("redis down"), checked_at=old)
		self.assertTrue(result["refreshed"])


class TestAppOverviewPayload(unittest.TestCase):
	"""The assembled page data: shape, JSON-safety, no token anywhere."""

	def call(self, row=None, sql_results=None):
		row = row or {
			"name": "My App", "enabled": 1, "enable_monitoring": 1, "github_repo": "acme/my-app", "ota_base_url": "https://acme.github.io/my-app/ota",
			"platform": "android", "latest_version": "1.2.7", "latest_runtime": "1.2", "latest_apk_version": "1.2.0",
			"published_at": "2026-11-01 10:00:00", "checked_at": "2026-11-01 11:00:00", "last_error": None, "github_checked_at": "2026-11-01 11:00:00",
			"github_error": None,
			"github_data": gh.dump_data({"releases": [gh.normalize_release(rel("v1.2.7", "- fixed"), "acme/my-app")], "runs": [gh.normalize_run(run(1), "acme/my-app")], "fetched_at": "2026-11-01 11:00:00"}),
		}
		db = MagicMock()
		db.get_value.side_effect = lambda doctype, name, field=None, **k: "My App" if field == "name" else frappe._dict(row)
		db.sql.side_effect = sql_results or [
			[("1.2.7", "1.2", "current", 3), ("1.2.6", "1.2", "behind_ota", 1)],
			[(4,)],
			[(2,)],
			[frappe._dict(install_id="abcd1234efgh", user="a@example.com", device_brand="Acme", device_model="X1", device_name="", app_version="1.2.6", runtime_version="1.2", status="behind_ota", update_to="1.2.7", last_seen="2026-11-01 09:00:00")],
		]
		with (
			patch.object(frappe, "get_roles", return_value=["System Manager"]),
			patch.object(frappe, "db", db),
			patch.object(frappe, "get_all", return_value=[frappe._dict(name="a@example.com", full_name="Ada Example")]),
			patch.object(mon, "now_datetime", return_value=__import__("datetime").datetime(2026, 11, 1, 12, 0, 0)),
			patch.object(mon, "_event_series", return_value=[{"day": "2026-11-01", "crash": 1, "error": 0, "api": 2, "log": 0}]),
			patch.object(mon, "_open_issues", return_value=[]),
			patch.object(mon, "_count_by", return_value=[{"label": "Acme X1", "n": 4}]),
			patch.object(mon, "_health_counts", return_value={"devices": 4, "low_battery": 1, "low_storage": 0, "offline": 0, "outdated_os": 0, "outdated_app": 1, "extra": 9}),
			patch("upande_mobile.validation.configured_hosts", return_value=()),
		):
			return mon.app_overview("My App")

	def test_shape_and_numbers(self):
		d = self.call()
		self.assertEqual(d["app"]["name"], "My App")
		self.assertEqual(d["version"]["summary"], "OTA 1.2.7 on runtime 1.2; newest APK 1.2.0")
		self.assertEqual(d["version"]["latest_overall"], "1.2.7")
		self.assertEqual(d["adoption"]["total"], 4)
		self.assertEqual((d["adoption"]["on_latest"], d["adoption"]["users_total"], d["adoption"]["users_on_latest"]), (3, 4, 2))
		self.assertEqual(d["adoption"]["behind_ota"], 1)
		self.assertEqual(d["adoption"]["behind"][0]["full_name"], "Ada Example")
		self.assertEqual(d["links"], {"repo": "https://github.com/acme/my-app", "releases": "https://github.com/acme/my-app/releases", "actions": "https://github.com/acme/my-app/actions", "pages": "https://acme.github.io/my-app/"})
		self.assertEqual(d["github"]["whats_new"], "v1.2.7")
		self.assertEqual(d["activity"]["health"], {"devices": 4, "low_battery": 1, "low_storage": 0, "offline": 0, "outdated_os": 0, "outdated_app": 1})
		self.assertEqual(d["refresh_cooldown"], mon.REFRESH_COOLDOWN_SECONDS)

	def test_the_payload_is_plain_json_and_has_no_token_or_raw_blob(self):
		with patch.object(frappe, "conf", {"upande_mobile_github_token": SECRET}), patch.object(gh, "github_token", return_value=SECRET):
			text = json.dumps(self.call())
		self.assertNotIn(SECRET, text)
		self.assertNotIn("github_data", text)
		self.assertNotIn("etags", text)

	def test_an_app_with_no_repo_and_no_devices_still_renders(self):
		row = {"name": "My App", "enabled": 0, "enable_monitoring": 1, "github_repo": None, "ota_base_url": None, "platform": "android", "latest_version": None, "latest_runtime": None, "latest_apk_version": None, "published_at": None, "checked_at": None, "last_error": None, "github_checked_at": None, "github_error": None, "github_data": None}
		d = self.call(row=row, sql_results=[[], [(0,)], []])  # no latest version: the users-on-latest query is skipped
		self.assertEqual((d["github"]["repo_set"], d["github"]["releases"]), (False, []))
		self.assertEqual((d["adoption"]["total"], d["adoption"]["behind"]), (0, []))
		self.assertEqual(d["version"]["summary"], "No published version found yet")
		self.assertEqual(d["links"]["repo"], "")
		json.dumps(d)


if __name__ == "__main__":
	unittest.main()
