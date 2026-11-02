# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# GitHub data for the per-app page of the mobile monitor: an app's recent releases
# (their notes, and whether they carry an APK) and its latest GitHub Actions runs.
#
# Everything here runs on the SERVER, from the hourly check or an admin's refresh
# button, and the result is cached on the Mobile App record; a browser never talks to
# GitHub and never receives a token. Rules:
#
#   * the only host ever requested is api.github.com, over https, for a repo that
#     passed validate_github_repo(); a redirect is followed only to the same host;
#   * a token (site_config `upande_mobile_github_token`, or the older
#     `mobile_app_github_token`) is sent only to that host, only as an
#     Authorization header, and is scrubbed from every message that is stored or shown;
#   * conditional requests (ETag / If-None-Match): an unchanged answer is a 304, which
#     GitHub does not count against the rate limit;
#   * any failure leaves the previous data in place; the caller records the message.
#
# Release notes are untrusted text. `notes_to_blocks` turns them into a small structure
# (headings, bullets, paragraphs; bold, inline code, http(s) links) that the page renders
# with text nodes. No HTML from GitHub is ever kept, passed on or interpreted.
#
# Pure functions apart from the HTTP call and the token lookup: no site or database needed.

import json
import re
from urllib.parse import quote, urlsplit

import requests

from upande_mobile.validation import InvalidSource, validate_github_repo

API_HOST = "api.github.com"
WEB_HOST = "github.com"
HTTP_TIMEOUT = 15
HTTP_HEADERS = {
	"User-Agent": "upande-mobile-version-check",
	"Accept": "application/vnd.github+json",
	"X-GitHub-Api-Version": "2022-11-28",
}
MAX_REDIRECTS = 2

MAX_RELEASES = 10
RELEASES_PER_PAGE = 15  # drafts, pre-releases and legacy tags are dropped from these
MAX_RUNS = 5
MAX_BODY_BYTES = 8 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
TRUNCATED_MARK = "\n\n... (release notes shortened; open the release on GitHub for the rest)"

RUN_STATUSES = ("queued", "in_progress", "completed", "waiting", "requested", "pending")
RUN_CONCLUSIONS = (
	"success", "failure", "cancelled", "skipped", "timed_out", "neutral", "action_required", "stale", "startup_failure",
)


class GitHubError(Exception):
	"""GitHub could not be read. The message is safe to store and show (token scrubbed)."""


class GitHubRateLimited(GitHubError):
	"""GitHub's rate limit is used up; `reset` is the epoch second it resets, or None."""

	def __init__(self, message, reset=None):
		super().__init__(message)
		self.reset = reset


# --------------------------------------------------------------------------- #
# token
# --------------------------------------------------------------------------- #

_TOKEN_SHAPE = re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{10,}|\bgithub_pat_[A-Za-z0-9_]{10,}")


def github_token():
	"""The site's GitHub token, or None. `upande_mobile_github_token` wins over the older name."""
	try:
		import frappe

		value = frappe.conf.get("upande_mobile_github_token") or frappe.conf.get("mobile_app_github_token")
	except Exception:
		return None
	value = str(value).strip() if value else ""
	return value or None


def scrub(text, token=None):
	"""`text` with the configured token (and anything shaped like a GitHub token) removed."""
	text = str(text)
	token = token or github_token()
	if token:
		text = text.replace(token, "[token]")
	return _TOKEN_SHAPE.sub("[token]", text)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #


def _is_api_url(url):
	parts = urlsplit(str(url or ""))
	try:
		port = parts.port
	except ValueError:
		return False
	return (
		parts.scheme == "https" and (parts.hostname or "").lower() == API_HOST and not parts.username and port in (None, 443)
	)


