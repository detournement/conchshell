"""One request in, one answer out. Generated commands are never executed."""

from __future__ import annotations

import argparse
import getpass
import importlib.resources
import json
import math
import os
import platform
import sys
import unicodedata

from . import __version__
from .config import (
    PROVIDERS, config_dir, effective_config, load_config, load_credentials,
    save_config, save_credentials,
)
from .errors import ConchError


ANSWER_SYSTEM = (
    "You are a concise shell helper, not an agent. Answer the user's question "
    "directly in a few sentences. For shell tasks give the command and a brief "
    "explanation. No tools are available and you cannot execute anything. "
    "Do not claim to have inspected files or run commands. Do not emit terminal "
    "escape sequences. Mention destructive effects when relevant."
)
COMMAND_SYSTEM = (
    "You are a shell command assistant, not an agent. Return exactly one "
    "single-line shell command implementing the user's request. No markdown, "
    "commentary, terminal escape sequences, or extra lines. No tools are "
    "available. Do not execute anything or claim to know the filesystem. "
    "Do not add destructive operations that the user did not request. "
    "If a safe command cannot be determined, return an empty string."
)


def _defaults() -> dict:
    from . import native, providers
    return {**providers.DEFAULT_MODELS, **native.DEFAULT_MODELS}


def _positive_timeout(value: str) -> float:
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("timeout must be a positive finite number")
    return result


def _model_id(value: str) -> str:
    value = value.strip()
    if not value:
        raise argparse.ArgumentTypeError("model must not be empty")
    return value


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="conchshell", description="Shell commands and short answers. Never automatic execution.",
        allow_abbrev=False,
    )
    root.add_argument("--version", action="version", version=f"conchshell {__version__}")
    commands = root.add_subparsers(dest="action")
    for name, description in (("ask", "Ask for a short answer."),
                              ("complete", "Suggest one command to review, not execute.")):
        child = commands.add_parser(name, help=description, description=description, allow_abbrev=False)
        if name == "ask":
            child.add_argument("--command", action="store_true", help="Return only a shell command.")
        child.add_argument("--shell", choices=("bash", "zsh"), help="Target shell (otherwise $SHELL).")
        child.add_argument("-m", "--model", type=_model_id,
                           help="Use this model for this request without changing the saved choice.")
        child.add_argument("--timeout", type=_positive_timeout, default=60, metavar="SECONDS")
        child.add_argument("request", nargs=argparse.REMAINDER,
                           help="Request words; use -- before an initial dash. Options precede request.")
    init = commands.add_parser("init", help="Print Bash or Zsh integration for sourcing.")
    init.add_argument("shell", choices=("bash", "zsh"))
    model = commands.add_parser("model", help="Show or change the saved model; prompts in a terminal.")
    selection = model.add_mutually_exclusive_group()
    selection.add_argument("model", nargs="?", type=_model_id, metavar="MODEL",
                           help="Model ID for the saved provider.")
    selection.add_argument("--show", action="store_true", help="Show the saved/effective model without prompting.")
    auth = commands.add_parser("auth", help="Sign in, inspect login, or remove credentials.")
    actions = auth.add_subparsers(dest="auth_action", required=True)
    login = actions.add_parser("login", help="Sign in and select a provider.")
    login.add_argument("provider", nargs="?", choices=PROVIDERS, default="openrouter")
    login.add_argument("--headless", action="store_true", help="Paste an OpenRouter OAuth code (SSH).")
    login.add_argument("--model", help="Select the model to use.")
    login.add_argument("--base-url", help="Override an HTTP provider's API root URL.")
    for name in ("status", "logout"):
        sub = actions.add_parser(name)
        sub.add_argument("provider", nargs="?", choices=PROVIDERS,
                         help="Defaults to the selected provider.")
    config = commands.add_parser("config", help="Show or change provider/model settings; never credentials.")
    config.add_argument("--provider", choices=PROVIDERS)
    config.add_argument("--model")
    config.add_argument("--base-url", help="HTTP API root; empty string restores the provider default.")
    return root


