# Copyright (c) 2026, Upande and contributors
# For license information, please see license.txt
#
# Notification outbox + inbox against a real site database (Expo is always a fake: no network).
#   bench --site <site> run-tests --app upande_mobile --module upande_mobile.tests.test_notifications
#
# `drain` commits (it has to, so other workers see its claims), so every test cleans up its own rows.

import json
import unittest
from datetime import UTC
from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, get_datetime, now_datetime

from upande_mobile import push
from upande_mobile.api import notifications as api

APP = "Notify Test App"
OTHER_APP = "Notify Test App 2"
TOKEN_A1 = "ExponentPushToken[testA1aaaaaaaa]"
TOKEN_A2 = "ExponentPushToken[testA2aaaaaaaa]"
TOKEN_B1 = "ExponentPushToken[testB1bbbbbbbb]"


class FakeExpo:
	"""Stands in for `push.http_post`. `answer(url, payload)` returns (status, body) or raises."""

	def __init__(self, answer=None):
		self.calls = []
		self.answer = answer or self.all_ok

	@staticmethod
	def all_ok(url, payload):
		return 200, {"data": [{"status": "ok", "id": f"ticket-{m['to']}"} for m in payload]}

	def __call__(self, url, payload):
		self.calls.append((url, payload))
		return self.answer(url, payload)

	@property
	def messages(self):
		return [m for url, payload in self.calls if url == push.EXPO_PUSH_URL for m in payload]


