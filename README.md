# loom-approvals

loom's **approvals** piece: single-use permission for one action.

An agent's body (a session in a house, a bridge, a lane) hits a wall it may
not cross on its own: a guarded GitHub write, a message to an agent the
household doesn't pair it with, a wake that needs the host. It asks. The owner
sees a card on loom's Home, under "Your call", and allows it once or denies it.
The body then spends the approval on exactly the action it named, once.

```
approvals request --kind KIND --target TARGET --ask "…" --why "…" --if-yes "…" --if-no "…" [--details "…"] [--minutes N]
approvals check ID          pending | allowed | denied | used | expired | withdrawn
approvals use ID --kind KIND --target TARGET
approvals list              the recent ones (the owner's view, on loom's machine)
```

- **KIND** is the wall (`github-write`, `relay`, `local-wake`, …), lowercase
  letters, digits and dashes.
- **TARGET** is exactly what it's for (`acme/app:octocat`, `ada->bo`). An
  approval allows that kind for that target and nothing else.
- **--ask** is the one plain question the owner reads, the card's headline,
  shown whole: at most 140 characters, one line, with no ids (a task's, a pull
  request's, an internal one), commit hashes, round numbers, lane words, raw
  site codes (S13) or leading tag (`pond:`). **--why** is at most 240 and the
  if-lines 200; they may name a pull request or a task, never a hash. It's checked by the same lint as a task's ask
  (taskstore `src/plain.ts`), and refused with what's wrong and an example.
  **--why** is one line; **--if-yes** and **--if-no** say what happens either
  way, and they sit beside Allow and Deny. **--details** is longer text for
  the sheet (ids are fine there). The kind and target stay machine fields,
  shown as small print under "Exactly what this allows", never as the
  headline. The old form, **--what** with **--why**, still works, but its card
  starts "(no plain question given)" so its caller gets fixed.
- The words are the body's own, shown to the owner, never what decides. They're shown whole or refused: words that don't fit on
  the card (300 characters, 200 for the target, after Unicode normalizing) are
  never cut, so nothing can hide past the part the owner reads.
- An allowed approval lasts **--minutes** (60 by default, 8 hours at most),
  and is spent by the first `use` that matches it.
- **Who spends it** is the agent that asked, from any of its own bodies: any
  plain house, a body outside a house, or loom's machine acting for it. The
  owner's rule: once allowed, it doesn't matter where it was asked from or
  which of the agent's bodies carries on (the one that asked is usually gone
  by the time the owner answers). A service's house (a chat or bridge house,
  which people other than the owner can steer) spends, re-asks and reads only
  what its own service asked for. Another agent never spends it, and a
  refusal tells it nothing of what the approval is for. What it allows stays
  pinned: that kind, that exact target, once, before it expires, and a later
  deny of the same thing overrules it.
- Asking again for the same thing, while it's pending or allowed, from a body
  that could spend it, is the same card.
- `approvals check ID --json` gives the agent that asked its state and the
  exact kind and target to spend it on, so a parked body's continuation
  (loom's clock, `wake park`) spends it without asking again. Another agent
  gets only the state, from plain `check`. A wake approval (`local-wake`) is
  spent only by the piece that runs it on loom's machine (the clock), never by
  a body's own `use`, which would use up the yes and run nothing.

Nothing here grants by itself: a body may be steered, so only the owner's
Allow on the page makes an approval spendable.

## On whose behalf

The owner's ruling (2026-11-07): his standing rules and his yes cover work an
agent does **for him**. When it acts for someone else (a person in Slack or
Discord, a body they steer), those don't carry over: it needs a grant he set
for that person, or it asks.

- **The principal** is on whose behalf a call is: the owner, a person (their
  slug), or someone loom can't name. It's the runtime's word, never the body's:
  loom's runtime signs a statement of who the body's conversation acts for
  (loom-box `loom runtime principal`), a house's `piece-call` adds it as
  `--principal <statement>`, and the piece checks it with
  `loom runtime principal --verify` (APPROVALS_PRINCIPAL), taking only an
  answer with `ok: true` and exit 0. No statement, one that doesn't verify, or
  one for another agent: someone loom can't name, **never the owner**. A
  statement lasts 5 minutes and the runtime refuses one whose conversation has
  heard someone new since; within that, it's a bearer's for that agent (the
  runtime's README states the limit: one agent's bodies share a house). A
  person whose slug is `owner` is that person, never the owner: unknown here. A body the runtime runs on loom's machine (a local wake)
  fetches its own; any other call on loom's machine is the owner's
  (APPROVALS_LOCAL_PRINCIPAL, `owner` or `unknown`).
- **`rule merge`** reads the standing rules only for the owner's own work. For
  a person, it reads the **grants** the owner set for them (most specific
  wins, as for rules), and with none it asks. For someone loom can't name, it
  asks. Asked on loom's machine for an agent (`--for`, the Changes piece's
  view), each target also lists `asked_for`: the people the agent has a pending
  merge card for, newest first (`{for, name, id}`), so the page can say whom
  it's for and answer with `grant --person` (t-1956).
