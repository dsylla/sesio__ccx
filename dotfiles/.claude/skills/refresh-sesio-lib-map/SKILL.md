---
name: refresh-sesio-lib-map
description: Refresh the sesio-v3 shared library reference map in the sesio__greengrass CLAUDE.md. Use when user says "refresh lib map", "refresh the map", "update library map", "refresh sesio-v3", or after significant library changes.
tools: Bash, Read, Edit, Write, Glob, Grep, Task
---

# Refresh sesio-v3 Library Reference Map

Regenerate the `## sesio-v3 Shared Library Reference Map` section in the sesio__greengrass project CLAUDE.md by scanning every Python module in the shared library.

## Paths

- **Project root**: `/home/david/Work/sesio/sesio__greengrass`
- **Library root**: `/home/david/Work/sesio/sesio__greengrass/__LIB/sesio-v3/`
- **Target file**: `/home/david/Work/sesio/sesio__greengrass/CLAUDE.md`
- **Section marker**: `## sesio-v3 Shared Library Reference Map` (replace from this line to EOF)

## Instructions

### Phase 1: Scan the library

Use parallel Task agents (subagent_type: general-purpose) to scan the library. Split by package directory -- launch one agent per 3-4 packages for speed.

**Scan targets** -- every `.py` file under `__LIB/sesio-v3/`, EXCLUDING:
- `tests/` directory
- `stubs/` directory
- `__pycache__/` directories
- `py.typed` marker files

**For each Python file, extract:**
- Classes: name, base class(es), one-line purpose
  - Key public methods with signatures (params + return type)
  - Important fields/attributes
- Standalone functions: name, signature, one-line purpose
- Constants/enums: name, value (or member count for large enums)
- Protocols: name, required methods

**Skip**: private helper functions, imports, type aliases that are obvious

### Phase 2: Organize by package

Group results by directory, in this order:
1. Top-level modules (files directly in `sesio-v3/`)
2. `base/`
3. `logger/`
4. `thread/`
5. `greengrass/` (component base, mqtt, protocols, enums)
6. `component/` (kind, manager, state, delta)
7. `shadow/`
8. `state/`
9. `node/`
10. `mixin/`
11. `record/` and `recorder/`
12. `payload/` (grouped by domain subdirectory)
13. `file/`
14. `tools/`
15. `usb/`
16. `device/` subdirectories (gpio, led, door, contact, laser, button, audio, energy, accelerometer, vibration, crossing_counter, cabin_call)
17. `movement/`
18. `bacnet/`
19. `vpn/`
20. `zwave/`
21. Any other packages not listed above

### Phase 3: Format the map

Use this exact format:

```markdown
## sesio-v3 Shared Library Reference Map

All paths are relative to `__LIB/sesio-v3/`. Import prefix: `from sesio.<path>`.

---

### Top-level modules

#### `module.py`
- `ClassName(BaseClass)` -- one-line description
  - `method_name(param: Type) -> ReturnType`
  - Fields: `field_name` (type)
- `function_name(params) -> ReturnType` -- description
- Constants: `CONST_NAME = value`

---

### package_name/

#### `package/module.py`
- ...
```

Rules:
- Use `####` for file headings, `###` for package headings
- Use `---` horizontal rules between packages
- Use `--` (not `:`) to separate name from description
- Indent methods under their class with 2 spaces
- For large enums (10+ members), list groups/prefixes not every value
- For Pydantic models, list fields as key attributes
- Keep method signatures concise -- omit `self`, use short param names

### Phase 4: Replace the section in CLAUDE.md

1. Read the current CLAUDE.md
2. Find the line `## sesio-v3 Shared Library Reference Map`
3. Replace everything from that line to EOF with the new map
4. Write the updated file using Edit (preferred) or Write

### Phase 5: Verify

1. Check the updated CLAUDE.md reads correctly (read last 30 lines to confirm ending)
2. Report: total modules scanned, total lines in map section, any new packages found since last refresh

## Output

After completion, report to the user:
- Number of packages scanned
- Number of Python modules cataloged
- New/removed modules since previous map (if detectable)
- Final line count of the map section
