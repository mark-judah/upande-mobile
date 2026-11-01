# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Grouping: reports of the same problem share one fingerprint, so they land on
# one Mobile Issue. Message text and URLs are normalised first (ids, numbers,
# hashes removed) so "Item ITM-0042 not found" and "Item ITM-0043 not found"
# are one problem. Pure functions: no site or database needed.

import hashlib
import re
from urllib.parse import unquote, urlsplit

_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_HEX = re.compile(r"\b(?:0x)?[0-9a-fA-F]{8,}\b")
_DOCNAME = re.compile(r"\b[A-Z]{2,}(?:-[A-Z0-9]+)*-\d[\w-]*\b")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_QUOTED = re.compile(r"""(["'`])(?:(?!\1).){1,80}\1""")
_URL_IN_TEXT = re.compile(r"https?://[^\s)\"']+")
_SPACES = re.compile(r"\s+")

TOP_FRAMES = 3
MAX_NORMALISED = 300


def normalise_message(message):
	"""The message with the parts that vary between occurrences replaced by placeholders."""
	text = _SPACES.sub(" ", str(message or "")).strip()
	text = _URL_IN_TEXT.sub(lambda m: "<url:" + path_template(m.group(0)) + ">", text)
	text = _UUID.sub("<id>", text)
	text = _DOCNAME.sub("<name>", text)
	text = _HEX.sub("<hex>", text)
	text = _QUOTED.sub("<str>", text)
	text = _NUMBER.sub("<n>", text)
	return text[:MAX_NORMALISED]


def path_template(url):
	"""`/api/resource/Sales%20Invoice/ACC-SINV-0001?x=1` -> `/api/resource/Sales Invoice/:id`; no host, no query."""
	try:
		path = urlsplit(str(url or "")).path or "/"
	except ValueError:
		path = str(url or "").split("?")[0]
	segments = []
	for segment in unquote(path).split("/"):
		if not segment:
			continue
		digits = any(c.isdigit() for c in segment)
		if segment.isdigit() or _UUID.fullmatch(segment) or _HEX.fullmatch(segment) or (digits and len(segment) >= 6):
			segments.append(":id")
		else:
			segments.append(segment)
	return "/" + "/".join(segments)


_FRAME_AT = re.compile(r"^\s*at\s+(?P<fn>[^(\s]+)?\s*\(?(?P<loc>[^)]*)\)?\s*$")
_FRAME_HERMES = re.compile(r"^(?P<fn>[^@\s]*)@(?P<loc>.+)$")


def stack_frames(stack, limit=TOP_FRAMES):
	"""Up to `limit` normalised `function@file` frames from a JS stack (V8 `at f (file:1:2)` or `f@file:1:2`)."""
	frames = []
	for line in str(stack or "").splitlines():
		line = line.strip()
		if not line or line.lower().startswith(("error", "typeerror", "referenceerror")) and ":" in line and "at " not in line:
			continue
		m = _FRAME_AT.match(line) or _FRAME_HERMES.match(line)
		if not m:
			continue
		fn = (m.group("fn") or "<anonymous>").strip()
		loc = (m.group("loc") or "").strip()
		loc = re.sub(r"[?#].*$", "", loc)  # query / bundle hash
		loc = re.sub(r"(:\d+)+$", "", loc)  # :line:col
		loc = loc.rsplit("/", 1)[-1]
		loc = re.sub(r"[0-9a-f]{8,}", "<hex>", loc)
		frames.append(f"{fn}@{loc}")
		if len(frames) >= limit:
			break
	return frames


def fingerprint(app, kind, message="", name="", stack="", request=None):
	"""(fingerprint, title, culprit) for one event.

	An API error groups by method + path template + status; anything else by
	kind + normalised message (+ error name) + the top three stack frames. The
	app is part of the key, so two apps never share an issue.
	"""
	kind = str(kind or "error")
	request = request if isinstance(request, dict) else None
	if kind == "api" and request:
		method = str(request.get("method") or "GET").upper()[:10]
		path = path_template(request.get("url"))
		status = str(request.get("status") or request.get("error") or "error")[:60]
		key = f"api|{method}|{path}|{status}"
		title = f"{method} {path} → {status}"
		culprit = path
	else:
		frames = stack_frames(stack)
		norm = normalise_message(message)
		key = f"{kind}|{str(name or '')[:60]}|{norm}|{'|'.join(frames)}"
		first = str(message or "").strip().splitlines()[0] if str(message or "").strip() else (name or kind)
		title = (f"{name}: " if name and not str(first).startswith(str(name)) else "") + str(first)
		culprit = frames[0] if frames else ""
	digest = hashlib.sha1(f"{app}\x1f{key}".encode()).hexdigest()
	return digest, title[:300], culprit[:140]
