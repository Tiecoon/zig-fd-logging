# zig-fd-logging

A small, optional transport for **Zig 0.17.1 `std.log`** and **Python 3.10+
`logging`**, sharing one Zig collector. No third-party runtime dependencies.

**Without a collector, logging uses the standard stderr behavior.** With the
collector, log records travel over one extra inherited pipe and are separated
into files automatically. stdin, stdout, and stderr are inherited unchanged;
explicit writes to stderr, panics, and stack traces are never rerouted.

## Import into a Zig project

Fetch the current `main` branch from your application's directory:

```sh
zig fetch --save=fd_log 'git+https://github.com/Tiecoon/zig-fd-logging.git#main'
```

Zig saves a resolved commit and content hash in `build.zig.zon`; builds do not
silently follow new commits. Repeat the fetch command to update, and commit the
resulting dependency pin. For local development, use
`.fd_log = .{ .path = "../zig-fd-logging" }` in `.dependencies` instead.

After creating your executable in `build.zig`:

```zig
const fd_log = b.dependency("fd_log", .{ .target = target, .optimize = optimize });
exe.root_module.addImport("fd_log", fd_log.module("fd_log"));
```

In the executable's root source:

```zig
const std = @import("std");
const fd_log = @import("fd_log");

pub const std_options: std.Options = .{
    .logFn = fd_log.logFn,
    .log_level = .debug, // Optional; std.log's normal build-mode default otherwise.
};

pub fn main(init: std.process.Init) !void {
    fd_log.configureFromEnvironment(init.environ_map);
    std.log.info("listening on port {d}", .{8080});
    std.log.scoped(.network).warn("retrying", .{});
}
```

Initialize once, before starting threads. Libraries keep calling `std.log` as
usual. Levels, scopes, filtering, and argument formatting stay upstream; the
hook calls `std.log.defaultLogFileTerminal` rather than copying its formatter.
Importing the module does not require building or running the collector.

Without the collector's `ZIG_LOG_FD` marker, the hook calls
`std.log.defaultLog` directly, preserving its stderr/color behavior without
allocations, a scope registry, or an extra lock. Missing, stale, and invalid
markers also fall back to standard logging. Logging before initialization is
standard logging too.

## Python client (3.10+)

Install into your project's Python environment; no Python collector or runtime
packages are needed:

```sh
python3 -m pip install ./python
```

Use a normal logging handler, with your usual format, levels, and filters:

```python
import logging
from zig_fd_logging import Handler

logging.basicConfig(handlers=[Handler()], level=logging.INFO)
logging.info("listening on port %d", 8080)
logging.getLogger("network").warning("retrying")
```

Without the collector, `Handler` delegates to `logging.StreamHandler`, including
its standard stderr, formatting, filtering, and exception behavior. An optional
`stream=` argument selects its normal fallback stream, just like StreamHandler.
Installing the handler follows normal logging configuration rules; use
`force=True` with `basicConfig` only if you intend to replace existing handlers.

With the collector, Python root logs use the severity files and named loggers
use the same `scope-NAME.log` convention as Zig. Logger names need no registration
and can be created at runtime. CRITICAL and ERROR route to `err`, WARNING to
`warn`, INFO to `info`, and lower custom levels to `debug`. The configured Python
formatter and terminator are preserved; Unicode is transported as UTF-8.

After installation, use the same collector or pane helpers:

```sh
./zig-out/bin/fd-log-run ./python-logs -- python3 examples/python_logging.py
fd-log-panes tmux ./python-logs -- python3 examples/python_logging.py
fd-log-panes rex ./python-logs -- python3 examples/python_logging.py
```

For a source checkout without installation, add the repository's `python/`
directory to `PYTHONPATH`. Nix users can add the `packages.<system>.python`
package to `pkgs.python3.withPackages`; it is deliberately not installed globally
by the shell-helper module. Python 3.10 is runtime-tested, not only declared in
package metadata.

## Build and run

```sh
nix develop
zig build

# No collector: standard logging to stderr.
./zig-out/bin/fd-log-example

# Optional collector: discover streams and append to their files.
./zig-out/bin/fd-log-run './logs with spaces' -- ./zig-out/bin/fd-log-example
cat './logs with spaces/scope-network.log'

# Include additional scopes and a multiline message.
./zig-out/bin/fd-log-run ./logs -- ./zig-out/bin/fd-log-example --scopes

zig build test
```

Nix is optional; `flake.lock` pins nixpkgs and Zig for reproducible builds.
Without Nix, install Zig 0.17.1 and run `zig build`. Python and the four shells
are needed for integration tests, not for the logging library or collector.

