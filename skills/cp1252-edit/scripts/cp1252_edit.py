#!/usr/bin/env python3
"""
cp1252_edit.py -- view, search, edit, check and repair Windows-1252 (or any
other single-byte code page) text files without corrupting them.

Why this exists
---------------
A lot of legacy source code (AdvPL/Protheus .prw/.prx/.ch, Clipper/Harbour
.prg, Delphi, VB6, old SQL scripts, ...) is stored as Windows-1252: every
accented character is ONE byte in 0x80-0xFF. Such a byte is invalid UTF-8, so
UTF-8 tooling -- including AI coding agents' built-in Read/Edit/Write tools --
misreads the file and, on save, rewrites the WHOLE file: every accent becomes
U+FFFD, even when the edit itself only touched ASCII. That loss is permanent.

This tool decodes/encodes with the legacy code page, reads and writes raw
bytes (no newline translation), and keeps each line's original terminator, so
everything it does not touch stays byte-for-byte identical. Text that YOU
author (edit JSON, snippet files, drafts) is read as UTF-8 and matching
happens in Unicode, so accents line up on both sides.

Commands
--------
  view     FILE [--start N] [--end M] [--show-special]
  search   PATTERN PATH... [--regex] [-i] [--ext LIST]
  replace  FILE (--edits FILE|- | --old-file F --new-file G | --old S --new T)
                [--replace-all] [--dry-run] [--diff]
  convert  SRC DST [--newline crlf|lf|keep] [--unmojibake] [--force]
  check    PATH... [--ext LIST] [--quiet]
  recover  FILE [--git REV | --ref FILE] [--out PATH] [--allow-unresolved]

Every command takes --encoding ENC (default: $CP1252_EDIT_ENCODING or cp1252);
any single-byte code page works (latin-1, iso8859-15, cp1250, cp850, ...).
Directory arguments are filtered by extension: --ext, else
$CP1252_EDIT_EXTENSIONS, else .prw,.prx,.ch,.prg.

Output is always UTF-8. Exit status: 0 success, 1 error / problems found /
no search match.
"""
import argparse
import codecs
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

__version__ = "2.0.0"

DEFAULT_ENCODING = os.environ.get("CP1252_EDIT_ENCODING") or "cp1252"
DEFAULT_EXTS = os.environ.get("CP1252_EDIT_EXTENSIONS") or ".prw,.prx,.ch,.prg"
SKIP_DIRS = {".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv"}
HANDLER = "cp1252edit-lossless"
FFFD = "�"
NBSP = " "
UTF8_BOM = b"\xef\xbb\xbf"

# Bytes the active code page leaves undefined (cp1252: 0x81 0x8D 0x8F 0x90
# 0x9D). They decode to the same-numbered C1 control -- exactly what Windows
# does -- and encode back, so ANY byte sequence round-trips losslessly.
_undefined = set()


def _lossless_decode(err):
    return "".join(chr(b) for b in err.object[err.start:err.end]), err.end


def _lossless_encode(err):
    chunk = err.object[err.start:err.end]
    if all(ord(c) in _undefined for c in chunk):
        return bytes(ord(c) for c in chunk), err.end
    raise err


codecs.register_error(HANDLER, lambda e: _lossless_decode(e)
                      if isinstance(e, UnicodeDecodeError) else _lossless_encode(e))


def _fail(msg):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(1)


def _setup_encoding(enc):
    try:
        codecs.lookup(enc)
    except LookupError:
        _fail("unknown encoding %r" % enc)
    _undefined.clear()
    for b in range(256):
        try:
            bytes([b]).decode(enc)
        except UnicodeDecodeError:
            _undefined.add(b)
    try:
        single = len(bytes(range(256)).decode(enc, HANDLER)) == 256
    except UnicodeDecodeError:
        single = False
    if not single:
        _fail("%s is not a single-byte code page; this tool is for legacy "
              "encodings like cp1252, latin-1, cp1250, cp850." % enc)


