#!/bin/sh
# pf self-check: build, query, auto-refresh on change, slice, refs. Exits 0 when all pass.
set -e
T=$(mktemp -d)/proj; mkdir -p "$T/src"; cd "$T"; git init -q .
cat > src/Widget.swift <<'EOF'
/// The widget that holds the strip together.
struct WidgetCard: View {
    var title: String
    func press() { Strip.move() }
}
EOF
cat > src/Strip.swift <<'EOF'
/// Moves the strip.
enum Strip { static func move() {} }
EOF
fail() { echo "FAIL: $1"; exit 1; }
pf -i > /dev/null
pf widget card | grep -q "WidgetCard:2" || fail "query did not find WidgetCard"
pf widget card uses press | grep -q "holds the strip" || fail "purpose line missing in the full shape"
pf strip move uses | grep -q "used Widget.swift:4" || fail "usage edge missing in the full shape"
# change a file, then query WITHOUT -i: the index must refresh itself
cat >> src/Strip.swift <<'EOF'
struct StripRuler: View { var ticks: Int = 3 }
EOF
pf strip ruler | grep -q "StripRuler:3" || fail "index did not refresh after an edit"
pf -s src/Strip.swift:2 | grep -q "enum Strip" || fail "slice wrong"
pf -r WidgetCard | grep -q "Widget.swift:2" || fail "refs wrong"
pf -t | grep -q "SCREENS" || fail "overview missing screens"

# --- a symlink must never pull content from outside the project ---
S=$(mktemp -d); echo "AWS_SECRET=hunter2" > "$S/secret.txt"
# 1. a symlinked source file must never be read into the index
ln -s "$S/secret.txt" src/Leak.py
pf -i > /dev/null
grep -rq "hunter2" .pf/ && fail "a symlinked file leaked into the index"
pf leak secret | grep -q "hunter2" && fail "a symlinked file leaked into a query"
rm src/Leak.py
# 2. an index path that leaves the project must be refused, not read
pf -s "../../../../../../$S/secret.txt:1" | grep -q "outside the project" || fail "path traversal not refused"
# 3. .pf as a symlink must stop the write, not redirect it
rm -rf .pf; ln -s "$S" .pf
pf -i 2>&1 | grep -q "refusing" || fail "a symlinked .pf did not stop the build"
test -f "$S/idx.tsv" && fail "the build wrote through a symlinked .pf"
rm .pf
# 4. idx.tsv as a symlink must stop the write too
mkdir .pf; ln -s "$S/secret.txt" .pf/idx.tsv
pf -i 2>&1 | grep -q "refusing" || fail "a symlinked idx.tsv did not stop the build"
grep -q "hunter2" "$S/secret.txt" || fail "the build overwrote a file through a symlink"
rm -rf .pf "$S"
echo "pf selfcheck: 11/11 pass"
