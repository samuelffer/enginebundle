"""EngineBundle CLI."""

from __future__ import annotations

import argparse
import itertools
import logging
import os
import shlex
import shutil
import sys
import textwrap
import threading
import time
from pathlib import Path

from enginebundle import __version__
from enginebundle.generator import create_bundle, scan_project

LOG = logging.getLogger("enginebundle")

# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------

def _supports_color() -> bool:
    if os.name == "nt":
        return (
            "ANSICON" in os.environ
            or "WT_SESSION" in os.environ
            or os.environ.get("TERM_PROGRAM") == "vscode"
            or os.environ.get("COLORTERM") is not None
        )
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


if _supports_color():
    R    = "\033[0m"
    BOLD = "\033[1m"
    DIM  = "\033[2m"
    BLU  = "\033[94m"
    GRN  = "\033[92m"
    YLW  = "\033[93m"
    RED  = "\033[91m"
    CYN  = "\033[96m"
    GRY  = "\033[90m"
    WHT  = "\033[97m"
    MGT  = "\033[95m"
else:
    R = BOLD = DIM = BLU = GRN = YLW = RED = CYN = GRY = WHT = MGT = ""


def c(col: str, text: str) -> str:
    return f"{col}{text}{R}"


def _w() -> int:
    return min(shutil.get_terminal_size((80, 24)).columns, 72)


def _rule(ch: str = "-", col: str = GRY) -> None:
    print(c(col, ch * _w()))


def _clear() -> None:
    os.system("cls" if os.name == "nt" else "clear")


# ---------------------------------------------------------------------------
# Spinner
# ---------------------------------------------------------------------------

class Spinner:
    """Displays an animated spinner on the current line while work runs."""

    FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    # Fallback for terminals that can't render braille
    FRAMES_PLAIN = ["-", "\\", "|", "/"]

    def __init__(self, label: str) -> None:
        self.label   = label
        self._stop   = threading.Event()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._frames = self.FRAMES if _supports_color() else self.FRAMES_PLAIN

    def _spin(self) -> None:
        for frame in itertools.cycle(self._frames):
            if self._stop.is_set():
                break
            sys.stdout.write(f"\r  {c(BLU, frame)}  {c(GRY, self.label)}   ")
            sys.stdout.flush()
            time.sleep(0.08)

    def __enter__(self) -> "Spinner":
        self._thread.start()
        return self

    def __exit__(self, *_) -> None:
        self._stop.set()
        self._thread.join()
        sys.stdout.write("\r" + " " * (_w()) + "\r")
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def _banner(project: "Path | None" = None) -> None:
    _rule("-", GRY)
    left  = f"{c(BOLD + BLU, 'EngineBundle')} {c(GRY, 'v' + __version__)}"
    right = c(GRY, "In Development — Expect Bugs!")
    gap   = " " * max(_w() - 42, 2)
    print(f"{left}{gap}{right}")
    if project:
        print(f"{c(GRY, 'project')}  {c(WHT, str(project))}")
    _rule("-", GRY)
    print()


def _ok(msg: str) -> None:
    print(f"{c(GRN, 'ok')}    {msg}")


def _err(msg: str) -> None:
    print(f"{c(RED, 'error')} {c(RED, msg)}")


def _warn(msg: str) -> None:
    print(f"{c(YLW, 'warn')}  {c(YLW, msg)}")


def _kv(key: str, val: str, key_w: int = 10) -> None:
    print(f"  {c(GRY, key.ljust(key_w))}  {c(WHT, val)}")


# ---------------------------------------------------------------------------
# Project validation
# ---------------------------------------------------------------------------

