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
echo "pf selfcheck: 7/7 pass"