def api_get(path, etag=None, token=None, params=None):
	"""GET https://api.github.com<path>. Returns (status, parsed json or None, etag).

	Status 304 returns (304, None, etag): the caller keeps what it has. Raises GitHubRateLimited or
	GitHubError (message scrubbed) for everything else that is not a 200.
	"""
	token = token or github_token()
	headers = dict(HTTP_HEADERS)
	if etag:
		headers["If-None-Match"] = str(etag)[:200]
	if token:
		headers["Authorization"] = f"Bearer {token}"
	url = f"https://{API_HOST}{path}"
	try:
		response = None
		for _hop in range(MAX_REDIRECTS + 1):
			response = requests.get(
				url, headers=headers, params=params, timeout=HTTP_TIMEOUT, allow_redirects=False
			)
			params = None
			if response.status_code in (301, 302, 307, 308):
				location = response.headers.get("Location") or ""
				if not _is_api_url(location):
					raise GitHubError("GitHub redirected to a host that is not allowed.")
				url = location
				continue
			break
		else:
			raise GitHubError("GitHub redirected too many times.")
	except GitHubError:
		raise
	except requests.exceptions.Timeout:
		raise GitHubError("GitHub did not answer in time.") from None
	except requests.exceptions.ConnectionError:
		raise GitHubError("Could not reach GitHub.") from None
	except requests.exceptions.RequestException as exc:
		raise GitHubError(f"GitHub request failed: {scrub(exc, token)[:200]}") from None

	status = response.status_code
	new_etag = response.headers.get("ETag") or etag
	if status == 304:
		return 304, None, new_etag
	if status in (403, 429) and (
		str(response.headers.get("X-RateLimit-Remaining")) == "0" or status == 429 or response.headers.get("Retry-After")
	):
		reset = response.headers.get("X-RateLimit-Reset")
		reset = int(reset) if str(reset or "").isdigit() else None
		raise GitHubRateLimited("GitHub rate limit reached." + ("" if token else " A token raises the limit."), reset)
	if status == 401:
		raise GitHubError("GitHub rejected the configured token.")
	if status == 404:
		raise GitHubError("Repository not found, or it is private (GitHub answers 404 for both).")
	if status >= 400:
		raise GitHubError(f"GitHub answered {status}.")
	content = response.content or b""
	if len(content) > MAX_RESPONSE_BYTES:
		raise GitHubError("GitHub's answer was too large to read.")
	try:
		return status, json.loads(content), new_etag
	except ValueError:
		raise GitHubError("GitHub sent an answer that could not be read.") from None


# --------------------------------------------------------------------------- #
# normalising what GitHub sends
# --------------------------------------------------------------------------- #


def _text(value, limit=200):
	if value is None:
		return ""
	return str(value).replace("\x00", "").strip()[:limit]


def clip_body(text, limit=MAX_BODY_BYTES):
	"""`text` cut to at most `limit` UTF-8 bytes (never mid-character), with a marker when it was cut."""
	text = _text(text, limit=10**7)
	raw = text.encode("utf-8")
	if len(raw) <= limit:
		return text
	room = max(0, limit - len(TRUNCATED_MARK.encode("utf-8")))
	cut = raw[:room].decode("utf-8", errors="ignore")
	# prefer a line boundary when one is near the end
	newline = cut.rfind("\n")
	if newline > len(cut) * 0.8:
		cut = cut[:newline]
	return cut.rstrip() + TRUNCATED_MARK


def safe_github_url(url, repo, fallback):
	"""`url` when it is an https github.com page inside this repo, otherwise the fallback."""
	parts = urlsplit(str(url or ""))
	try:
		port = parts.port
	except ValueError:
		return fallback
	if (
		parts.scheme == "https"
		and (parts.hostname or "").lower() == WEB_HOST
		and not parts.username
		and port in (None, 443)
		and parts.path.lower().startswith(f"/{repo.lower()}/")
	):
		return f"https://{WEB_HOST}{parts.path}"
	return fallback


