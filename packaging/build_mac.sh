#!/usr/bin/env bash
# Builds dist/Showrunner.app and dist/Showrunner.dmg. Must run on a Mac.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3}"
"$PYTHON" -m venv .venv-build
source .venv-build/bin/activate
pip install --upgrade pip
pip install -r requirements.txt pyinstaller

rm -rf build dist
pyinstaller --noconfirm --clean packaging/showrunner.spec

# Ad-hoc signature so Apple Silicon Macs will run it (no Apple developer account needed).
codesign --force --deep --sign - dist/Showrunner.app

# Drag-to-Applications disk image.
mkdir -p dist/dmg
cp -R dist/Showrunner.app dist/dmg/
ln -s /Applications dist/dmg/Applications
hdiutil create -volname Showrunner -srcfolder dist/dmg -ov -format UDZO dist/Showrunner.dmg
rm -rf dist/dmg

echo
echo "Built: dist/Showrunner.app and dist/Showrunner.dmg"