class NotificationTestBase(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		users = frappe.get_all(
			"User", filters={"enabled": 1, "name": ["not in", ["Guest", "Administrator"]]}, pluck="name", order_by="creation asc", limit_page_length=2
		)
		if len(users) < 2:
			raise unittest.SkipTest("needs two enabled users on the site")
		cls.user_a, cls.user_b = users

	def setUp(self):
		self._wipe()
		p = patch.object(push, "enabled", return_value=True)
		p.start()
		self.addCleanup(p.stop)
		self.addCleanup(self._wipe)
		self.addCleanup(frappe.set_user, "Administrator")

	def _wipe(self):
		frappe.db.rollback()
		frappe.db.delete(push.NOTIFICATION, {"app": ["in", [APP, OTHER_APP]]})
		frappe.db.delete(push.PUSH, {"app": ["in", [APP, OTHER_APP]]})
		frappe.db.commit()

	def token(self, user, token, app=APP):
		# The Link to Mobile App is skipped: the test app is not registered.
		frappe.get_doc({"doctype": push.PUSH, "app": app, "user": user, "token": token, "registered_at": now_datetime()}).insert(
			ignore_permissions=True, ignore_links=True
		)

	def notify(self, user=None, **kw):
		kw.setdefault("route", "/deployments")
		return push.notify_user(kw.pop("app", APP), user or self.user_a, kw.pop("title", "Deployment Deployed"), kw.pop("body", "app -> site"), **kw)

	def rows(self, **filters):
		return frappe.get_all(push.NOTIFICATION, filters=dict({"app": ["in", [APP, OTHER_APP]]}, **filters), fields=["*"], order_by="creation asc")

	def row(self, name=None):
		rows = self.rows(**({"name": name} if name else {}))
		self.assertEqual(len(rows), 1)
		return rows[0]

	def make_due(self, name=None):
		filters = {"name": name} if name else {"app": APP}
		frappe.db.set_value(push.NOTIFICATION, filters, "next_attempt_at", add_to_date(now_datetime(), minutes=-1), update_modified=False)
		frappe.db.commit()


class TestEnqueue(NotificationTestBase):
	def test_the_row_rolls_back_with_the_callers_transaction(self):
		self.assertEqual(self.notify(), 1)
		self.assertEqual(self.row().status, "Queued")
		frappe.db.rollback()
		self.assertEqual(self.rows(), [])

	def test_a_duplicate_dedupe_key_is_ignored(self):
		self.assertEqual(self.notify(dedupe_key="deployment:D-1:Deployed"), 1)
		self.assertEqual(self.notify(dedupe_key="deployment:D-1:Deployed"), 0)
		self.assertEqual(len(self.rows()), 1)
		# The key is per person and app; no key never dedupes.
		self.assertEqual(self.notify(self.user_b, dedupe_key="deployment:D-1:Deployed"), 1)
		self.assertEqual(self.notify(app=OTHER_APP, dedupe_key="deployment:D-1:Deployed"), 1)
		self.assertEqual(self.notify(), 1)
		self.assertEqual(self.notify(), 1)
		self.assertEqual(len(self.rows()), 5)

	def test_the_unique_index_catches_a_lost_race_without_hurting_the_caller(self):
		self.notify(dedupe_key="todo:T-1")
		frappe.db.commit()
		# The caller's own write in the same transaction must survive the failed insert.
		self.notify(self.user_b, title="callers own write")
		real_exists = frappe.db.exists
		message_log = list(getattr(frappe.local, "message_log", []) or [])

		lied = []

		def exists_without_dedupe_check(doctype, *a, **k):
			# The pre-insert dedupe check misses the row (as if a concurrent insert had not committed yet).
			if doctype == push.NOTIFICATION and not lied:
				lied.append(1)
				return False
			return real_exists(doctype, *a, **k)

		with patch.object(push.frappe.db, "exists", side_effect=exists_without_dedupe_check), patch.object(push.frappe, "log_error") as log:
			self.assertEqual(self.notify(dedupe_key="todo:T-1"), 0)
		log.assert_not_called()
		self.assertEqual(len(self.rows(dedupe_key="todo:T-1")), 1)
		self.assertEqual(len(self.rows(title="callers own write")), 1)
		self.assertEqual(list(getattr(frappe.local, "message_log", []) or []), message_log)

	def test_push_turned_off_still_writes_the_inbox_but_never_sends(self):
		self.token(self.user_a, TOKEN_A1)
		expo = FakeExpo()
		with patch.object(push, "enabled", return_value=False):
			self.assertEqual(self.notify(), 0)
			row = self.row()
			self.assertEqual((row.status, row.expo_ticket_ids), ("Sent", None))
			self.assertIsNotNone(row.sent_at)
			self.assertEqual(push.drain(post=expo)["claimed"], 0)
			push.check_receipts(post=expo)
		self.assertEqual(expo.calls, [])


class TestDrain(NotificationTestBase):
	def test_drain_sends_with_route_channel_and_the_ids_the_app_needs(self):
		self.token(self.user_a, TOKEN_A1)
		self.token(self.user_a, TOKEN_A2)
		self.notify(channel="events", kind="deployment", data={"kind": "event"})
		frappe.db.commit()
		expo = FakeExpo()
		summary = push.drain(post=expo)
		self.assertEqual((summary["claimed"], summary["Sent"]), (1, 1))
		row = self.row()
		self.assertEqual(sorted(m["to"] for m in expo.messages), sorted([TOKEN_A1, TOKEN_A2]))
		msg = expo.messages[0]
		self.assertEqual((msg["channelId"], msg["data"]["route"], msg["data"]["app"], msg["data"]["notification"]), ("events", "/deployments", APP, row.name))
		self.assertEqual(msg["data"]["kind"], "event")
		self.assertEqual((row.status, row.attempts), ("Sent", 1))
		self.assertEqual(sorted(json.loads(row.expo_ticket_ids)), sorted([f"ticket-{TOKEN_A1}", f"ticket-{TOKEN_A2}"]))

	def test_a_user_with_no_token_keeps_the_inbox_row_and_is_not_retried(self):
		self.notify()
		frappe.db.commit()
		expo = FakeExpo()
		push.drain(post=expo)
		row = self.row()
		self.assertEqual((row.status, row.expo_ticket_ids, row.next_attempt_at), ("Sent", None, None))
		self.assertEqual(row.last_error, push.NO_DEVICE)
		self.assertEqual(push.drain(post=expo)["claimed"], 0)
		self.assertEqual(expo.calls, [])

	def test_5xx_and_timeouts_back_off_then_go_dead(self):
		self.token(self.user_a, TOKEN_A1)
		self.notify()
		frappe.db.commit()

		def fail(url, payload):
			if len(expo.calls) % 2:
				return 503, {"errors": [{"code": "INTERNAL"}]}
			raise TimeoutError("read timed out")

		expo = FakeExpo(fail)
		delays = []
		for attempt in range(1, push.MAX_ATTEMPTS + 1):
			before = now_datetime()
			push.drain(post=expo)
			row = self.row()
			self.assertEqual(row.attempts, attempt)
			if attempt < push.MAX_ATTEMPTS:
				self.assertEqual(row.status, "Failed")
				delays.append(round((get_datetime(row.next_attempt_at) - before).total_seconds() / 60))
				# Not due yet: another run leaves it alone.
				self.assertEqual(push.drain(post=expo)["claimed"], 0)
				self.make_due()
		self.assertEqual(delays, [1, 5, 15, 60, 360])
		row = self.row()
		self.assertEqual(row.status, "Dead")
		self.assertIn("Gave up after 6 attempts", row.last_error)
		self.assertEqual(len(expo.calls), push.MAX_ATTEMPTS)
		self.assertEqual(push.drain(post=expo)["claimed"], 0)

	def test_rate_limited_is_retried_but_a_refused_request_is_dead(self):
		self.token(self.user_a, TOKEN_A1)
		self.notify()
		frappe.db.commit()
		push.drain(post=FakeExpo(lambda u, p: (429, None)))
		self.assertEqual(self.row().status, "Failed")
		self.make_due()
		push.drain(post=FakeExpo(lambda u, p: (400, {"errors": [{"code": "VALIDATION_ERROR"}]})))
		row = self.row()
		self.assertEqual(row.status, "Dead")
		self.assertIn("HTTP 400", row.last_error)

	def test_one_refused_row_does_not_sink_the_rest_of_the_request(self):
		self.token(self.user_a, TOKEN_A1)
		self.token(self.user_b, TOKEN_B1)
		self.notify(title="poison")
		self.notify(self.user_b, title="fine")
		bad = self.rows(title="poison")[0].name
		frappe.db.commit()

		def refuse_poison(url, payload):
			if any(m["title"] == "poison" for m in payload):
				return 400, {"errors": [{"code": "VALIDATION_ERROR"}]}
			return FakeExpo.all_ok(url, payload)

		push.drain(post=FakeExpo(refuse_poison))
		self.assertEqual(self.row(bad).status, "Dead")
		self.assertEqual(self.rows(title="fine")[0].status, "Sent")

	def test_device_not_registered_deletes_only_that_token(self):
		self.token(self.user_a, TOKEN_A1)
		self.token(self.user_a, TOKEN_A2)
		self.token(self.user_b, TOKEN_B1)
		self.notify()
		self.notify(self.user_b)
		frappe.db.commit()

		def gone_a1(url, payload):
			return 200, {
				"data": [
					{"status": "error", "message": "gone", "details": {"error": "DeviceNotRegistered"}} if m["to"] == TOKEN_A1 else {"status": "ok", "id": f"t-{m['to']}"}
					for m in payload
				]
			}

		push.drain(post=FakeExpo(gone_a1))
		self.assertEqual(sorted(frappe.get_all(push.PUSH, filters={"app": APP}, pluck="token")), sorted([TOKEN_A2, TOKEN_B1]))
		self.assertEqual([r.status for r in self.rows()], ["Sent", "Sent"])
		self.assertEqual(json.loads(self.rows(user=self.user_a)[0].expo_ticket_ids), [f"t-{TOKEN_A2}"])

	def test_message_too_big_is_dead(self):
		self.token(self.user_a, TOKEN_A1)
		self.notify()
		frappe.db.commit()
		push.drain(post=FakeExpo(lambda u, p: (200, {"data": [{"status": "error", "details": {"error": "MessageTooBig"}}]})))
		self.assertEqual(self.row().status, "Dead")

	def test_an_invalid_token_string_is_dropped_without_breaking_the_batch(self):
		# Written straight to the table: register_push would never accept it.
		self.token(self.user_a, "not-an-expo-token")
		self.token(self.user_a, TOKEN_A1)
		self.token(self.user_b, TOKEN_B1)
		self.notify()
		self.notify(self.user_b)
		frappe.db.commit()
		expo = FakeExpo()
		push.drain(post=expo)
		self.assertEqual(sorted(m["to"] for m in expo.messages), sorted([TOKEN_A1, TOKEN_B1]))
		self.assertEqual([r.status for r in self.rows()], ["Sent", "Sent"])
		self.assertFalse(frappe.db.exists(push.PUSH, {"token": "not-an-expo-token"}))

	def test_two_drain_runs_do_not_double_send(self):
		self.token(self.user_a, TOKEN_A1)
		self.notify()
		frappe.db.commit()
		nested = []

		def answer(url, payload):
			# A second worker starting while the first is still talking to Expo finds nothing to claim.
			nested.append(push.drain(post=FakeExpo())["claimed"])
			return FakeExpo.all_ok(url, payload)

		expo = FakeExpo(answer)
		push.drain(post=expo)
		push.drain(post=expo)
		self.assertEqual(nested, [0])
		self.assertEqual(len(expo.messages), 1)
		self.assertEqual(self.row().attempts, 1)

	def test_a_claimed_row_cannot_be_claimed_again(self):
		self.notify()
		frappe.db.commit()
		first = push.claim()
		self.assertEqual(len(first), 1)
		self.assertEqual(push.claim(), [])
		self.assertEqual(self.row().status, "Sending")

	def test_rows_stuck_in_sending_are_released(self):
		self.token(self.user_a, TOKEN_A1)
		self.notify()
		frappe.db.commit()
		push.claim()
		frappe.db.sql(f"update `tab{push.NOTIFICATION}` set modified=%s where app=%s", (add_to_date(now_datetime(), minutes=-11), APP))
		frappe.db.commit()
		expo = FakeExpo()
		summary = push.drain(post=expo)
		self.assertEqual((summary["released"], summary["claimed"]), (1, 1))
		row = self.row()
		self.assertEqual((row.status, row.attempts), ("Sent", 2))

	def test_rows_of_different_apps_go_in_different_requests(self):
		self.token(self.user_a, TOKEN_A1)
		self.token(self.user_a, TOKEN_A2, app=OTHER_APP)
		self.notify()
		self.notify(app=OTHER_APP)
		frappe.db.commit()
		expo = FakeExpo()
		push.drain(post=expo)
		self.assertEqual(sorted(len(p) for u, p in expo.calls), [1, 1])


class TestReceiptsAndCleanup(NotificationTestBase):
	def _sent_long_ago(self, *tokens):
		for t in tokens:
			self.token(self.user_a, t)
		self.notify()
		frappe.db.commit()
		push.drain(post=FakeExpo())
		frappe.db.set_value(push.NOTIFICATION, {"app": APP}, "sent_at", add_to_date(now_datetime(), minutes=-20), update_modified=False)
		frappe.db.commit()

	def test_receipts_delete_dead_tokens_and_mark_the_row_checked(self):
		self._sent_long_ago(TOKEN_A1, TOKEN_A2)

		def receipts(url, payload):
			self.assertEqual(url, push.EXPO_RECEIPTS_URL)
			return 200, {
				"data": {
					f"ticket-{TOKEN_A1}": {"status": "error", "details": {"error": "DeviceNotRegistered", "expoPushToken": TOKEN_A1}},
					f"ticket-{TOKEN_A2}": {"status": "ok"},
				}
			}

		self.assertEqual(push.check_receipts(post=FakeExpo(receipts))["checked"], 1)
		self.assertIsNotNone(self.row().delivered_checked_at)
		self.assertEqual(frappe.get_all(push.PUSH, filters={"app": APP}, pluck="token"), [TOKEN_A2])

	def test_other_receipt_errors_requeue_once(self):
		self._sent_long_ago(TOKEN_A1)
		rate = FakeExpo(lambda u, p: (200, {"data": {i: {"status": "error", "details": {"error": "MessageRateExceeded"}} for i in p["ids"]}}))
		push.check_receipts(post=rate)
		row = self.row()
		self.assertEqual((row.status, row.receipt_retried), ("Queued", 1))
		push.drain(post=FakeExpo())
		frappe.db.set_value(push.NOTIFICATION, {"app": APP}, "sent_at", add_to_date(now_datetime(), minutes=-20), update_modified=False)
		frappe.db.commit()
		push.check_receipts(post=rate)
		row = self.row()
		self.assertEqual(row.status, "Sent")
		self.assertIsNotNone(row.delivered_checked_at)
		self.assertIn("MessageRateExceeded", row.last_error)

	def test_missing_receipts_are_asked_for_again(self):
		self._sent_long_ago(TOKEN_A1)
		push.check_receipts(post=FakeExpo(lambda u, p: (200, {"data": {}})))
		self.assertIsNone(self.row().delivered_checked_at)
		push.check_receipts(post=FakeExpo(lambda u, p: (503, None)))
		self.assertIsNone(self.row().delivered_checked_at)

	def test_recent_rows_are_not_checked_yet(self):
		self.token(self.user_a, TOKEN_A1)
		self.notify()
		frappe.db.commit()
		push.drain(post=FakeExpo())
		expo = FakeExpo()
		push.check_receipts(post=expo)
		self.assertEqual(expo.calls, [])

	def test_cleanup_forgets_rows_older_than_30_days(self):
		self.notify(title="old")
		self.notify(title="new")
		frappe.db.sql(f"update `tab{push.NOTIFICATION}` set creation=%s where title='old' and app=%s", (add_to_date(now_datetime(), days=-31), APP))
		frappe.db.commit()
		push.cleanup()
		self.assertEqual([r.title for r in self.rows()], ["new"])


class TestInboxApi(NotificationTestBase):
	def test_guests_are_refused(self):
		frappe.set_user("Guest")
		for call in (lambda: api.list_notifications(APP), lambda: api.unread_count(APP), lambda: api.mark_read(APP, all=True)):
			with self.assertRaises(frappe.AuthenticationError):
				call()

	def test_arguments_are_validated(self):
		frappe.set_user(self.user_a)
		for bad in (None, "", "x" * 200, 5):
			with self.assertRaises(frappe.ValidationError):
				api.list_notifications(bad)
		for since in ("yesterday", "2026-13-01", 12345, "9" * 100):
			with self.assertRaises(frappe.ValidationError):
				api.list_notifications(APP, since=since)
		for names in ('["ok", 5]', "[not json", {"a": 1}):
			with self.assertRaises(frappe.ValidationError):
				api.mark_read(APP, names=names)

	def test_list_returns_only_the_session_users_rows_newest_first(self):
		self.notify(title="a1", data={"task": "T-1"}, kind="task", route="/task/T-1")
		self.notify(title="a2")
		self.notify(self.user_b, title="b1")
		self.notify(app=OTHER_APP, title="a-other-app")
		frappe.db.sql(f"update `tab{push.NOTIFICATION}` set creation=%s where title='a1' and app=%s", (add_to_date(now_datetime(), minutes=-5), APP))
		frappe.set_user(self.user_a)
		result = api.list_notifications(APP)
		self.assertEqual([i["title"] for i in result["items"]], ["a2", "a1"])
		self.assertEqual(result["unread"], 2)
		item = result["items"][1]
		self.assertEqual(set(item), {"name", "title", "body", "route", "kind", "data", "created", "read"})
		self.assertEqual((item["route"], item["kind"], item["data"], item["read"]), ("/task/T-1", "task", {"task": "T-1"}, False))
		self.assertIsNotNone(get_datetime(item["created"]).utcoffset())
		self.assertIsNotNone(get_datetime(result["server_time"]).utcoffset())

	def test_since_and_the_limit_cap(self):
		for i in range(api.MAX_LIMIT + 5):
			self.notify(title=f"n{i}")
		self.notify(title="old")
		frappe.db.sql(f"update `tab{push.NOTIFICATION}` set creation=%s where title='old' and app=%s", (add_to_date(now_datetime(), days=-1), APP))
		frappe.set_user(self.user_a)
		self.assertEqual(len(api.list_notifications(APP, limit=1000)["items"]), api.MAX_LIMIT)
		self.assertEqual(len(api.list_notifications(APP, limit="3")["items"]), 3)
		self.assertEqual(len(api.list_notifications(APP, limit="junk")["items"]), api.DEFAULT_LIMIT)
		self.assertEqual(len(api.list_notifications(APP, limit=-4)["items"]), api.DEFAULT_LIMIT)
		since = add_to_date(now_datetime(), hours=-1)
		titles = [i["title"] for i in api.list_notifications(APP, since=api.iso(since), limit=100)["items"]]
		self.assertNotIn("old", titles)
		# An offset-less timestamp is the site's own time; a UTC "Z" one is converted.
		titles = [i["title"] for i in api.list_notifications(APP, since=since.isoformat(), limit=100)["items"]]
		self.assertNotIn("old", titles)
		utc = get_datetime(api.iso(add_to_date(now_datetime(), days=-2))).astimezone(UTC)
		titles = [i["title"] for i in api.list_notifications(APP, since=utc.strftime("%Y-%m-%dT%H:%M:%S.000Z"), limit=100)["items"]]
		self.assertEqual(len(titles), 100)
		future = api.iso(add_to_date(now_datetime(), hours=1))
		self.assertEqual(api.list_notifications(APP, since=future)["items"], [])

	def test_unread_count_and_mark_read_only_touch_own_rows(self):
		self.notify(title="a1")
		self.notify(title="a2")
		self.notify(title="a3")
		self.notify(app=OTHER_APP, title="a-other")
		self.notify(self.user_b, title="b1")
		names = {r.title: r.name for r in self.rows()}
		frappe.set_user(self.user_a)
		self.assertEqual(api.unread_count(APP), {"unread": 3})
		# Someone else's row and another app's row are ignored, even by name.
		self.assertEqual(api.mark_read(APP, names=json.dumps([names["a1"], names["b1"], names["a-other"], "nope"])), {"updated": 1})
		self.assertEqual(api.mark_read(APP, names=[names["a1"]]), {"updated": 0})
		self.assertEqual(api.mark_read(APP, names=names["a2"]), {"updated": 1})
		self.assertEqual(api.unread_count(APP), {"unread": 1})
		self.assertEqual(api.mark_read(APP), {"updated": 0})
		self.assertEqual(api.mark_read(APP, all="true"), {"updated": 1})
		self.assertEqual(api.unread_count(APP), {"unread": 0})
		self.assertEqual([i["read"] for i in api.list_notifications(APP)["items"]], [True, True, True])
		frappe.set_user(self.user_b)
		self.assertEqual(api.unread_count(APP), {"unread": 1})
		self.assertEqual(api.unread_count(OTHER_APP), {"unread": 0})
		frappe.set_user(self.user_a)
		self.assertEqual(api.unread_count(OTHER_APP), {"unread": 1})


class TestHooks(unittest.TestCase):
	def test_scheduler_runs_the_outbox(self):
		from upande_mobile import hooks

		cron = hooks.scheduler_events["cron"]
		self.assertIn("upande_mobile.push.drain", cron["* * * * *"])
		self.assertIn("upande_mobile.push.check_receipts", cron["*/5 * * * *"])
		self.assertTrue(any("upande_mobile.push.cleanup" in jobs for jobs in cron.values()))


if __name__ == "__main__":
	unittest.main()