def _key(provider: str) -> str:
    from .providers import API_KEY_ENVS
    variable = API_KEY_ENVS.get(provider)
    return (os.environ.get(variable, "") if variable else "") or load_credentials().get(provider, "")


def _selected(provider: str, model=None, base_url=None) -> dict:
    previous = load_config()
    value = previous if previous.get("provider") == provider else {"provider": provider}
    value["model"] = model if model is not None else value.get("model", _defaults()[provider])
    if not value["model"].strip():
        raise ConchError("Model must not be empty.")
    if base_url is not None:
        if provider in ("codex", "claude-code") and base_url:
            raise ConchError("Subscription providers use their official CLI, not a custom API URL.")
        value["base_url"] = base_url
    return value


def _model(args) -> int:
    saved = load_config()
    provider = saved.get("provider", "openrouter")
    if provider not in PROVIDERS:
        raise ConchError("Unknown saved provider; select one with conchshell config --provider.")
    current = saved.get("model") or _defaults()[provider]
    # Environment overrides remain temporary; never persist their provider or endpoint.
    effective = effective_config()
    active = effective["provider"]
    active_model = effective.get("model") or _defaults()[active]
    overridden = any(name in os.environ for name in ("CONCHSHELL_PROVIDER", "CONCHSHELL_MODEL"))
    if overridden:
        print(f"Environment override active: {active}/{active_model}. "
              "Unset CONCHSHELL_PROVIDER/CONCHSHELL_MODEL to use the saved selection.", file=sys.stderr)
    chosen = args.model
    if chosen is None:
        print(f"Saved model: {provider}/{current}")
        if args.show or not sys.stdin.isatty():
            return 0
        print(f"Enter a {provider} model ID; provider default: {_defaults()[provider]}.", file=sys.stderr)
        print(f"Model ID [{current}]: ", end="", file=sys.stderr, flush=True)
        chosen = input().strip()
        if not chosen:
            return 0
    save_config(_selected(provider, chosen))
    print(f"Saved model: {provider}/{chosen}")
    return 0


def _auth(args) -> int:
    from . import native, oauth
    provider = args.provider or effective_config()["provider"]
    is_native = provider in native.DEFAULT_MODELS
    if args.auth_action == "login":
        if args.headless and provider != "openrouter":
            raise ConchError("--headless is for OpenRouter; subscription sign-in uses the official CLI.")
        settings = _selected(provider, args.model, args.base_url)
        if is_native:
            result = native.authenticate(provider, "login")
            if result:
                return result
            ready, detail = native.status(provider)
            if not ready:
                raise ConchError(detail)
        elif provider != "ollama":
            if provider == "openrouter":
                key = oauth.login(headless=args.headless)
            else:
                if not sys.stdin.isatty():
                    raise ConchError("API-key login needs a terminal; alternatively set the provider API-key environment variable.")
                key = getpass.getpass(f"{provider} API key (hidden): ").strip()
                if not key:
                    raise ConchError("No API key supplied; settings unchanged.")
            credentials = load_credentials()
            credentials[provider] = key
            save_credentials(credentials)
        save_config(settings)
        print(f"Selected {provider}/{settings['model']}. Try: ask how do I list hidden files")
        if provider == "ollama":
            print("Ollama must already be running with that model installed; no sign-in is required.")
        return 0
    if args.auth_action == "status":
        if is_native:
            ready, detail = native.status(provider)
        elif provider == "ollama":
            ready, detail = True, "local provider; no sign-in required (server/model not checked)."
        else:
            ready = bool(_key(provider))
            detail = "credential configured; model access not checked." if ready else "not configured; run conchshell auth login " + provider
        print(f"{provider}: {detail}")
        return 0 if ready else 1
    if is_native:
        return native.authenticate(provider, "logout")
    credentials = load_credentials()
    credentials.pop(provider, None)
    save_credentials(credentials)
    print(f"Removed locally stored {provider} credentials; environment variables are unchanged.")
    if provider == "openrouter":
        print("To revoke the issued key remotely, visit https://openrouter.ai/settings/keys")
    return 0


