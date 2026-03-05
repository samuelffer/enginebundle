"""CLI for EngineBundle - interactive and argparse modes."""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import textwrap
from pathlib import Path

from enginebundle import __version__
from enginebundle.generator import create_bundle

LOG = logging.getLogger("enginebundle")


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

def _term_width() -> int:
    return shutil.get_terminal_size((80, 24)).columns


def _supports_color() -> bool:
    if os.name == "nt":
        return (
            "ANSICON" in os.environ
            or "WT_SESSION" in os.environ
            or os.environ.get("TERM_PROGRAM") == "vscode"
        )
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


if _supports_color():
    R = "\033[0m"
    B = "\033[1m"
    DIM = "\033[2m"
    BLU = "\033[38;5;75m"
    GRN = "\033[38;5;83m"
    YLW = "\033[38;5;221m"
    RED = "\033[38;5;210m"
    CYN = "\033[38;5;87m"
    GRY = "\033[38;5;240m"
    WHT = "\033[38;5;252m"
else:
    R = B = DIM = BLU = GRN = YLW = RED = CYN = GRY = WHT = ""


def clr(color: str, text: str) -> str:
    return f"{color}{text}{R}"


def _hr(char: str = "-", color: str = GRY) -> str:
    return clr(color, char * min(_term_width(), 72))


def _print_hr(char: str = "-", color: str = GRY) -> None:
    print(_hr(char, color))


def _clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


def _banner() -> None:
    w = min(_term_width(), 72)
    print()
    _print_hr("=", BLU)
    title = "  ENGINE BUNDLE"
    ver = f"v{__version__}  "
    pad = w - len(title) - len(ver) - 2
    print(f"{clr(B + BLU, title)}{' ' * max(pad, 1)}{clr(GRY, ver)}")
    _print_hr("=", BLU)
    print()


def _section(title: str) -> None:
    print(f"\n{clr(B + YLW, '  ' + title)}")
    _print_hr("-", GRY)


def _ok(msg: str) -> None:
    print(f"  {clr(GRN, '[OK]')}  {msg}")


def _err(msg: str) -> None:
    print(f"  {clr(RED, '[X]')}  {clr(RED, msg)}")


def _info(msg: str) -> None:
    print(f"  {clr(BLU, '-')}  {msg}")


def _warn(msg: str) -> None:
    print(f"  {clr(YLW, '!')}{clr(YLW, msg)}")


def _tip(msg: str) -> None:
    print(f"  {clr(CYN, '->')}  {clr(DIM, msg)}")


