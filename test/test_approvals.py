#!/usr/bin/env python3
"""Tests for approvals (loom's approvals piece).

    python3 test/test_approvals.py

A body asks, the owner allows or denies on the page, and an allowed approval is
spent once by exactly the action it names, by the body that asked.
"""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "approvals"

# Nothing here reaches a real store, a real piece home or a house's forwarding.
_sandbox = tempfile.TemporaryDirectory()
for k in ("LOOM_PIECE_HOME", "HOUSE_ID", "APPROVALS_LOCAL_AGENT", "LOOM_BODY_CONVERSATION", "LOOM_BODY_KEY"):
    os.environ.pop(k, None)
os.environ["APPROVALS_DIR"] = str(Path(_sandbox.name) / "unused")
os.environ["APPROVALS_KINDS"] = json.dumps({"github-write": "A GitHub write"})

spec = importlib.util.spec_from_loader("approvals_cli", loader=None, origin=str(SCRIPT))
ap = importlib.util.module_from_spec(spec)
ap.__file__ = str(SCRIPT)
exec(compile(SCRIPT.read_text(), str(SCRIPT), "exec"), ap.__dict__)

HOUSE = {"kind": "agent", "agent": "ada", "house": "h-7"}


def fake_verify(statement):
    """The runtime's --verify, for tests: "S:<agent>:<for>" verifies; for is owner, a slug, ? (unknown) or a,b (several)."""
    parts = str(statement).split(":")
    if len(parts) != 3 or parts[0] != "S":
        return None
    agent, f = parts[1], parts[2]
    if f == "owner":
        return {"ok": True, "agent": agent, "for": "jonathan", "owner": True, "people": []}
    if f == "?":
        return {"ok": True, "agent": agent, "for": None, "owner": False, "people": []}
    if f.startswith("@"):      # a person by this name, not the owner
        return {"ok": True, "agent": agent, "for": f[1:], "owner": False, "people": [f[1:]]}
    people = f.split(",")
    return {"ok": True, "agent": agent, "for": people[0] if len(people) == 1 else None, "owner": False, "people": people}


# Every test runs with the fake runtime; a call that's the owner's own work says so, as a house's piece-call would.
REAL_VERIFY = ap.verify_statement
ap.verify_statement = fake_verify
AS_OWNER = ["--principal", "S:ada:owner"]


def owned(argv):
    """argv as the owner's own work, unless it already says whose (or is a bare verb with nothing to say it for)."""
    return argv if "--principal" in argv or not argv else [*argv, *AS_OWNER]


class Approvals(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(ap, "STATE", Path(self.td.name))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.td.cleanup()

    def api(self, argv, caller=HOUSE):
        return ap.api_answer({"caller": caller, "path": "run", "body": {"argv": owned(argv)}})

    def ask(self, target="acme/app:octocat", caller=HOUSE):
        r = self.api(["request", "--kind", "github-write", "--target", target, "--what", "push the fix to acme/app <b>",
                      "--why", "Sam asked for it in #general"], caller)
        self.assertEqual(r["body"]["exit"], 0, r)
        return r["body"]["stdout"].split(":")[0]

    def test_ask_allow_once_use_once(self):
        aid = self.ask()
        self.assertEqual(self.ask(), aid, "asking again is the same card")
        m = ap.ui_manifest()
        self.assertEqual(len(m["asks"]), 1)
        self.assertEqual(m["asks"][0]["text"], "(no plain question given) Push the fix to acme/app b")
        self.assertNotIn("<b>", json.dumps(m))
        # In words: the wall (named by APPROVALS_KINDS), the target, where it was asked from (no house id), and why.
        self.assertEqual(m["asks"][0]["meta"], "A GitHub write: acme/app:octocat · from Ada's house, for any of Ada's own bodies · “Sam asked for it in #general”")
        self.assertEqual([b["action"] for b in m["asks"][0]["actions"]], ["allow", "deny"])
        # Waiting: nothing to spend.
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"])["body"]["exit"], 1)
        self.assertTrue(ap.ui_act({"action": "allow", "fields": {"id": aid}})["ok"])
        self.assertEqual(ap.ui_manifest()["asks"], [])
        # Only exactly what it allowed, by who asked.
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/other:octocat"])["body"]["exit"], 1)
        self.assertEqual(self.api(["use", aid, "--kind", "relay", "--target", "acme/app:octocat"])["body"]["exit"], 1)
        # Another agent can't spend what this agent asked for.
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"],
                                  {"kind": "agent", "agent": "bo", "house": "h-8"})["body"]["exit"], 1)
        # Nor a house of a service outsiders can steer (a chat or bridge house), for what a plain house asked; and
        # its refusal names neither the kind, the target nor where it was asked.
        r = self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"],
                     {"kind": "agent", "agent": "ada", "house": "h-9", "service": "chat"})
        self.assertEqual(r["body"]["exit"], 1, r)
        for word in ("acme", "github-write", "h-7"):
            self.assertNotIn(word, r["body"]["stderr"])
        # Any of the agent's own plain bodies can, wherever it asked from (the owner, 2026-11-07: "if I've given
        # permission it doesn't matter from where or to which body"): here, a later house, once.
        ok = self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"],
                      {"kind": "agent", "agent": "ada", "house": "h-9"})
        self.assertEqual(ok["body"]["exit"], 0, ok)
        # Once.
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"])["body"]["exit"], 1)
        self.assertEqual(self.api(["check", aid])["body"]["stdout"], "used\n")
        events = [json.loads(l)["event"] for l in (Path(self.td.name) / "audit.jsonl").read_text().splitlines()]
        self.assertEqual(events, ["request", "allow", "refused-use", "refused-use", "refused-use", "refused-use", "use"])

    def test_any_of_the_agent_s_bodies_spends_it_and_asking_again_from_one_is_the_same_card(self):
        # Asked from a service's house; the continuation runs in a plain house, a plain agent token or loom's machine.
        svc = {"kind": "agent", "agent": "ada", "house": "h-8", "service": "chat"}
        aid = self.ask(caller=svc)
        for other in ({"kind": "agent", "agent": "ada", "house": "h-9"}, {"kind": "agent", "agent": "ada"}):
            self.assertEqual(self.ask(caller=other), aid, other)
        with mock.patch.object(ap, "LOCAL_AGENT", "ada"):
            self.assertEqual(ap.run(["request", "--kind", "github-write", "--target", "acme/app:octocat", "--what", "w", "--why", "y"]).split(":")[0], aid)
        # Another agent's ask for the same target is its own card.
        self.assertNotEqual(self.ask(caller={"kind": "agent", "agent": "bo", "house": "h-9"}), aid)
        ap.ui_act({"action": "allow", "fields": {"id": aid}})
        # Allowed: asking again from any of ada's bodies says so, and one spends it once.
        r = self.api(["request", "--kind", "github-write", "--target", "acme/app:octocat", "--what", "w", "--why", "y"], {"kind": "agent", "agent": "ada", "house": "h-10"})
        self.assertIn(f"{aid}: already allowed", r["body"]["stdout"])
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"], {"kind": "agent", "agent": "bo", "house": "h-10"})["body"]["exit"], 1)
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"], {"kind": "agent", "agent": "ada", "house": "h-10"})["body"]["exit"], 0)
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"], svc)["body"]["exit"], 1)

    def test_a_service_house_spends_only_what_its_own_service_asked_for(self):
        chat = {"kind": "agent", "agent": "ada", "house": "h-8", "service": "chat"}
        aid = self.ask(caller=chat)
        ap.ui_act({"action": "allow", "fields": {"id": aid}})
        # Another service's house can't, and asking from it is its own card.
        bridge = {"kind": "agent", "agent": "ada", "house": "h-9", "service": "bridge"}
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"], bridge)["body"]["exit"], 1)
        self.assertNotEqual(self.ask(caller=bridge), aid)
        self.assertEqual(self.api(["check", aid, "--json"], bridge)["body"]["exit"], 1)
        # A later house of the same service can.
        ok = self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"], {**chat, "house": "h-10"})
        self.assertEqual(ok["body"]["exit"], 0, ok)
        # What a plain house asked for is a service house's own card, never the plain one.
        plain = self.ask(target="acme/other:octocat", caller={"kind": "agent", "agent": "ada", "house": "h-11"})
        self.assertNotEqual(self.ask(target="acme/other:octocat", caller=chat), plain)

    def test_another_agent_learns_nothing_of_what_it_is_for_from_use(self):
        aid = self.ask(target="acme/secret-repo:octocat")
        ap.ui_act({"action": "allow", "fields": {"id": aid}})
        r = self.api(["use", aid, "--kind", "x", "--target", "y"], {"kind": "agent", "agent": "bo", "house": "h-9"})
        self.assertEqual(r["body"]["exit"], 1)
        for word in ("acme", "github-write", "h-7", "ada"):
            self.assertNotIn(word, r["body"]["stderr"])

    def test_another_body_asking_again_gets_the_card_but_doesn_t_reword_it(self):
        aid = self.ask(caller={"kind": "agent", "agent": "ada", "house": "h-8", "service": "chat"})
        r = self.api(["request", "--kind", "github-write", "--target", "acme/app:octocat", "--ask", "OK to push the fix to the app now?",
                      "--why", "It's ready.", "--if-yes", "It goes out.", "--if-no", "It waits."], {"kind": "agent", "agent": "ada", "house": "h-9"})
        self.assertTrue(r["body"]["stdout"].startswith(aid), r)
        self.assertNotIn("ask", ap.get(aid))

    def test_only_the_piece_spends_a_wake_approval_and_a_body_s_try_leaves_it_allowed(self):
        aid = ap.run(["request", "--kind", "local-wake", "--target", "ada:0123456789abcdef", "--what", "run X", "--why", "y",
                      "--as", json.dumps(HOUSE)]).split(":")[0]
        ap.ui_act({"action": "allow", "fields": {"id": aid}})
        r = self.api(["use", aid, "--kind", "local-wake", "--target", "ada:0123456789abcdef"], {"kind": "agent", "agent": "ada", "house": "h-9"})
        self.assertEqual(r["body"]["exit"], 1, r)
        self.assertEqual(ap.get(aid)["state"], "allowed")
        self.assertEqual(ap.run(["use", aid, "--kind", "local-wake", "--target", "ada:0123456789abcdef", "--as", json.dumps({**HOUSE, "house": "h-9"})]),
                         f"{aid}: used\n")

    def test_check_json_tells_the_agent_that_asked_exactly_what_to_spend_it_on(self):
        aid = self.ask(caller={"kind": "agent", "agent": "ada", "house": "h-8", "service": "chat"})
        r = self.api(["check", aid, "--json"], {"kind": "agent", "agent": "ada", "house": "h-9"})
        self.assertEqual(r["body"]["exit"], 0, r)
        got = json.loads(r["body"]["stdout"])
        self.assertEqual(got, {"id": aid, "agent": "ada", "for": "owner", "state": "pending", "kind": "github-write", "target": "acme/app:octocat", "expires": None})
        ap.ui_act({"action": "allow", "fields": {"id": aid}})
        got = json.loads(self.api(["check", aid, "--json"], {"kind": "agent", "agent": "ada", "house": "h-9"})["body"]["stdout"])
        self.assertEqual(got["state"], "allowed")
        self.assertTrue(got["expires"])
        # Another agent reads only the state, as plain check gives anyone: not what it's for.
        r = self.api(["check", aid, "--json"], {"kind": "agent", "agent": "bo", "house": "h-9"})
        self.assertEqual(r["body"]["exit"], 1, r)
        self.assertEqual(self.api(["check", aid], {"kind": "agent", "agent": "bo", "house": "h-9"})["body"]["stdout"], "allowed\n")
        # A piece on loom's machine reads it for the body it acts for (--as), never over the API.
        self.assertEqual(json.loads(ap.run(["check", aid, "--json", "--as", json.dumps(HOUSE)]))["target"], "acme/app:octocat")
        self.assertEqual(self.api(["check", aid, "--json", "--as", json.dumps(HOUSE)])["body"]["exit"], 1)
        with self.assertRaises(PermissionError):
            ap.run(["check", aid, "--json", "--as", json.dumps({"kind": "agent", "agent": "bo", "house": "h-1"})])

    def test_an_answer_records_the_member_loom_names(self):
        aid = self.ask()
        self.assertTrue(ap.ui_act({"action": "allow", "fields": {"id": aid}, "caller": {"kind": "owner", "who": "pat-owner", "agents": "*"}})["ok"])
        self.assertEqual(ap.get(aid)["by"], "pat-owner")
        self.assertIn("by pat-owner", ap.ui_manifest()["answered"][0]["outcome"])
        aid2 = self.ask(target="acme/web:octocat")
        ap.ui_act({"action": "deny", "fields": {"id": aid2}, "caller": {"kind": "owner", "who": "Not A Slug"}})
        self.assertNotIn("by", ap.get(aid2))

    def test_deny_and_expiry(self):
        aid = self.ask()
        ap.ui_act({"action": "deny", "fields": {"id": aid}})
        self.assertEqual(self.api(["check", aid])["body"]["stdout"], "denied\n")
        self.assertFalse(ap.ui_act({"action": "allow", "fields": {"id": aid}})["ok"], "a denied one stays denied")
        aid2 = self.ask(target="acme/web:octocat")
        ap.ui_act({"action": "allow", "fields": {"id": aid2}})
        later = ap.now().timestamp() + 61 * 60
        with mock.patch.object(ap, "now", lambda: ap.datetime.fromtimestamp(later, ap.timezone.utc)):
            self.assertEqual(self.api(["check", aid2])["body"]["stdout"], "expired\n")
            self.assertEqual(self.api(["use", aid2, "--kind", "github-write", "--target", "acme/web:octocat"])["body"]["exit"], 1)

    def test_only_bodies_ask_and_nobody_approves_through_the_api(self):
        for c in ({"kind": "owner"}, {"kind": "piece", "subject": "box-1"}, {}):
            self.assertEqual(self.api(["request", "--kind", "x", "--target", "t", "--what", "w", "--why", "y"], c)["status"], 403)
        aid = self.ask()
        for verb in (["allow", aid], ["ui", "act"], ["list"]):
            self.assertEqual(self.api(verb)["body"]["exit"], 1)
        self.assertEqual(self.api(["check", aid])["body"]["stdout"], "pending\n")
        # Words are required: the owner decides by them.
        self.assertEqual(self.api(["request", "--kind", "github-write", "--target", "t", "--what", "w"])["body"]["exit"], 1)

    def test_the_owner_reads_states_for_a_needs_you_and_nobody_else_does(self):
        aid = self.ask()
        other = self.ask(target="acme/web:octocat")
        state = lambda ids, c={"kind": "owner", "agents": "*"}: ap.api_answer({"caller": c, "path": "state", "body": {"ids": ids}})
        r = state([aid, other, "a-00000000"])
        self.assertEqual(r["status"], 200)
        # Known ids only: one it has no record of is left out (core reads that as gone).
        self.assertEqual(r["body"]["approvals"], {aid: {"state": "pending", "agent": "ada", "kind": "github-write"},
                                                  other: {"state": "pending", "agent": "ada", "kind": "github-write"}})
        ap.answer(aid, "allow")
        self.assertEqual(state([aid])["body"]["approvals"][aid]["state"], "allowed")
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"])["body"]["exit"], 0)
        self.assertEqual(state([aid])["body"]["approvals"][aid]["state"], "used")
        ap.answer(other, "deny")
        self.assertEqual(state([other])["body"]["approvals"][other]["state"], "denied")
        # No words in it: core shows the card's own while it waits.
        self.assertNotIn("push the fix", json.dumps(state([aid, other])))
        # A body (or a piece) doesn't read it, and nothing is answered through it.
        for c in (HOUSE, {"kind": "piece", "subject": "box-1"}, {}):
            self.assertEqual(state([aid], c)["status"], 403)
        for bad in ([], ["nope"], [aid] * 51, "a-00000000"):
            self.assertEqual(state(bad)["status"], 400)

    def test_a_piece_on_loom_s_machine_spends_it_for_the_body_it_acts_for_and_only_locally(self):
        # The clock runs a house's `wake relay` here, so it spends the house's approval with --as.
        r = self.api(["request", "--kind", "relay", "--target", "ada->cy", "--what", "relay to cy", "--why", "ask about the tracks"])
        aid = r["body"]["stdout"].split(":")[0]
        ap.ui_act({"action": "allow", "fields": {"id": aid}})
        # Over the API, --as is refused: a body can't claim to be another.
        self.assertEqual(self.api(["use", aid, "--kind", "relay", "--target", "ada->cy", "--as", json.dumps(HOUSE)],
                                  {"kind": "agent", "agent": "ada", "house": "h-9"})["body"]["exit"], 1)
        # A piece that says on whose behalf (the clock) is held to it: the owner's yes isn't spent for Loren.
        with self.assertRaises(PermissionError):
            ap.run(["use", aid, "--kind", "relay", "--target", "ada->cy", "--as", json.dumps(HOUSE), "--on-behalf", "loren"])
        # A relay spent without saying whom is refused (t-2052): the runtime always says (t-1953), so whom is checked.
        with self.assertRaises(PermissionError) as e:
            ap.run(["use", aid, "--kind", "relay", "--target", "ada->cy", "--as", json.dumps(HOUSE)])
        self.assertIn("--on-behalf", str(e.exception))
        with self.assertRaises(PermissionError):
            ap.run(["use", aid, "--kind", "relay", "--target", "ada->cy", "--as", json.dumps(HOUSE), "--on-behalf", ""])
        # "-" (the runtime's word for a caller that proved no conversation) is someone loom can't name: not the owner's.
        with self.assertRaises(PermissionError):
            ap.run(["use", aid, "--kind", "relay", "--target", "ada->cy", "--as", json.dumps(HOUSE), "--on-behalf", "-"])
        self.assertEqual(ap.get(aid)["state"], "allowed", "a refused spend leaves it allowed")
        self.assertEqual(ap.run(["use", aid, "--kind", "relay", "--target", "ada->cy", "--as", json.dumps(HOUSE), "--on-behalf", "owner"]), f"{aid}: used\n")

    def test_a_piece_may_file_a_request_for_the_body_it_acts_for_locally(self):
        out = ap.run(["request", "--kind", "local-wake", "--target", "ada:abc", "--what", "run X", "--why", "y", "--as", json.dumps(HOUSE)])
        aid = out.split(":")[0]
        self.assertEqual(ap.get(aid)["who"], {"agent": "ada", "where": "h-7"})
        r = self.api(["request", "--kind", "local-wake", "--target", "ada:abd", "--what", "w", "--why", "y", "--as", json.dumps(HOUSE)],
                     {"kind": "agent", "agent": "ada", "house": "h-9"})
        self.assertEqual(r["body"]["exit"], 1)

    def test_a_request_from_loom_s_machine_is_the_local_agents(self):
        with mock.patch.object(ap, "LOCAL_AGENT", "zed"):
            aid = ap.run(["request", "--kind", "relay", "--target", "zed->ada", "--what", "w", "--why", "y"]).split(":")[0]
        self.assertEqual(ap.get(aid)["who"], {"agent": "zed", "where": "loom's machine"})

    def test_a_target_is_matched_exactly_as_shown(self):
        # A target is printable ASCII and compared raw: two raw targets never meet as one, and none looks like another.
        refused = lambda argv: self.assertNotEqual(self.api(argv)["body"]["exit"], 0, argv)
        for t in ("a\tb", " padded", "two  spaces", "ｆｕｌｌwidth", "zero\u200bwidth", "acme/\u0430pp", "cafe\u0301"):
            refused(["request", "--kind", "github-write", "--target", t, "--what", "w", "--why", "y"])
        aid = self.ask(target="acme/app")
        ap.answer(aid, "allow", "pat-owner")
        refused(["use", aid, "--kind", "github-write", "--target", "acme/app "])
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/app"])["body"]["exit"], 0)

    def test_only_a_piece_files_a_wake_approval(self):
        # A body can't file a local-wake card itself (its words would stand in for the piece's).
        r = self.api(["request", "--kind", "local-wake", "--target", "ada:abcdef12", "--what", "w", "--why", "y"])
        self.assertNotEqual(r["body"]["exit"], 0)

    def test_the_card_hides_only_a_wakes_internal_key(self):
        a = {"kind": "github-write", "target": "acme:deadbeef00", "who": HOUSE, "why": "y"}
        self.assertIn("acme:deadbeef00", ap.meta_of(a))
        self.assertNotIn("ada:deadbeef00", ap.meta_of({**a, "kind": "local-wake", "target": "ada:deadbeef00"}))

    def test_state_files_are_private(self):
        self.ask()
        for f in ("approvals.json", "audit.jsonl", "approvals.lock"):
            self.assertEqual(os.stat(ap.STATE / f).st_mode & 0o777, 0o600, f)
        self.assertEqual(os.stat(ap.STATE).st_mode & 0o777, 0o700)

    def test_words_that_dont_fit_on_the_card_are_refused_not_cut(self):
        # Fullwidth letters normalize (NFKC) to more than they look, and a long tail could hide.
        for what in ("x" * 301, "ﾟ" * 200 + "x" * 150):
            r = self.api(["request", "--kind", "github-write", "--target", "t", "--what", what, "--why", "y"])
            self.assertEqual(r["body"]["exit"], 1, what[:20])
            self.assertIn("at most", r["body"]["stderr"])

    def test_the_inbox_files_them_as_permissions_and_keeps_what_was_answered(self):
        aid = self.ask()
        m = ap.ui_manifest()
        self.assertEqual(m["asks"][0]["kind"], "permission")
        self.assertEqual(m["answered"], [])
        ap.ui_act({"action": "deny", "fields": {"id": aid}})
        m = ap.ui_manifest()
        self.assertEqual(m["asks"], [])
        self.assertEqual((m["answered"][0]["outcome"], m["answered"][0]["kind"], m["answered"][0]["agent"]), ("Denied.", "permission", "ada"))
        # The agent's name, not its slug.
        self.assertTrue(m["answered"][0]["text"].startswith("Ada asked: push the fix"))

    def test_a_wake_on_loom_s_machine_says_what_it_would_do_once(self):
        a = {"kind": "local-wake", "what": "run on loom's machine: read-only DNS check of the resolver", "who": {"agent": "ada"}}
        self.assertEqual(ap.what_of(a), "Read-only DNS check of the resolver")
        self.assertEqual(ap.what_of(dict(a, what="run on loom's machine: t-12: flip the boxes")), "t-12: flip the boxes")
        self.assertEqual(ap.what_of({"kind": "github-write", "what": "push the fix", "who": {"agent": "ada"}}), "push the fix")

    def test_a_wake_on_loom_s_machine_reads_as_one_without_its_key(self):
        a = {"kind": "local-wake", "target": "ada:0123456789abcdef", "why": "DNS check", "who": {"agent": "ada", "where": "h-306 (chat-bodies)"}}
        self.assertEqual(ap.meta_of(a), "A wake on loom's machine · from Ada's chat bodies house, for any of Ada's own bodies or its chat bodies houses · “DNS check”")

    def test_an_unnamed_kind_reads_as_words(self):
        a = {"kind": "deploy-gate", "target": "acme/app", "why": "ship", "who": {"agent": "ada", "where": "a body"}}
        self.assertEqual(ap.meta_of(a), "Deploy gate: acme/app · from a body, for any of Ada's own bodies · “ship”")

    def test_the_manifest_has_a_tab_so_its_links_land_on_a_page(self):
        # Without a tab loom serves no /p/approvals: every ask's and answered row's page "" was a dead link.
        aid = self.ask()
        m = ap.ui_manifest()
        self.assertEqual(m["tab"], {"label": "Approvals", "kind": "setting", "icon": "stamp"})
        self.assertEqual(m["asks"][0]["page"], "")
        ap.ui_act({"action": "deny", "fields": {"id": aid}})
        self.assertEqual(ap.ui_manifest()["answered"][0]["page"], "")
        self.assertNotEqual(ap.ui_page({"path": ""})["title"], "Not found")

    def test_the_page_puts_what_waits_first_with_its_answers_in_a_sheet(self):
        aid = self.ask()
        page = ap.ui_page({"path": ""})
        self.assertEqual(page["title"], "Approvals")
        waiting = page["sections"][0]
        self.assertEqual((waiting["id"], waiting["tone"]), ("waiting", "warn"))
        row = waiting["blocks"][0]["rows"][0]
        # An old-style request: its words, marked as having no plain question; why as the value; the kind and target
        # only in the sheet's small print.
        self.assertEqual(row["label"], "(no plain question given) Push the fix to acme/app b")
        self.assertEqual(row["ref"], aid, "the desktop can chat about the exact approval")
        self.assertEqual(row["value"], "Sam asked for it in #general")
        self.assertEqual(row["detail"], "Ada · just now")
        self.assertIn("A GitHub write: acme/app:octocat", row["sheet"]["blocks"][-1]["text"])
        buttons = [b for b in row["sheet"]["blocks"] if b["type"] == "buttons"][0]["items"]
        self.assertEqual([(b["action"], b["label"], b["fields"]) for b in buttons], [("allow", "Allow once", {"id": aid}), ("deny", "Deny", {"id": aid})])
        # No raw id in what people read: only in the record's sheet.
        shown = json.dumps([s for s in page["sections"] if s["id"] != "record"])
        self.assertNotIn(aid, shown.replace(json.dumps({"id": aid})[1:-1], "").replace(json.dumps({"ref": aid})[1:-1], ""))
        self.assertIn(aid, json.dumps([s for s in page["sections"] if s["id"] == "record"]))

    def test_the_page_says_how_each_answered_one_went_in_words(self):
        aid = self.ask()
        ap.ui_act({"action": "allow", "fields": {"id": aid}, "caller": {"kind": "owner", "who": "pat-owner", "agents": "*"}})
        self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"])
        aid2 = self.ask(target="acme/web:octocat")
        ap.ui_act({"action": "deny", "fields": {"id": aid2}})
        page = ap.ui_page({"path": ""})
        self.assertEqual(page["sections"][0]["blocks"][0]["type"], "health", "nothing waiting: a plain headline")
        answered = next(s for s in page["sections"] if s["id"] == "answered")["blocks"][0]
        self.assertEqual({i["ref"] for i in answered["items"]}, {aid, aid2})
        self.assertTrue(answered["wrap"], "a body's words are shown whole")
        metas = {i["meta"].split(" · ")[1] for i in answered["items"]}
        self.assertEqual(metas, {"Allowed by Pat Owner, and used just now", "Denied just now"})
        self.assertEqual({i["text"] for i in answered["items"]}, {"Ada · A GitHub write: acme/web:octocat", "Ada · A GitHub write: acme/app:octocat"})

    def test_the_record_fits_loom_s_limit_however_many_there_are(self):
        for n in range(80):
            self.ask(target=f"acme/app-{n:02d}-with-a-longish-name:octocat")
        page = ap.ui_page({"path": ""})
        rec = next(s for s in page["sections"] if s["id"] == "record")["blocks"][0]["rows"][0]["sheet"]["blocks"][0]
        self.assertLessEqual(len(rec["text"]), 2000)
        self.assertIn("older (approvals list, on loom's machine)", rec["text"].splitlines()[-1])

    def test_ago_says_it_in_words(self):
        from datetime import timedelta
        t = ap.now()
        for secs, words in ((5, "just now"), (300, "5 min ago"), (7200, "2 hours ago"), (90000, "yesterday"), (3 * 86400, "3 days ago")):
            self.assertEqual(ap.ago(ap.iso(t - timedelta(seconds=secs)), t), words)
        self.assertTrue(ap.ago(ap.iso(t - timedelta(days=30)), t).startswith("on "))