# ---------------------------------------------------------------- byte I/O --

def _read_bytes(path):
    try:
        with open(path, "rb") as fh:
            return fh.read()
    except OSError as e:
        _fail("cannot read %s: %s" % (path, e))


def _write_bytes(path, data):
    """Write atomically (temp file + rename) when possible, keeping file mode."""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".cp1252edit-")
    except OSError:
        fd = None
    if fd is not None:
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
            if os.path.exists(path):
                shutil.copymode(path, tmp)
            os.replace(tmp, path)
            return
        except OSError:
            # e.g. Windows refusing to replace a file another program holds open
            try:
                os.unlink(tmp)
            except OSError:
                pass
    try:
        with open(path, "wb") as fh:
            fh.write(data)
    except OSError as e:
        _fail("cannot write %s: %s" % (path, e))


def _decode(raw, enc):
    return raw.decode(enc, HANDLER)


def _encode(text, enc, label):
    try:
        return text.encode(enc, HANDLER)
    except UnicodeEncodeError as e:
        bad = text[e.start]
        line = text.count("\n", 0, e.start) + 1
        _fail("%s: character %r (U+%04X) on line %d cannot be encoded in %s. "
              "cp1252 covers Western European text (accents, curly quotes, en/em "
              "dash, euro sign); the usual culprit is an emoji, an arrow/box "
              "symbol or a non-Latin character. Replace it with an equivalent "
              "the code page has." % (label, bad, ord(bad), line, enc))


def _is_utf8(raw):
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _read_utf8(path):
    """Read text YOU authored (JSON, snippets, drafts); '-' means stdin."""
    if path == "-":
        raw = sys.stdin.buffer.read()
    else:
        raw = _read_bytes(path)
    try:
        return raw.decode("utf-8-sig").replace("\r\n", "\n")
    except UnicodeDecodeError as e:
        _fail("%s is not valid UTF-8 (byte 0x%02X at offset %d). Snippet/edit/draft "
              "files must be UTF-8 -- if it is a legacy file, don't use it as input "
              "here." % ("stdin" if path == "-" else path, raw[e.start], e.start))


def read_legacy(path, enc, assume_legacy=False):
    raw = _read_bytes(path)
    if raw.startswith(UTF8_BOM):
        _fail("%s starts with a UTF-8 BOM, so it is a UTF-8 file, not %s. Edit it "
              "with normal tools, or `convert` it if it should be %s." % (path, enc, enc))
    if not assume_legacy and not raw.isascii() and _is_utf8(raw):
        _fail("%s is valid UTF-8 with multi-byte characters, so it is NOT a %s "
              "file. If it is meant to be UTF-8, edit it with normal tools. If it "
              "is a %s file that something re-saved as UTF-8, run `check` on it "
              "and `convert` it back first. (--assume-legacy overrides this.)"
              % (path, enc, enc))
    text = _decode(raw, enc)
    damaged = _decode(b"\xef\xbf\xbd", enc)
    if damaged in text:
        print("WARNING: %s contains %r -- a U+FFFD left over from an earlier UTF-8 "
              "round-trip; accents there are already lost (see `recover`)."
              % (path, damaged), file=sys.stderr)
    return text


# ------------------------------------------------------------ line endings --

_EOL = re.compile(r"\r\n|\n")


def split_eol(text):
    """Split into (lines, eols). eols[i] is the terminator after lines[i]
    ('\\r\\n' or '\\n'); the last entry is always ''. A lone '\\r' stays content."""
    lines, eols, pos = [], [], 0
    for m in _EOL.finditer(text):
        lines.append(text[pos:m.start()])
        eols.append(m.group())
        pos = m.end()
    lines.append(text[pos:])
    eols.append("")
    return lines, eols


def _eol_stats(eols):
    crlf = sum(1 for e in eols if e == "\r\n")
    lf = sum(1 for e in eols if e == "\n")
    return crlf, lf


