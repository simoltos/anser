#!/usr/bin/env bash
#
# Live ISO entry point on tty1: set up the live VT, run the configurator
# wizard, then hand off to the Python install orchestrator. Mirrors the
# stream/env contract from the previously-working installer:
#   - stdout teed to /var/log/omarchy-install.log (CSI-stripped) AND to tty
#   - stderr direct to /dev/tty so gum (which draws its TUI on stderr)
#     renders correctly
#   - CLICOLOR_FORCE/FORCE_COLOR so gum emits ANSI even with stdout piped
#   - COLUMNS/LINES so gum picks up real terminal size
set -euo pipefail

[[ $(tty) == /dev/tty1 ]] || exit 0

# The N1x recovery boot entry is a diagnostic shell, never the installer: record
# the hardware probe, show how to reach the machine, and leave tty1 at a prompt.
if grep -qw 'omarchy.n1x_recovery=1' /proc/cmdline; then
  /usr/local/sbin/omarchy-n1x-live-probe || true
  exit 0
fi

export OMARCHY_MIRROR="$(cat /root/omarchy_mirror)"
if [[ -f /root/omarchy_iso_ref ]]; then
  export OMARCHY_ISO_REF="$(cat /root/omarchy_iso_ref)"
fi
if [[ -f /usr/share/omarchy-iso/package-targets ]]; then
  # shellcheck disable=SC1091
  source /usr/share/omarchy-iso/package-targets
  export OMARCHY_RUNTIME_PACKAGE OMARCHY_SETTINGS_PACKAGE OMARCHY_NVIM_PACKAGE
fi
export OMARCHY_PATH=/usr/share/omarchy
export OMARCHY_INSTALL=$OMARCHY_PATH/install
export OMARCHY_INSTALL_LOG_FILE=/var/log/omarchy-install.log
if [[ -f /usr/share/omarchy-iso/install-debug ]]; then
  export OMARCHY_INSTALL_DEBUG=1
fi

# gum's key hints ("enter submit") are hardcoded English and add nothing to
# prompts that already say what to do, so every widget leaves them off.
export GUM_CHOOSE_SHOW_HELP=false GUM_CONFIRM_SHOW_HELP=false GUM_FILTER_SHOW_HELP=false GUM_INPUT_SHOW_HELP=false

# Tokyo Night palette so the live VT matches the installed look.
set_tokyo_night_colors() {
  echo -en "\e]P01a1b26"; echo -en "\e]P1f7768e"; echo -en "\e]P29ece6a"
  echo -en "\e]P3e0af68"; echo -en "\e]P47aa2f7"; echo -en "\e]P5bb9af7"
  echo -en "\e]P67dcfff"; echo -en "\e]P7a9b1d6"; echo -en "\e]P8414868"
  echo -en "\e]P9f7768e"; echo -en "\e]PA9ece6a"; echo -en "\e]PBe0af68"
  echo -en "\e]PC7aa2f7"; echo -en "\e]PDbb9af7"; echo -en "\e]PE7dcfff"
  echo -en "\e]PFc0caf5"
  echo -en "\033[0m"
  clear
}
set_tokyo_night_colors

mkdir -p /var/log
touch "$OMARCHY_INSTALL_LOG_FILE"

export COLUMNS=$(tput cols)
export LINES=$(tput lines)
exec > >(tee >(sed -u 's/\x1b\[[0-9;?]*[A-Za-z]//g' >>"$OMARCHY_INSTALL_LOG_FILE") 2>/dev/null) 2>/dev/tty
export CLICOLOR_FORCE=1
export FORCE_COLOR=1

if [[ ${OMARCHY_INSTALL_DEBUG:-} == "1" ]]; then
  echo "=== Omarchy ISO debug build ==="
  [[ -f /usr/share/omarchy-iso/build-info ]] && cat /usr/share/omarchy-iso/build-info
  pacman -Q omarchy-settings omarchy-keyring 2>/dev/null || true
  echo "================================"
fi

# Interactive installations always start with the welcome screen.
./configurator

# Deferred-provisioning installs skip the celebration/reboot prompt and reboot on
# their own — the owner completes setup at first boot. Signalled by the config's
# defer_provisioning flag (interactive) or the defer-provisioning marker (cidata).
# Parse the flag with jq (the same JSON semantics the orchestrator uses) rather
# than a line regex, so a reformatted config can't read as a direct install.
if [[ -f /root/defer-provisioning ]] ||
  [[ "$(jq -r '.omarchy_install.defer_provisioning // false' /root/user_configuration.json 2>/dev/null)" == "true" ]]; then
  export OMARCHY_UI_DEFER_PROVISIONING=yes
fi

# The dashboard speaks the language picked in the configurator, or named by an
# autoinstall configuration.
OMARCHY_ISO_LANGUAGE="$(jq -r '.omarchy_install.language // "en"' /root/user_configuration.json 2>/dev/null || echo en)"
export OMARCHY_ISO_LANGUAGE

# The foreground dashboard is now the sole visible install UI owner. It starts
# the actual installer as a non-interactive child, logs child output, waits for
# completion, then renders the final installed-time/reboot prompt itself.
export OMARCHY_DASHBOARD_TTY="$(tty)"
rm -f /run/omarchy-install/state.json
/usr/local/bin/omarchy-install-dashboard \
  "$OMARCHY_INSTALL_LOG_FILE" \
  /run/omarchy-install/state.json \
  -- \
  /usr/local/bin/omarchy-iso-install \
    --config /root/user_configuration.json \
    --creds /root/user_credentials.json \
    --encrypt-file /root/user_encrypt_installation.txt \
    --authorized-keys-file /root/authorized_keys \
    --tailscale-authkey-file /root/tailscale_authkey \
    --defer-provisioning-file /root/defer-provisioning