class Home(unittest.TestCase):
    """Where the store is: the setting, else the piece home loom gave it, else found from where the bin lives."""

    def load(self, env, script=SCRIPT):
        with mock.patch.dict(os.environ, env, clear=False):
            for k in ("APPROVALS_DIR", "LOOM_PIECE_HOME"):
                if k not in env:
                    os.environ.pop(k, None)
            m = importlib.util.module_from_spec(importlib.util.spec_from_loader("x", loader=None))
            m.__file__ = str(script)
            exec(compile(SCRIPT.read_text(), str(script), "exec"), m.__dict__)
            return m

    def test_the_setting_wins(self):
        self.assertEqual(self.load({"APPROVALS_DIR": "/tmp/somewhere"}).STATE, Path("/tmp/somewhere"))

    def test_the_piece_home_loom_gives_it(self):
        self.assertEqual(self.load({"LOOM_PIECE_HOME": "/srv/pieces/approvals"}).STATE, Path("/srv/pieces/approvals/data"))

    def test_a_direct_run_of_a_managed_install_finds_its_home_and_the_owners_settings(self):
        with tempfile.TemporaryDirectory() as d:
            home = Path(d) / "approvals"
            pkg = home / "versions" / ("a" * 40) / "node_modules" / "loom-approvals" / "bin"
            pkg.mkdir(parents=True)
            (home / "piece.json").write_text("{}")
            (home / "settings.json").write_text(json.dumps({"APPROVALS_LOCAL_AGENT": "zed"}))
            m = self.load({}, script=pkg / "approvals")
            self.assertEqual((m.STATE, m.LOCAL_AGENT), (home / "data", "zed"))



SHA = "a" * 40
SHA2 = "b" * 40
T = f"github:acme/app#12@{SHA}"
CARDS = [{"slug": "pond", "name": "Pond", "repos": ["github:acme/pond", "github:acme/pond-ui"]}]
OWNER = {"kind": "owner", "who": "jonathan-barket", "agents": "*"}
HOUSE_WHO = {"agent": "ada", "where": "h-7"}


