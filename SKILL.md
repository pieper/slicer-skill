---
name: slicer
description: >
  Search and reason over 3D Slicer source code, extensions, and community
  discussions.  Use for questions about medical imaging, MRML scene graphs,
  VTK/ITK pipelines, Slicer Python scripting, C++ module development,
  extension development, Qt-based module UI, segmentation, volume rendering,
  DICOM workflows, and the Slicer build system.
version: "1.1"
setup: ./setup.sh
requires: [git, perl, bash]
---

# Slicer Skill

This repository contains the information and helper scripts needed by an AI coding agent ("skill")
that is designed to answer questions about the [3D Slicer](https://www.slicer.org) application and
its extension ecosystem.  It is intentionally generic so that it can be consumed by any tool that
understands the SKILLS.md convention (e.g. Claude Code, OpenAI agents, etc.).

## Setup Modes

The setup script (`./setup.sh`) supports three modes, balancing disk usage against
query speed.  On first run it prompts the user to choose; subsequent runs reuse the
previous choice.  The mode is recorded in `.setup-stamp.json` (the `"mode"` field).

| Mode          | Disk    | Setup time | What's local                                         |
| ------------- | ------- | ---------- | ---------------------------------------------------- |
| **full**      | ~15 GB  | ~20 min    | Source, deps, all 200+ extensions, discourse archive  |
| **lightweight**| ~1 GB  | ~2 min     | Source + ExtensionsIndex metadata (JSON only)         |
| **web**       | minimal | instant    | Nothing cloned — all access via web APIs              |

You can override interactively or via `./setup.sh --mode full|lightweight|web`.

**Determining the active mode:** Read `.setup-stamp.json` in the skill workspace.
The `"mode"` field will be `"full"`, `"lightweight"`, or `"web"`.  If the file does
not exist, setup has not been run yet — run `./setup.sh` first.

## Goal

Depending on the active mode, the skill provides access to these resources either
locally (via cloned repositories) or remotely (via web APIs):

1. The **Slicer source code** – the official C++/Python repositories that make up the
   application.  *(Local in full and lightweight modes.)*
2. The **Extensions Index** – a machine‑readable list of third‑party extensions and their
   repositories.  The skill should iterate through the index files and clone each listed
   repository so that extension code is available for searching.
3. The **Discourse archive** – a mirror of the Slicer Discourse forum content (see
   https://github.com/pieper/slicer-discourse-archive) to allow question‑answering based on
   past community discussions.
4. The **NA-MIC Project Week repository** – a collection of tutorials, presentations, and code
   from NA-MIC Project Weeks (https://github.com/NA-MIC/ProjectWeek), providing additional
   examples and educational materials for Slicer programming. The `scripts/extract_pw_stats.py`
   utility can be used to extract statistics about project weeks, including participant lists
   and project counts. This is useful for finding papers, collaborations, or expertise by
   searching for people who have attended NA-MIC Project Weeks.
5. **Build dependencies** (VTK, ITK, CTK, etc.) – cloned from SuperBuild references.
   *(Local in full mode only.  In other modes, browse via GitHub API.)*
6. **Coding conversations** (optional) – JSONL transcripts of AI-assisted coding sessions
   captured by the [CodingChats](https://github.com/pieper/CodingChats) VS Code extension.
   *(Cloned in full and lightweight modes when the repo exists.)*

With these resources available locally, the agent can use standard command‑line tools
(`git grep`, `grep`, `find`, etc.) to search for symbols, examples, documentation,
Python modules, build configurations, and other snippets that help it craft accurate and
precise responses to programming questions about Slicer.

> 📁 Repositories are checked out into subdirectories of the skill workspace named
> `slicer-source`, `slicer-extensions`, `slicer-discourse`, `slicer-dependencies`,
> `slicer-projectweek`, and `coding-chats` respectively.  You are free to override these paths by setting the
> `SLICER_SRC_DIR`, `SLICER_EXT_DIR`, `SLICER_DISCOURSE_DIR`, `SLICER_DEP_DIR`,
> `SLICER_PROJECTWEEK_DIR`, and `CODING_CHATS_DIR` environment variables before running the setup script.

---

## Prerequisites

The setup script requires the following tools to be available on `$PATH`:

- **git** – for cloning and updating repositories (not needed in web mode).
- **perl** – used to parse CMake files when resolving SuperBuild dependencies (full mode only).
- **bash** – the setup script targets Bash (macOS `/bin/bash` or Linux).

On macOS the built-in versions of these tools are sufficient. On minimal Linux
containers you may need to install `perl` and `git` explicitly.

For **web mode**, the agent needs network access to `github.com` and
`discourse.slicer.org`.  If the `gh` CLI is authenticated, GitHub API rate limits
are much higher (5,000/hr vs 60/hr unauthenticated).

---

## Setup Instructions

Run the setup script:

```sh
./setup.sh              # interactive mode selection on first run
./setup.sh --mode full  # or: lightweight, web
./setup.sh --force      # re-run ignoring 24h cooldown
```

The script checks available disk space and warns if full mode may be tight.
On first run it presents an interactive menu; subsequent runs reuse the previous
choice (stored in `.setup-stamp.json`).

### What each mode creates

**Full mode** creates/updates:
- `slicer-source` – `git clone` of `https://github.com/Slicer/Slicer.git`
- `slicer-extensions` – ExtensionsIndex + all ~200 extension repositories
- `slicer-discourse` – `git clone` of the discourse archive
- `slicer-dependencies` – SuperBuild dependency repositories (VTK, ITK, CTK, etc.)
- `CodingChats-conversations` (optional) – coding session transcripts

**Lightweight mode** creates/updates:
- `slicer-source` – same as full
- `slicer-extensions` – ExtensionsIndex JSON metadata only (no extension repo clones)
- `CodingChats-conversations` (optional) – same as full

**Web mode** creates:
- Only `.setup-stamp.json` — no repositories are cloned

The script is idempotent; re-running it will `git pull` existing clones rather than cloning
afresh.  On completion it writes a `.setup-stamp.json` timestamp file. Subsequent runs
automatically skip if the stamp is less than 24 hours old. Pass `--force` to bypass the
age check.

### Switching modes

To switch from one mode to another, pass the new mode explicitly:

```sh
./setup.sh --force --mode full    # upgrade from lightweight to full
./setup.sh --force --mode web     # downgrade (existing clones remain on disk)
```

Note: switching to a lighter mode does not delete existing clones. The agent will
simply stop using them. Delete the directories manually if you want to reclaim space.

### Verifying the setup

```sh
cat .setup-stamp.json   # check "mode" field
# Full mode:
ls slicer-source/CMakeLists.txt slicer-extensions/README.md slicer-discourse/README.md slicer-dependencies/VTK
# Lightweight mode:
ls slicer-source/CMakeLists.txt slicer-extensions/README.md
# Web mode: nothing to verify locally
```

---

## How the Agent Should Use the Data

**First, check the active mode** by reading `.setup-stamp.json` in the skill workspace
and looking at the `"mode"` field.  This determines which search strategies are available.

> **Performance hint:** If you are in `web` or `lightweight` mode and a task is taking
> noticeably long due to repeated API calls, rate-limit errors, or slow web fetches,
> proactively suggest that the user upgrade to a heavier mode for faster local searches.
> For example: *"This search is slow over the GitHub API. You could switch to
> lightweight mode (`./setup.sh --force --mode lightweight`) to get local source
> for much faster grep, or full mode (`./setup.sh --force --mode full`) to also
> have extensions, dependencies, and the discourse archive on disk."*
> Match the suggestion to what's actually bottlenecking — if only Discourse searches
> are slow, suggest full mode; if code search is slow, lightweight may suffice.

### Local search strategies (full and lightweight modes)

When repositories are available locally, search, read, and reason over them:

- **Search for code symbols** across `slicer-source`, extension subdirectories, or
  `slicer-dependencies`.
  CLI example: `git -C slicer-source grep -rn "vtkSmartPointer"`.
- **Find files by name or pattern** — locate headers, Python modules, CMake configs, etc.
  CLI example: `find slicer-source -name "*Logic.h"`.
- **Query the discourse archive** (full mode) for community discussions about a topic.
  CLI example: `grep -rn "SegmentEditor" slicer-discourse`.
- **Inspect build dependencies** (full mode) in `slicer-dependencies` when reasoning about
  build-time behavior, API versions, or exact tags used by the SuperBuild.
  CLI example: `git -C slicer-dependencies/VTK grep -rn "vtkNew"`.
- **Search coding conversations** in `CodingChats-conversations/sessions/` for past AI sessions
  that discuss the same topic.  These JSONL transcripts show what approaches were
  tried, what failed, and what prompts led to working solutions.
  CLI example: `grep -rn "SegmentEditor" CodingChats-conversations/sessions/`.
- **Understand project structure** by reading CMakeLists, Python `__init__.py` files, and
  other configuration files in the clones.

> Agents with higher-level file search and content search tools (e.g. Glob, Grep, Read)
> should prefer those over raw shell commands when available.  The CLI examples above are
> provided for reference and for agents that only have shell access.

### Extension documentation search (full mode)

In full mode, all extension repositories are pre-cloned into `slicer-extensions/`. When
researching a topic, **explicitly check documentation files in all extension directories**
for additional help and examples. Many extensions contain valuable tutorials, usage examples,
and implementation details that complement the main Slicer documentation.

Look for files like:
- `README.md` - General extension documentation and usage
- `SKILL.md` - AI agent guidance (if present, similar to this file)
- Documentation in `docs/`, `Documentation/`, or similar directories
- Tutorial files, example scripts, and implementation guides

- **Search extension docs**: `find slicer-extensions/ -name "*.md" -exec grep -l "<topic>" {} \;`
- **Read extension documentation**: Check `slicer-extensions/<ExtensionName>/README.md` and
  `slicer-extensions/<ExtensionName>/SKILL.md` (if present) for usage examples and guidance
- **Browse extension source**: Look at `slicer-extensions/<ExtensionName>/` for code examples
  and module implementations

### On-demand extension cloning (lightweight mode)

In lightweight mode, extension source code is not pre-cloned. When you need to search
or read an extension's source:

1. Find the extension's repository URL from the ExtensionsIndex JSON file:
   `grep -l "ExtensionName" slicer-extensions/*.json` then read the `"scm_url"` field.
2. Clone it on-demand: `git clone --depth 1 <url> slicer-extensions/<name>`
3. The clone persists for future queries in the same session.

### Web-based search strategies (lightweight fallback and web mode)

When local data is not available, use these web APIs:

- **Discourse search**: `curl -s "https://discourse.slicer.org/search.json?q=<query>"`
  Supports filters: `category:support`, `order:latest`, `after:2025-01-01`.
  Read a topic: `curl -s "https://discourse.slicer.org/t/<id>.json"`
- **GitHub code search** (requires `gh` CLI): `gh search code "<query>" --repo Slicer/Slicer --limit 20`
- **Read files from GitHub**: `gh api repos/Slicer/Slicer/contents/<path> --jq '.content' | base64 -d`
  Or raw URLs: `https://raw.githubusercontent.com/Slicer/Slicer/main/<path>`
- **Extension metadata**: `gh api repos/Slicer/ExtensionsIndex/contents/<Name>.json --jq '.content' | base64 -d`
- **Dependency source**: `gh search code "<query>" --repo Kitware/VTK --limit 20`

### Search strategy by mode

| Resource         | full               | lightweight           | web                    |
| ---------------- | ------------------ | --------------------- | ---------------------- |
| Slicer source    | local grep/find    | local grep/find       | GitHub API / raw URLs  |
| Extensions       | local grep/find    | on-demand clone       | GitHub API / raw URLs  |
| Dependencies     | local grep/find    | GitHub API / raw URLs | GitHub API / raw URLs  |
| Discourse        | local grep         | Discourse search API  | Discourse search API   |
| GitHub Issues/PRs| GitHub search API  | GitHub search API     | GitHub search API      |
| Coding chats     | local grep         | local grep            | not available          |

The goal is not merely to index, but to *reason* over the material.  For example, when
asked "how do I add a module to the build", the agent can search CMake macros in
`slicer-source` (or via GitHub API) and provide a snippet of the real call sites.

### Script Repository

The Slicer source tree contains a rich collection of scripted examples and utilities
under the **Script Repository** section of the documentation (located in
`slicer-source/Docs/developer_guide/script_repository.md` and related files).  When
implementing or explaining Slicer features, agents should **search the script repository
first** — it contains working Python snippets that demonstrate how to accomplish common
tasks such as:

- Loading and saving data (volumes, models, segmentations, transforms, etc.)
- Manipulating MRML nodes and the scene graph
- Working with the Segment Editor and its effects
- Creating and updating views, layouts, and widget properties
- Accessing volume data as NumPy arrays via `slicer.util.arrayFromVolume`
- Running CLI modules and connecting to module logic classes
- Registering custom keyboard shortcuts, timers, and event observers

These snippets are the closest equivalent to "official cookbook recipes" and are
frequently more accurate and idiomatic than ad-hoc code generation.  When answering a
user's question, prefer citing or adapting a script repository example over writing code
from scratch.

The script repository is assembled from per-topic markdown files.  The main entry point
is `slicer-source/Docs/developer_guide/script_repository.md`, which includes:

| File                          | Topics covered                                      |
| ----------------------------- | --------------------------------------------------- |
| `script_repository/gui.md`    | Layouts, views, widget access, keyboard shortcuts   |
| `script_repository/volumes.md`| Loading volumes, NumPy access, scalar/vector data   |
| `script_repository/segmentations.md` | Segment Editor, effects, import/export       |
| `script_repository/transforms.md`    | Linear and non-linear transforms             |
| `script_repository/markups.md`       | Fiducials, curves, planes, ROIs              |
| `script_repository/models.md`        | Surface meshes, polydata, model display      |
| `script_repository/dicom.md`         | DICOM loading, exporting, database           |
| `script_repository/plots.md`         | Chart views and plot series                  |
| `script_repository/sequences.md`     | Time sequences, browsing, replay             |
| `script_repository/registration.md`  | Image registration workflows                 |
| `script_repository/screencapture.md` | Screenshots, video, 3D export                |
| `script_repository/subjecthierarchy.md` | Subject hierarchy tree operations         |
| `script_repository/tractography.md`  | Diffusion tractography                       |
| `script_repository/batch.md`         | Batch processing patterns                    |
| `script_repository/webserver.md`     | Slicer web server API                        |

When searching for an example, grep within these files by topic keyword rather than
searching the entire source tree.

---

## Slicer Architecture — Where to Learn About Key Concepts

Rather than duplicating Slicer's documentation, this section tells you **where to look**
in the checked-out repositories to learn about each major concept.  Read the referenced
files directly when you need to understand or explain a topic.

### Project Structure

Inspect `slicer-source/` to understand the top-level layout:

- `Base/` — Core application framework.
  - `Base/Python/slicer/` — The `slicer` Python package (`util.py`, `ScriptedLoadableModule.py`, etc.).  Read these to understand the Python API surface.
  - `Base/QTCore/` — Non-GUI application logic (settings, I/O manager, module factory).
  - `Base/QTGUI/` — Main application GUI (layout manager, module panel, data widgets).
  - `Base/Logic/` — Application-level logic classes.
- `Libs/` — Shared libraries that do not depend on Qt.
  - `Libs/MRML/Core/` — The MRML scene graph: node classes, events, serialization.  Header files (`vtkMRML*.h`) document the node hierarchy.
  - `Libs/vtkSegmentationCore/` — Segmentation data structures and conversion logic.
  - `Libs/vtkITK/` — VTK/ITK bridge filters.
  - `Libs/vtkTeem/` — Teem-based readers (NRRD, DWI).
- `Modules/` — Built-in modules, organized by type:
  - `Modules/Loadable/` — C++ modules with Qt UI (Volumes, Segmentations, Markups, Transforms, Models, VolumeRendering, etc.).
  - `Modules/Scripted/` — Python-only modules (SegmentEditor, DICOM, SampleData, ExtensionWizard, SegmentStatistics, etc.).
  - `Modules/CLI/` — Command-line interface modules (filters, registration, model makers).
- `Docs/developer_guide/` — Developer documentation in Markdown/RST.
- `SuperBuild/` — CMake `External_*.cmake` files that define each dependency's repository URL, tag, and build flags.

### Module Types

Slicer has three module types.  To understand the conventions for each, read these
reference implementations:

- **Scripted modules**: Read `slicer-source/Modules/Scripted/SampleData/` or
  `slicer-source/Modules/Scripted/SegmentStatistics/` for the standard pattern:
  a module class, a widget class, a logic class, and a test class, all in Python.
  The base classes are defined in `slicer-source/Base/Python/slicer/ScriptedLoadableModule.py`.
- **Loadable modules** (C++ with Qt UI): Read `slicer-source/Modules/Loadable/Volumes/`
  or `slicer-source/Modules/Loadable/Markups/` for the pattern: a `qSlicer*Module` class,
  a widget, a logic, and MRML node classes, built with CMake.
- **CLI modules**: Read `slicer-source/Modules/CLI/AddScalarVolumes/` for the minimal
  pattern: an XML description file and a C++ (or Python) executable using
  `SlicerExecutionModel`.

For an overview document, read `slicer-source/Docs/developer_guide/module_overview.md`.

### MRML (Medical Reality Markup Language)

MRML is the in-memory scene graph that holds all data.  To learn about it:

- Read `slicer-source/Docs/developer_guide/mrml_overview.md` for the conceptual overview.
- Read `slicer-source/Docs/developer_guide/mrml.md` for the developer reference.
- Browse header files in `slicer-source/Libs/MRML/Core/` — each `vtkMRML*Node.h` file
  documents a node type (volume, model, segmentation, transform, display, storage, etc.).
- For the Python API to the scene, read `slicer-source/Base/Python/slicer/util.py` —
  functions like `getNode()`, `loadVolume()`, `arrayFromVolume()`, and `updateVolumeFromArray()`
  are defined there.

### Segment Editor

The Segment Editor is one of Slicer's most complex subsystems.  To understand it:

- Read `slicer-source/Modules/Scripted/SegmentEditor/` for the module and widget.
- Read the Python effects in
  `slicer-source/Modules/Loadable/Segmentations/EditorEffects/Python/SegmentEditorEffects/`
  — each file (`SegmentEditorThresholdEffect.py`, `SegmentEditorDrawEffect.py`, etc.)
  implements one effect and serves as a template for custom effects.
- Read the abstract base classes in the same directory
  (`AbstractScriptedSegmentEditorEffect.py`, etc.) to understand the effect API.
- Search the script repository file `script_repository/segmentations.md` for usage examples.

### VTK and ITK Patterns

When questions involve VTK or ITK classes:

- Search `slicer-dependencies/VTK/` for VTK header files and examples.
- Search `slicer-dependencies/ITK/` for ITK header files and examples.
- Read `slicer-source/Libs/vtkITK/` for how Slicer bridges VTK and ITK.
- Read `slicer-source/Docs/developer_guide/vtkAddon.md` for Slicer's VTK add-on utilities.
- For VTK pipeline patterns used in Slicer modules, browse `.cxx` files in
  `slicer-source/Modules/Loadable/` — these show real-world VTK pipeline construction,
  smart pointer usage, and observer patterns.

### Build System

Slicer uses CMake with a SuperBuild pattern:

- `slicer-source/CMakeLists.txt` — top-level build configuration.
- `slicer-source/SuperBuild/External_*.cmake` — one file per dependency, specifying the
  repository URL, git tag, and CMake arguments.  Read these to find the exact version of
  VTK, ITK, CTK, DCMTK, etc. that Slicer uses.
- `slicer-source/Docs/developer_guide/build_instructions/` — platform-specific build guides.
- For module-level CMake patterns, read `CMakeLists.txt` in any module under
  `Modules/Loadable/` or `Modules/CLI/`.

### Extension Development

To understand how extensions are structured and distributed:

- Read `slicer-source/Docs/developer_guide/extensions.md` for the developer guide.
- Inspect the Extension Wizard at `slicer-source/Modules/Scripted/ExtensionWizard/` —
  this is the tool that generates new extension scaffolding.
- Browse `slicer-extensions/` for real extension examples.  Well-structured extensions
  that demonstrate common patterns include:
  - `slicer-extensions/SlicerIGT/` — a multi-module C++ extension for image-guided therapy
    with loadable modules, transforms, and Qt widgets.
  - `slicer-extensions/MONAILabel/` — a Python extension integrating deep learning inference.
- The `slicer-extensions/` directory also contains `.json` index files
  (e.g. `SlicerIGT.json`).  These specify the repository URL, description, and
  dependencies for each extension.  Read them when you need to locate an extension's
  source repository.

### Python Utilities and the `slicer` Package

The `slicer` Python package is the primary API for scripting.  To understand it:

- Read `slicer-source/Base/Python/slicer/util.py` — this is the most important file.
  It defines data loading/saving functions, node access, array conversion, and
  UI utilities.
- Read `slicer-source/Base/Python/slicer/ScriptedLoadableModule.py` — defines the base
  classes for scripted modules (`ScriptedLoadableModule`, `ScriptedLoadableModuleWidget`,
  `ScriptedLoadableModuleLogic`, `ScriptedLoadableModuleTest`).
- Read `slicer-source/Base/Python/slicer/parameterNodeWrapper/` — the parameter node
  wrapper system for declarative module parameters.
- Read `slicer-source/Base/Python/slicer/__init__.py` for the top-level namespace
  (access to `slicer.mrmlScene`, `slicer.app`, `slicer.modules`, etc.).

### Coding Style and Conventions

Slicer spans multiple toolkits, each with its own style.  To understand what conventions
to follow:

- Read `slicer-source/Docs/developer_guide/style_guide.md` — the primary style reference.
  It links to the VTK, Qt, and Python (PEP 8) conventions and explains when each applies.
- Read `slicer-source/CONTRIBUTING.md` for contribution guidelines.
- For Python style in Slicer modules specifically, examine existing scripted modules
  (e.g. `Modules/Scripted/SegmentStatistics/`) — they demonstrate Slicer naming
  conventions such as `onApplyButton`, `setParameterNode`, camelCase method names
  on widget classes, and the `logic`/`widget`/`test` class separation.
- For C++ style, browse `.cxx`/`.h` files in `Modules/Loadable/` and follow the VTK
  conventions: `vtkNew`, `vtkSmartPointer`, `SetX()`/`GetX()` accessors, and the
  `PrintSelf`/`Modified()` pattern.

### Testing

To understand how Slicer modules are tested:

- Each scripted module can include a test class derived from
  `ScriptedLoadableModuleTest` (defined in
  `slicer-source/Base/Python/slicer/ScriptedLoadableModule.py`).  The standard pattern
  is a `runTest()` method that calls individual test functions.
- See `slicer-source/Modules/Scripted/SegmentStatistics/Testing/` and
  `slicer-source/Modules/Scripted/SampleData/Testing/` for Python test examples.
- See `slicer-source/Modules/Scripted/SelfTests/` for the self-test runner module.
- For C++ module tests, browse `Testing/` subdirectories under modules in
  `Modules/Loadable/` — these use CTest and Google Test patterns.
- Read `slicer-source/Docs/developer_guide/debugging/` for debugging guides across
  platforms and IDEs (VS Code, Qt Creator, CLion, etc.).
- Read `slicer-source/Docs/developer_guide/python_faq.md` for common Python
  environment questions including `PythonSlicer`, virtual environments, and
  package installation.

### Discourse — Searching Community Knowledge

**Full mode (local archive):**

The discourse archive contains ~18,700 rendered forum topics organized by year and month:

```
slicer-discourse/archive/rendered-topics/
  2017/ 2018/ 2019/ 2020/ 2021/ 2022/ 2023/ 2024/ 2025/ 2026/
    YYYY-MM/
      YYYY-MM-DD-topic-slug-idNNNNN.md
```

Each file is a single Discourse thread rendered as Markdown.  The filename includes a
date, a human-readable slug, and the topic ID.  To search effectively:

- Grep across the archive for keywords (e.g. `grep -rn "arrayFromVolume" slicer-discourse/`).
- Use the `slicer-discourse/archive/INDEX.md` file for an overview.

**Lightweight and web modes:** Use the Discourse search API as described in the
"Web-based search strategies" section above.

In any mode, forum threads often explain *why* things work a certain way, not
just *how* — search the discourse when code-search alone is insufficient.

### GitHub Issues and Pull Requests

GitHub issues and PRs complement Discourse and source code in these situations:

- **Specific error message or traceback** — issues often contain exact error text and
  the accepted resolution.  Discourse posts frequently omit full tracebacks.
- **"Is this a known bug?"** — search closed issues to confirm and find workarounds.
- **"When was X added / why was Y removed?"** — PR descriptions explain the motivation
  for API changes in a way that `git log` alone does not.
- **Discovering undocumented pitfalls** — closed bug reports surface gotchas that never
  make it into official docs or the Common Pitfalls section below.

Use the GitHub REST API directly (no CLI dependency required):

```sh
# Search closed issues for a keyword or error message
curl -s "https://api.github.com/search/issues?q=<keyword>+repo:Slicer/Slicer+type:issue+state:closed&per_page=10"

# Search merged PRs for an API symbol or topic
curl -s "https://api.github.com/search/issues?q=<symbol>+repo:Slicer/Slicer+type:pr+is:merged&per_page=10"

# Read a specific issue or PR body
curl -s "https://api.github.com/repos/Slicer/Slicer/issues/<number>"
curl -s "https://api.github.com/repos/Slicer/Slicer/pulls/<number>"

# Fetch comments on an issue (where the resolution often appears)
curl -s "https://api.github.com/repos/Slicer/Slicer/issues/<number>/comments"
```

Unauthenticated requests are rate-limited to 60/hr; set a `GITHUB_TOKEN` environment
variable to raise this to 5,000/hr.  Prefer Discourse over issues/PRs for how-to and
workflow questions; prefer issues/PRs when debugging concrete errors or tracing API
evolution.

---

## Common Pitfalls

These are frequently encountered mistakes that are **not obvious from reading the source
code alone**.  The agent should be aware of them when generating or reviewing Slicer code.

- **`arrayFromVolume` returns a view, not a copy.** After modifying the array in-place,
  you must call `slicer.util.arrayFromVolumeModified(volumeNode)` to notify the
  display pipeline.  Forgetting this results in the view not updating.
- **MRML node names are not unique identifiers.** Multiple nodes can share the same name.
  Use `node.GetID()` for reliable identification, not `node.GetName()`.
- **The Python console runs on the main Qt thread.** Long-running operations block the
  UI.  Use `slicer.app.processEvents()` in loops or run work in a background thread
  with `qt.QTimer.singleShot()` callbacks.
- **Coordinate system conventions.** Slicer uses RAS (Right-Anterior-Superior) internally,
  while many file formats and tools use LPS (Left-Posterior-Superior).  Transforms
  between RAS and LPS are a common source of sign-flip bugs.
- **Volume axis ordering.** `slicer.util.arrayFromVolume()` returns arrays in KJI order
  (slice, row, column), not IJK.  This is the reverse of what many users expect.
- **Extension CMake patterns differ from standalone projects.** Extensions must use
  Slicer-specific CMake macros (e.g. `slicerMacroBuildScriptedModule`,
  `slicerMacroBuildLoadableModule`).  Using plain `add_library` will not integrate
  correctly with Slicer's module loading system.
- **`slicer.util.pip_install()` for runtime dependencies.** Slicer bundles its own Python
  environment.  Extensions should install additional Python packages via
  `slicer.util.pip_install("package")` in their module code, not via system pip.

---

## Common Workflows — Where to Find Each Step

Many Slicer tasks span multiple subsystems.  Rather than documenting full workflows here,
this section tells you which script repository files and source directories to consult
for each step of common multi-step tasks.

**Load DICOM data, segment a structure, export the result:**
1. DICOM import — `script_repository/dicom.md`
2. Segmentation — `script_repository/segmentations.md`
3. Export to STL/OBJ/NRRD — search `script_repository/segmentations.md` for "export"
   and `script_repository/models.md` for surface mesh saving

**Create a new scripted module from scratch:**
1. Scaffolding — `slicer-source/Modules/Scripted/ExtensionWizard/`
2. Module pattern — `slicer-source/Modules/Scripted/SampleData/` as a template
3. Parameter node wrapper — `slicer-source/Base/Python/slicer/parameterNodeWrapper/`
4. Testing — `slicer-source/Modules/Scripted/SegmentStatistics/Testing/`

**Add a custom Segment Editor effect:**
1. Base class API — `AbstractScriptedSegmentEditorEffect.py` in
   `Modules/Loadable/Segmentations/EditorEffects/Python/SegmentEditorEffects/`
2. Example effects — other `SegmentEditor*Effect.py` files in the same directory
3. Registration — search `slicer-source` for `registerEditorEffect`

**Build Slicer or an extension from source:**
1. Build instructions — `slicer-source/Docs/developer_guide/build_instructions/`
2. SuperBuild dependencies — `slicer-source/SuperBuild/External_*.cmake`
3. Extension build — `slicer-source/Docs/developer_guide/extensions.md`

**Work with transforms and coordinate systems:**
1. Transform examples — `script_repository/transforms.md`
2. RAS/LPS conventions — search `slicer-source/Docs/` for "coordinate" or "RAS"
3. Transform node API — `slicer-source/Libs/MRML/Core/vtkMRMLTransformNode.h`

---

## Extending the Skill

Additional data sources can be added by editing `setup.sh` and updating this document.
For example, if a new GitHub repository is released with tutorials, the script can be
extended to clone that repository and document its purpose here.

Agents that understand the SKILLS.md format should parse this file and use its sections to
bootstrap their reasoning about how to prepare and query the environment.

### Design Principles

This skill was designed with specific trade-offs in mind.  Future contributors (human
or agent) should follow these principles when extending it:

1. **Prefer pointers to copies.**  This file directs the agent to read specific files
   in the checked-out repositories rather than embedding code snippets or API
   documentation inline.  The Slicer source tree, script repository, and developer
   guide are the single source of truth — duplicating their content here would create
   version skew as Slicer evolves.  Pointers cost one extra file-read per query but
   are always accurate.

2. **Be specific with pointers.**  Vague references ("look in the source") force
   expensive open-ended searches.  Every pointer should name a concrete file or
   directory path and, where helpful, suggest a grep pattern or section heading.
   The agent should be able to resolve any pointer with a single Read or Grep
   operation.

3. **Inline only what cannot be discovered.**  The "Common Pitfalls" section is the
   intentional exception to the pointers-over-copies rule.  Pitfalls like RAS/LPS
   sign flips and KJI axis ordering live in the gap between the code and how people
   misuse it — they are not documented in any single source file and are not
   discoverable by code search.  New pitfalls should be added here only when they
   meet this bar.

4. **Keep the file under 600 lines.**  The Agent Skills convention recommends concise
   skill files to avoid overwhelming the agent's context window.  If this file
   approaches the limit, move detailed content to supporting files in a `references/`
   directory and link to them from here.

5. **Stay agent-agnostic.**  This skill targets any agent that understands the
   SKILLS.md convention (Claude Code, OpenAI agents, Codex, Cursor, etc.).  Avoid
   features specific to a single agent runtime.  The frontmatter uses only fields
   from the open Agent Skills standard.

6. **Leverage all data sources available in the active mode.**  The unique strength of
   this skill is the combination of source code, extensions, dependencies, community
   discussions, and coding conversations.  In lighter modes, some of these are accessed
   via web APIs rather than local clones, but the agent should still cross-reference
   multiple sources — for example, a discourse search may explain *why* something
   works a certain way when the source code only shows *how*.

7. **Adapt to the active mode gracefully.**  Check `.setup-stamp.json` at the start
   of a session.  Use local tools (grep, find, git log) when data is available locally,
   and fall back to web APIs (GitHub API, Discourse API) when it is not.  Never fail
   simply because a directory is missing — check the mode and use the appropriate
   strategy.

---

*Created and maintained by the Slicer community.*
