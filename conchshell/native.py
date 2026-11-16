"""Optional official subscription CLIs; credentials remain entirely CLI-owned."""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time

from .errors import ConchError

DEFAULT_MODELS = {"codex": "default", "claude-code": "sonnet"}
_CLIENTS = {
    "codex": ("codex", "https://developers.openai.com/codex/cli/"),
    "claude-code": ("claude", "https://code.claude.com/docs/en/setup"),
}
_SCHEMA = {"type": "object", "properties": {"text": {"type": "string"}},
           "required": ["text"], "additionalProperties": False}
_CODEX_AUTH = ["-c", 'forced_login_method="chatgpt"', "-c", 'model_provider="openai"']
# shell_tool is the execution gate; disabling unified_exec alone is insufficient.
_CODEX_DISABLED = (
    "shell_tool", "shell_snapshot", "shell_snapshot_v2",
    "code_mode", "code_mode_host", "code_mode_only",
    "apps", "enable_mcp_apps", "plugins", "remote_plugin", "hooks",
    "browser_use", "browser_use_external", "computer_use",
    "image_generation", "view_image", "multi_agent", "multi_agent_v2",
    "skill_mcp_dependency_install", "skill_search", "workspace_dependencies",
    "memories", "goals", "tool_suggest", "request_permissions_tool",
    "standalone_web_search", "daemon_auto_start",
)
# Unlike --bare, safe mode retains native subscription authentication.
_CLAUDE_ISOLATION = [
    "--restricted", "--setting-sources", "",
    "--settings", '{"disableAllHooks":true,"forceLoginMethod":"claudeai"}',
    "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--safe-mode",
]


def _client(provider: str) -> str:
    if provider not in _CLIENTS:
        raise ConchError("Unsupported native subscription provider")
    return _CLIENTS[provider][0]


def _environment() -> dict:
    env = dict(os.environ)
    for name in tuple(env):
        if (name.startswith(("ANTHROPIC_", "OPENAI_", "CLAUDE_CODE_", "CODEX_"))
                and name not in ("CODEX_HOME", "CLAUDE_CODE_USE_SYSTEM_KEYCHAIN")):
            env.pop(name, None)
    for name in ("NODE_OPTIONS", "NODE_PATH", "BASH_ENV", "ENV"):
        env.pop(name, None)
    return env


def _missing(provider: str) -> ConchError:
    binary, url = _CLIENTS[provider]
    return ConchError(f"Install the optional official {binary} CLI first: {url}")


def _deadline(timeout: float) -> float:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ConchError("Native CLI timeout must be a positive finite number")
    return time.monotonic() + timeout


def _run(provider: str, argv: list, *, cwd: str, timeout: float,
         prompt: str = None) -> subprocess.CompletedProcess:
    """Bound the entire process group, including helpers surviving the leader."""
    _deadline(timeout)
    process = None
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            env=_environment(), cwd=cwd, start_new_session=True,
        )
        stdout, stderr = process.communicate(input=prompt, timeout=timeout)
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    except FileNotFoundError:
        raise _missing(provider) from None
    except subprocess.TimeoutExpired:
        raise ConchError("Native CLI timed out; its process group was stopped") from None
    except (OSError, UnicodeError):
        raise ConchError("Could not communicate with the official native CLI") from None
    finally:
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError:
                process.kill()
                raise ConchError("Could not stop the native CLI process group") from None
            finally:
                # Do not communicate() again: escaped descendants could retain pipes.
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    raise ConchError("Could not reap the native CLI after stopping it") from None
                finally:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            stream.close()


def _auth_argv(provider: str, action: str) -> list:
    binary = _client(provider)
    if provider == "codex":
        return [binary, *_CODEX_AUTH, *(["login", "status"] if action == "status" else [action])]
    argv = [binary, *_CLAUDE_ISOLATION, "auth", action]
    if action == "login":
        argv.append("--claudeai")
    elif action == "status":
        argv.append("--json")
    return argv


def authenticate(provider: str, action: str) -> int:
    """Run official login/logout interactively, without inspecting token stores."""
    _client(provider)
    if action not in ("login", "logout"):
        raise ConchError("Native authentication action must be login or logout")
    try:
        with tempfile.TemporaryDirectory(prefix="conchshell-auth-") as cwd:
            return subprocess.call(_auth_argv(provider, action), env=_environment(), cwd=cwd)
    except FileNotFoundError:
        raise _missing(provider) from None
    except OSError:
        raise ConchError("Could not launch the official CLI authentication command") from None


def _decode(text: str):
    def invalid_constant(value):
        raise ValueError("Non-JSON constant")
    try:
        return json.loads(text, parse_constant=invalid_constant)
    except (ValueError, TypeError):
        raise ConchError("Native CLI returned malformed JSON") from None


def _status(provider: str, timeout: float, cwd: str) -> tuple[bool, str]:
    result = _run(provider, _auth_argv(provider, "status"), cwd=cwd, timeout=timeout)
    if provider == "codex":
        lines = (result.stdout + "\n" + result.stderr).lower().splitlines()
        ready = (result.returncode == 0
                 and any(line.strip() == "logged in using chatgpt" for line in lines)
                 and not any("api key" in line or "api-key" in line for line in lines))
    else:
        identity = _decode(result.stdout)
        ready = (result.returncode == 0 and isinstance(identity, dict)
                 and identity.get("loggedIn") is True
                 and identity.get("authMethod") == "claude.ai"
                 and identity.get("apiProvider", "firstParty") == "firstParty"
                 and isinstance(identity.get("subscriptionType"), str)
                 and identity["subscriptionType"].strip().lower()
                 in {"pro", "max", "ultra", "team", "enterprise"})
    if ready:
        return True, "Native subscription login detected; model access has not been verified."
    return False, f"Subscription login required; run conchshell auth login {provider}."


