#!/usr/bin/env bash
# The gate before every push and in CI: repo hygiene, unit tests in a fresh venv, packaging smoke test.
#
#   scripts/check.sh
#   PYTHON=3.14 scripts/check.sh      # another interpreter (version or path, resolved by uv)
#
# Dependencies are installed into a throw-away venv under $TMPDIR (network only for that step:
# PyPI and the pinned kleinanzeigen-bot commit on GitHub). The tests themselves never go online,
# never start Chrome and never read the real ~/.config/kassist (tests/conftest.py).
#
# Repeatable: venv and caches live outside the tree, no bytecode or pytest cache lands in it.
# Run it twice in a row; both runs must be green.
set -euo pipefail
cd "$(dirname "$0")/.."

export PYTHONDONTWRITEBYTECODE=1

step() { printf '\n\033[1m▸ %s\033[0m\n' "$1"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
fail() { printf '\n\033[31m✗ %s\033[0m\n' "$1" >&2; exit 1; }

# Hygiene runs on any python3 (stdlib only); the package needs >= 3.13, uv finds or fetches it.
PY="$(command -v python3 || true)"
[[ -n "$PY" ]] || fail "python3 missing"
TARGET="${PYTHON:-3.13}"

# Text fast path (repokit): a change of only allow-listed text runs only its own check.
if grund="$(scripts/_nur_text.sh)"; then
    step "$grund"
    "$PY" tests/test_repo.py || fail "repo hygiene"
    printf '\n\033[32m✓ Text only - hygiene green\033[0m\n'
    exit 0
fi
step "$grund"

# Unused dependencies (kit 0.73.0, PO 2026-11-27): the app carries only what it uses. Allow list with reasons:
# .github/abhaengigkeiten.json. Fetches pinned tools (deptry, cargo-machete), so it runs here, not in the hook.
step "Unused dependencies (kit, scripts/_abhaengigkeiten.py)"
"$PY" scripts/_abhaengigkeiten.py || fail "unused dependencies (scripts/_abhaengigkeiten.py)"

# ---------------------------------------------------------------- hygiene
step "Repo hygiene"
"$PY" tests/test_repo.py || fail "repo hygiene"

if command -v shellcheck >/dev/null 2>&1; then
    mapfile -t shfiles < <(git ls-files '*.sh' .githooks/pre-push)
    shellcheck "${shfiles[@]}" || fail "shellcheck"
    ok "shellcheck: ${#shfiles[@]} script(s) clean"
elif [[ -n "${CI:-}" ]]; then
    fail "shellcheck missing - in CI that is red, not skipped"
else
    printf '  \033[33m-\033[0m shellcheck not installed (skipped, local only)\n'
fi

# ---------------------------------------------------------------- environment
command -v uv >/dev/null 2>&1 || fail "uv missing (installs the throw-away venv)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/kassist-check.XXXXXX")"
trap 'rm -rf -- "$WORK"' EXIT

step "Throw-away venv ($TARGET)"
uv venv -q --python "$TARGET" "$WORK/venv" || fail "uv venv --python $TARGET"
VPY="$WORK/venv/bin/python"
# Not editable: the package is installed as a wheel, like a user gets it, so missing package data
# (templates, static files) shows up below.
uv pip install -q --python "$VPY" ".[test]" || fail "uv pip install .[test]"
ok "$("$VPY" -V) in $WORK/venv"

# ---------------------------------------------------------------- tests
step "Unit tests (pytest collects tests/)"
"$VPY" -m pytest -q -p no:cacheprovider tests || fail "unit tests"

# ---------------------------------------------------------------- packaging smoke test
step "Installed package (outside the source tree)"
(cd "$WORK" && "$VPY" - <<'PY') || fail "installed package incomplete"
import pathlib
import kassist

pkg = pathlib.Path(kassist.__file__).parent
assert "site-packages" in str(pkg), f"imported from the source tree: {pkg}"
for rel in ("gui/templates/base.html", "gui/static/icon.svg", "gui/static/c22/css/c22.css",
            "gui/static/c22/fonts/inter.woff2", "listing_template.yaml"):
    assert (pkg / rel).is_file(), f"missing in the wheel: {rel}"
print(f"  kassist {kassist.__version__} from {pkg}")
PY
(cd "$WORK" && "$WORK/venv/bin/kassist" --help >/dev/null) || fail "kassist --help"
ok "kassist --help"

printf '\n\033[32m✓ All green\033[0m\n'