- **A card names whom it's for**, in the piece's words, first: "Merge
  on-pond#220 for Loren?", or "For Loren: <the ask>" for any other kind. The
  body's own question follows in the sheet.
- **Several people** ("Merge on-pond#221 for Loren and Dan?") are someone loom
  can't name, so no grant applies; the card is theirs, and it's spent only for
  exactly those people.
- **Spent only for them.** An approval asked for Loren is spent only by a body
  acting for Loren (and the agent, the exact target, once, before it expires,
  as before). Asking again is the same card only for the same person. The
  owner's yes for himself isn't one for Loren, and the other way round. A deny
  still wins over every yes for that exact target, whoever it was for.
- **Grants are the owner's**, on the page's "Standing rules" section, in a
  "Merging, for other people" panel beside his own: a person, a scope (a repo,
  a pattern or a project) and a tier. A pending card for one person has
  **Always for <person> on <repo>**, which sets that grant. Nothing in the
  piece API sets one. The Changes piece's `grant` is the owner's own work
  unless it says `--person` (and `--person owner` is refused: that's a person
  by that name). Its `rule merge --for AGENT` is a call on loom's machine like
  any other: the statement if there is one, else APPROVALS_LOCAL_PRINCIPAL, and
  a local body is never the owner for saying `--for`.
- **The clock** files and spends a wake approval for a body with `--as` and
  `--on-behalf <whom the wake is for>`; only a local `--as` call may say that,
  and it's held to it. **The runtime's relay** spends a relay approval with
  `--as` and no `--on-behalf` (it has no conversation to ask about): then
  everything is checked but whom it was for. That's for a relay only: any other
  kind a piece files or spends without `--on-behalf` is someone loom can't name.
  In a house, the bin itself (rarely run there: `piece-call` is) fetches its
  statement with the house's `loom-runtime`, and keeps a supplied one if that
  fails. A relay carries none of the
  owner's authority to the agent it reaches.

## Access to a piece

A body that needs an agent to have one of loom's pieces (Mark asking for Tasks) doesn't ask the owner to go and do
it: it asks for a `grant`, and the owner's Allow does it (t-1970).

```
approvals request --kind grant --target tasks:mark --ask "Give Mark access to Tasks?" --why "…" --if-yes "…" --if-no "…"
```

