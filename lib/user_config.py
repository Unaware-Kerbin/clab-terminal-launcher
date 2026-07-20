#!/usr/bin/env python3
"""Persistent user settings for clab-ssh (stored outside the repo).

Config path (first existing / writable):
  $CLAB_SSH_CONFIG
  $XDG_CONFIG_HOME/clab-ssh/config
  ~/.config/clab-ssh/config

Format: KEY=VALUE lines (shell-compatible).

Usage:
  python3 lib/user_config.py path
  python3 lib/user_config.py get CLAB_HOST
  python3 lib/user_config.py set CLAB_HOST 192.0.2.10
  python3 lib/user_config.py get-all
"""

from __future__ import annotations

import argparse
import os
import pathlib
import sys


def config_path() -> pathlib.Path:
    override = os.environ.get("CLAB_SSH_CONFIG", "").strip()
    if override:
        return pathlib.Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return pathlib.Path(xdg).expanduser() / "clab-ssh" / "config"
    return pathlib.Path.home() / ".config" / "clab-ssh" / "config"


def load() -> dict[str, str]:
    path = config_path()
    data: dict[str, str] = {}
    if not path.is_file():
        return data
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            data[key] = value
    return data


def save(updates: dict[str, str]) -> pathlib.Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = load()
    for key, value in updates.items():
        if value is None:
            continue
        value = str(value).strip()
        if value:
            data[key] = value
        elif key in data:
            del data[key]
    lines = ["# clab-ssh user settings — local only, do not commit", ""]
    for key in sorted(data):
        lines.append(f"{key}={data[key]}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="clab-ssh user config")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("path")

    get_p = sub.add_parser("get")
    get_p.add_argument("key")

    set_p = sub.add_parser("set")
    set_p.add_argument("key")
    set_p.add_argument("value")

    sub.add_parser("get-all")

    args = parser.parse_args(argv)
    if args.cmd == "path":
        print(config_path())
        return 0
    if args.cmd == "get":
        print(load().get(args.key, ""))
        return 0
    if args.cmd == "set":
        path = save({args.key: args.value})
        print(path, file=sys.stderr)
        return 0
    if args.cmd == "get-all":
        for key, value in sorted(load().items()):
            print(f"{key}={value}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