def normalize_release(raw, repo):
	"""One release as stored, or None for a draft, pre-release, `legacy*` tag or a release with no tag."""
	if not isinstance(raw, dict) or raw.get("draft") or raw.get("prerelease"):
		return None
	tag = _text(raw.get("tag_name"), 100)
	if not tag or tag.lower().startswith("legacy"):
		return None
	assets = [a for a in (raw.get("assets") or []) if isinstance(a, dict)]
	apks = [a for a in assets if _text(a.get("name")).lower().endswith(".apk")]
	# the asset named for its version (app_v1.2.0.apk) rather than an alias
	apks.sort(key=lambda a: tag.lstrip("vV") not in _text(a.get("name")))
	size = apks[0].get("size") if apks else None
	return {
		"tag": tag,
		"version": tag.lstrip("vV"),
		"name": _text(raw.get("name"), 200) or tag,
		"body": clip_body(raw.get("body")),
		"published_at": _text(raw.get("published_at") or raw.get("created_at"), 40),
		"url": safe_github_url(raw.get("html_url"), repo, f"https://{WEB_HOST}/{repo}/releases/tag/{quote(tag)}"),
		"has_apk": bool(apks),
		"apk_name": _text(apks[0].get("name"), 120) if apks else "",
		"apk_size": int(size) if isinstance(size, (int, float)) and size >= 0 else None,
	}


def normalize_releases(data, repo, limit=MAX_RELEASES):
	"""The newest `limit` publishable releases from GitHub's list, newest first."""
	out = []
	for raw in data if isinstance(data, list) else []:
		release = normalize_release(raw, repo)
		if release:
			out.append(release)
		if len(out) >= limit:
			break
	return out


def normalize_run(raw, repo):
	"""One workflow run as stored, or None when it has no id."""
	if not isinstance(raw, dict) or not isinstance(raw.get("id"), int):
		return None
	run_id = raw["id"]
	commit = raw.get("head_commit") if isinstance(raw.get("head_commit"), dict) else {}
	title = _text(raw.get("display_title"), 400) or _text(commit.get("message"), 400)
	status = _text(raw.get("status"), 30)
	conclusion = _text(raw.get("conclusion"), 30)
	return {
		"id": run_id,
		"name": _text(raw.get("name"), 100),
		"status": status if status in RUN_STATUSES else "",
		"conclusion": conclusion if conclusion in RUN_CONCLUSIONS else "",
		"branch": _text(raw.get("head_branch"), 100),
		"title": title.splitlines()[0][:200] if title else "",
		"event": _text(raw.get("event"), 30),
		"started_at": _text(raw.get("run_started_at") or raw.get("created_at"), 40),
		"updated_at": _text(raw.get("updated_at"), 40),
		"url": safe_github_url(raw.get("html_url"), repo, f"https://{WEB_HOST}/{repo}/actions/runs/{run_id}"),
	}


def normalize_runs(data, repo, limit=MAX_RUNS):
	runs = data.get("workflow_runs") if isinstance(data, dict) else None
	out = []
	for raw in runs if isinstance(runs, list) else []:
		run = normalize_run(raw, repo)
		if run:
			out.append(run)
		if len(out) >= limit:
			break
	return out


def fetch_releases(repo, etag=None, token=None):
	"""(releases, etag) for the repo, or (None, etag) when GitHub says nothing changed."""
	repo = validate_github_repo(repo)
	if not repo:
		raise GitHubError("GitHub Repo is not set.")
	status, data, new_etag = api_get(f"/repos/{repo}/releases", etag, token, {"per_page": RELEASES_PER_PAGE})
	if status == 304:
		return None, new_etag
	return normalize_releases(data, repo), new_etag


def fetch_runs(repo, etag=None, token=None):
	"""(runs, etag) for the repo's newest workflow runs, or (None, etag) when nothing changed."""
	repo = validate_github_repo(repo)
	if not repo:
		raise GitHubError("GitHub Repo is not set.")
	status, data, new_etag = api_get(f"/repos/{repo}/actions/runs", etag, token, {"per_page": MAX_RUNS})
	if status == 304:
		return None, new_etag
	return normalize_runs(data, repo), new_etag


# --------------------------------------------------------------------------- #
# release notes -> safe blocks
# --------------------------------------------------------------------------- #

MAX_BLOCKS = 120
MAX_TEXT = 600
MAX_HREF = 500
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_BULLET = re.compile(r"^[-*+]\s+(.*)$")
# a link target may hold one level of balanced parentheses, e.g. https://example.com/Foo_(bar)
_INLINE = re.compile(r"\*\*([^*\n]+)\*\*|`([^`\n]+)`|\[([^\]\n]+)\]\(((?:[^()\s]|\([^()\s]*\))+)\)")


