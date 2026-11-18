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
import time
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


class LinkReading(unittest.TestCase):
    def test_only_https_and_at_most_two(self):
        mod = load()
        self.assertEqual(
            mod.link_urls("see https://a.example/x, http://b.example and "
                          "https://c.example/y https://d.example/z"),
            ["https://a.example/x", "https://c.example/y"])

    def test_a_page_becomes_its_title_and_text(self):
        mod = load()
        page = (b"<html><head><title>News</title><style>p{color:red}</style>"
                b"</head><body><p>Hello <b>world</b></p>"
                b"<script>bad()</script></body></html>")
        title, text = mod.link_text_of(page, "text/html")
        self.assertEqual(title, "News")
        self.assertIn("Hello world", text)
        self.assertNotIn("bad()", text)
        self.assertNotIn("color:red", text)

    def test_a_pdf_is_not_a_page(self):
        mod = load()
        self.assertEqual(mod.link_text_of(b"%PDF-1.7", "application/pdf"),
                         ("", ""))

    def test_the_page_rides_on_the_queue_item(self):
        mod = load()
        mid = sf()
        mod.fetch_link = lambda url: ("A page", "the text on it")
        mod.note_links({"id": mid, "content": "look: https://example.com/a"})
        deadline = time.time() + 5
        while time.time() < deadline and not mod.links_for(mid, wait=0):
            time.sleep(0.02)
        self.assertEqual(mod.links_for(mid, wait=0),
                         [{"url": "https://example.com/a", "title": "A page",
                           "text": "the text on it"}])

    def test_a_failed_fetch_is_left_out(self):
        mod = load()
        mid = sf()
        mod.fetch_link = lambda url: None
        mod.note_links({"id": mid, "content": "https://example.com/a"})
        deadline = time.time() + 5
        while time.time() < deadline:
            with mod._LINKS_LOCK:
                entry = mod._LINKS.get(mid) or {}
                if all(v is not None for v in entry.get("items", {}).values()):
                    break
            time.sleep(0.02)
        self.assertEqual(mod.links_for(mid, wait=0), [])


class VoiceMessages(unittest.TestCase):
    def test_audio_attachments_are_recognised(self):
        # (The download itself runs in collect_media, which the harness
        # stubs out: this covers the decision that routes a file there.)
        mod = load()
        self.assertTrue(mod.is_audio_attachment("audio/ogg",
                                                "https://x/a.ogg"))
        self.assertTrue(mod.is_audio_attachment("", "https://x/voice.m4a"))
        self.assertTrue(mod.is_audio_attachment("", "https://x/a.opus?size=1"))
        self.assertFalse(mod.is_audio_attachment("image/png", "https://x/a.png"))
        self.assertFalse(mod.is_audio_attachment("", "https://x/a.mp4"))

    def test_voice_notes_are_kept_for_the_queue_item(self):
        mod = load()
        mid = sf()
        mod._remember_audio(mid, [{"name": "a.ogg", "path": "/tmp/a.ogg",
                                   "content_type": "audio/ogg"}])
        got = mod.audio_for(mid)
        self.assertEqual([e["name"] for e in got], ["a.ogg"])
        self.assertEqual(mod.audio_for(mid), got)   # still there on a retry

    def test_no_audio_is_no_list(self):
        mod = load()
        self.assertEqual(mod.audio_for(sf()), [])


class Summaries(unittest.TestCase):
    def test_written_and_read_back(self):
        mod = load()
        st = mod.load_state()
        cid = sf()               # a channel id (ids are snowflakes)
        self.assertEqual(mod.channel_summary(st, cid), "")
        mod.store_summary(st, {"channel_id": cid, "reply_to": "5",
                               "summary": "They ship on Friday."})
        self.assertEqual(mod.channel_summary(st, cid), "They ship on Friday.")
        self.assertEqual(len(mod.channel_summary(st, cid)),
                         len("They ship on Friday."))

    def test_refuses_what_is_not_a_sentence_for_this_channel(self):
        mod = load()
        st = mod.load_state()
        self.assertIsNone(mod.store_summary(st, {"channel_id": CH,
                                                 "summary": 5}))
        self.assertIsNone(mod.store_summary(st, {"channel_id": "1/../x",
                                                 "summary": "hi"}))
        self.assertIsNone(mod.store_summary(st, {"channel_id": "C1",
                                                 "summary": ""}))
        self.assertFalse(st.get("summaries"))


