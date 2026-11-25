# Add scripts/ to PATH, then source this from ~/.bashrc or ~/.zshrc.
# Usage: log-files ./zig-out/bin/fd-log-example --long
log-files() { command fd-log-run ./logs -- "$@"; }
log-tmux() { command fd-log-panes tmux ./logs -- "$@"; }
log-rex() { command fd-log-panes rex ./logs -- "$@"; }
