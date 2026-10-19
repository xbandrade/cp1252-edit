---
name: cp1252-edit
description: >-
  View, search, edit, check and repair Windows-1252 (cp1252) and other legacy
  single-byte encoded text files without corrupting them — e.g. AdvPL/Protheus
  .prw/.prx/.ch, Clipper/Harbour .prg, Delphi .pas/.dfm, VB6 .bas/.frm/.cls, old
  SQL/COBOL/INI files. ALWAYS use this instead of the Edit or Write tools on any
  existing file that is not valid UTF-8: those tools re-save the whole file as UTF-8
  and destroy every accented character (á ç ã é õ ü ñ …), even when the change
  itself is ASCII-only. Trigger when editing such a file, when Read shows "�" or
  mojibake like "Ã§" / "Ã£", when the cp1252-edit guard hook blocks an Edit/Write,
  when Grep can't find accented text in a legacy file, when creating a new legacy
  file that must contain accents, or when a file's accents were already damaged.
allowed-tools: Bash(sh ${CLAUDE_SKILL_DIR}/scripts/cp1252 *)
---

# Editing Windows-1252 / legacy-encoded files

## The trap

Legacy sources store each accented character as **one byte** in `0x80`–`0xFF`
(cp1252: `ç` = `0xE7`). On its own such a byte is invalid UTF-8, so:

- **Read** shows `�` where accents are — text you copy from it won't match.
- **Edit / Write** re-encode the *whole* file as UTF-8. Every accent in the file
  becomes `U+FFFD` (bytes `EF BF BD`), permanently. "Keeping my edit ASCII-only"
  does **not** help: the damage comes from the round-trip, not from your text.
- **Grep** matches bytes: ASCII patterns work, accented patterns silently miss.

So **never use Edit or Write on an existing non-UTF-8 file.** Use the bundled
script: it decodes/encodes with the legacy code page, writes raw bytes, and keeps
every line's terminator (CRLF, LF or mixed), so untouched bytes stay identical.

Pure-ASCII *new* files can be created with Write (ASCII is the same in every code
page). A new file that needs accents goes through `convert` (below).

If the plugin's guard hook is installed, Edit/Write on these files is blocked
with a message pointing here — that is expected; switch to the script, don't retry.

## Running it

Use the Bash tool with the launcher, which finds a working Python 3.7+ (it skips
Windows Store `python` stubs):

```bash
sh "${CLAUDE_SKILL_DIR}/scripts/cp1252" <command> ...
```

Below, `cp1252` stands for that whole prefix. Without Bash (PowerShell only), call
`py -3 "${CLAUDE_SKILL_DIR}/scripts/cp1252_edit.py" <command> ...` (or `python3`).
Output is always UTF-8, so accents print correctly through pipes.

Encoding defaults to cp1252; pass `--encoding latin-1|iso8859-15|cp1250|cp850…`
when the project uses another single-byte code page (or set `CP1252_EDIT_ENCODING`).

## Not sure what a file is?

```bash
cp1252 check path/to/File.prw            # or a directory; -q shows only problems
```

It reports the encoding (`ascii` / `cp1252` / `utf-8`), line endings, invisible
NBSPs, and damage: `U+FFFD` (→ `recover`), double-encoded `Ã§` (→ `convert
--unmojibake`), or UTF-8 in an extension expected to be legacy (→ `convert`).
Directory walks use `--ext` (default `.prw,.prx,.ch,.prg`, or
`CP1252_EDIT_EXTENSIONS`; `'*'` for all).

## Editing an existing file

**1. View the real text** (decoded, line-numbered) and copy `old` text from here,
never from Read:

```bash
cp1252 view path/to/File.prw --start 40 --end 90
```

The header shows encoding, line endings and NBSP count. If NBSPs (`0xA0`,
invisible "hard spaces") are present, or whitespace looks odd, add
`--show-special` (NBSP `⍽`, tab `→`, trailing space `·`, control bytes `<0xNN>`) —
and copy `old` text from a view *without* that flag.

**2. Apply the edit.** `old` must match exactly and **uniquely**, like the Edit
tool; line endings are normalized for matching, so don't think about CRLF.