def _resolve_project(raw: str) -> "Path | None":
    path = Path(raw).expanduser().resolve()

    if not path.exists():
        _err(f"path not found: {path}")
        print()
        return None

    # Accept Assets/ subfolder — step up
    if path.name.lower() == "assets" and path.is_dir():
        path = path.parent

    # Check for known engine project markers
    markers = [
        (path / "Assets").is_dir() and (path / "ProjectSettings").is_dir(),  # Unity
        # Future: (path / "project.godot").exists(),  # Godot
        # Future: (path / "Intermediate").is_dir(),   # Unreal
    ]
    if any(markers):
        return path

    _err("no supported game project found at this path.")
    print(f"  {c(GRY, 'Expected the project root folder, e.g.:')} {c(WHT, 'C:/Projects/MyGame')}")
    print(f"  {c(GRY, 'You can also point to the Assets/ subfolder.')}")
    print()
    return None


# ---------------------------------------------------------------------------
# Scene / asset file helpers
# ---------------------------------------------------------------------------

def _list_scenes(project: Path) -> list:
    """Return sorted list of scene files found in the project."""
    # Unity
    unity = list((project / "Assets").rglob("*.unity")) if (project / "Assets").is_dir() else []
    # Future engines can append here
    return sorted(unity)


def _print_scenes(project: Path, scenes: list) -> None:
    if not scenes:
        print(f"  {c(GRY, 'No scene files found.')}")
        return
    print(f"  {c(BOLD + WHT, 'Scenes:')}")
    for i, p in enumerate(scenes, 1):
        try:
            rel = p.relative_to(project)
        except ValueError:
            rel = p
        print(f"    {c(BLU, str(i) + '.')}  {c(WHT, str(rel))}")


def _resolve_scene_arg(arg: str, scenes: list) -> "list[Path] | None":
    if not scenes:
        return []

    arg = arg.strip()
    if arg.lower() in ("all", "*"):
        return list(scenes)

    selected: list[Path] = []
    for part in [p.strip() for p in arg.split(",") if p.strip()]:
        if part.isdigit():
            idx = int(part)
            if idx < 1 or idx > len(scenes):
                _err(f"scene {idx} does not exist  (valid: 1 – {len(scenes)})")
                return None
            p = scenes[idx - 1]
        else:
            matches = [s for s in scenes if s.stem.lower() == part.lower()]
            if not matches:
                _err(f"scene '{part}' not found")
                print(f"  {c(GRY, 'Use the number or the exact filename without extension.')}")
                print()
                return None
            p = matches[0]
        if p not in selected:
            selected.append(p)

    return selected or None


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

DEFAULT_OUTPUT_DIR = (
    (Path.home() / "Documents" / "EngineBundle" / "output")
    if (Path.home() / "Documents").exists()
    else (Path.home() / "EngineBundle" / "output")
)


def _run_build(project: Path, scene_arg: str, out_dir: Path) -> "tuple[Path, Path] | None":
    """Run the bundle generation. Returns (bundle_dir, zip_path) or None on error."""
    scenes   = _list_scenes(project)
    selected = _resolve_scene_arg(scene_arg, scenes)
    if selected is None:
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    scene_filter = [str(p.relative_to(project)) for p in selected] if selected else None

    print()
    _kv("project", str(project))
    _kv("output",  str(out_dir))
    if scene_filter:
        for s in scene_filter:
            _kv("scene", s)
    else:
        _kv("scenes", "all")
    print()

    result: "tuple | None" = None
    error:  "Exception | None" = None

    def _work() -> None:
        nonlocal result, error
        try:
            result = create_bundle(project, output_dir=out_dir, scene_filter=scene_filter)
        except Exception as exc:
            error = exc

    with Spinner("Generating bundle..."):
        _work()

    if error:
        _err(str(error))
        return None

    bundle_dir, zip_path = result
    _rule("-", GRN)
    _ok("Bundle generated successfully")
    _rule("-", GRN)
    print()
    _kv("bundle", str(bundle_dir))
    _kv("zip",    str(zip_path))
    print()
    print(f"  {c(GRY, 'Upload the .zip to your AI tool and start with SUMMARY.md.')}")
    print()
    return bundle_dir, zip_path


