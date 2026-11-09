#!/bin/sh
# Smoke test: the approvals piece on its own, with nothing of any agent's machine around it.
# A clean environment, a throwaway HOME and piece home; ask, allow on the "page", spend once.
#
#   sh test/smoke.sh
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
run() { env -i PATH=/usr/local/bin:/usr/bin:/bin HOME="$tmp/home" LOOM_PIECE_HOME="$tmp/piece" "$here/bin/approvals" "$@"; }
fail() { echo "smoke: FAIL: $*" >&2; exit 1; }

run --help | grep -q "approvals request" || fail "--help"

out=$(run request --kind relay --target "agent->other" --what "relay to other" --why "smoke test")
id=${out%%:*}
echo "$id" | grep -Eq '^a-[0-9a-f]{8}$' || fail "request printed: $out"
[ "$(run check "$id")" = pending ] || fail "not pending"

run ui --json | grep -q "\"$id\"" || fail "the manifest has no ask for $id"
echo "{\"action\":\"allow\",\"fields\":{\"id\":\"$id\"}}" | run ui act --json | grep -q '"ok": true' || fail "allow"
[ "$(run check "$id")" = allowed ] || fail "not allowed"

run use "$id" --kind relay --target "agent->wrong" 2>/dev/null && fail "spent for the wrong target"
run use "$id" --kind relay --target "agent->other" | grep -q "used" || fail "use"
run use "$id" --kind relay --target "agent->other" 2>/dev/null && fail "spent twice"
[ "$(run check "$id")" = used ] || fail "not used"

echo '{"caller":{"kind":"agent","agent":"agent","house":"h-1"},"path":"run","body":{"argv":["check","'"$id"'"]}}' \
  | run api --json | grep -q '"stdout": "used\\n"' || fail "api check"
echo '{"caller":{"kind":"owner"},"path":"run","body":{"argv":["list"]}}' | run api --json | grep -q '"status": 403' || fail "api lets the owner in"
echo '{"path":""}' | run ui page --json | grep -q '"title": "Approvals"' || fail "page"

sha=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
[ "$(run rule merge "github:acme/app#1@$sha")" = ask ] || fail "no rule should ask"
g=$(run grant --kind merge --target "github:acme/app#1@$sha" --for agent --by owner)
gid=${g%%:*}
[ "$(run rule merge "github:acme/app#1@$sha")" = "ask allowed $gid" ] || fail "the grant isn't what rule reads"
run use "$gid" --kind merge --target "github:acme/app#1@$sha" | grep -q used || fail "spend the grant"

# Administration cards belong to the controller. Every invocation is a fresh
# process; decision provenance must survive Allow, consumption and rereading.
admin_target=box:fixture:administration:adm-0123456789abcdef0123456789abcdef:0123456789abcdef
admin_as='{"kind":"agent","agent":"agent","house":"runtime"}'
run request --kind box-administration --target "$admin_target" --what "Repair the test box" --why "Isolated smoke" \
  2>/dev/null && fail "body filed an administration card"
admin_out=$(run request --kind box-administration --target "$admin_target" --what "Repair the test box" --why "Isolated smoke" \
  --as "$admin_as" --on-behalf owner)
admin_id=${admin_out%%:*}
echo '{"action":"allow","caller":{"kind":"owner","who":"fixture-owner"},"fields":{"id":"'"$admin_id"'"}}' \
  | run ui act --json | grep -q '"ok": true' || fail "administration Allow"
run check "$admin_id" --json --as "$admin_as" | grep -q '"decidedBy": "fixture-owner"' || fail "missing actual decider"
run use "$admin_id" --kind box-administration --target "$admin_target" 2>/dev/null && fail "body consumed administration"
[ "$(run check "$admin_id")" = allowed ] || fail "refused body spend changed approval"
run use "$admin_id" --kind box-administration --target "$admin_target" --as "$admin_as" --on-behalf owner \
  | grep -q used || fail "runtime consumption"
run check "$admin_id" --json --as "$admin_as" | grep -q '"decidedBy": "fixture-owner"' || fail "lost decider after consumption"
[ "$(run check "$admin_id")" = used ] || fail "administration was not consumed"

[ -f "$tmp/piece/data/approvals.json" ] || fail "the store isn't under the piece home"
[ ! -e "$tmp/home/.local/state/loom-approvals" ] || fail "wrote outside the piece home"
echo "smoke: ok ($id asked, allowed, spent once; store in \$LOOM_PIECE_HOME/data)"
