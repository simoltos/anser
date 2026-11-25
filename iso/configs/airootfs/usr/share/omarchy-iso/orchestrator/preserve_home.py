"""Reinstall into the standard Btrfs layout with the existing home subvolume."""

from __future__ import annotations

import filecmp
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from .command import capture, capture_identifier
from .context import InstallContext
from .ui import info

ESP_TYPE = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
SUBVOLUMES = {"/": "@", "/home": "@home", "/var/log": "@log", "/var/cache/pacman/pkg": "@pkg"}
BASE_SYSTEM = Path("/run/anser-base/system")
WORKSPACE = ".anser-reinstall"
PENDING_FACTORY = ".anser-new-factory"
MAPPER = "omarchy_root"


def _partitions(disk: str) -> list[dict]:
    output = capture_identifier(
        ["lsblk", "--list", "--json", "--paths", "-o",
         "PATH,TYPE,PKNAME,FSTYPE,UUID,PARTUUID,PARTTYPE,MOUNTPOINTS", "--", disk],
        f"partitions of {disk}",
    )
    return [row for row in json.loads(output)["blockdevices"]
            if row["type"] == "part" and row["pkname"] == disk and row["partuuid"]]


def _is_esp(part: dict) -> bool:
    return part["fstype"] == "vfat" and (part["parttype"] or "").lower() == ESP_TYPE


def _available(part: dict) -> bool:
    holders = Path("/sys/class/block") / Path(part["path"]).name / "holders"
    return not any(part["mountpoints"] or []) and not any(holders.iterdir())


def candidates(disk: str) -> list[dict]:
    if not Path("/sys/firmware/efi").is_dir() or not BASE_SYSTEM.is_mount():
        return []
    partitions = _partitions(disk)
    if not any(map(_is_esp, partitions)):
        return []
    return [part for part in partitions if part["fstype"] in {"btrfs", "crypto_LUKS"} and _available(part)]


def _open_root(part: dict, password: bytes, mapper: str, *, readonly: bool) -> str:
    if part["fstype"] == "btrfs":
        return part["path"]
    if not password:
        raise RuntimeError("Enter the existing LUKS passphrase.")
    args = ["cryptsetup", "open", "--type", "luks2", "--key-file", "-"]
    if readonly:
        args.append("--readonly")
    result = subprocess.run(
        [*args, part["path"], mapper], input=password, capture_output=True,
    )
    if result.returncode:
        raise RuntimeError(f"Could not unlock {part['path']}: {os.fsdecode(result.stderr).strip()}")
    return f"/dev/mapper/{mapper}"


@contextmanager
def _mount(device: str, path: Path, fstype: str, options: str):
    subprocess.run(["mount", "-t", fstype, "-o", options, device, path], check=True)
    try:
        yield path
    finally:
        subprocess.run(["umount", path], check=True)


