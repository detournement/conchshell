import unittest

from conchshell.cli import _clean_response, parser
from conchshell.errors import ConchError


class ResponseSafetyTests(unittest.TestCase):
    def test_terminal_control_sequences_never_reach_shell(self):
        for text in ("echo ok\x1b[2J", "echo ok\rmalicious", "echo \x00", "echo \u202eevil"):
            for command in (False, True):
                with self.subTest(text=text, command=command), self.assertRaises(ConchError):
                    _clean_response(text, command)

    def test_multiline_command_is_not_insertable(self):
        for text in ("echo safe\necho unsafe", "echo safe\u2028echo unsafe", "```sh\necho a\necho b\n```", "echo\tunsafe"):
            with self.subTest(text=text), self.assertRaises(ConchError):
                _clean_response(text, True)

    def test_single_fenced_command_is_unwrapped_without_execution(self):
        command = "printf '%s' '$(touch never-execute)'"
        self.assertEqual(_clean_response("```bash\n" + command + "\n```", True), command)

    def test_answers_keep_explanations_and_unicode(self):
        self.assertEqual(_clean_response("  Use `ls -a`.\nIt includes café files.  ", False),
                         "Use `ls -a`.\nIt includes café files.")

    def test_request_flags_are_not_consumed_as_helper_options(self):
        args = parser().parse_args(["ask", "explain", "ls", "--help"])
        self.assertEqual(args.request, ["explain", "ls", "--help"])
        args = parser().parse_args(["complete", "--", "--help"])
        self.assertEqual(args.request, ["--", "--help"])


if __name__ == "__main__":
    unittest.main()
