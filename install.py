#!/usr/bin/env python3
"""Interactive installer for Plurapack.

Asks which chat platform(s) you host - Stoat, Fluxer, or both - and installs
only the matching client libraries. Uses nothing outside the standard library,
so it runs before Plurapack's own dependencies exist.

    python install.py                       # ask questions
    python install.py --platform stoat      # no questions
    python install.py --platform both --dev # also install the test tools
    python install.py --dry-run             # show what would run, change nothing

Equivalent manual commands:

    python -m pip install -e '.[stoat]'
    python -m pip install -e '.[fluxer]'
    python -m pip install -e '.[stoat,fluxer]'
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import venv
from pathlib import Path

MINIMUM_PYTHON = (3, 11)
PLATFORMS = {"stoat": ("stoat",), "fluxer": ("fluxer",), "both": ("stoat", "fluxer")}
TOKEN_VARIABLES = {"stoat": "STOAT_BOT_TOKEN", "fluxer": "FLUXER_BOT_TOKEN"}
ROOT = Path(__file__).resolve().parent
VENV_DIRECTORY = ROOT / ".venv"


def extras_for(platform: str, dev: bool = False) -> list[str]:
    """Return the pip extras that install the chosen platform(s)."""
    if platform not in PLATFORMS:
        raise ValueError(f"Platform must be one of: {', '.join(PLATFORMS)}.")
    return [*PLATFORMS[platform], *(["dev"] if dev else [])]


def pip_command(python: str | os.PathLike[str], platform: str, dev: bool = False) -> list[str]:
    """Build the pip command that installs Plurapack from this checkout."""
    target = f"{ROOT}[{','.join(extras_for(platform, dev))}]"
    return [str(python), "-m", "pip", "install", "-e", target]


def venv_python(directory: Path = VENV_DIRECTORY) -> Path:
    """Locate the interpreter inside a virtual environment on any OS."""
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def activation_hint(directory: Path = VENV_DIRECTORY) -> str:
    if os.name == "nt":
        return f"{directory.name}\\Scripts\\activate"
    return f". {directory.name}/bin/activate"


def next_steps(platform: str, activate: str | None, installed: bool = True) -> str:
    """Explain what to do after installing, including the right token variables."""
    lines = ["", "Plurapack is installed. Next:" if installed else "After installing:"]
    if activate:
        lines.append(f"  1. Activate the environment:  {activate}")
    step = 2 if activate else 1
    for name in PLATFORMS[platform]:
        lines.append(f"  {step}. Set {TOKEN_VARIABLES[name]} to your {name.title()} bot token (never commit it).")
        step += 1
    lines.append(f"  {step}. Start the bot:  plurapack   (or: python main.py)")
    lines.append("See documentation/docs/getting-started/install.md for the other settings.")
    return "\n".join(lines)


def in_virtual_environment() -> bool:
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def choose_platform() -> str:
    print("Which chat platform(s) will this Plurapack bot connect to?")
    print("  1) Stoat")
    print("  2) Fluxer")
    print("  3) Both")
    while True:
        answer = ask("Choose 1, 2, or 3 [3]: ").casefold()
        if answer in {"", "3", "both", "b"}:
            return "both"
        if answer in {"1", "stoat", "s"}:
            return "stoat"
        if answer in {"2", "fluxer", "f"}:
            return "fluxer"
        print("Please answer 1, 2, or 3.")


def yes_no(prompt: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        answer = ask(f"{prompt} [{suffix}]: ").casefold()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer y or n.")


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install Plurapack with only the chat platforms you use.")
    parser.add_argument("--platform", choices=sorted(PLATFORMS),
                        help="stoat, fluxer, or both (asked interactively when omitted)")
    parser.add_argument("--dev", action="store_true", help="also install the test tools")
    venv_group = parser.add_mutually_exclusive_group()
    venv_group.add_argument("--venv", action="store_true", help="create and use .venv")
    venv_group.add_argument("--no-venv", action="store_true", help="install into the current Python")
    parser.add_argument("--dry-run", action="store_true", help="print the commands without running them")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    if sys.version_info < MINIMUM_PYTHON:
        print(f"Plurapack needs Python {MINIMUM_PYTHON[0]}.{MINIMUM_PYTHON[1]} or newer; "
              f"this is {sys.version_info.major}.{sys.version_info.minor}.", file=sys.stderr)
        return 1
    if not (ROOT / "pyproject.toml").is_file():
        print("Run install.py from a Plurapack source checkout.", file=sys.stderr)
        return 1

    interactive = sys.stdin.isatty() and arguments.platform is None
    platform = arguments.platform or (choose_platform() if sys.stdin.isatty() else "both")
    if arguments.platform is None and not sys.stdin.isatty():
        print("No terminal to ask on; installing both platforms. Use --platform to choose.")

    python: str | os.PathLike[str] = sys.executable
    activate = None
    if arguments.venv:
        make_venv = True
    elif arguments.no_venv or in_virtual_environment():
        make_venv = False
    else:
        make_venv = interactive and yes_no(
            f"Create a virtual environment at {VENV_DIRECTORY.name}/ (recommended)?", True)
    if make_venv:
        python = venv_python()
        activate = activation_hint()

    command = pip_command(python, platform, arguments.dev)
    if arguments.dry_run:
        if make_venv:
            print(f"Would create a virtual environment at {VENV_DIRECTORY}")
        print("Would run:", " ".join(command))
        print(next_steps(platform, activate, installed=False))
        return 0

    if make_venv and not venv_python().exists():
        print(f"Creating virtual environment at {VENV_DIRECTORY} ...")
        venv.EnvBuilder(with_pip=True).create(VENV_DIRECTORY)
    print("Running:", " ".join(command))
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode:
        print("\nInstallation failed; see the messages above.", file=sys.stderr)
        return result.returncode
    print(next_steps(platform, activate))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
