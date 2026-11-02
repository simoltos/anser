# shellcheck shell=bash
# Apply the distribution prompt only to interactive shells using a stock prompt.
[[ $- == *i* && ${TERM:-dumb} != dumb ]] || return 0
case $PS1 in
    '\s-\v\$ '|'[\u@\h \W]\$ ')
        _anser_default_prompt='\[\e[1;38;2;51;209;122m\]\u@\h:\w\$\[\e[0m\] '
        PS1=$_anser_default_prompt
        _anser_first_prompt=1
        _anser_prompt_spacing() {
            local status=$?
            # Ghostty may wrap PS1 with its own nonprinting integration marks.
            # A personal prompt that replaces ours must keep its own spacing.
            if [[ $PS1 == *"$_anser_default_prompt"* ]]; then
                if (( _anser_first_prompt )); then
                    _anser_first_prompt=0
                else
                    printf '\n'
                fi
            fi
            return "$status"
        }
        PROMPT_COMMAND+=(_anser_prompt_spacing)
        ;;
esac
