# zig-fd-logging

A small POSIX file-descriptor sink for **Zig 0.17.1** (latest stable). No Zig
package dependencies. Nix is optional; `flake.lock` pins nixpkgs and Zig for
reproducible development and builds.

| `std.log` level | Inherited FD | Helper output |
| --- | ---: | --- |
| `.err` | 3 | `err.log` |
| `.warn` | 4 | `warn.log` |
| `.info` | 5 | `info.log` |
| `.debug` | 6 | `debug.log` |

stdin/stdout/stderr stay untouched. Panics, stack traces, and direct writes to
stderr are not logging calls and are not rerouted.

## Import into another Zig project

Add a local dependency to your application's `build.zig.zon` (or use
`zig fetch --save=fd_log URL` once this package is hosted):

```zig
.dependencies = .{
    .fd_log = .{ .path = "../zig-fd-logging" },
},
```

In its `build.zig`, after creating the executable:

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
    .log_level = .debug, // Optional; otherwise std.log's build-mode default applies.
};

pub fn main(init: std.process.Init) !void {
    try fd_log.configureFromEnvironment(init.environ_map);
    std.log.info("listening on port {d}", .{8080});
    std.log.scoped(.network).warn("retrying", .{});
}
```

Library code keeps calling ordinary `std.log`. All levels, scoped loggers,
compile-time filtering, and argument formatting stay upstream. The sink calls
`std.log.defaultLogFileTerminal` rather than copying its formatter. Unrouted
messages call `std.log.defaultLog`, including its standard color behavior.
There is no parallel logging API or compatibility shim.

Without `ZIG_LOG_FDS=1`, `configureFromEnvironment` keeps logging to stderr,
even if other FDs happen to exist. With it, all four reserved FDs must be valid.
For custom or partial routing, use `configure` instead:

```zig
try fd_log.configure(.{ .warn = 9, .debug = 10 });
```

Null routes use stderr. Configure before concurrent logging. Descriptors are
borrowed and must remain open, writable, and blocking. Invalid configuration
returns `error.InvalidDescriptor` without changing the previous routes. Logging
before initialization uses stderr. Do not close or repurpose configured FDs.

## Build and run

```sh
nix develop
zig build
./scripts/fd-log-run './logs with spaces' -- ./zig-out/bin/fd-log-example
zig build test
```

The helper works unchanged from **Bash, Zsh, Fish, and Nushell**; Nu can prefix the
command with `^`. It opens append-only files, sets `ZIG_LOG_FDS=1`, and execs the
application, preserving arguments and exit/signal behavior. Use the **built
binary**, not `zig build run`: intermediate tools may close or repurpose FDs.

Bash and current Fish support direct numeric-FD redirection too; Nushell does
not interpret `3>file` that way. For example, in Bash:

```bash
ZIG_LOG_FDS=1 ./zig-out/bin/fd-log-example \
  3>>err.log 4>>warn.log 5>>info.log 6>>debug.log
