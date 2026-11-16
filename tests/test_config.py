from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from conchshell import cli, config
from conchshell.errors import ConchError


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        env = patch.dict(os.environ, {"XDG_CONFIG_HOME": self.directory.name}, clear=True)
        env.start()
        self.addCleanup(env.stop)

    def test_provider_override_cannot_inherit_other_provider_endpoint(self):
        config.save_config({"provider": "openai", "model": "private-model", "base_url": "https://old.example/v1"})
        with patch.dict(os.environ, {"CONCHSHELL_PROVIDER": "anthropic"}):
            self.assertEqual(config.effective_config(), {"provider": "anthropic"})
        self.assertEqual(config.load_config()["provider"], "openai")
        with patch.dict(os.environ, {"CONCHSHELL_PROVIDER": "anthropic", "CONCHSHELL_MODEL": "explicit"}):
            self.assertEqual(config.effective_config(), {"provider": "anthropic", "model": "explicit"})

    def test_credentials_stay_private_on_create_and_replace(self):
        for secret in ("first-private-key", "replacement-private-key"):
            config.save_credentials({"openai": secret})
            directory = config.config_dir()
            self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual((directory / "credentials.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(config.load_credentials(), {"openai": secret})
            self.assertEqual(config.load_config(), {})

    def test_malformed_credentials_fail_without_echoing_secrets(self):
        directory = config.config_dir()
        directory.mkdir()
        (directory / "credentials.json").write_text('{"secret": "do-not-echo"', encoding="utf-8")
        with self.assertRaises(ConchError) as error:
            config.load_credentials()
        self.assertNotIn("do-not-echo", str(error.exception))

    def test_symlinked_config_directory_does_not_receive_credentials(self):
        destination = Path(self.directory.name) / "elsewhere"
        destination.mkdir()
        config.config_dir().symlink_to(destination, target_is_directory=True)
        with self.assertRaises(ConchError):
            config.save_credentials({"openai": "private"})
        self.assertFalse((destination / "credentials.json").exists())

    def test_model_selection_preserves_provider_endpoint_and_credentials_under_overrides(self):
        saved = {"provider": "openai", "model": "old", "base_url": "https://private.example/v1"}
        config.save_config(saved)
        config.save_credentials({"openai": "private-key"})
        credentials = (config.config_dir() / "credentials.json").read_bytes()
        with patch.dict(os.environ, {"CONCHSHELL_PROVIDER": "anthropic", "CONCHSHELL_MODEL": "temporary"}):
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                result = cli.main(["model", " chosen-model "])
            self.assertEqual(result, 0)
            self.assertEqual(config.effective_config(), {"provider": "anthropic", "model": "temporary"})
        self.assertEqual(config.load_config(), {**saved, "model": "chosen-model"})
        self.assertEqual((config.config_dir() / "credentials.json").read_bytes(), credentials)

    def test_empty_or_cancelled_model_prompt_does_not_change_saved_selection(self):
        config.save_config({"provider": "codex", "model": "chosen-model"})
        path = config.config_dir() / "config.json"
        original = path.read_bytes()
        for response, expected_status in (("", 0), (KeyboardInterrupt(), 130)):
            with self.subTest(response=response), patch.object(cli.sys.stdin, "isatty", return_value=True), \
                    patch("builtins.input", side_effect=[response]), \
                    redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(["model"]), expected_status)
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
