#!/usr/bin/env python3
"""Report which clab-ssh launchers are available on this machine."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


def which(name: str) -> str | None:
    return shutil.which(name)


def exists_exec(path: str) -> str | None:
    p = Path(path)
    if p.is_file():
        return str(p)
    return None


def find_first(candidates: list[str]) -> str | None:
    for c in candidates:
        if os.sep in c or (len(c) > 1 and c[1] == ":"):
            hit = exists_exec(c)
            if hit:
                return hit
        else:
            hit = which(c)
            if hit:
                return hit
    return None


def probe() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []

    asbru = find_first(["asbru", "asbru-cm"])
    rows.append(
        {
            "name": "asbru",
            "available": "yes" if asbru else "no",
            "path": asbru or "",
            "platforms": "Linux (primary), others if installed",
            "jump": "built-in (no saved session needed)",
        }
    )

    securecrt = find_first(
        [
            "securecrt",
            "SecureCRT",
            "SecureCRT.exe",
            "/Applications/SecureCRT.app/Contents/MacOS/SecureCRT",
            "/mnt/c/Program Files/VanDyke Software/SecureCRT/SecureCRT.exe",
            "/mnt/c/Program Files (x86)/VanDyke Software/SecureCRT/SecureCRT.exe",
            r"C:\Program Files\VanDyke Software\SecureCRT\SecureCRT.exe",
            r"C:\Program Files (x86)\VanDyke Software\SecureCRT\SecureCRT.exe",
        ]
    )
    rows.append(
        {
            "name": "securecrt",
            "available": "yes" if securecrt else "no",
            "path": securecrt or "",
            "platforms": "Windows, macOS, Linux, WSL",
            "jump": "saved session OR OpenSSH ProxyJump fallback",
        }
    )

    putty = find_first(
        [
            "putty",
            "putty.exe",
            "/mnt/c/Program Files/PuTTY/putty.exe",
            "/mnt/c/Program Files (x86)/PuTTY/putty.exe",
            r"C:\Program Files\PuTTY\putty.exe",
            r"C:\Program Files (x86)\PuTTY\putty.exe",
        ]
    )
    rows.append(
        {
            "name": "putty",
            "available": "yes" if putty else "no",
            "path": putty or "",
            "platforms": "Windows, WSL (putty.exe), Linux if installed",
            "jump": "saved session OR ssh -W proxycmd (no saved session needed)",
        }
    )

    ssh = which("ssh")
    rows.append(
        {
            "name": "native",
            "available": "yes" if ssh else "no",
            "path": ssh or "",
            "platforms": "All (opens OS terminal + ssh)",
            "jump": "ssh -J (no saved session needed)",
        }
    )

    wt = find_first(["wt.exe", "wt"])
    rows.append(
        {
            "name": "wt",
            "available": "yes" if wt else "no",
            "path": wt or "",
            "platforms": "Windows / WSL",
            "jump": "ssh -J via Windows Terminal tabs",
        }
    )

    ps = find_first(["powershell.exe", "pwsh.exe", "pwsh", "powershell"])
    rows.append(
        {
            "name": "powershell",
            "available": "yes" if ps else "no",
            "path": ps or "",
            "platforms": "Windows / WSL / pwsh on Linux/macOS",
            "jump": "ssh -J (no saved session needed)",
        }
    )

    cmd = find_first(["cmd.exe"])
    rows.append(
        {
            "name": "cmd",
            "available": "yes" if cmd else "no",
            "path": cmd or "",
            "platforms": "Windows / WSL",
            "jump": "ssh -J (no saved session needed)",
        }
    )

    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="List available clab-ssh launchers")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    rows = probe()
    if args.json:
        import json

        print(json.dumps(rows, indent=2))
        return 0

    print(f"{'LAUNCHER':<12} {'AVAIL':<6} {'JUMP':<48} PATH")
    print(f"{'-'*12} {'-'*6} {'-'*48} {'-'*20}")
    for r in rows:
        path = r["path"] or "-"
        print(f"{r['name']:<12} {r['available']:<6} {r['jump']:<48} {path}")
    print()
    print("Use:  -t LAUNCHER   or   CLAB_LAUNCHER=LAUNCHER")
    return 0


if __name__ == "__main__":
    sys.exit(main())
