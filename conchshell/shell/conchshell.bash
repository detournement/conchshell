# conchshell: source via conchshell init bash. Suggestions are never executed.
ask() {
    command conchshell ask --shell bash "$@"
}

_conchshell_suggest() {
    local request="$READLINE_LINE" suggestion
    if [[ -z "$request" ]]; then
        printf '\n'
        IFS= read -r -e -p 'ask: ' request || return 0
        [[ -n "$request" ]] || return 0
    else
        printf '\n'
    fi
    if suggestion=$(command conchshell complete --shell bash -- "$request"); then
        if [[ -n "$suggestion" ]]; then
            READLINE_LINE="$suggestion"
            # Readline offsets are bytes, not the locale's character count.
            local LC_ALL=C
            READLINE_POINT=${#READLINE_LINE}
        fi
    fi
    return 0
}

_conchshell_complete() {
    local current="${COMP_WORDS[COMP_CWORD]}" previous="${COMP_WORDS[COMP_CWORD-1]}"
    local subcommand="${COMP_WORDS[1]}" choices='' i
    COMPREPLY=()
    for ((i=1; i<COMP_CWORD; i++)); do
        [[ ${COMP_WORDS[i]} != -- ]] || return 0
    done
    case "$previous" in
        --shell) choices='bash zsh' ;;
        --provider) choices='openrouter openai anthropic ollama codex claude-code' ;;
        -m|--model|--base-url|--timeout) return 0 ;;
    esac
    if [[ -z "$choices" ]]; then
        if [[ ${COMP_WORDS[0]} == ask ]]; then
            choices='--command --shell --model --timeout --help'
        elif (( COMP_CWORD == 1 )); then
            choices='ask complete init auth model config --help --version'
        else
            case "$subcommand" in
                ask) choices='--command --shell --model --timeout --help' ;;
                complete) choices='--shell --model --timeout --help' ;;
                init) choices='bash zsh --help' ;;
                model) choices='--show --help' ;;
                config) choices='--provider --model --base-url --help' ;;
                auth)
                    if (( COMP_CWORD == 2 )); then
                        choices='login status logout --help'
                    else
                        choices='openrouter openai anthropic ollama codex claude-code --help'
                        [[ ${COMP_WORDS[2]} != login ]] || choices="$choices --headless --model --base-url"
                    fi
                    ;;
            esac
        fi
    fi
    COMPREPLY=( $(compgen -W "$choices" -- "$current") )
}

if [[ $- == *i* ]]; then
    complete -o bashdefault -o default -F _conchshell_complete conchshell ask
    # Bash 3.2 has no writable Readline buffer API; ask/Tab still work there.
    if (( BASH_VERSINFO[0] >= 4 )); then
        bind -m emacs-standard -x '"\C-g":_conchshell_suggest'
        bind -m emacs-standard -x '"\C-@":_conchshell_suggest'
        bind -m emacs-standard -x '"\e\e":_conchshell_suggest'
        bind -m vi-insertion -x '"\C-g":_conchshell_suggest'
        bind -m vi-insertion -x '"\C-@":_conchshell_suggest'
        bind -m vi-insertion -x '"\e\e":_conchshell_suggest'
    fi
fi