def _subvolume(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink() and path.stat().st_ino == 256


def _subvolume_id(path: Path) -> int:
    return int(capture_identifier(
        ["btrfs", "inspect-internal", "rootid", str(path)], f"the subvolume ID of {path}"
    ))


def _matches(source: str, uuid: str, partuuid: str = "") -> bool:
    return bool(uuid and source.casefold() == f"UUID={uuid}".casefold()) or bool(
        partuuid and source.casefold() == f"PARTUUID={partuuid}".casefold()
    )


class HomeOwnerMismatch(RuntimeError):
    """The layout is recognizable, but the entered login has no matching home."""


def _inspect(top: Path, fs_uuid: str, partitions: list[dict], username: str) -> dict:
    if not username or "/" in username or username in {".", ".."}:
        raise HomeOwnerMismatch("Enter the existing account name.")
    if not all(_subvolume(top / name) for name in SUBVOLUMES.values()):
        raise RuntimeError("This disk does not have the standard Anser Btrfs layout.")
    etc = top / "@" / "etc"
    if etc.is_symlink() or any((etc / name).is_symlink() for name in ("fstab", "passwd")):
        raise RuntimeError("Could not verify the existing system's mount and account configuration.")

    mounts = {}
    for line in (etc / "fstab").read_text().splitlines():
        fields = line.partition("#")[0].split()
        if len(fields) < 4:
            continue
        source, target, fstype, options, *_ = fields
        if target in mounts:
            raise RuntimeError(f"The existing mount point {target} is ambiguous.")
        if target in SUBVOLUMES or target in {"/boot", "/efi"}:
            mounts[target] = source, fstype, options.split(",")
        elif target.startswith("/home/"):
            raise RuntimeError("There are separate mounts inside /home.")
    for target, name in SUBVOLUMES.items():
        source, fstype, options = mounts.get(target, ("", "", []))
        if fstype != "btrfs" or not _matches(source, fs_uuid) or not {
            f"subvol={name}", f"subvol=/{name}",
        }.intersection(options):
            raise RuntimeError(f"{target} does not use the expected Btrfs subvolume {name}.")

    efi = [
        (target, part) for target in ("/boot", "/efi") for part in partitions
        if _is_esp(part) and _available(part) and target in mounts
        and mounts[target][1] == "vfat" and _matches(mounts[target][0], part["uuid"], part["partuuid"])
    ]
    if len(efi) != 1:
        raise RuntimeError("Could not identify the existing system's EFI partition on this disk.")
    esp_mount, esp = efi[0]

    for line in (etc / "passwd").read_text().splitlines():
        account = line.split(":")
        if len(account) == 7 and account[0] == username and account[5] == f"/home/{username}":
            uid, gid = (int(number) for number in account[2:4])
            home = top / "@home" / username
            if uid >= 1000 and gid >= 1000 and not home.is_symlink() and home.is_dir() and home.stat().st_uid == uid:
                break
    else:
        raise HomeOwnerMismatch(f"Could not match /home/{username} to an existing account.")

    return {"esp_device": esp["path"], "esp_partuuid": esp["partuuid"],
            "esp_mount": esp_mount, "uid": uid, "gid": gid,
            "home_id": _subvolume_id(top / "@home"), "root_id": _subvolume_id(top / "@")}


@dataclass
class PendingReinstall:
    """Identifiers needed to finish or undo an interrupted subvolume exchange."""

    fs_uuid: str
    esp_partuuid: str
    home_id: int
    old_root: int
    old_factory: int | None
    new_root: int | None = None
    new_factory: int | None = None
    esp_backed_up: bool = False

    def save(self, workspace: Path) -> None:
        tmp = workspace / "state.tmp"
        tmp.write_text(json.dumps(asdict(self)))
        tmp.replace(workspace / "state.json")
        subprocess.run(["sync", "-f", workspace], check=True)


def _optional_id(path: Path) -> int | None:
    if not os.path.lexists(path):
        return None
    if not _subvolume(path):
        raise RuntimeError(f"Expected a Btrfs subvolume at {path}.")
    return _subvolume_id(path)


def _pending(top: Path, fs_uuid: str, plan: dict) -> PendingReinstall | None:
    workspace = top / WORKSPACE
    factory = top / PENDING_FACTORY
    if not os.path.lexists(workspace) and not os.path.lexists(factory):
        return None
    marker = workspace / "state.json"
    if workspace.is_symlink() or not marker.is_file() or marker.is_symlink():
        raise RuntimeError("Unrecognized reinstall files on the disk. Inspect them before reinstalling.")
    if {entry.name for entry in workspace.iterdir()} - {"state.json", "state.tmp", "root", "efi-backup"}:
        raise RuntimeError("Unexpected files in the reinstall directory. Inspect them before reinstalling.")
    try:
        previous = PendingReinstall(**json.loads(marker.read_text()))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Cannot read the previous reinstall state.") from exc
    expected = (fs_uuid, plan["esp_partuuid"], plan["home_id"])
    if (previous.fs_uuid, previous.esp_partuuid, previous.home_id) != expected:
        raise RuntimeError("The previous reinstall belongs to a different filesystem, home or EFI partition.")
    active_root = plan["root_id"]
    if active_root not in {previous.old_root, previous.new_root}:
        raise RuntimeError("The active root changed since the previous reinstall.")
    active_factory = _optional_id(top / "@factory")
    root_switched = active_root != previous.old_root
    valid_factories = {previous.old_factory}
    if previous.new_factory is not None:
        valid_factories.add(previous.new_factory)
    if active_factory not in valid_factories or (root_switched and active_factory != previous.new_factory):
        raise RuntimeError("The factory snapshot changed since the previous reinstall.")
    for path, staged_id, swapped, old_id in (
        (workspace / "root", previous.new_root, root_switched, previous.old_root),
        (factory, previous.new_factory, active_factory != previous.old_factory, previous.old_factory),
    ):
        found = _optional_id(path)
        # A crash can occur between creating a subvolume and recording its
        # ID. In that case neither exchange has started.
        expected_id = old_id if swapped else staged_id
        if found is not None and staged_id is not None and found != expected_id:
            raise RuntimeError(f"The staged subvolume at {path} has changed.")
    return previous


def _efi_fallback(esp: Path) -> bool:
    arch = "aa64" if platform.machine() == "aarch64" else "x64"
    loader = esp / f"EFI/limine/limine_{arch}.efi"
    fallback = esp / f"EFI/BOOT/BOOT{arch.upper()}.EFI"
    if not loader.is_file() or not (esp / "limine.conf").is_file():
        raise RuntimeError("The selected EFI partition does not hold the existing Limine installation.")
    return not fallback.exists() or filecmp.cmp(loader, fallback, shallow=False)


def _probe(disk: str, part: dict, device: str, username: str) -> dict:
    fs_uuid = capture_identifier(["blkid", "-p", "-s", "UUID", "-o", "value", device], "the Btrfs UUID")
    with tempfile.TemporaryDirectory(prefix="anser-preserve-", dir="/run") as directory:
        path = Path(directory)
        with _mount(device, path, "btrfs", "ro,rescue=nologreplay,subvolid=5"):
            details = capture_identifier(
                ["env", "LC_ALL=C", "btrfs", "filesystem", "show", "--raw", str(path)],
                "the Btrfs layout",
            )
            if not any(line.split()[:3] == ["Total", "devices", "1"] for line in details.splitlines()):
                raise RuntimeError("Reinstalling needs a Btrfs filesystem on a single partition.")
            plan = _inspect(path, fs_uuid, _partitions(disk), username)
            pending = _pending(path, fs_uuid, plan)
            if pending:
                plan["interrupted"] = True
                if pending.old_root == plan["root_id"] and pending.esp_backed_up:
                    # The power may have gone out halfway through an EFI write.
                    plan["enable_fallback"] = _efi_fallback(path / WORKSPACE / "efi-backup")
        if "enable_fallback" not in plan:
            with _mount(plan["esp_device"], path, "vfat", "ro"):
                plan["enable_fallback"] = _efi_fallback(path)
    return {"disk": disk, "root_device": part["path"], "root_partuuid": part["partuuid"],
            "luks_uuid": part["uuid"] if part["fstype"] == "crypto_LUKS" else None,
            "fs_uuid": fs_uuid, **plan}


def _selected_partition(disk: str, device: str) -> dict:
    for part in candidates(disk):
        if part["path"] == device:
            return part
    raise RuntimeError(f"{device} is not an available Btrfs or LUKS partition on {disk}.")


def probe(disk: str, device: str, username: str, password: bytes) -> dict:
    part = _selected_partition(disk, device)
    mapper = f"anser-probe-{os.getpid()}"
    root_device = _open_root(part, password, mapper, readonly=True)
    try:
        return _probe(disk, part, root_device, username)
    finally:
        if root_device != part["path"]:
            subprocess.run(["cryptsetup", "close", mapper], check=True)


def _copy_system(source: Path, target: Path) -> None:
    # Stop on a fatal write error. cp -a keeps trying every remaining file
    # after ENOSPC and can fill the live installer's log as well.
    copied = capture(["rsync", "-aHAXS", "--numeric-ids", f"{source}/", str(target)])
    if copied.returncode:
        reason = next(iter(copied.stderr.splitlines()), f"rsync exited {copied.returncode}")
        raise RuntimeError(f"Could not stage the new system: {reason}")


class Reinstall:
    """Stage a new root, keeping the original @ and @home separate from setup."""

    def __init__(self, ctx: InstallContext):
        self.ctx = ctx
        self.top = ctx.state_dir / "reinstall-top"
        self.workspace = self.top / WORKSPACE
        self.root = self.workspace / "root"
        # Btrfs can rename a read-only subvolume within the same directory.
        self.factory = self.top / PENDING_FACTORY
        self.esp_backup = self.workspace / "efi-backup"
        self.resources = ExitStack()
        self.pending: PendingReinstall | None = None
        self.opened_mapper = False
        self.mounted_target = False
        self.activated = False

    def prepare(self) -> None:
        ctx = self.ctx
        expected = ctx.omarchy_install.get("preserve_home")
        disk_config = ctx.user_configuration.get("disk_config") or {}
        if not isinstance(expected, dict) or ctx.defer_provisioning or disk_config.get("config_type") != "pre_mounted_config":
            raise RuntimeError("Invalid configuration for reinstalling with the existing home.")
        if subprocess.run(["mountpoint", "-q", ctx.target], check=False).returncode == 0:
            raise RuntimeError(f"Installation target {ctx.target} is already mounted.")
        subprocess.run(
            ["findmnt", "-rn", "-t", "btrfs", "--mountpoint", BASE_SYSTEM],
            stdout=subprocess.DEVNULL, check=True,
        )

        part = _selected_partition(expected["disk"], expected["root_device"])
        if ctx.encrypt != (part["fstype"] == "crypto_LUKS"):
            raise RuntimeError("The existing disk's encryption does not match the confirmed reinstall.")
        password = ctx.user_credentials.get("encryption_password", "").encode()
        device = _open_root(part, password, MAPPER, readonly=False)
        self.opened_mapper = device != part["path"]
        self.plan = _probe(expected["disk"], part, device, ctx.username)
        if self.plan != expected:
            raise RuntimeError("The existing installation changed after confirmation. Select it again.")

        ctx.omarchy_install["boot"] = {
            "esp_mount": self.plan["esp_mount"], "enable_fallback": self.plan["enable_fallback"],
        }
        ctx.omarchy_install["storage"] = {
            "root_device": part["path"], "root_mapper": device, "luks_uuid": self.plan["luks_uuid"],
            "esp_device": self.plan["esp_device"], "kernel": ctx.user_configuration["kernels"][0],
        }
        self.esp = ctx.target / self.plan["esp_mount"].lstrip("/")
        self.top.mkdir(parents=True, exist_ok=True)
        self.resources.enter_context(_mount(device, self.top, "btrfs", "noatime,compress=zstd,subvolid=5"))
        if (_subvolume_id(self.top / "@") != self.plan["root_id"]
                or _subvolume_id(self.top / "@home") != self.plan["home_id"]):
            raise RuntimeError("The root or home changed after the read-only check. Select the installation again.")
        if previous := _pending(self.top, self.plan["fs_uuid"], self.plan):
            info("› recovering the interrupted reinstall")
            self._finish_pending(previous)
        pending = PendingReinstall(self.plan["fs_uuid"], self.plan["esp_partuuid"], self.plan["home_id"],
                                   self.plan["root_id"], _optional_id(self.top / "@factory"))
        # Publish the directory and its state together. An interrupted mkdir
        # must never make an unrelated directory eligible for removal.
        with tempfile.TemporaryDirectory(dir=self.top, prefix=".anser-") as tmp:
            initial = Path(tmp) / "pending"
            initial.mkdir(mode=0o700)
            pending.save(initial)
            initial.rename(self.workspace)
        self.pending = pending

        info("› copying the installation image into a new Btrfs subvolume")
        subprocess.run(["btrfs", "subvolume", "create", self.root], check=True)
        pending.new_root = _subvolume_id(self.root)
        pending.save(self.workspace)
        _copy_system(BASE_SYSTEM, self.root)
        self.esp_backup.mkdir()
        with tempfile.TemporaryDirectory(prefix="anser-efi-", dir="/run") as directory:
            with _mount(self.plan["esp_device"], Path(directory), "vfat", "ro") as old_esp:
                subprocess.run(["rsync", "-rt", f"{old_esp}/", self.esp_backup], check=True)
        # The backup and the state that says it is complete must be on disk
        # before the first writable ESP mount.
        pending.esp_backed_up = True
        pending.save(self.workspace)

        ctx.target.mkdir(parents=True, exist_ok=True)
        options = "noatime,compress=zstd,subvol="
        subprocess.run(["mount", "-t", "btrfs", "-o", options + str(self.root.relative_to(self.top)),
                        device, ctx.target], check=True)
        self.mounted_target = True
        # useradd and package hooks can populate /home during setup. Keep
        # that temporary skeleton out of the root and its factory snapshot.
        subprocess.run(["mount", "-t", "tmpfs", "-o", "mode=0755", "tmpfs", ctx.target / "home"], check=True)
        for dest in ("/var/log", "/var/cache/pacman/pkg"):
            path = ctx.target / dest.lstrip("/")
            path.mkdir(parents=True, exist_ok=True)
            subprocess.run(["mount", "-t", "btrfs", "-o", options + SUBVOLUMES[dest],
                            device, path], check=True)
        self.esp.mkdir(parents=True, exist_ok=True)
        subprocess.run(["mount", "-t", "vfat", "-o", "umask=0077", self.plan["esp_device"], self.esp], check=True)

    def restore_account_ids(self) -> None:
        target = str(self.ctx.target)
        username = self.ctx.username
        uid = str(self.plan["uid"])
        gid = str(self.plan["gid"])
        subprocess.run(["arch-chroot", target, "groupmod", "-g", gid, "--", username], check=True)
        subprocess.run(["arch-chroot", target, "usermod", "-u", uid, "-g", gid, "--", username], check=True)

    def activate(self) -> None:
        home = self.ctx.target / "home"
        device = self.ctx.omarchy_install["storage"]["root_mapper"]
        subprocess.run(["umount", home], check=True)
        subprocess.run(["mount", "-t", "btrfs", "-o", "noatime,compress=zstd,subvol=@home", device, home], check=True)
        if _subvolume_id(home) != self.plan["home_id"]:
            raise RuntimeError("The mounted home no longer matches the confirmed installation.")

        pending = self.pending
        assert pending is not None
        pending.new_factory = _subvolume_id(self.factory)
        pending.save(self.workspace)
        subprocess.run(["sync", "-f", self.esp], check=True)
        old_factory = self.top / "@factory"
        if pending.old_factory is None:
            self.factory.rename(old_factory)
        else:
            _exchange(self.factory, old_factory)
        _exchange(self.root, self.top / "@")
        self.activated = True
        subprocess.run(["sync", "-f", self.top], check=True)

    def _finish_pending(self, pending: PendingReinstall) -> None:
        active = _subvolume_id(self.top / "@")
        factory = self.top / "@factory"
        if active == pending.old_root:
            if pending.esp_backed_up:
                info("› restoring the previous EFI files")
                if not self.esp_backup.is_dir() or self.esp_backup.is_symlink():
                    raise RuntimeError("The EFI backup is unavailable.")
                _efi_fallback(self.esp_backup)
                with tempfile.TemporaryDirectory(dir="/run") as tmp:
                    with _mount(self.plan["esp_device"], Path(tmp), "vfat", "umask=0077") as esp:
                        subprocess.run(["rsync", "-rt", "--ignore-times", "--delete",
                                        f"{self.esp_backup}/", esp], check=True)
                        subprocess.run(["sync", "-f", esp], check=True)
            if pending.new_factory is not None and _optional_id(factory) == pending.new_factory:
                if pending.old_factory is None:
                    factory.rename(self.factory)
                else:
                    if _optional_id(self.factory) != pending.old_factory:
                        raise RuntimeError("Could not identify the previous factory snapshot.")
                    _exchange(self.factory, factory)
        elif active == pending.new_root:
            default = capture_identifier(["btrfs", "subvolume", "get-default", str(self.top)],
                                         "the default Btrfs subvolume").split()
            if default[:2] == ["ID", str(pending.old_root)]:
                subprocess.run(["btrfs", "subvolume", "set-default", str(active), self.top], check=True)
        else:
            raise RuntimeError("Could not identify the active root; the staged files were kept.")

        if pending.esp_backed_up:
            # A later recovery must never restore a partially removed backup.
            pending.esp_backed_up = False
            pending.save(self.workspace)
        for subvolume in (self.factory, self.root):
            if _optional_id(subvolume) is not None:
                subprocess.run(["btrfs", "subvolume", "delete", "--recursive", subvolume], check=True)
        if self.esp_backup.exists():
            shutil.rmtree(self.esp_backup)
        # Move the marker out atomically so a crash during its removal
        # cannot leave an unrecognizable active workspace.
        with tempfile.TemporaryDirectory(dir=self.top, prefix=".anser-") as tmp:
            self.workspace.rename(Path(tmp) / "finished")
        subprocess.run(["sync", "-f", self.top], check=True)

    def cleanup(self) -> None:
        try:
            if self.mounted_target and not self.activated:
                subprocess.run(["omarchy-release-install-target", "--keep-mappers", self.ctx.target], check=True)
            if self.pending:
                self._finish_pending(self.pending)
        finally:
            self.resources.close()
            # The normal dashboard release unmounts the finished target.
            if self.opened_mapper and not self.activated:
                subprocess.run(["cryptsetup", "close", MAPPER], check=True)


def _exchange(staged: Path, active: Path) -> None:
    subprocess.run(["mv", "--exchange", "--no-copy", "-T", "--", staged, active], check=True)


def main() -> int:
    try:
        action, disk, *args = sys.argv[1:]
        if action == "candidates":
            for part in candidates(disk):
                print(part["path"], part["fstype"], sep="\t")
        elif action == "probe":
            device, username = args
            print(json.dumps(probe(disk, device, username, sys.stdin.buffer.read())))
        else:
            raise ValueError("Expected candidates or probe.")
    except HomeOwnerMismatch as exc:
        print(exc, file=sys.stderr)
        return 3
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
