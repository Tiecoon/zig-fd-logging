# Add scripts/ and zig-out/bin/ to PATH, then source this from ~/.bashrc or ~/.zshrc.
# Usage: log-files ./zig-out/bin/fd-log-example --long
# Files and viewers are created for discovered streams, not fixed levels.
# Override the default ./logs directory with FD_LOG_DIR.
log-files() { command fd-log-run "${FD_LOG_DIR:-./logs}" -- "$@"; }
log-tmux() { command fd-log-panes tmux "${FD_LOG_DIR:-./logs}" -- "$@"; }
log-rex() { command fd-log-panes rex "${FD_LOG_DIR:-./logs}" -- "$@"; }
