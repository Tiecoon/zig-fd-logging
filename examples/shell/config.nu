# Add scripts/ and zig-out/bin/ to PATH, then source this in $nu.config-path.
# Usage: log files ./zig-out/bin/fd-log-example --long
# Override the default ./logs directory with $env.FD_LOG_DIR.

# Run a command with discovered stream files in $env.FD_LOG_DIR or ./logs.
def --wrapped "log files" [...command: string] {
    let dir = if ($env.FD_LOG_DIR? | is-not-empty) { $env.FD_LOG_DIR } else { "./logs" }
    ^fd-log-run $dir -- ...$command
}

# Run a command with discovered stream tmux viewers.
def --wrapped "log tmux" [...command: string] {
    let dir = if ($env.FD_LOG_DIR? | is-not-empty) { $env.FD_LOG_DIR } else { "./logs" }
    ^fd-log-panes tmux $dir -- ...$command
}

# Run a command with discovered stream Rex viewers.
def --wrapped "log rex" [...command: string] {
    let dir = if ($env.FD_LOG_DIR? | is-not-empty) { $env.FD_LOG_DIR } else { "./logs" }
    ^fd-log-panes rex $dir -- ...$command
}