class AckReactions(unittest.TestCase):
    def test_the_marker_is_replaced_once(self):
        mod = load("engine:\n  ack_reactions: on\n")
        calls = []
        mod.add_reaction = lambda cid, mid, e: calls.append(("add", e)) or True
        mod.remove_reaction = lambda cid, mid, e: calls.append(("del", e)) or True
        mid = sf()
        self.assertTrue(mod.ack_start(CH, mid))
        self.assertFalse(mod.ack_start(CH, mid))       # once
        self.assertTrue(mod.ack_done(CH, mid, "replied"))
        self.assertFalse(mod.ack_done(CH, mid, "replied"))  # already settled
        self.assertEqual([c[0] for c in calls], ["add", "del", "add"])
        self.assertEqual(calls[-1][1], mod.ACK_DONE)

    def test_silence_only_removes_it(self):
        mod = load("engine:\n  ack_reactions: on\n")
        calls = []
        mod.add_reaction = lambda cid, mid, e: calls.append(("add", e)) or True
        mod.remove_reaction = lambda cid, mid, e: calls.append(("del", e)) or True
        mid = sf()
        mod.ack_start(CH, mid)
        self.assertTrue(mod.ack_done(CH, mid, "skipped"))
        self.assertEqual([c[0] for c in calls], ["add", "del"])

    def test_off_by_default(self):
        mod = load()
        self.assertFalse(mod.ack_start(CH, sf()))


class ThreadPolish(unittest.TestCase):
    def test_the_thread_starts_with_a_mention(self):
        mod = load("engine:\n  thread_replies: on\n  thread_mention: on\n")
        mid, uid = sf(), "111"
        mod.send_fragmented(CH, ["hello"], mid, ping_user=uid)
        body = sent(mod)[0][1]
        self.assertTrue(body["content"].startswith(f"<@{uid}> "))
        self.assertEqual(body["allowed_mentions"], {"parse": [], "users": [uid]})

    def test_no_mention_when_it_is_off(self):
        mod = load("engine:\n  thread_replies: on\n")
        mid = sf()
        mod.send_fragmented(CH, ["hello"], mid, ping_user="111")
        self.assertEqual(sent(mod)[0][1]["content"], "hello")

    def test_a_reply_can_stay_in_the_channel(self):
        mod = load("engine:\n  thread_replies: on\n")
        mid = sf()
        mod.send_fragmented(CH, ["hello"], mid, thread_ok=False)
        self.assertEqual(thread_posts(mod), [])
        self.assertEqual(sent(mod)[0][0], f"/channels/{CH}/messages")

    def test_renaming_the_thread(self):
        mod = load("engine:\n  thread_replies: on\n")
        mid = sf()
        mod.send_fragmented(CH, ["hello"], mid)
        tid = only_thread(mod)
        patched = {}
        mod.api_patch = lambda path, data: patched.update({path: data}) or {}
        self.assertTrue(mod.rename_reply_thread(CH, mid, "  a   better\nname "))
        self.assertEqual(patched, {f"/channels/{tid}":
                                   {"name": "a better name"}})
        self.assertEqual(mod.CHANNELS[tid]["name"], "general > a better name")

    def test_renaming_without_a_thread_does_nothing(self):
        mod = load()
        patched = {}
        mod.api_patch = lambda path, data: patched.update({path: data}) or {}
        self.assertFalse(mod.rename_reply_thread(CH, sf(), "nope"))
        self.assertEqual(patched, {})