The collector works from Bash, Zsh, Fish, and Nushell. Use the built application
binary, not an intermediate build runner that may close inherited descriptors.
The application must install this logging hook; arbitrary programs still run
normally but their stderr cannot be separated into scopes.

### Automatic outputs

No scope list or discovery command is required. The first emitted record for a
stream creates its file:

| Logging call | File |
| --- | --- |
| `std.log.err(...)` | `err.log` |
| `std.log.warn(...)` | `warn.log` |
| `std.log.info(...)` | `info.log` |
| `std.log.debug(...)` | `debug.log` |
| `std.log.scoped(.network).info(...)` | `scope-network.log` |
| `std.log.scoped(.network).err(...)` | the same `scope-network.log` |
| `std.log.scoped(.database).debug(...)` | `scope-database.log` |

Named scopes combine all their emitted severities. Default-scope logs remain
separated by severity. Only emitted streams appear; standard compile-time
filtering still applies. Names are compile-time Zig scope names, not a new
logging API. There is no fixed stream count, apart from OS resource limits.

The `scope-` prefix avoids collisions with severity names. Each scope-name byte
outside ASCII letters, digits, `-`, and `_` becomes `%HH`, so quotes, slashes,
Unicode, and other characters cannot turn a name into a filesystem path.
Filesystem filename-length limits still apply. Log files contain formatted
messages, including multiline messages—not transport metadata. The clients do
not add terminal colors; explicitly supplied escapes or custom formatter output
are preserved. Files append across runs; choose another directory for a fresh run.

## Shell configuration

The Nix module below installs and sources these functions automatically.
Without Nix, add both the built binaries and scripts to PATH, then source the
matching configuration from the project root:

**Bash / Zsh** (`~/.bashrc` or `~/.zshrc`):

```sh
export PATH="$PWD/zig-out/bin:$PWD/scripts:$PATH"
source examples/shell/config.sh
log-files ./zig-out/bin/fd-log-example
log-rex ./zig-out/bin/fd-log-example
log-tmux ./zig-out/bin/fd-log-example
```

**Fish** (`~/.config/fish/config.fish`):

```fish
set -gx PATH "$PWD/zig-out/bin" "$PWD/scripts" $PATH
source examples/shell/config.fish
log-files ./zig-out/bin/fd-log-example
log-rex ./zig-out/bin/fd-log-example
```

**Nushell** (`$nu.config-path`):

```nu
$env.PATH = ($env.PATH | prepend [($env.PWD | path join zig-out bin) ($env.PWD | path join scripts)])
source examples/shell/config.nu
log files ./zig-out/bin/fd-log-example
log rex ./zig-out/bin/fd-log-example
log tmux ./zig-out/bin/fd-log-example
```

Use absolute paths for permanent configurations. Shortcuts append to
`FD_LOG_DIR` (defaulting to `./logs` in the working directory) and pass
arguments literally. They launch external programs, not shell builtins or
aliases. Application stdout/stderr redirections and pipelines work normally,
such as `log-files my-app | less` or `log files my-app | complete` in Nu.

### Rex / tmux viewers

Run the pane shortcut inside the corresponding multiplexer, or specify a log
directory explicitly:

```sh
fd-log-panes rex './logs with spaces' -- ./zig-out/bin/fd-log-example
fd-log-panes tmux ./logs -- ./zig-out/bin/fd-log-example
```

The application stays in the calling pane. Viewers appear as streams are
emitted, following regular files with `tail -n 20 -F`. They remain open when the
application exits. Closing a viewer does not break the logging pipe or block
the application.

Repeated runs from the same calling pane and log directory reuse surviving
viewers, creating replacements only for missing viewers. Pane bookkeeping stays
in the log directory; use a different directory to keep two runs separate.
Rex keeps the caller on the left with equal-height log viewers on the right,
without changing unrelated panes. Rex keeps severity colors reserved and assigns
new scopes the most widely spaced available hues, with muted dark backgrounds
and high-contrast text. Surviving viewers retain their colors across runs. With
very many streams, colors necessarily become less distinguishable; pane labels
remain authoritative. Colors stay in the shell, not the files or logging clients.
tmux uses the same local left/right arrangement,
with equal-height viewers, leaving unrelated panes alone.
Very many streams can exceed the available terminal space or multiplexer limits;
use file-only collection in that case.

Rex must already be installed and connected, with `REX_BLOCK` set and support
for embedded `rex do`, nested splits, and pane resizing. tmux needs `TMUX` and
sufficient room. The shell integration needs Bash and standard tools on PATH.
No external Lua or JSON parser is required for Rex.