class Rules(unittest.TestCase):
    """Standing rules (loom docs/specs/changes.md): the owner's lasting answer for merging."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(ap, "STATE", Path(self.td.name)),
                        mock.patch.object(ap, "projects", lambda: CARDS)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.td.cleanup()

    def api(self, argv, caller=HOUSE):
        return ap.api_answer({"caller": caller, "path": "run", "body": {"argv": owned(argv)}})["body"]

    def rules(self, *rows):
        with ap.Store() as s:
            s.rules()
            s.replace_rules([ap.make_rule("merge", sc, t, "test") for sc, t in rows])

    def test_most_specific_wins_then_strictest(self):
        rules = [ap.make_rule("merge", sc, t, "x") for sc, t in [
            ("github:acme/*", "do"), ("project:pond", "ask"), ("github:acme/pond-*", "tell"), ("github:acme/pond", "tell")]]
        tier = lambda t, cards=CARDS: ap.resolve(rules, "merge", t, cards)[0]
        self.assertEqual(tier(f"github:acme/pond#1@{SHA}"), "tell", "the exact repo wins over its project")
        self.assertEqual(tier(f"github:acme/pond-ui#1@{SHA}"), "ask", "a project wins over a pattern")
        self.assertEqual(tier(f"github:acme/pond-x#1@{SHA}"), "tell", "fewer * wins")
        self.assertEqual(tier(f"github:acme/other#1@{SHA}"), "do")
        self.assertEqual(tier(f"github:ACME/Other#1@{SHA}"), "do", "case doesn't matter")
        self.assertEqual(tier(f"github:else/app#1@{SHA}"), "ask", "no rule: ask")
        self.assertEqual(tier(f"forgejo:acme/app#1@{SHA}"), "ask", "another forge isn't github's pattern")
        self.assertEqual(tier(f"github:acme/sub/deep#1@{SHA}"), "ask", "* stays inside one segment")
        tie = [ap.make_rule("merge", "github:acme/*", "do", "x"), ap.make_rule("merge", "github:*/app", "tell", "x")]
        self.assertEqual(ap.resolve(tie, "merge", T)[0], "tell", "a tie goes to the stricter")
        # The project list unreadable: no project rule applies, and the answer says so.
        t, _, note = ap.resolve(rules, "merge", f"github:acme/other#1@{SHA}", None)
        self.assertEqual(t, "ask", "fail closed: a do pattern doesn't win while a project rule can't be checked")
        self.assertIn("project list", note)
        with self.assertRaises(ValueError):
            ap.resolve(rules, "merge", "github:acme/app#12")       # no head

    def test_the_seed_is_written_once_and_only_when_there_are_none(self):
        seed = json.dumps([{"kind": "merge", "scope": "github:sleepunit-agents/*", "tier": "do"},
                           {"kind": "merge", "scope": "github:onterris/*", "tier": "ask"}])
        with mock.patch.dict(os.environ, {"APPROVALS_SEED_RULES": seed}):
            self.assertEqual(self.api(["rule", "merge", f"github:sleepunit-agents/loom#5@{SHA}"])["stdout"], "do\n")
            self.assertEqual(self.api(["rule", "merge", f"github:onterris/on-pond#5@{SHA}"])["stdout"], "ask\n")
            self.assertTrue(ap.ui_act({"action": "rule", "caller": OWNER,
                                       "fields": {"scope": "github:sleepunit-agents/*", "kind": "merge", "tier": "none"}})["ok"])
            # Removed stays removed: the seed isn't written again.
            self.assertEqual(self.api(["rule", "merge", f"github:sleepunit-agents/loom#5@{SHA}"])["stdout"], "ask\n")
        rules = json.loads((Path(self.td.name) / "rules.json").read_text())["rules"]
        self.assertEqual([(r["scope"], r["by"]) for r in rules], [("github:onterris/*", "seed")])
        self.assertEqual((Path(self.td.name) / "rules.json").stat().st_mode & 0o777, 0o600)

    def test_rule_says_the_newest_approval_for_that_exact_target(self):
        self.assertEqual(self.api(["rule", "merge", T])["stdout"], "ask\n")
        r = self.api(["request", "--kind", "merge", "--target", T, "--what", "merge acme/app#12", "--why", "CI green"])
        aid = r["stdout"].split(":")[0]
        self.assertEqual(self.api(["rule", "merge", T])["stdout"], f"ask pending {aid}\n")
        ap.ui_act({"action": "allow", "fields": {"id": aid}})
        self.assertEqual(self.api(["rule", "merge", T])["stdout"], f"ask allowed {aid}\n")
        self.assertEqual(self.api(["rule", "merge", f"github:acme/app#12@{SHA2}"])["stdout"], "ask\n", "another head")
        self.assertEqual(self.api(["rule", "merge", "github:acme/app#12"])["exit"], 1)
        self.rules(("github:acme/app", "do"))
        self.assertEqual(self.api(["rule", "merge", T])["stdout"], "do\n")

    def test_grant_is_spent_once_by_that_agent_from_anywhere_for_that_target_for_a_day(self):
        self.assertIn("allowed", ap.run(["grant", "--kind", "merge", "--target", T, "--for", "ada", "--by", "jonathan-barket"]))
        # The agent's request finds the owner's answer; its rule check says so.
        r = self.api(["request", "--kind", "merge", "--target", T, "--what", "merge it", "--why", "green"])
        self.assertIn("already allowed", r["stdout"])
        aid = r["stdout"].split(":")[0]
        a = ap.get(aid)
        self.assertEqual(a["by"], "jonathan-barket")
        hours = (ap.datetime.strptime(a["expires"], "%Y-%m-%dT%H:%M:%SZ") - ap.datetime.strptime(a["decided"], "%Y-%m-%dT%H:%M:%SZ")).total_seconds() / 3600
        self.assertEqual(hours, 24)
        # Not another agent; not another head (a push invalidates it); then the agent, once, from another house.
        self.assertEqual(self.api(["use", aid, "--kind", "merge", "--target", T], {"kind": "agent", "agent": "bo", "house": "h-2"})["exit"], 1)
        self.assertEqual(self.api(["use", aid, "--kind", "merge", "--target", f"github:acme/app#12@{SHA2}"])["exit"], 1)
        self.assertEqual(self.api(["use", aid, "--kind", "merge", "--target", T], {"kind": "agent", "agent": "ada", "house": "h-99"})["exit"], 0)
        self.assertEqual(self.api(["use", aid, "--kind", "merge", "--target", T])["exit"], 1, "once")

    def test_grant_answers_a_pending_request_instead_of_making_a_second(self):
        r = self.api(["request", "--kind", "merge", "--target", T, "--what", "merge it", "--why", "green"])
        aid = r["stdout"].split(":")[0]
        out = ap.run(["grant", "--kind", "merge", "--target", T, "--for", "ada", "--by", "jonathan-barket", "--deny"])
        self.assertEqual(out, f"{aid}: denied\n")
        self.assertEqual(len(ap.recent()), 1)
        self.assertEqual(self.api(["rule", "merge", T])["stdout"], f"ask denied {aid}\n")

    def test_grant_is_refused_over_the_api_and_for_anything_but_a_merge_target(self):
        self.assertEqual(self.api(["grant", "--kind", "merge", "--target", T, "--for", "ada"])["exit"], 1)
        with self.assertRaises(ValueError):
            ap.run(["grant", "--kind", "relay", "--target", "a->b", "--for", "ada"])
        with self.assertRaises(ValueError):
            ap.run(["grant", "--kind", "merge", "--target", "github:acme/app#12", "--for", "ada"])
        with mock.patch.dict(os.environ, {"HOUSE_ID": "h-1"}), mock.patch.object(ap, "forward", side_effect=SystemExit(9)) as fw:
            with mock.patch.object(ap.sys, "argv", ["approvals", "grant", "--kind", "merge", "--target", T, "--for", "ada"]):
                with self.assertRaises(SystemExit):
                    ap.main()
            fw.assert_called_once()          # in a house it's forwarded, so it arrives as a body's call and is refused

    def test_rules_are_the_owners_and_the_api_has_no_way_to_change_one(self):
        member = {"kind": "owner", "who": "sam", "agents": ["ada"]}
        for c in (member, {"kind": "agent", "agent": "ada"}):
            r = ap.ui_act({"action": "rule", "caller": c, "fields": {"scope": "github:acme/*", "kind": "merge", "tier": "do"}})
            self.assertFalse(r["ok"])
        self.assertFalse(ap.ui_act({"action": "rule", "fields": {"scope": "github:acme/*", "kind": "merge", "tier": "do"}})["ok"],
                         "no caller is not the owner: it fails closed")
        self.assertNotIn("rules", [x["id"] for x in ap.ui_page({"path": ""})["sections"]])
        self.assertTrue(ap.is_owner({"caller": {"kind": "owner", "who": "jo"}}), "agents absent: unrestricted")
        self.assertEqual(self.api(["rule", "merge", T])["stdout"], "ask\n")
        for argv in (["rule", "set", "github:acme/*", "do"], ["rule-add"], ["rules"]):
            self.assertEqual(self.api(argv)["exit"], 1)
        self.assertEqual(ap.api_answer({"caller": OWNER, "path": "rule", "body": {}})["status"], 404)
        ok = ap.ui_act({"action": "rule-add", "caller": OWNER, "fields": {"scope": "GitHub:Acme/*", "tier": "do"}})
        self.assertFalse(ok["ok"], "the forge is lowercase")
        ok = ap.ui_act({"action": "rule-add", "caller": OWNER, "fields": {"scope": "github:Acme/*", "tier": "do"}})
        self.assertTrue(ok["ok"], ok)
        self.assertEqual(self.api(["rule", "merge", T])["stdout"], "do\n")
        # The page shows them to the owner only.
        self.assertIn("rules", [x["id"] for x in ap.ui_page({"path": "", "caller": OWNER})["sections"]])
        self.assertNotIn("rules", [x["id"] for x in ap.ui_page({"path": "", "caller": member})["sections"]])

    def test_always_sets_the_projects_rule_and_allows_it_in_one_go(self):
        t = f"github:acme/pond-ui#3@{SHA}"
        aid = self.api(["request", "--kind", "merge", "--target", t, "--what", "merge pond-ui#3", "--why", "green"])["stdout"].split(":")[0]
        sheet = ap.ui_page({"path": "", "caller": OWNER})["sections"][0]["blocks"][0]["rows"][0]["sheet"]
        labels = [b["label"] for b in next(x for x in sheet["blocks"] if x["type"] == "buttons")["items"]]
        self.assertEqual(labels, ["Allow once", "Always for Pond", "Deny"])
        r = ap.ui_act({"action": "always", "caller": OWNER, "fields": {"id": aid}})
        self.assertTrue(r["ok"], r)
        self.assertEqual(ap.get(aid)["state"], "allowed")
        self.assertEqual(self.api(["rule", "merge", f"github:acme/pond#9@{SHA}"])["stdout"], "do\n")
        # With no project naming the repo, Always is for the repo itself.
        aid2 = self.api(["request", "--kind", "merge", "--target", T, "--what", "merge app#12", "--why", "green"])["stdout"].split(":")[0]
        self.assertEqual(ap.always_scope(ap.get(aid2), CARDS), ("github:acme/app", "Always for acme/app"))
        self.assertFalse(ap.ui_act({"action": "always", "caller": {"kind": "owner", "who": "sam", "agents": ["ada"]}, "fields": {"id": aid2}})["ok"])

    def test_tell_files_a_notice_for_do_or_tell_and_refuses_ask(self):
        self.rules(("github:acme/*", "tell"))
        r = self.api(["tell", "--kind", "merge", "--target", T, "--what", "the fix for the login page"])
        self.assertEqual(r["exit"], 0, r)
        n = ap.ui_manifest()["notices"][0]
        self.assertEqual((n["text"], n["meta"], n["agent"]), ("Ada merged acme/app#12", "the fix for the login page", "ada"))
        self.assertEqual(self.api(["tell", "--kind", "merge", "--target", f"github:else/app#1@{SHA}", "--what", "x"])["exit"], 1)

    def test_rule_answers_many_targets_for_another_agent_only_locally(self):
        self.rules(("github:acme/*", "do"))
        t2 = f"github:else/app#2@{SHA}"
        ap.run(["grant", "--kind", "merge", "--target", t2, "--for", "ada", "--by", "jo"])
        d = json.loads(ap.run(["rule", "merge", T, t2, "--for", "ada", "--json"])[0])
        self.assertEqual(d[T], {"tier": "do", "for": "owner", "scope": "github:acme/*"})
        self.assertEqual((d[t2]["tier"], d[t2]["approval"]["state"]), ("ask", "allowed"))
        self.assertEqual(self.api(["rule", "merge", T, "--for", "ada"])["exit"], 1)

    def test_a_target_with_a_pattern_in_it_is_refused_and_never_becomes_a_wildcard_rule(self):
        for bad in ("github:onterris/*#1@abc", f"github:onterris/*#1@{SHA}", f"github:onterris/a?b#1@{SHA}",
                    f"github:onterris/[ab]#1@{SHA}", f"github:onterris#1@{SHA}", f"github:a/b c#1@{SHA}"):
            r = self.api(["request", "--kind", "merge", "--target", bad, "--what", "merge it", "--why", "green"])
            self.assertEqual(r["exit"], 1, bad)
            self.assertIn("literal", r["stderr"], bad)
            with self.assertRaises(ValueError):
                ap.run(["grant", "--kind", "merge", "--target", bad, "--for", "ada"])
        # A record that somehow holds one (an older store, a hand edit) still can't become a rule.
        with ap.Store() as st:
            st.d["approvals"]["a-0000beef"] = {"id": "a-0000beef", "kind": "merge", "target": f"github:onterris/*#1@{SHA}",
                                               "what": "x", "why": "y", "who": HOUSE_WHO, "minutes": 60, "at": ap.iso(ap.now()),
                                               "state": "pending"}
            st.save()
        self.assertIsNone(ap.always_scope(ap.get("a-0000beef"), CARDS))
        r = ap.ui_act({"action": "always", "caller": OWNER, "fields": {"id": "a-0000beef"}})
        self.assertFalse(r["ok"])
        self.assertEqual(ap.get("a-0000beef")["state"], "pending")
        with ap.Store() as st:
            self.assertFalse(any("*" in x["scope"] for x in st.rules()))
        # The literal forms each forge needs are accepted.
        for good in (T, f"forgejo:codeberg.org/acme/app#3@{SHA}", f"azure:org/proj/repo.x_y-z#9@{SHA}"):
            self.assertIsNotNone(ap.repo_of(good), good)

    def test_a_deny_stops_an_earlier_allow(self):
        r = self.api(["request", "--kind", "merge", "--target", T, "--what", "merge it", "--why", "green"])
        aid = r["stdout"].split(":")[0]
        ap.ui_act({"action": "allow", "fields": {"id": aid}})
        # Then the owner says Not yet for the same target (the Changes piece's grant --deny).
        ap.run(["grant", "--kind", "merge", "--target", T, "--for", "ada", "--by", "jo", "--deny"])
        self.assertEqual(ap.get(aid)["state"], "denied")
        self.assertEqual(self.api(["use", aid, "--kind", "merge", "--target", T])["exit"], 1)
        # Asking again finds the no, not the old yes.
        r = self.api(["request", "--kind", "merge", "--target", T, "--what", "merge it", "--why", "green"])
        self.assertIn("not yet", r["stdout"])
        # A deny on the card does the same to an earlier grant.
        t2 = f"github:acme/app#13@{SHA}"
        g = ap.run(["grant", "--kind", "merge", "--target", t2, "--for", "ada"]).split(":")[0]
        p = self.api(["request", "--kind", "merge", "--target", t2, "--what", "merge it", "--why", "green"])
        self.assertEqual(p["stdout"].split(":")[0], g)
        with ap.Store() as st:     # a pending one beside it (an older house's), then the owner denies it
            st.d["approvals"]["a-0000cafe"] = {"id": "a-0000cafe", "kind": "merge", "target": t2, "what": "x", "why": "y",
                                               "who": HOUSE_WHO, "minutes": 60, "at": ap.iso(ap.now()), "state": "pending"}
            st.save()
        ap.ui_act({"action": "deny", "fields": {"id": "a-0000cafe"}})
        self.assertEqual(ap.get(g)["state"], "denied")
        self.assertEqual(self.api(["use", g, "--kind", "merge", "--target", t2])["exit"], 1)
        # A later yes counts again.
        g2 = ap.run(["grant", "--kind", "merge", "--target", t2, "--for", "ada"]).split(":")[0]
        with ap.Store() as st:     # decided a second after the deny
            st.d["approvals"][g2]["decided"] = "2999-01-01T00:00:00Z"
            st.save()
        self.assertEqual(self.api(["use", g2, "--kind", "merge", "--target", t2])["exit"], 0)

    def test_a_project_scope_is_a_slug_and_project_is_no_forge(self):
        for bad in ("project:Pond", "project:po*nd", "project:foo/bar", "project:"):
            with self.assertRaises(ValueError, msg=bad):
                ap.make_rule("merge", bad, "ask", "x")
        r = ap.ui_act({"action": "rule-add", "caller": OWNER, "fields": {"scope": "project:Pond", "tier": "ask"}})
        self.assertFalse(r["ok"])

    def test_the_answer_is_for_this_agent_alone(self):
        ap.run(["grant", "--kind", "merge", "--target", T, "--for", "bo", "--by", "jo"])
        self.assertEqual(self.api(["rule", "merge", T])["stdout"], "ask\n", "bo's yes is not ada's")
        d = json.loads(ap.run(["rule", "merge", T, "--for", "ada", "--json"])[0])
        self.assertNotIn("approval", d[T])

    def test_a_rules_file_that_isnt_rules_asks_and_is_never_overwritten(self):
        path = Path(self.td.name) / "rules.json"
        for text in ('{"v":1,"rules":{"a":1}}', '{"v":1,"rules":[{"scope":"github:a/*","tier":"sure","kind":"merge"}]}', "not json"):
            path.write_text(text)
            self.assertEqual(self.api(["rule", "merge", f"github:a/b#1@{SHA}"])["stdout"], "ask\n", text)
            self.assertTrue(ap.ui_page({"path": "", "caller": OWNER})["sections"])
            r = ap.ui_act({"action": "rule-add", "caller": OWNER, "fields": {"scope": "github:x/*", "tier": "do"}})
            self.assertFalse(r["ok"], text)
            self.assertEqual(path.read_text(), text, "an edit never writes over a file it couldn't read")

    def test_a_project_list_that_cant_be_read_says_so_on_stderr(self):
        self.rules(("project:pond", "ask"), ("github:acme/*", "do"))
        with mock.patch.object(ap, "projects", lambda: None):
            r = self.api(["rule", "merge", f"github:acme/pond#1@{SHA}"])
        self.assertEqual(r["stdout"], "ask\n")
        self.assertIn("project list", r["stderr"])
        # Without a project rule, the list isn't needed and the pattern stands.
        self.rules(("github:acme/*", "do"))
        with mock.patch.object(ap, "projects", lambda: None):
            self.assertEqual(self.api(["rule", "merge", f"github:acme/pond#1@{SHA}"])["stdout"], "do\n")
        # Straight at resolve: no project rule, no list: the pattern stands. An exact repo stands even beside one.
        do = [ap.make_rule("merge", "github:acme/*", "do", "x")]
        self.assertEqual(ap.resolve(do, "merge", T, None)[0], "do")
        exact = do + [ap.make_rule("merge", "project:pond", "ask", "x"), ap.make_rule("merge", "github:acme/app", "tell", "x")]
        self.assertEqual(ap.resolve(exact, "merge", T, None)[0], "tell")
        # tell, blind: refused, saying the real reason.
        self.rules(("project:pond", "ask"), ("github:acme/*", "do"))
        with mock.patch.object(ap, "projects", lambda: None):
            r = self.api(["tell", "--kind", "merge", "--target", T, "--what", "merged it"])
        self.assertEqual(r["exit"], 1)
        self.assertIn("project list couldn't be read", r["stderr"])

    def test_tell_refuses_words_it_would_have_to_cut(self):
        self.rules(("github:acme/*", "tell"))
        self.assertEqual(self.api(["tell", "--kind", "merge", "--target", T, "--what", "x" * 301])["exit"], 1)

    def test_rules_show_in_words(self):
        self.rules(("github:onterris/*", "ask"), ("project:pond", "do"), ("github:acme/app", "tell"))
        page = ap.ui_page({"path": "", "caller": OWNER})
        rules = next(x for x in page["sections"] if x["id"] == "rules")["blocks"][0]["rows"]
        self.assertEqual([(r["label"], r["value"]) for r in rules[:-1]],
                         [("Everything in onterris", "Ask me"), ("acme/app", "Tell me after"), ("The Pond project", "Just do it")])
        self.assertEqual(rules[-1]["label"], "Add a rule")
        self.assertEqual(rules[0]["choice"]["fields"], {"scope": "github:onterris/*", "kind": "merge"})



class PlainAsks(unittest.TestCase):
    """The card says what it's asking in plain words: --ask, --why, --if-yes, --if-no (taskstore's lint)."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(ap, "STATE", Path(self.td.name))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.td.cleanup()

    def api(self, argv):
        return ap.api_answer({"caller": HOUSE, "path": "run", "body": {"argv": owned(argv)}})["body"]

    GOOD = ["--ask", "A change to Art's sync script landed. OK to start updating lane prompts on loom again?",
            "--why", "The script that copies lane prompts to loom changed, so it stopped until you look.",
            "--if-yes", "Lane prompt changes go live again.", "--if-no", "Lane prompts stay as they are until you look."]

    def ask(self, *extra, kind="github-write", target="acme/app:octocat"):
        return self.api(["request", "--kind", kind, "--target", target, *extra])

    def test_a_plain_ask_is_the_cards_headline_with_what_happens_either_way(self):
        r = self.ask(*self.GOOD, "--details", "It changed in PR 12 (commit a7b81358f7bd).\nNothing else moved.")
        self.assertEqual(r["exit"], 0, r)
        m = ap.ui_manifest()["asks"][0]
        self.assertEqual(m["text"], self.GOOD[1])
        self.assertEqual((m["why"], m["if_yes"], m["if_no"]), (self.GOOD[3], self.GOOD[5], self.GOOD[7]))
        self.assertEqual(m["meta"], self.GOOD[3])
        self.assertIn("a7b81358f7bd", m["details"], "details may carry ids: they're the sheet's, not the headline")
        self.assertIn("acme/app:octocat", m["fine"], "the target is the small print")
        self.assertNotIn("acme/app:octocat", m["text"])
        sheet = ap.ui_page({"path": "", "caller": OWNER})["sections"][0]["blocks"][0]["rows"][0]["sheet"]["blocks"]
        self.assertEqual(sheet[0]["text"], self.GOOD[1])
        self.assertEqual(sheet[2]["items"], [{"label": "If you allow", "value": self.GOOD[5]}, {"label": "If you deny", "value": self.GOOD[7]}])
        self.assertTrue(sheet[-1]["text"].startswith("Exactly what this allows: "))

    def test_a_request_names_its_conversation_and_the_manifest_carries_it(self):
        # loom docs/specs/comms.md, "Asks inline": a Comms thread shows the pending approvals asked from it.
        r = self.ask(*self.GOOD, "--conversation", "cv-0123456789ab")
        self.assertEqual(r["exit"], 0, r)
        self.assertEqual(ap.ui_manifest()["asks"][0]["conversation"], "cv-0123456789ab")
        bad = self.ask(*self.GOOD, "--conversation", "cv-nothex", target="acme/app:other")
        self.assertEqual(bad["exit"], 1)
        self.assertIn("--conversation is a conversation id", bad["stderr"])
        newline = self.ask(*self.GOOD, "--conversation", "cv-0123456789ab\n", target="acme/app:nl")
        self.assertEqual(newline["exit"], 1, "a trailing newline fails the check (fullmatch)")
        # The manifest says whose it is (the agent loom authenticated), so a thread can check the pair.
        self.assertEqual(ap.ui_manifest()["asks"][0]["agent"], HOUSE["agent"])
        plain = self.ask(*self.GOOD, target="acme/app:third")
        self.assertEqual(plain["exit"], 0, plain)
        self.assertNotIn("conversation", [a for a in ap.ui_manifest()["asks"] if "third" in a["fine"]][0])

    def test_a_continuation_repairs_missing_pending_provenance_without_duplicate_or_retarget(self):
        first = self.ask(*self.GOOD)
        aid = first["stdout"].split(":")[0]
        later = self.ask(*self.GOOD, "--conversation", "cv-0123456789ab")
        self.assertEqual(later["exit"], 0, later)
        self.assertEqual(later["stdout"].split(":")[0], aid)
        self.assertEqual(len(ap.recent()), 1)
        self.assertEqual(ap.ui_manifest()["asks"][0]["conversation"], "cv-0123456789ab")
        self.ask(*self.GOOD, "--conversation", "cv-00000000000f")
        self.assertEqual(ap.ui_manifest()["asks"][0]["conversation"], "cv-0123456789ab", "never move an existing thread's ask")

    def test_in_a_house_a_body_s_request_carries_its_conversation(self):
        seen = {}
        with mock.patch.object(ap, "in_a_house", return_value=True), \
             mock.patch.object(ap, "forward", side_effect=lambda piece, argv: seen.setdefault("argv", argv) and (_ for _ in ()).throw(SystemExit(0))), \
             mock.patch.dict(ap.os.environ, {"LOOM_BODY_CONVERSATION": "cv-00000000000f"}), \
             mock.patch.object(ap, "fetch_statement", return_value="stmt.mac"), \
             mock.patch.object(ap.sys, "argv", ["approvals", "request", "--kind", "relay", "--target", "mark", "--principal", "typed"]):
            with self.assertRaises(SystemExit):
                ap.main()
        # Its conversation, then the runtime's statement for it, last (a typed one gives way to it).
        self.assertEqual(seen["argv"][-4:], ["--conversation", "cv-00000000000f", "--principal", "stmt.mac"])
        self.assertNotIn("typed", seen["argv"])

    def test_local_body_request_carries_conversation_without_replacing_explicit_provenance(self):
        for explicit in ([], ["--conversation", "cv-00000000000e"]):
            with mock.patch.object(ap, "LOCAL_BODY", False), \
                 mock.patch.object(ap, "in_a_house", return_value=False), \
                 mock.patch.dict(ap.os.environ, {"LOOM_BODY_CONVERSATION": "cv-00000000000f"}), \
                 mock.patch.object(ap, "fetch_statement", return_value="stmt.mac"), \
                 mock.patch.object(ap, "run", return_value="") as run, \
                 mock.patch.object(ap.sys, "argv", ["approvals", "request", "--kind", "relay", "--target", "mark", *explicit]):
                ap.main()
            argv = run.call_args.args[0]
            self.assertEqual(argv.count("--conversation"), 1)
            self.assertEqual(argv[argv.index("--conversation") + 1], explicit[1] if explicit else "cv-00000000000f")
            self.assertEqual(argv[-2:], ["--principal", "stmt.mac"])

    def test_the_lint_refuses_ids_hashes_rounds_and_lane_words_saying_why(self):
        bad = {"t-40 needs the OAuth click": "a task id", "Merge acme/app#12?": "a pull request or issue id",
               "OK to push a7b81358f7bd?": "a hex id", "Park on the click, round 13?": "a round number",
               "OK to run the chain-check now?": "a lane or wake word", "lane: OK to push?": "a leading tag",
               "x" * 141: "over 140 characters", "Is S13 back?": "a raw site or unit code"}
        for text, what in bad.items():
            argv = list(self.GOOD)
            argv[1] = text
            r = self.ask(*argv)
            self.assertEqual(r["exit"], 1, text)
            self.assertIn(what, r["stderr"], text)
            self.assertIn("For example", r["stderr"])
        # why and the if-lines may name a pull request, never a hash.
        argv = list(self.GOOD)
        argv[3] = "It changed in acme/app#12."
        self.assertEqual(self.ask(*argv)["exit"], 0)
        argv[5] = "It pushes a7b81358f7bd."
        self.assertEqual(self.ask(*argv, target="acme/other:octocat")["exit"], 1)
        # An ask needs its why and both if-lines.
        self.assertEqual(self.ask("--ask", self.GOOD[1], "--why", "x")["exit"], 1)

    def test_140_is_the_limit_and_the_headline_is_whole(self):
        ask = ("OK to " + "move " * 40)[:139] + "?"
        self.assertEqual(len(ask), 140)
        argv = list(self.GOOD)
        argv[1] = ask
        self.assertEqual(self.ask(*argv)["exit"], 0)
        self.assertEqual(ap.ui_manifest()["asks"][0]["text"], ask, "whole, never cut")
        row = ap.ui_page({"path": "", "caller": OWNER})["sections"][0]["blocks"][0]["rows"][0]
        self.assertEqual(row["label"], ask)
        argv[1] = ask[:-1] + "x?"
        self.assertEqual(self.ask(*argv, target="acme/other:octocat")["exit"], 1, "141")

    def test_words_that_are_only_markup_are_no_words(self):
        argv = list(self.GOOD)
        argv[5] = "<>"
        r = self.ask(*argv)
        self.assertEqual(r["exit"], 1)
        self.assertEqual(ap.ui_manifest()["asks"], [])

    def test_a_long_hash_is_an_id_too(self):
        self.assertTrue(ap.plain_findings("ask", "OK to push " + "ab12" * 16 + "?"))

    def test_asking_again_plainly_upgrades_a_pending_card(self):
        self.ask("--what", "lane push held", "--why", "the gate changed")
        self.assertEqual(self.ask(*self.GOOD)["exit"], 0)
        m = ap.ui_manifest()["asks"]
        self.assertEqual(len(m), 1)
        self.assertEqual((m[0]["text"], m[0]["if_yes"]), (self.GOOD[1], self.GOOD[5]))

    def test_the_answered_row_leads_with_the_ask(self):
        aid = self.ask(*self.GOOD)["stdout"].split(":")[0]
        ap.ui_act({"action": "deny", "fields": {"id": aid}})
        row = next(x for x in ap.ui_page({"path": ""})["sections"] if x["id"] == "answered")["blocks"][0]["items"][0]
        self.assertEqual(row["text"], self.GOOD[1])

    def test_an_old_style_request_still_works_and_says_it_has_no_plain_question(self):
        r = self.ask("--what", "lane push held: the merge also changed the gate", "--why", "the gate changed")
        self.assertEqual(r["exit"], 0, r)
        m = ap.ui_manifest()["asks"][0]
        self.assertTrue(m["text"].startswith("(no plain question given) "), m["text"])
        self.assertNotIn("if_yes", m)

    def test_example_asks_pass_the_lint(self):
        # Examples of the shape callers send (each caller's own suite runs its real asks through this lint).
        for ask in ("A change to Art's sync script landed in pull request 61. OK to start updating lane prompts on loom again?",
                    "OK to run Art's reflect routine on loom's machine now?",
                    "OK for Art to push a fix to loom as Art's GitHub account?"):
            self.assertEqual(ap.plain_findings("ask", ask), [], ask)