class Commands(unittest.TestCase):
    def ask(self, mod, mid=None, cid=CH, question="hello?"):
        ix = {"id": mid or sf(), "channel_id": cid,
              "data": {"options": [{"name": "question", "value": question}]},
              "member": {"user": {"username": "yu"}}}
        return ix, mod.on_ask_interaction(ix, "", "111", {})

    def test_options_are_named(self):
        mod = load()
        ix, _ = self.ask(mod)
        self.assertEqual(mod.command_options(ix), {"question": "hello?"})

    def test_ask_answers_at_once_and_records_the_request(self):
        mod = load()
        _, resp = self.ask(mod)
        self.assertEqual(resp["type"], 4)
        self.assertTrue(resp["data"]["flags"] & 64)     # ephemeral
        self.assertEqual(len(mod._CONTROL["ask"]), 1)

    def test_ask_becomes_a_queue_item(self):
        mod = load()
        st = mod.load_state()
        st["guild_joined"] = {G1: 0}     # the bot has been here all along
        mod.CHANNELS = {CH: chan()}
        ix, _ = self.ask(mod)
        q = []
        self.assertEqual(mod.apply_controls(st, q), 1)
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["content"], "hello?")
        self.assertEqual(q[0]["message_id"], ix["id"])
        self.assertEqual(q[0]["channel_id"], CH)

    def test_new_forgets_history_and_summary_only(self):
        mod = load()
        st = mod.load_state()
        st["history"] = {CH: [{"id": "1", "text": "hi"}]}
        st["summaries"] = {CH: {"text": "old"}}
        st["replied_to"] = ["9"]
        resp = mod.on_new_interaction({"id": sf(), "channel_id": CH}, "",
                                      "111", {})
        self.assertEqual(resp["type"], 4)
        self.assertEqual(mod.apply_controls(st, []), 1)
        self.assertNotIn(CH, st["history"])
        self.assertNotIn(CH, st["summaries"])
        self.assertEqual(st["replied_to"], ["9"])

    def test_status_reports_the_numbers(self):
        mod = load()
        mod._STATUS.update(queue=2, outbox=1, replied=7, skipped=3,
                           gateway=True, worker=time.time() - 5)
        resp = mod.on_status_interaction({}, "", "111", {})
        text = resp["data"]["content"]
        for line in ("Queue: 2", "Outbox: 1", "Replies: 7 sent, 3 passed on",
                     "Gateway: connected"):
            self.assertIn(line, text)

    def test_a_command_with_options_is_compared_by_its_json(self):
        mod = load()
        self.assertFalse(mod._command_differs(dict(mod.ASK_COMMAND),
                                              mod.ASK_COMMAND))
        self.assertTrue(mod._command_differs(dict(mod.ASK_COMMAND, options=[]),
                                             mod.ASK_COMMAND))


class FakeSettings:
    """settings() with one resolved place: enough for trigger_verdict."""

    def __init__(self, replies):
        self.replies = replies

    def resolve(self, scope):
        return {"who.replies": self.replies, "who.bots": "when-addressed",
                "who.allow": "everyone", "who.ignore": []}


THREAD_ITEM = {"name": "general > chat", "guild_id": G1, "guild_name": "Srv",
               "can_send": True, "thread": True, "parent_id": CH}


