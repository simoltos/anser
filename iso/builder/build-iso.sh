#!/bin/bash
set -euo pipefail
[[ $PWD == /project && $EUID == 0 ]] || exit 2

arch=$ANSER_ARCH
pacman_profile=system/default/pacman
if [[ $arch == aarch64 ]]; then
    pacman_profile+=/aarch64
fi
work=$(mktemp -d "$PWD/$ANSER_BUILD_STATE/work/build-XXXXXX")
iso_output=''
trap 'rm -rf -- "$work" "$iso_output"' EXIT
iso_output=$(mktemp -d "$PWD/out/.build-XXXXXX")
profile=$work/profile
media=$work/media
baseline=/usr/share/archiso/configs/baseline
mkdir -p "$profile/airootfs" "$media/boot/grub"
python iso/builder/lib/packages.py prepare "$work"

# Only the initramfs needs the Archiso hook. Run it during kernel installation.
mkdir -p "$profile/airootfs/etc/initcpio/"{install,hooks}
for kind in install hooks; do
    bsdtar -xOf "$(<"$work/boot-package")" "usr/lib/initcpio/$kind/archiso" > "$profile/airootfs/etc/initcpio/$kind/archiso"
done
export ANSER_WORK="$work"
install -Dm644 iso/configs/initcpio/hooks/anser_base "$profile/airootfs/etc/initcpio/hooks/anser_base"
install -Dm644 iso/configs/initcpio/install/anser_base "$profile/airootfs/etc/initcpio/install/anser_base"
mkdir -p "$profile/airootfs/etc/mkinitcpio.conf.d"
modules='loop btrfs'
boot_options=quiet
live_hooks='base udev modconf archiso'
if [[ $arch == aarch64 ]]; then
    modules+=' virtio_pci? virtio_blk? virtio_scsi? virtio_gpu?'
else
    boot_options+=' splash plymouth.ignore-serial-consoles systemd.show_status=false'
    live_hooks+=' plymouth'
    live=iso/configs/airootfs
    cp -- "$live/etc/initcpio/hooks/udev" "$profile/airootfs/etc/initcpio/hooks/"
    install -Dm644 "$live/usr/share/plymouth/themes/omarchy/logo.png" "$profile/airootfs/etc/initcpio/anser/logo.png"
    cp -- "$live/etc/plymouth/plymouthd.conf" "$profile/airootfs/etc/initcpio/anser/"
fi
printf 'MODULES=(%s)\nHOOKS=(%s block filesystems anser_base)\n' "$modules" "$live_hooks" >"$profile/airootfs/etc/mkinitcpio.conf.d/archiso.conf"
install -Dm644 iso/builder/linux-omarchy.preset "$profile/airootfs/etc/mkinitcpio.d/linux-omarchy.preset"
install -Dm644 "$pacman_profile/pacman-stable.conf" "$profile/airootfs/etc/pacman.conf"
install -Dm644 "$pacman_profile/mirrorlist-stable" "$profile/airootfs/etc/pacman.d/mirrorlist"

export ANSER_HOOKS="$work/hooks"
mkdir -p "$ANSER_HOOKS"/{live,bootstrap}
bsdtar -xOf "$(<"$work/initcpio-package")" usr/share/libalpm/hooks/90-mkinitcpio-install.hook > "$ANSER_HOOKS/live/90-mkinitcpio-install.hook"
ln -s /dev/null "$ANSER_HOOKS/live/80-limine-efi-deploy.hook"
ln -s /dev/null "$ANSER_HOOKS/bootstrap/90-mkinitcpio-install.hook"
epoch=$(date +%s)
date_version=$(date -ud "@$epoch" +%Y.%m.%d)
iso_date=$(date -ud "@$epoch" +%Y%m%d%H%M%S00)
iso_uuid=$(date -ud "@$epoch" +%Y-%m-%d-%H-%M-%S-00)
label=ANSER_${date_version//./}
cp -- "$baseline/profiledef.sh" "$profile/"
export ANSER_ISO_VERSION="$date_version" ANSER_ISO_LABEL="$label"
cat iso/configs/profiledef.sh >> "$profile/profiledef.sh"
# Archiso inventories the guest before its keyring is initialized.
# shellcheck disable=SC2329
pacman() {
    if [[ $# == 3 && $1 == -Q && $2 == --sysroot ]]; then
        command pacman --config /dev/null --root "$3" -Q
    else
        command pacman "$@"
    fi
}
export -f pacman
mkarchiso -v -m netboot -w "$work/archiso" -o "$media" "$profile"
cmp "$work/packages.target" "$media/arch/pkglist.$arch.txt"
cp "$work/packages.live" "$media/arch/pkglist.$arch.txt"
# On a USB disk GRUB's ISO9660 and HFS+ views share a label. Select ISO9660 by UUID.
: > "$media/boot/$iso_uuid.uuid"
grub_platform=()
console=ttyS0
output_name="anser-$date_version.iso"
if [[ $arch == aarch64 ]]; then
    grub_platform=(-d /usr/lib/grub/arm64-efi)
    console=ttyAMA0
    output_name="anser-$date_version-aarch64.iso"
fi
sed -e "s/@ISO_UUID@/$iso_uuid/g" -e "s/@ARCH@/$arch/g" -e "s/@CONSOLE@/$console/g" -e "s/@BOOT_OPTIONS@/$boot_options/g" \
    iso/configs/grub/grub.cfg > "$media/boot/grub/grub.cfg"
install -m644 LICENSE "$media/LICENSE"
grub-mkrescue "${grub_platform[@]}" -o "$iso_output/$output_name" "$media" -iso-level 3 -volid "$label" "--modification-date=$iso_date"
mv -T -- "$iso_output/$output_name" "out/$output_name"