class OnWhoseBehalf(unittest.TestCase):
    """t-1943, the owner's ruling (2026-11-07): his standing rules and his yes cover work done for him. A body acting
    for anyone else gets the grants he set for that person, or asks; the card names whom it's for; an approval asked
    for one person is spent only for them. Who that is, is the runtime's signed word (--principal), never the body's."""

    ON_POND = f"github:onterris/on-pond#220@{SHA}"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.object(ap, "STATE", Path(self.td.name)), mock.patch.object(ap, "projects", lambda: CARDS)]
        for p in self.patches:
            p.start()
        # The owner's own rule for all of onterris: just do it, for him.
        with ap.Store() as s:
            s.rules()
            s.replace_rules([ap.make_rule("merge", "github:onterris/*", "do", "test")])

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.td.cleanup()

    def api(self, argv, whose=None, caller=HOUSE):
        """A body's call over the API, with the runtime's statement for `whose` (owner, a slug, ?, a,b), or none."""
        return ap.api_answer({"caller": caller, "path": "run", "body": {"argv": argv + (["--principal", f"S:{caller['agent']}:{whose}"] if whose else [])}})["body"]

    def rule(self, whose, target=None):
        return self.api(["rule", "merge", target or self.ON_POND], whose)["stdout"].strip()

    def grant(self, person, scope, tier, caller=OWNER):
        return ap.ui_act({"action": "grant-add", "caller": caller, "fields": {"person": person, "scope": scope, "tier": tier}})

    def ask_merge(self, whose, target=None):
        r = self.api(["request", "--kind", "merge", "--target", target or self.ON_POND, "--ask", "OK to merge the alarm fix for the pond page?",
                      "--why", "Its checks are green.", "--if-yes", "I merge it.", "--if-no", "It waits."], whose)
        self.assertEqual(r["exit"], 0, r)
        return r["stdout"].split(":")[0]

    def test_the_owner_s_rules_are_for_his_own_work_only(self):
        self.assertEqual(self.rule("owner"), "do")
        self.assertEqual(self.rule("loren"), "ask", "his rule for onterris doesn't carry to work for Loren")
        self.assertEqual(self.rule(None), "ask", "no word from the runtime: someone loom can't name")
        self.assertEqual(self.rule("?"), "ask")
        self.assertEqual(self.rule("loren,dan"), "ask", "several people")
        # A statement for another agent says nothing about this one.
        self.assertEqual(self.api(["rule", "merge", self.ON_POND, "--principal", "S:bo:owner"])["stdout"], "ask\n")
        # A statement the runtime doesn't vouch for is no statement.
        self.assertEqual(self.api(["rule", "merge", self.ON_POND, "--principal", "forged"])["stdout"], "ask\n")
        r = self.api(["rule", "merge", self.ON_POND])
        self.assertIn("someone loom can't name", r["stderr"])
        self.assertEqual(json.loads(self.api(["rule", "merge", self.ON_POND, "--json"], "loren")["stdout"])[self.ON_POND]["for"], "loren")

    def test_a_grant_the_owner_set_for_a_person_answers_for_them_and_no_one_else(self):
        self.assertTrue(self.grant("loren", "github:onterris/on-pond", "do")["ok"])
        self.assertEqual(self.rule("loren"), "do")
        self.assertEqual(self.rule("dan"), "ask", "Loren's grant isn't Dan's")
        self.assertEqual(self.rule(None), "ask")
        self.assertEqual(self.rule("loren", f"github:onterris/other#1@{SHA}"), "ask", "only the scope granted")
        # Most specific wins, as for rules; a tie goes to the stricter.
        self.assertTrue(self.grant("loren", "github:onterris/*", "tell")["ok"])
        self.assertEqual(self.rule("loren"), "do")
        self.assertEqual(self.rule("loren", f"github:onterris/other#1@{SHA}"), "tell")
        self.assertTrue(self.grant("loren", "project:pond", "ask")["ok"])
        self.assertEqual(self.rule("loren", f"github:acme/pond#1@{SHA}"), "ask")
        # tier none removes it.
        self.assertTrue(ap.ui_act({"action": "grant", "caller": OWNER, "fields": {"person": "loren", "scope": "github:onterris/on-pond", "kind": "merge", "tier": "none"}})["ok"])
        self.assertEqual(self.rule("loren"), "tell")
        # The owner's own rules are untouched by any of it.
        self.assertEqual(self.rule("owner"), "do")

    def test_only_the_owner_sets_a_grant_and_only_on_the_page(self):
        member = {"kind": "owner", "who": "sam", "agents": ["ada"]}
        for c in (member, {"kind": "agent", "agent": "ada"}, None):
            self.assertFalse(self.grant("loren", "github:onterris/*", "do", caller=c)["ok"], c)
        self.assertEqual(self.rule("loren"), "ask")
        # Over the API there's no verb for it, whoever the body says it acts for.
        for argv in (["grant-add", "loren"], ["grants"], ["grant", "--kind", "merge", "--target", self.ON_POND, "--for", "ada", "--person", "loren"]):
            self.assertEqual(self.api(argv, "owner")["exit"], 1, argv)
        self.assertEqual(self.rule("loren"), "ask")
        self.assertFalse(self.grant("Not A Slug", "github:onterris/*", "do")["ok"])
        self.assertFalse(self.grant("loren", "nonsense", "do")["ok"])
        self.assertTrue(self.grant("loren", "github:onterris/*", "do")["ok"])
        # On the page, beside his own rules, in words; a member who owns some agents doesn't see either.
        rules = next(x for x in ap.ui_page({"path": "", "caller": OWNER})["sections"] if x["id"] == "rules")
        panels = {b["title"]: b for b in rules["blocks"]}
        self.assertEqual(list(panels), ["Merging, for you", "Merging, for other people", "Steps on loom's machine", "Standing jobs"])
        row = panels["Merging, for other people"]["rows"][0]
        self.assertEqual((row["label"], row["value"]), ("Loren: Everything in onterris", "Just do it"))
        self.assertEqual(row["choice"]["fields"], {"person": "loren", "scope": "github:onterris/*", "kind": "merge"})
        self.assertEqual(panels["Merging, for other people"]["rows"][-1]["edit"]["action"], "grant-add")
        self.assertNotIn("rules", [x["id"] for x in ap.ui_page({"path": "", "caller": member})["sections"]])

    def test_the_card_says_whom_it_is_for_in_the_piece_s_words(self):
        aid = self.ask_merge("loren")
        a = ap.get(aid)
        self.assertEqual((a["for"], ap.headline(a)), ("loren", "Merge on-pond#220 for Loren?"))
        ask = next(x for x in ap.ui_manifest()["asks"] if x["actions"][0]["fields"]["id"] == aid)
        self.assertEqual((ask["text"], ask["for"]), ("Merge on-pond#220 for Loren?", "loren"))
        self.assertIn("OK to merge the alarm fix for the pond page?", ask["details"], "the body's own question is still there")
        self.assertIn("It's for Loren, not for you: it's spent only for Loren.", ask["fine"])
        sheet = ap.ui_page({"path": "", "caller": OWNER})["sections"][0]["blocks"][0]["rows"][0]["sheet"]
        self.assertEqual([b["text"] for b in sheet["blocks"][:2]], ["Merge on-pond#220 for Loren?", "OK to merge the alarm fix for the pond page?"])
        # Any other kind leads with whom; someone loom can't name, and several people, say so.
        r = self.api(["request", "--kind", "github-write", "--target", "onterris/on-pond:jbarket", "--ask", "OK to push the alarm fix?",
                      "--why", "It's ready.", "--if-yes", "I push it.", "--if-no", "It waits."])
        self.assertEqual(ap.headline(ap.get(r["stdout"].split(":")[0])), "For someone loom can't name: OK to push the alarm fix?")
        several = self.ask_merge("loren,dan", f"github:onterris/on-pond#221@{SHA}")
        self.assertEqual(ap.headline(ap.get(several)), "Merge on-pond#221 for Loren and Dan?")
        # The owner's own: the body's ask, as before.
        self.assertEqual(ap.headline(ap.get(self.ask_merge("owner", f"github:acme/on-pond#222@{SHA}"))), "OK to merge the alarm fix for the pond page?")
        # The record says it too.
        rec = next(x for x in ap.ui_page({"path": "", "caller": OWNER})["sections"] if x["id"] == "record")
        self.assertIn(f"{aid}  pending  merge", rec["blocks"][0]["rows"][0]["sheet"]["blocks"][0]["text"])
        self.assertIn("for loren", rec["blocks"][0]["rows"][0]["sheet"]["blocks"][0]["text"])

    def test_asked_for_one_person_it_is_spent_only_for_them(self):
        aid = self.ask_merge("loren")
        self.assertEqual(self.ask_merge("loren"), aid, "asking again for the same person is the same card")
        mine = self.ask_merge("owner", f"github:acme/on-pond#220@{SHA}")
        self.assertNotEqual(mine, aid, "for the owner it's another card")
        ap.ui_act({"action": "allow", "fields": {"id": aid}})
        use = lambda whose: self.api(["use", aid, "--kind", "merge", "--target", self.ON_POND], whose)
        for whose in ("owner", "dan", None, "loren,dan"):
            r = use(whose)
            self.assertEqual(r["exit"], 1, whose)
            self.assertIn("was asked for Loren", r["stderr"])
        self.assertEqual(ap.get(aid)["state"], "allowed", "a refused spend leaves it allowed")
        self.assertEqual(use("loren")["exit"], 0)
        self.assertEqual(use("loren")["exit"], 1, "once")
        # check --json says whom it's for, so a continuation spends it as that.
        self.assertEqual(json.loads(self.api(["check", aid, "--json"], "loren")["stdout"])["for"], "loren")

    def test_a_merge_the_rules_answer_files_no_card(self):
        # t-2063: the owner's own work under `do` (or `tell`) merges; a card for it is refused, nothing filed.
        r = self.api(["request", "--kind", "merge", "--target", self.ON_POND, "--ask", "OK to merge the alarm fix?",
                      "--why", "Green.", "--if-yes", "I merge it.", "--if-no", "It waits."], "owner")
        self.assertEqual(r["exit"], 1, r)
        self.assertIn("the standing rule for this merge is do", r["stderr"])
        self.assertEqual([a for a in ap.ui_manifest()["asks"]], [], "nothing on his Inbox")
        with ap.Store() as s:
            s.replace_rules([ap.make_rule("merge", "github:onterris/*", "tell", "test")])
        r = self.api(["request", "--kind", "merge", "--target", self.ON_POND, "--ask", "OK to merge the alarm fix?",
                      "--why", "Green.", "--if-yes", "I merge it.", "--if-no", "It waits."], "owner")
        self.assertIn("is tell", r["stderr"])
        self.assertIn("approvals tell", r["stderr"])
        # Someone loom can't name, a person with no grant, and no rule at all: asked, as before.
        for whose, target in (("?", self.ON_POND), (None, self.ON_POND), ("loren", self.ON_POND), ("owner", f"github:else/app#1@{SHA}")):
            self.assertTrue(self.ask_merge(whose, target).startswith("a-"), whose)
        # A person's own grant of do answers for them too.
        self.assertTrue(self.grant("dan", "github:onterris/*", "do")["ok"])
        r = self.api(["request", "--kind", "merge", "--target", self.ON_POND, "--ask", "OK to merge the alarm fix?",
                      "--why", "Green.", "--if-yes", "I merge it.", "--if-no", "It waits."], "dan")
        self.assertIn("is do", r["stderr"])

    def test_a_body_takes_back_its_own_agent_s_pending_card(self):
        other = f"github:else/app#1@{SHA}"
        unknown = self.ask_merge("?", self.ON_POND)
        mine = self.ask_merge("owner", other)
        lorens = self.ask_merge("loren", self.ON_POND)
        w = lambda aid, whose, caller=HOUSE: self.api(["withdraw", aid], whose, caller)
        # Another agent's body: refused, in words that say nothing of the card.
        r = w(mine, "owner", {"kind": "agent", "agent": "bo", "house": "h-9"})
        self.assertEqual((r["exit"], r["stderr"]), (1, f"approvals: {mine} isn't one this body may take back\n"))
        # Someone loom can't name takes back neither the owner's card nor Loren's.
        self.assertEqual(w(mine, "?")["exit"], 1)
        self.assertEqual(w(lorens, None)["exit"], 1)
        # The owner's own work takes back its card and one asked for someone loom can't name, never Loren's.
        self.assertEqual(w(lorens, "owner")["stderr"], f"approvals: {lorens} isn't one this body may take back\n")
        self.assertEqual(w(unknown, "owner")["stdout"], f"{unknown}: withdrawn\n")
        self.assertEqual(w(mine, "owner")["stdout"], f"{mine}: withdrawn\n")
        self.assertEqual(w(lorens, "loren")["stdout"], f"{lorens}: withdrawn\n")
        self.assertEqual({ap.get(a)["state"] for a in (unknown, mine, lorens)}, {"withdrawn"})
        self.assertEqual([a for a in ap.ui_manifest()["asks"]], [], "off his Inbox")
        # An answer stands.
        again = self.ask_merge("owner", f"github:else/app#2@{SHA}")
        ap.ui_act({"action": "allow", "fields": {"id": again}})
        self.assertIn("allowed: an answer stands", w(again, "owner")["stderr"])
        # A chat house takes back only what its own service asked.
        chat = {"kind": "agent", "agent": "ada", "house": "h-8", "service": "discord-bodies"}
        c = self.ask_merge("owner", f"github:else/app#3@{SHA}")
        self.assertEqual(w(c, "owner", chat)["exit"], 1)
        # A body on loom's machine never says --as, not even empty (which would skip fetching whom it acts for).
        with mock.patch.dict(os.environ, {"LOOM_BODY_CONVERSATION": "cv-0123456789ab"}):
            for argv in (["withdraw", c, "--as", ""], ["withdraw", c, "--as="], ["withdraw", c, "--a", ""], ["withdraw", c, "--a="]):
                with self.assertRaises(PermissionError):
                    ap.run(argv)
        self.assertEqual(ap.get(c)["state"], "pending")
        # Not a piece's card (a vouch-wake, the clock's), from a body: the old test covers it; --as stays the piece's.
        self.assertEqual(self.api(["withdraw", c, "--as", json.dumps(HOUSE)], "owner")["exit"], 1)

    def test_the_owner_s_yes_for_himself_is_not_a_yes_for_someone_else(self):
        mine = self.ask_merge("owner", f"github:acme/app#5@{SHA}")
        ap.ui_act({"action": "allow", "fields": {"id": mine}})
        r = self.api(["request", "--kind", "merge", "--target", f"github:acme/app#5@{SHA}", "--ask", "OK to merge the fix?",
                      "--why", "Green.", "--if-yes", "I merge it.", "--if-no", "It waits."], "loren")
        self.assertIn("asked the owner", r["stdout"], "a new card for Loren, not his allowed one")
        self.assertEqual(self.api(["use", mine, "--kind", "merge", "--target", f"github:acme/app#5@{SHA}"], "loren")["exit"], 1)
        # The Changes piece's Yes is his own, unless it names whom it's for.
        t = f"github:acme/app#6@{SHA}"
        g = ap.run(["grant", "--kind", "merge", "--target", t, "--for", "ada", "--by", "jonathan-barket"]).split(":")[0]
        self.assertEqual(self.rule("loren", t), "ask")
        self.assertEqual(self.api(["use", g, "--kind", "merge", "--target", t], "loren")["exit"], 1)
        g2 = ap.run(["grant", "--kind", "merge", "--target", t, "--for", "ada", "--person", "loren"]).split(":")[0]
        self.assertEqual(self.rule("loren", t), f"ask allowed {g2}")
        self.assertEqual(self.api(["use", g2, "--kind", "merge", "--target", t], "loren")["exit"], 0)

    def test_the_changes_view_says_whom_else_a_merge_waits_on_and_its_yes_answers_that_card(self):
        # t-1956: the Changes piece reads rule merge --for <agent> --json as the owner's own work; a merge the agent
        # asked for Loren isn't, so each target also names who else it waits on, and grant --person answers that card.
        t = f"github:acme/app#9@{SHA}"
        lorens = self.ask_merge("loren", t)
        self.ask_merge("owner", f"github:acme/app#10@{SHA}")
        view = lambda: json.loads(ap.run(["rule", "merge", t, f"github:acme/app#10@{SHA}", "--for", "ada", "--json"])[0])
        view_of = lambda target, agent: json.loads(ap.run(["rule", "merge", target, "--for", agent, "--json"])[0])[target]
        got = view()
        self.assertEqual(got[t]["asked_for"], [{"for": "loren", "name": "Loren", "id": lorens, "state": "pending"}])
        self.assertNotIn("asked_for", got[f"github:acme/app#10@{SHA}"], "the owner's own card is the rule's, not someone else's")
        # Only on loom's machine, for the Changes piece: a body's own rule answer never lists other people's cards.
        self.assertNotIn("asked_for", self.api(["rule", "merge", t, "--json"], "owner")["stdout"])
        g = ap.run(["grant", "--kind", "merge", "--target", t, "--for", "ada", "--person", "loren"]).split(":")[0]
        self.assertEqual(g, lorens, "his yes for Loren answers her card")
        self.assertEqual(ap.get(lorens)["state"], "allowed")
        # Allowed and not yet spent: still listed, as allowed (the page says he said yes for her, never "ask" again).
        self.assertEqual(view()[t]["asked_for"], [{"for": "loren", "name": "Loren", "id": lorens, "state": "allowed"}])
        self.assertEqual(self.api(["use", lorens, "--kind", "merge", "--target", t], "loren")["exit"], 0)
        self.assertNotIn("asked_for", view()[t], "spent: nothing left for her")
        # Another agent's card, a withdrawn or denied one, or one for someone loom can't name: never listed.
        t2 = f"github:acme/app#11@{SHA}"
        dans = self.ask_merge("loren", t2)
        ap.ui_act({"action": "deny", "fields": {"id": dans}})
        self.assertNotIn("asked_for", view_of(t2, "ada"))
        self.ask_merge("loren", t2.replace("#11", "#12"))
        self.assertNotIn("asked_for", view_of(t2.replace("#11", "#12"), "bo"), "another agent's view")

    def test_the_owner_s_answer_for_himself_isn_t_the_answer_to_someone_else_s_request(self):
        t = f"github:acme/app#8@{SHA}"
        g = ap.run(["grant", "--kind", "merge", "--target", t, "--for", "ada", "--by", "jonathan-barket"]).split(":")[0]
        r = self.api(["request", "--kind", "merge", "--target", t, "--ask", "OK to merge the fix?", "--why", "Green.",
                      "--if-yes", "I merge it.", "--if-no", "It waits."], "loren")
        self.assertIn("asked the owner", r["stdout"], "Loren's request is a new card, not the owner's granted one")
        self.assertNotIn(g, r["stdout"])
        mine = self.api(["request", "--kind", "merge", "--target", t, "--what", "merge it", "--why", "green"], "owner")
        self.assertIn(f"{g}: already allowed", mine["stdout"])

    def test_a_deny_wins_whoever_it_was_for(self):
        mine = self.ask_merge("owner", f"github:acme/app#7@{SHA}")
        ap.ui_act({"action": "allow", "fields": {"id": mine}})
        theirs = self.ask_merge("loren", f"github:acme/app#7@{SHA}")
        ap.ui_act({"action": "deny", "fields": {"id": theirs}})
        self.assertEqual(ap.get(mine)["state"], "denied", "a no to this exact change stops every yes for it")

    def test_t_1929_still_holds_with_a_principal(self):
        aid = self.ask_merge("loren")
        ap.ui_act({"action": "allow", "fields": {"id": aid}})
        # Another agent never spends it, even for Loren.
        bo = {"kind": "agent", "agent": "bo", "house": "h-3"}
        self.assertEqual(self.api(["use", aid, "--kind", "merge", "--target", self.ON_POND], "loren", caller=bo)["exit"], 1)
        # A service's house spends only what its own service asked for.
        svc = {**HOUSE, "house": "h-8", "service": "discord-bodies"}
        self.assertEqual(self.api(["use", aid, "--kind", "merge", "--target", self.ON_POND], "loren", caller=svc)["exit"], 1)
        # The exact target only.
        self.assertEqual(self.api(["use", aid, "--kind", "merge", "--target", f"github:onterris/on-pond#220@{SHA2}"], "loren")["exit"], 1)
        # Expiry.
        with ap.Store() as s:
            s.d["approvals"][aid]["expires"] = "2000-01-01T00:00:00Z"
            s.save()
        self.assertIn("expired", self.api(["use", aid, "--kind", "merge", "--target", self.ON_POND], "loren")["stderr"])

    def test_always_on_a_card_for_someone_else_is_a_grant_for_them(self):
        aid = self.ask_merge("loren")
        a = ap.get(aid)
        self.assertEqual(ap.always_scope(a, CARDS), ("github:onterris/on-pond", "Always for Loren on onterris/on-pond"))
        r = ap.ui_act({"action": "always", "caller": OWNER, "fields": {"id": aid}})
        self.assertTrue(r["ok"], r)
        self.assertIn("for Loren", r["message"])
        self.assertEqual(self.rule("loren", f"github:onterris/on-pond#300@{SHA}"), "do")
        self.assertEqual(self.rule("dan", f"github:onterris/on-pond#300@{SHA}"), "ask")
        with ap.Store() as s:
            self.assertEqual([r["scope"] for r in s.rules()], ["github:onterris/*"], "his own rules didn't change")
        # Nobody to grant it to: no Always.
        unk = self.ask_merge(None, f"github:onterris/on-pond#301@{SHA}")
        self.assertIsNone(ap.always_scope(ap.get(unk), CARDS))
        self.assertFalse(ap.ui_act({"action": "always", "caller": OWNER, "fields": {"id": unk}})["ok"])

    def test_tell_is_for_a_do_or_tell_that_applies_to_whom_it_was_for(self):
        tell = lambda whose: self.api(["tell", "--kind", "merge", "--target", self.ON_POND, "--what", "merged the alarm fix"], whose)
        self.assertEqual(tell("loren")["exit"], 1)
        self.assertEqual(tell("owner")["exit"], 0)
        self.grant("loren", "github:onterris/on-pond", "tell")
        self.assertEqual(tell("loren")["exit"], 0)
        self.assertEqual(ap.notices()[0].get("for"), "loren")

    def test_on_loom_s_machine_it_is_the_owner_s_unless_set_otherwise_or_a_body_in_a_conversation(self):
        self.assertEqual(ap.run(["rule", "merge", self.ON_POND]), ("do\n", ""))
        with mock.patch.dict(os.environ, {"APPROVALS_LOCAL_PRINCIPAL": "unknown"}):
            self.assertEqual(ap.run(["rule", "merge", self.ON_POND])[0], "ask\n")
        with mock.patch.object(ap, "LOCAL_BODY", True):
            self.assertEqual(ap.run(["rule", "merge", self.ON_POND])[0], "ask\n", "a local body with no statement")
            self.assertEqual(ap.run(["rule", "merge", self.ON_POND, "--principal", f"S:{ap.LOCAL_AGENT}:owner"])[0], "do\n")
        # --on-behalf is only a piece's, acting for a body (--as).
        with self.assertRaises(PermissionError):
            ap.run(["request", "--kind", "relay", "--target", "a->b", "--what", "w", "--why", "y", "--on-behalf", "owner"])

    def test_a_grants_list_that_can_t_be_read_answers_ask_and_is_never_overwritten(self):
        self.grant("loren", "github:onterris/*", "do")
        path = Path(self.td.name) / "rules.json"
        doc = json.loads(path.read_text())
        doc["grants"].append({"person": "dan", "kind": "merge", "scope": "github:onterris/*", "tier": "bogus"})
        path.write_text(json.dumps(doc))
        self.assertEqual(self.rule("dan"), "ask")
        self.assertFalse(self.grant("dan", "github:onterris/*", "do")["ok"], "refused, not overwritten")
        self.assertIn("bogus", path.read_text())

    def test_a_card_for_several_people_is_theirs_and_no_one_else_s(self):
        aid = self.ask_merge("loren,dan")
        self.assertEqual(self.ask_merge("dan,loren"), aid, "the same people, in any order: the same card")
        self.assertNotEqual(self.ask_merge("eve,mallory"), aid)
        self.assertNotEqual(self.ask_merge(None), aid, "nobody named isn't Loren and Dan")
        ap.ui_act({"action": "allow", "fields": {"id": aid}})
        use = lambda whose: self.api(["use", aid, "--kind", "merge", "--target", self.ON_POND], whose)["exit"]
        self.assertEqual([use("eve,mallory"), use(None), use("loren")], [1, 1, 1])
        self.assertEqual(use("dan,loren"), 0)
        many = ap.get(self.ask_merge("a,b,c,d,e", f"github:onterris/on-pond#230@{SHA}"))
        self.assertEqual(ap.headline(many), "Merge on-pond#230 for A, B and 3 others?")

    def test_a_person_named_owner_is_not_the_owner(self):
        self.assertEqual(self.rule("@owner"), "ask")
        self.assertEqual(ap.principal_of(HOUSE_WHO, "S:ada:@owner")["for"], ap.UNKNOWN)
        self.assertFalse(self.grant("owner", "github:onterris/*", "do")["ok"])
        with self.assertRaises(ValueError):
            ap.grant("merge", self.ON_POND, "ada", None, person="owner-x Y")

    def test_editing_a_rule_keeps_every_grant_and_editing_a_grant_keeps_every_rule(self):
        self.grant("loren", "github:onterris/*", "do")
        self.assertTrue(ap.ui_act({"action": "rule-add", "caller": OWNER, "fields": {"scope": "github:acme/*", "tier": "tell"}})["ok"])
        self.assertEqual(self.rule("loren"), "do", "the grant survived a rule edit")
        with ap.Store() as s:     # a fresh store, the rule edit straight away (nothing read before it)
            s.replace_rules([ap.make_rule("merge", "github:onterris/*", "do", "t")])
        self.assertEqual(self.rule("loren"), "do")
        self.grant("dan", "github:onterris/*", "tell")
        self.assertEqual(self.rule("owner"), "do", "the rules survived a grant edit")

    def test_only_a_grant_is_spent_by_a_piece_without_saying_whom(self):
        aid = self.ask_merge("loren")
        ap.ui_act({"action": "allow", "fields": {"id": aid}})
        with self.assertRaises(PermissionError):
            ap.run(["use", aid, "--kind", "merge", "--target", self.ON_POND, "--as", json.dumps(HOUSE)])
        self.assertEqual(ap.run(["use", aid, "--kind", "merge", "--target", self.ON_POND, "--as", json.dumps(HOUSE), "--on-behalf", "loren"]), f"{aid}: used\n")
        # And a piece filing without saying whom is someone loom can't name, never the owner.
        out = ap.run(["request", "--kind", "local-wake", "--target", "ada:abc", "--what", "w", "--why", "y", "--as", json.dumps(HOUSE)])
        self.assertEqual(ap.get(out.split(":")[0])["for"], ap.UNKNOWN)

    def test_a_body_the_runtime_runs_on_loom_s_machine_is_its_conversation_s(self):
        def main(*argv, statement=None, env=None):
            out = io.StringIO()
            with mock.patch.object(ap, "in_a_house", return_value=False), mock.patch.object(ap, "LOCAL_BODY", False), \
                 mock.patch.object(ap, "fetch_statement", return_value=statement), \
                 mock.patch.dict(ap.os.environ, {"LOOM_BODY_CONVERSATION": "", **(env or {})}), mock.patch.object(ap.sys, "argv", ["approvals", *argv]), \
                 contextlib.redirect_stdout(out):
                ap.main()
            return out.getvalue()
        agent = ap.LOCAL_AGENT
        conv = {"LOOM_BODY_CONVERSATION": "cv-0123456789ab"}
        self.assertEqual(main("rule", "merge", self.ON_POND), "do\n", "loom's machine itself: the owner's")
        self.assertEqual(main("rule", "merge", self.ON_POND, env=conv), "ask\n", "a local body the runtime can't vouch for")
        self.assertEqual(main("rule", "merge", self.ON_POND, env=conv, statement=f"S:{agent}:loren"), "ask\n")
        self.assertEqual(main("rule", "merge", self.ON_POND, env=conv, statement=f"S:{agent}:owner"), "do\n")
        # --for (the Changes piece's view) is a call here like any other: a local body isn't the owner by saying it.
        self.assertEqual(main("rule", "merge", self.ON_POND, "--for", agent, env=conv), "ask\n")
        # Nor by a statement it types itself.
        self.assertEqual(main("rule", "merge", self.ON_POND, "--principal", f"S:{agent}:owner", env=conv), "ask\n")
        # Nor by an empty --as, which once skipped fetching whom it acts for and read as loom's machine's own call.
        out = main("request", "--kind", "github-write", "--target", "x/y", "--what", "w", "--why", "y", "--as", "", env=conv)
        self.assertEqual(ap.get(out.split(":")[0])["for"], ap.UNKNOWN)
        with mock.patch.dict(os.environ, {"APPROVALS_LOCAL_PRINCIPAL": "unknown"}):
            self.assertEqual(ap.run(["rule", "merge", self.ON_POND, "--for", "ada"])[0], "ask\n")
        self.assertEqual(ap.run(["rule", "merge", self.ON_POND, "--for", "ada"])[0], "do\n")

    def test_on_behalf_reads_owner_a_person_or_else_unknown(self):
        cases = {"owner": ap.OWNER, "loren": "loren", "-": ap.UNKNOWN, "Loren!": ap.UNKNOWN, "?": ap.UNKNOWN, "owner ": ap.UNKNOWN}
        for said, want in cases.items():
            self.assertEqual(ap.principal_for_call(HOUSE, HOUSE_WHO, None, acting_for=True, on_behalf=said)["for"], want, said)
        # And a wake the clock filed for the owner is spent as the owner's, for nobody else.
        out = ap.run(["request", "--kind", "local-wake", "--target", "ada:abc", "--what", "w", "--why", "y", "--as", json.dumps(HOUSE), "--on-behalf", "owner"])
        aid = out.split(":")[0]
        self.assertEqual(ap.get(aid)["for"], ap.OWNER)
        ap.ui_act({"action": "allow", "fields": {"id": aid}})
        for said in ("-", "loren"):
            with self.assertRaises(PermissionError):
                ap.run(["use", aid, "--kind", "local-wake", "--target", "ada:abc", "--as", json.dumps(HOUSE), "--on-behalf", said])
        self.assertEqual(ap.run(["use", aid, "--kind", "local-wake", "--target", "ada:abc", "--as", json.dumps(HOUSE), "--on-behalf", "owner"]), f"{aid}: used\n")

    def test_grant_person_owner_is_refused_and_no_person_is_the_owner_s(self):
        with self.assertRaises(ValueError):
            ap.run(["grant", "--kind", "merge", "--target", self.ON_POND, "--for", "ada", "--person", "owner"])
        g = ap.run(["grant", "--kind", "merge", "--target", self.ON_POND, "--for", "ada"]).split(":")[0]
        self.assertEqual(ap.get(g)["for"], ap.OWNER)

    def test_a_body_can_t_say_on_whose_behalf_over_the_api(self):
        r = self.api(["request", "--kind", "merge", "--target", self.ON_POND, "--what", "w", "--why", "y", "--on-behalf", "owner"], "loren")
        self.assertEqual(r["exit"], 1)
        self.assertIn("--on-behalf is for a piece", r["stderr"])
        r = self.api(["request", "--kind", "merge", "--target", self.ON_POND, "--what", "w", "--why", "y", "--on-behalf", "owner",
                      "--as", json.dumps(HOUSE)], "loren")
        self.assertEqual(r["exit"], 1, "nor with --as")

    def test_the_fyi_says_whom_and_a_crowded_card_names_everyone(self):
        self.grant("loren", "github:onterris/on-pond", "tell")
        self.api(["tell", "--kind", "merge", "--target", self.ON_POND, "--what", "merged the alarm fix"], "loren")
        self.assertEqual(ap.ui_manifest()["notices"][0]["text"], "Ada merged onterris/on-pond#220 for Loren")
        many = ap.get(self.ask_merge("a,b,c,d,e", f"github:onterris/on-pond#231@{SHA}"))
        self.assertIn("(A, B, C, D, E)", ap.fine_print(many))
        with self.assertRaises(ValueError):
            ap.grant("merge", self.ON_POND, "ada", None, person=" Owner ")

    def test_the_statement_is_taken_off_the_argv_and_never_kept(self):
        self.assertEqual(ap.take_principal(["rule", "--principal", "a", "merge", "--principal", "b"]), (["rule", "merge"], "b"))
        self.assertEqual(ap.take_principal(["rule", "--principal", "a", "--principal=c"]), (["rule"], "c"))
        self.assertEqual(ap.take_principal(["rule", "--principal", "FORGED", "--principal", ""]), (["rule"], None), "the last, even empty")
        aid = self.ask_merge("loren")
        for f in Path(self.td.name).iterdir():
            self.assertNotIn("S:ada:loren", f.read_text(), f.name)
        self.assertEqual(ap.get(aid)["for"], "loren")