class FreeReplies(unittest.TestCase):
    def threaded(self):
        mod = load()
        mod.CHANNELS["T1"] = dict(THREAD_ITEM)
        # The bot is already part of the thread (it spoke there).
        st = mod.load_state()
        mod.record_history(st, "T1", [H.msg("T1", "999", "hi", bot=True, age=5)])
        mod.tstate = st
        return mod

    def said(self, cid, uid="222", bot=False):
        return {"id": sf(), "channel_id": cid, "guild_id": G1,
                "author": {"id": uid, "username": "u", "bot": bot},
                "content": "and then?", "type": 0}

    def test_a_thread_message_reaches_the_worker(self):
        mod = self.threaded()
        m = self.said("T1")
        self.assertTrue(mod.free_reply(m, "conversational", m["author"],
                                       mod.tstate))

    def test_a_message_for_someone_else_is_not_ours(self):
        mod = self.threaded()
        st = mod.tstate
        m = self.said("T1")
        m["mentions"] = [{"id": "333"}]
        mod.record_history(st, "T1", [m])
        self.assertIsNone(mod.trigger_verdict(m, G1, st)[0])

    def test_a_thread_the_bot_never_joined_stays_quiet(self):
        mod = load()
        mod.CHANNELS["T2"] = dict(THREAD_ITEM)
        m = self.said("T2")
        self.assertFalse(mod.free_reply(m, "conversational", m["author"],
                                        mod.load_state()))
        self.assertFalse(mod.free_reply(m, "free", m["author"],
                                        mod.load_state()))
        self.assertEqual(mod.trigger_verdict(m, G1, None)[0], "free")

    def test_when_addressed_keeps_a_thread_quiet(self):
        mod = self.threaded()
        m = self.said("T1")
        self.assertFalse(mod.free_reply(m, "when-addressed", m["author"]))
        mod.settings = lambda: FakeSettings("when-addressed")
        self.assertIsNone(mod.trigger_verdict(m, G1, None)[0])

    def test_a_place_can_be_free(self):
        mod = load()
        m = self.said(CH)
        self.assertTrue(mod.free_reply(m, "free", m["author"]))
        mod.settings = lambda: FakeSettings("free")
        self.assertEqual(mod.trigger_verdict(m, G1, None)[0], "free")

    def test_a_plain_channel_is_not_free(self):
        mod = load()
        m = self.said(CH)
        self.assertFalse(mod.free_reply(m, "conversational", m["author"]))

    def test_a_forum_post_opening_is_not_free(self):
        # A post's opening message shares the thread's id: posting is not
        # talking to the bot, so only the conversation after it is free.
        mod = self.threaded()
        m = self.said("T1")
        m["id"] = "T1"
        self.assertFalse(mod.free_reply(m, "free", m["author"]))
        later = self.said("T1")
        self.assertTrue(mod.free_reply(later, "free", later["author"]))

    def test_another_bot_never_gets_one(self):
        mod = self.threaded()
        m = self.said("T1", bot=True)
        self.assertFalse(mod.free_reply(m, "free", m["author"]))

    def test_the_item_says_free(self):
        mod = load()
        st = mod.load_state()
        st["guild_joined"] = {G1: 0}
        mod.CHANNELS = {CH: chan()}
        q = []
        self.assertTrue(mod.enqueue_message(H.msg(CH, "222", "hello there"),
                                            "Srv #general", st, q,
                                            guild_id=G1, kind="free"))
        self.assertEqual(q[0]["kind"], "free")

    def test_a_mention_still_upgrades_a_open_burst(self):
        mod = self.threaded()
        st = mod.load_state()
        st["guild_joined"] = {G1: 0}
        first = self.said("T1")
        mod.stage_trigger(st, first, "Srv general > chat", G1, "free")
        second = self.said("T1", uid="222")
        second["mentions"] = [{"id": H.BOT}]
        mod.stage_trigger(st, second, "Srv general > chat", G1, "mention")
        entry = list(st["pending"].values())[0]
        self.assertEqual(entry["kind"], "mention")


class SharedMemory(unittest.TestCase):
    def test_a_thread_and_its_channel_share_the_conversation(self):
        mod = load()
        st = mod.load_state()
        mod.CHANNELS = {CH: chan(), "T1": dict(THREAD_ITEM)}
        mod.record_history(st, CH, [H.msg(CH, "111", "said in the channel")])
        mod.record_history(st, "T1", [H.msg("T1", "111", "said in the thread")])
        # the channel's window carries what was said in the thread
        self.assertIn("said in the thread",
                      [h["text"] for h in st["history"][CH]])
        self.assertIn("via", st["history"][CH][-1])
        # and a thread's item gets both
        got = [h["text"] for h in mod.history_window(st, "T1")]
        self.assertIn("said in the channel", got)
        self.assertIn("said in the thread", got)

    def test_a_thread_summary_falls_back_to_its_parent(self):
        mod = load()
        st = mod.load_state()
        parent, tid = sf(), sf()      # ids are snowflakes
        mod.CHANNELS = {parent: chan(),
                        tid: dict(THREAD_ITEM, parent_id=parent)}
        mod.store_summary(st, {"channel_id": parent,
                               "summary": "we ship Friday"})
        self.assertEqual(mod.channel_summary(st, tid), "we ship Friday")
        # ...but its own wins
        mod.store_summary(st, {"channel_id": tid, "summary": "own"})
        self.assertEqual(mod.channel_summary(st, tid), "own")

    def test_the_memory_file_travels_with_every_item(self):
        mod = load()
        path = os.path.join(mod.TMP, "MEMORY.md")
        with open(path, "w") as f:
            f.write("Likes tea. Works on a Discord bot.\n")
        mod.MEMORY_FILE = path
        mod._MEMORY_TEXT.update(mtime=None, text="")
        self.assertIn("Likes tea", mod.muse_memory())
        st = mod.load_state()
        st["guild_joined"] = {G1: 0}
        mod.CHANNELS = {CH: chan()}
        q = []
        self.assertTrue(mod.enqueue_message(H.msg(CH, "111", "hi"), "Srv #general",
                                            st, q, guild_id=G1))
        self.assertIn("Likes tea", q[0]["muse_memory"])

    def test_memory_nothing_leaves_it_out(self):
        mod = load()
        path = os.path.join(mod.TMP, "MEMORY.md")
        with open(path, "w") as f:
            f.write("Likes tea.\n")
        mod.MEMORY_FILE = path
        mod._MEMORY_TEXT.update(mtime=None, text="")
        st = mod.load_state()
        st["guild_joined"] = {G1: 0}
        mod.CHANNELS = {CH: chan()}
        q = []
        mod.enqueue_message(H.msg(CH, "222", "hi"), "Srv #general", st, q,
                            guild_id=G1)
        self.assertEqual(q[0]["muse_memory"], "")   # not the owner: no private memory