def _eol_label(eols):
    crlf, lf = _eol_stats(eols)
    if crlf and lf:
        return "MIXED (%d CRLF, %d LF)" % (crlf, lf)
    return "LF" if lf else "CRLF" if crlf else "no newline"


def rejoin(old_lines, old_eols, new_text):
    """Restore line terminators on edited text (which uses '\\n' only).

    Uniform files get their single style back. Mixed files keep each surviving
    line's own terminator; new lines get the majority style."""
    new_lines = new_text.split("\n")
    crlf, lf = _eol_stats(old_eols)
    major = "\n" if lf > crlf else "\r\n"
    if not (crlf and lf):
        return major.join(new_lines)
    new_eols = [major] * (len(new_lines) - 1) + [""]
    sm = difflib.SequenceMatcher(None, old_lines[:-1], new_lines[:-1], autojunk=False)
    for a, b, size in sm.get_matching_blocks():
        for k in range(size):
            new_eols[b + k] = old_eols[a + k]
    return "".join(line + eol for line, eol in zip(new_lines, new_eols))


# ------------------------------------------------------------------- files --

def _ext_set(spec):
    if spec.strip() == "*":
        return None
    return {("." + e.strip().lstrip(".")).lower() for e in spec.split(",") if e.strip()}


def iter_files(paths, ext_spec):
    """Explicit files always; directories walked, filtered by extension."""
    exts = _ext_set(ext_spec)
    for p in paths:
        if os.path.isdir(p):
            for root, dirs, files in os.walk(p):
                dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
                for f in sorted(files):
                    if exts is None or os.path.splitext(f)[1].lower() in exts:
                        yield os.path.join(root, f)
        elif os.path.exists(p):
            yield p
        else:
            print("WARNING: %s does not exist" % p, file=sys.stderr)


def _cont_chars(enc, lo, hi):
    return "".join(re.escape(c) for c in bytes(range(lo, hi)).decode(enc, HANDLER))


def _mojibake_rx(enc):
    """A UTF-8 lead byte followed by a continuation byte, both rendered in the
    legacy code page -- e.g. 'Ã§' for 'ç'. Real text almost never has these."""
    return re.compile("[%s][%s]" % (_cont_chars(enc, 0xC2, 0xF5), _cont_chars(enc, 0x80, 0xC0)))


# -------------------------------------------------------------------- view --

def _special(line):
    out = []
    for c in line:
        if c == NBSP:
            out.append("⍽")                       # ⍽
        elif c == "\t":
            out.append("→")                       # →
        elif c == "\r":
            out.append("<CR>")
        elif 0x80 <= ord(c) <= 0x9F or ord(c) < 0x20:
            out.append("<0x%02X>" % ord(c))
        else:
            out.append(c)
    s = "".join(out)
    stripped = s.rstrip(" ")
    return stripped + "·" * (len(s) - len(stripped))   # trailing spaces → ·


def cmd_view(a):
    text = read_legacy(a.file, a.encoding, a.assume_legacy)
    lines, eols = split_eol(text)
    if len(lines) > 1 and lines[-1] == "":
        lines.pop()                                   # trailing newline, not a line
    total = len(lines)
    start = max(1, a.start or 1)
    end = min(total, a.end or total)
    notes = [a.encoding, _eol_label(eols), "%d lines" % total]
    nbsp = text.count(NBSP)
    if nbsp:
        notes.append("%d NBSP 0xA0%s" % (nbsp, "" if a.show_special else
                                          " -- invisible, use --show-special"))
    print("# %s  (%s)" % (a.file, ", ".join(notes)))
    width = len(str(end))
    for i in range(start, end + 1):
        line = lines[i - 1]
        print("%*d\t%s" % (width, i, _special(line) if a.show_special else line))


# ------------------------------------------------------------------ search --