class TheRuntime(unittest.TestCase):
    """verify_statement and fetch_statement against a stand-in runtime command (APPROVALS_PRINCIPAL): what it's handed,
    and that anything but a clean yes is no statement."""

    FAKE = """#!/usr/bin/env python3
import json, os, sys
stdin = sys.stdin.read() if "--verify" in sys.argv else ""
with open(os.environ["RT_RECORD"], "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "stdin": stdin}) + "\\n")
sys.stdout.write(os.environ.get("RT_ANSWER", ""))
sys.exit(int(os.environ.get("RT_EXIT", "0")))
"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        d = Path(self.td.name)
        (d / "rt").write_text(self.FAKE)
        (d / "rt").chmod(0o755)
        self.record = d / "record.jsonl"
        self.env = mock.patch.dict(os.environ, {"APPROVALS_PRINCIPAL": str(d / "rt"), "RT_RECORD": str(self.record)})
        self.env.start()
        # The real one, not the tests' stand-in.
        self.real = mock.patch.object(ap, "verify_statement", REAL_VERIFY)
        self.real.start()

    def tearDown(self):
        self.real.stop()
        self.env.stop()
        self.td.cleanup()

    def calls(self):
        return [json.loads(l) for l in self.record.read_text().splitlines()] if self.record.exists() else []

    def answer(self, ans, code=0):
        return mock.patch.dict(os.environ, {"RT_ANSWER": json.dumps(ans) if not isinstance(ans, str) else ans, "RT_EXIT": str(code)})

    def test_verify_hands_the_statement_on_stdin_and_takes_only_a_clean_yes(self):
        good = {"ok": True, "agent": "ada", "conversation": "cv-0123456789ab", "for": "loren", "owner": False, "people": ["loren"]}
        with self.answer(good):
            self.assertEqual(ap.verify_statement("the.statement"), good)
        self.assertEqual(self.calls()[-1], {"argv": ["--verify"], "stdin": "the.statement"})
        with self.answer(good, 1):
            self.assertIsNone(ap.verify_statement("x"), "a non-zero exit is no, whatever it printed")
        with self.answer({**good, "ok": False}):
            self.assertIsNone(ap.verify_statement("x"))
        with self.answer({**good, "ok": "yes"}):
            self.assertIsNone(ap.verify_statement("x"), "ok is true, not truthy")
        with self.answer("not json"):
            self.assertIsNone(ap.verify_statement("x"))
        with mock.patch.dict(os.environ, {"APPROVALS_PRINCIPAL": "/nonexistent/rt"}):
            self.assertIsNone(ap.verify_statement("x"), "a command that won't run")
        with mock.patch.object(ap.subprocess, "run", side_effect=ap.subprocess.TimeoutExpired("rt", 10)):
            self.assertIsNone(ap.verify_statement("x"))
        # And end to end: the owner's statement for this agent reads his rules; for another agent it doesn't.
        with self.answer({**good, "for": "jonathan", "owner": True}):
            self.assertEqual(ap.principal_of({"agent": "ada"}, "s")["for"], ap.OWNER)
            self.assertEqual(ap.principal_of({"agent": "bo"}, "s")["for"], ap.UNKNOWN)

    def test_a_body_fetches_its_own_statement_from_the_runtime(self):
        # On loom's machine (a local body): the setting's command.
        here = mock.patch.object(ap, "in_a_house", return_value=False)
        here.start()
        self.addCleanup(here.stop)
        with self.answer({"ok": True, "statement": "eyJ2IjoxfQ.c2ln"}):
            self.assertEqual(ap.fetch_statement("cv-0123456789ab"), "eyJ2IjoxfQ.c2ln")
        self.assertEqual(self.calls()[-1]["argv"], ["--conversation", "cv-0123456789ab"])
        for bad in ({"ok": False, "statement": "a.b"}, {"ok": True, "statement": ""}, {"ok": True, "statement": "a b"}, "nope"):
            with self.answer(bad):
                self.assertIsNone(ap.fetch_statement("cv-0123456789ab"), bad)
        # In a house: the house's own loom-runtime, never the machine's setting.
        seen = []
        here.stop()
        with mock.patch.object(ap, "in_a_house", return_value=True), \
             mock.patch.object(ap.subprocess, "run", side_effect=lambda argv, **k: seen.append(argv) or ap.subprocess.CompletedProcess(argv, 1, "", "")):
            ap.fetch_statement("cv-0123456789ab")
        self.assertEqual(seen[0][:2], ["loom-runtime", "principal"])


class LoomPageRules(unittest.TestCase):
    """What loom refuses a whole page for: here, a details block inside a sheet (extensions.md, "Panels")."""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(ap, "STATE", Path(self.td.name))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.td.cleanup()

    @staticmethod
    def sheets(x):
        if isinstance(x, dict):
            if "sheet" in x:
                yield x["sheet"]
            for v in x.values():
                yield from LoomPageRules.sheets(v)
        elif isinstance(x, list):
            for v in x:
                yield from LoomPageRules.sheets(v)

    def test_no_sheet_holds_a_details_block_in_any_state(self):
        api = lambda argv: ap.api_answer({"caller": HOUSE, "path": "run", "body": {"argv": owned(argv)}})["body"]
        plain = ["--ask", "OK to push the fix to the app now?", "--why", "It's ready.", "--if-yes", "It goes out.", "--if-no", "It waits."]
        ids = [api(["request", "--kind", "github-write", "--target", f"acme/r{n}:octocat", *(plain if n % 2 else []),
                    "--what", "push it", "--why", "ready"])["stdout"].split(":")[0] for n in range(4)]
        ap.ui_act({"action": "allow", "fields": {"id": ids[0]}})
        ap.ui_act({"action": "deny", "fields": {"id": ids[1]}})
        api(["request", "--kind", "merge", "--target", f"github:acme/app#1@{'a' * 40}", *plain])
        for caller in (OWNER, {"kind": "owner", "who": "sam", "agents": ["ada"]}, None):
            page = ap.ui_page({"path": "", **({"caller": caller} if caller else {})})
            found = list(self.sheets(page))
            self.assertTrue(found)
            for sh in found:
                self.assertNotIn('"type": "details"', json.dumps(sh), caller)

    def test_the_small_print_says_kind_target_where_and_how_long(self):
        r = ap.api_answer({"caller": HOUSE, "path": "run", "body": {"argv": owned(["request", "--kind", "github-write", "--target", "acme/app:octocat",
              "--ask", "OK to push the fix to the app now?", "--why", "It's ready.", "--if-yes", "It goes out.", "--if-no", "It waits.", "--minutes", "30"])}})
        self.assertEqual(r["body"]["exit"], 0)
        line = ap.ui_page({"path": "", "caller": OWNER})["sections"][0]["blocks"][0]["rows"][0]["sheet"]["blocks"][-1]["text"]
        self.assertEqual(line, "Exactly what this allows: A GitHub write: acme/app:octocat, from Ada's house, for any of Ada's own bodies. "
                               "Allowing it lets that one action through once, within 30 min.")


class Projects(unittest.TestCase):
    """projects(): the cards a project rule reads, from the command, and None when they can't be read."""

    def run_with(self, cmd):
        with mock.patch.dict(os.environ, {"APPROVALS_PROJECTS": cmd}):
            return ap.projects()

    def test_reads_cards_and_normalizes_bare_repos(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        f = Path(td.name) / "cards.json"
        f.write_text(json.dumps([{"slug": "pond", "name": "Pond", "links": {"repos": ["Acme/Pond", "forgejo:codeberg.org/a/b"]}}]))
        self.assertEqual(self.run_with(f"cat {f}"), [{"slug": "pond", "name": "Pond", "repos": ["github:acme/pond", "forgejo:codeberg.org/a/b"]}])

    def test_none_when_it_fails_or_isnt_a_list(self):
        self.assertIsNone(self.run_with("false"))
        self.assertIsNone(self.run_with("echo nope"))
        self.assertIsNone(self.run_with("echo {}"))
        self.assertIsNone(self.run_with("/no/such/command"))


OWNER_CALLER = {"kind": "owner", "who": "jonathan-barket", "agents": "*"}
MEMBER_CALLER = {"kind": "owner", "who": "loren", "agents": ["ada"]}


class Grants(unittest.TestCase):
    """Access to a piece (t-1970): a body asks, only the household owner answers, and only the runtime spends it."""
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(ap, "STATE", Path(self.td.name))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.td.cleanup()

    def ask(self, target="tasks:mark", ask="Give Mark access to Tasks so he can file his own work?"):
        r = ap.api_answer({"caller": HOUSE, "path": "run", "body": {"argv": owned([
            "request", "--kind", "grant", "--target", target, "--ask", ask, "--why", "Mark asked to keep his own work list.",
            "--if-yes", "Mark can use Tasks from now on.", "--if-no", "Mark keeps asking me to file them."])}})
        self.assertEqual(r["body"]["exit"], 0, r)
        return r["body"]["stdout"].split(":")[0]

    def test_a_grant_target_is_piece_agent_and_optional_paths(self):
        self.assertEqual(ap.grant_of("tasks:mark"), {"piece": "tasks", "agent": "mark", "paths": ["*"]})
        self.assertEqual(ap.grant_of("tasks:mark:run,people/*"), {"piece": "tasks", "agent": "mark", "paths": ["run", "people/*"]})
        for bad in ("tasks", "Tasks:mark", "tasks:mark:", "tasks:mark:../x", "tasks:mark:a b", "tasks:*", "*:mark", "tasks:mark:a/../b"):
            self.assertIsNone(ap.grant_of(bad), bad)
        r = ap.api_answer({"caller": HOUSE, "path": "run", "body": {"argv": owned(["request", "--kind", "grant", "--target", "tasks",
                           "--what", "w", "--why", "y"])}})
        self.assertEqual(r["body"]["exit"], 1)
        self.assertIn("grant target", r["body"]["stderr"])

    def test_the_card_says_what_allow_gives_in_the_piece_s_words(self):
        aid = self.ask(ask="Can I have a cookie?")
        card = [a for a in ap.ui_manifest()["asks"] if a["actions"][0]["fields"]["id"] == aid][0]
        # The headline is the target's, whatever the body wrote: its own words are the detail under it.
        self.assertEqual(card["text"], "Give Mark access to Tasks?")
        self.assertIn("Can I have a cookie?", card["details"])
        self.assertEqual(card["actions"][0]["label"], "Allow")
        self.assertIs(card["lead"], True)
        self.assertIn("it stays until you change it on Permissions", card["fine"])
        aid2 = self.ask(target="tasks:mark:run", ask="Give Mark access to filing tasks?")
        self.assertEqual(ap.headline(ap.get(aid2)), "Give Mark access to Tasks, for run?")
        # Beside Allow, the piece's words, never the body's.
        self.assertEqual(card["if_yes"], "Mark can use Tasks (all of it) from now on, added to whatever else it has.")
        self.assertIn("nothing is taken away", card["fine"])
        self.assertEqual(card["if_no"], "Nothing changes: Mark can't use any more of Tasks than it can now.")

    def test_only_the_household_owner_answers_a_grant(self):
        aid = self.ask()
        for caller in (MEMBER_CALLER, None, {"kind": "agent", "agent": "ada"}):
            r = ap.ui_act({"action": "allow", "fields": {"id": aid}, **({"caller": caller} if caller else {})})
            self.assertFalse(r["ok"], caller)
            self.assertIn("household owner", r["message"])
            self.assertFalse(ap.ui_act({"action": "deny", "fields": {"id": aid}, **({"caller": caller} if caller else {})})["ok"])
        self.assertEqual(ap.get(aid)["state"], "pending")
        r = ap.ui_act({"action": "allow", "fields": {"id": aid}, "caller": OWNER_CALLER})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["message"], "Allowed: Give Mark access to Tasks.")
        self.assertEqual(ap.get(aid)["by"], "jonathan-barket")

    def test_a_deny_is_recorded_and_nothing_can_spend_it(self):
        aid = self.ask()
        self.assertTrue(ap.ui_act({"action": "deny", "fields": {"id": aid}, "caller": OWNER_CALLER})["ok"])
        self.assertEqual(ap.get(aid)["state"], "denied")
        with self.assertRaises(PermissionError):
            ap.run(["use", aid, "--kind", "grant", "--target", "tasks:mark", "--as", json.dumps(HOUSE)])

    def test_only_the_runtime_spends_it_never_the_body(self):
        aid = self.ask()
        ap.ui_act({"action": "allow", "fields": {"id": aid}, "caller": OWNER_CALLER})
        r = ap.api_answer({"caller": HOUSE, "path": "run", "body": {"argv": owned(["use", aid, "--kind", "grant", "--target", "tasks:mark"])}})
        self.assertEqual(r["body"]["exit"], 1)
        self.assertIn("not by a body", r["body"]["stderr"])
        self.assertEqual(ap.get(aid)["state"], "allowed")
        # The runtime, on loom's machine, for the agent that asked: for exactly that target, once, whomever it was for.
        with self.assertRaises(PermissionError):
            ap.run(["use", aid, "--kind", "grant", "--target", "secrets:mark", "--as", json.dumps(HOUSE)])
        self.assertEqual(ap.run(["use", aid, "--kind", "grant", "--target", "tasks:mark", "--as", json.dumps(HOUSE)]), f"{aid}: used\n")
        with self.assertRaises(PermissionError):
            ap.run(["use", aid, "--kind", "grant", "--target", "tasks:mark", "--as", json.dumps(HOUSE)])

    def test_asking_again_for_one_allowed_but_not_applied_never_says_use_it(self):
        aid = self.ask()
        ap.ui_act({"action": "allow", "fields": {"id": aid}, "caller": OWNER_CALLER})
        r = ap.api_answer({"caller": HOUSE, "path": "run", "body": {"argv": owned([
            "request", "--kind", "grant", "--target", "tasks:mark", "--ask", "Give Mark access to Tasks?", "--why", "w",
            "--if-yes", "y", "--if-no", "n"])}})
        self.assertIn("wasn't applied; ask the owner to give it on Permissions", r["body"]["stdout"])
        self.assertNotIn("approvals use", r["body"]["stdout"])

    def test_the_longest_grant_target_still_fits_loom_s_caps(self):
        # loom refuses a card's fine print past 400 and its text past 2000, and with them every card of the piece.
        paths = ",".join(f"p{i:02d}/abcdefghijklm" for i in range(10))
        target = f"{'t' * 40}:{'m' * 40}:{paths}"[:200]
        target = target.rstrip(",/")
        self.assertIsNotNone(ap.grant_of(target), target)
        aid = self.ask(target=target, ask="Give the agent access to that piece?")
        card = [a for a in ap.ui_manifest()["asks"] if a["actions"][0]["fields"]["id"] == aid][0]
        self.assertLessEqual(len(card["fine"]), 400)
        for k in ("text", "why", "if_yes", "if_no"):
            self.assertLessEqual(len(card[k]), 300 if k != "text" else 2000, k)

    def test_the_page_s_sheet_says_the_piece_s_words_beside_allow_too(self):
        aid = self.ask()
        page = ap.ui_page({"path": "", "caller": OWNER_CALLER})
        sheet = json.dumps(page)
        self.assertIn("Mark can use Tasks (all of it) from now on", sheet)
        self.assertNotIn("Mark keeps asking me to file them.", sheet.split("Exactly what")[0].split("If you deny")[1][:200])

    def test_the_runtime_s_exact_spend_works(self):
        # loom-box's spendGrant: use ID --kind grant --target T --as {"kind": "agent", "agent": <who asked>}, nothing else.
        aid = self.ask()
        ap.ui_act({"action": "allow", "fields": {"id": aid}, "caller": OWNER_CALLER})
        self.assertEqual(ap.run(["use", aid, "--kind", "grant", "--target", "tasks:mark", "--as", json.dumps({"kind": "agent", "agent": "ada"})]), f"{aid}: used\n")

    def test_state_carries_a_grant_s_target_for_core(self):
        aid = self.ask()
        r = ap.api_answer({"caller": {"kind": "owner"}, "path": "state", "body": {"ids": [aid]}})
        self.assertEqual(r["body"]["approvals"][aid], {"state": "pending", "agent": "ada", "kind": "grant", "target": "tasks:mark"})


