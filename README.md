# EngineBundle

> AI context generator for Unity projects.

EngineBundle scans your Unity project and produces a compact bundle of structured files that let an AI understand your entire codebase — without you needing to paste every script manually.

## What it generates

| File | Description |
|---|---|
| `SUMMARY.md` | High-level overview — paste this into your AI first |
| `SCRIPTS.md` | All C# types: namespace, base class, interfaces, Unity messages |
| `HIERARCHY_MIN.txt` | Low-token project tree (scripts + asmdefs + scenes only) |
| `HIERARCHY.txt` | Full project tree |
| `ASSEMBLIES.txt` | Assembly definition graph and dependencies |
| `SCENES.txt` | GameObjects per scene/prefab with attached scripts |
| `MANIFEST.json` | Counts and provenance |

## Install

```bash
pip install enginebundle
```

Or clone and run directly:

```bash
git clone https://github.com/samuelffer/enginebundle
cd enginebundle
pip install -e .
```

## Usage

### Interactive mode (no arguments)

```bash
enginebundle
```

### Command line

```bash
enginebundle build ./MyUnityProject
enginebundle build ./MyUnityProject --output ./bundles
```

### Python module

```bash
python -m enginebundle build ./MyUnityProject
```

## How to use the bundle with AI

1. Upload the `.zip` or paste `SUMMARY.md` into your AI tool.
2. The AI now understands your project structure and architecture.
3. Ask the AI to read a **specific script** only when needed — this keeps token usage low.
4. Use `SCENES.txt` to tell the AI which scripts are active on which GameObjects.

## Requirements

- Python 3.10+
- No external dependencies

## Why no .exe?

EngineBundle is distributed as a Python package to avoid false-positive antivirus detections that affect compiled Python executables. Install via `pip` for the cleanest experience.
