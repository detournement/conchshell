# Changelog

## 0.1.0

- Independent stdlib-only shell helper: short-answer `ask`, single-command suggestions, Bash/Zsh completion and editable shortcuts.
- Direct OpenRouter PKCE browser/headless sign-in; OpenAI/Anthropic API keys; local Ollama; optional isolated Codex/Claude subscription clients.
- Private, separate configuration; no Conch runtime or configuration dependency.
- Standalone zipapp installation with quoted paths, startup backups, and idempotent reinstall.
- Preserve completed Codex responses after nonfatal diagnostic events while rejecting failed turns and native tool attempts.
- Fix empty-line Zsh shortcuts silently exiting: use Zsh's native minibuffer instead of reading disconnected widget stdin; verify delayed Escape Escape and cancellation in a real PTY.
- Keep successful Codex suggestions quiet instead of printing advisory diagnostics into the shell prompt; malformed responses, failed turns, and native tool attempts still fail visibly.
- Add `conchshell model [MODEL]` for saved model selection, an interactive model-ID prompt, and `--show`; add `ask`/`complete --model` (`-m`) for one-off overrides without changing saved settings.
- Add a stdlib-only animated Thinking indicator with elapsed time and provider/model context; keep captured output clean and clear progress on completion, errors, and cancellation.
