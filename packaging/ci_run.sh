#!/usr/bin/env bash
# ci_run.sh <command...>: runs a CI command; on failure the end of its output becomes a GitHub
# annotation, which is readable without logging in (full job logs are not).
set -uo pipefail
"$@" 2>&1 | tee ci-output.log
status=${PIPESTATUS[0]}
if [ "$status" -ne 0 ]; then
  summary=$(tail -c 3500 ci-output.log | python -c "import sys; print(sys.stdin.read().replace('%','%25').replace('\r','').replace('\n','%0A'))")
  echo "::error title=$1 failed::${summary}"
fi
exit "$status"
