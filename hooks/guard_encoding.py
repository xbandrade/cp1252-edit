#!/usr/bin/env python3
"""
PreToolUse guard: stop the agent's UTF-8 Edit/Write tools from touching legacy
(Windows-1252 & co.) files, which they would silently corrupt.

Blocks (exit 2, reason on stderr -- the agent sees it and switches tools):
  * Edit/Write on an existing file that is NOT valid UTF-8 -- any extension.
    Saving it through a UTF-8 tool replaces every accent with U+FFFD, even if
    the edit itself is pure ASCII.
  * Edit/Write that would put non-ASCII text into a file whose extension is
    listed in CP1252_EDIT_EXTENSIONS (default .prw,.prx,.ch,.prg) -- e.g. an
    accent added to a pure-ASCII .prw, or a brand-new .prw with accents. The
    tool would store UTF-8 bytes in a file the compiler reads as cp1252.

Allows everything else, and fails open (exit 0) on anything unexpected, so a
broken guard never blocks unrelated work. CP1252_EDIT_GUARD=off disables it.
"""
import json
import os
import sys

EXTS = os.environ.get("CP1252_EDIT_EXTENSIONS") or ".prw,.prx,.ch,.prg"


def _launcher():
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root:
        return 'sh "%s/skills/cp1252-edit/scripts/cp1252"' % root.replace("\\", "/")
    return "the cp1252-edit skill's scripts/cp1252"


def _block(path, why):
    sys.stderr.write(
        "BLOCKED by cp1252-edit guard: %s\n%s\n"
        "Use the cp1252-edit skill instead (%s): `view` to read the file, `replace` "
        "to edit it, `convert` to create a new legacy file from a UTF-8 draft "
        "written with a .utf8 suffix. Do not retry with Edit/Write.\n"
        % (path, why, _launcher()))
    sys.exit(2)


def main():
    if os.environ.get("CP1252_EDIT_GUARD", "").lower() in ("off", "0", "false", "no"):
        return
    try:
        event = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    except Exception:
        return
    tool = event.get("tool_name", "")
    inp = event.get("tool_input") or {}
    path = inp.get("file_path")
    if tool not in ("Edit", "Write", "MultiEdit") or not isinstance(path, str):
        return
    if not os.path.isabs(path) and event.get("cwd"):
        path = os.path.join(event["cwd"], path)

    if tool == "Write":
        incoming = [inp.get("content") or ""]
    elif tool == "MultiEdit":
        incoming = [e.get("new_string") or "" for e in inp.get("edits") or []]
    else:
        incoming = [inp.get("new_string") or ""]
    adds_non_ascii = any(not s.isascii() for s in incoming if isinstance(s, str))
    exts = {("." + e.strip().lstrip(".")).lower() for e in EXTS.split(",") if e.strip()}
    legacy_ext = os.path.splitext(path)[1].lower() in exts

    if os.path.isfile(path):
        try:
            with open(path, "rb") as fh:
                raw = fh.read()
        except OSError:
            return
        if raw.startswith(b"\xef\xbb\xbf"):
            return                                   # declared UTF-8
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as e:
            _block(path, "It is not valid UTF-8 (byte 0x%02X at offset %d): it is a "
                         "legacy-encoded (Windows-1252) file, and saving it through "
                         "Edit/Write would replace every accented character with "
                         "U+FFFD -- even for an ASCII-only change."
                   % (raw[e.start], e.start))
        if raw.isascii() and legacy_ext and adds_non_ascii:
            _block(path, "It is pure ASCII today, but %s files are Windows-1252 in "
                         "this project and your change adds non-ASCII text, which "
                         "Edit/Write would store as UTF-8 bytes."
                   % os.path.splitext(path)[1])
        return

    if tool == "Write" and legacy_ext and adds_non_ascii:
        _block(path, "New %s files must be Windows-1252, and this content has "
                     "non-ASCII characters that Write would store as UTF-8."
               % os.path.splitext(path)[1])


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        pass                                         # fail open
