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
