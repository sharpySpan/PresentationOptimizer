#!/usr/bin/env bash
# Installs Showrunner for the current user: ~/.local/share/showrunner + an app-menu entry.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
target="$HOME/.local/share/showrunner"

rm -rf "$target"
mkdir -p "$target" "$HOME/.local/share/applications" "$HOME/.local/bin"
cp -R "$here"/. "$target"/
ln -sf "$target/Showrunner" "$HOME/.local/bin/showrunner"
sed "s|@EXEC@|$target/Showrunner|" "$here/showrunner.desktop" > "$HOME/.local/share/applications/showrunner.desktop"
echo "Installed. Start it from your app menu, or run: showrunner"
