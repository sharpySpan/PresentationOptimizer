#!/usr/bin/env bash
# Runs the tests in CI. On failure, the end of the output becomes a GitHub annotation,
# which is readable without logging in (full job logs are not).
set -uo pipefail
QT_QPA_PLATFORM=offscreen python -m pytest -q -rfE 2>&1 | tee pytest.log
status=${PIPESTATUS[0]}
if [ "$status" -ne 0 ]; then
  summary=$(tail -c 3500 pytest.log | python -c "import sys; print(sys.stdin.read().replace('%','%25').replace('\r','').replace('\n','%0A'))")
  echo "::error title=pytest failed::${summary}"
fi
exit "$status"
