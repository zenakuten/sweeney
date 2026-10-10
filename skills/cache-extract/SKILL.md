---
name: cache-extract
description: Recover a downloaded UT2004 package from cache by package name, including all transitive package dependencies, into the proper install folders. Use when a map or mod was downloaded from a server, when asked to extract cached packages, or when an installed cached map is missing dependencies.
---

# Cache extract

Accept one **package name** as the parameter, with or without its extension:
`ONS-Dreamus2SE-T32-C-V1` or `ONS-Dreamus2SE-T32-C-V1.ut2`. Match the complete
name case-insensitively, not a substring. If no name was supplied, ask for it.

## Run

Read `~/.sweeney/config.json`. The tool is
`<plugin_root>/scripts/cache_extract.py`; `plugin_root` is written by Sweeney setup.
Do not assume a plugin checkout or a particular front end. Use an available Python
3.8+ interpreter (`python3`, `python`, or `py`).

The default source is `<client_install_root>/Cache` and the destination is
`install_root`. If no separate client is configured, the source defaults to the
destination's `Cache`. `UT2004_INSTALL` overrides the destination. Honour explicit
source/destination instructions using `--cache` and `--install`; if the configured
installs do not unambiguously match the request, ask before copying.

```text
python <plugin_root>/scripts/cache_extract.py <package-name> --dry-run
python <plugin_root>/scripts/cache_extract.py <package-name>
```

Run the dry run first and inspect the source, destination and planned copies. Then
run the same command without `--dry-run`. Both modes resolve the entire dependency
closure using Sweeney's `uttexture.ue2.Package` import-table reader, including
dependencies of packages already installed.

## What gets copied

`Cache/cache.ini` maps identifiers to original filenames. The corresponding
`<identifier>.uxx` is copied under that original filename:

| Extension | Destination folder |
|---|---|
| `.ut2` | `Maps` |
| `.u` | `System` |
| `.utx` | `Textures` |
| `.usx` | `StaticMeshes` |
| `.ukx` | `Animations` |
| `.uax` | `Sounds` |
| `.umx`, `.ogg` | `Music` |

The cache and its index are left intact. Existing destination files are never
overwritten. The requested package must be found in cache; if already installed,
its bytes must match. Dependencies use installed packages first and cache entries
only when absent. All copied files are verified with SHA-256.
When content types share a basename (for example `.utx` and `.usx`), dependency
resolution conservatively scans both; they are not conflicting versions.

Cycles are visited once. Missing packages, multiple cached versions, conflicting
installed files, malformed package data and unsafe filenames are errors, not skipped
dependencies. Preflight errors copy nothing; an I/O failure during copying can leave
earlier verified copies, so report the failure rather than claiming completion.
For ambiguous versions, ask the user which one to use; do not guess or remove files.

Import tables identify dependencies by package name, not the server's required GUID.
Finding an installed dependency is not proof that its version matches the server.
Likewise, resolving packages does not prove the map loads in UnrealEd: an embedded
`MyLevel` bytecode crash such as `Bad expr token` can persist independently.

Report the copied filenames and any unresolved error. If retrying a map in UnrealEd,
start a fresh editor process; do not modify or round-trip the map to extract cache.
