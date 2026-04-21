---
name: greengrass-v3-docs
description: Generate or refresh Sphinx documentation for sesio Greengrass v3 components and shared library. Scans v3-migrated components, analyzes the sesio-v3 shared library, and produces RST documentation covering architecture, library modules, each component, and development workflow. Use when user says "update greengrass docs", "refresh v3 docs", "document components", "generate component docs", or needs to regenerate the sesio Greengrass documentation.
tools: Bash, Read, Edit, Write, Glob, Grep, Task
args: refresh | full | component <name> | library | workflow
---

# Greengrass v3 Documentation Generator

Generate comprehensive Sphinx RST documentation for the sesio Greengrass v3 library and migrated components.

## Arguments

- `full` - Full documentation generation (default): analyze all v3 components + library, generate all RST files
- `refresh` - Refresh existing docs: detect code changes since last doc update, update only changed sections
- `component <name>` - Regenerate docs for a single component (e.g., `component status`, `component led`)
- `library` - Regenerate only the library module documentation
- `workflow` - Regenerate only the workflow/tooling documentation

## Project Context

- **Repository**: `/home/david/Work/sesio/sesio__greengrass`
- **Docs directory**: `docs/`
- **Shared v3 library**: `__LIB/sesio-v3/`
- **Component directories**: `sesio.greengrass.{Name}/`
- **Sphinx config**: `docs/conf.py`

## V3 Components (migrated)

These are the v3-migrated components to document:

| Component | Directory | Kind | GPIO |
|-----------|-----------|------|------|
| Status | sesio.greengrass.Status | status | No |
| Audio | sesio.greengrass.Audio | audio | No |
| BehavioralAnalysis | sesio.greengrass.BehavioralAnalysis | behavioral-analysis | No |
| DependencyManager | sesio.greengrass.DependencyManager | dependency-manager | No |
| BACnet | sesio.greengrass.BACnet | bacnet | No |
| VPN | sesio.greengrass.VPN | vpn | No |
| Led | sesio.greengrass.Led | led | Yes (3 out) |
| InspectionMode | sesio.greengrass.InspectionMode | inspection-mode | Yes (1 in) |
| CabinCallGPIO | sesio.greengrass.CabinCallGPIO | cabin-call-gpio | Yes (out) |
| ContactGPIO | sesio.greengrass.ContactGPIO | contact-gpio | Yes (in/out) |
| DoorGPIO | sesio.greengrass.DoorGPIO | door-gpio | Yes (2 in) |

## V2 Components (legacy, DO NOT document)

CabinCall, Movement, CrossingCounterGPIO, LocalCabinCallGPIO, Energy, DeviceVibration

## Instructions

### Phase 1: Analyze Codebase

**For each v3 component**, read these files:
- `src/component.py` - Main logic, dataclass definition, task handlers, events, MQTT
- `src/main.py` - Entry point (if exists, GPIO components)
- `src/enums.py` - Task kinds, constants (if exists)
- `src/mqtt_constants.py` - Topic definitions (if exists)
- `recipe.yaml` - Greengrass recipe (dependencies, config)
- `gdk-config.json` - Version, author, build config
- `requirements.txt` - Python dependencies

**Extract for each component:**
1. Purpose (1-2 sentences)
2. Dataclass signature (class name, generic params, mixins)
3. Task kinds enum (if any)
4. Events subscribed and emitted (from `event_bus.subscribe()` calls)
5. MQTT topics (IoT Core + local, from mqtt_constants.py and subscribe calls)
6. Shadow interactions (which named shadows, read/write)
7. Dependencies (Greengrass recipe + Python)
8. State metadata callback return value
9. Configuration options (from recipe DefaultConfiguration)
10. GPIO pin usage (for GPIO components only)
11. Background threads (names, purposes)

**For the shared library** (`__LIB/sesio-v3/`), analyze:
- `greengrass/component.py` - SesioGreengrassComponent base class
- `component/manager.py` - SesioComponentManager
- `node/manager.py` - SesioNodeManager
- `event_bus.py` - SesioEventBus
- `event.py` - SesioEvent enum (all events)
- `shadow/manager.py` - SesioShadowManager
- `shadow/enums.py` - SesioNamedShadow, SesioStateTopic
- `ipc.py` - SesioIPCManager
- `greengrass/mqtt/manager.py` - SesioMQTTManager
- `state/store.py` - SesioStateStore
- `state/topics.py` - SesioStateTopic
- `initializer.py` - init_component()
- `device/` - GPIO, Door, Laser, LED, CabinCall, Contact, Button drivers
- `payload/` - Payload models and actions
- `recorder/` - Recording system base
- `mixin/state/stateful.py` - State machine mixin
- `base/__class__.py` - SesioBaseClass
- `logger/__class__.py` - Logging setup

### Phase 2: Generate Documentation

Use Explore agents (Task tool with subagent_type=Explore) to parallelize component analysis. Launch one agent per 2-3 components.

#### Documentation Structure

