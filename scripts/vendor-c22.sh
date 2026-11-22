#!/usr/bin/env bash
# Vendor C22 (look) into the GUI — a copy in the repo instead of `pip install c22`.
#
# Why: C22's compiled style packs are build outputs (gitignored), and the release wheel (v0.3.0)
# ships no compiled CSS either; the GUI would render unstyled without an error. So: a copy,
# reproducible with this script, with its origin recorded in ORIGIN — the same pattern C22 uses
# for Basecoat and FleetCommander/paperlaiss use for C22.
#
# Usage:  scripts/vendor-c22.sh [path-to-c22-checkout] [pack]
set -euo pipefail
cd "$(dirname "$0")/.."

source_dir="${1:-../C22}"
pack="${2:-vega}"
target="src/kassist/gui/static/c22"

[ -d "$source_dir/.git" ] || { echo "no C22 checkout at $source_dir" >&2; exit 1; }
css="$source_dir/c22/static/css/c22-$pack.css"
[ -f "$css" ] || { echo "pack '$pack' not built ($css missing) - run scripts/build-gallery.sh in C22" >&2; exit 1; }
for src in "$source_dir"/c22/static/css/{tokens,components,input}.css; do
  if [ "$src" -nt "$css" ]; then
    echo "$(basename "$src") is newer than the built pack - run scripts/build-gallery.sh in C22 first" >&2
    exit 1
  fi
done

mkdir -p "$target/css" "$target/js" "$target/fonts"
cp "$css" "$target/css/c22.css"
cp "$source_dir/c22/static/js/basecoat.all.min.js" "$source_dir/c22/static/js/c22.js" "$source_dir/c22/static/js/icons.js" "$target/js/"
cp "$source_dir/c22/static/fonts/inter.woff2" "$source_dir/c22/static/fonts/OFL.txt" "$target/fonts/"

version="$(git -C "$source_dir" describe --tags --always)"
sha="$(git -C "$source_dir" rev-parse HEAD)"
cat > "$target/ORIGIN" <<ORIGIN
C22 - shared design system (look). Copy, do not edit.

Version: $version
Commit:  $sha
Pack:    $pack
Fetched: $(date -I) by scripts/vendor-c22.sh

Contains: css/c22.css (pack), js/basecoat.all.min.js, js/c22.js, js/icons.js (Phosphor, MIT), fonts/inter.woff2
(Inter, SIL Open Font License 1.1 - licence text in fonts/OFL.txt). C22 itself: MIT.

Changes to the look belong in C22, not here: a fix in this copy is gone after the next vendor run.
ORIGIN
echo "C22 $version ($pack) vendored to $target"
