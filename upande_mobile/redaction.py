# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Server-side redaction for crash / API reports. The app redacts before it sends;
# this runs again on everything received, because the server must never store a
# credential even when a client is old, buggy or hostile. Pure functions: no
# site or database needed.

import json
import re
from urllib.parse import parse_qsl, quote, urlsplit

REDACTED = "[redacted]"

# A key is sensitive when its letters-and-digits form CONTAINS one of these...
SENSITIVE_SUBSTRINGS = (
	"token",
	"secret",
	"password",
	"passwd",
	"pwd",
	"apikey",
	"authorization",
	"cookie",
	"credential",
	"csrf",
	"session",
	"privatekey",
	"signature",
)
# ...or any of its words (split on punctuation and camelCase) is one of these.
SENSITIVE_WORDS = frozenset({"sid", "otp", "pin", "cvv", "cvc", "card", "auth", "usr", "passcode", "ssn"})

_WORD_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")

# Values that look like credentials wherever they appear (messages, stacks, bodies).
_VALUE_PATTERNS = (
	re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
	re.compile(r"(?i)\bbasic\s+[A-Za-z0-9+/=]{8,}"),
	# Frappe's `Authorization: token <key>:<secret>`
	re.compile(r"(?i)\btoken\s+[A-Za-z0-9]{6,}:[A-Za-z0-9]{6,}"),
	# JSON web tokens
	re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"),
)
# key=value / key: value / "key": "value" inside free text
_KV_IN_TEXT = re.compile(
	r"""(?ix)
	(?<![A-Za-z0-9_.-])
	(["']?)([A-Za-z0-9_.-]{0,40}?(?:token|secret|passw(?:or)?d|pwd|api[_-]?key|authorization|cookie|session|sid)[A-Za-z0-9_.-]{0,40})\1
	(\s{0,3}[:=]\s{0,3})
	("[^"]{0,2000}"|'[^']{0,2000}'|[^\s,&;}"']{1,2000})
	"""
)

MAX_DEPTH = 8
MAX_LIST_ITEMS = 100
MAX_DICT_ITEMS = 200
MAX_KEY_LENGTH = 100

BINARY_TYPES = ("multipart/", "application/octet-stream", "image/", "audio/", "video/", "application/pdf", "application/zip")


def is_sensitive_key(key):
	"""True for a header / field / query name that may carry a secret."""
	key = str(key or "")
	compact = re.sub(r"[^a-z0-9]", "", key.lower())
	if any(s in compact for s in SENSITIVE_SUBSTRINGS):
		return True
	return any(w.lower() in SENSITIVE_WORDS for w in _WORD_SPLIT.split(key) if w)


def redact_text(text, limit=None):
	"""Mask credential-shaped values inside free text, then cut to `limit` characters."""
	text = "" if text is None else str(text)
	for pattern in _VALUE_PATTERNS:
		text = pattern.sub(REDACTED, text)
	text = _KV_IN_TEXT.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(1)}{m.group(3)}{REDACTED}", text)
	if limit and len(text) > limit:
		text = text[:limit] + f"…[truncated {len(text) - limit} chars]"
	return text


def redact_value(value, extra_keys=(), depth=0):
	"""A JSON-like value with every sensitive key's value replaced, bounded in size and depth."""
	if depth > MAX_DEPTH:
		return "[too deep]"
	if isinstance(value, dict):
		out = {}
		for i, (k, v) in enumerate(value.items()):
			if i >= MAX_DICT_ITEMS:
				out["…"] = f"{len(value) - MAX_DICT_ITEMS} more keys"
				break
			k = str(k)[:MAX_KEY_LENGTH]
			if _sensitive(k, extra_keys):
				out[k] = REDACTED
			else:
				out[k] = redact_value(v, extra_keys, depth + 1)
		return out
	if isinstance(value, (list, tuple)):
		items = [redact_value(v, extra_keys, depth + 1) for v in list(value)[:MAX_LIST_ITEMS]]
		if len(value) > MAX_LIST_ITEMS:
			items.append(f"…{len(value) - MAX_LIST_ITEMS} more items")
		return items
	if isinstance(value, str):
		return redact_text(value, 2000)
	if isinstance(value, (int, float, bool)) or value is None:
		return value
	return redact_text(str(value), 200)


