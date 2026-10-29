# Anser

Anser is an Arch based distrubution that features the
[Honk](https://github.com/simoltos/honk) desktop environment and offline
installation through [Cenere](https://github.com/simoltos/cenere).

Download the latest [x86_64 ISO](https://anser-download.simoltos.workers.dev/latest).
[SHA256 checksum](https://anser-download.simoltos.workers.dev/latest.sha256) and
[GPG signature](https://anser-download.simoltos.workers.dev/latest.sig) are also available.

This repository contains the package recipes and distribution files
maintained for Anser:

- [packages/](packages/) contains the recipes and patches, including the
  changes needed to make Honk work with GNOME apps and services and to
  recognize Honk and HWin in fastfetch.
- [integration/](integration/) contains the Anser setup and live environment
  cleanup used by Cenere, along with support for testing local repositories
  in virtual machines.
- [branding/](branding/) contains the system identity, login screen branding
  and application menu customizations, with a pacman hook that preserves
  the Anser identity after package updates.

[packages.x86_64](packages.x86_64) defines the packages installed with Anser.
[pacman.conf](pacman.conf) defines the repositories and signature policy,
including Arch's `core`, `extra` and `multilib` repositories.

The binary repositories are hosted in GitHub releases:

- [anser](https://github.com/simoltos/anser/releases/tag/anser-x86_64) is the
  stable repository, enabled by default.
- [anser-testing](https://github.com/simoltos/anser/releases/tag/anser-testing-x86_64)
  contains packages to test before promotion to stable. It is disabled by
  default.

Both Anser repositories require signatures for packages and databases.
ISO images are signed with the same key.
The [public signing key](https://github.com/simoltos/anser/releases/download/anser-x86_64/public.asc)
has this fingerprint:

```text
035BC512082112AD67C7346B369CB9BAB7B3D215
```
