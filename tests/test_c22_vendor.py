"""The vendored C22 copy carries the same-origin guard for data-href (C22 #160, CodeQL js/xss-through-dom).

The guard itself is tested in C22's browser suite; this checks that the copy here has it and that it behaves: the
function is cut out of c22.js and run in node against a fake page origin.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

C22_JS = Path(__file__).resolve().parent.parent / "src/kassist/gui/static/c22/js/c22.js"


def test_data_href_never_reaches_location_unchecked():
    text = C22_JS.read_text(encoding="utf-8")
    assert not re.search(r"location\.assign\([^)]*getAttribute\('data-href'\)", text)
    assert text.count("oeffneZiel(") >= 3          # definition and both callers (row, merged cell)


def test_safe_href_allows_only_the_own_origin():
    node = shutil.which("node")
    assert node, "node is needed for this test"
    text = C22_JS.read_text(encoding="utf-8")
    m = re.search(r"  function sicheresZiel\(href\) \{.*?\n  \}\n", text, re.S)
    assert m, "sicheresZiel missing in the vendored c22.js"
    faelle = ["x/y?a=1", "/z", "javascript:alert(1)", "JavaScript:alert(1)", "data:text/html,x",
              "https://example.invalid/", "//example.invalid/x", "", None]
    skript = ("const location = new URL('http://127.0.0.1:8765/items/a');\n" + m.group(0)
              + f"\nconsole.log(JSON.stringify({json.dumps(faelle)}.map(sicheresZiel)));\n")
    out = subprocess.run([node, "-e", skript], capture_output=True, text=True, timeout=30, check=True).stdout
    assert json.loads(out) == ["http://127.0.0.1:8765/items/x/y?a=1", "http://127.0.0.1:8765/z"] + [None] * 7
