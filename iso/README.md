# Anser ISO

The builder packages the Omarchy sources in `system/` and adds the live
installer from `iso/configs/airootfs/`. The installer writes a Btrfs image to the
target and grows the filesystem. **Try live** uses the same image read-only
with a writable layer in RAM. Logging out returns to the installer.

## Build

On an x86_64 Arch host, the builder uses Bubblewrap and a subordinate UID and
GID range configured for `unshare --map-auto`. It uses an Arch build root in
`.build/root` with Archiso, GRUB for BIOS and UEFI, and the tools for building
the Btrfs and SquashFS images.

```sh
./iso/builder/run fetch
./iso/bin/anser-iso-make
```

Fetch fills the signed package cache and saves the repository indexes. The
build uses that cache offline and produces `out/anser-YYYY.MM.DD.iso`.
Without `--arch`, the builder selects the machine’s architecture. On x86_64,
`--arch x86_64` selects the same build explicitly.

### Experimental ARM64 build

For generic ARM64 UEFI machines, run the build on Arch Linux ARM or in an
ARM64 VM. Prepare `.build/root-aarch64` with the same build tools compiled for
ARM64, including `grub` with its `arm64-efi` modules, the Arch Linux ARM
keyring, and the Archiso script and baseline profile.

```sh
./iso/builder/run fetch --arch aarch64
./iso/bin/anser-iso-make --arch aarch64
```

On x86_64, `./iso/bin/anser-iso-make --arch aarch64` can use a prepared ARM64 VM
through the local launcher at `.build/arm64-builder/run-vm`. The launcher
receives the source tarball through `ANSER_ARM64_VM_SOURCE` and returns the
ISO through `ANSER_ARM64_VM_OUTPUT`. The guest needs its own ARM64 tools and
package cache.

The ARM64 build uses its own package cache and creates
`out/anser-YYYY.MM.DD-aarch64.iso`. To boot it in QEMU with ARM64 UEFI, use
`iso/test/arm64-live-boot out/anser-YYYY.MM.DD-aarch64.iso`. Add `--window` for the graphical console.
On x86_64 this runs with software CPU emulation.

## Install

A full-disk UEFI installation creates a 2 GiB EFI partition for Limine and a
Btrfs root with LUKS encryption by default. Omarchy mounts `@`, `@home`,
`@log` and `@pkg` and creates a read-only factory snapshot. **Keep /home**
checks the existing layout and account, then stages a new root while
retaining the home subvolume. It asks for the existing LUKS passphrase on
encrypted disks.

## Test

The integration test uses a disposable UEFI VM with 4 vCPUs, 4 GiB of RAM
and no network adapter. It requires KVM, OVMF, Pillow and Tesseract.

```sh
iso/test/integration out/anser-YYYY.MM.DD.iso .build/install
iso/test/integration out/anser-YYYY.MM.DD.iso .build/install --preserve
iso/test/integration out/anser-YYYY.MM.DD.iso .build/preview --try-live
```

The installer adapts Omarchy 4.5 and the preview and home preservation
changes from Remigante at `59822a4`. The welcome screen also follows the
upstream fix for console resizing. [Omarchy ISO source](https://github.com/omacom/omarchy-iso/tree/d5136a69782c00fe8d53fc98e4f3a990447d4840) · [Console resize fix](https://github.com/omacom/omarchy-iso/commit/a12820c0a487d688ea67db59fe5c96f4d4821884) · [MIT license](../LICENSE) · [Package recipe license](builder/recipes/LICENSE)
