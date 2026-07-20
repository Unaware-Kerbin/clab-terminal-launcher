#!/usr/bin/env python3
"""Discover running Containerlab nodes via SSH + clab inspect.

Prints one JSON object per line:
  {"long": "...", "short": "...", "kind": "...", "ip": "..."}

Usage:
  python3 lib/discover.py --host 192.0.2.10 --ssh-user labuser
  # or pipe inspect JSON on stdin:
  clab inspect -f json | python3 lib/discover.py --stdin
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Any


def strip_cidr(value: Any) -> str:
    text = str(value or "")
    return text.split("/", 1)[0]


def iter_nodes(data: Any):
    if isinstance(data, list):
        yield from data
        return
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list):
                yield from value
            elif isinstance(value, dict):
                # single-node map edge case
                if "name" in value or "Name" in value or "Labels" in value:
                    yield value


def short_from_long(long_name: str) -> str:
    """clab-<lab>-<node> -> <node> (node may contain hyphens)."""
    if not long_name.startswith("clab-"):
        return long_name
    rest = long_name[len("clab-") :]
    if "-" not in rest:
        return rest
    # drop lab segment (first hyphen-separated token)
    return rest.split("-", 1)[1]


def parse_node(node: dict[str, Any]) -> dict[str, str] | None:
    state = node.get("State") or node.get("state") or ""
    if state and state != "running":
        return None

    labels = node.get("Labels") or {}
    if not isinstance(labels, dict):
        labels = {}

    long_name = (
        labels.get("clab-node-longname")
        or node.get("name")
        or node.get("Name")
        or ""
    )
    if not long_name:
        return None

    from_long = short_from_long(long_name)
    short = from_long if from_long and from_long != long_name else (
        labels.get("clab-node-name")
        or node.get("shortname")
        or long_name
    )

    kind = (
        labels.get("clab-node-kind")
        or node.get("kind")
        or node.get("Kind")
        or "?"
    )

    net = node.get("NetworkSettings") or {}
    ip = strip_cidr(
        (net.get("IPv4addr") if isinstance(net, dict) else None)
        or node.get("ipv4_address")
        or node.get("IPv4Address")
        or ""
    )
    if not ip or ip == "N/A":
        return None

    return {
        "long": str(long_name),
        "short": str(short),
        "kind": str(kind),
        "ip": str(ip),
    }


def parse_inspect(data: Any) -> list[dict[str, str]]:
    devices: list[dict[str, str]] = []
    seen: set[str] = set()
    for node in iter_nodes(data):
        if not isinstance(node, dict):
            continue
        parsed = parse_node(node)
        if not parsed:
            continue
        key = parsed["long"]
        if key in seen:
            continue
        seen.add(key)
        devices.append(parsed)
    return devices


def fetch_inspect(
    host: str,
    ssh_user: str,
    timeout: int = 15,
    control_path: str = "",
) -> Any:
    target = f"{ssh_user}@{host}" if ssh_user else host
    cmd = [
        "ssh",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={timeout}",
        "-o",
        "UpdateHostkeys=no",
    ]
    if control_path:
        cmd += [
            "-o",
            f"ControlPath={control_path}",
            "-o",
            "ControlMaster=no",
        ]
    cmd += [
        target,
        "clab inspect --all -f json 2>/dev/null || clab inspect -f json",
    ]
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise SystemExit("ssh not found on PATH") from exc
    except subprocess.CalledProcessError as exc:
        err = (exc.stderr or exc.stdout or str(exc)).strip()
        raise SystemExit(f"Failed to run 'clab inspect' on {target}: {err}") from exc

    raw = proc.stdout.strip()
    if not raw:
        raise SystemExit(f"Empty inspect output from {target}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON from clab inspect on {target}: {exc}") from exc


def matches_filter(device: dict[str, str], filt: str) -> bool:
    if filt == "all":
        return True
    long_name = device["long"]
    short = device["short"]
    if long_name == filt or short == filt:
        return True
    return filt in long_name or filt in short


def filter_devices(
    devices: list[dict[str, str]], filters: list[str] | None
) -> list[dict[str, str]]:
    if not filters:
        return devices
    out: list[dict[str, str]] = []
    for device in devices:
        if any(matches_filter(device, f) for f in filters):
            out.append(device)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover Containerlab nodes")
    parser.add_argument("--host", default="", help="Containerlab host")
    parser.add_argument("--ssh-user", default="", help="Linux user on clab host")
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read clab inspect JSON from stdin instead of SSH",
    )
    parser.add_argument(
        "--filter",
        action="append",
        default=[],
        help="Node filter (repeatable); short/long name or substring",
    )
    parser.add_argument(
        "--format",
        choices=("jsonl", "pipe"),
        default="jsonl",
        help="Output format (jsonl=default, pipe=long|short|kind|ip)",
    )
    parser.add_argument(
        "--control-path",
        default="",
        help="OpenSSH ControlMaster socket (reuse existing jump tunnel)",
    )
    args = parser.parse_args(argv)

    if args.stdin:
        try:
            data = json.load(sys.stdin)
        except json.JSONDecodeError as exc:
            print(f"Invalid JSON on stdin: {exc}", file=sys.stderr)
            return 1
    else:
        if not args.host:
            print("--host is required unless --stdin is used", file=sys.stderr)
            return 1
        if args.control_path:
            print(
                f"Discovering via jump tunnel ({args.ssh_user + '@' if args.ssh_user else ''}{args.host})...",
                file=sys.stderr,
            )
        else:
            print(
                f"Connecting to {args.ssh_user + '@' if args.ssh_user else ''}{args.host} "
                "to run 'clab inspect'...",
                file=sys.stderr,
            )
            print("(enter the host password if prompted)", file=sys.stderr)
        data = fetch_inspect(
            args.host, args.ssh_user, control_path=args.control_path or ""
        )

    devices = filter_devices(parse_inspect(data), args.filter or None)
    if not devices:
        print("No running nodes with management IPv4 found.", file=sys.stderr)
        return 1

    for device in devices:
        if args.format == "pipe":
            print(f"{device['long']}|{device['short']}|{device['kind']}|{device['ip']}")
        else:
            print(json.dumps(device, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