## Nix / Home Manager

Add this input to your configuration:

```nix
inputs.fd-log.url = "github:Tiecoon/zig-fd-logging";
inputs.fd-log.inputs.nixpkgs.follows = "nixpkgs";
```

Import `inputs.fd-log.homeManagerModules.default` into **Home Manager**, not a
top-level NixOS/nix-darwin module. If your home modules receive `inputs` through
`extraSpecialArgs`:

```nix
{ inputs, ... }: {
  imports = [inputs.fd-log.homeManagerModules.default];
}
```

It installs the collector and shell helpers, sourcing config for each
already-enabled Bash, Zsh, Fish, and Nushell. It does not enable shells or
replace your config. Rebuild/activate normally and open a new shell; no manual
sourcing or local checkout is needed. Rex comes from your existing installation.
Zig is a build dependency, not a runtime dependency.

- `nix build` — collector, example, shell helpers, and shell configs.
- `nix build .#collector` — collector only.
- `nix build .#helpers` — collector plus shell helpers/configs, without the example.
- `nix build .#example` — example only.
- `nix build .#python` — installable Python handler package.

Configs are installed in `share/fd-log/config.{sh,fish,nu}`. The consuming
configuration pins a GitHub revision. After publishing an update, run
`nix flake update fd-log` there before rebuilding; this does not activate anything
by itself. No changes to `/etc/nixos` are needed for the new module interface.

## Transport and failure behavior

The launcher opens one blocking pipe, passes its writable descriptor through
`ZIG_LOG_FD`, and spawns the application with inherited standard streams. While
the application runs, the launcher ignores `SIGINT`/`SIGQUIT` and forwards
`SIGTERM` to the child so graceful shutdown logs drain cleanly before exit. It
returns the application's exit code (128 + signal number for signal exits), or
fails if collection fails. It waits for application completion and drains logs.
Other caller-opened inheritable FDs also reach the application unchanged: for
example, `fd-log-run ./logs -- my-app 7>extra.log` lets the application write to
FD 7 normally. This does not assign collected log streams to that descriptor;
the collector's discovered log outputs are files.

Records have a length-prefixed binary header containing severity, scope length,
and message length. Newlines and binary bytes cannot split or forge records.
A mutex serializes records from threads within one application. Typical records
use stack storage; long collected messages may allocate. No application-side
registration, worker thread, background service, or runtime dependency is needed.

If writing to the collector fails, the hook switches to standard stderr logging,
including retrying the current message. Data already accepted into the pipe may
be lost if the collector dies; a partially delivered record may be incomplete.
Slow collectors apply backpressure. These are best-effort logs, not a durable
message queue. The normal Zig process runtime handles SIGPIPE; an application
that overrides that signal policy is responsible for preserving it.

The inherited descriptor is borrowed: do not close, reuse, or write to it
outside this hook. Initialization marks it close-on-exec, so unrelated children
do not keep collection alive. Multiple processes must not share one logging
pipe; launch each participating executable with its own collector. Descendants
that intentionally retain the write end can delay EOF. Concurrent collectors
should use separate log directories when pane bookkeeping matters.

The collector optionally accepts `--on-stream SCRIPT`, executing
`SCRIPT DIRECTORY STREAM` once per discovered output per run, after writing its
first record. Arguments are not shell-evaluated, and hooks run with `/dev/null`
stdin/stdout so they cannot interfere with the application's pipelines. The
supplied pane helper uses this interface; the collector knows nothing about Rex
or tmux. Hook failures fail collection, close the pipe to enable application
stderr fallback, and wait for the application rather than killing it.

## Verification

`zig build test` exercises both Zig and Python clients through real processes:
standard stderr fallback, unchanged stdin/stdout/stderr and extra inherited FDs,
automatic scope/severity separation, name escaping, filtering, Python formatters
and exceptions, long/multiline/concurrent records, append behavior, broken
collectors, fragmented/truncated transport, hook arguments, and exit status.
Shell integration covers Bash, Zsh, Fish, and Nushell. Run
`python3 tests/panes.py` for separate real Rex/tmux reuse and replacement checks.
To exercise a specific Python version, run
`python3.10 tests/python_client.py zig-out/bin/fd-log-run`.

`nix flake check` builds and tests the packaged collector and helpers. Linux is
runtime-tested; Darwin cross-compilation is checked separately, not equivalent
to macOS runtime testing. Nix exposes x86_64/aarch64 Linux and Darwin outputs.
Windows HANDLE inheritance is out of scope. Upstream formatting updates arrive
with Zig; pre-1.0 API changes may still require source edits.