- **The target** is `<piece>:<agent>`, or `<piece>:<agent>:<path>,<path>` for only those paths (a path, or a prefix
  ending in `/*`, as the runtime's grants take them).
- **The card's headline is the piece's**, from the target ("Give Mark access to Tasks?"), whatever the body wrote;
  its own words are the detail under it. Its Allow says **Allow**, not Allow once: a grant lasts.
- **Only the household owner answers it** (a member who owns some agents is refused, Allow and Deny).
- **Allow applies it.** loom core, right after the owner's Allow, has the grants authority (the runtime) spend it and
  apply it as the owner: the same as `loom runtime grants set`, adding that agent and keeping everyone else. Deny
  records no, and nothing changes.
- **Only the runtime spends it**, on loom's machine (`use --as` the agent that asked); a body's `use` is refused, so
  it can't spend the owner's yes without the grant being applied.
- `state` (core's read) carries a grant's target, so core can hand it to the runtime.

## Whose work it is

Two owner-only actions used to need the owner at a terminal on loom's machine; now he's asked on loom's page, and his
Allow does it (t-1975). Both are filed by the piece that owns the state (never a body: the words on the card are the
piece's), answered only by the household owner, and spent only by that piece, in the step that applies it.

- **`vouch-wake`**, target `<agent>:<w-id>[,<w-id>…]` (1 to 12 wake ids, sorted, each once: one set has one target),
  filed by the clock: "Count these 8 scheduled jobs as your work?", each wake listed (`--details`) with what it's for
  and when it fires. Allow: the clock marks exactly those wakes, still pending and unknown, as the owner's work.
- **`principal-reset`**, target `<agent>:<cv-id>`, filed by the runtime: "Count this conversation as your work from
  now on?", with its label. Allow: the runtime resets whom it acts for, signed as the owner's (`loom runtime principal
  --reset`, without the terminal).
- **The card's headline, "If you allow" and "If you deny" are this piece's**, from the target; its Allow says
  **Allow**. A request for another agent's wakes or conversation is refused.
- **Only the household owner answers** (a member who owns some agents is refused, Allow and Deny). Right after his
  Allow, loom core reads the approval back (`state` carries the target) and posts it to the piece that applies it as
  him. Deny records no, and nothing changes.
- **Never asked twice.** A request naming a wake (or conversation) the owner said no to is refused ("not asked
  again"), and so is one overlapping a card still waiting (withdraw that one first). Both are filed and spent
  `--on-behalf owner`, and spent only with `--as` the agent and `--on-behalf owner`. A body in a house or over the API
  can't file, withdraw or spend one; a body the runtime runs on loom's machine is refused all three while its
  environment is as the runtime set it (either of `LOOM_BODY_CONVERSATION`, `LOOM_BODY_KEY`). Past that, loom's machine is the trust boundary, as for every piece: its bodies share the owner's
  account. A newer No for anything a card covers overrules that card's unspent yes; a card overlapping an allowed one
  that isn't applied yet is refused.
- **At most 12 wakes per card**, each listed with its id, when it fires and what it's for (a card that leaves an id
  out of `--details` is refused); a clock with more files the first 12 and the rest once that's answered.
- **`withdraw ID --as <agent>`**: the filing piece takes back its own pending card, unanswered (the clock refreshing
  its one card with an up-to-date list). Only a kind a piece files, only while pending; never over the API. It shows
  under Answered as "Withdrawn unanswered".

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
approvals grant --kind merge --target <same> --for AGENT --by MEMBER [--person SLUG] [--deny]   loom's machine only
```

- **A scope** is a repo pattern (`github:acme/*`, `github:acme/app`; `*`
  stays inside one path segment) or a project (`project:pond`: every repo
  that project's card lists, read with `loom project list --json`). The most
  specific rule wins (an exact repo, then a project, then fewer `*`), the
  stricter one at a tie, and with no rule a merge asks. When the project list can't be
  read and any project rule exists, only an exact-repo rule still stands;
  anything else asks (it fails closed).
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
| `APPROVALS_KINDS` | none | Words for kinds the page should name, as JSON: `{"github-write": "A GitHub write"}`. `local-wake`, `relay`, `merge`, `grant`, `vouch-wake` and `principal-reset` are built in; any other kind is shown as its own words |
| `APPROVALS_SEED_RULES` | none | The standing rules to start with, as JSON: `[{"kind": "merge", "scope": "github:acme/*", "tier": "do"}]`. Written once, when there are no rules yet |
| `APPROVALS_PROJECTS` | `loom project list --json` | The command that lists project cards, for `project:` rules |
| `APPROVALS_PRINCIPAL` | `loom runtime principal` | The runtime's command for on whose behalf a body acts: `--verify` checks a statement, `--conversation cv` gets a local body's own |
| `APPROVALS_LOCAL_PRINCIPAL` | `owner` | Who a call on loom's machine itself (not a body in a conversation) acts for: `owner` or `unknown` |

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