def _prompt(msg: str) -> str:
    try:
        return input(f"\n  {clr(BLU, '>')} {msg} ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return ""


def _yn(question: str, default_yes: bool = True) -> bool:
    hint = clr(GRN, "Y") + clr(GRY, "/n") if default_yes else clr(GRY, "y/") + clr(GRN, "N")
    while True:
        ans = _prompt(f"{question} [{hint}]").lower()
        if ans == "":
            return default_yes
        if ans in ("y", "yes"):
            return True
        if ans in ("n", "no"):
            return False
        _err("Please type y or n.")


# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

def _default_output_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "output"
    home_docs = Path.home() / "Documents"
    if home_docs.exists():
        return home_docs / "enginebundle" / "output"
    return Path.home() / "enginebundle" / "output"


DEFAULT_OUTPUT_DIR = _default_output_dir()


# ---------------------------------------------------------------------------
# Interactive mode
# ---------------------------------------------------------------------------

def _imode_build() -> None:
    _clear()
    _banner()
    _section("Build - Enter Unity project path")

    raw = _prompt("Unity project root path (folder containing Assets/):")
    if not raw:
        return

    project_root = Path(raw).expanduser().resolve()
    if not project_root.exists():
        _err(f"Path not found: {project_root}")
        _prompt("Press Enter to go back.")
        return

    if not (project_root / "Assets").is_dir():
        _err("This does not look like a Unity project (no Assets/ folder).")
        _prompt("Press Enter to go back.")
        return

    raw_out = _prompt(f"Output directory [{DEFAULT_OUTPUT_DIR}]:")
    out_dir = Path(raw_out).expanduser().resolve() if raw_out else DEFAULT_OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    _clear()
    _banner()
    _section("Building...")
    _info(f"Project  {clr(WHT, str(project_root))}")
    _info(f"Output   {clr(WHT, str(out_dir))}")
    print()

    try:
        bundle_dir, zip_path = create_bundle(project_root, output_dir=out_dir)
    except (RuntimeError, OSError, ValueError) as exc:
        _err(f"Build failed: {exc}")
        _prompt("Press Enter to go back.")
        return

    _print_hr("-", GRN)
    _ok("Bundle complete!")
    _print_hr("-", GRN)
    print()
    _info(f"Bundle  {clr(WHT, str(bundle_dir))}")
    _info(f"ZIP     {clr(BLU, str(zip_path))}")
    print()
    _tip("Upload the .zip to your AI tool and start with SUMMARY.md.")
    _prompt("Press Enter to return to the main menu.")


def _imode_main_menu() -> None:
    while True:
        _clear()
        _banner()

        _section("Main Menu")
        print(f"  {clr(B + GRN, '[1]')}  {clr(WHT, 'Build')}    {clr(GRY, '- generate AI context bundle from a Unity project')}")
        print(f"  {clr(B + GRY, '[0]')}  {clr(GRY, 'Exit')}")
        print()

        choice = _prompt("Choose an option:")

        if choice == "1":
            _imode_build()
        elif choice in ("0", "q", "exit", "quit", ""):
            _clear()
            print(f"\n  {clr(GRY, 'Goodbye.')}\n")
            sys.exit(0)
        else:
            _err("Invalid option.")
            input()


# ---------------------------------------------------------------------------
# Argparse mode
# ---------------------------------------------------------------------------

def cmd_build(args: argparse.Namespace) -> int:
    project_root = Path(args.project).expanduser().resolve()

    if not project_root.exists():
        print(f"  Error: path not found: {project_root}", file=sys.stderr)
        return 1

    out_dir = Path(args.output).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n  {clr(B + BLU, 'enginebundle build')}")
    print(f"  {'project':<10} {clr(WHT, str(project_root))}")
    print(f"  {'output':<10} {clr(WHT, str(out_dir))}")
    print()

    try:
        bundle_dir, zip_path = create_bundle(project_root, output_dir=out_dir)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"  {clr(RED, 'Error:')} {exc}", file=sys.stderr)
        return 1

    print(f"  {clr(GRN, '[OK]')}  Done.")
    print(f"  {'bundle':<10} {clr(WHT, str(bundle_dir))}")
    print(f"  {'zip':<10} {clr(BLU, str(zip_path))}")
    print()
    return 0


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="enginebundle",
        description="enginebundle - AI context generator for Unity projects",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              enginebundle build ./MyUnityProject
              enginebundle build ./MyUnityProject --output ./bundles
              enginebundle --version
        """),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--verbose", "-v", action="store_true", help=argparse.SUPPRESS)

    sub = parser.add_subparsers(dest="command")
    pb = sub.add_parser("build", help="Generate a bundle from a Unity project folder.")
    pb.add_argument("project", help="Unity project root (folder containing Assets/).")
    pb.add_argument(
        "--output", "-o",
        default=str(DEFAULT_OUTPUT_DIR),
        metavar="DIR",
        help=f"Output directory (default: {DEFAULT_OUTPUT_DIR}).",
    )

    return parser


def main() -> None:
    args_passed = sys.argv[1:]

    if args_passed:
        parser = _build_argparser()
        args = parser.parse_args()
        level = logging.DEBUG if getattr(args, "verbose", False) else logging.WARNING
        logging.basicConfig(level=level)

        if args.command == "build":
            sys.exit(cmd_build(args))
        else:
            parser.print_help()
            sys.exit(0)
    else:
        logging.basicConfig(level=logging.WARNING)
        _imode_main_menu()


if __name__ == "__main__":
    main()