def _post_build_menu(project: Path) -> "Path":
    """Show options after a successful build. Returns the (possibly new) project."""
    _rule("-", GRY)
    print(f"  {c(BOLD + WHT, 'What would you like to do next?')}")
    print()
    print(f"    {c(BOLD + BLU, '1.')}  {c(WHT, 'Generate another bundle for this project')}")
    print(f"    {c(BOLD + BLU, '2.')}  {c(WHT, 'Switch to a different project')}")
    print(f"    {c(BOLD + BLU, '3.')}  {c(WHT, 'Exit EngineBundle')}")
    _rule("-", GRY)
    print()

    while True:
        try:
            choice = input(f"  {c(BLU, 'choice')} {c(GRY, '>')} ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(0)

        if choice == "1":
            return project
        elif choice == "2":
            print()
            while True:
                try:
                    raw = input(f"  {c(BLU, 'project path')} {c(GRY, '>')} ").strip()
                except (KeyboardInterrupt, EOFError):
                    print()
                    sys.exit(0)
                if not raw:
                    continue
                new = _resolve_project(raw)
                if new:
                    return new
        elif choice == "3":
            print()
            sys.exit(0)
        else:
            _err(f"invalid choice '{choice}'  (enter 1, 2 or 3)")


# ---------------------------------------------------------------------------
# Shell commands
# ---------------------------------------------------------------------------

def _cmd_build(project: Path, args: list, out_dir: Path) -> "Path":
    """Run build and show post-build menu. Returns (possibly new) project."""
    scenes = _list_scenes(project)

    if not args:
        print()
        _print_scenes(project, scenes)
        print()
        if scenes:
            print(f"  {c(GRY, 'Usage:')}  "
                  f"{c(WHT, 'build all')}  {c(GRY, '|')}  "
                  f"{c(WHT, 'build 1')}  {c(GRY, '|')}  "
                  f"{c(WHT, 'build 1,2')}  {c(GRY, '|')}  "
                  f"{c(WHT, 'build SampleScene')}")
            print(f"  {c(GRY, 'Custom output:')}  {c(WHT, 'build all -o C:/my/output')}")
        print()
        return project

    scene_arg = args[0]
    i = 1
    while i < len(args):
        if args[i] in ("-o", "--output") and i + 1 < len(args):
            out_dir = Path(args[i + 1]).expanduser().resolve()
            i += 2
        else:
            _err(f"unknown option '{args[i]}'")
            print(f"  {c(GRY, 'Usage:  build <scene|all> [-o <dir>]')}")
            print()
            return project

    result = _run_build(project, scene_arg, out_dir)
    if result:
        project = _post_build_menu(project)
        _clear()
        _banner(project)
        _print_scenes(project, _list_scenes(project))
        print()
        print(
            f"  {c(GRY, 'Type')} {c(BOLD + WHT, 'help')} {c(GRY, 'for all commands')}  "
            f"{c(GRY, '·')}  "
            f"{c(GRY, 'Full docs:')} {c(CYN, 'github.com/samuelffer/enginebundle')}"
        )
        print()

    return project


def _cmd_info(project: Path) -> None:
    print()
    with Spinner("Scanning project..."):
        try:
            scan = scan_project(project)
            error = None
        except Exception as exc:
            scan = None
            error = exc

    if error or scan is None:
        _err(str(error))
        return

    cs_types = [t for fi in scan.cs_files for t in fi.types]
    mono  = sum(1 for t in cs_types if t.is_mono_behaviour)
    so    = sum(1 for t in cs_types if t.is_scriptable_object)
    other = len(cs_types) - mono - so

    _kv("project",     project.name,             20)
    _kv("path",        str(project),             20)
    print()
    _kv("scripts",     str(len(scan.cs_files)),  20)
    _kv("MB types",    c(GRN, str(mono)),         20)
    _kv("SO types",    c(CYN, str(so)),           20)
    _kv("other types", str(other),               20)
    _kv("assemblies",  str(len(scan.asmdefs)),   20)
    _kv("scenes",      str(len(scan.scenes)),    20)
    _kv("prefabs",     str(len(scan.prefabs)),   20)
    if scan.tag_layer and scan.tag_layer.tags:
        _kv("tags", ", ".join(scan.tag_layer.tags), 20)
    print()


def _cmd_scripts(project: Path) -> None:
    with Spinner("Reading scripts..."):
        try:
            scan = scan_project(project)
            error = None
        except Exception as exc:
            scan = None
            error = exc

    if error or scan is None:
        _err(str(error))
        return

    if not any(fi.types for fi in scan.cs_files):
        _warn("no types found")
        return

    print()
    for fi in sorted(scan.cs_files, key=lambda f: f.rel_path):
        if not fi.types:
            continue
        print(f"  {c(GRY, fi.rel_path)}")
        for t in fi.types:
            tag, col = ("MB", GRN) if t.is_mono_behaviour else \
                       ("SO", CYN) if t.is_scriptable_object else \
                       ("--", GRY)
            ns   = c(GRY, t.namespace + ".") if t.namespace else ""
            base = c(GRY, " : " + t.base_class) if t.base_class else ""
            print(f"    {c(col, tag)}  {ns}{c(BOLD + WHT, t.name)}{base}")
    print()


def _cmd_scenes(project: Path) -> None:
    with Spinner("Reading scenes..."):
        try:
            scan = scan_project(project)
            error = None
        except Exception as exc:
            scan = None
            error = exc

    if error or scan is None:
        _err(str(error))
        return

    assets = [("scene", s) for s in scan.scenes] + [("prefab", p) for p in scan.prefabs]
    if not assets:
        _warn("no scene or prefab files found")
        return

    print()
    for kind, asset in sorted(assets, key=lambda x: x[1].rel_path):
        col = BLU if kind == "scene" else MGT
        print(f"  {c(BOLD + col, kind)}  {c(WHT, asset.rel_path)}")
        with_scripts = [o for o in asset.objects if o.script_guids]
        if not with_scripts:
            print(f"    {c(GRY, 'no scripts attached')}")
        else:
            for obj in sorted(with_scripts, key=lambda o: o.name):
                names = [
                    Path(scan.guid_to_script[g]).stem
                    if g in scan.guid_to_script else f"?{g[:6]}"
                    for g in obj.script_guids
                ]
                print(f"    {c(GRY, obj.name)}  {c(GRN, ', '.join(names))}")
        print()


def _cmd_help() -> None:
    print()
    _rule("-", GRY)
    print(f"  {c(BOLD + WHT, 'Commands')}")
    _rule("-", GRY)
    print()

    groups = [
        ("Build", [
            ("build all",         "generate bundle with all scenes"),
            ("build 1",           "generate bundle — scene by number"),
            ("build 1,2",         "generate bundle — multiple scenes"),
            ("build SampleScene", "generate bundle — scene by name"),
            ("build all -o DIR",  "custom output directory"),
        ]),
        ("Inspect", [
            ("info",    "project overview — scripts, scenes, prefabs"),
            ("scripts", "list all script types found"),
            ("scenes",  "list scenes and attached scripts"),
        ]),
        ("Shell", [
            ("cd PATH", "switch to a different project"),
            ("clear",   "clear the terminal"),
            ("help",    "show this help"),
            ("exit",    "quit EngineBundle"),
        ]),
    ]

    for group_name, rows in groups:
        print(f"  {c(DIM, group_name)}")
        for cmd, desc in rows:
            print(f"    {c(BOLD + BLU, cmd.ljust(26))}{c(GRY, desc)}")
        print()

    _rule("-", GRY)
    print(
        f"  {c(GRY, 'Full docs:')}  "
        f"{c(CYN, 'github.com/samuelffer/enginebundle')}"
    )
    _rule("-", GRY)
    print()


# ---------------------------------------------------------------------------
# Interactive shell
# ---------------------------------------------------------------------------

def _shell(project: Path) -> None:
    _clear()
    _banner(project)
    _print_scenes(project, _list_scenes(project))
    print()
    print(
        f"  {c(GRY, 'Type')} {c(BOLD + WHT, 'help')} {c(GRY, 'for all commands')}  "
        f"{c(GRY, '·')}  "
        f"{c(GRY, 'Full docs:')} {c(CYN, 'github.com/samuelffer/enginebundle')}"
    )
    print()

    while True:
        try:
            raw = input(f"{c(BOLD + BLU, 'enginebundle')} {c(GRY, '>')} ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(0)

        if not raw:
            continue

        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = raw.split()

        cmd  = parts[0].lower()
        args = parts[1:]

        if cmd in ("exit", "quit", "q"):
            sys.exit(0)

        elif cmd == "clear":
            _clear()
            _banner(project)

        elif cmd == "cd":
            if not args:
                _err("cd requires a path")
                print(f"  {c(GRY, 'e.g.  cd \"C:/My Projects/MyGame\"')}")
                print()
            else:
                raw_path = args[0] if len(args) == 1 else " ".join(args)
                new = _resolve_project(raw_path)
                if new:
                    project = new
                    _clear()
                    _banner(project)
                    _print_scenes(project, _list_scenes(project))
                    print()

        elif cmd == "build":
            project = _cmd_build(project, args, DEFAULT_OUTPUT_DIR)

        elif cmd == "info":
            _cmd_info(project)

        elif cmd == "scripts":
            _cmd_scripts(project)

        elif cmd == "scenes":
            _cmd_scenes(project)

        elif cmd == "help":
            _cmd_help()

        else:
            _err(f"unknown command '{cmd}'")
            print(f"  {c(GRY, 'Type')} {c(WHT, 'help')} {c(GRY, 'to see available commands.')}")
            print()


# ---------------------------------------------------------------------------
# `enginebundle start`
# ---------------------------------------------------------------------------

def _cmd_start() -> None:
    _clear()
    _banner()
    print(f"  {c(GRY, 'Enter the path to your project to get started.')}")
    print(f"  {c(GRY, 'Tip: paths with spaces work fine — no quotes needed.')}")
    print()

    while True:
        try:
            raw = input(f"  {c(BLU, 'project path')} {c(GRY, '>')} ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(0)

        if not raw:
            print(f"  {c(GRY, 'Please enter a path to continue.')}")
            continue

        with Spinner("Validating project..."):
            project = _resolve_project(raw)

        if project:
            _shell(project)
            return


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        prog="enginebundle",
        description="EngineBundle — In Development — Expect Bugs!",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            commands:
              enginebundle start        start the interactive shell
              enginebundle --version    show version
              enginebundle --help       show this message

            full docs: https://github.com/samuelffer/enginebundle
        """),
    )
    parser.add_argument("--version", action="version", version=f"EngineBundle v{__version__}")
    parser.add_argument("--verbose", "-v", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("command", nargs="?", metavar="COMMAND")

    parsed = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if parsed.verbose else logging.WARNING)

    if parsed.command is None:
        print()
        print(f"  {c(BOLD + BLU, 'EngineBundle')} {c(GRY, 'v' + __version__)}")
        print()
        print(f"  {c(GRY, 'Run')} {c(BOLD + WHT, 'enginebundle start')} {c(GRY, 'to begin.')}")
        print(f"  {c(GRY, 'Run')} {c(WHT, 'enginebundle --help')} {c(GRY, 'for more options.')}")
        print()
        sys.exit(0)

    if parsed.command.lower() == "start":
        _cmd_start()
    else:
        _err(f"unknown command '{parsed.command}'")
        print(f"  {c(GRY, 'Did you mean')} {c(WHT, 'enginebundle start')}?")
        print()
        sys.exit(1)


if __name__ == "__main__":
    main()
