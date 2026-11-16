"""Small, non-streaming HTTP model clients with no tool execution."""

import http.client
import ipaddress
import json
import math
import socket
import urllib.error
import urllib.parse
import urllib.request

from .errors import ConchError

DEFAULT_MODELS = {
    "openrouter": "openai/gpt-4o-mini",
    "openai": "gpt-4o-mini",
    "anthropic": "claude-haiku-4-5",
    "ollama": "llama3.2",
}
API_KEY_ENVS = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "ollama": "",
}
_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "ollama": "http://127.0.0.1:11434",
}
_MAX_RESPONSE = 1024 * 1024
_MAX_TOKENS = 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _validate_url(url: str) -> bool:
    """Validate the destination and return whether it is loopback."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname
        port = parts.port
        if (not host or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or (port is not None and port == 0)
                or any(ord(c) <= 32 or ord(c) == 127 for c in url)):
            raise ValueError
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host.lower() == "localhost"
        if parts.scheme != "https" and not (parts.scheme == "http" and local):
            raise ValueError
    except (ValueError, TypeError):
        raise ConchError("Endpoint must use HTTPS (HTTP is allowed only on loopback).") from None
    return local


def _check_timeout(timeout: float) -> None:
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise ConchError("Timeout must be a positive finite number.")


def _post_json(url: str, payload: dict, headers: dict, timeout: float, label: str) -> dict:
    """POST without redirects; never include server text or secrets in errors."""
    local = _validate_url(url)
    _check_timeout(timeout)
    handlers = [_NoRedirect()]
    if local:
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    try:
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": "conchshell", **headers},
        )
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(_MAX_RESPONSE + 1)
            if len(raw) > _MAX_RESPONSE:
                raise ConchError(f"{label}: response exceeded the size limit.")
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        if 300 <= status < 400:
            detail = "redirect refused; use the final trusted endpoint"
        elif status in (401, 403):
            detail = "authentication rejected; check your credentials"
        elif status == 429:
            detail = "rate limit or account quota exceeded"
        elif status >= 500:
            detail = "provider service unavailable"
        else:
            detail = "request rejected; check the model and provider configuration"
        raise ConchError(f"{label}: HTTP {status}: {detail}.") from None
    except (TimeoutError, socket.timeout):
        raise ConchError(f"{label}: request timed out.") from None
    except urllib.error.URLError as exc:
        detail = "request timed out" if isinstance(exc.reason, TimeoutError) else "connection failed; check the endpoint and network"
        raise ConchError(f"{label}: {detail}.") from None
    except (OSError, http.client.HTTPException, ValueError, UnicodeError):
        raise ConchError(f"{label}: connection or request failed.") from None
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise ConchError(f"{label}: malformed JSON response.") from None
    if not isinstance(data, dict):
        raise ConchError(f"{label}: malformed response; expected a JSON object.")
    if "error" in data:
        raise ConchError(f"{label}: provider reported an error; check the model and account.")
    return data


def complete(provider: str, model: str, system: str, prompt: str, timeout: float,
             *, api_key: str = "", base_url: str = "") -> str:
    """Return one complete text answer, rejecting truncated or tool-only replies."""
    if provider not in DEFAULT_MODELS:
        raise ConchError("Unsupported HTTP provider.")
    if provider != "ollama" and not api_key.strip():
        raise ConchError(f"{provider}: set {API_KEY_ENVS[provider]} or log in first.")
    if any(ord(c) < 32 or ord(c) == 127 for c in api_key):
        raise ConchError(f"{provider}: invalid API key.")
    base = (base_url or _BASE_URLS[provider]).rstrip("/")
    _validate_url(base)
    messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
    headers = {}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    payload = {"model": model or DEFAULT_MODELS[provider], "stream": False}
    if provider == "anthropic":
        headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}
        payload.update(system=system, messages=messages[1:], max_tokens=_MAX_TOKENS)
        path = "/messages"
    elif provider == "ollama":
        payload.update(messages=messages, options={"num_predict": _MAX_TOKENS})
        path = "/api/chat"
    else:
        payload["messages"] = messages
        cap_field = "max_tokens" if provider == "openrouter" else "max_completion_tokens"
        payload[cap_field] = _MAX_TOKENS
        path = "/chat/completions"
    data = _post_json(base + path, payload, headers, timeout, provider)
    try:
        if provider == "anthropic":
            if data.get("stop_reason") not in ("end_turn", "stop_sequence"):
                raise ConchError(f"{provider}: incomplete or non-text response.")
            blocks = data["content"]
            if not isinstance(blocks, list) or not blocks:
                raise ValueError
            text_parts = []
            for block in blocks:
                if block.get("type") != "text" or not isinstance(block.get("text"), str):
                    raise ValueError
                text_parts.append(block["text"])
            text = "".join(text_parts)
        elif provider == "ollama":
            if data.get("done") is not True or data.get("done_reason") != "stop":
                raise ConchError(f"{provider}: incomplete or non-text response.")
            message = data["message"]
            if message.get("tool_calls"):
                raise ValueError
            text = message["content"]
        else:
            choice = data["choices"][0]
            if choice.get("finish_reason") != "stop":
                raise ConchError(f"{provider}: incomplete or non-text response.")
            message = choice["message"]
            if message.get("tool_calls") or message.get("function_call") or message.get("refusal"):
                raise ValueError
            text = message["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError
    except (KeyError, IndexError, TypeError, AttributeError, ValueError):
        raise ConchError(f"{provider}: malformed or empty text response.") from None
    return text.strip()
