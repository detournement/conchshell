"""Exercise the actual ZLE input boundary, not a mocked shell widget."""

import fcntl
import os
from pathlib import Path
import pty
import select
import shlex
import shutil
import signal
import subprocess
import tempfile
import termios
import time
import unittest


@unittest.skipUnless(shutil.which("zsh"), "Zsh is required for the widget regression")
class EmptyLineShortcutTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="conchshell-zle-test-")
        self.addCleanup(self.directory.cleanup)
        self.master, slave = pty.openpty()
        self.addCleanup(os.close, self.master)
        environment = dict(os.environ, HOME=self.directory.name, ZDOTDIR=self.directory.name,
                           TERM="xterm-256color", PROMPT="CONCHSHELL_TEST> ", RPROMPT="")
        self.process = subprocess.Popen(
            [shutil.which("zsh"), "-dfi"], stdin=slave, stdout=slave, stderr=slave,
            cwd=self.directory.name, env=environment, start_new_session=True,
            preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0),
        )
        os.close(slave)
        self.addCleanup(self.stop_shell)
        self.expect(b"CONCHSHELL_TEST> ")
        integration = Path(__file__).resolve().parents[1] / "conchshell/shell/conchshell.zsh"
        os.write(self.master, ("source " + shlex.quote(str(integration)) + "\n").encode())
        self.expect(b"CONCHSHELL_TEST> ")

    def stop_shell(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait(timeout=3)

    def expect(self, expected):
        output = b""
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if select.select([self.master], [], [], max(0, deadline - time.monotonic()))[0]:
                output += os.read(self.master, 65536)
                if expected in output:
                    return output
        self.fail(f"Terminal did not display {expected!r}; received {output!r}")

    def test_escape_escape_opens_editable_prompt_and_empty_submit_returns(self):
        os.write(self.master, b"\x1b")
        time.sleep(0.15)  # Two human keypresses, not one atomic escape sequence.
        os.write(self.master, b"\x1b")
        self.expect(b"ask: ")
        os.write(self.master, b"request that must not be sent")
        os.write(self.master, b"\x15\r")  # Clear the request and submit an empty line.
        self.expect(b"CONCHSHELL_TEST> ")

    def test_control_g_request_can_be_cancelled(self):
        os.write(self.master, b"\x07")
        self.expect(b"ask: ")
        os.write(self.master, b"cancel this request")
        os.write(self.master, b"\x03")
        self.expect(b"CONCHSHELL_TEST> ")


if __name__ == "__main__":
    unittest.main()
