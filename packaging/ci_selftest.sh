#!/usr/bin/env bash
# Runs the built app's --self-test (the real pipeline, inside the bundle) and fails if it isn't OK.
set -uo pipefail
report="$PWD/selftest-report.txt"
rm -f "$report"
QT_QPA_PLATFORM=offscreen "$1" --self-test "$report"
cat "$report" 2>/dev/null || echo "no report: the app did not start"
[ "$(head -n1 "$report" 2>/dev/null)" = "OK" ]
