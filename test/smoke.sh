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

[ -f "$tmp/piece/data/approvals.json" ] || fail "the store isn't under the piece home"
[ ! -e "$tmp/home/.local/state/loom-approvals" ] || fail "wrote outside the piece home"
echo "smoke: ok ($id asked, allowed, spent once; store in \$LOOM_PIECE_HOME/data)"
