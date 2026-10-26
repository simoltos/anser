# Repository Guidelines

See [README.md](README.md) for the project layout, binary repositories and
signing key.

## Package Changes

Pin public sources to release archives or full upstream commit IDs and verify
their checksums. Keep patches beside their `PKGBUILD` in `packages/<pkgbase>/`
and apply them in `prepare()`.

Update `pkgver` for upstream changes and `pkgrel` for packaging changes at the
same version. Rebuilding unchanged recipes does not require a bump. Preserve
upstream licenses and notices and keep dependencies of split packages consistent.

Build updates for testing; promote tested binaries to stable without rebuilding.
Do not replace published package files under the same version.

## Integration & Repositories

Keep Anser-specific setup in `integration/` and identity and presentation changes
in `branding/`. `packages.x86_64` selects the installed system. Remove Cenere and
tools used only in the live environment during installation, preserving shared
dependencies. OCA remains optional and outside this manifest.

Use the public URLs in `pacman.conf` by default. Local repositories require
explicit selection and must not replace the configuration delivered in the image.
Keep package and database signatures mandatory for Anser repositories.

## Validation

Read and follow `local/AGENTS.md` for builds and validation. If the local
instructions or tools are unavailable, state which checks could not be run.

Report the checks actually performed and their results, distinguishing package
builds, filesystem checks, VM boots and installation tests.
