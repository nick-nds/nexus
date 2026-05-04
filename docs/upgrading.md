# Upgrading

## v1.0 is the first public release

There is no prior stable version to upgrade from. If you have been running a pre-release build from source, follow the steps below.

---

## Migrating from a pre-release build to v1.0

Pre-release builds did not have a stable schema. The index format changed across development milestones.

**Before upgrading:**

1. Delete your existing index data to avoid schema mismatches:

```bash
nexus index clear --force
```

Or manually:

```bash
rm -rf ~/.nexus/projects/<your-project-slug>/
```

2. Install v1.0:

```bash
pip install --upgrade nexus
```

3. Update the Composer extractor:

```bash
# in your Laravel project
composer require --dev nexus/extractor-php:^1.0
```

4. Rebuild the index from scratch:

```bash
nexus index rebuild
```

---

## Version policy after v1.0

Nexus uses [SemVer](https://semver.org/) from v1.0 onward.

| Change type | Version bump |
|---|---|
| New query tool added | **minor** (`1.x.0`) |
| New MCP tool name or input field | **major** (`2.0.0`) |
| Removed or renamed MCP tool | **major** (`2.0.0`) |
| Changed MCP tool input schema (breaking) | **major** (`2.0.0`) |
| Bug fix with no API change | **patch** (`1.0.x`) |
| New CLI option added (additive) | **minor** (`1.x.0`) |
| Renamed CLI command or option | **major** (`2.0.0`) |

### What is frozen at v1.0

- All CLI command names and option names
- All MCP tool names and their input schemas
- The `nexus.yml` schema keys
- The `reflection.json` schema (major version field)

Any breaking change to these contracts requires a major version bump and a migration guide in this document.

---

## Migrating from v1.x to v2.0 (when applicable)

When a v2.0 is released, migration instructions will be added here. Until then, all v1.x releases are backwards-compatible with the v1.0 CLI and MCP contracts.
