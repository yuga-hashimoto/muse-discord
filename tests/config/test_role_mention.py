"""Role mentions: a message that @mentions a role the bot itself holds is
direct address, exactly like an @mention of the bot (Discord puts role
ids in `mention_roles`, never in `mentions`, so the old check missed
them). A role the bot does not hold is someone else's; `@everyone` /
`@here` never trigger. Membership is read once per server and cached
(member_roles), and a failed lookup wakes nobody.

MUT_DIR = the package tree under test.

Usage: [MUT_DIR=<tree>] python3 tests/config/test_role_mention.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "_lib"))
import monitor_harness as H  # noqa: E402
from monitor_harness import G1, chan  # noqa: E402

CH = "C1"
THREAD = "T1"
ROLE = "R1"      # a role the bot itself holds
OTHER = "R2"     # a role it does not

MEMBER = f"/guilds/{G1}/members/{H.BOT}"


def load(own_roles=(ROLE,)):
    mod = H.load_monitor()
    mod.CHANNELS = {
        CH: chan(),
        THREAD: {"name": "chat", "guild_id": G1, "guild_name": "Srv",
                 "can_send": True, "thread": True, "parent_id": CH},
    }
    mod.api.routes[MEMBER] = {"roles": list(own_roles)}
    return mod


def said(cid=CH, roles=(), mention=False, everyone=False, uid="222",
         bot=False):
    m = H.msg(cid, uid, "hey", mention=mention, bot=bot)
    m["mention_roles"] = list(roles)
    m["mention_everyone"] = everyone
    return m


class RoleMention(unittest.TestCase):
    def test_a_role_the_bot_holds_is_direct_address(self):
        mod = load()
        m = said(roles=[ROLE])
        self.assertTrue(mod.is_mention(m))
        self.assertFalse(mod.addresses_someone_else(m))
        self.assertEqual(mod.trigger_verdict(m, G1, None)[0], "mention")

    def test_a_user_mention_still_works_without_any_role(self):
        mod = load()
        m = said(mention=True)
        self.assertTrue(mod.is_mention(m))
        self.assertEqual(mod.trigger_verdict(m, G1, None)[0], "mention")
        self.assertEqual(mod.api.calls.count(MEMBER), 0)  # no lookup needed

    def test_a_role_it_does_not_hold_is_someone_else(self):
        mod = load()
        m = said(roles=[OTHER])
        self.assertFalse(mod.is_mention(m))
        self.assertTrue(mod.addresses_someone_else(m))
        self.assertIsNone(mod.trigger_verdict(m, G1, None)[0])

    def test_everyone_is_not_a_role_mention(self):
        mod = load()
        m = said(roles=[], everyone=True)
        self.assertFalse(mod.is_mention(m))
        self.assertTrue(mod.addresses_someone_else(m))
        self.assertIsNone(mod.trigger_verdict(m, G1, None)[0])

    def test_a_role_mention_wakes_it_in_a_thread(self):
        # Threads are mention-only: a role mention is a mention there too.
        mod = load()
        m = said(cid=THREAD, roles=[ROLE])
        self.assertEqual(mod.trigger_verdict(m, G1, None)[0], "mention")

    def test_another_bots_role_ping_still_counts_as_addressed(self):
        mod = load()
        m = said(roles=[ROLE], uid="888", bot=True)
        self.assertEqual(mod.trigger_verdict(m, G1, None)[0], "mention")

    def test_membership_is_read_once(self):
        mod = load()
        for _ in range(3):
            mod.trigger_verdict(said(roles=[ROLE]), G1, None)
        self.assertEqual(mod.api.calls.count(MEMBER), 1)

    def test_a_failed_lookup_wakes_nobody_and_heals(self):
        mod = load()
        mod.api.routes.pop(MEMBER)
        self.assertFalse(mod.is_mention(said(roles=[ROLE])))
        self.assertIsNone(mod.trigger_verdict(said(roles=[ROLE]), G1,
                                               None)[0])
        # The API answers again: the next ping triggers as usual.
        mod._MEMBER_ROLES.clear()
        mod.api.routes[MEMBER] = {"roles": [ROLE]}
        self.assertEqual(mod.trigger_verdict(said(roles=[ROLE]), G1,
                                             None)[0], "mention")


if __name__ == "__main__":
    unittest.main(verbosity=2)
