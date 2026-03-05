"""Basic tests for enginebundle."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from enginebundle.cs_parser import parse_cs_file
from enginebundle.unity_parser import parse_asmdef
from enginebundle.generator import create_bundle


# ---------------------------------------------------------------------------
# Fixtures - fake Unity project
# ---------------------------------------------------------------------------

@pytest.fixture
def unity_project(tmp_path: Path) -> Path:
    """Create a minimal fake Unity project."""
    root = tmp_path / "MyGame"
    assets = root / "Assets"
    settings = root / "ProjectSettings"
    scripts = assets / "Scripts"
    scripts.mkdir(parents=True)
    settings.mkdir(parents=True)

    # C# script
    (scripts / "PlayerController.cs").write_text(
        """
using UnityEngine;

namespace MyGame.Player
{
    public class PlayerController : MonoBehaviour
    {
        void Awake() {}
        void Update() {}
    }
}
""",
        encoding="utf-8",
    )

    # .meta for guid resolution
    (scripts / "PlayerController.cs.meta").write_text(
        "fileFormatVersion: 2\nguid: abcdef1234567890abcdef1234567890\n",
        encoding="utf-8",
    )

    # .asmdef
    (assets / "MyGame.asmdef").write_text(
        json.dumps({
            "name": "MyGame",
            "references": [],
            "allowUnsafeCode": False,
            "autoReferenced": True,
        }),
        encoding="utf-8",
    )

    # Minimal .unity scene
    (assets / "SampleScene.unity").write_text(
        """%YAML 1.1
%TAG !u! tag:unity3d.com,2011:
--- !u!1 &100000
GameObject:
  m_Name: Player
  m_Component:
  - component: {fileID: 114000}
--- !u!114 &114000
MonoBehaviour:
  m_Script: {fileID: 11500000, guid: abcdef1234567890abcdef1234567890, type: 3}
""",
        encoding="utf-8",
    )

    # TagManager
    (settings / "TagManager.asset").write_text(
        """%YAML 1.1
TagManager:
  m_Tags:
  - Player
  - Enemy
  m_Layers:
  - 0:
  - 1:
  - 8: Ground
  - 9: Player
""",
        encoding="utf-8",
    )

    return root


# ---------------------------------------------------------------------------
# cs_parser tests
# ---------------------------------------------------------------------------

def test_parse_cs_file(unity_project: Path) -> None:
    path = unity_project / "Assets" / "Scripts" / "PlayerController.cs"
    info = parse_cs_file(path, unity_project)

    assert info.rel_path.endswith("PlayerController.cs")
    assert len(info.types) == 1
    t = info.types[0]
    assert t.name == "PlayerController"
    assert t.namespace == "MyGame.Player"
    assert t.base_class == "MonoBehaviour"
    assert t.is_mono_behaviour
    assert "Awake" in t.unity_messages
    assert "Update" in t.unity_messages


# ---------------------------------------------------------------------------
# unity_parser tests
# ---------------------------------------------------------------------------

def test_parse_asmdef(unity_project: Path) -> None:
    path = unity_project / "Assets" / "MyGame.asmdef"
    info = parse_asmdef(path, unity_project)
    assert info is not None
    assert info.name == "MyGame"
    assert info.references == []
    assert not info.allow_unsafe
    assert info.auto_referenced


# ---------------------------------------------------------------------------
# Generator / integration test
# ---------------------------------------------------------------------------

def test_create_bundle(unity_project: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "out"
    bundle_dir, zip_path = create_bundle(unity_project, output_dir=out_dir)

    assert bundle_dir.is_dir()
    assert zip_path.exists()

    # Check expected files exist
    for fname in ["SUMMARY.md", "SCRIPTS.md", "HIERARCHY.txt",
                  "HIERARCHY_MIN.txt", "ASSEMBLIES.txt", "SCENES.txt", "MANIFEST.json"]:
        assert (bundle_dir / fname).exists(), f"Missing {fname}"

    # SUMMARY should mention MonoBehaviour count
    summary = (bundle_dir / "SUMMARY.md").read_text(encoding="utf-8")
    assert "MonoBehaviour" in summary
    assert "PlayerController" in summary

    # SCRIPTS should have the class
    scripts_md = (bundle_dir / "SCRIPTS.md").read_text(encoding="utf-8")
    assert "PlayerController" in scripts_md
    assert "MyGame.Player" in scripts_md

    # ZIP should contain the files
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
    assert "SUMMARY.md" in names
    assert "SCRIPTS.md" in names


def test_create_bundle_invalid_path(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="does not look like a Unity project"):
        create_bundle(tmp_path / "NotAProject", output_dir=tmp_path / "out")