def _sensitive(key, extra_keys):
	if is_sensitive_key(key):
		return True
	low = str(key).lower()
	return any(str(e).strip().lower() and str(e).strip().lower() in low for e in extra_keys)


def redact_headers(headers, extra_keys=(), max_items=60, max_value=1000):
	"""{name: value} with sensitive headers masked. Accepts a dict or a list of [name, value] pairs."""
	if isinstance(headers, (list, tuple)):
		pairs = [(p[0], p[1]) for p in headers if isinstance(p, (list, tuple)) and len(p) >= 2]
	elif isinstance(headers, dict):
		pairs = list(headers.items())
	else:
		return {}
	out = {}
	for name, value in pairs[:max_items]:
		name = str(name)[:MAX_KEY_LENGTH]
		if _sensitive(name, extra_keys):
			out[name] = REDACTED
		else:
			out[name] = redact_text(str(value), max_value)
	return out


def redact_url(url, extra_keys=(), limit=2000):
	"""The URL with credentials, sensitive query values and credential-shaped text masked."""
	url = "" if url is None else str(url).strip()
	try:
		parts = urlsplit(url)
	except ValueError:
		return redact_text(url, limit)
	netloc = parts.netloc
	if "@" in netloc:
		netloc = REDACTED + "@" + netloc.rsplit("@", 1)[1]
	query = ""
	if parts.query:
		pairs = []
		for k, v in parse_qsl(parts.query, keep_blank_values=True):
			pairs.append(f"{quote(k, safe='')}={quote(REDACTED if _sensitive(k, extra_keys) else redact_text(v, 300), safe='[]')}")
		query = "&".join(pairs)
	rebuilt = (f"{parts.scheme}://" if parts.scheme else "") + netloc + parts.path
	if query:
		rebuilt += "?" + query
	if parts.fragment:
		rebuilt += "#" + redact_text(parts.fragment, 100)
	return redact_text(rebuilt, limit) if not parts.scheme and not netloc else rebuilt[:limit]


def _content_type(headers):
	for name, value in (headers or {}).items():
		if str(name).lower() == "content-type":
			return str(value).lower()
	return ""


def redact_body(body, headers=None, extra_keys=(), max_bytes=8192):
	"""A request / response body as redacted text, capped at `max_bytes`, or a size note for binary content.

	JSON is parsed, redacted key by key and re-serialised; form bodies (a=1&b=2)
	are redacted by key; anything else is masked by pattern. A body the client
	already replaced with a note is kept as is.
	"""
	if body is None or body == "":
		return ""
	ctype = _content_type(headers)
	if any(ctype.startswith(t) for t in BINARY_TYPES):
		size = len(body) if isinstance(body, (str, bytes)) else 0
		return f"[{ctype.split(';')[0] or 'binary'} body, {size} bytes]"
	if isinstance(body, bytes):
		try:
			body = body.decode("utf-8")
		except UnicodeDecodeError:
			return f"[binary body, {len(body)} bytes]"
	if not isinstance(body, str):
		text = json.dumps(redact_value(body, extra_keys), ensure_ascii=False, default=str)
		return _cut(text, max_bytes)
	if "\x00" in body[:512]:
		return f"[binary body, {len(body)} chars]"
	stripped = body.strip()
	if stripped[:1] in "{[":
		try:
			parsed = json.loads(stripped)
		except ValueError:
			parsed = None
		if parsed is not None:
			return _cut(json.dumps(redact_value(parsed, extra_keys), ensure_ascii=False, default=str), max_bytes)
	if "=" in stripped and "\n" not in stripped[:200] and re.match(r"^[^\s=&]+=[^\s]*(&[^\s=&]+=[^\s]*)*$", stripped):
		pairs = []
		for k, v in parse_qsl(stripped, keep_blank_values=True):
			pairs.append(f"{quote(k, safe='')}={quote(REDACTED if _sensitive(k, extra_keys) else redact_text(v, 500), safe='[]')}")
		return _cut("&".join(pairs), max_bytes)
	return _cut(redact_text(body), max_bytes)


def _cut(text, max_bytes):
	if len(text) <= max_bytes:
		return text
	return text[:max_bytes] + f"…[truncated {len(text) - max_bytes} chars]"
