#!/usr/bin/python3
"""Add graphical unlock to the storage hooks already selected by Cenere."""
from pathlib import Path
import re


def enable_plymouth(path: Path) -> None:
    text = path.read_text()
    assignments = list(re.finditer(r"^HOOKS=\(([^\n]*)\)$", text, re.MULTILINE))
    if not assignments:
        raise ValueError(f"Missing HOOKS assignment in {path}")
    match = assignments[-1]
    hooks = match[1].split()
    if "plymouth" in hooks:
        return
    # The installed initramfs uses systemd; its units must precede Plymouth.
    position = hooks.index("systemd") + 1
    hooks.insert(position, "plymouth")
    path.write_text(text[:match.start()] + "HOOKS=(" + " ".join(hooks) + ")" + text[match.end():])


def main() -> None:
    for path in (Path("/etc/mkinitcpio.conf"), Path("/etc/mkinitcpio.conf.d/cenere.conf")):
        if path.is_file():
            enable_plymouth(path)


if __name__ == "__main__":
    main()
