"""Thread replies (engine.thread_replies): a reply lands in a thread under
the message it answers, the whole burst and a retry find the same thread,
DMs and existing threads stay inline, and a refused thread (403/400/error)
never costs the reply. Also the setting itself (engine.thread_replies is a
known engine key, so bin/settings.py validate accepts it).

Drives send_fragmented with the stubbed API (tests/_lib/monitor_harness).
MUT_DIR = the package tree under test.

Usage: [MUT_DIR=<tree>] python3 tests/threads/test_threads.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "_lib"))
import monitor_harness as H  # noqa: E402
from monitor_harness import G1, HTTPErr, chan, sf  # noqa: E402
from settings_harness import MS, MUT, tmpdir, write  # noqa: E402

CH = "C1"
THREAD_CFG = "engine:\n  thread_replies: on\n"


def load(extra=""):
    mod = H.load_monitor(cfg_extra=extra)
    mod.CHANNELS = {CH: chan()}
    return mod


def thread_posts(mod):
    return [p for p in mod.api.posts if str(p[0]).endswith("/threads")]


def sent(mod):
    """The messages really posted (thread creations excluded)."""
    return [(p, d) for p, d in mod.api.posts if not str(p).endswith("/threads")]


def only_thread(mod):
    ids = [cid for cid in mod.CHANNELS if cid != CH]
    assert len(ids) == 1, ids
    return ids[0]


class ThreadReplies(unittest.TestCase):
    def test_config_value_is_read(self):
        self.assertFalse(load().THREAD_REPLIES)
        self.assertTrue(load(THREAD_CFG).THREAD_REPLIES)

    def test_reply_lands_in_a_new_thread(self):
        mod = load(THREAD_CFG)
        mid = sf()
        mod.send_fragmented(CH, ["hello there"], mid)
        made = thread_posts(mod)
        self.assertEqual(len(made), 1)
        path, data = made[0]
        self.assertEqual(path, f"/channels/{CH}/messages/{mid}/threads")
        self.assertEqual(data["name"], "hello there")
        self.assertEqual(data["auto_archive_duration"], 1440)
        tid = only_thread(mod)
        self.assertEqual(sent(mod), [(f"/channels/{tid}/messages",
                                      sent(mod)[0][1])])
        body = sent(mod)[0][1]
        self.assertEqual(body["content"], "hello there")
        # The thread's starter is the reply itself: no reply reference.
        self.assertNotIn("message_reference", body)
        info = mod.CHANNELS[tid]
        self.assertTrue(info["thread"])
        self.assertEqual(info["parent_id"], CH)
        self.assertEqual(info["guild_id"], G1)
        self.assertTrue(info["can_send"])
        # Read from its start: a person answering in the thread before the
        # poll first reads it is not skipped as history.
        self.assertIn(tid, mod._NEW_THREADS)
        st = mod.load_state()
        mod.seed_channel_seen(tid, "general > hello there", st)
        self.assertEqual(st["last_seen"][tid], str(int(tid) - 1))

    def test_off_by_default_replies_in_the_channel(self):
        mod = load()
        mid = sf()
        mod.send_fragmented(CH, ["hello"], mid)
        self.assertEqual(thread_posts(mod), [])
        path, body = sent(mod)[0]
        self.assertEqual(path, f"/channels/{CH}/messages")
        self.assertEqual(body["message_reference"]["message_id"], mid)

    def test_one_thread_for_the_burst_and_for_a_retry(self):
        mod = load(THREAD_CFG)
        mid = sf()
        mod.send_fragmented(CH, ["first"], mid)
        mod.send_fragmented(CH, ["first"], mid)      # the retry
        mod.send_fragmented(CH, ["second"], mid)     # the next burst
        self.assertEqual(len(thread_posts(mod)), 1)
        tid = only_thread(mod)
        self.assertTrue(all(p == f"/channels/{tid}/messages"
                            for p, _ in sent(mod)))

    def test_no_thread_inside_a_thread(self):
        mod = load(THREAD_CFG)
        mod.CHANNELS["T1"] = {"name": "general > chat", "guild_id": G1,
                              "guild_name": "Srv", "can_send": True,
                              "thread": True, "parent_id": CH}
        mid = sf()
        mod.send_fragmented("T1", ["hello"], mid)
        self.assertEqual(thread_posts(mod), [])
        path, body = sent(mod)[0]
        self.assertEqual(path, "/channels/T1/messages")
        self.assertEqual(body["message_reference"]["message_id"], mid)

    def test_no_thread_in_a_dm(self):
        mod = load(THREAD_CFG)
        mod.CHANNELS = {}       # a DM is not a monitored channel
        mid = sf()
        mod.send_fragmented("D1", ["hello"], mid)
        self.assertEqual(thread_posts(mod), [])
        self.assertEqual(sent(mod)[0][0], "/channels/D1/messages")

    def test_a_plain_post_makes_no_thread(self):
        mod = load(THREAD_CFG)
        mod.send_fragmented(CH, ["a closing remark"], None)
        self.assertEqual(thread_posts(mod), [])
        self.assertEqual(sent(mod)[0][0], f"/channels/{CH}/messages")

    def test_403_falls_back_inline_and_is_remembered(self):
        mod = load(THREAD_CFG)
        mid = sf()
        mod.api.errors[f"/channels/{CH}/messages/{mid}/threads"] = HTTPErr(403)
        mod.send_fragmented(CH, ["hello"], mid)
        self.assertEqual(len(thread_posts(mod)), 1)     # tried once
        path, body = sent(mod)[0]
        self.assertEqual(path, f"/channels/{CH}/messages")
        self.assertEqual(body["message_reference"]["message_id"], mid)
        # The next reply in that channel does not try again.
        mod.send_fragmented(CH, ["again"], sf())
        self.assertEqual(len(thread_posts(mod)), 1)
        self.assertIn(CH, mod._NO_THREAD_PERM)

    def test_400_reuses_the_thread_already_on_the_message(self):
        mod = load(THREAD_CFG)
        mid, tid = sf(), sf()
        mod.api.errors[f"/channels/{CH}/messages/{mid}/threads"] = HTTPErr(400)
        mod.api.routes[f"/channels/{CH}/messages/{mid}"] = {
            "id": mid, "thread": {"id": tid, "name": "already here"}}
        mod.send_fragmented(CH, ["hello"], mid)
        self.assertEqual(only_thread(mod), tid)
        self.assertEqual(sent(mod)[0][0], f"/channels/{tid}/messages")

    def test_a_failed_create_never_costs_the_reply(self):
        mod = load(THREAD_CFG)
        mid = sf()
        mod.api.errors[f"/channels/{CH}/messages/{mid}/threads"] = HTTPErr(500)
        mod.send_fragmented(CH, ["hello"], mid)
        path, body = sent(mod)[0]
        self.assertEqual(path, f"/channels/{CH}/messages")
        self.assertEqual(body["message_reference"]["message_id"], mid)

    def test_a_bad_message_id_is_never_put_in_a_path(self):
        mod = load(THREAD_CFG)
        self.assertIsNone(mod.reply_thread(CH, "1/../guilds/2", "x"))
        self.assertEqual(mod.api.posts, [])

    def test_thread_names_are_one_line_and_capped(self):
        mod = load(THREAD_CFG)
        self.assertEqual(mod.reply_thread_name("  a\n b\tc "), "a b c")
        self.assertEqual(mod.reply_thread_name(""), "Thread")
        long = mod.reply_thread_name("word " * 200)
        self.assertLessEqual(len(long), 100)
        self.assertTrue(long.endswith("…"))
        self.assertNotIn("\n", long)


class TheSetting(unittest.TestCase):
    CFG = """version: 1
owners: ["111"]
token_from: env:MUSE_TEST_TOKEN
engine:
  thread_replies: on
  rest_poll_seconds: 30
"""

    def test_validate_accepts_it(self):
        d = tmpdir("cfg_threads_")
        st = MS.load(write(os.path.join(d, "config.yaml"), self.CFG),
                     os.path.join(MUT, "plugins"))
        self.assertTrue(st.engine.get("thread_replies"))

    def test_validate_rejects_a_stranger_next_to_it(self):
        d = tmpdir("cfg_threads_")
        cfg = self.CFG.replace("thread_replies: on", "threads_reply: on")
        with self.assertRaises(Exception) as ctx:
            MS.load(write(os.path.join(d, "config.yaml"), cfg),
                    os.path.join(MUT, "plugins"))
        self.assertIn("threads_reply", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
