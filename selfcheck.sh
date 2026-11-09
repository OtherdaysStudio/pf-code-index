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

# --- nothing outside the project reaches the index, and the project holds no index ---
S=$(mktemp -d); printf '# token ghp_LIVE7788 for deploy\nAPI_KEY = "sk-live-9931"\n' > "$S/.env.py"
# 1. a symlinked source file must never be read
ln -s "$S/.env.py" src/Config.py
pf -i > /dev/null
pf config token api | grep -q "ghp_LIVE7788" && fail "a symlinked file leaked into a query"
pf -s src/Config.py:1 | grep -q "outside the project" || fail "a symlinked path was not refused"
rm src/Config.py
# 2. a path that leaves the project must be refused, not read
pf -s "../../../../../../$S/.env.py:1" | grep -q "outside the project" || fail "path traversal not refused"
# 3. pf must write nothing into the project
pf -i > /dev/null
test -e .pf && fail "pf wrote an index into the project"
# 4. an index the repo ships must be ignored, even a well-formed one
IDX=$(pf -i | awk '{print $1}')
mkdir -p .pf; sed 's/WidgetCard/FAKESYMBOL/g' "$IDX" > .pf/idx.tsv
pf fakesymbol | grep -q "FAKESYMBOL" && fail "a shipped index was trusted"
pf widget card | grep -q "WidgetCard:2" || fail "the real code was not indexed"
rm -rf .pf "$S"
echo "pf selfcheck: 11/11 pass"
