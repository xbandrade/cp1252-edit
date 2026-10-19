# cp1252-edit

A [Claude Code](https://claude.com/claude-code) plugin that lets the agent work
on **Windows-1252 (cp1252) and other legacy single-byte encoded files** without
destroying them. Examples include AdvPL/Protheus `.prw`/`.prx`/`.ch`,
Clipper/Harbour `.prg`, Delphi, VB6 and old SQL scripts.

## The problem

In cp1252 each accented character (`ç ã é õ ü ñ …`) is a single byte in
`0x80`–`0xFF`, and such a byte on its own is invalid UTF-8. The agent's
built-in Read/Edit/Write tools work in UTF-8. On save, they rewrite the **whole
file**, and every accent becomes `U+FFFD` (`�`) for good. This happens even
when the change itself only touched ASCII. One ASCII-only edit to a 2,000-line
AdvPL source corrupted all 484 of its accented lines. Telling the model to
"keep edits ASCII-only" doesn't help.

## What you get

- **Skill `cp1252-edit`** teaches the agent the safe workflow and ships a
  stdlib-only Python tool, `cp1252_edit.py`:

  | command   | what it does |
  |-----------|--------------|
  | `view`    | prints the file decoded, with line numbers. `--show-special` reveals NBSP, tabs and control bytes |
  | `search`  | grep that matches accented text. The Grep tool silently misses it in legacy files |
  | `replace` | exact, unique find/replace like the Edit tool, with batch JSON edits from a file or stdin. Supports `--dry-run`/`--diff` and gives hints when a match fails |
  | `convert` | turns a UTF-8 draft (or a legacy file re-saved as UTF-8) into cp1252. `--unmojibake` fixes `Ã§` → `ç` |
  | `check`   | reports encoding, line endings, NBSPs and damage for files or whole directories. Exits 1 on problems, so it works in CI and pre-commit |
  | `recover` | restores accents lost to `U+FFFD`, taking them from `git HEAD` (or any reference copy) and keeping later ASCII edits |

  Untouched bytes stay identical: the tool reads and writes raw bytes and
  keeps every line's own terminator (CRLF, LF or mixed). Bytes the code page
  leaves undefined (`0x81`, `0x8D`, …) also round-trip. Output is always
  UTF-8, so accents survive pipes on Windows.

- **Guard hook** (`PreToolUse` on Edit/Write) blocks, with a message that
  points the agent to the skill:
  - Edit/Write on any existing file that is not valid UTF-8, whatever its
    extension
  - non-ASCII text going into a file whose extension is configured as legacy:
    a new `.prw` with accents, or an accent added to a pure-ASCII `.prw`

  Everything else passes, and the guard fails open if Python is missing or
  anything unexpected happens.

## Install

Requirements: Python 3.7+ on PATH (`python3`, `python` or the Windows `py`
launcher; Windows Store stubs are detected and skipped), and `sh` (Git Bash on
Windows, which Claude Code already uses).

### As a plugin (skill + guard hook)

```
/plugin marketplace add brunoandrade/cp1252-edit
/plugin install cp1252-edit@cp1252-edit
```

From a shell: `claude plugin marketplace add brunoandrade/cp1252-edit`, then
`claude plugin install cp1252-edit@cp1252-edit` (add `--scope project` to
share it through the repo's `.claude/settings.json`).

### Skill only (manual copy)

Copy `skills/cp1252-edit/` to `~/.claude/skills/cp1252-edit/` (all projects)
or to `<project>/.claude/skills/cp1252-edit/` (one project, and committed with
it). You can add the guard by hand in `.claude/settings.json`, using an
absolute path to `hooks/`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Edit|Write|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "sh \"/path/to/cp1252-edit/hooks/run-python.sh\" \"/path/to/cp1252-edit/hooks/guard_encoding.py\"",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

## Configure

Environment variables. Set them in your shell, or under `"env"` in
`.claude/settings.json` so the hook sees them too:

| variable | default | meaning |
|----------|---------|---------|
| `CP1252_EDIT_EXTENSIONS` | `.prw,.prx,.ch,.prg` | extensions that are legacy-encoded in your project. Used by the hook and by `check`/`search` directory walks (case-insensitive) |
| `CP1252_EDIT_ENCODING` | `cp1252` | any single-byte code page: `latin-1`, `iso8859-15`, `cp1250`, `cp850`, … (or `--encoding` per command) |
| `CP1252_EDIT_GUARD` | on | `off` disables the hook |

Example for a Delphi project:

```json
{ "env": { "CP1252_EDIT_EXTENSIONS": ".pas,.dfm,.dpr,.inc" } }
```

It also helps to say it once in the project's `CLAUDE.md`:

```markdown
Source files (*.prw, *.prx, *.ch) are Windows-1252 with CRLF. Never use Edit/Write on
them, not even for ASCII-only changes; use the cp1252-edit skill.
```

## Command line

The tool works without Claude too:

```bash
sh skills/cp1252-edit/scripts/cp1252 check -q src/          # audit a tree
sh skills/cp1252-edit/scripts/cp1252 view src/File.prw --start 10 --end 40
sh skills/cp1252-edit/scripts/cp1252 recover src/File.prw   # after an accident
python3 skills/cp1252-edit/scripts/cp1252_edit.py --help    # direct call
```

## Tip: readable `git diff` for cp1252 files

Git prints raw bytes, so accents show up as `<E7>` or `�` in diffs, for both
you and the agent. A textconv driver decodes them for display only; the
stored content doesn't change:

```bash
git config diff.cp1252.textconv "iconv -f cp1252 -t utf-8"
echo "*.prw diff=cp1252" >> .gitattributes    # repeat per extension
```

## Tests

```bash
python3 -m unittest discover -s tests -v
```

## License

MIT, see [LICENSE](LICENSE).