- **Preferred — JSON edits on stdin** (multi-line, accents, several edits at once;
  each edit applies to the result of the previous one, all-or-nothing):

  ```bash
  cp1252 replace path/to/File.prw --edits - <<'EOF'
  [
    {"old": "cValor := 0", "new": "cValor := nTotal"},
    {"old": "// revisão pendente", "new": "// revisão concluída"},
    {"old": "cOld", "new": "cNew", "replace_all": true}
  ]
  EOF
  ```

  Quote the heredoc delimiter (`<<'EOF'`) so the shell leaves `$` and backticks
  alone. In **PowerShell**, don't pipe accented text into the script (Windows
  PowerShell 5.1 turns it into `?`): write the JSON to a file with Write and pass
  `--edits edits.json`.

- **Snippet files** — the exact text to find and its replacement in two UTF-8
  files (written with Write): `cp1252 replace File.prw --old-file old.txt --new-file new.txt`

- **ASCII-only one-off**: `cp1252 replace File.prw --old 'nQtd := 1' --new 'nQtd := 2'`

Options: `--replace-all` (instead of requiring uniqueness), `--dry-run` (print the
diff, write nothing), `--diff` (write and print the diff).

**3. Confirm** from the `OK:` line (edits, occurrences, line delta, endings
preserved), or `view` the range again. Don't re-Read with the normal tool.

When a match fails, the error carries hints: whitespace that differs only by
NBSP/tabs (with the line to `view --show-special`), a case-only difference, or
`old` text that contains `�`/mojibake because it was copied from a UTF-8 view.

## Searching

Grep can't match accented patterns in legacy files. Use:

```bash
cp1252 search "Atenção" path/to/dir            # literal, -i ignores case
cp1252 search -E "u_\w+\(.*descrição" src/     # regex
```

Output is `path:line: text`; UTF-8 files encountered are searched as UTF-8.

## Creating a new file with accents

Write the content as a UTF-8 draft with a non-legacy suffix (so the guard hook
allows it), then encode it:

```bash
cp1252 convert New.prw.utf8 path/to/New.prw      # CRLF by default; --newline lf|keep
rm New.prw.utf8
```

`convert` refuses to overwrite an existing destination unless `--force`.

## Repairing damaged files

Run `check` first; it tells you which case you're in.

- **`U+FFFD` damage** (the Edit/Write round-trip; `check` reports "accents destroyed")
  — the bytes are gone, so they come back from a good version. `recover` aligns the
  damaged file with the reference line by line, ignoring accents, and copies each
  lost character back. ASCII changes made since (yours or the user's) are kept:

  ```bash
  cp1252 recover path/to/File.prw                 # reference = git HEAD
  cp1252 recover path/to/File.prw --git HEAD~3    # an older commit
  cp1252 recover path/to/File.prw --ref backup/File.prw
  ```

  It writes in place and keeps the damaged copy as `File.prw.damaged` (delete it
  once satisfied; `--out` writes elsewhere). Lines that were edited since the
  reference are restored by fuzzy alignment and listed as **review** — check each
  one. Lines with no counterpart (new lines that had accents) make it stop without
  writing; `--allow-unresolved` writes `?` for those characters so you can fix
  them with `replace`. Accented text the agent *added* in UTF-8 is kept and
  encoded properly.

- **Re-saved as UTF-8** (accents intact, but stored as UTF-8 — `check` says
  "UTF-8, but this extension is expected to be cp1252"):
  `cp1252 convert File.prw File.prw --force --newline keep`

- **Double-encoded** (`Ã§`, `Ã£` visible in the text):
  `cp1252 convert File.prw File.prw --force --newline keep --unmojibake`, then
  `view` to confirm.

After any repair, `git diff` should show only the lines that were meant to change.

## Errors

- **"old string not found"** — copy from `view`, including indentation; read the hints.
- **"found N times (lines …)"** — add context, or use `replace_all`.
- **"cannot be encoded in cp1252"** — the new text has a character the code page
  lacks (emoji, arrows/box drawing, non-Latin script). cp1252 does have accents,
  `“” ‘’`, `–` `—`, `…`, `€`. Use an equivalent.
- **"is valid UTF-8 with multi-byte characters"** — the file is UTF-8, not legacy:
  edit it with the normal tools, or `check` it if it should be legacy.
- **"starts with a UTF-8 BOM"** — same: a UTF-8 file.