class FreeGate(unittest.TestCase):
    """F: a free message wakes the worker only when it plausibly wants it,
    and what it drops stays in history."""

    def setUp(self):
        self.mod = load()
        self.mod.CHANNELS["T1"] = dict(THREAD_ITEM)
        self.st = self.mod.load_state()
        self.st["guild_joined"] = {G1: 0}
        # the bot is already part of the thread
        self.mod.record_history(self.st, "T1",
                                [H.msg("T1", "999", "hi", bot=True, age=1800)])

    def said(self, text="and then?", uid="222", bot=False):
        return {"id": sf(), "channel_id": "T1", "guild_id": G1,
                "author": {"id": uid, "username": "u", "bot": bot},
                "content": text, "type": 0}

    def verdict(self, text="and then?", uid="222"):
        m = self.said(text, uid)
        self.mod.record_history(self.st, "T1", [m])
        return self.mod.trigger_verdict(m, G1, self.st)

    def test_plain_chatter_waits(self):
        self.assertIsNone(self.verdict("and then we went home")[0])

    def test_a_question_wakes_it(self):
        self.assertEqual(self.verdict("これどう思う？")[0], "free")

    def test_a_question_mark_in_ascii_counts(self):
        self.assertEqual(self.verdict("what now?")[0], "free")

    def test_its_name_wakes_it(self):
        self.assertEqual(self.verdict("muse これ見て")[0], "free")

    def test_the_owner_always_wakes_it(self):
        self.assertEqual(self.verdict("ただのつぶやき", uid="111")[0], "free")

    def test_an_exchange_it_was_in(self):
        self.mod.record_history(self.st, "T1",
                                [H.msg("T1", "999", "sure", bot=True, age=5)])
        self.assertEqual(self.verdict("and then we went home")[0], "free")

    def test_what_it_drops_is_still_history(self):
        self.assertIsNone(self.verdict("and then we went home", "222")[0])
        texts = [h["text"] for h in self.st["history"]["T1"]]
        self.assertIn("and then we went home", texts)

    def test_a_flood_only_lets_the_owner_through(self):
        for _ in range(mod_const(self.mod)):
            self.assertEqual(self.verdict("why?", "222")[0], "free")
        self.assertIsNone(self.verdict("why again?", "222")[0])
        self.assertEqual(self.verdict("owner asks why?", "111")[0], "free")


def mod_const(mod):
    return mod.FREE_GATE_MAX_PER_MINUTE


class SummaryIndex(unittest.TestCase):
    """B: other places' summaries, same server only, never a DM's."""

    def test_the_same_servers_places_ride_along(self):
        mod = load()
        st = mod.load_state()
        here, there = sf(), sf()
        mod.CHANNELS = {here: chan("general"),
                        there: {"name": "plans", "guild_id": G1,
                                "guild_name": "Srv"}}
        mod.store_summary(st, {"channel_id": there, "summary": "we ship Friday"})
        index = mod.summary_index(st, here, G1, False)
        self.assertEqual(len(index), 1)
        self.assertEqual(index[0]["place"], "Srv #plans")
        self.assertIn("ship Friday", index[0]["text"])

    def test_another_server_stays_out(self):
        mod = load()
        st = mod.load_state()
        here, there = sf(), sf()
        mod.CHANNELS = {here: chan("general"),
                        there: {"name": "secret", "guild_id": "G2",
                                "guild_name": "Other"}}
        mod.store_summary(st, {"channel_id": there, "summary": "x"})
        self.assertEqual(mod.summary_index(st, here, G1, False), [])

    def test_a_dm_summary_never_reaches_a_channel(self):
        mod = load()
        st = mod.load_state()
        here, dm = sf(), sf()
        mod.CHANNELS = {here: chan("general")}
        other_dm = sf()
        mod.DM_CHANNELS[dm] = "DM with yu"
        mod.DM_CHANNELS[other_dm] = "DM with someone"
        mod.store_summary(st, {"channel_id": other_dm,
                               "summary": "private thing"})
        self.assertEqual(mod.summary_index(st, here, G1, False), [])
        index = mod.summary_index(st, dm, None, True)
        self.assertEqual(index[0]["place"], "DM with someone")

    def test_it_is_capped(self):
        mod = load()
        st = mod.load_state()
        here = sf()
        mod.CHANNELS = {here: chan("general")}
        for _ in range(mod.SUMMARY_INDEX_MAX + 3):
            cid = sf()
            mod.CHANNELS[cid] = {"name": "c" + cid[-4:], "guild_id": G1,
                                 "guild_name": "Srv"}
            mod.store_summary(st, {"channel_id": cid, "summary": "x"})
        self.assertLessEqual(len(mod.summary_index(st, here, G1, False)),
                             mod.SUMMARY_INDEX_MAX)


