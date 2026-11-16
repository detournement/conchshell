"""Separate, private settings and credentials; never read Conch's configuration."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .errors import ConchError

PROVIDERS = ("openrouter", "openai", "anthropic", "ollama", "codex", "claude-code")


def config_dir() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "conchshell"


def _load(name: str) -> dict:
    path = config_dir() / name
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, UnicodeError) as exc:
        raise ConchError(f"Cannot read {path}; expected a JSON object.") from exc
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in value.items()
    ):
        raise ConchError(f"Invalid {path}; expected string settings.")
    return value


def _save(name: str, value: dict) -> None:
    directory = config_dir()
    temporary = None
    try:
        if directory.is_symlink():
            raise ConchError("Refusing to write credentials through a symlinked conchshell directory.")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory.chmod(0o700)
        fd, temporary = tempfile.mkstemp(prefix=".save-", dir=directory)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, indent=2, ensure_ascii=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / name)
    except OSError as exc:
        raise ConchError(f"Cannot save settings in {directory}.") from exc
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def load_config() -> dict:
    return _load("config.json")


def save_config(value: dict) -> None:
    _save("config.json", value)


def effective_config() -> dict:
    value = load_config()
    saved_provider = value.get("provider", "openrouter")
    for key in ("provider", "model", "base_url"):
        override = os.environ.get("CONCHSHELL_" + key.upper())
        if override is not None:
            value[key] = override
    provider = value.get("provider", "openrouter")
    if provider not in PROVIDERS:
        raise ConchError("Unknown provider; use conchshell config --help.")
    # A provider override must not inherit another provider's model or endpoint.
    if provider != saved_provider:
        for key in ("model", "base_url"):
            if "CONCHSHELL_" + key.upper() not in os.environ:
                value.pop(key, None)
    value["provider"] = provider
    return value


def load_credentials() -> dict:
    return _load("credentials.json")


def save_credentials(value: dict) -> None:
    _save("credentials.json", value)