def cmd_search(a):
    flags = re.IGNORECASE if a.ignore_case else 0
    try:
        rx = re.compile(a.pattern if a.regex else re.escape(a.pattern), flags)
    except re.error as e:
        _fail("bad regex: %s" % e)
    hits = 0
    for path in iter_files(a.paths, a.ext):
        raw = _read_bytes(path)
        if raw.startswith(UTF8_BOM) or (not raw.isascii() and _is_utf8(raw)):
            text = raw.decode("utf-8-sig")            # UTF-8 files searched as such
        else:
            text = _decode(raw, a.encoding)
        for n, line in enumerate(split_eol(text)[0], 1):
            if rx.search(line):
                hits += 1
                print("%s:%d: %s" % (path, n, line))
    if not hits:
        sys.exit(1)


# ----------------------------------------------------------------- replace --

def _hint(text, old, enc):
    hints = []
    if FFFD in old or _mojibake_rx(enc).search(old):
        hints.append("the old text contains '�' or mojibake like 'Ã§': it "
                     "was copied from a UTF-8 rendering of the file. Copy it from "
                     "`view` output instead.")
    ws = re.compile(r"[ \t ]+")
    pieces = [re.escape(p) for p in ws.split(old)]
    m = re.search(r"[ \t ]+".join(pieces), text)
    if m:
        n = text.count("\n", 0, m.start()) + 1
        hints.append("it matches at line %d if spaces, tabs and NBSP (0xA0) are "
                     "treated alike -- the whitespace differs. Run `view --start %d "
                     "--show-special` to see it." % (n, n))
    else:
        m = re.search(re.escape(old), text, re.IGNORECASE)
        if m:
            hints.append("it matches ignoring case at line %d."
                         % (text.count("\n", 0, m.start()) + 1))
        else:
            first = old.split("\n", 1)[0].strip()
            if "\n" in old and first and first in text:
                n = text.count("\n", 0, text.index(first)) + 1
                hints.append("its first line occurs at line %d; the difference is "
                             "further down." % n)
    return "".join("\n  hint: " + h for h in hints)


def _apply(text, old, new, replace_all, label, enc):
    old = old.replace("\r\n", "\n")
    new = new.replace("\r\n", "\n")
    if old == "":
        _fail("%s: 'old' string is empty" % label)
    count = text.count(old)
    if count == 0:
        _fail("%s: old string not found. `view` the file and copy the exact text, "
              "indentation included.%s" % (label, _hint(text, old, enc)))
    if count > 1 and not replace_all:
        lines = []
        pos = text.find(old)
        while pos != -1 and len(lines) < 10:
            lines.append(str(text.count("\n", 0, pos) + 1))
            pos = text.find(old, pos + 1)
        _fail("%s: old string found %d times (lines %s); it must be unique. Add "
              "surrounding context, or use replace_all / --replace-all."
              % (label, count, ", ".join(lines)))
    return text.replace(old, new), count


def _load_edits(path):
    try:
        edits = json.loads(_read_utf8(path))
    except json.JSONDecodeError as e:
        _fail("invalid JSON in %s: %s" % ("stdin" if path == "-" else path, e))
    if isinstance(edits, dict) and "old" in edits:
        edits = [edits]
    if not isinstance(edits, list) or not edits:
        _fail("--edits must be a non-empty JSON array of {old, new, replace_all?}")
    for i, ed in enumerate(edits):
        if not isinstance(ed, dict) or not isinstance(ed.get("old"), str) \
                or not isinstance(ed.get("new"), str):
            _fail("edit #%d must be an object with string 'old' and 'new'" % i)
    return edits


