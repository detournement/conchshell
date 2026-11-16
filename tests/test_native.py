"""Behavioral regressions for the optional subscription CLI boundary."""

import io
import json
import signal
import subprocess
import unittest
from unittest.mock import Mock, patch

from conchshell import native
from conchshell.errors import ConchError


def stream(*events):
    return "\n".join(json.dumps(event) for event in events)


def claude_result(**changes):
    payload = {"type": "result", "subtype": "success", "is_error": False,
               "permission_denials": [], "structured_output": {"text": "answer"}}
    payload.update(changes)
    return json.dumps(payload)


class NativeResultTests(unittest.TestCase):
    def test_accepts_completed_text_only_results(self):
        native._codex_events(stream(
            {"type": "thread.started"}, {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "reasoning", "text": "thinking"}},
            {"type": "item.completed", "item": {"type": "agent_message", "text": "answer"}},
            {"type": "turn.completed"},
        ))
        self.assertEqual(native._claude_result(claude_result()), "answer")

    def test_codex_rejects_tools_even_when_turn_completes(self):
        for kind in ("command_execution", "file_change", "mcp_tool_call", "web_search",
                     "todo_list", "future_tool"):
            for stage in ("item.started", "item.updated", "item.completed"):
                with self.subTest(kind=kind, stage=stage), self.assertRaises(ConchError):
                    native._codex_events(stream(
                        {"type": stage, "item": {"type": kind}},
                        {"type": "turn.completed"},
                    ))

    def test_nonfatal_diagnostic_cannot_mask_failure(self):
        diagnostic = {"type": "item.completed", "item": {
            "type": "error", "message": "diagnostic with private-secret"}}
        for ending in ({"type": "turn.failed"}, {"type": "error"}, {"type": "turn.started"}):
            with self.subTest(ending=ending), self.assertRaises(ConchError):
                native._codex_events(stream(diagnostic, ending))

    def test_codex_rejects_failed_incomplete_or_unknown_streams(self):
        for output in ("", "not JSON", "[]", "NaN", stream({"type": "turn.started"}),
                       stream({"type": "error", "message": "private-secret"}),
                       stream({"type": "turn.failed"}), stream({"type": "unknown"}),
                       stream({"type": "item.completed", "item": None}),
                       stream({"type": "turn.completed"}, {"type": "turn.failed"})):
            with self.subTest(output=output), self.assertRaises(ConchError) as raised:
                native._codex_events(output)
            self.assertNotIn("private-secret", str(raised.exception))

    def test_claude_rejects_incomplete_errors_and_tool_denials(self):
        cases = [
            {"subtype": "error_max_turns"}, {"subtype": "error_during_execution"},
            {"is_error": True}, {"is_error": None}, {"type": "assistant"},
            {"permission_denials": [{"tool_name": "Bash", "tool_input": "private-secret"}]},
            {"errors": ["private-secret"]}, {"api_error_status": 429},
            {"tool_calls": [{"name": "Bash", "arguments": "private-secret"}]},
            {"message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
            {"structured_output": None}, {"structured_output": {"text": 3}},
            {"structured_output": {"text": ""}}, {"structured_output": {"text": "  "}},
            {"structured_output": {"text": "answer", "tool_calls": []}},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ConchError) as raised:
                native._claude_result(claude_result(**changes))
            self.assertNotIn("private-secret", str(raised.exception))

    def test_claude_rejects_nonfinal_event_sequences(self):
        for output in ("not JSON", "null", "[]", "NaN", stream(
                {"type": "assistant", "message": {"content": [{"type": "tool_use"}]}},
                json.loads(claude_result()))):
            with self.subTest(output=output), self.assertRaises(ConchError):
                native._claude_result(output)

    def test_api_logins_never_count_as_subscription_or_echo_identity(self):
        cases = [
            ("codex", "Logged in using an API key: private-secret"),
            ("codex", "Logged in using ChatGPT\nAPI key: private-secret"),
            ("claude-code", json.dumps({"loggedIn": True, "authMethod": "apiKey",
                                       "subscriptionType": "pro", "email": "private-secret"})),
            ("claude-code", json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                                       "subscriptionType": None, "email": "private-secret"})),
        ]
        for provider, output in cases:
            result = subprocess.CompletedProcess([], 0, output, "")
            with self.subTest(provider=provider), patch.object(native, "_run", return_value=result):
                ready, diagnosis = native.status(provider)
            self.assertFalse(ready)
            self.assertNotIn("private-secret", diagnosis)


class NativeProcessTests(unittest.TestCase):
    def process(self):
        process = Mock(pid=1234, returncode=-signal.SIGKILL)
        process.stdin, process.stdout, process.stderr = io.StringIO(), io.StringIO(), io.StringIO()
        return process

    def test_timeout_kills_group_reaps_leader_and_closes_pipes_without_redraining(self):
        process = self.process()
        process.communicate.side_effect = subprocess.TimeoutExpired("codex", 0.1)
        with patch.object(native.subprocess, "Popen", return_value=process), \
                patch.object(native.os, "killpg") as killpg, self.assertRaisesRegex(ConchError, "timed out"):
            native._run("codex", ["codex"], cwd="/", timeout=0.1, prompt="private prompt")
        killpg.assert_called_once_with(process.pid, signal.SIGKILL)
        process.wait.assert_called_once_with(timeout=1)
        self.assertEqual(process.communicate.call_count, 1)
        self.assertTrue(all(pipe.closed for pipe in (process.stdin, process.stdout, process.stderr)))

    def test_success_still_stops_surviving_helpers(self):
        process = self.process()
        process.returncode = 0
        process.communicate.return_value = ("response", "")
        with patch.object(native.subprocess, "Popen", return_value=process), \
                patch.object(native.os, "killpg") as killpg:
            result = native._run("codex", ["codex"], cwd="/", timeout=1)
        self.assertEqual(result.stdout, "response")
        killpg.assert_called_once_with(process.pid, signal.SIGKILL)
        process.wait.assert_called_once_with(timeout=1)

    def test_exited_group_does_not_hide_original_timeout(self):
        process = self.process()
        process.communicate.side_effect = subprocess.TimeoutExpired("codex", 1)
        with patch.object(native.subprocess, "Popen", return_value=process), \
                patch.object(native.os, "killpg", side_effect=ProcessLookupError), \
                self.assertRaisesRegex(ConchError, "timed out"):
            native._run("codex", ["codex"], cwd="/", timeout=1)
        process.wait.assert_called_once_with(timeout=1)

    def test_auth_preflight_consumes_the_overall_timeout(self):
        with patch.object(native.time, "monotonic", side_effect=[0, 2]), \
                patch.object(native, "_status", return_value=(True, "ready")), \
                patch.object(native, "_run") as run, self.assertRaisesRegex(ConchError, "timed out"):
            native.complete("claude-code", "sonnet", "instructions", "prompt", 1)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