def _clean_response(text: str, command: bool) -> str:
    if not isinstance(text, str) or not text.strip():
        raise ConchError("Provider returned no answer.")
    # Never send terminal controls from the model to the user's terminal/readline.
    if any(unicodedata.category(char) in ("Cc", "Cf", "Cs") and char not in "\n\t" for char in text):
        raise ConchError("Provider returned terminal control characters; answer discarded.")
    text = text.strip()
    if command and text.startswith("```"):
        lines = text.splitlines()
        if len(lines) == 3 and lines[0] in ("```", "```sh", "```bash", "```zsh", "```shell") and lines[-1] == "```":
            text = lines[1].strip()
    if command and (not text or len(text.splitlines()) != 1 or "\t" in text or "```" in text):
        raise ConchError("Provider did not return one single-line command; nothing inserted.")
    return text


def _ask(args) -> int:
    from . import native, providers
    from .progress import Thinking
    words = args.request[1:] if args.request[:1] == ["--"] else args.request
    request = " ".join(words).strip()
    if not request:
        if not sys.stdin.isatty():
            raise ConchError("Provide a request (see conchshell ask --help).")
        print("ask: ", end="", file=sys.stderr, flush=True)
        request = input().strip()
        if not request:
            return 0
    command = args.action == "complete" or getattr(args, "command", False)
    shell = args.shell or os.path.basename(os.environ.get("SHELL", "sh"))
    system = (COMMAND_SYSTEM if command else ANSWER_SYSTEM) + f" Target: {platform.system()}, {shell}."
    settings = effective_config()
    provider = settings["provider"]
    model = args.model or settings.get("model") or _defaults()[provider]
    is_native = provider in native.DEFAULT_MODELS
    key = "" if is_native else _key(provider)
    if not is_native and provider != "ollama" and not key:
        raise ConchError(f"Sign in first: conchshell auth login {provider}")
    with Thinking(provider, model):
        if is_native:
            text = native.complete(provider, model, system, request, args.timeout)
        else:
            text = providers.complete(provider, model, system, request, args.timeout,
                                      api_key=key, base_url=settings.get("base_url", ""))
        text = _clean_response(text, command)
    print(text)
    return 0


def main(argv=None) -> int:
    root = parser()
    args = root.parse_args(argv)
    try:
        if args.action in ("ask", "complete"):
            return _ask(args)
        if args.action == "init":
            resource = importlib.resources.files("conchshell").joinpath("shell").joinpath(f"conchshell.{args.shell}")
            print(resource.read_text(encoding="utf-8"), end="")
            return 0
        if args.action == "auth":
            return _auth(args)
        if args.action == "model":
            return _model(args)
        if args.action == "config":
            if any(value is not None for value in (args.provider, args.model, args.base_url)):
                provider = args.provider or load_config().get("provider", "openrouter")
                if provider not in PROVIDERS:
                    raise ConchError("Unknown saved provider; select one with --provider.")
                save_config(_selected(provider, args.model, args.base_url))
            settings = effective_config()
            settings.setdefault("model", _defaults()[settings["provider"]])
            # Print a fixed allowlist, never unknown settings that could contain secrets.
            print(json.dumps({key: settings[key] for key in ("provider", "model", "base_url") if key in settings}, indent=2))
            print(f"Settings: {config_dir() / 'config.json'}", file=sys.stderr)
            return 0
        root.print_help()
        return 0
    except ConchError as exc:
        print(f"conchshell: {exc}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\nconchshell: cancelled", file=sys.stderr)
        return 130
    except BrokenPipeError:
        return 1
    except OSError as exc:
        print(f"conchshell: operation failed ({type(exc).__name__})", file=sys.stderr)
        return 1