class DiscordLinks(unittest.TestCase):
    """C: a Discord message or thread link is read with the bot's token,
    never a DM, never a channel it cannot read."""

    def test_a_link_is_parsed(self):
        mod = load()
        self.assertEqual(
            mod.discord_link_parts("https://discord.com/channels/1/2/3"),
            ("1", "2", "3"))
        self.assertEqual(
            mod.discord_link_parts("<https://discordapp.com/channels/1/2>"),
            ("1", "2", None))
        self.assertIsNone(mod.discord_link_parts("https://discord.gg/abc"))

    def test_a_message_is_read(self):
        mod = load()
        cid, mid = sf(), sf()
        mod.api.routes[f"/channels/{cid}"] = {"id": cid, "type": 0,
                                              "name": "general"}
        mod.api.routes[f"/channels/{cid}/messages/{mid}"] = {
            "id": mid, "content": "ship it", "author": {"username": "yu"}}
        title, text = mod.resolve_discord_link(
            f"https://discord.com/channels/1/{cid}/{mid}")
        self.assertIn("yu", title)
        self.assertIn("ship it", text)

    def test_a_thread_link_gives_its_recent_messages(self):
        mod = load()
        cid = sf()
        mod.api.routes[f"/channels/{cid}"] = {"id": cid, "type": 11,
                                              "name": "plans"}
        mod.api.msgs[cid] = [
            {"id": sf(), "content": "first", "author": {"username": "a"}},
            {"id": sf(), "content": "second", "author": {"username": "b"}}]
        title, text = mod.resolve_discord_link(
            f"https://discord.com/channels/1/{cid}")
        self.assertIn("plans", title)
        self.assertIn("first", text)
        self.assertIn("second", text)

    def test_a_dm_is_never_read(self):
        mod = load()
        cid, mid = sf(), sf()
        mod.api.routes[f"/channels/{cid}"] = {"id": cid, "type": 1}
        self.assertIsNone(mod.resolve_discord_link(
            f"https://discord.com/channels/1/{cid}/{mid}"))

    def test_an_unreadable_channel_is_none(self):
        mod = load()
        cid = sf()
        mod.api.errors[f"/channels/{cid}"] = HTTPErr(403)
        self.assertIsNone(mod.resolve_discord_link(
            f"https://discord.com/channels/1/{cid}"))


