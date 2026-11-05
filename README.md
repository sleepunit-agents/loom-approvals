# loom-approvals

loom's **approvals** piece: single-use permission for one action.

An agent's body (a session in a house, a bridge, a lane) hits a wall it may
not cross on its own: a guarded GitHub write, a message to an agent the
household doesn't pair it with, a wake that needs the host. It asks. The owner
sees a card on loom's Home, under "Your call", and allows it once or denies it.
The body then spends the approval on exactly the action it named, once.

```
approvals request --kind KIND --target TARGET --what "…" --why "…" [--minutes N]
approvals check ID          pending | allowed | denied | used | expired
approvals use ID --kind KIND --target TARGET
approvals list              the recent ones (the owner's view, on loom's machine)
```

- **KIND** is the wall (`github-write`, `relay`, `local-wake`, …), lowercase
  letters, digits and dashes.
- **TARGET** is exactly what it's for (`acme/app:octocat`, `ada->bo`). An
  approval allows that kind for that target and nothing else.
- **--what** and **--why** are the body's own words, shown to the owner,
  never what decides. They're shown whole or refused: words that don't fit on
  the card (300 characters, 200 for the target, after Unicode normalizing) are
  never cut, so nothing can hide past the part the owner reads.
- An allowed approval lasts **--minutes** (60 by default, 8 hours at most),
  and is spent by the first `use` that matches it.
