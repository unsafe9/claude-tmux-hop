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
    local back_key
    local cycle_mode

    cycle_key=$(get_tmux_option @hop-cycle-key "Space")
    picker_key=$(get_tmux_option @hop-picker-key "C-Space")
    back_key=$(get_tmux_option @hop-back-key "M-Space")
    cycle_mode=$(get_tmux_option @hop-cycle-mode "priority")

    # Wrapper script respects @hop-dev-path for local development, otherwise uses uvx
    local cmd="$CURRENT_DIR/bin/claude-tmux-hop"

    # Build cycle command with mode flag
    local cycle_args="--pane '#{pane_id}' --mode '$cycle_mode'"

    # Bind cycle key (prefix + key)
    # Pass pane_id via tmux variable substitution since run-shell doesn't preserve pane context
    tmux bind-key "$cycle_key" run-shell "$cmd cycle $cycle_args"

    # Bind picker key (prefix + key)
    tmux bind-key "$picker_key" run-shell "$cmd picker"

    # Bind back key (root binding, no prefix needed)
    tmux bind-key -n "$back_key" run-shell "$cmd back"

    # Auto-discover existing Claude Code sessions (skips already registered panes)
    $cmd discover --quiet &

    # Set up status format for use in status-left/status-right
    # Users can use #{E:@hop-status} in their tmux config
    tmux set-option -g @hop-status "#($cmd status)"
}

main
