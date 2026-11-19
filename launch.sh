#!/usr/bin/env bash
set -euo pipefail
bundle_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export GRUVBOX_DESKTOP_DIR="$bundle_dir"
unset GRUVBOX_GREETING_DONE
cd "$HOME"
exec kitty --class arch-gruvbox-desktop --title 'Arch · Gruvbox Desktop' \
    --config "$bundle_dir/kitty.conf" --session "$bundle_dir/desktop.session" --start-as maximized