- **Who spends it** must match who asked: the same agent, from the same kind
  of place (a house, loom's machine, or a body outside a house) and the same
  service. Only *which* house is relaxed, because houses are short-lived and
  the one that asked is usually gone by the time the owner answers.
- Asking again for the same thing, while it's pending or allowed, is the same
  card.

Nothing here grants by itself: a body may be steered, so only the owner's
Allow on the page makes an approval spendable.

## Standing rules

Most merges don't need asking. A **standing rule** is the owner's lasting
answer for a scope (loom `docs/specs/changes.md`):

| Tier | Words on the page | What the agent does |
|---|---|---|
| `do` | Just do it | merges once CI is green and review is clean, without asking |
| `tell` | Tell me after | merges the same way, then `approvals tell` (an FYI on loom's Inbox) |
| `ask` | Ask me | asks for a `merge` approval, waits for it, then spends it |

```
approvals rule merge github:acme/app#12@<40-hex head>    do | tell | ask [pending|allowed|denied a-…]
approvals tell --kind merge --target <same> --what "…"
approvals grant --kind merge --target <same> --for AGENT --by MEMBER [--deny]   loom's machine only
```

- **A scope** is a repo pattern (`github:acme/*`, `github:acme/app`; `*`
  stays inside one path segment) or a project (`project:pond`: every repo
  that project's card lists, read with `loom project list --json`). The most
  specific rule wins (an exact repo, then a project, then fewer `*`), the
  stricter one at a tie, and with no rule a merge asks. With a project rule in play
  and the project list unreadable, it asks (it fails closed).
- **A merge approval** is for one change at one head:
  `<forge>:<locator>#<n>@<head>`. A push changes the head, so an old Yes no
  longer matches and `use` refuses it.
- **Only the owner edits rules**, on the page's "Standing rules" panel (the
  household's owner; a member who owns some agents doesn't see it). A pending
  merge approval's sheet has **Always for <project>** beside Allow once and
  Deny: it sets that project's rule to Just do it and allows this one. The
  piece API has no way to change a rule.
- **`grant`** is how the Changes piece passes on the owner's **Yes, merge
  it** and **Not yet**, before the agent has asked: it answers the agent's
  pending request for that target, or makes the answer the agent's request
  will find. A grant lasts 24 hours and is spent by that agent from any house
  or loom's machine. It's refused over the API, so a body can't grant itself.
- **The seed.** `APPROVALS_SEED_RULES` (JSON) is written as the first rules
  when `rules.json` doesn't exist yet, and never again.
- A rule never grants a write a guard would refuse: it only says whether to
  ask the owner.

## Install

It's a loom piece: an npm package with a `loom-approvals` bin and a `loom`
block in `package.json` (loom `docs/extensions.md`, `docs/specs/pieces-install.md`).

```
loom pieces install --from github:sleepunit-agents/loom-approvals#<40-char commit> --yes
```

It also installs as `approvals`, the name bodies type. It needs Python 3.9+
and nothing else (no npm dependencies).

Unmanaged, any `loom-approvals` on `PATH` works too: clone the repo and link
`bin/approvals` as `loom-approvals`.

## Settings

| Setting | Default | What it is |
|---|---|---|
| `APPROVALS_DIR` | `${LOOM_PIECE_HOME}/data` | Where the store lives: `approvals.json` (0600) and `audit.jsonl`. Unmanaged, without `LOOM_PIECE_HOME`: `~/.local/state/loom-approvals` |
| `APPROVALS_LOCAL_AGENT` | `agent` | The agent a request made on loom's machine itself (not from a house) is filed as |
| `APPROVALS_KINDS` | none | Words for kinds the page should name, as JSON: `{"github-write": "A GitHub write"}`. `local-wake` and `relay` (the clock's) are built in; any other kind is shown as its own words |
| `APPROVALS_SEED_RULES` | none | The standing rules to start with, as JSON: `[{"kind": "merge", "scope": "github:acme/*", "tier": "do"}]`. Written once, when there are no rules yet |
| `APPROVALS_PROJECTS` | `loom project list --json` | The command that lists project cards, for `project:` rules |

A managed piece gets its settings from loom. Run directly by the bin loom
linked (`$LOOM_HOME/pieces/bin/approvals`, a body typing `approvals`), it
finds its piece home from where it's installed and reads the owner's
`settings.json` there, so every way in reads the same store.

## How it composes with loom

- **The page.** `ui --json` puts each pending approval under "Your call" on
  loom's Home (an Inbox `permission`, with *Allow once* and *Deny*) and the
  answered ones in its history, each linking to the piece's page. The manifest's
  `tab` (`Approvals`, under Setup, its tile a `stamp`) is what makes that page exist at
  `/p/approvals`. `ui page --json` is an app page: what's waiting first, each a
  row whose sheet holds *Allow once* and *Deny*; then what was answered, each
  body's words whole, with how it went, who decided and when, in words; raw ids
  only behind *The record*.
  `ui act --json` takes Allow and Deny, and records who answered when loom
  names the household member (`caller.who`).
- **The piece API.** `api --json` (loom's `POST /api/approvals/run`) is how a
  body in a house asks: `{"argv": ["request", …]}`, `check` and `use`, as the
  house's own token. Only an agent's bodies reach it; the owner answers on
  the page, never through the API, and `list` is the owner's, on loom's
  machine.
- **A Needs you that waits on one.** An agent's ask in its conversation
  digest can say it waits on an approval (`waits_on: {"approval": "a-…"}`,
  loom `docs/specs/conversations.md`). loom's Inbox then shows that card's
  *Allow once* and *Deny* inside the ask, posted here as the card's would be
  (one item, not two), and clears the ask once the approval is answered. It
  reads that through `api --json` path `state` (`{"ids": ["a-…"]}`, the
  owner's caller only): each known id's state, agent and kind, no words. An
  id it has no record of is left out.
- **In a house** (a container: `/run/.containerenv`, or `HOUSE_ID`), the
  command forwards itself to loom's piece API with `LOOM_URL` and
  `LOOM_TOKEN_FILE` (default `/run/secrets/loom-token`), so the store is
  never a house's own copy. It never answers as the piece there.
- **Another piece acting for a body.** The clock piece files and spends a
  house's approval on loom's machine with `--as '<caller JSON>'`. Only a
  local call may say `--as`; over the API it's refused, so a body can't
  claim to be another.
- **Absent,** nothing breaks: a piece that would have asked gets a clear
  error, and loom's page has no Approvals tab.

## Moving an existing store in

The store is plain files (`approvals.json`, `rules.json`, `audit.jsonl`). To move an unmanaged store into the piece's home:

```
loom pieces install --from github:sleepunit-agents/loom-approvals#<commit> --yes
dest="${LOOM_HOME:-$HOME/.config/loom}/pieces/approvals/data"
mkdir -p "$dest" && chmod 700 "$dest"
cp -p ~/.local/state/loom-approvals/approvals.json ~/.local/state/loom-approvals/audit.jsonl "$dest"/
```

Or point `APPROVALS_DIR` at the old directory and leave it where it is.

## Tests

```
npm test            # python3 test/test_approvals.py && sh test/smoke.sh
```

`test/smoke.sh` runs the bin in a clean environment (`env -i`, a throwaway
`HOME` and `LOOM_PIECE_HOME`): ask, allow on the page, spend once, refuse a
second spend and a wrong target, the API, the page.

## License

AGPL-3.0-or-later, like loom and its other pieces. See [LICENSE](LICENSE).