class OwnerApplied(unittest.TestCase):
    """Owner-only actions asked on loom's page (t-1975): the clock's vouch-wake and the runtime's principal-reset. Filed
    by the piece (never a body), answered only by the household owner, spent only by the piece, for exactly its target."""
    AS = json.dumps({"kind": "agent", "agent": "art"})

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(ap, "STATE", Path(self.td.name))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.td.cleanup()

    def file(self, kind="vouch-wake", target="art:w-0f0cfb,w-3c68fa,w-55e1c0",
             details="- w-0f0cfb, fires 10:00: t-40 confirm first backup run\n- w-3c68fa, fires 10:00\n- w-55e1c0, fires 10:00"):
        if kind == "principal-reset" and details.startswith("- w-"):
            details = f"The conversation: Slack, #general ({target.split(':')[1]})"
        out = ap.run(["request", "--kind", kind, "--target", target, "--ask", "Count these scheduled jobs as your work?",
                      "--why", "They were set before loom could say whom each was for.", "--if-yes", "y", "--if-no", "n",
                      "--details", details, "--as", self.AS, "--on-behalf", "owner"])
        return out.split(":")[0]

    def card(self, aid):
        return [a for a in ap.ui_manifest()["asks"] if a["actions"][0]["fields"]["id"] == aid][0]

    def test_targets_are_exact_sorted_and_bounded(self):
        self.assertEqual(ap.vouch_of("art:w-0f0cfb,w-3c68fa"), {"agent": "art", "ids": ["w-0f0cfb", "w-3c68fa"]})
        self.assertEqual(ap.reset_of("art:cv-0123456789ab"), {"agent": "art", "conversation": "cv-0123456789ab"})
        twelve = ",".join(f"w-{i:06x}" for i in range(12))
        self.assertIsNotNone(ap.vouch_of(f"art:{twelve}"))
        for bad in ("art:w-3c68fa,w-0f0cfb", "art:w-0f0cfb,w-0f0cfb", "art:", "art:w-0f0cfb,", "art:w-0f0cfb,*", "art:*",
                    f"art:{twelve},w-ffffff", "Art:w-0f0cfb", "art:w-0f0cf"):
            self.assertIsNone(ap.vouch_of(bad), bad)
        for bad in ("art:cv-0123", "art:cv-0123456789ab,cv-0123456789ac", "art", "art:w-0f0cfb"):
            self.assertIsNone(ap.reset_of(bad), bad)
        with self.assertRaises(ValueError):
            self.file(target="art:w-3c68fa,w-0f0cfb")
        with self.assertRaises(ValueError):
            self.file(kind="principal-reset", target="art:w-0f0cfb")

    def test_only_a_piece_files_them_and_only_for_its_own_agent(self):
        for kind, target in (("vouch-wake", "ada:w-0f0cfb"), ("principal-reset", "ada:cv-0123456789ab")):
            r = ap.api_answer({"caller": HOUSE, "path": "run", "body": {"argv": owned([
                "request", "--kind", kind, "--target", target, "--ask", "Count it as your work?", "--why", "w",
                "--if-yes", "y", "--if-no", "n"])}})
            self.assertEqual(r["body"]["exit"], 1, kind)
            self.assertIn("not by a body", r["body"]["stderr"])
        with self.assertRaises(PermissionError):
            self.file(target="ada:w-0f0cfb")

    def test_the_card_is_in_the_piece_s_words_with_its_listing(self):
        aid = self.file()
        c = self.card(aid)
        self.assertEqual(c["text"], "Count these 3 scheduled jobs as your work?")
        self.assertIn("t-40 confirm first backup run", c["details"])
        self.assertEqual(c["actions"][0]["label"], "Allow")
        self.assertTrue(c["lead"])
        self.assertIn("fire as your work", c["if_yes"])
        self.assertTrue(c["if_no"].startswith("Nothing changes"))
        self.assertTrue(c["fine"].endswith("as you."), "never cut")
        one = self.file(target="art:w-840b4e", details="- Check the backup (w-840b4e): fires 10:00 AM")
        self.assertEqual(self.card(one)["text"], "Count this scheduled job as your work?")
        r = self.file(kind="principal-reset", target="art:cv-0123456789ab", details="The conversation: Slack, #general (cv-0123456789ab)")
        self.assertEqual(self.card(r)["text"], "Count this conversation as your work from now on?")
        self.assertIn("cv-0123456789ab", self.card(r)["fine"])

    def test_only_the_household_owner_answers(self):
        aid = self.file()
        # Loren owns art here: an agent's own owner still can't answer one (the household owner's alone).
        for caller in ({"kind": "owner", "who": "loren", "agents": ["art"]}, MEMBER_CALLER, None, {"kind": "agent", "agent": "art"}):
            for act in ("allow", "deny"):
                r = ap.ui_act({"action": act, "fields": {"id": aid}, **({"caller": caller} if caller else {})})
                self.assertFalse(r["ok"], (caller, act))
                self.assertIn("household owner", r["message"])
        self.assertEqual(ap.get(aid)["state"], "pending")
        r = ap.ui_act({"action": "allow", "fields": {"id": aid}, "caller": OWNER_CALLER})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["message"], "Allowed: Count these 3 scheduled jobs as your work.")

    def test_only_the_piece_spends_it_for_exactly_its_target(self):
        aid = self.file()
        ap.ui_act({"action": "allow", "fields": {"id": aid}, "caller": OWNER_CALLER})
        r = ap.api_answer({"caller": {"kind": "agent", "agent": "art", "house": "h-1"}, "path": "run", "body": {"argv": [
            "use", aid, "--kind", "vouch-wake", "--target", "art:w-0f0cfb,w-3c68fa,w-55e1c0"]}})
        self.assertEqual(r["body"]["exit"], 1)
        self.assertIn("not by a body", r["body"]["stderr"])
        # Never widened: one more wake, or one fewer, is another target.
        for t in ("art:w-0f0cfb,w-3c68fa,w-55e1c0,w-840b4e", "art:w-0f0cfb,w-3c68fa"):
            with self.assertRaises(PermissionError):
                ap.run(["use", aid, "--kind", "vouch-wake", "--target", t, "--as", self.AS, "--on-behalf", "owner"])
        self.assertEqual(ap.run(["use", aid, "--kind", "vouch-wake", "--target", "art:w-0f0cfb,w-3c68fa,w-55e1c0",
                                 "--as", self.AS, "--on-behalf", "owner"]), f"{aid}: used\n")
        with self.assertRaises(PermissionError):
            ap.run(["use", aid, "--kind", "vouch-wake", "--target", "art:w-0f0cfb,w-3c68fa,w-55e1c0", "--as", self.AS, "--on-behalf", "owner"])

    def test_a_deny_is_recorded_and_can_t_be_spent(self):
        aid = self.file(kind="principal-reset", target="art:cv-0123456789ab")
        self.assertTrue(ap.ui_act({"action": "deny", "fields": {"id": aid}, "caller": OWNER_CALLER})["ok"])
        self.assertEqual(ap.get(aid)["state"], "denied")
        with self.assertRaises(PermissionError):
            ap.run(["use", aid, "--kind", "principal-reset", "--target", "art:cv-0123456789ab", "--as", self.AS, "--on-behalf", "owner"])

    def test_a_box_write_off_is_the_runtime_s_owner_card_for_one_agent_and_box(self):
        box = "7f9310-0a1b2c-3d4e5f-6a7b8c"
        self.assertEqual(ap.writeoff_of(f"art:{box}@1762718400"), {"agent": "art", "box": box, "since": 1762718400})
        for bad in ("art:", "art", f"art:{box}", f"art:{box}@", f"art:{box}@x", f"art:{box}@1,2", "Art:one@1", "art:-one@1"):
            self.assertIsNone(ap.writeoff_of(bad), bad)
        details = f"Box {box}, last heard 2026-11-09T20:00:00Z. Its conversations there: cv-0123456789ab."
        def file(target=f"art:{box}@1762718400", d=details, as_=self.AS):
            return ap.run(["request", "--kind", "box-write-off", "--target", target, "--ask", "The box one has been silent for 12 minutes. Carry on Art's conversation from there on another box?",
                           "--why", "w", "--if-yes", "y", "--if-no", "n", "--details", d, "--as", as_, "--on-behalf", "owner"]).split(":")[0]
        with self.assertRaises(PermissionError):
            file(target=f"ada:{box}@1762718400")          # only for the agent it's filed for
        with self.assertRaises(ValueError):
            file(d="Its conversations: cv-0123456789ab")   # names the box
        r = ap.api_answer({"caller": HOUSE, "path": "run", "body": {"argv": owned([
            "request", "--kind", "box-write-off", "--target", f"art:{box}@1762718400", "--ask", "Move it?", "--why", "w", "--if-yes", "y", "--if-no", "n"])}})
        self.assertEqual(r["body"]["exit"], 1)
        self.assertIn("not by a body", r["body"]["stderr"])
        aid = file()
        c = self.card(aid)
        self.assertTrue(c["lead"])
        self.assertIn("silent for 12 minutes", c["text"])
        self.assertIn("next box", c["if_yes"])
        self.assertIn(box, c["fine"])
        r = ap.ui_act({"action": "allow", "fields": {"id": aid}, "caller": {"kind": "owner", "who": "loren", "agents": ["art"]}})
        self.assertFalse(r["ok"])
        self.assertTrue(ap.ui_act({"action": "allow", "fields": {"id": aid}, "caller": OWNER_CALLER})["ok"])
        r = ap.api_answer({"caller": {"kind": "owner"}, "path": "state", "body": {"ids": [aid]}})
        self.assertEqual(r["body"]["approvals"][aid], {"state": "allowed", "agent": "art", "kind": "box-write-off", "target": f"art:{box}@1762718400"})
        with self.assertRaises(PermissionError):
            ap.run(["use", aid, "--kind", "box-write-off", "--target", f"art:{box}@1762718400"])   # never without --as
        self.assertEqual(ap.run(["use", aid, "--kind", "box-write-off", "--target", f"art:{box}@1762718400", "--as", self.AS, "--on-behalf", "owner"]), f"{aid}: used\n")

    def test_state_carries_the_target_for_core(self):
        aid = self.file()
        r = ap.api_answer({"caller": {"kind": "owner"}, "path": "state", "body": {"ids": [aid]}})
        self.assertEqual(r["body"]["approvals"][aid], {"state": "pending", "agent": "art", "kind": "vouch-wake",
                                                        "target": "art:w-0f0cfb,w-3c68fa,w-55e1c0"})

    def test_the_piece_withdraws_its_own_pending_card_only(self):
        aid = self.file()
        r = ap.api_answer({"caller": {"kind": "agent", "agent": "art", "house": "h-1"}, "path": "run", "body": {"argv": ["withdraw", aid]}})
        self.assertEqual(r["body"]["exit"], 1)
        with self.assertRaises(PermissionError):
            ap.run(["withdraw", aid, "--as", json.dumps({"kind": "agent", "agent": "ada"})])
        self.assertEqual(ap.run(["withdraw", aid, "--as", self.AS]), f"{aid}: withdrawn\n")
        self.assertEqual(ap.get(aid)["state"], "withdrawn")
        self.assertEqual([a for a in ap.ui_manifest()["asks"] if a["actions"][0]["fields"]["id"] == aid], [])
        self.assertFalse(ap.ui_act({"action": "allow", "fields": {"id": aid}, "caller": OWNER_CALLER})["ok"])
        # An answer stands: an allowed one can't be withdrawn.
        b = self.file(target="art:w-840b4e", details="- Check the backup (w-840b4e): fires 10:00 AM")
        ap.ui_act({"action": "allow", "fields": {"id": b}, "caller": OWNER_CALLER})
        with self.assertRaises(PermissionError):
            ap.run(["withdraw", b, "--as", self.AS])
        # A body's own kinds aren't the piece's to withdraw.
        m = ap.api_answer({"caller": {"kind": "agent", "agent": "art", "house": "h-1"}, "path": "run", "body": {"argv": owned([
            "request", "--kind", "github-write", "--target", "x/y", "--ask", "May I push to the repo?", "--why", "w", "--if-yes", "y", "--if-no", "n"])}})
        mid = m["body"]["stdout"].split(":")[0]
        with self.assertRaises(PermissionError):
            ap.run(["withdraw", mid, "--as", self.AS])
        page = json.dumps(ap.ui_page({"path": "", "caller": OWNER_CALLER}))
        self.assertIn("Withdrawn unanswered", page)

    def test_a_card_lists_exactly_what_it_covers_or_is_refused(self):
        with self.assertRaises(ValueError):
            self.file(details="")
        with self.assertRaises(ValueError):
            self.file(details="- w-0f0cfb, fires 10:00\n- w-3c68fa, fires 10:00")   # w-55e1c0 left out
        with self.assertRaises(ValueError):
            self.file(details="w-0f0cfb,w-3c68fa,w-55e1c0")                      # ids alone: not when each fires
        with self.assertRaises(ValueError):
            self.file(kind="principal-reset", target="art:cv-0123456789ab", details=".")   # not the conversation
        with self.assertRaises(ValueError):
            self.file(kind="principal-reset", target="art:cv-0123456789ab", details=" ")
        # Another agent's wake, or a malformed id: refused at the filing.
        for t in ("ada:w-0f0cfb", "art:w-0F0CFB", "art:w-0f0cfb,*"):
            with self.assertRaises((ValueError, PermissionError)):
                self.file(target=t, details=f"- x ({t}): fires 10:00")

    def test_never_asked_again_after_a_no_and_never_two_waiting(self):
        aid = self.file()
        # Overlapping a card still waiting: refused (the piece withdraws that one first).
        with self.assertRaises(PermissionError):
            self.file(target="art:w-0f0cfb,w-840b4e", details="- w-0f0cfb, fires 10:00\n- w-840b4e, fires 10:00")
        self.assertEqual(self.file(), aid, "the same card again is the same card")
        ap.ui_act({"action": "deny", "fields": {"id": aid}, "caller": OWNER_CALLER})
        for t in ("art:w-0f0cfb,w-3c68fa,w-55e1c0", "art:w-3c68fa", "art:w-3c68fa,w-840b4e"):
            with self.assertRaises(PermissionError) as e:
                self.file(target=t, details="\n".join(f"- {i}, fires 10:00" for i in t.split(":")[1].split(",")))
            self.assertIn("not asked again", str(e.exception))
        self.assertTrue(self.file(target="art:w-840b4e", details="- w-840b4e, fires 10:00").startswith("a-"))
        r = self.file(kind="principal-reset", target="art:cv-0123456789ab", details="The conversation: Slack, #general (cv-0123456789ab)")
        ap.ui_act({"action": "deny", "fields": {"id": r}, "caller": OWNER_CALLER})
        with self.assertRaises(PermissionError):
            self.file(kind="principal-reset", target="art:cv-0123456789ab", details="The conversation: Slack, #general (cv-0123456789ab)")

    def test_filed_and_spent_on_the_owner_s_behalf_only_never_from_a_body(self):
        with self.assertRaises(ValueError):
            ap.run(["request", "--kind", "vouch-wake", "--target", "art:w-0f0cfb", "--ask", "Count it?", "--why", "w",
                    "--if-yes", "y", "--if-no", "n", "--details", "- w-0f0cfb", "--as", self.AS])
        aid = self.file()
        ap.ui_act({"action": "allow", "fields": {"id": aid}, "caller": OWNER_CALLER})
        t = "art:w-0f0cfb,w-3c68fa,w-55e1c0"
        with self.assertRaises(PermissionError):    # no --on-behalf: whom it's for is said, never left unsaid
            ap.run(["use", aid, "--kind", "vouch-wake", "--target", t, "--as", self.AS])
        for k in ("LOOM_BODY_CONVERSATION", "LOOM_BODY_KEY"):     # either one: a body the runtime runs here
            with mock.patch.dict(os.environ, {k: "x"}):
                with self.assertRaises(PermissionError):
                    ap.run(["use", aid, "--kind", "vouch-wake", "--target", t, "--as", self.AS, "--on-behalf", "owner"])
                with self.assertRaises(PermissionError):
                    self.file(target="art:w-0000ff", details="- w-0000ff, fires 10:00")
                with self.assertRaises(PermissionError):
                    ap.run(["withdraw", aid, "--as", self.AS])
        # On loom's machine with no --as, even as the card's own agent: refused (a piece's spend always says whose).
        with mock.patch.object(ap, "LOCAL_AGENT", "art"), self.assertRaises(PermissionError) as e:
            ap.run(["use", aid, "--kind", "vouch-wake", "--target", t])
        self.assertIn("--as the agent", str(e.exception))
        self.assertEqual(ap.get(aid)["state"], "allowed")

    def test_a_withdrawn_card_shows_under_answered_in_loom_s_inbox(self):
        aid = self.file()
        ap.run(["withdraw", aid, "--as", self.AS])
        done = [x for x in ap.ui_manifest()["answered"] if x["outcome"].startswith("Withdrawn")]
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0]["text"], "Count these 3 scheduled jobs as your work?")

    def test_a_no_overrules_an_earlier_unspent_yes_covering_the_same_wake(self):
        a1 = self.file(target="art:w-000001,w-000002", details="- w-000001, fires 10:00\n- w-000002, fires 10:00")
        a2 = self.file(target="art:w-000003", details="- w-000003, fires 10:00")
        ap.ui_act({"action": "allow", "fields": {"id": a1}, "caller": OWNER_CALLER})
        # Overlapping a yes not applied yet: refused (apply it first), so a No can't land on part of a yes.
        with self.assertRaises(PermissionError):
            self.file(target="art:w-000002,w-000004", details="- w-000002, fires 10:00\n- w-000004, fires 10:00")
        ap.run(["withdraw", a2, "--as", self.AS])
        # Filed before the yes and answered No after it (both waiting at once can't happen for one wake, so by hand):
        b = {**ap.get(a1), "id": "a-0000dead", "target": "art:w-000002,w-000009", "state": "denied", "decided": "2999-01-01T00:00:00Z"}
        with ap.Store() as st:
            st.d["approvals"][b["id"]] = b
            st.save()
        with self.assertRaises(PermissionError) as e:
            ap.run(["use", a1, "--kind", "vouch-wake", "--target", "art:w-000001,w-000002", "--as", self.AS, "--on-behalf", "owner"])
        self.assertIn("overruled", str(e.exception))
        # A No changes no other card: a1 is still allowed, and w-000003 (asked, then withdrawn) can be asked again.
        self.assertEqual(ap.get(a1)["state"], "allowed")
        self.assertTrue(self.file(target="art:w-000003", details="- w-000003, fires 10:00").startswith("a-"))

    def test_a_no_outlives_the_store_s_trim(self):
        aid = self.file(target="art:w-00000a", details="- w-00000a, fires 10:00")
        ap.ui_act({"action": "deny", "fields": {"id": aid}, "caller": OWNER_CALLER})
        with ap.Store() as st:      # older than everything after it, so a trim to the newest would drop it
            st.d["approvals"][aid]["at"] = "2000-01-01T00:00:00Z"
            st.save()
        with mock.patch.object(ap, "KEEP", 1):
            self.file(target="art:w-00000b", details="- w-00000b, fires 10:00")
            self.file(target="art:w-00000c", details="- w-00000c, fires 10:00")
        with self.assertRaises(PermissionError):
            self.file(target="art:w-00000a", details="- w-00000a, fires 10:00")

    def test_filed_from_the_runtime_s_house_spent_by_its_plain_caller(self):
        out = ap.run(["request", "--kind", "principal-reset", "--target", "art:cv-0123456789ab", "--ask", "Count it?", "--why", "w",
                      "--if-yes", "y", "--if-no", "n", "--details", "The conversation: Slack, #general (cv-0123456789ab)",
                      "--as", json.dumps({"kind": "agent", "agent": "art", "house": "loom's runtime"}), "--on-behalf", "owner"])
        aid = out.split(":")[0]
        ap.ui_act({"action": "allow", "fields": {"id": aid}, "caller": OWNER_CALLER})
        self.assertEqual(ap.run(["use", aid, "--kind", "principal-reset", "--target", "art:cv-0123456789ab",
                                 "--as", json.dumps({"kind": "agent", "agent": "art"}), "--on-behalf", "owner"]), f"{aid}: used\n")

    def test_asking_again_for_an_allowed_card_not_yet_applied_says_so(self):
        aid = self.file()
        ap.ui_act({"action": "allow", "fields": {"id": aid}, "caller": OWNER_CALLER})
        out = ap.run(["request", "--kind", "vouch-wake", "--target", "art:w-0f0cfb,w-3c68fa,w-55e1c0", "--ask", "Count these?",
                      "--why", "w", "--if-yes", "y", "--if-no", "n", "--details", "- w-0f0cfb, fires 1\n- w-3c68fa, fires 2\n- w-55e1c0, fires 3",
                      "--as", self.AS, "--on-behalf", "owner"])
        self.assertEqual(out, f"{aid}: the owner allowed it; the piece that filed it applies it (use it there: --kind vouch-wake)\n")



