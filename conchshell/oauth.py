"""OpenRouter's public PKCE flow; the caller is responsible for saving the key."""

import base64
import getpass
import hashlib
import http.server
import secrets
import signal
import sys
import threading
import time
import urllib.parse
import webbrowser

from .errors import ConchError
from .providers import _check_timeout, _post_json

_AUTH_URL = "https://openrouter.ai/auth"
_KEY_URL = "https://openrouter.ai/api/v1/auth/keys"


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ConchError("OpenRouter login timed out; start login again.")
    return remaining


def _show_url(url: str) -> None:
    print("Authorize OpenRouter in your browser:\n" + url, file=sys.stderr)
    try:
        webbrowser.open(url)
    except (OSError, webbrowser.Error):
        pass  # The printed URL also supports manually opening a browser.


def _pasted_code(deadline: float) -> str:
    prompt_text = "Paste the OpenRouter authorization code: "
    if hasattr(signal, "setitimer") and threading.current_thread() is threading.main_thread():
        # Interrupt getpass in its own thread so its finally block restores echo.
        def expired(signum, frame):
            raise ConchError("OpenRouter login timed out; start login again.")

        remaining = _remaining(deadline)
        previous_handler = signal.signal(signal.SIGALRM, expired)
        started = time.monotonic()
        previous_timer = signal.setitimer(signal.ITIMER_REAL, remaining)
        try:
            try:
                code = getpass.getpass(prompt_text)
            except (EOFError, OSError):
                code = None
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, previous_handler)
            if previous_timer[0]:
                signal.setitimer(signal.ITIMER_REAL, max(0.001, previous_timer[0] - (time.monotonic() - started)), previous_timer[1])
    else:
        raise ConchError("Headless login requires a POSIX main thread; use browser login instead.")
    if not isinstance(code, str) or not code.strip() or len(code) > 8192:
        raise ConchError("No valid OpenRouter authorization code was provided.")
    return code.strip()


def _callback_code(params: dict, deadline: float) -> str:
    path = "/callback/" + secrets.token_urlsafe(24)
    state = secrets.token_urlsafe(32)
    codes = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_GET(self):
            valid = False
            try:
                target = urllib.parse.urlsplit(self.path)
                values = urllib.parse.parse_qs(target.query, max_num_fields=8)
                states = values.get("state", [])
                received = values.get("code", [])
                valid = (
                    target.path == path and len(self.path) <= 16384
                    and len(states) == 1 and len(received) == 1
                    and secrets.compare_digest(states[0].encode("utf-8"), state.encode("ascii"))
                    and bool(received[0].strip()) and len(received[0]) <= 8192
                    and "error" not in values and time.monotonic() < deadline
                )
            except (ValueError, UnicodeError):
                pass
            if valid:
                codes.append(received[0].strip())
                body = b"OpenRouter authorization received. You may close this window.\n"
            else:
                body = b"Invalid authorization callback. Return to the original login page.\n"
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

    class Server(http.server.HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(max(0.001, min(1.0, deadline - time.monotonic())))
            return connection, address

        def handle_error(self, request, client_address):
            pass  # No HTTP logging, including broken connections or malformed requests.

    try:
        server = Server(("127.0.0.1", 0), Handler)
    except OSError:
        raise ConchError("Cannot listen on loopback; use headless OpenRouter login.") from None
    with server:
        params.update(callback_url=f"http://127.0.0.1:{server.server_port}{path}", state=state)
        _show_url(_AUTH_URL + "?" + urllib.parse.urlencode(params))
        while not codes:
            server.timeout = min(0.25, _remaining(deadline))
            server.handle_request()
    return codes[0]


def login(*, headless: bool = False, timeout: float = 180) -> str:
    """Authorize with S256 PKCE and return the newly issued, nonempty API key."""
    _check_timeout(timeout)
    deadline = time.monotonic() + timeout
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    params = {"code_challenge": challenge, "code_challenge_method": "S256", "key_label": "conchshell"}
    if headless:
        _show_url(_AUTH_URL + "?" + urllib.parse.urlencode(params))
        code = _pasted_code(deadline)
    else:
        code = _callback_code(params, deadline)
    data = _post_json(
        _KEY_URL,
        {"code": code, "code_verifier": verifier, "code_challenge_method": "S256"},
        {}, _remaining(deadline), "OpenRouter login",
    )
    key = data.get("key")
    if not isinstance(key, str) or not key.strip() or any(ord(c) < 32 or ord(c) == 127 for c in key):
        raise ConchError("OpenRouter login returned no valid API key.")
    return key.strip()
