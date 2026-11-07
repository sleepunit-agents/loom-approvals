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
        self.assertEqual(m["asks"][0]["text"], "(no plain question given) Push the fix to acme/app b")
        self.assertNotIn("<b>", json.dumps(m))
        # In words: the wall (named by APPROVALS_KINDS), the target, where it was asked from (no house id), and why.
        self.assertEqual(m["asks"][0]["meta"], "A GitHub write: acme/app:octocat · from Ada's house, for any of Ada's bodies · “Sam asked for it in #general”")
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
        # Any of the agent's bodies can, wherever it asked from (the owner, 2026-11-07: "if I've given permission it
        # doesn't matter from where or to which body"): here, its chat service's house, once.
        ok = self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"],
                      {"kind": "agent", "agent": "ada", "house": "h-9", "service": "chat"})
        self.assertEqual(ok["body"]["exit"], 0, ok)
        # Once.
        self.assertEqual(self.api(["use", aid, "--kind", "github-write", "--target", "acme/app:octocat"])["body"]["exit"], 1)
        self.assertEqual(self.api(["check", aid])["body"]["stdout"], "used\n")
        events = [json.loads(l)["event"] for l in (Path(self.td.name) / "audit.jsonl").read_text().splitlines()]
        self.assertEqual(events, ["request", "allow", "refused-use", "refused-use", "refused-use", "use"])

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

    def test_another_body_asking_again_gets_the_card_but_doesn_t_reword_it(self):
        aid = self.ask(caller={"kind": "agent", "agent": "ada", "house": "h-8", "service": "chat"})
        r = self.api(["request", "--kind", "github-write", "--target", "acme/app:octocat", "--ask", "OK to push the fix to the app now?",
                      "--why", "It's ready.", "--if-yes", "It goes out.", "--if-no", "It waits."], {"kind": "agent", "agent": "ada", "house": "h-9"})
        self.assertTrue(r["body"]["stdout"].startswith(aid), r)
        self.assertNotIn("ask", ap.get(aid))

    def test_check_json_tells_the_agent_that_asked_exactly_what_to_spend_it_on(self):
        aid = self.ask(caller={"kind": "agent", "agent": "ada", "house": "h-8", "service": "chat"})
        r = self.api(["check", aid, "--json"], {"kind": "agent", "agent": "ada", "house": "h-9"})
        self.assertEqual(r["body"]["exit"], 0, r)
        got = json.loads(r["body"]["stdout"])
        self.assertEqual(got, {"id": aid, "agent": "ada", "state": "pending", "kind": "github-write", "target": "acme/app:octocat", "expires": None})
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
        self.assertEqual(ap.meta_of(a), "A wake on loom's machine · from Ada's chat bodies house, for any of Ada's bodies · “DNS check”")

    def test_an_unnamed_kind_reads_as_words(self):
        a = {"kind": "deploy-gate", "target": "acme/app", "why": "ship", "who": {"agent": "ada", "where": "a body"}}
        self.assertEqual(ap.meta_of(a), "Deploy gate: acme/app · from a body, for any of Ada's bodies · “ship”")

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
        self.assertEqual(row["value"], "Sam asked for it in #general")
        self.assertEqual(row["detail"], "Ada · just now")
        self.assertIn("A GitHub write: acme/app:octocat", row["sheet"]["blocks"][-1]["text"])
        buttons = [b for b in row["sheet"]["blocks"] if b["type"] == "buttons"][0]["items"]
        self.assertEqual([(b["action"], b["label"], b["fields"]) for b in buttons], [("allow", "Allow once", {"id": aid}), ("deny", "Deny", {"id": aid})])
        # No raw id in what people read: only in the record's sheet.
        shown = json.dumps([s for s in page["sections"] if s["id"] != "record"])
        self.assertNotIn(aid, shown.replace(json.dumps({"id": aid})[1:-1], ""))
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
        return ap.api_answer({"caller": caller, "path": "run", "body": {"argv": argv}})["body"]

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
        self.assertEqual(d[T], {"tier": "do", "scope": "github:acme/*"})
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
        return ap.api_answer({"caller": HOUSE, "path": "run", "body": {"argv": argv}})["body"]

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

    def test_in_a_house_a_body_s_request_carries_its_conversation(self):
        seen = {}
        with mock.patch.object(ap, "in_a_house", return_value=True), \
             mock.patch.object(ap, "forward", side_effect=lambda piece, argv: seen.setdefault("argv", argv) and (_ for _ in ()).throw(SystemExit(0))), \
             mock.patch.dict(ap.os.environ, {"LOOM_BODY_CONVERSATION": "cv-00000000000f"}), \
             mock.patch.object(ap.sys, "argv", ["approvals", "request", "--kind", "relay", "--target", "mark"]):
            with self.assertRaises(SystemExit):
                ap.main()
        self.assertEqual(seen["argv"][-2:], ["--conversation", "cv-00000000000f"])

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
        api = lambda argv: ap.api_answer({"caller": HOUSE, "path": "run", "body": {"argv": argv}})["body"]
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
        r = ap.api_answer({"caller": HOUSE, "path": "run", "body": {"argv": ["request", "--kind", "github-write", "--target", "acme/app:octocat",
              "--ask", "OK to push the fix to the app now?", "--why", "It's ready.", "--if-yes", "It goes out.", "--if-no", "It waits.", "--minutes", "30"]}})
        self.assertEqual(r["body"]["exit"], 0)
        line = ap.ui_page({"path": "", "caller": OWNER})["sections"][0]["blocks"][0]["rows"][0]["sheet"]["blocks"][-1]["text"]
        self.assertEqual(line, "Exactly what this allows: A GitHub write: acme/app:octocat, from Ada's house, for any of Ada's bodies. "
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


if __name__ == "__main__":
    unittest.main()
