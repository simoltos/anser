#!/bin/bash
set -euo pipefail

root=$1
container=$2
output=$3

# Build the filesystem in userspace without attaching a loop device on the host.
mkdir -p "$container/tree/omarchy-root"
truncate -s 20G "$container/omarchy-root.img"
bwrap --ro-bind "$root" / --dev /dev --proc /proc --tmpfs /tmp \
    --bind "$container" /mnt --ro-bind "$root" /mnt/tree/omarchy-root \
    /usr/bin/mkfs.btrfs -q -f --rootdir /mnt/tree --subvol rw:omarchy-root \
        --compress zstd:3 --shrink /mnt/omarchy-root.img

/usr/bin/mksquashfs "$container/omarchy-root.img" "$output" -noappend -processors 2 -mem 256M \
    -no-fragments -no-xattrs -comp zstd -Xcompression-level 3 -b 256K -no-progress -quiet