def safe_href(url):
	"""`url` when it is a plain http(s) link with a host, otherwise None (so no javascript:, data:, ...)."""
	url = str(url or "").strip()
	if not url or len(url) > MAX_HREF or any(ord(c) < 33 or ord(c) == 127 for c in url):
		return None
	parts = urlsplit(url)
	if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
		return None
	return url


def inline_nodes(text):
	"""[{k: 'text'|'b'|'code'|'a', v, href?}] for one line of notes. Text is data, never markup."""
	text = _text(text, MAX_TEXT)
	nodes, pos = [], 0
	for match in _INLINE.finditer(text):
		if match.start() > pos:
			nodes.append({"k": "text", "v": text[pos : match.start()]})
		bold, code, label, href = match.groups()
		if bold is not None:
			nodes.append({"k": "b", "v": bold})
		elif code is not None:
			nodes.append({"k": "code", "v": code})
		else:
			safe = safe_href(href)
			# an unsafe link stays visible as the text it was, without being a link
			nodes.append({"k": "a", "v": label, "href": safe} if safe else {"k": "text", "v": match.group(0)})
		pos = match.end()
	if pos < len(text):
		nodes.append({"k": "text", "v": text[pos:]})
	# neighbouring plain pieces (an unsafe link kept as text, then what follows) read as one
	merged = []
	for node in nodes:
		if node["k"] == "text" and merged and merged[-1]["k"] == "text":
			merged[-1] = {"k": "text", "v": merged[-1]["v"] + node["v"]}
		else:
			merged.append(node)
	return merged


def notes_to_blocks(body):
	"""Release notes as [{t: 'h'|'p'|'ul', ...}], from a tiny markdown subset.

	Headings, `-`/`*` bullets, paragraphs, **bold**, `code` and [links](http://...). Anything else
	(HTML, images, tables, code fences) is kept as plain text: the page renders every node with
	text nodes, so nothing here can become markup.
	"""
	blocks, paragraph, bullets = [], [], []

	def flush_paragraph():
		if paragraph:
			blocks.append({"t": "p", "inline": inline_nodes(" ".join(paragraph))})
			paragraph.clear()

	def flush_bullets():
		if bullets:
			blocks.append({"t": "ul", "items": [inline_nodes(item) for item in bullets]})
			bullets.clear()

	for line in str(body or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
		if len(blocks) >= MAX_BLOCKS:
			break
		stripped = line.strip()
		if stripped.startswith("```"):
			continue  # fence markers are dropped; the lines between stay as text
		if not stripped:
			flush_paragraph()
			flush_bullets()
			continue
		heading = _HEADING.match(stripped)
		if heading:
			flush_paragraph()
			flush_bullets()
			blocks.append({"t": "h", "level": min(len(heading.group(1)) + 2, 6), "inline": inline_nodes(heading.group(2))})
			continue
		bullet = _BULLET.match(stripped)
		if bullet:
			flush_paragraph()
			bullets.append(bullet.group(1))
			continue
		flush_bullets()
		paragraph.append(stripped)
	flush_paragraph()
	flush_bullets()
	return blocks[:MAX_BLOCKS]


# --------------------------------------------------------------------------- #
# the stored blob
# --------------------------------------------------------------------------- #


def load_data(raw):
	"""The Mobile App's github_data as a dict (empty when missing or damaged)."""
	if isinstance(raw, dict):
		return raw
	try:
		data = json.loads(raw) if raw else {}
	except ValueError:
		return {}
	return data if isinstance(data, dict) else {}


def dump_data(data):
	return json.dumps(data, separators=(",", ":"), ensure_ascii=False)


__all__ = [
	"GitHubError",
	"GitHubRateLimited",
	"InvalidSource",
	"api_get",
	"clip_body",
	"dump_data",
	"fetch_releases",
	"fetch_runs",
	"github_token",
	"inline_nodes",
	"load_data",
	"normalize_release",
	"normalize_releases",
	"normalize_run",
	"normalize_runs",
	"notes_to_blocks",
	"safe_github_url",
	"safe_href",
	"scrub",
]
