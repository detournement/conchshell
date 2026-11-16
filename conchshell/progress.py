"""Transient generation feedback on stderr; never part of the model response."""

import os
import sys
import threading
import time
import unicodedata


def _clip(text, columns):
    visible = []
    used = 0
    for char in text:
        if not char.isprintable():
            continue
        width = 0 if unicodedata.combining(char) else (2 if unicodedata.east_asian_width(char) in "WF" else 1)
        if used + width > columns:
            break
        visible.append(char)
        used += width
    return "".join(visible), used


class Thinking:
    """An indeterminate spinner and elapsed time, not invented provider progress."""

    def __init__(self, provider, model):
        self.stream = sys.stderr
        self.enabled = self.stream.isatty() and os.environ.get("TERM") != "dumb"
        self.color = "NO_COLOR" not in os.environ
        self.label = f"{provider}/{model}"
        self.frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        try:
            self.frames.encode(self.stream.encoding or "utf-8")
        except (UnicodeError, LookupError):
            self.frames = "|/-\\"
        self.stop = threading.Event()
        self.thread = None
        self.drawn = False

    def __enter__(self):
        if self.enabled:
            self.started = time.monotonic()
            self.thread = threading.Thread(target=self._animate, name="conchshell-thinking", daemon=True)
            self.thread.start()
        return self

    def _animate(self):
        frame = 0
        while not self.stop.wait(0.08):
            try:
                try:
                    columns = os.get_terminal_size(self.stream.fileno()).columns
                except (OSError, ValueError):
                    columns = 80
                columns = max(0, columns - 1)  # Never wrap into the shell's next line.
                elapsed = time.monotonic() - self.started
                state, used = _clip(f"{self.frames[frame % len(self.frames)]} Thinking {elapsed:.1f}s", columns)
                detail, _ = _clip("  " + self.label, columns - used)
                if self.color:
                    line = f"\033[1;36m{state}\033[0m\033[2m{detail}\033[0m"
                else:
                    line = state + detail
                self.stream.write("\r\033[2K" + line)
                self.stream.flush()
                self.drawn = True
            except (OSError, ValueError, UnicodeError):
                # A vanished terminal must not turn successful inference into failure.
                return
            frame += 1

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
        if self.drawn:
            try:
                self.stream.write("\r\033[2K")
                self.stream.flush()
            except (OSError, ValueError):
                pass
        return False