```

## Nix / Home Manager

Add this flake as an input (use a hosted URL once published):

```nix
inputs.fd-log.url = "path:/home/luke/projects/zig-fd-logging";
inputs.fd-log.inputs.nixpkgs.follows = "nixpkgs";
```

Then import its module in your Home Manager configuration, with `inputs` in scope:

```nix
imports = [inputs.fd-log.homeManagerModules.default];
```

The module installs `packages.<system>.helpers` and sources the matching config
for each **already-enabled** shell: Bash, Zsh, Fish, and Nushell. It does not
enable shells, change your login shell, or replace your existing config. The
helpers package does not compile or depend on Zig. It installs the scripts and
configs under immutable store paths; no checkout is needed at shell startup.
Rex still comes from your existing installation.

A local path input must exist wherever you update or rebuild the Nix config;
replace it with a hosted URL before using the config on other machines. Changes
to this project require `nix flake update fd-log` in the consuming config before
rebuilding. This does not activate anything by itself.

## Shell config examples

Without Home Manager, source the matching example from the project root, or
copy its functions into `~/.bashrc`, `~/.zshrc`,
`~/.config/fish/config.fish`, or Nushell's `$nu.config-path`.
For a permanent setup, add the absolute `scripts/` path to your shell's PATH
(or use the helpers installed by Nix).

**Bash / Zsh:**

```sh
export PATH="$PWD/scripts:$PATH"
source examples/shell/config.sh
```

**Fish:**

```fish
set -gx PATH "$PWD/scripts" $PATH
source examples/shell/config.fish
```

**Nushell:**

```nu
$env.PATH = ($env.PATH | prepend ($env.PWD | path join scripts))
source examples/shell/config.nu
```

The shortcuts are command-first and always append to `./logs` in the current
directory. **Bash / Zsh / Fish:**

```sh
log-files ./zig-out/bin/fd-log-example --long
log-tmux ./zig-out/bin/fd-log-example
log-rex ./zig-out/bin/fd-log-example
```

**Nushell** uses native subcommands with typed rest arguments and help:

```nu
log files ./zig-out/bin/fd-log-example --long
log tmux ./zig-out/bin/fd-log-example
log rex ./zig-out/bin/fd-log-example
help log files
```

Arguments, pipes, and stdout/stderr redirections work as they normally do in
that shell. For example, `log-files my-app | less` (Bash/Fish) or
`log files my-app | complete` (Nu) captures application output, while per-level
logs still go to their separate files. These shortcuts run external programs,
not shell builtins, aliases, or Nushell custom commands.

For a different directory, use `fd-log-run './logs with spaces' -- my-app`
or `fd-log-panes rex './logs with spaces' -- my-app` directly. The pane commands
require running inside that multiplexer. The configs contain no FD or pane
implementation; that stays in `scripts/`.

## Pane helpers

Run inside an existing tmux or Rex pane:

```sh
./scripts/fd-log-panes tmux ./logs -- ./zig-out/bin/fd-log-example
./scripts/fd-log-panes rex ./logs -- ./zig-out/bin/fd-log-example
```

Both create four background viewers and run the application in the original
pane. tmux requires `TMUX` and enough room; it tiles the current window. Rex
requires `REX_BLOCK`, anchors to that block, labels each viewer, and preserves
focus. Rex itself must already be installed and connected.

Viewers run `tail -n 20 -F` on regular files, not FIFOs: closing a pane cannot
block the application or cause SIGPIPE. They remain open after the application
exits; stop them with Ctrl-C or close the panes. Files append across runs; use a
new directory for a fresh run. If pane creation fails, the application does not
start; any viewers already created remain for manual inspection/closing.
You can also run `fd-log-run` alone and start your own `tail -F` viewers.

## Contracts and verification

Routed output is uncolored and flushed per message using a buffered streaming
writer. It is not truncated at the buffer boundary. A mutex prevents routed
records from this process interleaving, even when routes share a descriptor.
It does not synchronize other processes or arbitrary direct writes. Slow sinks
can block; write failures are best effort, as with `std.log`. No fallback retry
after a partial write. Custom pipes follow the application's SIGPIPE policy.
Clear the environment marker and close reserved FDs for unrelated children that
must not inherit the logging convention.

`zig build test` checks real executable behavior under all four shells:
level separation, unchanged stdout/stderr, scope filtering, fallback and partial
routing, invalid FDs, append semantics, long/concurrent records, arguments, and
exit status. `nix flake check` also builds the installed package; `nix build`
puts the example and both helpers in `result/bin`.

Linux is runtime-tested. Both macOS architectures are cross-compiled, not
runtime-tested. Nix exposes x86_64/aarch64 Linux and Darwin outputs; Windows
HANDLE inheritance is out of scope. Update with `nix flake update`, then check
`zig version`, `zig build test`, and `nix flake check`. Upstream formatter updates
come with the toolchain; pre-1.0 Zig API changes may still require source edits.
