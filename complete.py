"""mule completion: print a shell completion script."""


def completion_script(shell, flags, models):
    # flags and models come from the real parser and pricing table,
    # so the scripts never drift from the actual cli
    if shell == "bash":
        return _bash(flags, models)
    if shell == "zsh":
        return _zsh(flags, models)
    if shell == "fish":
        return _fish(flags, models)
    raise ValueError("unknown shell: %s (try bash, zsh, or fish)" % shell)


SUBCOMMANDS = "init config doctor completion models"


def _bash(flags, models):
    return """# mule bash completion: source this or drop it in /etc/bash_completion.d/
_mule_complete() {
    local cur prev
    COMPREPLY=()
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"
    local subcommands="%s"
    local flags="%s"
    local models="%s"
    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=($(compgen -W "$subcommands $flags" -- "$cur"))
        return 0
    fi
    case "$prev" in
        completion)
            COMPREPLY=($(compgen -W "bash zsh fish" -- "$cur"))
            return 0 ;;
        config)
            COMPREPLY=($(compgen -W "get set unset list" -- "$cur"))
            return 0 ;;
        --model)
            COMPREPLY=($(compgen -W "$models" -- "$cur"))
            return 0 ;;
    esac
    COMPREPLY=($(compgen -W "$flags" -- "$cur"))
}
complete -F _mule_complete mule
""" % (SUBCOMMANDS, " ".join(flags), " ".join(models))


def _zsh(flags, models):
    return """#compdef mule
# mule zsh completion: drop this in a directory on your $fpath
_mule() {
    local -a subcommands flags models
    subcommands=(%s)
    flags=(%s)
    models=(%s)
    if (( CURRENT == 2 )); then
        _describe 'command' subcommands
        _describe 'flag' flags
        return
    fi
    case "$words[2]" in
        completion) _describe 'shell' '(bash zsh fish)' ;;
        config) _describe 'action' '(get set unset list)' ;;
        --model) _describe 'model' models ;;
        *) _describe 'flag' flags ;;
    esac
}
_mule "$@"
""" % (SUBCOMMANDS, " ".join(flags), " ".join(models))


def _fish(flags, models):
    lines = ["# mule fish completion: drop this in ~/.config/fish/completions/",
             "complete -c mule -f"]
    for sub in SUBCOMMANDS.split():
        lines.append("complete -c mule -n __fish_use_subcommand "
                     "-a %s" % sub)
    for flag in flags:
        lines.append("complete -c mule -l %s" % flag.lstrip("-"))
    lines.append("complete -c mule -n '__fish_seen_subcommand_from completion' "
                 "-a 'bash zsh fish'")
    lines.append("complete -c mule -n '__fish_seen_subcommand_from config' "
                 "-a 'get set unset list'")
    lines.append("complete -c mule -n '__fish_seen_argument -l model' "
                 "-a '%s'" % " ".join(models))
    return "\n".join(lines) + "\n"
