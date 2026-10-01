"""Run lightweight dependency-free JavaScript dashboard behavior tests."""
import shutil
import subprocess
from pathlib import Path

import pytest


def test_dashboard_frontend_behaviors():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    root = Path(__file__).parent.parent
    subprocess.run([node, "--test", "tests/frontend.test.js"], cwd=root, check=True)


def test_dashboard_exposes_transfer_formats_strategies_and_safe_defaults():
    root = Path(__file__).parent.parent
    page = (root / "index.html").read_text()
    script = (root / "app.js").read_text()
    assert all(label in page for label in (
        "Plurapack backup", "PluralKit", "Tupperbox", "Auto-detect",
        "Merge", "Skip existing", "Overwrite",
    ))
    assert 'name="exportForms" value="members" checked' in page
    assert 'name="importStrategy" value="merge" checked' in page
    assert "Creating a Plurapack backup first is strongly recommended" in script
    assert "report.proxyTagsImported" in script and "report.warnings" in script