class StandingJobs(unittest.TestCase):
    """t-2013: Allow every time on a local wake's card. One Allow covers every later time the same agent sets exactly
    those words for the same principal; changed words, another agent, another principal or a turned-off job ask."""
    TGT = "ada:0123456789abcdef"

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(ap, "STATE", Path(self.td.name))
        self.p.start()
        self.la = mock.patch.object(ap, "LOCAL_AGENT", "ada")     # loom's machine's own agent
        self.la.start()

    def tearDown(self):
        self.la.stop()
        self.p.stop()
        self.td.cleanup()

    def test_never_for_another_agent_s_local_wake(self):
        # Its clock never asks (its local wake runs in a house): a standing job for it would be a promise that's false.
        bo = {**HOUSE, "agent": "bo"}
        aid = self.file(target="bo:0123456789abcdef", house=bo)
        self.assertFalse(ap.every_time_ok(ap.get(aid)))
        self.assertFalse(ap.ui_act({"action": "every-time", "caller": OWNER, "fields": {"id": aid}})["ok"])

    @staticmethod
    def dig(target, tail="0"):
        """A whole-words hash whose first 16 hex digits are the target's (the clock's sha256 of the words)."""
        return target.split(":", 1)[1] + tail * 48

    def file(self, target=TGT, on_behalf="owner", house=HOUSE, digest=True):
        """The clock files a local wake's card for a house's body (--as), on whose behalf it was set."""
        out = ap.run(["request", "--kind", "local-wake", "--target", target, *(["--digest", self.dig(target)] if digest else []), "--what", "run on loom's machine: check the disk",
                      "--ask", "OK for Ada to run the job below on loom's machine itself?", "--why", "It needs loom's machine.",
                      "--if-yes", "It runs once.", "--if-no", "Nothing runs.", "--details", "Runs exactly as written:\ncheck the disk",
                      "--as", json.dumps(house), "--on-behalf", on_behalf])
        return out.split(":")[0]

    def spend(self, aid, target=TGT, on_behalf="owner"):
        """The continuation sets the wake the card was for: the clock spends it."""
        ap.run(["use", aid, "--kind", "local-wake", "--target", target, "--as", json.dumps(HOUSE), "--on-behalf", on_behalf])

    def match(self, target=TGT, on_behalf="owner", house=HOUSE, tail="0"):
        try:
            return ap.run(["standing", "--kind", "local-wake", "--target", target, "--digest", self.dig(target, tail),
                           "--as", json.dumps(house), "--on-behalf", on_behalf])
        except LookupError:
            return None

    def test_one_allow_every_time_covers_every_later_time_and_says_so(self):
        aid = self.file()
        # The third button: on the page's sheet for the owner, and on Home's card.
        sheet = ap.ui_page({"path": "", "caller": OWNER})["sections"][0]["blocks"][0]["rows"][0]["sheet"]
        labels = [b["label"] for b in next(x for x in sheet["blocks"] if x["type"] == "buttons")["items"]]
        self.assertEqual(labels, ["Allow once", "Allow every time", "Deny"])
        ask = ap.ui_manifest()["asks"][0]
        self.assertEqual([x["action"] for x in ask["actions"]], ["allow", "every-time", "deny"])
        self.assertIn("Allow every time: these exact words run as you", ask["fine"])
        self.assertLessEqual(len(ask["fine"]), ap.FINE_MAX)
        self.assertIsNone(self.match(), "nothing stands before the owner says so")
        r = ap.ui_act({"action": "every-time", "caller": OWNER, "fields": {"id": aid}})
        self.assertTrue(r["ok"], r)
        # This card is allowed (its continuation spends it as before), and the job stands from now on.
        self.assertEqual(ap.get(aid)["state"], "allowed")
        first, second = self.match(), self.match()
        self.assertRegex(first or "", r"^s-[0-9a-f]{8}: standing")
        self.assertEqual(first, second)
        # No second card: the clock never files one while it matches. And each use is counted and logged.
        with ap.Store() as s:
            [j] = s.standing()
        self.assertEqual((j["uses"], j["for"], j["agent"], j["target"], j["by"]), (2, "owner", "ada", self.TGT, "jonathan-barket"))
        log = [json.loads(l) for l in (Path(self.td.name) / "audit.jsonl").read_text().splitlines()]
        self.assertEqual([e["event"] for e in log].count("standing-use"), 2)
        # Asking again for an identical one (set the long way round) doesn't double it.
        self.spend(aid)
        aid2 = self.file()
        self.assertNotEqual(aid2, aid)
        ap.ui_act({"action": "every-time", "caller": OWNER, "fields": {"id": aid2}})
        with ap.Store() as s:
            self.assertEqual(len(s.standing()), 1)
        # The owner's page lists it: what it runs, used twice.
        panel = next(b for b in ap.ui_page({"path": "", "caller": OWNER})["sections"][1]["blocks"] if b.get("title") == "Standing jobs")
        self.assertEqual(panel["rows"][0]["value"], "Every time")
        self.assertIn("used 2 times", panel["rows"][0]["detail"])

    def test_changed_words_another_agent_another_principal_or_service_ask(self):
        ap.ui_act({"action": "every-time", "caller": OWNER, "fields": {"id": self.file()}})
        self.assertIsNone(self.match(target="ada:0123456789abcdee"), "other words")
        self.assertIsNone(self.match(target="bo:0123456789abcdef", house={**HOUSE, "agent": "bo"}), "another agent")
        self.assertIsNone(self.match(on_behalf="loren"), "set for someone else")
        self.assertIsNone(self.match(on_behalf="-"), "set for someone loom can't name")
        # A chat house (others steer it) spends only what its own service asked for.
        self.assertIsNone(self.match(house={**HOUSE, "house": "h-9", "service": "chat"}))
        # Any of the agent's plain houses: a later continuation's.
        self.assertIsNotNone(self.match(house={**HOUSE, "house": "h-99"}))

    def test_turned_off_it_asks_again_and_a_wake_set_under_it_is_told(self):
        first = self.file()
        ap.ui_act({"action": "every-time", "caller": OWNER, "fields": {"id": first}})
        self.spend(first)
        sid = self.match().split(":")[0]
        check = lambda who=HOUSE: ap.run(["standing", "--check", sid, "--as", json.dumps(who)])
        self.assertEqual(check(), "on\n")
        self.assertEqual(check({**HOUSE, "agent": "bo"}), "none\n", "only the agent it's for reads it")
        self.assertFalse(ap.ui_act({"action": "standing-off", "caller": {"kind": "owner", "who": "sam", "agents": ["ada"]}, "fields": {"id": sid}})["ok"])
        r = ap.ui_act({"action": "standing-off", "caller": OWNER, "fields": {"id": sid}})
        self.assertTrue(r["ok"], r)
        self.assertEqual(check(), "off\n")
        self.assertIsNone(self.match())
        # Kept, shown as turned off; asking again files a card, and a new Allow every time makes a new one.
        with ap.Store() as s:
            [j] = s.standing()
        self.assertEqual(j["off"]["by"], "jonathan-barket")
        aid = self.file()
        self.assertEqual(ap.get(aid)["state"], "pending")
        ap.ui_act({"action": "every-time", "caller": OWNER, "fields": {"id": aid}})
        self.assertNotEqual(self.match().split(":")[0], sid)

    def test_only_the_owner_sets_one_and_never_for_a_named_person_or_another_kind(self):
        aid = self.file()
        for c in (None, {"kind": "owner", "who": "sam", "agents": ["ada"]}, {"kind": "agent", "agent": "ada"}):
            self.assertFalse(ap.ui_act({"action": "every-time", "caller": c, "fields": {"id": aid}})["ok"], c)
        sheet = ap.ui_page({"path": "", "caller": {"kind": "owner", "who": "sam", "agents": ["ada"]}})["sections"][0]["blocks"][0]["rows"][0]["sheet"]
        self.assertNotIn("every-time", json.dumps(next(x for x in sheet["blocks"] if x["type"] == "buttons")))
        self.assertEqual(ap.get(aid)["state"], "pending")
        # For a named person: a standing yes for them would be a grant, not this. Not offered, refused if tried.
        pid = self.file(target="ada:1111111111111111", on_behalf="loren")
        self.assertNotIn("every-time", json.dumps(next(a for a in ap.ui_manifest()["asks"] if "Loren" in a["text"])))
        self.assertFalse(ap.ui_act({"action": "every-time", "caller": OWNER, "fields": {"id": pid}})["ok"])
        # Another kind (a body's own wall) never.
        r = ap.api_answer({"caller": HOUSE, "path": "run", "body": {"argv": ["request", "--kind", "github-write", "--target", "acme/app:x",
                           "--what", "push", "--why", "y", *AS_OWNER]}})
        gid = r["body"]["stdout"].split(":")[0]
        self.assertFalse(ap.ui_act({"action": "every-time", "caller": OWNER, "fields": {"id": gid}})["ok"])
        # Answered already: refused.
        ap.ui_act({"action": "deny", "caller": OWNER, "fields": {"id": aid}})
        self.assertFalse(ap.ui_act({"action": "every-time", "caller": OWNER, "fields": {"id": aid}})["ok"])

    def test_someone_loom_can_t_name_gets_it_as_the_owner_said_for_exactly_that(self):
        aid = self.file(on_behalf="-")
        self.assertIn("every-time", json.dumps(ap.ui_manifest()["asks"][0]["actions"]))
        ap.ui_act({"action": "every-time", "caller": OWNER, "fields": {"id": aid}})
        self.assertIsNotNone(self.match(on_behalf="-"))
        self.assertIsNone(self.match(on_behalf="owner"), "the owner's own work is its own principal")

    def test_a_body_never_matches_or_reads_it_itself(self):
        ap.ui_act({"action": "every-time", "caller": OWNER, "fields": {"id": self.file()}})
        r = ap.api_answer({"caller": HOUSE, "path": "run", "body": {"argv": ["standing", "--kind", "local-wake", "--target", self.TGT,
                           "--on-behalf", "owner"]}})
        self.assertEqual(r["body"]["exit"], 1)
        with self.assertRaises(PermissionError):
            ap.run(["standing", "--kind", "local-wake", "--target", self.TGT, "--on-behalf", "owner"])
        with self.assertRaises(PermissionError):
            ap.run(["standing", "--kind", "local-wake", "--target", self.TGT, "--as", json.dumps(HOUSE)])
        with mock.patch.dict(os.environ, {"LOOM_BODY_CONVERSATION": "cv-0123456789ab"}), self.assertRaises(PermissionError):
            self.match()
        with ap.Store() as s:
            self.assertEqual(s.standing()[0]["uses"], 0)

    def test_a_body_on_loom_s_machine_never_files_a_local_wake_card(self):
        # The cold review's hole: a body's own words on the card, made standing by Allow every time.
        for env in ({"LOOM_BODY_CONVERSATION": "cv-0123456789ab"}, {"LOOM_BODY_KEY": "k"}):
            with mock.patch.dict(os.environ, env), self.assertRaises(PermissionError):
                self.file()
        self.assertEqual(ap.ui_manifest()["asks"], [])

    def test_not_offered_for_a_chat_house_or_named_people_and_always_explained(self):
        chat = self.file(on_behalf="-", house={**HOUSE, "house": "h-9", "service": "chat"})
        self.assertFalse(ap.every_time_ok(ap.get(chat)))
        self.assertFalse(ap.ui_act({"action": "every-time", "caller": OWNER, "fields": {"id": chat}})["ok"])
        self.assertFalse(ap.every_time_ok({**ap.get(chat), "who": HOUSE, "people": ["loren", "dan"]}))
        # The sentence beside the button is never dropped, however long the rest.
        a = {**ap.get(self.file(target="ada:2222222222222222")), "who": {"agent": "ada", "where": "h-1 " + "x" * 300}}
        fine = ap.fine_print(a)
        self.assertLessEqual(len(fine), ap.FINE_MAX)
        self.assertTrue(fine.endswith("until you turn it off under Standing jobs."))

    def test_matched_on_the_whole_hash_and_the_people_named(self):
        # The fresh read's hole: two prompts made to share the 16-hex target. The whole sha256 tells them apart.
        ap.ui_act({"action": "every-time", "caller": OWNER, "fields": {"id": self.file()}})
        self.assertIsNone(self.match(tail="1"))
        self.assertIsNotNone(self.match())
        # A digest that isn't the target's own is refused when filed; a card filed without one isn't offered.
        with self.assertRaises(ValueError):
            ap.run(["request", "--kind", "local-wake", "--target", "ada:3333333333333333", "--digest", "4" * 64, "--what", "w",
                    "--why", "y", "--as", json.dumps(HOUSE), "--on-behalf", "owner"])
        self.assertFalse(ap.every_time_ok(ap.get(self.file(target="ada:5555555555555555", digest=False))))
        # Someone loom can't name, with the people the runtime named: offered only with nobody named, matched exactly.
        eve = self.file(target="ada:6666666666666666", on_behalf="?:eve")
        self.assertEqual(ap.get(eve)["people"], ["eve"])
        self.assertFalse(ap.every_time_ok(ap.get(eve)))
        none = self.file(target="ada:7777777777777777", on_behalf="-")
        ap.ui_act({"action": "every-time", "caller": OWNER, "fields": {"id": none}})
        self.assertIsNotNone(self.match(target="ada:7777777777777777", on_behalf="-"))
        self.assertIsNone(self.match(target="ada:7777777777777777", on_behalf="?:eve"))
        # And a single card asked while Eve steered is spent only for Eve.
        ap.ui_act({"action": "allow", "caller": OWNER, "fields": {"id": eve}})
        with self.assertRaises(PermissionError):
            ap.run(["use", eve, "--kind", "local-wake", "--target", "ada:6666666666666666", "--as", json.dumps(HOUSE), "--on-behalf", "?:dan"])
        ap.run(["use", eve, "--kind", "local-wake", "--target", "ada:6666666666666666", "--as", json.dumps(HOUSE), "--on-behalf", "?:eve"])

    def test_a_broken_file_has_no_standing_jobs(self):
        ap.ui_act({"action": "every-time", "caller": OWNER, "fields": {"id": self.file()}})
        f = Path(self.td.name) / "rules.json"
        d = json.loads(f.read_text())
        good = dict(d["standing"][0])
        for bad in ({"target": "ada:*"}, {"uses": "x"}, {"uses": -1}, {"off": {}}, {"used": 5}, {"used": "yesterday"},
                    {"off": {"at": "soon"}}, {"what": 3}, {"details": ["x"]}):
            d["standing"] = [{**good, **bad}]
            f.write_text(json.dumps(d))
            self.assertIsNone(self.match(), bad)
            # And nothing set under it before runs: a file that can't be read can't be turned off from the page either.
            self.assertEqual(ap.run(["standing", "--check", good["id"], "--as", json.dumps(HOUSE)]), "none\n")
            # The page still draws, with nothing standing.
            panel = next(b for b in ap.ui_page({"path": "", "caller": OWNER})["sections"][1]["blocks"] if b.get("title") == "Standing jobs")
            self.assertEqual(panel["rows"][0]["label"], "No standing jobs")
        # And a rule edit never writes the job away while it's unreadable.
        with self.assertRaises(ValueError):
            ap.set_rule("merge", "github:acme/*", "do", "jo")


