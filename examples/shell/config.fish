# Add scripts/ to PATH, then source this in ~/.config/fish/config.fish.
# Usage: log-files ./zig-out/bin/fd-log-example --long
function log-files --description 'Run a command with per-level files in ./logs'
    command fd-log-run ./logs -- $argv
end
function log-tmux --description 'Run a command with per-level tmux viewers'
    command fd-log-panes tmux ./logs -- $argv
end
function log-rex --description 'Run a command with per-level Rex viewers'
    command fd-log-panes rex ./logs -- $argv
end
