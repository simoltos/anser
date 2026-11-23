# Developing the shared Omarchy code

These conventions apply to the shared Omarchy sources. Source paths and test commands below are relative to the `system/` directory in Anser. The initial import follows [Omarchy v4.5.0, commit ac2690c](https://github.com/omacom/omarchy/blob/ac2690cd7be4f47e4a255f5e2e78d904d436b068/AGENTS.md).

## Task guides

Read the matching guide before starting work.

- Adding or changing a command in `bin/` — [Command metadata](agents/skills/command-metadata.md)
- Editing `install/` or a system or user setup command — [Install scripts](agents/skills/install-scripts.md)
- Adding or changing tools managed by mise — [mise tools](agents/skills/mise-tools.md)
- Editing the Quickshell desktop in `shell/` — [Shell development](agents/skills/shell-dev.md)
- Adding a branded glyph to `default/fonts/omarchy/omarchy.ttf` — [Icon font](agents/skills/icon-font.md)
- Writing or running graphical tests in `test/acceptance.d/` — [Acceptance tests](agents/skills/acceptance-tests.md)
- Changing an animation or transition in `shell/` — [Motion](agents/skills/motion.md)
- Verifying a change with a visible effect in the running UI — [Visual verification](agents/skills/visual-verification.md)
- Creating or changing a migration in `migrations/` — [Migrations](agents/skills/migrations.md)

## Documentation layout

- `agents/skills/` contains task guides for contributors.
- `docs/` explains the implementation, including file layout, updates, theming and shell architecture. Task guides link here for technical detail.
- `manual/` contains the published end-user manual. Write about using Omarchy here and put codebase internals in `docs/`.

## Style

- In Markdown under `plans/`, `docs/` and `manual/`, keep paragraphs on full lines. Break at structural boundaries such as headings and list items.
- Indent with two spaces.
- Use Bash 5 conditionals. Use `[[ ]]` for string and file tests and `(( ))` for numeric tests, for example `(( count < 50 ))`.
- In `[[ ]]`, leave variable operands unquoted and quote string literals in comparisons, for example `[[ $branch == "dev" ]]`.
- Use a full `if`/`else` for a simple two-path decision. Both paths should be clear without relying on an `exec` or `exit` to make later statements unreachable.
- Quote paths with spaces, for example `"$APP_DIR/Disk Usage.desktop"`.
- Bash entry points use `#!/bin/bash`. The exact `#!/bin/bash -p` form is reserved for security-sensitive entry points that must suppress `BASH_ENV` and exported-function startup injection before the first command. Explain that exception at the entry point and cover it with a regression test that distinguishes privileged mode from a normal Bash launch with `-p` passed as a script argument.
- Scripts under `install/` and `migrations/` may be sourced and intentionally omit shebangs.

## Command naming

Omarchy commands start with `omarchy-`. Their prefixes describe their purpose.

The authoritative list of user-facing command groups is `GROUP_DESCRIPTIONS` in [bin/omarchy](bin/omarchy). Consult it when selecting a prefix and update it when adding a group users should be able to browse. Adding a group to the table advertises it in the top-level listing. Omit groups whose commands are all marked `# omarchy:hidden=true`. The `apply-` and `provision-` commands still route, and `omarchy <group>` can print a group header without a table entry.

Common prefixes include the following.

- `cmd-` checks command availability and provides command-line utilities.
- `capture-` takes screenshots and screen recordings.
- `pkg-` manages packages.
- `hw-` detects hardware through exit codes that scripts can test.
- `refresh-` restores default configuration in `~/.config/`.
- `restart-` restarts a component.
- `launch-` opens applications.
- `install-` installs optional software.
- `setup-` runs interactive setup.
- `toggle-` switches features on or off.
- `theme-` manages themes.
- `update-` updates components.

## Runtime environment

The uwsm session sets `$OMARCHY_PATH` for Omarchy runtime code. Commands in `bin/` should use that value directly. Quickshell QML should use `Quickshell.env("OMARCHY_PATH")`. Leave initialization to the session environment and avoid deriving a fallback from `HOME` or `Quickshell.shellDir`.

## Platforms

Omarchy identifies a platform by CPU architecture and adds a hardware family when it needs separate handling.

- `x86` covers all x86_64 machines.
- `aarch64` covers ARM machines without a family of their own, including Raspberry Pis and ARM VMs in the 4.5 tree.
- `aarch64-apple` covers Apple Silicon Macs.
- `aarch64-qualcomm` covers Snapdragon laptops.
- `aarch64-n1x` covers NVIDIA N1x laptops.
- `aarch64-gb10` covers NVIDIA GB10 desktops such as the DGX Spark and ASUS Ascent GX10.

Add a family as `aarch64-<family>` once it needs its own code, so `aarch64*` continues to cover ARM. Use the exact names returned by `omarchy-hw-platform` in package lists, Pacman template directories, image manifests, dispatch registration, tests and documentation. Old spellings such as `apple-silicon`, `generic-aarch64` and `generic` are read for compatibility with older builders. Use the canonical names in new code, including `aarch64` for ARM.

Use the hardware helpers in feature code. Leave `uname -m` and device-tree detection to those helpers.

- Use `omarchy-hw-x86` and `omarchy-hw-aarch64` for architecture and ABI decisions such as package availability, x86 guests and multilib.
- Use the image-aware family predicates `omarchy-hw-aarch64-apple`, `omarchy-hw-aarch64-qualcomm`, `omarchy-hw-aarch64-n1x` and `omarchy-hw-aarch64-gb10` for behaviour shared by a family.
- Gate a single-model quirk with `omarchy-hw-match` on its DMI name. Where models share that name, use `omarchy-hw-match-device-tree` with the device-tree compatible string. The hidden aliases `omarchy-hw-apple-silicon` and `omarchy-hw-n1x` serve older images and omarchy-mac packages.

Keep platform-specific code with the corresponding component.

- `omarchy-pkg-defaults` composes `install/omarchy-base.packages`, `install/omarchy-aarch64.packages` and the relevant `install/omarchy-<platform>.packages`. Base packages without an aarch64 build belong in `install/omarchy-x86_64-only.packages`.
- Pacman templates live in `default/pacman/` for x86 and `default/pacman/<platform>/` for ARM platforms.
- Put hardware setup in `install/hardware/<vendor-or-family>/` and gate it with a predicate. `install/hardware/apple/` covers Intel Mac quirks. Apple Silicon setup ships in the omarchy-mac packages.
- Platforms using Limine with unified kernel images follow the generic boot path. A boot chain Omarchy cannot drive generically belongs in a platform package behind `omarchy-lifecycle-dispatch`. [Lifecycle dispatch](docs/lifecycle-dispatch.md)

Aim for the same experience on every platform. When something is missing or broken, identify the gap and seek an upstream build, bug fix or maintainer patch first. Explain that option when asked for a workaround. Hide a menu entry or skip a package only for features that cannot exist on the platform, such as an x86 guest VM or an x86-only proprietary binary. If upstream cannot or will not fix the issue and a platform-specific replacement is necessary, record the reason beside the code.

## Privileged commands

Choose `sudo` or `pkexec` according to whether the caller has a terminal for entering a password. Follow the same rule in repository scripts. [Privilege escalation](default/agents/skills/omarchy/SKILL.md#privilege-escalation)

## Git

The [repository rules](../AGENTS.md#repository-rules) apply to commits that touch shared code.

## Helper commands

- Check optional commands with `omarchy-cmd-missing` or `omarchy-cmd-present`.
- Check packages with `omarchy-pkg-missing` or `omarchy-pkg-present` when a separate check is necessary. For an installation or removal, call `omarchy-pkg-add` or `omarchy-pkg-drop` directly.
- `omarchy-pkg-add` installs from configured Pacman repositories, including Omarchy's. Use `omarchy-pkg-aur-add` for AUR packages.
- Use `omarchy-pkg-drop` for removal and `omarchy-notification-send` for desktop notifications.
- Use hardware helpers such as `omarchy-hw-asus-rog` for device checks.

Commands in the default package set are available at runtime. Invoke them directly. Reserve command-presence checks for optional dependencies and code that runs before the default set is installed. Migrations and package-helper implementations may need direct package-manager operations, especially before the helpers themselves are available.

## Background work

The shell, daemons and watchers should react to events. Use polling only where there is no signal for a state change. [Shell development](agents/skills/shell-dev.md)

`test/shell.d/shell-idle-spawn-test.sh` fails if an idle shell starts a process. The router, menu guards and theme switch also have performance budgets. [Performance guards](docs/testing.md#performance-guards)

## Menu

The menu is defined in `default/omarchy/omarchy-menu.jsonc`. Reserve `aliases` for established alternate names that users already type. Leave the field out of new entries. [Menu schema, guards and providers](docs/menu.md)

## Keybindings

Choose the keybinding plane by what the binding does. Preserve existing bindings when adding one and use `Alt` only for a variant of the same binding without it. Apply these rules when reviewing both new and changed bindings. [Hotkey layout](manual/07-hotkeys.md#how-hotkeys-are-laid-out)

## Config structure

- `config/` contains defaults copied into the user's `~/.config/`.
- `default/themed/*.tpl` contains templates with `{{ variable }}` placeholders for theme colors.
- `themes/*/colors.toml` defines the accent, background, foreground and the red, green, yellow, blue, magenta and cyan colors with their bright variants.

## Tests

Run focused tests for the area you changed. The shared system has three main entry points. [Testing reference](docs/testing.md)

- `./test/all` runs the CLI and shell suites. Graphical acceptance has a separate runner.
- `./test/cli` covers command routing, metadata, theme helpers and safe dispatch.
- `./test/shell` runs the shell tests in `test/shell.d/`.

For a change to one plugin or command, run its test files directly. Include `test/shell.d/i18n-test.sh` when catalog strings change. Run the full shell or aggregate suite once at the end when a change reaches shared code such as `shell/Commons/`, `test/shell.d/base-test.sh`, a runner or a widely used helper in `bin/`.

Save the complete test output and inspect the log, for example with `bash test/shell.d/<area>-test.sh >/tmp/<area>.log 2>&1`. Piping the run through `grep`, `head` or `tail` discards evidence. The shell runner already saves every file's output and prints the log directory at the end.

Rerun a test after changing something it covers. Use its saved output to investigate a pass or failure. For a failure in a file you did not touch, run that file once on the base branch using a worktree or stash. Report an existing failure separately. Tests such as `require_compositor` consumers and `shell-idle-spawn-test.sh` observe the live desktop. If one fails during a full run and passes in isolation, report the session interference and stop rerunning it.

Before adding a test, identify a regression that existing coverage would miss.

- Small declarative changes such as a menu row, config value, package-list entry, string or rename need no dedicated test.
- Exercise observable behaviour. Grepping for the line just edited, asserting that a value equals itself, or rereading a config only repeats the implementation.
- Verify that the test would fail without the fix. Revert the fix locally or reason through the failing case.
- Exercise the real implementation when using a fixture. Behaviour supplied entirely by a test stub does not establish a regression.
- Avoid large snapshots, file lists or exact counts that break on unrelated additions.
- Find the area's existing test with `git grep` and extend it in its own style. Keep the size of the test proportional to the change.
- A migration runs once and is frozen after release. Move logic needing a test into a command in `bin/`.
- Test parsing, routing, hardware guards and edge cases in real logic. A bug fix gets the focused case that fails on the base branch and passes with the fix.

Place new shell tests in `test/shell.d/*-test.sh` so the shell runner picks them up. Source `test/shell.d/base-test.sh` for path discovery, assertions and Node helpers.

Run graphical acceptance in a disposable VM. [Acceptance tests](agents/skills/acceptance-tests.md)

Verify visible changes in the running UI as well as with automated tests. [Visual verification](agents/skills/visual-verification.md)

## Refresh pattern

This command restores the shipped Hyprland config and backs up the user's current copy.

```bash
omarchy-refresh-config hypr/hyprland.lua
```

Pass a plain path relative to the configuration directory. The helper copies `$OMARCHY_PATH/config/hypr/hyprland.lua` to `~/.config/hypr/hyprland.lua` for this example.
