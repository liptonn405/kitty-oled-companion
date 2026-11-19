# Source once in an interactive dashboard Bash. Preserve existing prompt hooks.
if [[ $- == *i* && -z ${_GRUVBOX_ROBOT_HOOKS_READY:-} ]]; then
    _GRUVBOX_ROBOT_HOOKS_READY=1
    _gruvbox_robot_begin() {
        python3 "${GRUVBOX_DESKTOP_DIR:-$HOME/.config/kitty/gruvbox-desktop}/robot_bus.py" start "$$" 2>/dev/null || :
    }
    _gruvbox_robot_end() {
        local robot_status=$?
        python3 "${GRUVBOX_DESKTOP_DIR:-$HOME/.config/kitty/gruvbox-desktop}/robot_bus.py" end "$$" "$robot_status" 2>/dev/null || :
        return "$robot_status"
    }
    PS0="${PS0-}"'$(_gruvbox_robot_begin)'
    if [[ $(declare -p PROMPT_COMMAND 2>/dev/null) == 'declare -a '* ]]; then
        PROMPT_COMMAND=(_gruvbox_robot_end "${PROMPT_COMMAND[@]}")
    elif [[ -n ${PROMPT_COMMAND:-} ]]; then
        PROMPT_COMMAND=(_gruvbox_robot_end "$PROMPT_COMMAND")
    else
        PROMPT_COMMAND=(_gruvbox_robot_end)
    fi
fi

# Re-sourcing installs Git observation without duplicating prompt hooks.
# Keep user-defined git functions intact; ordinary aliases continue to work.
if [[ $- == *i* && -z ${_GRUVBOX_ROBOT_GIT_READY:-} ]] && [[ $(type -t git) != function ]]; then
    _GRUVBOX_ROBOT_GIT_READY=1
    function git {
        GRUVBOX_ROBOT_SHELL_PID="$$" python3 "${GRUVBOX_DESKTOP_DIR:-$HOME/.config/kitty/gruvbox-desktop}/robot_git.py" "$@"
    }
fi