def cmd_replace(a):
    original = read_legacy(a.file, a.encoding, a.assume_legacy)
    lines, eols = split_eol(original)
    before = "\n".join(lines)
    text = before

    if a.edits:
        steps = [(ed["old"], ed["new"], bool(ed.get("replace_all", False)), "edit #%d" % i)
                 for i, ed in enumerate(_load_edits(a.edits))]
    elif a.old_file is not None or a.new_file is not None:
        if a.old_file is None or a.new_file is None:
            _fail("--old-file and --new-file must be given together")
        steps = [(_read_utf8(a.old_file), _read_utf8(a.new_file), a.replace_all, "replace")]
    elif a.old is not None or a.new is not None:
        if a.old is None or a.new is None:
            _fail("--old and --new must be given together")
        steps = [(a.old, a.new, a.replace_all, "replace")]
    else:
        _fail("provide one of: --edits FILE|-, --old-file/--new-file, --old/--new")

    occurrences = 0
    for old, new, replace_all, label in steps:      # each edit sees the previous result
        text, n = _apply(text, old, new, replace_all, label, a.encoding)
        occurrences += n

    if a.diff or a.dry_run:
        sys.stdout.writelines(difflib.unified_diff(
            before.splitlines(True), text.splitlines(True),
            a.file + " (before)", a.file + " (after)", n=2))
        print()
    data = _encode(rejoin(lines, eols, text), a.encoding, a.file)
    if a.dry_run:
        print("DRY RUN: %d edit(s), %d occurrence(s); %s not written."
              % (len(steps), occurrences, a.file))
        return
    _write_bytes(a.file, data)
    delta = text.count("\n") - before.count("\n")
    print("OK: applied %d edit(s) (%d occurrence(s), %+d lines) to %s -- %s, %s preserved"
          % (len(steps), occurrences, delta, a.file, a.encoding, _eol_label(eols)))


# ----------------------------------------------------------------- convert --

def unmojibake(text, enc):
    """Undo UTF-8-read-as-legacy double encoding ('Ã§' -> 'ç'), run by run, so
    surrounding legitimate accents are left alone. Repeats for triple encoding."""
    run = re.compile("[%s]{2,}" % _cont_chars(enc, 0x80, 0x100))

    def fix(m):
        decoded = m.group().encode(enc, HANDLER).decode("utf-8", "surrogateescape")
        return "".join(bytes([ord(c) - 0xDC00]).decode(enc, HANDLER)
                       if 0xDC80 <= ord(c) <= 0xDCFF else c for c in decoded)

    for _ in range(3):
        fixed = run.sub(fix, text)
        if fixed == text:
            break
        text = fixed
    return text


def cmd_convert(a):
    if os.path.exists(a.dst) and not a.force:
        _fail("%s already exists; pass --force to overwrite it" % a.dst)
    raw = _read_bytes(a.src)
    if not _is_utf8(raw[3:] if raw.startswith(UTF8_BOM) else raw):
        _fail("%s is not valid UTF-8 -- it may already be %s (run `check` on it)."
              % (a.src, a.encoding))
    text = raw.decode("utf-8-sig")
    lines, eols = split_eol(text)
    if a.unmojibake:
        text = unmojibake(text, a.encoding)
        lines, eols = split_eol(text)
    if FFFD in text:
        _fail("%s contains %d U+FFFD replacement characters: the original accents "
              "are gone and converting cannot bring them back. Use `recover` with "
              "the last good version instead." % (a.src, text.count(FFFD)))
    if a.newline == "keep":
        out = text
    else:
        out = ("\r\n" if a.newline == "crlf" else "\n").join(lines)
    _write_bytes(a.dst, _encode(out, a.encoding, a.dst))
    print("OK: wrote %s as %s (%s)" % (a.dst, a.encoding,
                                       _eol_label(split_eol(out)[1])))


# ------------------------------------------------------------------- check --

