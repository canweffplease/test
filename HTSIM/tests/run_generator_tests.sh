#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DF_GEN="$ROOT_DIR/htsim/sim/datacenter/topologies/dragonfly/generate_dragonfly_assets.py"
SF_GEN="$ROOT_DIR/htsim/sim/datacenter/topologies/slimfly/generate_slimfly_assets.py"
DF_BIN="$ROOT_DIR/htsim/sim/datacenter/htsim_uec_df"
SF_BIN="$ROOT_DIR/htsim/sim/datacenter/htsim_uec_sf"

REF_DF="$ROOT_DIR/htsim/sim/datacenter/topologies/dragonfly/p2a4h2"

PASS=0
FAIL=0

fail() {
    echo "FAIL: $1"
    FAIL=$((FAIL + 1))
}

pass() {
    echo "PASS: $1"
    PASS=$((PASS + 1))
}

require_file() {
    local path="$1"
    local msg="$2"
    if [[ -f "$path" ]]; then
        pass "$msg"
    else
        fail "$msg (missing: $path)"
    fi
}

require_dir() {
    local path="$1"
    local msg="$2"
    if [[ -d "$path" ]]; then
        pass "$msg"
    else
        fail "$msg (missing: $path)"
    fi
}

echo "=== Generator tests (small + end-to-end) ==="

if ! python3 -c "import networkx, sympy" >/dev/null 2>&1; then
    echo "ERROR: missing Python dependencies. Install with: pip install networkx sympy"
    exit 1
fi

if [[ ! -x "$DF_BIN" || ! -x "$SF_BIN" ]]; then
    echo "ERROR: missing simulator binaries. Build HTSIM first (cmake --build ...)."
    exit 1
fi

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

echo "Using temp directory: $TMP_DIR"

DF_OUT="$TMP_DIR/dragonfly_p2a1h1"
DF_LOG="$TMP_DIR/dragonfly_compare.log"
python3 "$DF_GEN" -p 2 -a 1 -H 1 --out "$DF_OUT" >/dev/null

require_file "$DF_OUT/dragonfly.topo" "Dragonfly generator writes dragonfly.topo"
require_file "$DF_OUT/dragonfly.adjlist" "Dragonfly generator writes dragonfly.adjlist"
require_dir "$DF_OUT/host_table" "Dragonfly generator writes host_table directory"
require_file "$DF_OUT/host_table/0.lt" "Dragonfly host table includes switch 0"
require_file "$DF_OUT/host_table/1.lt" "Dragonfly host table includes switch 1"

if [[ -d "$REF_DF/host_table" ]]; then
    python3 "$DF_GEN" -p 2 -a 4 -H 2 --out "$TMP_DIR/dragonfly_p2a4h2" --compare-ref "$REF_DF" >"$DF_LOG"
fi

if [[ ! -d "$REF_DF/host_table" ]]; then
    pass "Dragonfly compare-ref skipped (no host_table in $REF_DF)"
elif grep -q "generated pairs subset of reference: yes" "$DF_LOG" && grep -q "average generated-pair coverage in reference: 1.000" "$DF_LOG"; then
    pass "Dragonfly generator matches reference host-table coverage"
else
    fail "Dragonfly compare-ref coverage check"
fi

cat >"$TMP_DIR/df_1flow.tm" <<'EOF'
Nodes 4
Connections 1
0->3 start 0 size 4096
EOF

"$DF_BIN" -basepath "$DF_OUT" -routing SOURCE -tm "$TMP_DIR/df_1flow.tm" >"$TMP_DIR/df_run.out" 2>&1 || true
if grep -q "Done" "$TMP_DIR/df_run.out" && [[ "$(grep -c 'finished at' "$TMP_DIR/df_run.out")" -eq 1 ]]; then
    pass "Dragonfly SOURCE end-to-end run succeeds with generated assets"
else
    fail "Dragonfly SOURCE end-to-end run"
fi

SF_OUT="$TMP_DIR/slimfly_p2q3"
python3 "$SF_GEN" -p 2 -q 3 --out "$SF_OUT" -n 1 >/dev/null

require_file "$SF_OUT/slimfly.topo" "SlimFly generator writes slimfly.topo"
require_file "$SF_OUT/slimfly.adjlist" "SlimFly generator writes slimfly.adjlist"
require_dir "$SF_OUT/fib" "SlimFly generator writes fib directory"
require_dir "$SF_OUT/host_table" "SlimFly generator writes host_table directory"

FIB_COUNT="$(ls "$SF_OUT"/fib/*.fib 2>/dev/null | wc -l | tr -d ' ')"
HT_COUNT="$(ls "$SF_OUT"/host_table/*.lt 2>/dev/null | wc -l | tr -d ' ')"
if [[ "$FIB_COUNT" -eq 18 && "$HT_COUNT" -eq 18 ]]; then
    pass "SlimFly generator writes expected fib/host_table file counts for q=3"
else
    fail "SlimFly output counts (fib=$FIB_COUNT host_table=$HT_COUNT, expected 18/18)"
fi

cat >"$TMP_DIR/sf_1flow.tm" <<'EOF'
Nodes 36
Connections 1
0->35 start 0 size 4096
EOF

"$SF_BIN" -topo "$SF_OUT" -routing SOURCE -tm "$TMP_DIR/sf_1flow.tm" >"$TMP_DIR/sf_run.out" 2>&1 || true
if grep -q "Done" "$TMP_DIR/sf_run.out" && [[ "$(grep -c 'finished at' "$TMP_DIR/sf_run.out")" -eq 1 ]]; then
    pass "SlimFly SOURCE end-to-end run succeeds with generated assets"
else
    fail "SlimFly SOURCE end-to-end run"
fi

echo "========================================="
echo "Generator tests: PASS=$PASS FAIL=$FAIL"
echo "========================================="

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
