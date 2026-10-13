import importlib.util
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("plurapack_install", ROOT / "install.py")
install = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(install)


def _project():
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]


def _names(requirements):
    return {item.split(">")[0].split("[")[0].casefold() for item in requirements}


def test_platform_libraries_are_optional_extras():
    project = _project()
    extras = project["optional-dependencies"]
    assert not _names(project["dependencies"]) & {"stoat.py", "fluxer.py"}
    assert _names(extras["stoat"]) == {"stoat.py"}
    assert _names(extras["fluxer"]) == {"fluxer.py"}
    assert {"stoat.py", "fluxer.py"} <= _names(extras["all"])
    assert {"stoat.py", "fluxer.py", "pytest"} <= _names(extras["dev"])


def test_docker_build_installs_both_platforms_by_default():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "ARG PLURAPACK_PLATFORMS=stoat,fluxer" in dockerfile
    assert '".[${PLURAPACK_PLATFORMS}]"' in dockerfile


@pytest.mark.parametrize("platform, dev, expected", [
    ("stoat", False, ["stoat"]),
    ("fluxer", False, ["fluxer"]),
    ("both", False, ["stoat", "fluxer"]),
    ("both", True, ["stoat", "fluxer", "dev"]),
])
def test_extras_follow_the_chosen_platform(platform, dev, expected):
    assert install.extras_for(platform, dev) == expected


def test_unknown_platform_is_rejected():
    with pytest.raises(ValueError):
        install.extras_for("discord")


def test_pip_command_installs_this_checkout_with_only_the_chosen_extras():
    command = install.pip_command("python", "fluxer")
    assert command[:5] == ["python", "-m", "pip", "install", "-e"]
    assert command[5] == f"{install.ROOT}[fluxer]"


def test_dry_run_does_not_claim_anything_was_installed():
    assert "is installed" not in install.next_steps("stoat", None, installed=False)
    assert "is installed" in install.next_steps("stoat", None)


def test_next_steps_name_only_the_chosen_tokens():
    stoat = install.next_steps("stoat", None)
    assert "STOAT_BOT_TOKEN" in stoat and "FLUXER_BOT_TOKEN" not in stoat
    both = install.next_steps("both", ". .venv/bin/activate")
    assert "STOAT_BOT_TOKEN" in both and "FLUXER_BOT_TOKEN" in both
    assert ". .venv/bin/activate" in both


@pytest.mark.parametrize("answers, expected", [
    (["1"], "stoat"), (["2"], "fluxer"), (["3"], "both"), ([""], "both"),
    (["x", "fluxer"], "fluxer"),
])
def test_interactive_question_accepts_numbers_names_and_a_default(monkeypatch, capsys, answers, expected):
    replies = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(replies))
    assert install.choose_platform() == expected


def test_dry_run_with_platform_asks_nothing_and_changes_nothing(monkeypatch, capsys):
    def explode(*args, **kwargs):
        raise AssertionError("dry run must not run commands")

    monkeypatch.setattr(install.subprocess, "run", explode)
    monkeypatch.setattr("builtins.input", explode)
    assert install.main(["--platform", "stoat", "--no-venv", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert f"{install.ROOT}[stoat]" in output
    assert "[fluxer]" not in output and "[stoat,fluxer]" not in output


def test_install_runs_pip_for_the_chosen_platform(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(install.subprocess, "run",
                        lambda command, cwd=None: calls.append(command) or type("R", (), {"returncode": 0})())
    assert install.main(["--platform", "both", "--dev", "--no-venv"]) == 0
    assert calls == [install.pip_command(install.sys.executable, "both", True)]


def test_failed_pip_install_is_reported(monkeypatch, capsys):
    monkeypatch.setattr(install.subprocess, "run", lambda command, cwd=None: type("R", (), {"returncode": 3})())
    assert install.main(["--platform", "fluxer", "--no-venv"]) == 3
    assert "Installation failed" in capsys.readouterr().err


def test_without_a_terminal_it_installs_both_and_says_so(monkeypatch, capsys):
    monkeypatch.setattr(install.sys.stdin, "isatty", lambda: False, raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(AssertionError("no prompts")))
    assert install.main(["--no-venv", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "installing both platforms" in output and "[stoat,fluxer]" in output
