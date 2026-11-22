#!/usr/bin/env python3
"""Repo hygiene from the shared kit (repokit), not by hand.

Required files, version parity, no artefacts, no secrets, no private infrastructure, hardened
workflows, backlog structure. The repository is public; the rules must not depend on good intentions.

Runs on the system interpreter with the standard library only; `scripts/check.sh` calls it directly
(pytest skips it, see `tests/conftest.py`). Exempted kit checks carry their reason in the call:
leaving one out is allowed, hiding it is not.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _kit import backlog, hygiene, manifest  # noqa: E402
from _kit.report import Report  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
r = Report("Repo hygiene")

POLICY = hygiene.lade_policy()
PROJEKTE = ["kleinanzeigen-assistant", "kassist"]
DATEIEN = hygiene.getrackte_dateien(str(ROOT))

# ---- The vendored kit is unchanged: every check below comes from it.
_kit_drift = manifest.pruefe(str(ROOT))
r.check(f"tests/_kit unchanged (kit {manifest.version(str(ROOT))}; otherwise: repokit sync .)",
        not _kit_drift, " | ".join(_kit_drift[:3]))

# ---- Does the suite see every file?
_liste = hygiene.pruefe_dateiliste_plausibel(DATEIEN, root=str(ROOT))
r.check(f"file list complete ({len(DATEIEN)} tracked files)", not _liste, " | ".join(_liste[:3]))

# ---- Required files (bilingual where readers need them)
PFLICHT = [
    "README.md", "i18n/README.de.md", "LICENSE", "CHANGELOG.md", "THIRD_PARTY_NOTICES.md",
    "CONTRIBUTING.md", "i18n/CONTRIBUTING.de.md", "SECURITY.md", "i18n/SECURITY.de.md",
    "CODE_OF_CONDUCT.md", "i18n/CODE_OF_CONDUCT.de.md",
    "pyproject.toml", ".ci-image", ".ci-network", ".gitignore",
    "scripts/check.sh", "scripts/_residue_check.sh", "scripts/_nur_text.sh", ".githooks/pre-push",
    ".github/workflows/ci.yml", ".github/workflows/release.yml", ".github/dependabot.yml",
    "tests/_kit/hygiene.py", "tests/_kit/backlog.py", "tests/_kit/manifest.py",
    "scripts/_backlog.py", "backlog/README-KONVENTION.md", "docs/kassist.png",
]
fehlt = hygiene.pruefe_pflichtdateien(str(ROOT), PFLICHT)
r.check("all required files present", not fehlt, " | ".join(fehlt))

# ---- The licence is the official AGPL text, and pyproject says so
_lic = (ROOT / "LICENSE").read_text(encoding="utf-8")
r.check("LICENSE is the GNU AGPL v3 text",
        _lic.lstrip().startswith("GNU AFFERO GENERAL PUBLIC LICENSE") and "Version 3, 19 November 2007" in _lic)
_pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
r.check("pyproject license = AGPL-3.0-or-later",
        re.search(r'^license\s*=\s*"AGPL-3\.0-or-later"', _pyproject, re.M) is not None)

# ---- No private infrastructure, no personal data
treffer = hygiene.pruefe_private_infrastruktur(str(ROOT), DATEIEN, POLICY, PROJEKTE)
r.check(f"no private infrastructure ({len(POLICY['private_muster'])} patterns"
        f" + {len(POLICY['private_namen_sha256_16'])} names)",
        not treffer, " | ".join(sorted(set(treffer))[:4]))

# Reviewed hosts: the services kassist talks to (Kleinanzeigen web, gateway and login; the Mistral
# API), README badges (img.shields.io), the official AGPL text (gnu.org, fsf.org), and the projects
# named in vendored third-party files (Lucide, Phosphor, Tailwind, SIL).
adressen = hygiene.pruefe_adressen(str(ROOT), DATEIEN, POLICY, zusaetzliche_hosts=[
    r"(?:www\.|gateway\.|login\.)?kleinanzeigen\.de", r"api\.mistral\.ai",
    r"img\.shields\.io", r"(?:www\.)?gnu\.org", r"fsf\.org",
    r"lucide\.dev", r"phosphoricons\.com", r"tailwindcss\.com", r"scripts\.sil\.org"])
r.check("only neutral example addresses", not adressen, " | ".join(sorted(set(adressen))[:4]))

# Reviewed bare names: Kleinanzeigen hosts kassist recognizes in the browser (portal.py), the Mistral
# console named in a help text, the Lucide credit, the kit's own sources, and three template/JS keys
# that end in ".ai" (the AI buttons) and only look like host names to the check.
blank = hygiene.pruefe_blanke_adressen(str(ROOT), DATEIEN, POLICY, grundstock=[
    "kleinanzeigen.de", "login.kleinanzeigen.de", "console.mistral.ai", "lucide.dev",
    "python.org", "devguide.python.org",           # sources named in the kit's python_matrix.json
    "btn.dataset.ai", "item.import.ai", "tab.ai",  # template/JS keys, not hosts
])
r.check("no unreviewed bare host names", not blank, " | ".join(blank[:4]))

lecks = hygiene.pruefe_geheimnisse(str(ROOT), DATEIEN, POLICY)
r.check("no secrets in plain text", not lecks, " | ".join(lecks[:3]))

# ---- Version: pyproject, CHANGELOG and the package agree
versionsfehler = hygiene.pruefe_versionsgleichstand(
    str(ROOT), {"src/kassist/__init__.py": r'^__version__ = "([^"]+)"'})
_version = re.search(r'^version\s*=\s*"([^"]+)"', _pyproject, re.M).group(1)
r.check(f"version {_version}: pyproject, CHANGELOG, __init__ and SemVer agree",
        not versionsfehler, " | ".join(versionsfehler))

# ---- No artefacts
artefakte = hygiene.pruefe_artefakte(DATEIEN, POLICY)
r.check("no generated artefacts tracked", not artefakte, " | ".join(artefakte[:3]))
r.check("no .env tracked", not [f for f in DATEIEN if Path(f).name.endswith(".env")])

# ---- Workflows: documented standards, enforced
ungepinnt = hygiene.pruefe_actions_sha_gepinnt(str(ROOT), DATEIEN)
r.check("actions pinned by commit SHA", not ungepinnt, " | ".join(ungepinnt[:3]))
ohne_rechte = hygiene.pruefe_workflow_permissions(str(ROOT), DATEIEN)
r.check("every workflow sets `permissions:`", not ohne_rechte, " | ".join(ohne_rechte[:3]))
runner = hygiene.pruefe_kein_self_hosted_runner(str(ROOT), DATEIEN)
r.check("no self-hosted runner (public repo)", not runner, " | ".join(runner[:3]))
_cip = hygiene.pruefe_kein_abbruch_auf_default_branch(str(ROOT), DATEIEN)
r.check("no unconditional cancel-in-progress on main", not _cip, " | ".join(_cip[:3]))
_pc = hygiene.pruefe_persist_credentials(str(ROOT), DATEIEN)
r.check("every actions/checkout sets `persist-credentials: false`", not _pc, " | ".join(_pc[:3]))
_vt = hygiene.pruefe_veroeffentlichen_am_tag(str(ROOT))
r.check("in tag workflows every publish step depends on the tag", not _vt, " | ".join(_vt[:3]))
_au = hygiene.pruefe_audit_workflow(str(ROOT), DATEIEN)
r.check("vulnerability gate is sharp (pip-audit, hashed tools, no escape hatch)", not _au, " | ".join(_au[:3]))
_pw = hygiene.pruefe_parallel_worker(str(ROOT), DATEIEN)
r.check("parallel worker count from CI_KERNE", not _pw, " | ".join(_pw[:3]))

# ---- Python matrix: one source (tests/_kit/python_matrix.json)
_mx = hygiene.pruefe_python_matrix(str(ROOT), DATEIEN)
r.check("ci.yml matrix equals the kit's Python matrix", not _mx, " | ".join(_mx[:3]))
_rp = hygiene.pruefe_requires_python(str(ROOT))
r.check("requires-python names the lower end of the matrix", not _rp, " | ".join(_rp[:3]))
_rr = hygiene.pruefe_python_matrix_regel()
r.check("kit matrix agrees with the rolling rule", not _rr, " | ".join(_rr[:3]))

# ---- Docs
kategorien = hygiene.pruefe_changelog_kategorien(str(ROOT), POLICY)
r.check("CHANGELOG uses Keep a Changelog categories", not kategorien, " | ".join(kategorien[:2]))
uebersetzung = hygiene.pruefe_uebersetzungs_struktur(str(ROOT), [("README.md", "i18n/README.de.md")])
r.check("i18n/README.de.md follows the structure of README.md", not uebersetzung, " | ".join(uebersetzung[:2]))
_tx = hygiene.pruefe_texte(str(ROOT), [d for d in DATEIEN if d.endswith(".md")])
r.check("texts: links, anchors, front matter", not _tx, " | ".join(_tx[:3]))
_nt = hygiene.pruefe_nur_text(str(ROOT))
r.check("text fast path wired into check.sh", not _nt, " | ".join(_nt[:3]))

# The English code of conduct must stay the pure template, or GitHub's community profile shows
# "other" instead of the Contributor Covenant.
_coc = (ROOT / "CODE_OF_CONDUCT.md").read_text(encoding="utf-8")
r.check("CODE_OF_CONDUCT.md is the pure Contributor Covenant 2.1",
        _coc.startswith("# Contributor Covenant Code of Conduct\n") and "<img" not in _coc
        and "kleinanzeigen-assistant-github@ollornog.de" in _coc and "[INSERT" not in _coc)

# ---- Nothing is loaded from third parties; every page has a favicon
_fremd = hygiene.pruefe_keine_fremdressourcen(str(ROOT), DATEIEN, POLICY)
r.check("nothing is loaded from third parties", not _fremd, " | ".join(_fremd[:3]))
_favicon = hygiene.pruefe_favicon(str(ROOT), DATEIEN, POLICY)
r.check("every page has a favicon", not _favicon, " | ".join(_favicon[:3]))

# ---- Every test file runs; scripts are executable
_td = hygiene.pruefe_testdateien_gerufen(str(ROOT))
r.check("every test file is called by a runner", not _td, " | ".join(_td[:3]))
nicht_x = hygiene.pruefe_ausfuehrbar(str(ROOT), ["scripts/check.sh", ".githooks/pre-push"])
r.check("scripts/check.sh and pre-push are executable", not nicht_x, " | ".join(nicht_x))

# ---- Backlog: structure, references, generated index
for _v in backlog.alle_pruefungen(str(ROOT)):
    r.check(f"backlog: {_v}", False)
r.check("backlog has entries", bool(backlog.lade(str(ROOT))))
_idx = subprocess.run([sys.executable, "scripts/_backlog.py", "index", "--dry-run"],
                      cwd=ROOT, capture_output=True, text=True)
r.check("backlog/README.md is current (otherwise: scripts/_backlog.py index)", _idx.returncode == 0)

# ---- The guards above the guards
_beleg = hygiene.pruefe_belegstellen_eng(str(ROOT), DATEIEN, [])
r.check("evidence patterns hit no code", not _beleg, " | ".join(_beleg[:3]))
_tab = hygiene.pruefe_tabelle_vollstaendig()
r.check("every kit check is in exactly one list", not _tab, " | ".join(_tab[:3]))
_pk = hygiene.pruefe_policy_schluessel_gelesen(POLICY)
r.check("every policy key is read", not _pk, " | ".join(_pk[:3]))

_ng = hygiene.pruefe_kit_prueffunktionen_gerufen(str(ROOT), ausgenommen={
    "pruefe_run_all_sammelt_automatisch":
        "no run_all.py: check.sh runs `pytest tests`, pytest collects the suites itself",
    "pruefe_extras_imports":
        "single extra `test` (pytest, httpx2) used only by the suite; no CI job runs without extras",
})
r.check("every kit check is called or exempted with a reason", not _ng, " | ".join(_ng[:3]))

# Must run last: did every check above see something?
_ns = hygiene.pruefe_etwas_gesehen()
r.check("every kit check saw something (none ran over an empty set)", not _ns, " | ".join(_ns[:3]))

sys.exit(r.done())
