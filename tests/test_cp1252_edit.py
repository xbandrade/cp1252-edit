"""Run with:  python -m unittest discover -s tests -v   (Python 3.7+, stdlib only)"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skills", "cp1252-edit", "scripts", "cp1252_edit.py")
GUARD = os.path.join(ROOT, "hooks", "guard_encoding.py")

SAMPLE = ("User Function Teste()\r\n"
          "    Local cMsg := \"Atenção: operação concluída\"\r\n"
          "    // revisão pendente\r\n"
          "    MsgInfo(cMsg, \"Aviso\")\r\n"
          "Return\r\n")


def _env():
    env = dict(os.environ)
    for k in ("PYTHONIOENCODING", "PYTHONUTF8", "CP1252_EDIT_ENCODING",
              "CP1252_EDIT_EXTENSIONS", "CP1252_EDIT_GUARD"):
        env.pop(k, None)
    return env


def run(*args, stdin=None):
    r = subprocess.run([sys.executable, SCRIPT] + list(args), input=stdin,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env())
    return r.returncode, r.stdout.decode("utf-8"), r.stderr.decode("utf-8")


def damage(raw):
    """What a UTF-8 editor does to a cp1252 file: each invalid byte -> U+FFFD."""
    return raw.decode("utf-8", "replace").encode("utf-8")


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        def writable(func, path, _):               # git objects are read-only on Windows
            os.chmod(path, 0o700)
            func(path)
        shutil.rmtree(self.dir, onerror=writable)

    def file(self, name, data):
        p = os.path.join(self.dir, name)
        with open(p, "wb") as fh:
            fh.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        return p

    def bytes_of(self, p):
        with open(p, "rb") as fh:
            return fh.read()


class ViewSearch(Base):
    def test_view_prints_real_accents_as_utf8(self):
        p = self.file("A.prw", SAMPLE.encode("cp1252"))
        rc, out, _ = run("view", p)
        self.assertEqual(rc, 0)
        self.assertIn("Atenção: operação concluída", out)
        self.assertIn("CRLF", out)
        self.assertIn("5 lines", out)

    def test_view_show_special_marks_nbsp(self):
        p = self.file("A.prw", "x :=\xa0 1\t\r\n".encode("cp1252"))
        rc, out, _ = run("view", p, "--show-special")
        self.assertIn("x :=⍽ 1→", out)
        self.assertIn("1 NBSP", out)

    def test_undefined_bytes_round_trip(self):
        raw = b"a\x81b\x8d\x8f\x90\x9dc \xe7\r\n"
        p = self.file("A.prw", raw)
        rc, _, err = run("replace", p, "--old", "a", "--new", "z")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.bytes_of(p), b"z" + raw[1:])

    def test_search_accented(self):
        self.file("A.prw", SAMPLE.encode("cp1252"))
        self.file("B.txt", "operação".encode("cp1252"))          # filtered by --ext
        rc, out, _ = run("search", "operação", self.dir)
        self.assertEqual(rc, 0)
        self.assertIn("A.prw:2:", out)
        self.assertNotIn("B.txt", out)
        rc, _, _ = run("search", "nada disso", self.dir)
        self.assertEqual(rc, 1)

    def test_refuses_utf8_file(self):
        p = self.file("U.prw", "operação".encode("utf-8"))
        rc, _, err = run("view", p)
        self.assertEqual(rc, 1)
        self.assertIn("valid UTF-8", err)


class Replace(Base):
    def test_accented_json_edit_via_stdin(self):
        p = self.file("A.prw", SAMPLE.encode("cp1252"))
        edits = json.dumps([{"old": "// revisão pendente", "new": "// revisão feita\n    // ação"}],
                           ensure_ascii=False).encode("utf-8")
        rc, out, err = run("replace", p, "--edits", "-", stdin=edits)
        self.assertEqual(rc, 0, err)
        expected = SAMPLE.replace("// revisão pendente", "// revisão feita\r\n    // ação")
        self.assertEqual(self.bytes_of(p), expected.encode("cp1252"))
        self.assertIn("+1 lines", out)

    def test_only_target_bytes_change(self):
        raw = SAMPLE.encode("cp1252")
        p = self.file("A.prw", raw)
        run("replace", p, "--old", "Aviso", "--new", "Alerta")
        self.assertEqual(self.bytes_of(p), raw.replace(b"Aviso", b"Alerta"))

    def test_mixed_line_endings_preserved(self):
        raw = "a1\r\nb2\nc3 é\r\nd4\n".encode("cp1252")
        p = self.file("A.prw", raw)
        rc, _, err = run("replace", p, "--old", "c3", "--new", "c3\nNEW")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.bytes_of(p), "a1\r\nb2\nc3\r\nNEW é\r\nd4\n".encode("cp1252"))

    def test_lf_file_stays_lf(self):
        p = self.file("A.prw", "x\nção\n".encode("cp1252"))
        run("replace", p, "--old", "x", "--new", "x\ny")
        self.assertEqual(self.bytes_of(p), "x\ny\nção\n".encode("cp1252"))

    def test_not_unique_and_replace_all(self):
        p = self.file("A.prw", "a\r\na\r\n".encode("cp1252"))
        rc, _, err = run("replace", p, "--old", "a", "--new", "b")
        self.assertEqual(rc, 1)
        self.assertIn("lines 1, 2", err)
        rc, _, _ = run("replace", p, "--old", "a", "--new", "b", "--replace-all")
        self.assertEqual(self.bytes_of(p), b"b\r\nb\r\n")

    def test_failed_batch_writes_nothing(self):
        raw = SAMPLE.encode("cp1252")
        p = self.file("A.prw", raw)
        edits = json.dumps([{"old": "Aviso", "new": "X"}, {"old": "missing", "new": "Y"}])
        rc, _, _ = run("replace", p, "--edits", "-", stdin=edits.encode())
        self.assertEqual(rc, 1)
        self.assertEqual(self.bytes_of(p), raw)

    def test_nbsp_hint(self):
        p = self.file("A.prw", "Local x :=\xa0\xa01\r\n".encode("cp1252"))
        rc, _, err = run("replace", p, "--old", "x :=  1", "--new", "x := 2")
        self.assertEqual(rc, 1)
        self.assertIn("NBSP", err)
        self.assertIn("line 1", err)

    def test_mojibake_hint(self):
        p = self.file("A.prw", SAMPLE.encode("cp1252"))
        rc, _, err = run("replace", p, "--old", "Aten��o", "--new", "x")
        self.assertIn("copied from a UTF-8 rendering", err)

    def test_unencodable_char(self):
        p = self.file("A.prw", SAMPLE.encode("cp1252"))
        edits = json.dumps([{"old": "Aviso", "new": "Aviso \U0001F600"}])
        rc, _, err = run("replace", p, "--edits", "-", stdin=edits.encode())
        self.assertEqual(rc, 1)
        self.assertIn("cannot be encoded", err)
        self.assertEqual(self.bytes_of(p), SAMPLE.encode("cp1252"))

    def test_dry_run(self):
        raw = SAMPLE.encode("cp1252")
        p = self.file("A.prw", raw)
        rc, out, _ = run("replace", p, "--old", "Aviso", "--new", "Alerta", "--dry-run")
        self.assertEqual(rc, 0)
        self.assertIn('+    MsgInfo(cMsg, "Alerta")', out)
        self.assertEqual(self.bytes_of(p), raw)

    def test_other_encoding(self):
        p = self.file("A.txt", "Zażółć gęślą".encode("cp1250"))
        rc, _, err = run("replace", p, "--encoding", "cp1250", "--old", "Zażółć", "--new", "Źdźbło")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.bytes_of(p), "Źdźbło gęślą".encode("cp1250"))


class ConvertCheck(Base):
    def test_convert_draft(self):
        src = self.file("N.prw.utf8", "a\nção\n")
        dst = os.path.join(self.dir, "N.prw")
        rc, _, err = run("convert", src, dst)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.bytes_of(dst), "a\r\nção\r\n".encode("cp1252"))
        rc, _, err = run("convert", src, dst)
        self.assertEqual(rc, 1)
        self.assertIn("--force", err)

    def test_convert_in_place_unmojibake(self):
        p = self.file("M.prw", "x := 'operaÃ§Ã£o atenção'\r\n")   # one mojibake run, one fine
        rc, _, err = run("convert", p, p, "--force", "--newline", "keep", "--unmojibake")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.bytes_of(p), "x := 'operação atenção'\r\n".encode("cp1252"))

    def test_check_classifies(self):
        self.file("ok.prw", SAMPLE.encode("cp1252"))
        self.file("ascii.prw", b"Return\r\n")
        self.file("broken.prw", damage(SAMPLE.encode("cp1252")))
        self.file("resaved.prw", SAMPLE.encode("utf-8"))
        self.file("moji.prw", "operaÃ§Ã£o\r\n")
        self.file("fine.md", "operação\n")                          # not a legacy ext
        rc, out, _ = run("check", self.dir, "--ext", ".prw,.md", "-q")
        self.assertEqual(rc, 1)
        self.assertIn("broken.prw", out)
        self.assertIn("recover", out)
        self.assertIn("resaved.prw", out)
        self.assertIn("moji.prw", out)
        self.assertIn("unmojibake", out)
        self.assertNotIn("ok.prw", out)
        rc, out, _ = run("check", os.path.join(self.dir, "ok.prw"))
        self.assertEqual(rc, 0)
        self.assertIn("cp1252, CRLF", out)


class Recover(Base):
    ORIGINAL = ("// Função de teste\r\n"
                "User Function X()\r\n"
                "  cMsg := \"Não há títulos\"\r\n"
                "  cA := \"á\"\r\n"
                "  cB := \"é\"\r\n"
                "Return\r\n")

    def test_recover_keeps_ascii_edits(self):
        ref = self.file("ref.prw", self.ORIGINAL.encode("cp1252"))
        bad = damage(self.ORIGINAL.encode("cp1252")).decode("utf-8")
        # after the damage, an agent kept editing: ASCII change on an accented line,
        # a new ASCII line, and a new line with a real accent (written as UTF-8)
        bad = bad.replace("cMsg :=", "cMensagem :=").replace(
            "Return", "  nX := 1\r\n  cC := \"ção nova\"\r\nReturn")
        p = self.file("X.prw", bad.encode("utf-8"))
        rc, out, err = run("recover", p, "--ref", ref)
        self.assertEqual(rc, 0, err + out)
        expected = self.ORIGINAL.replace("cMsg :=", "cMensagem :=").replace(
            "Return", "  nX := 1\r\n  cC := \"ção nova\"\r\nReturn")
        self.assertEqual(self.bytes_of(p), expected.encode("cp1252"))
        self.assertTrue(os.path.exists(p + ".damaged"))
        self.assertIn("review line 3", out)

    def test_recover_unresolved_stops(self):
        ref = self.file("ref.prw", b"a\r\n")
        p = self.file("X.prw", "a\r\nnova � linha\r\n".encode("utf-8"))
        rc, out, err = run("recover", p, "--ref", ref)
        self.assertEqual(rc, 1)
        self.assertIn("unresolved line 2", out)
        rc, out, err = run("recover", p, "--ref", ref, "--allow-unresolved", "--no-backup")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.bytes_of(p), b"a\r\nnova ? linha\r\n")

    def test_recover_from_git(self):
        if not shutil.which("git"):
            self.skipTest("git not installed")
        g = ["git", "-C", self.dir, "-c", "user.name=t", "-c", "user.email=t@t",
             "-c", "core.autocrlf=false"]
        subprocess.run(g[:3] + ["init", "-q"], check=True)
        p = self.file("G.prw", self.ORIGINAL.encode("cp1252"))
        subprocess.run(g + ["add", "G.prw"], check=True)
        subprocess.run(g + ["commit", "-qm", "x"], check=True)
        with open(p, "wb") as fh:
            fh.write(damage(self.ORIGINAL.encode("cp1252")))
        rc, out, err = run("recover", p, "--no-backup")
        self.assertEqual(rc, 0, err + out)
        self.assertEqual(self.bytes_of(p), self.ORIGINAL.encode("cp1252"))


class Guard(Base):
    def guard(self, tool, path, **inp):
        inp["file_path"] = path
        event = json.dumps({"tool_name": tool, "tool_input": inp, "cwd": self.dir})
        r = subprocess.run([sys.executable, GUARD], input=event.encode("utf-8"),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env())
        return r.returncode, r.stderr.decode("utf-8")

    def test_blocks_edit_on_cp1252_any_extension(self):
        p = self.file("legacy.txt", SAMPLE.encode("cp1252"))
        rc, err = self.guard("Edit", p, old_string="a", new_string="b")
        self.assertEqual(rc, 2)
        self.assertIn("cp1252-edit", err)

    def test_allows_utf8_and_ascii(self):
        p = self.file("u.prw", "x\n")
        self.assertEqual(self.guard("Edit", p, old_string="x", new_string="y")[0], 0)
        p = self.file("u.md", "operação\n".encode("utf-8"))
        self.assertEqual(self.guard("Write", p, content="ção")[0], 0)

    def test_blocks_accents_into_ascii_legacy_ext(self):
        p = self.file("a.PRW", "x\r\n")
        self.assertEqual(self.guard("Edit", p, old_string="x", new_string="ç")[0], 2)

    def test_new_files(self):
        new = os.path.join(self.dir, "New.prw")
        self.assertEqual(self.guard("Write", new, content="ação")[0], 2)
        self.assertEqual(self.guard("Write", new, content="Return")[0], 0)
        self.assertEqual(self.guard("Write", new + ".utf8", content="ação")[0], 0)

    def test_ignores_other_tools_and_garbage(self):
        p = self.file("legacy.prw", SAMPLE.encode("cp1252"))
        self.assertEqual(self.guard("Read", p)[0], 0)
        r = subprocess.run([sys.executable, GUARD], input=b"not json",
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