```
docs/
├── index.rst                      # Main entry, toctree
├── architecture.rst               # Architecture overview + mermaid diagram
├── workflow.rst                   # Dev workflow (build, deploy, publish, logs)
├── library/
│   ├── index.rst                  # Library overview
│   ├── component-base.rst         # SesioGreengrassComponent
│   ├── event-bus.rst              # SesioEventBus + SesioEvent
│   ├── shadow-manager.rst         # SesioShadowManager + named shadows
│   ├── ipc-mqtt.rst               # IPC + MQTT managers
│   ├── state-store.rst            # SesioStateStore (SQLite)
│   ├── device-drivers.rst         # GPIO, Door, Laser, LED, etc.
│   └── payloads.rst               # Payload system + actions
└── components/
    ├── index.rst                  # Component overview + summary table
    ├── status.rst
    ├── audio.rst
    ├── behavioral-analysis.rst
    ├── dependency-manager.rst
    ├── bacnet.rst
    ├── vpn.rst
    ├── led.rst
    ├── inspection-mode.rst
    ├── cabin-call-gpio.rst
    ├── contact-gpio.rst
    └── door-gpio.rst
```

#### Component Page Template (RST)

Each `docs/components/{name}.rst` should follow this structure:

```rst
{Component Name}
{'=' * len(title)}

.. _component-{kind}:

Purpose
-------

{1-2 sentence description}

Dataclass Definition
--------------------

.. code-block:: python

   @dataclass(slots=True, kw_only=True)
   class {ClassName}(
       {mixins},
       SesioGreengrassComponent[{TTaskKind}, {TTaskPayload}],
   ):
       {fields}

Task Kinds
----------

{Table or "No custom task kinds" if None}

Events
------

**Subscribed:**

{Table: Event → Handler → Description}

**Emitted:**

{Table: Event → When → Payload}

MQTT Topics
-----------

**IoT Core:**

{Table: Topic → Direction → Purpose}

**Local (inter-component):**

{Table: Topic → Direction → Purpose}

Shadow Interactions
-------------------

{Which named shadows, read/write, structure}

Dependencies
------------

**Greengrass:**

{Table: Component → Version → Type}

**Python:**

{Key packages only, not all deps}

State Metadata
--------------

.. code-block:: python

   def _get_state_metadata(self) -> dict:
       {return value}

Configuration
-------------

{Recipe config, timing constants, options}

{For GPIO components only:}

GPIO Pin Usage
--------------

{Pin types, configuration, hardware requirements}
```

#### Library Page Guidelines

- Use `.. code-block:: python` for class signatures and key methods
- Document the public API, not internal implementation
- Cross-reference between pages with `:ref:` labels
- Include the component lifecycle flow in component-base.rst
- List all 110+ SesioEvent values grouped by category in event-bus.rst
- Show the 8 named shadows with their purposes in shadow-manager.rst

#### Architecture Page Guidelines

- Include a mermaid diagram (use `.. mermaid::` directive if sphinxcontrib-mermaid is available, otherwise use `.. code-block:: text` with ASCII art)
- Show component interaction flows
- Document the MQTT topic pattern
- Document the local state topic pattern
- Show the initialization sequence

#### Workflow Page Guidelines

Document these workflows:
1. **Build**: `./build.sh` (copies lib → GDK build → restores symlink)
2. **Dev Build**: `./build-dev.sh` (venv paths)
3. **Local Deploy**: `gg-deploy dev <kind> --sudo`
4. **Publish**: `gg-publish --component <kind> --bump patch`
5. **AWS Deploy**: `aws greengrassv2 create-deployment`
6. **Logs**: `gg-logs --component <kind> --node <thing-name>`
7. **Provisioning**: `__PROVISIONNER/` configs
8. **Monitoring**: `ba-monitor` TUI tool

### Phase 3: Write Files

1. Create directories: `docs/library/`, `docs/components/`
2. Write all RST files using the Write tool
3. Update `docs/index.rst` to include all new pages in toctree
4. Verify `docs/conf.py` has required extensions (myst_parser, sphinx_rtd_theme, sphinxcontrib-mermaid if available)

### Phase 4: Build & Verify

```bash
cd /home/david/Work/sesio/sesio__greengrass
/usr/bin/uv run sphinx-build -b html docs docs/_build/html 2>&1 | tail -20
```

Fix any RST warnings or errors. Common issues:
- Duplicate labels
- Missing toctree entries
- Bad indentation in code blocks
- Undefined references

### Refresh Mode

When `refresh` argument is given:

1. Read existing RST files in `docs/`
2. Check git log for code changes since last doc commit:
   ```bash
   git log --oneline --name-only -- '__LIB/sesio-v3/' 'sesio.greengrass.*/src/' | head -50
   ```
3. Identify which components/modules changed
4. Re-analyze only changed files
5. Update corresponding RST pages using Edit (not Write) to preserve manual sections
6. Look for `.. MANUAL::` markers - preserve content between them

## RST Style Guide

- Use `=` underline for page titles, `-` for sections, `~` for subsections
- Use `.. code-block:: python` (not triple backticks)
- Use `.. note::`, `.. warning::`, `.. important::` for callouts
- Use `:ref:` for cross-references (define labels with `.. _label-name:`)
- Keep line length reasonable (no hard wrap needed for RST)
- Use tables with grid format for complex data, simple format for basic lists
- Always include blank lines before/after directives