def status(provider: str, timeout: float = 10) -> tuple[bool, str]:
    """Report subscription readiness, never native account details or secrets."""
    _client(provider)
    try:
        _deadline(timeout)
        with tempfile.TemporaryDirectory(prefix="conchshell-status-") as cwd:
            return _status(provider, timeout, cwd)
    except ConchError as exc:
        return False, str(exc)
    except OSError:
        return False, "Could not isolate the native login status command"


def _text(response) -> str:
    if (not isinstance(response, dict) or set(response) != {"text"}
            or not isinstance(response["text"], str) or not response["text"].strip()):
        raise ConchError("Native CLI returned no valid schema-constrained text response")
    return response["text"]


def _codex_events(stdout: str) -> None:
    completed = False
    for line in stdout.splitlines():
        if not line.strip():
            continue
        event = _decode(line)
        if not isinstance(event, dict) or completed:
            raise ConchError("Codex returned an invalid event stream")
        kind = event.get("type")
        if kind in ("item.started", "item.updated", "item.completed"):
            item = event.get("item")
            if not isinstance(item, dict):
                raise ConchError("Codex returned an invalid stream item")
            if item.get("type") == "error":
                # Codex ErrorItem is a nonfatal diagnostic, unlike error/turn.failed.
                if not isinstance(item.get("message"), str) or not item["message"].strip():
                    raise ConchError("Codex returned an invalid diagnostic")
            elif item.get("type") not in ("agent_message", "reasoning"):
                raise ConchError("Codex attempted native tool execution")
        elif kind == "turn.completed":
            completed = True
        elif kind not in ("thread.started", "turn.started"):
            raise ConchError("Codex returned an error or unsupported event; update the official CLI")
    if not completed:
        raise ConchError("Codex did not complete its response")


def _claude_result(stdout: str) -> str:
    payload = _decode(stdout)
    if not isinstance(payload, dict) or payload.get("type") != "result":
        raise ConchError("Claude returned no final result")
    if payload.get("subtype") != "success" or payload.get("is_error") is not False:
        raise ConchError("Claude reported an error or incomplete response")
    if payload.get("errors") or payload.get("api_error_status") or payload.get("permission_denials"):
        raise ConchError("Claude reported an error or attempted native tool execution")
    pending = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if value.get("tool_calls") or value.get("type") in (
                    "tool_use", "server_tool_use", "tool_result", "mcp_tool_use",
                    "function_call", "function_call_output"):
                raise ConchError("Claude attempted native tool execution")
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return _text(payload.get("structured_output"))


def complete(provider: str, model: str, system: str, prompt: str, timeout: float) -> str:
    """One isolated inference; unsupported safety flags fail without a downgrade."""
    binary = _client(provider)
    deadline = _deadline(timeout)
    # Both instructions and user input travel over stdin, never command arguments.
    request = system + "\n\nReturn only the requested JSON object with a text field.\n\n" + prompt
    try:
        with tempfile.TemporaryDirectory(prefix="conchshell-native-") as directory:
            root = Path(directory)
            work = root / "work"
            work.mkdir(mode=0o700)
            ready, diagnosis = _status(provider, min(timeout, 10), str(work))
            if not ready:
                raise ConchError(diagnosis)
            if provider == "codex":
                schema_path, output_path = root / "schema.json", root / "response.json"
                for path, content in ((schema_path, json.dumps(_SCHEMA)), (output_path, "")):
                    with path.open("x", encoding="utf-8") as handle:
                        os.chmod(path, 0o600)
                        handle.write(content)
                argv = [
                    binary, "exec", *_CODEX_AUTH, "--json", "--output-schema", str(schema_path),
                    "--output-last-message", str(output_path), "--sandbox", "read-only",
                    "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                    "-c", 'approval_policy="never"', "-c", "agents.enabled=false",
                    "-c", 'web_search="disabled"', "-c", "tools.web_search=false",
                    "-c", "tools.update_plan.enabled=false",
                    "-c", "tools.experimental_request_user_input.enabled=false",
                    "-c", 'history.persistence="none"', "-c", "project_doc_max_bytes=0",
                    "-c", "skills.bundled.enabled=false", "-c", "skills.include_instructions=false",
                ]
                for feature in _CODEX_DISABLED:
                    argv.extend(["--disable", feature])
                if model and model != "default":
                    argv.extend(["--model", model])
                argv.append("-")
            else:
                argv = [
                    binary, *_CLAUDE_ISOLATION, "--print", "--output-format", "json",
                    "--json-schema", json.dumps(_SCHEMA), "--tools", "",
                    "--disable-slash-commands", "--no-session-persistence", "--no-chrome",
                    "--permission-mode", "dontAsk", "--model", model or "sonnet",
                ]
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ConchError("Native CLI timed out while checking its subscription login")
            result = _run(provider, argv, cwd=str(work), timeout=remaining, prompt=request)
            if result.returncode:
                # Native output can contain secrets, prompt text, or account identities.
                raise ConchError(
                    f"Official {binary} CLI failed (exit {result.returncode}); check subscription login "
                    "and model access, and update the CLI to support isolated execution."
                )
            if provider == "claude-code":
                return _claude_result(result.stdout)
            _codex_events(result.stdout)
            return _text(_decode(output_path.read_text(encoding="utf-8")))
    except (OSError, UnicodeError):
        raise ConchError("Could not isolate the native CLI or read its structured response") from None
