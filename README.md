# conchshell

Shell command suggestions and short answers. Nothing else.

A standalone extraction of the shell-helper idea from [Conch](https://github.com/detournement/conch). No chat session, agent tools, MCP, memory, scheduler, or background service. It does not import, configure, or modify Conch.

## Install

Requires **Python 3.9+** on macOS or Linux. The normal installer uses only the Python standard library: no pip, virtualenv, build step, or sudo.

Clone and install:

```sh
git clone https://github.com/detournement/conchshell.git
cd conchshell
sh install.sh
```

Open a new terminal, then choose a provider:

```sh
conchshell auth login              # OpenRouter browser sign-in
ask how do I find large files
```

The installer selects Bash or Zsh from `$SHELL`, installs a standalone zipapp, and adds a marked startup block. Existing startup files are backed up before the first edit. Reinstalling the same location is idempotent. Zsh honors `$ZDOTDIR`; Bash integrates both login and non-login shells.

Default paths:

- Executable: `~/.local/bin/conchshell`
- Application: `~/.local/share/conchshell/conchshell.pyz`
- Settings: `${XDG_CONFIG_HOME:-~/.config}/conchshell/config.json`
- API credentials: the same directory's `credentials.json`

The checkout can be removed after installation; Python must remain installed at its installation-time path. To choose an installation location or avoid startup edits:

```sh
sh install.sh --prefix "$HOME/tools/conchshell" --shell zsh
sh install.sh --no-shell
```

With `--no-shell`, add the installed `bin` directory to `PATH` and add one trusted initialization line to your shell's startup file:

```sh
eval "$(conchshell init zsh)"        # use bash for Bash
```

Only initialization code is evaluated. Model responses are never evaluated or executed. Do not source both Conch's and conchshell's shell helpers if you want an unambiguous `ask` function and keybindings; the last sourced helper wins. The full `conch` executable remains independent.

## Use

```sh
ask what does chmod 755 mean
ask --command find files larger than 100 MB
conchshell complete --shell zsh -- list hidden files
```

- `ask`: print a short answer; with no request, prompt for one.
- `ask --command`: print one command, without executing it.
- **Escape Escape**, **Ctrl+G**, or **Ctrl+Space**: use the current editable line as a request, or prompt if it is empty; replace the line with a suggested command. Review/edit it, then press Enter yourself.
- Tab: complete conchshell commands, provider names, and options. Existing native shell completion remains available.

Editable keybindings require **Zsh or Bash 4+**. Apple's Bash 3.2 supports `ask` and Tab completion, but not the editable-buffer shortcuts; use Zsh or install a newer Bash for those. Some terminals intercept Ctrl+Space; Escape Escape and Ctrl+G are alternatives.

In Zsh, the empty-line shortcut opens an editable `ask:` minibuffer; Ctrl+C cancels it. After updating an already-open terminal, reload the integration with `eval "$(conchshell init zsh)"` or open a new terminal.

Options go before the request. Use `--` before a request that starts with a dash. Request flags after its first word stay part of the request:

```sh
ask --timeout 30 explain ls --help
```

Failed requests leave the current line intact. Multiline commands, terminal controls, incomplete responses, and tool requests are rejected. A model can still suggest an incorrect or destructive command: **review every suggestion**.

## Sign in

| Provider | Command | Credentials and billing |
| --- | --- | --- |
| OpenRouter | `conchshell auth login` | Direct browser OAuth/PKCE; your OpenRouter credits pay for inference |
| ChatGPT subscription | `conchshell auth login codex` | Optional official Codex CLI owns login and refresh; subscription limits apply |
| Claude subscription | `conchshell auth login claude-code` | Optional official Claude Code CLI owns login and refresh; subscription limits apply |
| OpenAI API | `conchshell auth login openai` | Hidden API-key prompt; separately billed API usage |
| Anthropic API | `conchshell auth login anthropic` | Hidden API-key prompt; separately billed API usage |
| Ollama | `conchshell auth login ollama --model llama3.2` | No authentication; a running server and downloaded model are required |

OpenRouter's [documented OAuth flow](https://openrouter.ai/docs/guides/overview/auth/oauth) uses S256 PKCE, a random loopback callback and matching state. On SSH or another headless machine:

```sh
conchshell auth login openrouter --headless
```

Open the printed URL on your own machine, authorize, then paste the displayed code into the hidden prompt. Login expires after three minutes. No OpenRouter password or OAuth client secret is handled by conchshell.

Subscription adapters require installing the official [Codex CLI](https://developers.openai.com/codex/cli/) or [Claude Code CLI](https://code.claude.com/docs/en/setup) separately. They are **optional non-stdlib programs**, not Python dependencies. Conchshell never reads their token stores. Native requests use an empty temporary directory, disable tools/hooks/MCP/project configuration, and fail rather than fall back to unsafe execution when isolation flags are unsupported. Their login/logout affects the official CLI's shared account session.

```sh
conchshell auth status             # credential presence, not a model-access probe
conchshell auth status codex
conchshell auth logout openrouter
```

API logout removes the locally saved credential, not environment variables or the remote key. Revoke an OpenRouter-issued key in your OpenRouter account if needed. API credentials are plaintext in a mode-0600 file inside a mode-0700 directory; subscription credentials remain provider-managed.

## Models and endpoints

Choose a saved model interactively, or inspect it without prompting:

```sh
conchshell model
conchshell model --show
```

Enter a model ID offered by your selected provider. Enter keeps the current choice; Ctrl+C cancels. Selection is saved for both `ask` and Escape-triggered suggestions. It does not change provider, credentials, or endpoint, and model access is checked only when you make a request. For Codex, `default` lets the official CLI choose its default model.

Set an explicit model directly, or override it for just one request:

```sh
conchshell config --provider openai
conchshell model gpt-4o-mini
ask --model gpt-4o-mini what does chmod do
```

`ask -m MODEL` and `conchshell complete --model MODEL` also work. Per-request options take precedence over `CONCHSHELL_MODEL`, then the saved model, then the provider default; they do not change saved settings. `conchshell model` reports environment overrides and edits the saved provider's selection, never persisting a temporary environment provider or endpoint.

Advanced provider/endpoint configuration remains available:

```sh
conchshell config
conchshell config --provider openai --model gpt-4o-mini
conchshell config --provider ollama --model llama3.2 --base-url http://127.0.0.1:11434
```

Provider changes reset the previous provider's model and endpoint. `--base-url ''` restores the default endpoint. API roots for OpenAI/OpenRouter/Anthropic include `/v1`; Ollama's root does not include `/api`. HTTPS is required except on loopback; HTTP redirects are refused to avoid forwarding credentials.

Environment overrides: `CONCHSHELL_PROVIDER`, `CONCHSHELL_MODEL`, `CONCHSHELL_BASE_URL`. API keys can instead come from `OPENROUTER_API_KEY`, `OPENAI_API_KEY`, or `ANTHROPIC_API_KEY`; nonempty environment keys take precedence over saved keys. A provider override does not inherit a different provider's model or endpoint.

Conchshell sends your request and OS/shell type. It does not collect shell history, directory contents, files, or environment values as prompt context. Provider retention policies still apply.

## Update, remove, develop

Re-run `sh install.sh` from the updated checkout to replace the installed application. Changing the prefix of an existing startup block requires removing that old marked block first.

To uninstall, remove the `# >>> conchshell >>>` through `# <<< conchshell <<<` startup block, the installed executable and application directory. Remove the separate configuration directory only if you also want to discard settings and saved API credentials. No other Conch files are involved.

Run from source with `python3 -m conchshell`. Optional `pip install .` uses setuptools to build the package; the normal installer does not. Run focused offline regressions with:

```sh
python3 -m unittest discover -s tests -v
```

Tests use local HTTP services, not live provider accounts. Live browser sign-in requires your authorization. Shell and installer smoke checks have been exercised on macOS with Zsh and Bash 3.2; Bash 4+ widgets and live Claude/OpenRouter account flows require separate verification on those runtimes/accounts.
