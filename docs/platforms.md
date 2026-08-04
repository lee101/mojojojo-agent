# Windows and Linux tool support

MJJ runs the same bounded tool registry on native Windows and Linux. The
platform boundary is confined to process invocation, executable discovery,
timeouts, and terminal rendering; file, patch, search, checkpoint, skill,
plan, and model-tool schemas remain identical.

| Tool area | Windows | Linux |
| --- | --- | --- |
| `read`, `list`, `search` | Native paths; Git/ripgrep when installed, Python fallback otherwise | Same |
| `apply_patch`, checkpoint/undo | Atomic same-volume replacement; Windows mode bits are best effort | Atomic replacement with POSIX modes |
| `shell` | Direct strings use Windows command-line quoting; `shell=true` uses the system command processor | Direct strings use POSIX argv quoting; `shell=true` uses `/bin/sh` |
| `check` | Python/JSON/TOML and tree-sitter; PowerShell parser through installed `pwsh`/Windows PowerShell; project `.exe`/`.cmd` formatters | Python/JSON/TOML and tree-sitter; installed Unix compiler/formatter tools |
| `py` | In-process timeout uses tracing because `SIGALRM` is unavailable; remote isolation remains available | `SIGALRM` timeout on the main thread; optional local `mojojail` and remote isolation |
| `navigate` | Installed `.exe`/`.cmd` language servers; indexed fallback | Installed language servers; indexed fallback |
| `display_image` | ANSI/ConPTY with `vision`, or dependency-free Kitty when available | ANSI with `vision`, or dependency-free Kitty |
| `delegate` | Git for Windows worktrees | Git worktrees |
| MCP | Direct configured argv, including `.exe`/`.cmd` servers | Direct configured argv |

Prefer a JSON argv array for generated shell calls because it is portable and
does not need quoting:

```json
{"command":["python","-m","pytest","tests","-q"],"timeout":300}
```

String commands with `shell=false` are also supported and use the host's rules,
so a quoted Windows path such as `"C:\\Program Files\\Python\\python.exe"`
remains one argument. Shell operators are rejected unless `shell=true` is
explicit. Executable policy matching normalizes `.exe`, `.cmd`, `.bat`, and
`.com`, so read-only `git.exe` and `rg.exe` calls retain the same permission
behavior as their Linux names.

Mojo acceleration is optional on both systems. MJJ recognizes Pixi's Unix
`bin/mojo` and Windows `Scripts/mojo.exe` or `Library/bin/mojo.exe` layouts and
derives the matching Max package root. If Mojo, ripgrep, a language server, or
a local sandbox is absent, the documented Python/index/remote fallback remains
available instead of suppressing the tool.

The Python fallback matrix remains the portable correctness gate. A separate
Linux Mojo workflow pins the compiler with Pixi, builds
`mjj/search/embed.mojo` as a shared library, and calls its exported top-k ABI
with known vectors. Windows continues to exercise the guarded fallback because
the current Mojo distribution is not a reliable native Windows CI target.

Every push runs the complete test suite and package build on Ubuntu and native
`windows-latest`. Release CI separately builds and smoke-tests the standalone
Windows executable and Linux binaries.
