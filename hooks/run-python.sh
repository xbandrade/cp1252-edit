#!/bin/sh
# Run a Python 3 script with the first interpreter that actually works.
# `python`/`python3` can be Windows Store stubs that exist on PATH but only
# print "Python was not found", so each candidate is test-run, not just looked up.
script="$1"
shift
for py in python3 python "py -3"; do
  if $py -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)' </dev/null >/dev/null 2>&1; then
    exec $py "$script" "$@"
  fi
done
echo "cp1252-edit: no working Python 3.7+ found (tried python3, python, py -3); encoding guard skipped" >&2
exit 0
