# slicer-skill

This repository provides the groundwork for an **AI coding skill** that assists with
programming questions related to [3D Slicer](https://www.slicer.org).  The primary piece of
content is `SKILL.md` which describes how an agent can prepare its environment and what
resources are available for search.

## Getting started

1. Read `SKILL.md` for detailed information about the expected workflow and data
   sources.
2. Run the helper script to fetch the required repositories:
   ```sh
   chmod +x setup.sh
   ./setup.sh
   ```
3. Use the resulting local copies when answering questions by searching for
   code, files, and community discussions across the cloned repositories.

## Using the skill from other projects

The slicer-skill directory is intended to be a **shared, standalone resource** that
multiple projects can reference.  You should not clone Slicer repositories into your
own project directory.

To point your AI agent at the skill, add a section like the following to your
project's configuration file (e.g. `CLAUDE.md`, `AGENTS.md`, or equivalent).
Replace `/path/to/slicer-skill` with the absolute path where you cloned this
repository:

````markdown
## Slicer Programming Reference

For help answering 3D Slicer programming questions, use the slicer skill located at:

    /path/to/slicer-skill

That directory contains `SKILL.md` with instructions for searching Slicer source
code, extensions, discourse archives, dependency repositories, and NA-MIC Project Week materials.

**Important:** All slicer-skill data lives in that single shared directory.
Do NOT clone repositories into this project directory.

- If the repos are not yet set up, run `setup.sh` **from within the slicer-skill
  directory**:
  ```sh
  cd /path/to/slicer-skill && ./setup.sh
  ```
- All searches should target paths under `/path/to/slicer-skill/`:
  - `/path/to/slicer-skill/slicer-source/`
  - `/path/to/slicer-skill/slicer-extensions/`
  - `/path/to/slicer-skill/slicer-discourse/`
  - `/path/to/slicer-skill/slicer-dependencies/`
  - `/path/to/slicer-skill/slicer-projectweek/`
````

### Claude Code

When using [Claude Code](https://docs.anthropic.com/en/docs/claude-code), you can
also add the slicer-skill directory to your session with the `--add-dir` flag:

```sh
claude --add-dir /path/to/slicer-skill
```

This makes the skill and all its data available for searching without copying
anything into your project.

## MCP Server

This repository includes `slicer-mcp-server.py`, a self-contained
[Model Context Protocol](https://modelcontextprotocol.io/) (MCP) server that
runs inside 3D Slicer.  It exposes tools such as `list_nodes`,
`execute_python`, `screenshot`, and `load_sample_data` over HTTP so that MCP
clients (Claude Code, Claude Desktop, Cline, etc.) can interact with a live
Slicer session.

### Quick start

1. Open 3D Slicer and paste the contents of `slicer-mcp-server.py` into the
   Python console (or run it via `execfile`).
2. Add the server to your MCP client configuration (`.mcp.json`,
   `claude_desktop_config.json`, etc.):
   ```json
   {
     "mcpServers": {
       "slicer": {
         "type": "http",
         "url": "http://localhost:2026/mcp"
       }
     }
   }
   ```
3. Your MCP client can now query the scene, execute code, and take screenshots
   in Slicer.

To stop the server from within Slicer: `mcpLogic.stop()`

### Related projects

* **[mcp-slicer](https://github.com/zhaoyouj/mcp-slicer)** — a standalone
  MCP server for 3D Slicer by @zhaoyouj, installable via `pip` / `uvx`.  It
  uses Slicer's built-in WebServer API as a bridge and can be launched outside
  of Slicer.
* **[SlicerDeveloperAgent](https://github.com/muratmaga/SlicerDeveloperAgent)**
  — a Slicer extension by Murat Maga that embeds an AI coding agent directly
  inside 3D Slicer using Gemini, letting users prompt, run, and iterate on
  scripts and modules without leaving the application.  See the
  [Discourse discussion](https://discourse.slicer.org/t/developer-agent-for-slicer/44787)
  for background.
* **[NA-MIC Project Week 44 — Claude Scientific Skill for Imaging Data Commons](https://projectweek.na-mic.org/PW44_2026_GranCanaria/Projects/ClaudeScientificSkillForImagingDataCommons/)**
  — a project that developed a Claude skill for the
  [Imaging Data Commons](https://portal.imaging.datacommons.cancer.gov/)
  (IDC), published at
  [ImagingDataCommons/idc-claude-skill](https://github.com/ImagingDataCommons/idc-claude-skill).
* **[SlicerChat: Building a Local Chatbot for 3D Slicer](https://arxiv.org/abs/2407.11987)**
  (Barr, 2024) — explores integrating a locally-run LLM (Code-Llama Instruct) into 3D Slicer
  to assist users with the software's steep learning curve, investigating the effects of
  fine-tuning, model size, and domain knowledge (Python samples, Markdown docs, Discourse
  forums) on answer quality.

### Security warning

The MCP server grants any connected client the ability to **execute arbitrary
Python code** inside your Slicer process.  This is powerful but carries
significant risk:

* **Code execution** — A compromised or malicious MCP client can run any code
  with the full privileges of the Slicer process, including reading and writing
  files, accessing the network, and installing software.
* **Protected Health Information (PHI)** — If you are working with patient
  data or other confidential information, be aware that an MCP client (and the
  remote AI model behind it) may send and receive data that includes PHI.
  Ensure you comply with your institution's data-handling policies, HIPAA, and
  any other applicable regulations.
* **Third-party models** — Prompts and tool responses may be transmitted to
  cloud-hosted AI services.  Do not assume that data shared through the MCP
  connection stays local.

**We strongly recommend running the MCP server inside a contained
environment** rather than on your everyday workstation:

* **Docker** — Use a containerised Slicer such as
  [SlicerDockers](https://github.com/pieper/SlicerDockers) or the official
  [Slicer/SlicerDocker](https://github.com/Slicer/SlicerDocker).
* **Virtual machines** — Provision a VM through
  [Jetstream2](https://jetstream-cloud.org/),
  [vast.ai](https://vast.ai/),
  AWS, GCP, Azure, or any other cloud provider.  This isolates the Slicer
  process (and any data it loads) from your local machine.

By keeping Slicer and the MCP server in an isolated environment you limit the
blast radius of any unintended or malicious actions and reduce the chance of
accidentally exposing sensitive data.

## License

This project is licensed under the [Apache License 2.0](LICENSE).
