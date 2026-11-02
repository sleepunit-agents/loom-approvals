#!/usr/bin/env python3
"""Tests for approvals (loom's approvals piece).

    python3 test/test_approvals.py

A body asks, the owner allows or denies on the page, and an allowed approval is
spent once by exactly the action it names, by the body that asked.
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parent.parent / "bin" / "approvals"

# Nothing here reaches a real store, a real piece home or a house's forwarding.
_sandbox = tempfile.TemporaryDirectory()
for k in ("LOOM_PIECE_HOME", "HOUSE_ID", "APPROVALS_LOCAL_AGENT"):
    os.environ.pop(k, None)
os.environ["APPROVALS_DIR"] = str(Path(_sandbox.name) / "unused")
os.environ["APPROVALS_KINDS"] = json.dumps({"github-write": "A GitHub write"})

spec = importlib.util.spec_from_loader("approvals_cli", loader=None, origin=str(SCRIPT))
ap = importlib.util.module_from_spec(spec)
ap.__file__ = str(SCRIPT)
exec(compile(SCRIPT.read_text(), str(SCRIPT), "exec"), ap.__dict__)

HOUSE = {"kind": "agent", "agent": "ada", "house": "h-7"}


class Approvals(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(ap, "STATE", Path(self.td.name))
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.td.cleanup()

    def api(self, argv, caller=HOUSE):
        return ap.api_answer({"caller": caller, "path": "run", "body": {"argv": argv}})

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
        self.assertIn("push the fix to acme/app b", m["asks"][0]["text"])
        self.assertNotIn("<b>", json.dumps(m))
        # In words: the wall (named by APPROVALS_KINDS), the target, where it was asked from (no house id), and why.
        self.assertEqual(m["asks"][0]["meta"], "A GitHub write: acme/app:octocat · from Ada's house (or a later one) · “Sam asked for it in #general”")
        self.assertEqual([b["action"] for b in m["asks"][0]["actions"]], ["allow", "deny"])
        # Waiting: nothing to spend.
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"])["body"]["exit"], 1)
        self.assertTrue(ap.ui_act({"action": "allow", "fields": {"id": aid}})["ok"])
        self.assertEqual(ap.ui_manifest()["asks"], [])
        # Only exactly what it allowed, by who asked.
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/other:octocat"])["body"]["exit"], 1)
        self.assertEqual(self.api(["use", aid, "--kind", "relay", "--target", "acme/app:octocat"])["body"]["exit"], 1)
        # Another agent, or the agent's service, can't spend what its house asked for.
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"],
                                  {"kind": "agent", "agent": "bo", "house": "h-8"})["body"]["exit"], 1)
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"],
                                  {"kind": "agent", "agent": "ada", "house": "h-8", "service": "chat"})["body"]["exit"], 1)
        # Nor the agent outside a house (a plain agent token), or loom's machine acting for it.
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"],
                                  {"kind": "agent", "agent": "ada"})["body"]["exit"], 1)
        self.assertEqual(ap.spender({"agent": "ada", "where": "loom's machine"})[1], "local")
        # The agent's later house can: the one that asked is usually gone by the time it's allowed.
        ok = self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"],
                      {"kind": "agent", "agent": "ada", "house": "h-9"})
        self.assertEqual(ok["body"]["exit"], 0, ok)
        # Once.
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"])["body"]["exit"], 1)
        self.assertEqual(self.api(["check", aid])["body"]["stdout"], "used\n")
        events = [json.loads(l)["event"] for l in (Path(self.td.name) / "audit.jsonl").read_text().splitlines()]
        self.assertEqual(events, ["request", "allow", "refused-use", "refused-use", "refused-use", "refused-use", "refused-use", "use"])

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

    def test_a_piece_on_loom_s_machine_spends_it_for_the_body_it_acts_for_and_only_locally(self):
        # The clock runs a house's `wake relay` here, so it spends the house's approval with --as.
        r = self.api(["request", "--kind", "relay", "--target", "ada->cy", "--what", "relay to cy", "--why", "ask about the tracks"])
        aid = r["body"]["stdout"].split(":")[0]
        ap.ui_act({"action": "allow", "fields": {"id": aid}})
        # Over the API, --as is refused: a body can't claim to be another.
        self.assertEqual(self.api(["use", aid, "--kind", "relay", "--target", "ada->cy", "--as", json.dumps(HOUSE)],
                                  {"kind": "agent", "agent": "ada", "house": "h-9"})["body"]["exit"], 1)
        self.assertEqual(ap.run(["use", aid, "--kind", "relay", "--target", "ada->cy", "--as", json.dumps(HOUSE)]), f"{aid}: used\n")

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
        self.assertEqual(ap.meta_of(a), "A wake on loom's machine · from Ada's chat bodies house (or a later one) · “DNS check”")

    def test_an_unnamed_kind_reads_as_words(self):
        a = {"kind": "deploy-gate", "target": "acme/app", "why": "ship", "who": {"agent": "ada", "where": "a body"}}
        self.assertEqual(ap.meta_of(a), "Deploy gate: acme/app · from a body · “ship”")

    def test_the_page_lists_them(self):
        self.ask()
        page = ap.ui_page({"path": ""})
        self.assertEqual(page["title"], "Approvals")
        self.assertTrue(page["sections"][0]["blocks"][0]["items"][0]["meta"].startswith("Waiting on you · A GitHub write"))


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


if __name__ == "__main__":
    unittest.main()
