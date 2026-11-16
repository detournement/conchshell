# conchshell: source via conchshell init zsh. Suggestions are never executed.
ask() {
    command conchshell ask --shell zsh "$@"
}

_conchshell_suggest() {
    emulate -L zsh
    local request="$BUFFER" suggestion REPLY
    zle -I
    if [[ -z "$request" ]]; then
        # ZLE disconnects stdin; its minibuffer handles editing and cancellation.
        autoload -Uz read-from-minibuffer
        if ! read-from-minibuffer 'ask: '; then
            zle reset-prompt
            return 0
        fi
        request="$REPLY"
        if [[ -z "$request" ]]; then
            zle reset-prompt
            return 0
        fi
    fi
    if suggestion=$(command conchshell complete --shell zsh -- "$request"); then
        if [[ -n "$suggestion" ]]; then
            BUFFER="$suggestion"
            # ZLE offsets count characters, including under a UTF-8 locale.
            CURSOR=${#BUFFER}
        fi
    fi
    zle reset-prompt
    return 0
}

_conchshell_complete() {
    local previous="${words[CURRENT-1]}" subcommand="${words[2]}" i
    local -a choices
    for ((i=2; i<CURRENT; i++)); do
        [[ ${words[i]} != -- ]] || { _default; return; }
    done
    case "$previous" in
        --shell) compadd bash zsh; return ;;
        --provider) compadd openrouter openai anthropic ollama codex claude-code; return ;;
        -m|--model|--base-url|--timeout) _default; return ;;
    esac
    if [[ ${words[1]} == ask ]]; then
        choices=(--command --shell --model --timeout --help)
    elif (( CURRENT == 2 )); then
        choices=(ask complete init auth model config --help --version)
    else
        case "$subcommand" in
            ask) choices=(--command --shell --model --timeout --help) ;;
            complete) choices=(--shell --model --timeout --help) ;;
            init) choices=(bash zsh --help) ;;
            model) choices=(--show --help) ;;
            config) choices=(--provider --model --base-url --help) ;;
            auth)
                if (( CURRENT == 3 )); then
                    choices=(login status logout --help)
                else
                    choices=(openrouter openai anthropic ollama codex claude-code --help)
                    [[ ${words[3]} != login ]] || choices+=(--headless --model --base-url)
                fi
                ;;
        esac
    fi
    compadd -a choices || _default
}

if [[ -o interactive ]]; then
    zle -N _conchshell_suggest
    bindkey -M emacs '^G' _conchshell_suggest
    bindkey -M emacs '^@' _conchshell_suggest
    bindkey -M emacs '^[^[' _conchshell_suggest
    bindkey -M viins '^G' _conchshell_suggest
    bindkey -M viins '^@' _conchshell_suggest
    bindkey -M viins '^[^[' _conchshell_suggest
    if (( ! ${+functions[compdef]} )); then
        autoload -Uz compinit
        compinit
    fi
    if (( ${+functions[compdef]} )); then
        compdef _conchshell_complete conchshell ask
    fi
fi