class HostSteps(unittest.TestCase):
    """Steps on loom's machine (t-2104): the owner's standing rules per verb, the runtime's tell and spend, his answer."""

    AS = ["--as", json.dumps(HOUSE), "--on-behalf", "owner"]

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(ap, "STATE", Path(self.td.name))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.td.cleanup()

    def api(self, argv, caller=HOUSE):
        return ap.api_answer({"caller": caller, "path": "run", "body": {"argv": owned(argv)}})["body"]

    def set(self, scope, tier):
        r = ap.ui_act({"action": "host-rule-add", "caller": OWNER, "fields": {"scope": scope, "tier": tier}})
        self.assertTrue(r["ok"], r)

    def rule(self, target, extra=()):
        return ap.run(["rule", "host", target, *self.AS, *extra])[0]

    def test_no_rule_is_none_so_the_runtime_uses_the_verb_s_own_tier(self):
        self.assertEqual(self.rule("restart:loom-http.service"), "none\n")
        self.assertEqual(self.rule("read:allocator-shadow.jsonl"), "none\n")

    def test_most_specific_wins_then_strictest(self):
        self.set("restart", "tell")
        self.set("restart:loom-piece-*", "do")
        self.set("restart:loom-http.service", "ask")
        self.set("host:pieces-update:*", "do")
        self.assertEqual(self.rule("restart:loom-http.service"), "ask\n")
        self.assertEqual(self.rule("restart:loom-piece-tasks.service"), "do\n")
        self.assertEqual(self.rule("restart:loom-http-lan.service"), "tell\n")
        self.assertEqual(self.rule("pieces-update:runtime"), "do\n")
        self.assertEqual(self.rule("deploy-loom:fix"), "none\n")
        # The owner's rules, for his own work only: anyone else's asks.
        self.assertEqual(ap.run(["rule", "host", "pieces-update:runtime", "--as", json.dumps(HOUSE), "--on-behalf", "loren"])[0], "ask\n")
        # Over the API a body reads its own tier, as the owner's own work only.
        self.assertEqual(self.api(["rule", "host", "pieces-update:runtime"])["stdout"], "do\n")
        self.assertEqual(self.api(["rule", "host", "pieces-update:runtime", "--principal", "S:ada:@loren"])["stdout"], "ask\n")

    def test_bad_scopes_and_targets_are_refused(self):
        for bad in ("rm -rf", "host:shell", "restart:../x", "host:read:../../etc/passwd", "unknown:x", "host:restart:.."):
            r = ap.ui_act({"action": "host-rule-add", "caller": OWNER, "fields": {"scope": bad, "tier": "do"}})
            self.assertFalse(r["ok"], bad)
        for bad in ("restart", "shell:ls", "read:../secrets", "restart:a b"):
            with self.assertRaises(ValueError):
                ap.run(["rule", "host", bad, *self.AS])
        # The merge form never makes a host rule, and a merge scope never answers a host step.
        self.assertFalse(ap.ui_act({"action": "rule-add", "caller": OWNER, "fields": {"scope": "host:restart", "kind": "host", "tier": "do"}})["ok"])
        ap.ui_act({"action": "rule-add", "caller": OWNER, "fields": {"scope": "host:restart", "tier": "do"}})
        self.assertEqual(self.rule("restart:loom-http.service"), "none\n")

    def test_only_the_owner_sets_them_and_never_as_a_grant(self):
        member = {"kind": "owner", "who": "sam", "agents": ["ada"]}
        for c in (member, {"kind": "agent", "agent": "ada"}, None):
            self.assertFalse(ap.ui_act({"action": "host-rule-add", "caller": c, "fields": {"scope": "restart", "tier": "do"}})["ok"])
        with self.assertRaises(ValueError):
            ap.set_grant("loren", "host", "host:restart", "do", "jo")
        self.set("deploy-loom", "tell")
        panel = next(b for b in ap.ui_page({"path": "", "caller": OWNER})["sections"][1]["blocks"] if b.get("title") == "Steps on loom's machine")
        self.assertEqual((panel["rows"][0]["label"], panel["rows"][0]["value"]), ("Deploying loom: any", "Tell me after"))
        # No rule removes it.
        r = ap.ui_act({"action": "rule", "caller": OWNER, "fields": {"scope": "host:deploy-loom", "kind": "host", "tier": "none"}})
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.rule("deploy-loom:fix"), "none\n")

    def test_suggested_rules_show_until_he_sets_one_and_set_nothing_themselves(self):
        self.assertEqual(set(ap.HOST_OWN_TIER), set(ap.HOST_VERBS))
        self.assertEqual({ap.HOST_SCOPE.fullmatch(s).group(1) for s, _ in ap.HOST_SUGGESTED}, set(ap.HOST_VERBS))
        panel = lambda: next(b for b in ap.ui_page({"path": "", "caller": OWNER})["sections"][1]["blocks"]
                             if b.get("title") == "Steps on loom's machine")
        rows = {r["label"]: r for r in panel()["rows"]}
        dep = rows["Deploying loom: any"]
        self.assertEqual((dep["value"], dep["choice"]["value"]), ("No rule yet", "none"))
        self.assertIn("Suggested: Tell me after", dep["detail"])
        self.assertIn("asks you each time", dep["detail"])
        self.assertIn("runs without asking", rows["Reading a service's log: any"]["detail"])
        self.assertIn("Tell me after (suggested)", [o["label"] for o in dep["choice"]["options"]])
        # Showing them sets nothing: every step still runs at its own tier.
        self.assertEqual(self.rule("deploy-loom:fix"), "none\n")
        # His tap on the suggestion is an ordinary rule; the suggestion row goes, the rule row takes its place.
        r = ap.ui_act({"action": "rule", "caller": OWNER, "fields": dep["choice"]["fields"] | {"tier": "tell"}})
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.rule("deploy-loom:fix"), "tell\n")
        labels = [(r["label"], r["value"]) for r in panel()["rows"]]
        self.assertEqual(labels.count(("Deploying loom: any", "Tell me after")), 1)
        self.assertNotIn(("Deploying loom: any", "No rule yet"), labels)
        # A rule for every step of a verb, written either way, is his rule for it: no suggestion beside it.
        self.set("restart:*", "do")
        self.set("read:allocator-*", "ask")
        labels = [(r["label"], r["value"]) for r in panel()["rows"]]
        self.assertNotIn(("Restarting a service: any", "No rule yet"), labels)
        # A narrower rule leaves the verb's own suggestion standing.
        self.assertIn(("Reading the runtime's logs: any", "No rule yet"), labels)
        # Tapping "No rule" on a suggestion writes nothing.
        r = ap.ui_act({"action": "rule", "caller": OWNER, "fields": {"scope": "host:journal", "kind": "host", "tier": "none"}})
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.rule("journal:loom-http.service"), "none\n")
        # Not his, no rule: a member's tap on a suggestion is refused like any rule.
        self.assertFalse(ap.ui_act({"action": "rule", "caller": {"kind": "owner", "who": "sam", "agents": ["ada"]},
                                    "fields": {"scope": "host:restart", "kind": "host", "tier": "do"}})["ok"])

    def test_a_rules_file_that_can_t_be_read_asks_even_for_a_read(self):
        (Path(self.td.name) / "rules.json").write_text("{not json")
        self.assertEqual(self.rule("read:allocator-shadow.jsonl"), "ask\n")

    def test_tell_is_the_runtime_s_only_and_only_under_tell_or_do(self):
        self.set("pieces-update", "tell")
        # A body over the API, or one on loom's machine, never files a notice that a step ran.
        self.assertEqual(self.api(["tell", "--kind", "host", "--target", "pieces-update:runtime", "--what", "x"])["exit"], 1)
        self.assertEqual(self.api(["tell", "--kind", "host", "--target", "pieces-update:runtime", "--what", "x", *self.AS])["exit"], 1)
        with mock.patch.dict(os.environ, {"LOOM_BODY_CONVERSATION": "cv-0123456789ab"}):
            with self.assertRaises(PermissionError):
                ap.run(["tell", "--kind", "host", "--target", "pieces-update:runtime", "--what", "x", *self.AS])
        out = ap.run(["tell", "--kind", "host", "--target", "pieces-update:runtime", "--what", "ada ran pieces-update:runtime", *self.AS])
        self.assertIn("Ada: update loom's runtime piece on loom's machine", out)
        # Not under an ask rule, no rule, or for anyone else.
        for t in ("restart:loom-http.service", "deploy-loom:x"):
            with self.assertRaises(PermissionError):
                ap.run(["tell", "--kind", "host", "--target", t, "--what", "x", *self.AS])
        with self.assertRaises(PermissionError):
            ap.run(["tell", "--kind", "host", "--target", "pieces-update:runtime", "--what", "x", "--as", json.dumps(HOUSE), "--on-behalf", "loren"])

    def file(self, target="restart:loom-http.service"):
        r = self.api(["request", "--kind", "host", "--target", target, "--ask", "Can I restart loom-http to pick up the fix?",
                      "--why", "The new runtime needs it.", "--if-yes", "I restart it once.", "--if-no", "I leave it running."])
        self.assertEqual(r["exit"], 0, r)
        return r["stdout"].split(":")[0]

    def test_the_card_names_the_step_only_the_owner_answers_only_the_runtime_spends(self):
        aid = self.file()
        ask = next(x for x in ap.ui_manifest()["asks"] if x["text"].startswith("Restart"))
        self.assertEqual(ask["text"], "Restart loom-http on loom's machine?")
        self.assertIn("Can I restart loom-http", ask["details"])
        self.assertIn("exactly restart:loom-http.service once", ask["fine"])
        self.assertEqual(ask["if_no"], "Nothing runs on loom's machine.")
        # An agent's owner who isn't the household's can't answer it; nor no caller.
        for c in ({"kind": "owner", "who": "sam", "agents": ["ada"]}, None):
            self.assertFalse(ap.ui_act({"action": "allow", "caller": c, "fields": {"id": aid}})["ok"])
        self.assertTrue(ap.ui_act({"action": "allow", "caller": OWNER, "fields": {"id": aid}})["ok"])
        # A body's own use never burns his yes: over the API, or on loom's machine.
        self.assertEqual(self.api(["use", aid, "--kind", "host", "--target", "restart:loom-http.service"])["exit"], 1)
        with mock.patch.dict(os.environ, {"LOOM_BODY_KEY": "k"}):
            with self.assertRaises(PermissionError):
                ap.run(["use", aid, "--kind", "host", "--target", "restart:loom-http.service", *self.AS])
        self.assertEqual(ap.get(aid)["state"], "allowed")
        # The runtime spends it, for exactly that step, once.
        with self.assertRaises(PermissionError):
            ap.run(["use", aid, "--kind", "host", "--target", "restart:loom-http-lan.service", *self.AS])
        self.assertEqual(ap.run(["use", aid, "--kind", "host", "--target", "restart:loom-http.service", *self.AS]), f"{aid}: used\n")
        with self.assertRaises(PermissionError):
            ap.run(["use", aid, "--kind", "host", "--target", "restart:loom-http.service", *self.AS])

    def test_no_card_for_a_step_the_rules_answer_or_for_anyone_else(self):
        self.set("restart:loom-http.service", "do")
        r = self.api(["request", "--kind", "host", "--target", "restart:loom-http.service", "--what", "restart", "--why", "fix"])
        self.assertEqual(r["exit"], 1)
        self.assertIn("no card was filed", r["stderr"])
        r = self.api(["request", "--kind", "host", "--target", "deploy-loom:fix", "--what", "deploy", "--why", "fix", "--principal", "S:ada:@loren"])
        self.assertEqual(r["exit"], 1)
        self.assertEqual(self.api(["request", "--kind", "host", "--target", "shell:ls", "--what", "x", "--why", "y"])["exit"], 1)


if __name__ == "__main__":
    unittest.main()
