# Only this dashboard shell uses this file; normal shell settings are retained.
if [[ -r "$HOME/.bashrc" ]]; then source "$HOME/.bashrc"; fi
if [[ -z ${GRUVBOX_GREETING_DONE:-} ]]; then
    export GRUVBOX_GREETING_DONE=1
    clear
    printf '\033[38;2;126;231;135m  ARCH / WORKSPACE\033[0m\n'
    fastfetch --config "$GRUVBOX_DESKTOP_DIR/fastfetch.jsonc"
    printf '\n\033[38;2;141;161;143m  Рабочий терминал · Ctrl+Shift+стрелки — панели · Ctrl+Shift+Z — увеличить\033[0m\n\n'
fi
PS1='\[\e[38;2;167;243;160m\]\u\[\e[38;2;141;161;143m\] @ \[\e[38;2;126;231;135m\]\h \[\e[38;2;155;216;176m\]\w\n\[\e[38;2;126;231;135m\]❯ \[\e[0m\]'
source "${GRUVBOX_DESKTOP_DIR:-$HOME/.config/kitty/gruvbox-desktop}/robot_hooks.bash"
