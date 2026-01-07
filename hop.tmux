#!/usr/bin/env bash
#
# claude-tmux-hop - TPM plugin for hopping between Claude Code sessions
#

# Get tmux option with default value
get_tmux_option() {
    local option="$1"
    local default="$2"
    local value
    value=$(tmux show-option -gqv "$option")
    echo "${value:-$default}"
}

# Main plugin setup
main() {
    local cycle_key
    local picker_key

    cycle_key=$(get_tmux_option @hop-cycle-key "Tab")
    picker_key=$(get_tmux_option @hop-picker-key "C-Tab")

    # Bind cycle key (uvx always uses latest from PyPI)
    tmux bind-key "$cycle_key" run-shell "uvx claude-tmux-hop cycle"

    # Bind picker key
    tmux bind-key "$picker_key" run-shell "uvx claude-tmux-hop picker"

    # Auto-discover existing Claude Code sessions (skips already registered panes)
    uvx claude-tmux-hop discover --quiet &
}

main
