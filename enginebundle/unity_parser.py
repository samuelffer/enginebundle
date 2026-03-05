"""Unity asset parsers.

Handles:
- .asmdef / .asmref  (JSON)
- .unity / .prefab   (Unity YAML dialect - not standard YAML)
- ProjectSettings/TagManager.asset (YAML, for tags/layers)

Unity YAML uses non-standard tags like !u!114 &123456 and is intentionally
parsed with lightweight regex/line scanning, not a full YAML parser.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Assembly Definition (.asmdef)
# ---------------------------------------------------------------------------

@dataclass
class AsmdefInfo:
    name: str
    rel_path: str
    references: List[str]           # other assembly names this depends on
    include_platforms: List[str]
    exclude_platforms: List[str]
    allow_unsafe: bool
    auto_referenced: bool


def parse_asmdef(path: Path, project_root: Path) -> Optional[AsmdefInfo]:
    """Parse a .asmdef or .asmref file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(data, dict):
        return None

    name = data.get("name") or path.stem
    references = data.get("references") or []
    # Strip GUID: prefix if present (newer Unity format)
    references = [
        r.split(":")[-1].strip() if r.startswith("GUID:") else r
        for r in references
        if isinstance(r, str)
    ]

    return AsmdefInfo(
        name=name,
        rel_path=str(path.relative_to(project_root)),
        references=references,
        include_platforms=data.get("includePlatforms") or [],
        exclude_platforms=data.get("excludePlatforms") or [],
        allow_unsafe=bool(data.get("allowUnsafeCode", False)),
        auto_referenced=bool(data.get("autoReferenced", True)),
    )


# ---------------------------------------------------------------------------
# Unity YAML - Scene (.unity) and Prefab (.prefab)
# ---------------------------------------------------------------------------

# Unity YAML block header: --- !u!<typeId> &<fileId>
_BLOCK_HEADER = re.compile(r"^--- !u!(\d+) &(\d+)", re.MULTILINE)

# MonoBehaviour script reference:  m_Script: {fileID: 11500000, guid: abc123, type: 3}
_SCRIPT_GUID_RE = re.compile(
    r"m_Script:\s*\{[^}]*guid:\s*([a-f0-9]{32})[^}]*\}"
)

# GameObject name:  m_Name: PlayerController
_GAMEOBJECT_NAME_RE = re.compile(r"^\s+m_Name:\s+(.+)$", re.MULTILINE)

# Component list entry:  - component: {fileID: 123456}
_COMPONENT_REF_RE = re.compile(r"component:\s*\{fileID:\s*(\d+)\}")

# Unity type IDs we care about
_TYPE_GAMEOBJECT = "1"
_TYPE_MONOBEHAVIOUR = "114"
_TYPE_TRANSFORM = "4"
_TYPE_RECT_TRANSFORM = "224"


@dataclass
class SceneObject:
    """A GameObject extracted from a .unity or .prefab file."""
    name: str
    file_id: str
    script_guids: List[str] = field(default_factory=list)   # GUIDs of attached MonoBehaviours
    component_ids: List[str] = field(default_factory=list)  # fileIDs of components


@dataclass
class UnitySceneInfo:
    rel_path: str
    objects: List[SceneObject] = field(default_factory=list)
    # guid -> list of object names that use it (populated by generator)
    guid_usages: Dict[str, List[str]] = field(default_factory=dict)


def parse_unity_yaml(path: Path, project_root: Path) -> Optional[UnitySceneInfo]:
    """Lightly parse a .unity or .prefab file for GameObjects and script GUIDs."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    rel_path = str(path.relative_to(project_root))
    info = UnitySceneInfo(rel_path=rel_path)

    # Split into blocks by header
    headers = list(_BLOCK_HEADER.finditer(text))
    if not headers:
        return info

    blocks: Dict[str, tuple[str, str]] = {}  # file_id -> (type_id, block_text)
    for i, h in enumerate(headers):
        type_id = h.group(1)
        file_id = h.group(2)
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        blocks[file_id] = (type_id, text[start:end])

    # First pass: collect GameObjects with names and component refs
    go_map: Dict[str, SceneObject] = {}
    for file_id, (type_id, block) in blocks.items():
        if type_id != _TYPE_GAMEOBJECT:
            continue
        name_match = _GAMEOBJECT_NAME_RE.search(block)
        name = name_match.group(1).strip() if name_match else f"GameObject_{file_id}"
        component_ids = _COMPONENT_REF_RE.findall(block)
        go_map[file_id] = SceneObject(
            name=name,
            file_id=file_id,
            component_ids=component_ids,
        )

    # Second pass: collect MonoBehaviour script GUIDs and map to GameObjects
    mono_to_go: Dict[str, str] = {}  # monobehaviour file_id -> go file_id
    for go_file_id, obj in go_map.items():
        for comp_id in obj.component_ids:
            if comp_id in blocks and blocks[comp_id][0] == _TYPE_MONOBEHAVIOUR:
                mono_to_go[comp_id] = go_file_id

    for file_id, (type_id, block) in blocks.items():
        if type_id != _TYPE_MONOBEHAVIOUR:
            continue
        guid_match = _SCRIPT_GUID_RE.search(block)
        if not guid_match:
            continue
        guid = guid_match.group(1)
        go_file_id = mono_to_go.get(file_id)
        if go_file_id and go_file_id in go_map:
            go_map[go_file_id].script_guids.append(guid)

    info.objects = list(go_map.values())
    return info


# ---------------------------------------------------------------------------
# .meta file - maps script file path -> GUID
# ---------------------------------------------------------------------------

_META_GUID_RE = re.compile(r"^guid:\s*([a-f0-9]{32})", re.MULTILINE)


def parse_meta_guid(meta_path: Path) -> Optional[str]:
    """Extract the GUID from a .meta file."""
    try:
        text = meta_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = _META_GUID_RE.search(text)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# TagManager.asset - tags and layers
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"- (\w+)")
_LAYER_RE = re.compile(r"^\s+(\d+):\s+(.+)$", re.MULTILINE)
_TAGS_SECTION_RE = re.compile(r"m_Tags:(.*?)(?=\n  m_|\Z)", re.DOTALL)
_LAYERS_SECTION_RE = re.compile(r"m_Layers:(.*?)(?=\n  m_|\Z)", re.DOTALL)


@dataclass
class TagLayerInfo:
    tags: List[str] = field(default_factory=list)
    layers: Dict[int, str] = field(default_factory=dict)   # index -> name


def parse_tag_manager(path: Path) -> Optional[TagLayerInfo]:
    """Parse ProjectSettings/TagManager.asset for tags and named layers."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    info = TagLayerInfo()

    tags_m = _TAGS_SECTION_RE.search(text)
    if tags_m:
        info.tags = [
            t.strip().strip('"')
            for t in _TAG_RE.findall(tags_m.group(1))
            if t.strip()
        ]

    layers_m = _LAYERS_SECTION_RE.search(text)
    if layers_m:
        for idx_str, name in _LAYER_RE.findall(layers_m.group(1)):
            name = name.strip().strip('"')
            if name:
                info.layers[int(idx_str)] = name

    return info