def classify(path, raw, enc, legacy_exts):
    """Returns (kind, problems, notes)."""
    problems, notes = [], []
    has_bom = raw.startswith(UTF8_BOM)
    body = raw[3:] if has_bom else raw
    expected_legacy = legacy_exts is None or \
        os.path.splitext(path)[1].lower() in legacy_exts
    if raw.isascii():
        kind, text = "ascii", raw.decode("ascii")
    elif _is_utf8(body):
        kind, text = ("utf-8 with BOM" if has_bom else "utf-8"), body.decode("utf-8")
        bad = text.count(FFFD)
        moji = len(_mojibake_rx(enc).findall(text))
        if bad:
            problems.append("%d U+FFFD on %d line(s): accents destroyed by a UTF-8 "
                            "round-trip -> `recover`" % (bad, sum(
                                1 for line in text.split("\n") if FFFD in line)))
        if moji:
            problems.append("%d double-encoded sequence(s) like 'Ã§' -> "
                            "`convert --unmojibake`" % moji)
        if not bad and not moji and expected_legacy:
            problems.append("UTF-8, but this extension is expected to be %s -> "
                            "`convert` it back" % enc)
    else:
        kind, text = enc, _decode(body, enc)
        damaged = _decode(b"\xef\xbf\xbd", enc)
        if damaged in text:
            problems.append("%d '%s' (U+FFFD re-saved as %s): accents lost in an "
                            "earlier round-trip -> `recover`"
                            % (text.count(damaged), damaged, enc))
    lines, eols = split_eol(text)
    crlf, lf = _eol_stats(eols)
    if crlf and lf:
        notes.append("mixed line endings (%d CRLF, %d LF)" % (crlf, lf))
    if text.count(NBSP):
        notes.append("%d NBSP (0xA0)" % text.count(NBSP))
    return kind, _eol_label(eols) if not (crlf and lf) else "MIXED", problems, notes


def cmd_check(a):
    legacy_exts = _ext_set(a.ext)
    bad = 0
    total = 0
    for path in iter_files(a.paths, a.ext):
        total += 1
        kind, eol, problems, notes = classify(path, _read_bytes(path), a.encoding,
                                              legacy_exts)
        if problems:
            bad += 1
        if problems or not a.quiet:
            line = "%s: %s, %s" % (path, kind, eol)
            if notes:
                line += "; " + "; ".join(notes)
            print(line + "".join("\n  PROBLEM: " + p for p in problems))
    print("checked %d file(s): %d with problems" % (total, bad))
    if bad:
        sys.exit(1)


# ----------------------------------------------------------------- recover --

_NON_ASCII = re.compile(r"[^\x00-\x7f]")


