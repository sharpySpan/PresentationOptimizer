#!/usr/bin/env bash
# Installs Showrunner for the current user: ~/.local/share/showrunner-app + an app-menu entry.
# (~/.local/share/showrunner is the app's data folder, with the downloaded converter: never wiped here.)
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
target="$HOME/.local/share/showrunner-app"

rm -rf "$target"
mkdir -p "$target" "$HOME/.local/share/applications" "$HOME/.local/bin"
cp -R "$here"/. "$target"/
ln -sf "$target/Showrunner" "$HOME/.local/bin/showrunner"
sed "s|@EXEC@|$target/Showrunner|" "$here/showrunner.desktop" > "$HOME/.local/share/applications/showrunner.desktop"
if command -v ldconfig >/dev/null && ! ldconfig -p | grep -q "libxcb-cursor.so.0"; then
  echo "Note: if Showrunner doesn't open, install one small system library:"
  echo "  Ubuntu/Debian: sudo apt install libxcb-cursor0    Fedora: sudo dnf install xcb-util-cursor"
fi
echo "Installed. Start it from your app menu, or run: showrunner"
