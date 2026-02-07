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

# Get the hop command path (uses local bin wrapper)
get_hop_cmd() {
    echo "$CURRENT_DIR/bin/claude-tmux-hop"
}

# Check tmux version >= 3.2 (for popup support)
supports_popup() {
    local version
    version=$(tmux -V | sed 's/[^0-9.]//g')
    local major minor
    major=$(echo "$version" | cut -d. -f1)
    minor=$(echo "$version" | cut -d. -f2)
    [[ "$major" -gt 3 ]] || { [[ "$major" -eq 3 ]] && [[ "$minor" -ge 2 ]]; }
}

# Show picker using fzf in a popup
picker_popup() {
    local cmd="$1"

    # fzf options:
    # --ansi: enable color codes
    # --reverse: show from top
    # --no-info: hide match count
    # --with-nth=1: only show first field (before tab)
    # The selected line's second field (pane_id) is used for switching
    local fzf_cmd
    fzf_cmd="$cmd picker-data | fzf --ansi --reverse --no-info --with-nth=1 --delimiter='\t' --header='Claude Sessions' --pointer='>' --prompt='' --bind='enter:execute-silent($cmd switch --pane {2})+abort' || true"

    tmux display-popup -E -w 50% -h 50% -T " Claude Sessions " bash -c "$fzf_cmd"
}

# Show picker using display-menu (fallback)
picker_menu() {
    local cmd="$1"
    local menu_args=("-T" "#[align=centre]Claude Sessions")
    local line label pane_id

    while IFS=$'\t' read -r label pane_id; do
        [[ -z "$pane_id" ]] && continue
        menu_args+=("$label" "" "run-shell '$cmd switch --pane $pane_id'")
    done < <($cmd picker-data)

    if [[ ${#menu_args[@]} -eq 2 ]]; then
        tmux display-message "No Claude Code sessions found"
        return 0
    fi

    tmux display-menu "${menu_args[@]}"
}

# Main picker function
hop_picker() {
    local cmd
    cmd=$(get_hop_cmd)

    if supports_popup && command -v fzf &>/dev/null; then
        picker_popup "$cmd"
    else
        picker_menu "$cmd"
    fi
}

# Main plugin setup
main() {
    local cycle_key
    local picker_key
    local back_key
    local cycle_mode

    cycle_key=$(get_tmux_option @hop-cycle-key "Space")
    picker_key=$(get_tmux_option @hop-picker-key "C-f")
    back_key=$(get_tmux_option @hop-back-key "C-Space")
    cycle_mode=$(get_tmux_option @hop-cycle-mode "priority")

    # Wrapper script uses local project via PYTHONPATH
    local cmd="$CURRENT_DIR/bin/claude-tmux-hop"

    # Build cycle command with mode flag
    local cycle_args="--pane '#{pane_id}' --mode '$cycle_mode'"

    # Bind cycle key (prefix + key)
    # Pass pane_id via tmux variable substitution since run-shell doesn't preserve pane context
    tmux bind-key "$cycle_key" run-shell "$cmd cycle $cycle_args"

    # Bind picker key (prefix + key) - uses bash function from this script
    tmux bind-key "$picker_key" run-shell "source '$CURRENT_DIR/hop.tmux' && hop_picker"

    # Bind back key (root binding, no prefix needed)
    tmux bind-key -n "$back_key" run-shell "$cmd back"

    # Auto-discover existing Claude Code sessions (skips already registered panes)
    $cmd discover --quiet 2>/dev/null || true &

    # Set up status format for use in status-left/status-right
    # Users can use #{E:@hop-status} in their tmux config
    tmux set-option -g @hop-status "#($cmd status)"

    # Version check: compare tmux plugin with Claude Code plugin
    local tmux_version claude_plugin_path claude_version
    tmux_version=$(python3 -c "import tomllib; print(tomllib.load(open('$CURRENT_DIR/pyproject.toml','rb'))['project']['version'])" 2>/dev/null || grep -m1 '^version = ' "$CURRENT_DIR/pyproject.toml" | sed 's/version = "\(.*\)"/\1/')
    claude_plugin_path="$HOME/.claude/plugins/claude-tmux-hop"

    if [[ -n "$tmux_version" && -x "$claude_plugin_path/bin/claude-tmux-hop" ]]; then
        claude_version=$("$claude_plugin_path/bin/claude-tmux-hop" --version 2>/dev/null | awk '{print $2}')
        if [[ -n "$claude_version" && "$tmux_version" != "$claude_version" ]]; then
            tmux display-message "claude-tmux-hop: version mismatch (tmux: $tmux_version, claude: $claude_version)"
        fi
    fi
}

main