def _git_show(path, rev):
    folder = os.path.dirname(os.path.abspath(path))
    try:
        r = subprocess.run(["git", "-C", folder, "show",
                            "%s:./%s" % (rev, os.path.basename(path))],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as e:
        _fail("cannot run git: %s (use --ref FILE instead)" % e)
    if r.returncode:
        _fail("git show %s failed: %s" % (rev, r.stderr.decode("utf-8", "replace").strip()))
    return r.stdout


def _restore_line(ref, cur):
    """Fill each U+FFFD in `cur` with the accent at the aligned spot of `ref`.
    Returns (line, filled, exact)."""
    if _NON_ASCII.sub("", ref) == cur.replace(FFFD, "") and \
            len(_NON_ASCII.findall(ref)) == cur.count(FFFD) and \
            not _NON_ASCII.search(cur.replace(FFFD, "")):
        accents = iter(_NON_ASCII.findall(ref))
        return "".join(next(accents) if c == FFFD else c for c in cur), cur.count(FFFD), True
    a = _NON_ASCII.sub("\0", ref)
    b = cur.replace(FFFD, "\0")
    out = list(cur)
    filled = 0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for i, j, size in sm.get_matching_blocks():
        for k in range(size):
            if cur[j + k] == FFFD:
                out[j + k] = ref[i + k]
                filled += 1
    return "".join(out), filled, False


def restore(cur, ref):
    cur_lines, cur_eols = split_eol(cur)
    ref_lines = split_eol(ref)[0]

    def skel(s):
        return _NON_ASCII.sub("", s)

    sm = difflib.SequenceMatcher(None, [skel(x) for x in ref_lines],
                                 [skel(x) for x in cur_lines], autojunk=False)
    pairs = {}
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(j2 - j1):
                pairs[j1 + k] = i1 + k
        elif tag == "replace":
            for j in range(j1, j2):
                if FFFD not in cur_lines[j]:
                    continue
                best, score = None, 0.6
                for i in range(i1, i2):
                    r = difflib.SequenceMatcher(None, ref_lines[i], cur_lines[j],
                                                autojunk=False)
                    if r.quick_ratio() > score and r.ratio() > score:
                        best, score = i, r.ratio()
                if best is not None:
                    pairs[j] = best
    filled, review, unresolved = 0, [], []
    for j, line in enumerate(cur_lines):
        if FFFD not in line:
            continue
        if j in pairs:
            line, n, exact = _restore_line(ref_lines[pairs[j]], line)
            filled += n
            if not exact:
                review.append((j + 1, pairs[j] + 1, line))
        if FFFD in line:
            unresolved.append(j + 1)
        cur_lines[j] = line
    text = "".join(x + e for x, e in zip(cur_lines, cur_eols))
    return text, filled, review, unresolved


def cmd_recover(a):
    raw = _read_bytes(a.file)
    body = raw[3:] if raw.startswith(UTF8_BOM) else raw
    if _is_utf8(body):
        cur = body.decode("utf-8")
    else:
        # A legacy file holding 'ï¿½': the U+FFFD itself was saved back as bytes.
        cur = _decode(body, a.encoding).replace(_decode(b"\xef\xbf\xbd", a.encoding), FFFD)
    if FFFD not in cur:
        print("nothing to recover: %s has no U+FFFD replacement characters" % a.file)
        return
    if a.ref:
        ref_raw, source = _read_bytes(a.ref), a.ref
    else:
        ref_raw, source = _git_show(a.file, a.git), "git %s" % a.git
    ref = _decode(ref_raw, a.encoding)
    if FFFD in ref or _decode(b"\xef\xbf\xbd", a.encoding) in ref:
        _fail("the reference (%s) is damaged too; pick an older version" % source)

    text, filled, review, unresolved = restore(cur, ref)
    print("recover %s from %s: %d accent(s) restored, %d line(s) need review, %d "
          "unresolved" % (a.file, source, filled, len(review), len(unresolved)))
    for cur_no, ref_no, line in review:
        print("  review line %d (matched reference line %d, which was edited "
              "since): %s" % (cur_no, ref_no, line.strip()))
    if unresolved:
        if not a.allow_unresolved:
            for n in unresolved[:20]:
                print("  unresolved line %d: %s" % (n, split_eol(text)[0][n - 1].strip()))
            _fail("%d line(s) have no matching line in the reference; nothing "
                  "written. Fix them by hand afterwards with --allow-unresolved "
                  "(writes '?' for each lost character), or try an older reference."
                  % len(unresolved))
        text = text.replace(FFFD, "?")
    data = _encode(text, a.encoding, a.file)
    out = a.out or a.file
    if out == a.file and not a.no_backup:
        backup = a.file + ".damaged"
        shutil.copyfile(a.file, backup)
        print("  damaged original kept as %s" % backup)
    _write_bytes(out, data)
    print("OK: wrote %s (%s, %s)" % (out, a.encoding, _eol_label(split_eol(text)[1])))


# -------------------------------------------------------------------- main --

def main():
    if not os.environ.get("PYTHONIOENCODING"):
        for stream in (sys.stdout, sys.stderr):
            try:                    # pipes default to the ANSI code page on Windows
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, ValueError):
                pass

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--encoding", default=DEFAULT_ENCODING,
                        help="legacy code page (default: %(default)s)")
    legacy = argparse.ArgumentParser(add_help=False)
    legacy.add_argument("--assume-legacy", action="store_true",
                        help="treat the file as legacy even if it is valid UTF-8")

    p = argparse.ArgumentParser(description="Safe view/search/edit/repair of "
                                "Windows-1252 and other single-byte text files.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")
    sub.required = True

    v = sub.add_parser("view", parents=[common, legacy],
                       help="print a legacy file with line numbers, decoded")
    v.add_argument("file")
    v.add_argument("--start", type=int, help="first line (1-based)")
    v.add_argument("--end", type=int, help="last line (inclusive)")
    v.add_argument("--show-special", action="store_true",
                   help="mark NBSP as ⍽, tabs as →, trailing spaces as "
                        "·, control bytes as <0xNN>")
    v.set_defaults(func=cmd_view)

    s = sub.add_parser("search", parents=[common],
                       help="grep files/dirs, matching decoded text (accents work)")
    s.add_argument("pattern")
    s.add_argument("paths", nargs="+")
    s.add_argument("--regex", "-E", action="store_true", help="pattern is a regex")
    s.add_argument("--ignore-case", "-i", action="store_true")
    s.add_argument("--ext", default=DEFAULT_EXTS,
                   help="extensions for directory walks, or '*' (default: %(default)s)")
    s.set_defaults(func=cmd_search)

    r = sub.add_parser("replace", parents=[common, legacy],
                       help="exact find/replace; encoding and line endings preserved")
    r.add_argument("file")
    r.add_argument("--edits", help="UTF-8 JSON array of {old, new, replace_all?}; "
                                   "'-' reads stdin")
    r.add_argument("--old-file", help="UTF-8 file with the exact text to find")
    r.add_argument("--new-file", help="UTF-8 file with the replacement")
    r.add_argument("--old", help="inline text to find (keep to ASCII)")
    r.add_argument("--new", help="inline replacement (keep to ASCII)")
    r.add_argument("--replace-all", action="store_true",
                   help="replace every occurrence instead of requiring a unique match")
    r.add_argument("--dry-run", action="store_true", help="show the diff, write nothing")
    r.add_argument("--diff", action="store_true", help="also print the diff")
    r.set_defaults(func=cmd_replace)

    c = sub.add_parser("convert", parents=[common],
                       help="encode a UTF-8 file (draft, or re-saved legacy file) "
                            "into the legacy code page")
    c.add_argument("src", help="UTF-8 source")
    c.add_argument("dst", help="legacy file to write (may equal src with --force)")
    c.add_argument("--newline", choices=["crlf", "lf", "keep"], default="crlf")
    c.add_argument("--unmojibake", action="store_true",
                   help="also undo double encoding ('Ã§' -> 'ç')")
    c.add_argument("--force", action="store_true", help="overwrite dst")
    c.set_defaults(func=cmd_convert)

    k = sub.add_parser("check", parents=[common],
                       help="report encoding, line endings and damage of files/dirs")
    k.add_argument("paths", nargs="+")
    k.add_argument("--ext", default=DEFAULT_EXTS,
                   help="extensions expected to be legacy-encoded, also used for "
                        "directory walks; '*' for all (default: %(default)s)")
    k.add_argument("--quiet", "-q", action="store_true", help="only print problems")
    k.set_defaults(func=cmd_check)

    rc = sub.add_parser("recover", parents=[common],
                        help="restore accents a UTF-8 round-trip replaced with "
                             "U+FFFD, using a good earlier version")
    rc.add_argument("file")
    src = rc.add_mutually_exclusive_group()
    src.add_argument("--git", default="HEAD", metavar="REV",
                     help="take the reference from git (default: HEAD)")
    src.add_argument("--ref", metavar="FILE", help="reference copy of the file")
    rc.add_argument("--out", help="write here instead of in place")
    rc.add_argument("--no-backup", action="store_true",
                    help="in-place: don't keep FILE.damaged")
    rc.add_argument("--allow-unresolved", action="store_true",
                    help="write '?' for characters with no reference")
    rc.set_defaults(func=cmd_recover)

    a = p.parse_args()
    _setup_encoding(a.encoding)
    a.func(a)


if __name__ == "__main__":
    main()
