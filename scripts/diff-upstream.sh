#!/usr/bin/env bash
# Regenerate the complete diff of our vendored HTSIM against pristine upstream.
# Run this after any change to HTSIM/ so the record stays current.
#
#   ./scripts/diff-upstream.sh
#
# Writes docs/htsim-changes-vs-upstream.patch
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PINNED=$(cat "$HERE/docs/HTSIM_UPSTREAM_COMMIT.txt")
TMP=$(mktemp -d)

echo "cloning pristine upstream at $PINNED ..."
git clone --quiet https://github.com/spcl/HTSIM.git "$TMP/up"
git -C "$TMP/up" checkout --quiet "$PINNED"

diff -ru --exclude=.git --exclude=build --exclude=build-linux \
         --exclude=OUR_CHANGES.md \
         "$TMP/up" "$HERE/HTSIM" > "$HERE/docs/htsim-changes-vs-upstream.patch" || true

echo
echo "files we changed:"
grep "^diff -ru" "$HERE/docs/htsim-changes-vs-upstream.patch" | awk '{print "  " $NF}' | sed "s|$HERE/HTSIM/||"
echo
echo "wrote docs/htsim-changes-vs-upstream.patch"
rm -rf "$TMP"
