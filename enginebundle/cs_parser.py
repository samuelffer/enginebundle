"""C# script parser for Unity projects.

Extracts namespace, class name, base class, interfaces, and MonoBehaviour
Unity messages (Awake, Start, Update, etc.) without a full C# parser.
Uses regex-based heuristics, intentionally dependency-free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


# Unity MonoBehaviour lifecycle methods worth surfacing
UNITY_MESSAGES = {
    "Awake", "OnEnable", "Start", "FixedUpdate", "Update", "LateUpdate",
    "OnDisable", "OnDestroy", "OnTriggerEnter", "OnTriggerExit",
    "OnCollisionEnter", "OnCollisionExit", "OnApplicationQuit",
    "OnBecameVisible", "OnBecameInvisible", "Reset",
    # UI
    "OnPointerClick", "OnPointerDown", "OnPointerUp", "OnPointerEnter",
    "OnPointerExit", "OnDrag", "OnBeginDrag", "OnEndDrag",
    # Networking (Netcode / Mirror / Photon common names)
    "OnNetworkSpawn", "OnNetworkDespawn", "OnStartClient", "OnStartServer",
}

_STRIP_COMMENT_LINE = re.compile(r"//[^\n]*")
_STRIP_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRIP_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_STRIP_CHAR = re.compile(r"'(?:[^'\\]|\\.)'")

_NAMESPACE_RE = re.compile(
    r"\bnamespace\s+([\w.]+)\s*[{;]"
)
_CLASS_RE = re.compile(
    r"""
    (?:public|internal|private|protected|static|abstract|sealed|partial|\s)*
    \b(?:class|struct)\b\s+
    (?P<name>[A-Za-z_][A-Za-z0-9_]*)
    (?:\s*<[^>]*>)?           # optional generic params
    (?:\s*:\s*(?P<bases>[^{]+))?  # optional base list
    \s*\{
    """,
    re.VERBOSE,
)
_METHOD_RE = re.compile(
    r"\bvoid\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\("
)
_USING_RE = re.compile(r"^\s*using\s+([\w.]+)\s*;", re.MULTILINE)


def _strip_code(src: str) -> str:
    """Remove comments and string literals to avoid false matches."""
    src = _STRIP_COMMENT_BLOCK.sub(" ", src)
    src = _STRIP_COMMENT_LINE.sub(" ", src)
    src = _STRIP_STRING.sub('""', src)
    src = _STRIP_CHAR.sub("''", src)
    return src


@dataclass
class CsType:
    """Represents a single C# class or struct found in a .cs file."""
    name: str
    kind: str                       # "class" | "struct"
    namespace: Optional[str]
    base_class: Optional[str]
    interfaces: List[str]
    is_mono_behaviour: bool
    is_scriptable_object: bool
    unity_messages: List[str]       # lifecycle methods implemented
    file_path: str                  # relative to project root


@dataclass
class CsFileInfo:
    """All types found in a single .cs file."""
    rel_path: str
    usings: List[str]
    types: List[CsType] = field(default_factory=list)


def _parse_bases(bases_str: str) -> tuple[Optional[str], list[str]]:
    """Split 'BaseClass, IInterface1, IInterface2' into (base, [ifaces])."""
    if not bases_str:
        return None, []

    parts = [p.strip() for p in bases_str.split(",")]
    parts = [re.sub(r"<[^>]*>", "", p).strip() for p in parts]
    parts = [p for p in parts if p]

    if not parts:
        return None, []

    # Heuristic: interfaces start with 'I' followed by uppercase, or contain
    # known Unity base names. First non-interface is the base class.
    base: Optional[str] = None
    interfaces: list[str] = []

    for p in parts:
        # Remove generic suffix for display
        short = p.split("<")[0].strip()
        # Simple heuristic: IFoo pattern = interface
        if re.match(r"^I[A-Z]", short):
            interfaces.append(short)
        elif base is None:
            base = short
        else:
            # Could be another base or interface we can't distinguish; treat as interface
            interfaces.append(short)

    return base, interfaces


def parse_cs_file(path: Path, project_root: Path) -> CsFileInfo:
    """Parse a .cs file and return extracted type information."""
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return CsFileInfo(rel_path=str(path.relative_to(project_root)), usings=[])

    rel_path = str(path.relative_to(project_root))
    stripped = _strip_code(src)

    usings = _USING_RE.findall(src)

    # Find namespace(s) - take first one found (most files have one)
    ns_match = _NAMESPACE_RE.search(stripped)
    namespace: Optional[str] = ns_match.group(1) if ns_match else None

    # Find all void methods for Unity message detection
    method_names = {m.group("name") for m in _METHOD_RE.finditer(stripped)}
    found_messages = sorted(method_names & UNITY_MESSAGES)

    types: list[CsType] = []
    for m in _CLASS_RE.finditer(stripped):
        class_name = m.group("name")
        kind = "struct" if "struct" in m.group(0) else "class"
        bases_raw = m.group("bases") or ""
        base_class, interfaces = _parse_bases(bases_raw)

        is_mono = base_class in {"MonoBehaviour", "NetworkBehaviour"}
        is_so = base_class == "ScriptableObject"

        types.append(
            CsType(
                name=class_name,
                kind=kind,
                namespace=namespace,
                base_class=base_class,
                interfaces=interfaces,
                is_mono_behaviour=is_mono,
                is_scriptable_object=is_so,
                unity_messages=found_messages if is_mono else [],
                file_path=rel_path,
            )
        )

    return CsFileInfo(rel_path=rel_path, usings=usings, types=types)
