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

# Show notification inbox using fzf in a popup.
# Takes pre-rendered inbox data via a temp file so the (process-scanning)
# inbox command runs once per open, not once more inside the popup shell.
inbox_popup() {
    local cmd="$1" data="$2"

    local tmpfile
    tmpfile=$(mktemp "${TMPDIR:-/tmp}/hop-inbox.XXXXXX") || return 1
    printf '%s\n' "$data" > "$tmpfile"

    local fzf_cmd
    fzf_cmd="trap 'rm -f \"$tmpfile\"' EXIT; fzf --ansi --reverse --no-info --with-nth=1 --delimiter='\t' --header='enter: jump / ctrl-x: clear all' --pointer='>' --prompt='' --bind='enter:execute-silent($cmd switch --pane {2})+abort' --bind='ctrl-x:execute-silent($cmd inbox-clear)+abort' < \"$tmpfile\" || true"

    tmux display-popup -E -w 80% -h 60% -T " Notifications " bash -c "$fzf_cmd"
}

# Show notification inbox using display-menu (fallback)
inbox_menu() {
    local cmd="$1"
    local menu_args=("-T" "#[align=centre]Notifications")
    local line label pane_id
    local count=0

    while IFS=$'\t' read -r label pane_id; do
        [[ -z "$label" ]] && continue
        menu_args+=("$label" "" "run-shell '$cmd switch --pane $pane_id'")
        count=$((count + 1))
    done < <($cmd inbox)

    if [[ $count -eq 0 ]]; then
        tmux display-message "No notifications"
        return 0
    fi

    # Separator + clear option
    menu_args+=("" "" "")
    menu_args+=("Clear" "x" "run-shell '$cmd inbox-clear'")

    tmux display-menu "${menu_args[@]}"
}

# Main notification inbox function
hop_inbox() {
    local cmd
    cmd=$(get_hop_cmd)

    if supports_popup && command -v fzf &>/dev/null; then
        local data
        data=$($cmd inbox --ansi)
        if [[ -z "$data" ]]; then
            tmux display-message "No notifications"
            return 0
        fi
        inbox_popup "$cmd" "$data"
    else
        inbox_menu "$cmd"
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
    inbox_key=$(get_tmux_option @hop-inbox-key "i")
    cycle_mode=$(get_tmux_option @hop-cycle-mode "priority")

    # Wrapper script uses local project via PYTHONPATH
    local cmd="$CURRENT_DIR/bin/claude-tmux-hop"

    # Build cycle command with mode flag
    local cycle_args="--pane '#{pane_id}' --mode '$cycle_mode'"

    # Bind cycle key (prefix + key)
    # Pass pane_id via tmux variable substitution since run-shell doesn't preserve pane context
    tmux bind-key "$cycle_key" run-shell "$cmd cycle $cycle_args"

    # Bind picker key (prefix + key) - uses bash function from this script
    tmux bind-key "$picker_key" run-shell "bash -c 'source \"$CURRENT_DIR/hop.tmux\" && hop_picker'"

    # Bind inbox key (prefix + key) - notification inbox
    tmux bind-key "$inbox_key" run-shell "bash -c 'source \"$CURRENT_DIR/hop.tmux\" && hop_inbox'"

    # Bind back key (root binding, no prefix needed)
    tmux bind-key -n "$back_key" run-shell "$cmd back"

    # Conductor (opt-in; off by default). A persistent detached tmux session
    # (default name `conductor`, configurable via @hop-conductor-session) hosts
    # claude. The popup key attaches the popup to that session (creating it on
    # demand). `prefix + d` inside the popup detaches without killing claude;
    # reopening the popup re-attaches. The respawn key tears down the session
    # first, then re-attaches to a fresh claude (destructive).
    local conductor_enabled conductor_popup_key conductor_respawn_key
    conductor_enabled=$(get_tmux_option @hop-conductor-enabled "off")
    case "$conductor_enabled" in
        on|1|true|yes)
            conductor_popup_key=$(get_tmux_option @hop-conductor-popup-key "y")
            conductor_respawn_key=$(get_tmux_option @hop-conductor-respawn-key "Y")
            tmux bind-key "$conductor_popup_key" run-shell "$cmd conductor --popup"
            tmux bind-key "$conductor_respawn_key" run-shell "$cmd conductor --popup --respawn"
            ;;
    esac

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
