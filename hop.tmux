#!/usr/bin/env bash
#
# claude-tmux-hop - TPM plugin for hopping between Claude Code sessions
#

CURRENT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

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
    local expand

    cycle_key=$(get_tmux_option @hop-cycle-key "Space")
    picker_key=$(get_tmux_option @hop-picker-key "C-Space")
    expand=$(get_tmux_option @hop-expand "off")

    # Wrapper script respects @hop-dev-path for local development, otherwise uses uvx
    local cmd="$CURRENT_DIR/bin/claude-tmux-hop"

    # Build cycle command with optional --expand flag
    local cycle_args="--pane '#{pane_id}'"
    [[ "$expand" == "on" ]] && cycle_args="--expand $cycle_args"

    # Bind cycle key
    # Pass pane_id via tmux variable substitution since run-shell doesn't preserve pane context
    tmux bind-key "$cycle_key" run-shell "$cmd cycle $cycle_args"

    # Bind picker key
    tmux bind-key "$picker_key" run-shell "$cmd picker"

    # Auto-discover existing Claude Code sessions (skips already registered panes)
    $cmd discover --quiet &
}

main
