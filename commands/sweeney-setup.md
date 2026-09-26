---
description: Set up Sweeney — fetch the UT2004 script source and locate your install
---

Run Sweeney's setup script and report the result:

```bash
bash "${CLAUDE_PLUGIN_ROOT}/scripts/setup.sh"
```

It clones the UT2004 script source to `~/.sweeney/engine/ut2004` (or pulls, if a
previous run already did), looks for a UT2004 install by walking up from the working
directory, and writes `~/.sweeney/config.json`. It is safe to re-run and never writes
into a UT2004 install.

After it finishes:

- If it could not find a UT2004 install, say so and tell the user they can either run
  `/sweeney-setup` from inside their install, or set `install_root` in
  `~/.sweeney/config.json` by hand. Sweeney still answers engine questions without it.
- If `ucc_bits` came back as 32, warn that a 32-bit `UCC.exe` hangs when building
  large packages.
- If the clone failed, the likely cause is no network access to github.com. Report the
  error rather than trying to work around it.

Then summarise in two or three lines what was found: engine source path and `.uc`
count, install root, and whether `ut3converter` is available, and where the bundled `uttexture` toolchain is.
