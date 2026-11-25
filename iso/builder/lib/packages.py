"""Resolve live and build packages in the ISO worker's private Pacman environment."""
import argparse
import base64
import ctypes
import hashlib
import json
import io
from functools import cache, cmp_to_key
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tarfile
from dataclasses import dataclass

PROJECT = Path("/project")
SYSTEM = PROJECT / "system"
BUILDER = PROJECT / "iso/builder"
ARCH = os.environ["ANSER_ARCH"]
ARM = ARCH == "aarch64"
DISTRO_KEYRING = "archlinuxarm" if ARM else "archlinux"
DEFAULT = SYSTEM / "default/pacman"
if ARM:
    DEFAULT /= ARCH
PROFILE = Path("/etc/pacman.conf") if ARM else DEFAULT / "pacman-stable.conf"
CACHE = PROJECT / os.environ["ANSER_CACHE"] / "packages"
BUILD_STATE = PROJECT / os.environ["ANSER_BUILD_STATE"]
REPOS = BUILD_STATE / "repositories"
WORK = BUILD_STATE / "work"
LOCAL = "anser-build"
BOOT_TOOL = "mkinitcpio-archiso"
TOOLS = ["base", "base-devel", "git", "imagemagick"]


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, errors="replace", **kwargs)


@cache
def keyring():
    for options in (["--init"], ["--populate", DISTRO_KEYRING],
                    ["--add", BUILDER / "omarchy.asc"],
                    ["--lsign-key", "40DFB630FF42BCFFB047046CF0134EE680CAC571"]):
        run(["pacman-key", *options], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def regular(path):
    return not path.is_symlink() and path.is_file()


def signed(archive, signature):
    if not regular(archive) or not regular(signature):
        return False
    keyring()
    result = subprocess.run(["gpg", "--batch", "--no-auto-key-retrieve", "--homedir",
                             "/etc/pacman.d/gnupg", "--status-fd", "1", "--verify",
                             signature, archive], text=True, errors="replace", capture_output=True)
    rejected = ("BADSIG", "ERRSIG", "EXPSIG", "EXPKEYSIG", "REVKEYSIG")
    if any(f"[GNUPG:] {status} " in result.stdout for status in rejected):
        return False
    return result.returncode == 0 and any(
        line.startswith(("[GNUPG:] TRUST_FULLY", "[GNUPG:] TRUST_ULTIMATE"))
        for line in result.stdout.splitlines())


def digest(path):
    with path.open("rb") as data:
        return hashlib.file_digest(data, "sha256").hexdigest()


def offline_config(path, servers, cachedirs=()):
    lines = ["[options]", "Architecture = auto", "SigLevel = Required DatabaseOptional"]
    for directory in (*cachedirs, CACHE, Path("/var/cache/pacman/pkg")):
        lines.append(f"CacheDir = {directory}")
    for repo, directory in servers.items():
        lines.extend([f"[{repo}]", f"Server = {directory.as_uri()}"])
    path.write_text("\n".join(lines) + "\n")


def recipe_id():
    state = hashlib.sha256()
    for path in sorted((BUILDER / "recipes").glob("*/*")):
        if path.is_file():
            # Existing cache generations recorded recipe names under iso/.
            state.update(str(Path("iso") / path.relative_to(BUILDER)).encode() + b"\0")
            state.update(digest(path).encode())
    return state.hexdigest()


def source_id(tools):
    state = hashlib.sha256()
    state.update(recipe_id().encode())
    state.update(digest(PROJECT / "LICENSE").encode())
    for package in sorted(tools.values(), key=lambda p: p.name):
        state.update(package.sha256.encode())
    for path in sorted(SYSTEM.rglob("*")):
        if path.is_symlink():
            content = os.fsencode(os.readlink(path))
        elif path.is_file():
            content = digest(path).encode()
        else:
            continue
        state.update(os.fsencode(path.relative_to(SYSTEM)) + b"\0")
        state.update(str(path.lstat().st_mode).encode() + b"\0" + content + b"\0")
    return state.hexdigest()


def trust_id():
    keys = Path("/usr/share/pacman/keyrings")
    files = [*sorted(keys.glob(DISTRO_KEYRING + "*")), BUILDER / "omarchy.asc"]
    return hashlib.sha256("".join(digest(p) for p in files).encode()).hexdigest()


@dataclass(frozen=True)
class Package:
    name: str
    version: str
    repo: str
    filename: str
    sha256: str
    pgpsig: str

    @property
    def archive(self):
        return CACHE / self.filename

    @property
    def signature(self):
        return CACHE / (self.filename + ".sig")

    def stamp(self):
        result = [self.sha256]
        for file in (self.archive, self.signature):
            if not regular(file):
                return None
            stat = file.stat()
            result.extend([stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])
        return result

    def check(self):
        if not regular(self.archive):
            return "missing"
        if digest(self.archive) != self.sha256:
            return "bad checksum"
        if not signed(self.archive, self.signature):
            return "bad or missing signature"
        return "cached"

    def cached_state(self, verified):
        previous = verified.get(self.filename)
        if previous and self.stamp() == previous:
            return "cached"
        return self.check()


def package_names(path):
    return [name for line in path.read_text().splitlines()
            if (name := line.partition("#")[0].strip())]


def arm_packages(names):
    # Generic UEFI ARM64. The SoC-specific manifests are selected by the upstream
    # installer for their own hardware families.
    excluded = {
        "amd-ucode", "intel-ucode", "obs-studio", "yay-debug", "broadcom-wl-dkms",
        "asusctl", "dell-xps-touchpad-haptics", "dell-xps13-sidecar-amps",
        "linux-firmware-cirrus-dx13260", "intel-ipu7-camera", "intel-lpmd",
        "intel-media-driver", "libva-intel-driver", "vpl-gpu-rt", "thermald",
        "tuxedo-drivers-nocompatcheck-dkms", "nvidia-580xx-dkms", "nvidia-580xx-utils",
        "nvidia-dkms", "vulkan-intel", "vulkan-radeon", "vulkan-asahi",
        "apple-bcm-firmware-fetcher", "apple-t2-audio-config",
        "macbook12-spi-driver-dkms", "t2fanrd",
    }
    renamed = {"linux-t2": "linux-omarchy", "linux-t2-headers": "linux-omarchy-headers",
               "nvim": "neovim", "vi": "ex-vi-compat", "dotnet-runtime": "dotnet-runtime-bin"}
    return (renamed.get(name, name) for name in names
            if name not in excluded and not name.startswith("lib32-"))


class Selection:
    def __init__(self, work, sync):
        self.work = work
        self.config = work / "pacman.conf"
        self.db = work / "db"
        (self.db / "local").mkdir(parents=True)
        self.indexes = self.db / "sync"
        self.snapshot = REPOS / "current"
        self.repos = run(["pacman-conf", "--config", PROFILE, "--repo-list"], stdout=subprocess.PIPE).stdout.split()
        if not self.repos or "omarchy" not in self.repos:
            raise ValueError("Omarchy stable profile is missing its repositories")
        names = package_names(BUILDER / "image.packages")
        names += package_names(SYSTEM / "install/omarchy-base.packages")
        x86_only = set(package_names(SYSTEM / "install/omarchy-x86_64-only.packages"))
        if ARM:
            names += package_names(SYSTEM / "install/omarchy-aarch64.packages")
            names += ["archlinuxarm-keyring", "limine-mkinitcpio-hook", "limine-snapper-sync"]
            self.names = sorted(set(arm_packages(names)) - x86_only)
        else:
            self.names = sorted(set(names) | x86_only)
        live_list = BUILDER / "archinstall.packages"
        self.installer_names = package_names(live_list) if live_list.exists() else []
        optional = SYSTEM / "install/omarchy-other.packages"
        self.hardware_names = package_names(optional) if optional.exists() else []
        if self.hardware_names:
            hardware = set(self.hardware_names) | {"alsa-firmware", "tailscale"}
            if ARM:
                self.hardware_names = sorted(set(arm_packages(hardware)))
            else:
                self.hardware_names = sorted(hardware - {"asus-proart-p14-speaker-firmware", "vulkan-asahi"})
        if not self.names:
            raise ValueError("empty package manifest")
        if sync:
            self.config = PROFILE
            self.pacman("--disable-sandbox", "-Sy")
            self.config = work / "pacman.conf"
        else:
            self.indexes.mkdir()
            for repo in self.repos:
                db = self.snapshot / (repo + ".db")
                if not regular(db):
                    raise ValueError("missing repository indexes; run iso fetch")
                (self.indexes / db.name).symlink_to(db.resolve())
        self.remote = dict.fromkeys(self.repos, self.indexes)
        offline_config(self.config, self.remote)
        self.tools = self.resolve(TOOLS + [BOOT_TOOL])

    def pacman(self, *args):
        return run(["pacman", "--noconfirm", "--config", self.config, "--dbpath", self.db,
                    *args], stdout=subprocess.PIPE).stdout

    def resolve(self, names):
        result = {}
        for line in self.pacman("-Sp", "--print-format", "%n\t%v\t%r\t%f\t%h\t%g", "--", *names).splitlines():
            pkg = Package(*line.split("\t"))
            if (Path(pkg.filename).name != pkg.filename or not pkg.filename
                    or not re.fullmatch(r"[0-9a-f]{64}", pkg.sha256)):
                raise ValueError(f"invalid index entry for {pkg.name}")
            result[pkg.name] = pkg
        return result

    def select_live(self, build_local):
        local = self.work / "local" if build_local else self.snapshot
        if build_local:
            offline_config(self.work / "tools.conf", self.remote)
            keyring()
            print("Building packages from system/...", file=sys.stderr, flush=True)
            result = subprocess.run([BUILDER / "lib/source-packages", self.work, *TOOLS],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if result.returncode:
                raise ValueError("source package build failed:\n" + result.stdout[-7000:])
        elif not (local / "recipes.sha256").is_file() or (local / "recipes.sha256").read_text() != recipe_id():
            raise ValueError("source package recipes have changed or are missing; run iso fetch")
        (self.indexes / (LOCAL + ".db")).symlink_to((local / (LOCAL + ".db")).resolve(strict=True))
        self.servers = {LOCAL: local, **self.remote}
        offline_config(self.config, self.servers, (local,))
        self.target = self.resolve(self.names)
        self.live = self.resolve(self.names + self.installer_names) if self.installer_names else self.target
        self.hardware = self.resolve(self.hardware_names) if self.hardware_names else {}
        if any(self.live.get(name) != package for name, package in self.target.items()):
            raise ValueError("installer and installed system resolve different package versions")
        for recipe in (BUILDER / "recipes").glob("*/PKGBUILD"):
            name = recipe.parent.name
            if name not in self.live or self.live[name].repo != LOCAL:
                raise ValueError(f"expected {name} built from system/")
        self.required = self.tools | self.hardware | self.live


def retrieve(servers, filename, dest):
    for server in servers:
        result = subprocess.run(["curl", "--globoff", "--fail", "--silent", "--show-error",
                                 "--location", "--connect-timeout", "10", "--speed-time", "30",
                                 "--speed-limit", "1024", "--output", dest, "--",
                                 server.rstrip("/") + "/" + filename])
        if result.returncode == 0:
            return
    raise ValueError(f"download failed: {filename}")


def fetch(selection):
    servers = {repo: run(["pacman-conf", "--config", PROFILE, "--repo", repo, "Server"],
                         stdout=subprocess.PIPE).stdout.splitlines() for repo in selection.repos}
    verified = {}
    downloads = 0

    def populate(packages):
        nonlocal downloads
        with tempfile.TemporaryDirectory(prefix=".fetch-", dir=CACHE.parent) as tmp:
            staging = Path(tmp)
            for pkg in packages.values():
                if pkg.repo == LOCAL or pkg.filename in verified:
                    continue
                archive, signature = pkg.archive, pkg.signature
                if not regular(archive) or digest(archive) != pkg.sha256:
                    if signed(archive, signature):
                        raise ValueError(f"signed archive conflicts with new index: {pkg.filename}")
                    archive = staging / pkg.filename
                    retrieve(servers[pkg.repo], pkg.filename, archive)
                    if digest(archive) != pkg.sha256:
                        raise ValueError(f"bad checksum: {pkg.filename}")
                    downloads += 1
                if not signed(archive, signature):
                    signature = staging / (pkg.filename + ".sig")
                    if pkg.pgpsig and pkg.pgpsig != "(null)":
                        signature.write_bytes(base64.b64decode(pkg.pgpsig, validate=True))
                    else:
                        retrieve(servers[pkg.repo], signature.name, signature)
                    if not signed(archive, signature):
                        raise ValueError(f"bad signature: {pkg.filename}")
                if archive.parent == staging:
                    archive.replace(pkg.archive)
                if signature.parent == staging:
                    signature.replace(pkg.signature)
                verified[pkg.filename] = pkg.stamp()

    populate(selection.tools)
    fingerprint = source_id(selection.tools)
    last_source = selection.snapshot / "source.sha256"
    reuse_metadata = (last_source.is_file() and last_source.read_text() == fingerprint
                      and (selection.snapshot / (LOCAL + ".db")).is_file())
    selection.select_live(build_local=not reuse_metadata)
    populate(selection.required)
    generation = Path(tempfile.mkdtemp(prefix="indexes-", dir=REPOS))
    previous = selection.snapshot
    old = previous.resolve() if previous.is_symlink() else None
    try:
        for repo in selection.servers:
            db = selection.indexes / (repo + ".db")
            shutil.copyfile(db, generation / db.name)
            signature = db.with_suffix(".db.sig")
            if signature.is_file():
                shutil.copyfile(signature, generation / signature.name)
        (generation / "verified.json").write_text(json.dumps({"trust": trust_id(), "packages": verified}))
        (generation / "recipes.sha256").write_text(recipe_id())
        (generation / "source.sha256").write_text(fingerprint)
        pointer = generation / "next"
        pointer.symlink_to(generation.name)
        pointer.replace(previous)
    finally:
        if previous.resolve() != generation:
            shutil.rmtree(generation)
    if old and old.parent == REPOS and old.name.startswith("indexes-") and old.is_dir():
        shutil.rmtree(old)
    print(f"{len(verified)} packages verified, {downloads} downloaded")


def verified_files(selection):
    record = selection.snapshot / "verified.json"
    try:
        metadata = json.loads(record.read_text())
    except (FileNotFoundError, ValueError):
        return {}
    if isinstance(metadata, dict) and metadata.get("trust") == trust_id():
        stamps = metadata.get("packages")
        if isinstance(stamps, dict):
            return stamps
    return {}


def status(selection, show_all):
    verified = verified_files(selection)
    missing = 0
    print(f"{'Package':<30} {'Version':<28} {'Role':<11} Cache")
    for pkg in sorted(selection.required.values(), key=lambda p: p.name):
        role = "manifest" if pkg.name in selection.names or pkg.name in selection.installer_names else "dependency"
        if pkg.name not in selection.live:
            role = "build"
        state = "source" if pkg.repo == LOCAL else pkg.cached_state(verified)
        invalid = state not in ("cached", "source")
        missing += invalid
        if role == "manifest" or invalid or (show_all and role != "build"):
            print(f"{pkg.name:<30} {pkg.version:<28} {role:<11} {state}")
    print(f"{len(selection.live)} live packages, {len(selection.live)-len(set(selection.names + selection.installer_names))} dependencies, "
          f"{missing} missing or invalid")
    return int(bool(missing))


def verify(packages):
    for pkg in packages:
        if pkg.repo != LOCAL:
            state = pkg.check()
            if state != "cached":
                raise ValueError(f"{pkg.name} {pkg.version}: {state}; run iso fetch")


def prepare(selection, work):
    verify(selection.tools.values())
    selection.select_live(build_local=True)
    verify(pkg for name, pkg in selection.required.items() if name not in selection.tools)
    offline_config(work / "profile/pacman.conf", selection.servers, (work / "local",))
    (work / f"profile/packages.{ARCH}").write_text("\n".join(selection.names) + "\n")
    (work / "packages.live").write_text("".join(f"{n} {selection.live[n].version}\n" for n in sorted(selection.live)))
    (work / "boot-package").write_text(str(selection.tools[BOOT_TOOL].archive) + "\n")
    (work / "initcpio-package").write_text(str(selection.live["mkinitcpio"].archive) + "\n")
    (work / "packages.target").write_text("".join(f"{n} {selection.target[n].version}\n" for n in sorted(selection.target)))
    (work / "installer-packages").write_text("\n".join(selection.installer_names) + "\n")
    (work / "packages.live-only").write_text("\n".join(sorted(selection.live.keys() - selection.target.keys())) + "\n")
    prepare_mirror(selection, work / "offline")


def prepare_mirror(selection, mirror):
    """Ship hardware-specific archives once; keep metadata for already installed dependencies."""
    mirror.mkdir()
    packages = selection.hardware | selection.target
    names = {(pkg.repo, pkg.filename) for pkg in packages.values()}
    found = set()
    with tarfile.open(mirror / "offline.db", "w:gz") as target:
        for repo in selection.servers:
            with tarfile.open(selection.indexes / (repo + ".db")) as src:
                for entry in src:
                    if not entry.name.endswith("/desc"):
                        continue
                    data = src.extractfile(entry)
                    desc = data.read()
                    _, field, value = desc.partition(b"%FILENAME%\n")
                    if not field:
                        continue
                    key = (repo, value.partition(b"\n")[0].decode())
                    if key in names:
                        target.addfile(entry, io.BytesIO(desc))
                        found.add(key)
    if names != found:
        missing = ", ".join(f"{repo}/{filename}" for repo, filename in sorted(names - found))
        raise ValueError(f"incomplete installation mirror index: {missing}")
    for name in (selection.hardware.keys() - selection.target.keys()) | {DISTRO_KEYRING + "-keyring"}:
        package = packages[name]
        for path in (package.archive, package.signature):
            run(["cp", "--reflink=auto", "--", path, mirror / path.name])


def remove_files(files, dry_run):
    size = sum(file.stat().st_size for file in files)
    for file in files:
        if dry_run:
            print(file.name)
        else:
            file.unlink()
    return f"{size / 1024**2:.1f} MiB"


def clean_packages(selection, dry_run):
    required = {p.filename: p for p in selection.required.values() if p.repo != LOCAL}
    verified = verified_files(selection)
    issues, obsolete = [], []
    entries = sorted(CACHE.iterdir()) if CACHE.exists() else []
    for file in entries:
        name = file.name
        if name.endswith(".sig"):
            if not regular(file) or not regular(file.with_suffix("")):
                issues.append(name)
            continue
        if not regular(file) or not re.fullmatch(r".+\.pkg\.tar(?:\.[a-z0-9]+)?", name):
            issues.append(name)
            continue
        signature = CACHE / (name + ".sig")
        if name in required:
            if required[name].cached_state(verified) != "cached":
                issues.append(name)
            continue
        if not signed(file, signature):
            issues.append(name)
            continue
        info = subprocess.run(["bsdtar", "-xOqf", file, ".PKGINFO"], capture_output=True, text=True)
        fields = dict(line.split(" = ", 1) for line in info.stdout.splitlines() if " = " in line)
        version = fields.get("pkgver", "")
        stems = {f"{fields.get('pkgname')}-{v}-{fields.get('arch')}"
                 for v in (version, version.split(":")[-1])}
        if (info.returncode or not all(fields.get(key) for key in ("pkgname", "pkgver", "arch"))
                or name.rsplit(".pkg.tar", 1)[0] not in stems):
            issues.append(name)
            continue
        obsolete.extend((file, signature))
    size = remove_files(obsolete, dry_run)
    for name in issues:
        print(f"Unexpected or invalid: {name}", file=sys.stderr)
    print(f"{len(obsolete)//2} package versions {'to remove' if dry_run else 'removed'}, {size}")
    return int(bool(issues))


def clean_images(family, dry_run):
    # Accept numeric releases and date versions. Keep independent editions and architectures.
    pattern = re.compile(r"(.+?)-(\d+(?:\.\d+)*)([-_][a-zA-Z][\w.-]*)?\.iso")
    groups = {}
    for file in (PROJECT / "out").glob("*.iso"):
        match = pattern.fullmatch(file.name)
        if regular(file) and match and match[1] == family:
            variant = match[3] or ""
            if family == "anser" and variant == "-x86_64":
                variant = ""
            groups.setdefault(variant, []).append((match[2], file))
    compare = ctypes.CDLL("libalpm.so").alpm_pkg_vercmp
    compare.argtypes = (ctypes.c_char_p, ctypes.c_char_p)
    compare.restype = ctypes.c_int
    version_key = cmp_to_key(lambda a, b: compare(a.encode(), b.encode()))
    old = []
    for group in groups.values():
        newest = max((v for v, _ in group), key=version_key)
        old.extend(file for v, file in group if version_key(v) < version_key(newest))
    size = remove_files(sorted(old), dry_run)
    print(f"{len(old)} ISOs {'to remove' if dry_run else 'removed'}, {size}")


def main():
    parser = argparse.ArgumentParser(prog="iso")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("fetch")
    commands.add_parser("prepare").add_argument("work", type=Path)
    commands.add_parser("status").add_argument("--all", action="store_true")
    clean = commands.add_parser("clean", allow_abbrev=False)
    clean.add_argument("--dry-run", action="store_true")
    clean.add_argument("--out")
    args = parser.parse_args()
    if args.command == "clean" and args.out is not None:
        if not args.out or args.out.startswith((".", "-")) or Path(args.out).name != args.out:
            parser.error("expected an ISO family name")
        return clean_images(args.out, args.dry_run)
    if args.command == "prepare":
        prepare(Selection(args.work, sync=False), args.work)
        return
    sync = args.command == "fetch"
    with tempfile.TemporaryDirectory(prefix="build-", dir=WORK if sync else None) as tmp:
        selection = Selection(Path(tmp), sync=sync)
        if sync:
            return fetch(selection)
        selection.select_live(build_local=False)
        if args.command == "status":
            return status(selection, args.all)
        return clean_packages(selection, args.dry_run)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as error:
        if error.stderr:
            print(error.stderr.rstrip(), file=sys.stderr)
        sys.exit(f"iso: {error}")
    except (OSError, ValueError) as error:
        sys.exit(f"iso: {error}")
