import contextlib
import io
import os
import tempfile
import tomllib
import unittest
from pathlib import Path

from agent_bell import cli


class CodexSetupTests(unittest.TestCase):
    def run_setup(self, source: str, *, force: bool = False):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        os.environ["CODEX_HOME"] = directory.name
        path = Path(directory.name) / "config.toml"
        path.write_text(source)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = cli.codex_setup({}, Path(directory.name) / "agent-bell.yaml",
                                   force=force, start=False)
        return code, path, tomllib.loads(path.read_text())

    def test_force_replaces_foreign_notify_without_duplicate_key(self):
        code, path, parsed = self.run_setup('notify = ["other-hook"]\n[features]\nenabled = true\n', force=True)
        self.assertEqual(code, 0)
        self.assertIn("notify", parsed)
        self.assertTrue(parsed["features"]["enabled"])
        self.assertTrue(path.with_name("config.toml.agent-bell.bak").exists())

    def test_multiline_agent_notify_is_replaced_as_one_assignment(self):
        code, _, parsed = self.run_setup('notify = ["abll",\n "codex-hook"]\n[features]\nenabled = true\n')
        self.assertEqual(code, 0)
        self.assertEqual(parsed["notify"][1], "codex-hook")
        self.assertTrue(parsed["features"]["enabled"])

    def test_table_and_multiline_string_are_not_corrupted(self):
        source = 'instructions = """\n[example]\nkeep this\n"""\n[features]\nenabled = true\n'
        code, _, parsed = self.run_setup(source)
        self.assertEqual(code, 0)
        self.assertEqual(parsed["instructions"], "[example]\nkeep this\n")
        self.assertIn("notify", parsed)


if __name__ == "__main__":
    unittest.main()
