# Add scripts/ and zig-out/bin/ to PATH, then source this in ~/.config/fish/config.fish.
# Usage: log-files ./zig-out/bin/fd-log-example --long
# Override the default ./logs directory with FD_LOG_DIR.
function log-files --description 'Run a command with discovered stream files in $FD_LOG_DIR or ./logs'
    set -l dir ./logs
    if set -q FD_LOG_DIR; and test -n "$FD_LOG_DIR"
        set dir $FD_LOG_DIR
    end
    command fd-log-run "$dir" -- $argv
end
function log-tmux --description 'Run a command with discovered stream tmux viewers'
    set -l dir ./logs
    if set -q FD_LOG_DIR; and test -n "$FD_LOG_DIR"
        set dir $FD_LOG_DIR
    end
    command fd-log-panes tmux "$dir" -- $argv
end
function log-rex --description 'Run a command with discovered stream Rex viewers'
    set -l dir ./logs
    if set -q FD_LOG_DIR; and test -n "$FD_LOG_DIR"
        set dir $FD_LOG_DIR
    end
    command fd-log-panes rex "$dir" -- $argv
end