class OwnerRemember(unittest.TestCase):
    """D: remember from an owner's own server message, their own words
    only, into the same MEMORY.md."""

    def store(self, st, kind="mention", uid="111", **meta):
        mid = sf()
        st.setdefault("item_meta", {})[mid] = dict(
            {"kind": kind, "author_id": uid, "channel_id": CH}, **meta)
        return mid

    def check(self, mod, st, mid, facts=("Likes tea",)):
        index = {"frags": {}, "owners": set()}
        return mod.remember_check(
            st, {"remember": list(facts), "reply_to": mid,
                 "channel_id": CH}, mid, index)

    def test_an_owners_server_message_can_be_remembered(self):
        mod = load()
        st = mod.load_state()
        mid = self.store(st)
        why, t = self.check(mod, st, mid)
        self.assertIsNone(why)
        self.assertEqual(t, mid)

    def test_a_strangers_message_cannot(self):
        mod = load()
        st = mod.load_state()
        why, t = self.check(mod, st, self.store(st, uid="222"))
        self.assertIsNone(t)          # refused: a stranger writes nothing
        self.assertTrue(why)

    def test_a_quote_forward_or_attachment_cannot(self):
        mod = load()
        st = mod.load_state()
        for meta in ({"forwarded": True}, {"has_inputs": True},
                     {"bot": True}):
            with self.subTest(meta):
                why, t = self.check(mod, st, self.store(st, **meta))
                self.assertIsNone(t)
                self.assertTrue(why)

    def test_it_lands_in_the_memory_file(self):
        mod = load()
        st = mod.load_state()
        path = os.path.join(mod.TMP, "MEMORY.md")
        with open(path, "w") as f:
            f.write("")
        mod.MEMORY_FILE = path
        mod._MEMORY_TEXT.update(mtime=None, text="")
        mid = self.store(st)
        mod.save_facts(st, {"remember": ["Likes tea"], "channel_id": CH}, mid)
        with open(path) as f:
            body = f.read()
        self.assertIn("Likes tea", body)
        self.assertIn("Discord server", body)


class OwnerMode(unittest.TestCase):
    """A: the owner's own words get the private memory; everyone gets the
    public snapshot only."""

    def item_for(self, mod, st, uid="111", kind="mention"):
        mod.CHANNELS[CH] = chan()
        q = []
        mod.enqueue_message(H.msg(CH, uid, "hi"), "Srv #general", st, q,
                            guild_id=G1, kind=kind)
        return q[0]

    def setUp(self):
        self.mod = load()
        self.st = self.mod.load_state()
        self.st["guild_joined"] = {G1: 0}
        private = os.path.join(self.mod.TMP, "MEMORY.md")
        public = os.path.join(self.mod.TMP, "MEMORY.public.md")
        with open(private, "w") as f:
            f.write("PRIVATE: likes tea\n")
        with open(public, "w") as f:
            f.write("PUBLIC: builds a Discord bot\n")
        self.mod.MEMORY_FILE = private
        self.mod.PUBLIC_MEMORY_FILE = public
        self.mod._MEMORY_TEXT.update(mtime=None, text="")
        self.mod._PUBLIC_MEMORY_TEXT.update(mtime=None, text="")

    def test_the_owner_gets_the_private_memory(self):
        item = self.item_for(self.mod, self.st)
        self.assertTrue(item["owner_mode"])
        self.assertIn("PRIVATE", item["muse_memory"])
        self.assertIn("PUBLIC", item["public_memory"])

    def test_everyone_else_gets_only_the_public_one(self):
        item = self.item_for(self.mod, self.st, uid="222")
        self.assertFalse(item["owner_mode"])
        self.assertEqual(item["muse_memory"], "")
        self.assertIn("PUBLIC", item["public_memory"])

    def test_a_bot_never_gets_owner_mode(self):
        self.mod.CHANNELS[CH] = chan()
        q = []
        self.mod.enqueue_message(H.msg(CH, "999", "hi", bot=True),
                                 "Srv #general", self.st, q, guild_id=G1)
        self.assertFalse(q[0]["owner_mode"])


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

    def test_a_place_can_stay_inline(self):
        d = tmpdir("cfg_threads_")
        cfg = self.CFG + f"""servers:
  "200":                       # a server
    name: Lounge
    channels:
      "300":
        name: quiet
        threads: off
"""
        st = MS.load(write(os.path.join(d, "config.yaml"), cfg),
                     os.path.join(MUT, "plugins"))
        scope = MS.Scope.server("200", "Lounge", "300", None)
        self.assertFalse(st.resolve(scope)["style.threads"])
        other = MS.Scope.server("200", "Lounge", "301", None)
        self.assertTrue(st.resolve(other)["style.threads"])

    def test_validate_rejects_a_stranger_next_to_it(self):
        d = tmpdir("cfg_threads_")
        cfg = self.CFG.replace("thread_replies: on", "threads_reply: on")
        with self.assertRaises(Exception) as ctx:
            MS.load(write(os.path.join(d, "config.yaml"), cfg),
                    os.path.join(MUT, "plugins"))
        self.assertIn("threads_reply", str(ctx.exception))


if __name__ == "__main__":
    unittest.main(verbosity=2)
