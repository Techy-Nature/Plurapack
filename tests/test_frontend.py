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


def test_dashboard_api_requests_bypass_stale_browser_caches():
    script = (Path(__file__).parent.parent / "app.js").read_text()
    assert 'cache:"no-store"' in script


def test_login_page_offers_stoat_and_fluxer():
    page = (Path(__file__).parent.parent / "login.html").read_text()
    assert "Sign in with Stoat or Fluxer" in page
    assert "Plurapack bot on Stoat or Fluxer" in page


def test_browser_speech_requests_wav_without_server_credentials():
    script = (Path(__file__).parent.parent / "app.js").read_text()
    assert 'Accept:"audio/wav"' in script
    assert 'audio/mpeg' not in script
    assert 'PLURAPACK_TTS_API_KEY' not in script
    assert 'VOICE_STORAGE_API_KEY' not in script
