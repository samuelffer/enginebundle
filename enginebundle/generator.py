"""Bundle generator for Unity projects.

Scans a Unity project root and produces:
  SUMMARY.md          - compact overview for AI context
  SCRIPTS.md          - all C# types with namespace / base / interfaces
  HIERARCHY.txt       - full folder/file tree
  HIERARCHY_MIN.txt   - scripts + asmdef only (low-token)
  ASSEMBLIES.txt      - assembly graph
  SCENES.txt          - GameObjects per scene/prefab with attached scripts
  MANIFEST.json       - provenance and counts
  bundle.zip          - all of the above packaged
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import __version__ as EB_VERSION
from .cs_parser import CsFileInfo, CsType, parse_cs_file
from .unity_parser import (
    AsmdefInfo,
    TagLayerInfo,
    UnitySceneInfo,
    parse_asmdef,
    parse_meta_guid,
    parse_tag_manager,
    parse_unity_yaml,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_unity_project(root: Path) -> bool:
    return (root / "Assets").is_dir() and (root / "ProjectSettings").is_dir()


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

@dataclass
class ProjectScan:
    root: Path
    cs_files: List[CsFileInfo]
    asmdefs: List[AsmdefInfo]
    scenes: List[UnitySceneInfo]
    prefabs: List[UnitySceneInfo]
    tag_layer: Optional[TagLayerInfo]
    guid_to_script: Dict[str, str]   # guid -> rel_path of .cs file
    all_paths: List[str]             # every file rel path for HIERARCHY


def scan_project(root: Path) -> ProjectScan:
    """Walk the Unity project and parse all relevant files."""
    root = root.resolve()

    cs_files: List[CsFileInfo] = []
    asmdefs: List[AsmdefInfo] = []
    scenes: List[UnitySceneInfo] = []
    prefabs: List[UnitySceneInfo] = []
    tag_layer: Optional[TagLayerInfo] = None
    guid_to_script: Dict[str, str] = {}
    all_paths: List[str] = []

    assets = root / "Assets"
    settings = root / "ProjectSettings"

    # Scan Assets/
    for path in sorted(assets.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(root))
        suffix = path.suffix.lower()

        all_paths.append(rel)

        if suffix == ".cs":
            info = parse_cs_file(path, root)
            cs_files.append(info)
            # Try to find matching .meta for GUID
            meta_path = path.with_suffix(path.suffix + ".meta")
            if meta_path.exists():
                guid = parse_meta_guid(meta_path)
                if guid:
                    guid_to_script[guid] = rel

        elif suffix in (".asmdef", ".asmref"):
            asmdef = parse_asmdef(path, root)
            if asmdef:
                asmdefs.append(asmdef)

        elif suffix == ".unity":
            scene = parse_unity_yaml(path, root)
            if scene:
                scenes.append(scene)

        elif suffix == ".prefab":
            prefab = parse_unity_yaml(path, root)
            if prefab:
                prefabs.append(prefab)

    # Resolve scene GUID usages using guid_to_script
    for scene in scenes + prefabs:
        for obj in scene.objects:
            for guid in obj.script_guids:
                scene.guid_usages.setdefault(guid, []).append(obj.name)

    # ProjectSettings
    tag_manager = settings / "TagManager.asset"
    if tag_manager.exists():
        tag_layer = parse_tag_manager(tag_manager)

    return ProjectScan(
        root=root,
        cs_files=cs_files,
        asmdefs=asmdefs,
        scenes=scenes,
        prefabs=prefabs,
        tag_layer=tag_layer,
        guid_to_script=guid_to_script,
        all_paths=all_paths,
    )


# ---------------------------------------------------------------------------
# Output generators (each returns a string)
# ---------------------------------------------------------------------------

def _render_scripts_md(scan: ProjectScan) -> str:
    """SCRIPTS.md - compact type listing, optimised for low token usage."""
    lines = ["# Scripts", ""]
    lines.append(
        "> Format: `[kind] FullName : BaseClass (interfaces)` — `path`  "
    )
    lines.append(
        "> MonoBehaviour lifecycle methods shown only when present."
    )
    lines.append("")

    all_types: List[Tuple[str, CsType]] = []
    for fi in scan.cs_files:
        for t in fi.types:
            all_types.append((fi.rel_path, t))

    if not all_types:
        lines.append("*(no C# scripts found)*")
        return "\n".join(lines)

    # Group by namespace
    by_ns: Dict[str, List[Tuple[str, CsType]]] = {}
    for rel, t in all_types:
        ns = t.namespace or "(global)"
        by_ns.setdefault(ns, []).append((rel, t))

    for ns in sorted(by_ns):
        lines.append(f"## {ns}")
        lines.append("")
        for rel, t in sorted(by_ns[ns], key=lambda x: x[1].name):
            # Build compact signature
            bases: List[str] = []
            if t.base_class:
                bases.append(t.base_class)
            bases.extend(t.interfaces)
            base_str = " : " + ", ".join(bases) if bases else ""

            kind_tag = ""
            if t.is_mono_behaviour:
                kind_tag = "[MB] "
            elif t.is_scriptable_object:
                kind_tag = "[SO] "
            elif t.kind == "struct":
                kind_tag = "[struct] "

            lines.append(f"- {kind_tag}`{t.name}`{base_str}  — `{rel}`")

            if t.unity_messages:
                lines.append(f"  - messages: {', '.join(t.unity_messages)}")
        lines.append("")

    return "\n".join(lines)


def _render_hierarchy(scan: ProjectScan, min_only: bool = False) -> str:
    """HIERARCHY.txt or HIERARCHY_MIN.txt."""
    if min_only:
        # Only scripts, asmdefs, scenes, prefabs
        relevant_exts = {".cs", ".asmdef", ".asmref", ".unity", ".prefab"}
        paths = [p for p in scan.all_paths if Path(p).suffix.lower() in relevant_exts]
    else:
        paths = scan.all_paths

    lines: List[str] = []
    prev_parts: List[str] = []

    for rel in sorted(paths):
        parts = Path(rel).parts
        # Find common prefix depth
        common = 0
        for a, b in zip(prev_parts[:-1], parts[:-1]):
            if a == b:
                common += 1
            else:
                break

        # Print new directory levels
        for depth in range(common, len(parts) - 1):
            indent = "  " * depth
            if depth >= len(prev_parts) - 1 or parts[depth] != prev_parts[depth]:
                lines.append(f"{indent}{parts[depth]}/")

        indent = "  " * (len(parts) - 1)
        lines.append(f"{indent}{parts[-1]}")
        prev_parts = list(parts)

    return "\n".join(lines)


def _render_assemblies(scan: ProjectScan) -> str:
    """ASSEMBLIES.txt - assembly dependency graph."""
    lines = ["# Assembly Definitions", ""]

    if not scan.asmdefs:
        lines.append("*(no .asmdef files found — project uses default Assembly-CSharp)*")
        return "\n".join(lines)

    for a in sorted(scan.asmdefs, key=lambda x: x.name):
        lines.append(f"## {a.name}")
        lines.append(f"  file: {a.rel_path}")
        if a.references:
            lines.append(f"  depends on: {', '.join(a.references)}")
        else:
            lines.append("  depends on: (none)")
        flags: List[str] = []
        if a.allow_unsafe:
            flags.append("unsafe")
        if not a.auto_referenced:
            flags.append("no-auto-ref")
        if a.include_platforms:
            flags.append(f"platforms: {', '.join(a.include_platforms)}")
        if flags:
            lines.append(f"  flags: {', '.join(flags)}")
        lines.append("")

    return "\n".join(lines)


def _render_scenes(scan: ProjectScan) -> str:
    """SCENES.txt - GameObjects with their attached scripts per scene/prefab."""
    lines = ["# Scenes & Prefabs", ""]

    all_assets = [("Scene", s) for s in scan.scenes] + \
                 [("Prefab", p) for p in scan.prefabs]

    if not all_assets:
        lines.append("*(no .unity or .prefab files found)*")
        return "\n".join(lines)

    for kind, asset in sorted(all_assets, key=lambda x: x[1].rel_path):
        lines.append(f"## [{kind}] {asset.rel_path}")
        lines.append("")

        objects_with_scripts = [o for o in asset.objects if o.script_guids]
        if not objects_with_scripts:
            lines.append("  *(no MonoBehaviours attached or GUIDs unresolved)*")
        else:
            for obj in sorted(objects_with_scripts, key=lambda o: o.name):
                script_labels: List[str] = []
                for guid in obj.script_guids:
                    script_path = scan.guid_to_script.get(guid)
                    if script_path:
                        script_labels.append(f"`{Path(script_path).stem}`")
                    else:
                        script_labels.append(f"guid:{guid[:8]}…")
                lines.append(f"  - {obj.name}: {', '.join(script_labels)}")
        lines.append("")

    return "\n".join(lines)


def _render_summary(scan: ProjectScan) -> str:
    """SUMMARY.md - the main file to paste into an AI."""
    cs_types = [t for fi in scan.cs_files for t in fi.types]
    mono_count = sum(1 for t in cs_types if t.is_mono_behaviour)
    so_count = sum(1 for t in cs_types if t.is_scriptable_object)
    other_count = len(cs_types) - mono_count - so_count

    total_scenes = len(scan.scenes)
    total_prefabs = len(scan.prefabs)

    lines = [
        "# EngineBundle - Unity Project Summary",
        "",
        f"**Scripts (.cs):** {len(scan.cs_files)}  ",
        f"**Types found:** {len(cs_types)} "
        f"({mono_count} MonoBehaviours, {so_count} ScriptableObjects, {other_count} other)  ",
        f"**Assemblies (.asmdef):** {len(scan.asmdefs)}  ",
        f"**Scenes:** {total_scenes}  ",
        f"**Prefabs:** {total_prefabs}  ",
        "",
        "---",
        "",
        "## Assembly Overview",
        "",
    ]

    if not scan.asmdefs:
        lines.append("Project uses the default **Assembly-CSharp** (no .asmdef files).")
    else:
        for a in sorted(scan.asmdefs, key=lambda x: x.name):
            dep_str = " → " + ", ".join(a.references) if a.references else ""
            lines.append(f"- `{a.name}`{dep_str}")
    lines.append("")
    lines += ["---", ""]

    lines += ["## MonoBehaviours", ""]
    mb_types = [t for t in cs_types if t.is_mono_behaviour]
    if mb_types:
        for t in sorted(mb_types, key=lambda x: x.name):
            msgs = f"  *(messages: {', '.join(t.unity_messages)})*" if t.unity_messages else ""
            lines.append(f"- `{t.name}` — `{t.file_path}`{msgs}")
    else:
        lines.append("*(none)*")
    lines += ["", "---", ""]

    if so_count:
        lines += ["## ScriptableObjects", ""]
        for t in sorted([t for t in cs_types if t.is_scriptable_object], key=lambda x: x.name):
            lines.append(f"- `{t.name}` — `{t.file_path}`")
        lines += ["", "---", ""]

    if scan.tag_layer:
        lines += ["## Tags & Layers", ""]
        if scan.tag_layer.tags:
            lines.append(f"**Tags:** {', '.join(scan.tag_layer.tags)}")
        named_layers = {k: v for k, v in scan.tag_layer.layers.items() if k >= 8}
        if named_layers:
            layer_str = ", ".join(f"{v} ({k})" for k, v in sorted(named_layers.items()))
            lines.append(f"**Custom Layers:** {layer_str}")
        lines += ["", "---", ""]

    lines += [
        "## How to use this bundle",
        "",
        "1. Upload the `.zip` or paste `SUMMARY.md` into your AI tool.",
        "2. Use `SCRIPTS.md` for a full type map — namespace, base class, interfaces.",
        "3. Use `HIERARCHY_MIN.txt` for a low-token project tree.",
        "4. Use `SCENES.txt` to see which scripts are attached to which GameObjects.",
        "5. Use `ASSEMBLIES.txt` to understand the module dependency graph.",
        "6. Ask the AI to read a specific `.cs` file only when needed.",
        "",
        "> *Generated by [enginebundle](https://github.com/samuelffer/enginebundle)*",
        "",
    ]

    return "\n".join(lines)


def _render_manifest(scan: ProjectScan, project_root: Path) -> dict:
    cs_types = [t for fi in scan.cs_files for t in fi.types]
    return {
        "enginebundle_version": EB_VERSION,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "project_root": str(project_root),
        "counts": {
            "cs_files": len(scan.cs_files),
            "types": len(cs_types),
            "mono_behaviours": sum(1 for t in cs_types if t.is_mono_behaviour),
            "scriptable_objects": sum(1 for t in cs_types if t.is_scriptable_object),
            "asmdefs": len(scan.asmdefs),
            "scenes": len(scan.scenes),
            "prefabs": len(scan.prefabs),
        },
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def create_bundle(
    project_root: Path,
    *,
    output_dir: Path,
    only: Optional[List[str]] = None,
    scene_filter: Optional[List[str]] = None,
) -> Tuple[Path, Path]:
    """Scan a Unity project and write the bundle to output_dir.

    Args:
        project_root:  Root of the Unity project.
        output_dir:    Where to write the bundle folder and zip.
        only:          Optional list of file keys to include.
        scene_filter:  Optional list of relative scene paths to include.
                       If None, all scenes are included.

    Returns (bundle_dir, zip_path).
    """
    if not _is_unity_project(project_root):
        raise RuntimeError(
            f"{project_root} does not look like a Unity project "
            "(missing Assets/ or ProjectSettings/)."
        )

    scan = scan_project(project_root)

    # Apply scene filter if provided
    if scene_filter is not None:
        # Normalise separators for cross-platform comparison
        norm = {p.replace("\\", "/") for p in scene_filter}
        scan.scenes  = [s for s in scan.scenes  if s.rel_path.replace("\\", "/") in norm]
        scan.prefabs = [p for p in scan.prefabs if p.rel_path.replace("\\", "/") in norm]

    bundle_dir = output_dir / f"{project_root.name}_bundle"
    bundle_dir.mkdir(parents=True, exist_ok=True)

    files_to_zip: List[Path] = []

    def want(key: str) -> bool:
        return only is None or key in only

    def write(name: str, content: str) -> Path:
        p = bundle_dir / name
        _safe_write(p, content)
        files_to_zip.append(p)
        return p

    if want("summary"):
        write("SUMMARY.md", _render_summary(scan))
    if want("scripts"):
        write("SCRIPTS.md", _render_scripts_md(scan))
    if want("hierarchy"):
        write("HIERARCHY.txt", _render_hierarchy(scan, min_only=False))
    if want("hierarchy_min"):
        write("HIERARCHY_MIN.txt", _render_hierarchy(scan, min_only=True))
    if want("assemblies"):
        write("ASSEMBLIES.txt", _render_assemblies(scan))
    if want("scenes"):
        write("SCENES.txt", _render_scenes(scan))
    if want("manifest"):
        write(
            "MANIFEST.json",
            json.dumps(_render_manifest(scan, project_root), ensure_ascii=False, indent=2),
        )

    if not files_to_zip:
        raise ValueError("No files selected — check your --only filter.")

    zip_path = output_dir / f"{project_root.name}_bundle.zip"
    if zip_path.exists():
        zip_path.unlink()

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in files_to_zip:
            z.write(p, arcname=p.name)

    return bundle_dir, zip_path
