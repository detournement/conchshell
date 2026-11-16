import base64
import concurrent.futures
import contextlib
import hashlib
import http.server
import io
import json
import socket
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from unittest import mock

from conchshell import oauth
from conchshell.errors import ConchError


@contextlib.contextmanager
def key_service(challenge, response=None, status=200):
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, data))
            digest = hashlib.sha256(data.get("code_verifier", "").encode()).digest()
            actual = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
            valid = (actual == challenge[0] and data.get("code_challenge_method") == "S256"
                     and data.get("code") == "single-use-code" and self.path == "/auth/keys")
            body = response if response is not None else {"key": "issued-secret-key"}
            raw = json.dumps(body if valid else {"error": "wrong PKCE"}).encode()
            self.send_response(status if valid else 403)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/auth/keys", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def callback_status(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        with error:
            return error.code, error.read()


class OAuthTests(unittest.TestCase):
    def test_local_pkce_rejects_invalid_callbacks_then_exchanges(self):
        challenge = [None]
        browser_jobs = []
        callback_urls = []
        stderr = io.StringIO()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            def open_browser(url):
                parts = urllib.parse.urlsplit(url)
                self.assertEqual((parts.scheme, parts.netloc, parts.path), ("https", "openrouter.ai", "/auth"))
                params = urllib.parse.parse_qs(parts.query)
                challenge[0] = params["code_challenge"][0]
                self.assertEqual(params["code_challenge_method"], ["S256"])
                self.assertEqual(params["key_label"], ["conchshell"])
                callback = params["callback_url"][0]
                callback_urls.append(callback)
                state = params["state"][0]
                self.assertGreaterEqual(len(state), 32)
                self.assertEqual(urllib.parse.urlsplit(callback).hostname, "127.0.0.1")
                self.assertGreater(len(urllib.parse.urlsplit(callback).path), len("/callback/"))

                def visit():
                    invalid = [
                        callback + "?code=attacker-code",
                        callback + "?state=wrong&code=attacker-code",
                        callback + "?state=%C3%A9&code=attacker-code",
                        callback + "?" + urllib.parse.urlencode({"state": state}),
                        callback + "?" + urllib.parse.urlencode({"state": state, "error": "denied"}),
                        callback + "?" + urllib.parse.urlencode({"state": state, "code": "x"}) + "&code=y",
                        callback + "?" + urllib.parse.urlencode({"state": state, "code": "x"}) + "&state=other",
                        callback + "/wrong?" + urllib.parse.urlencode({"state": state, "code": "x"}),
                    ]
                    for attempt in invalid:
                        self.assertEqual(callback_status(attempt)[0], 400)
                    status, body = callback_status(callback + "?" + urllib.parse.urlencode({"state": state, "code": "single-use-code"}))
                    self.assertEqual(status, 200)
                    self.assertNotIn(b"single-use-code", body)
                browser_jobs.append(pool.submit(visit))
                return True

            with key_service(challenge) as (endpoint, calls):
                with mock.patch.object(oauth, "_KEY_URL", endpoint), mock.patch.object(oauth.webbrowser, "open", open_browser), contextlib.redirect_stderr(stderr):
                    self.assertEqual(oauth.login(timeout=3), "issued-secret-key")
                browser_jobs[0].result(timeout=2)
                self.assertEqual(len(calls), 1)
                self.assertGreaterEqual(len(calls[0][1]["code_verifier"]), 43)
                self.assertNotIn(calls[0][1]["code_verifier"], stderr.getvalue())
        self.assertIn("https://openrouter.ai/auth?", stderr.getvalue())
        self.assertNotIn("single-use-code", stderr.getvalue())
        self.assertNotIn("issued-secret-key", stderr.getvalue())
        parsed = urllib.parse.urlsplit(callback_urls[0])
        with socket.socket() as connection:
            connection.settimeout(0.2)
            self.assertNotEqual(connection.connect_ex((parsed.hostname, parsed.port)), 0)

    def test_headless_omits_callback_and_uses_hidden_prompt(self):
        challenge = [None]
        urls = []

        def open_browser(url):
            urls.append(url)
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            challenge[0] = params["code_challenge"][0]
            self.assertNotIn("callback_url", params)
            self.assertNotIn("state", params)
            self.assertEqual(params["code_challenge_method"], ["S256"])
            return False

        with key_service(challenge) as (endpoint, calls):
            with mock.patch.object(oauth, "_KEY_URL", endpoint), mock.patch.object(oauth.webbrowser, "open", open_browser), mock.patch.object(oauth.getpass, "getpass", return_value=" single-use-code \n") as prompt, contextlib.redirect_stderr(io.StringIO()) as stderr:
                self.assertEqual(oauth.login(headless=True, timeout=2), "issued-secret-key")
            prompt.assert_called_once()
            self.assertEqual(len(calls), 1)
            self.assertIn(urls[0], stderr.getvalue())
            self.assertNotIn("single-use-code", stderr.getvalue())

    def test_callback_timeout_including_slow_client(self):
        connections = []

        def open_browser(url):
            params = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
            callback = urllib.parse.urlsplit(params["callback_url"][0])
            connection = socket.create_connection((callback.hostname, callback.port), timeout=1)
            connection.sendall(b"GET /incomplete")
            connections.append(connection)
            return True

        started = time.monotonic()
        try:
            with mock.patch.object(oauth.webbrowser, "open", open_browser), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(ConchError, "timed out"):
                    oauth.login(timeout=0.1)
            self.assertLess(time.monotonic() - started, 1.5)
        finally:
            for connection in connections:
                connection.close()

    def test_headless_input_timeout(self):
        release = threading.Event()
        entered = threading.Event()

        cleaned_up = threading.Event()

        def prompt(_):
            entered.set()
            try:
                release.wait(2)
                return "single-use-code"
            finally:
                cleaned_up.set()

        try:
            with mock.patch.object(oauth.webbrowser, "open", return_value=False), mock.patch.object(oauth.getpass, "getpass", prompt), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(ConchError, "timed out"):
                    oauth.login(headless=True, timeout=0.05)
            self.assertTrue(entered.is_set())
            if hasattr(oauth.signal, "setitimer"):
                self.assertTrue(cleaned_up.is_set(), "getpass must restore terminal modes before timeout returns")
        finally:
            release.set()

    def test_exchange_rejects_invalid_keys_and_provider_errors(self):
        for response, status in (({}, 200), ({"key": ""}, 200), ({"key": " \t"}, 200), ({"key": 123}, 200),
                                 ({"key": "secret\nkey"}, 200), ({"error": "single-use-code issued-secret-key"}, 403)):
            challenge = [None]

            def open_browser(url):
                challenge[0] = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)["code_challenge"][0]
                return False

            with self.subTest(response=response), key_service(challenge, response, status) as (endpoint, calls):
                with mock.patch.object(oauth, "_KEY_URL", endpoint), mock.patch.object(oauth.webbrowser, "open", open_browser), mock.patch.object(oauth.getpass, "getpass", return_value="single-use-code"), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(ConchError) as caught:
                        oauth.login(headless=True, timeout=2)
                self.assertEqual(len(calls), 1)
                self.assertNotIn("single-use-code", str(caught.exception))
                self.assertNotIn("issued-secret-key", str(caught.exception))

    def test_empty_pasted_code_is_not_exchanged(self):
        with mock.patch.object(oauth.webbrowser, "open", return_value=False), mock.patch.object(oauth.getpass, "getpass", return_value="  "), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(ConchError, "authorization code"):
                oauth.login(headless=True, timeout=1)


if __name__ == "__main__":
    unittest.main()
