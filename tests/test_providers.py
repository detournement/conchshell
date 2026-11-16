import contextlib
import http.server
import json
import threading
import time
import unittest
from unittest import mock

from conchshell.errors import ConchError
from conchshell import providers


@contextlib.contextmanager
def fake_service(body=None, status=200, headers=None, delay=0, disconnect=False):
    requests = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, dict(self.headers), payload))
            if disconnect:
                self.close_connection = True
                return
            if delay:
                time.sleep(delay)
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            try:
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def chat_response(text="printf hello", reason="stop"):
    return {"choices": [{"finish_reason": reason, "message": {"content": text}}]}


class ProviderTests(unittest.TestCase):
    def request(self, provider, base_url, timeout=1):
        return providers.complete(provider, "chosen-model", "system instruction", "user request", timeout,
                                  api_key="secret-key", base_url=base_url)

    def test_provider_errors_are_bounded_and_secret_free(self):
        for status, expected in ((401, "authentication"), (403, "authentication"), (429, "quota"), (503, "unavailable"), (400, "configuration")):
            with self.subTest(status=status), fake_service({"error": "secret-key\x1b[31m" * 1000}, status) as (url, _):
                with self.assertRaises(ConchError) as caught:
                    self.request("openai", url)
                message = str(caught.exception)
                self.assertIn(str(status), message)
                self.assertIn(expected, message)
                self.assertNotIn("secret-key", message)
                self.assertNotIn("\x1b", message)
                self.assertLess(len(message), 200)

    def test_malformed_empty_and_error_responses(self):
        bodies = [b"not JSON secret-key", b"\xff", [], {}, {"error": "secret-key"},
                  {"choices": []}, {"choices": [None]}, chat_response(None), chat_response("   ")]
        for body in bodies:
            with self.subTest(body=body), fake_service(body) as (url, _):
                with self.assertRaises(ConchError) as caught:
                    self.request("openai", url)
                self.assertNotIn("secret-key", str(caught.exception))

    def test_incomplete_and_tool_responses_are_not_commands(self):
        cases = [
            ("openai", chat_response("rm -", "length")),
            ("openrouter", chat_response("partial", None)),
            ("openai", {"choices": [{"finish_reason": "stop", "message": {"content": "text", "tool_calls": [{}]}}]}),
            ("anthropic", {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "partial"}]}),
            ("anthropic", {"stop_reason": "end_turn", "content": [{"type": "tool_use", "name": "shell"}]}),
            ("ollama", {"done": False, "done_reason": "stop", "message": {"content": "partial"}}),
            ("ollama", {"done": True, "done_reason": "length", "message": {"content": "partial"}}),
        ]
        for provider, body in cases:
            with self.subTest(provider=provider, body=body), fake_service(body) as (url, _):
                with self.assertRaises(ConchError):
                    self.request(provider, url)

    def test_redirect_never_sends_credentials_to_target(self):
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status), fake_service(chat_response()) as (target, target_calls):
                with fake_service({}, status, {"Location": target + "/stolen"}) as (url, calls):
                    with self.assertRaisesRegex(ConchError, "redirect refused"):
                        self.request("openai", url)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(target_calls, [])

    def test_loopback_requests_do_not_use_environment_proxy(self):
        with fake_service(chat_response()) as (url, calls), fake_service({}) as (proxy, proxy_calls):
            with mock.patch.dict("os.environ", {"http_proxy": proxy, "HTTP_PROXY": proxy, "no_proxy": "", "NO_PROXY": ""}):
                self.assertEqual(self.request("openai", url), "printf hello")
            self.assertEqual(len(calls), 1)
            self.assertEqual(proxy_calls, [])

    def test_rejects_unsafe_urls_before_any_request(self):
        urls = ["http://example.com", "http://127.0.0.1.evil.invalid", "https://user:secret-key@example.com",
                "https://example.com?key=secret-key", "https://example.com/#secret-key", "file:///tmp/key",
                "https://example.com:bad", "https://example.com\n"]
        for url in urls:
            with self.subTest(url=url), self.assertRaisesRegex(ConchError, "HTTPS"):
                self.request("openai", url)

    def test_size_limit_timeout_and_disconnect(self):
        with fake_service(b"x" * (1024 * 1024 + 1)) as (url, _):
            with self.assertRaisesRegex(ConchError, "size limit"):
                self.request("openai", url)
        with fake_service(chat_response(), delay=0.15) as (url, _):
            with self.assertRaisesRegex(ConchError, "timed out"):
                self.request("openai", url, timeout=0.03)
        with fake_service(disconnect=True) as (url, _):
            with self.assertRaisesRegex(ConchError, "connection"):
                self.request("openai", url)

    def test_missing_credentials_and_bad_timeout(self):
        with self.assertRaisesRegex(ConchError, "OPENAI_API_KEY"):
            providers.complete("openai", "m", "s", "p", 1)
        with fake_service(chat_response()) as (url, calls):
            for timeout in (0, -1, float("inf"), float("nan")):
                with self.assertRaises(ConchError):
                    self.request("openai", url, timeout)
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
