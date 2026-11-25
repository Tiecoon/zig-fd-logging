# Add scripts/ to PATH, then source this in $nu.config-path.
# Usage: log files ./zig-out/bin/fd-log-example --long

# Run a command with per-level files in ./logs.
def --wrapped "log files" [...command: string] {
    ^fd-log-run ./logs -- ...$command
}

# Run a command with per-level tmux viewers.
def --wrapped "log tmux" [...command: string] {
    ^fd-log-panes tmux ./logs -- ...$command
}

# Run a command with per-level Rex viewers.
def --wrapped "log rex" [...command: string] {
    ^fd-log-panes rex ./logs -- ...$command
}
