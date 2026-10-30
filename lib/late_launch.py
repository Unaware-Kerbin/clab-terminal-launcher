#!/usr/bin/env python3
"""Open discovered Containerlab nodes as SSH sessions inside Late.

Late's daemon (127.0.0.1:7420) stages the nodes in a folder and opens a
terminal for each one. The device password is read from CLAB_DEVICE_PASSWORD
(never argv) and is not printed.

  python3 lib/late_launch.py probe
  python3 lib/late_launch.py --host 192.0.2.10 --ssh-user lab --devices-json '[...]'
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request


def daemon_url() -> str:
    raw = os.environ.get("LATE_DAEMON_URL", "http://127.0.0.1:7420").strip()
    return raw.rstrip("/") or "http://127.0.0.1:7420"


def config_dir() -> pathlib.Path:
    override = os.environ.get("LATE_CONFIG", "").strip()
    if override:
        return pathlib.Path(override).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(pathlib.Path.home() / "AppData" / "Roaming")
        return pathlib.Path(base) / "late"
    if sys.platform == "darwin":
        return pathlib.Path.home() / "Library" / "Application Support" / "late"
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = pathlib.Path(xdg).expanduser() if xdg else pathlib.Path.home() / ".config"
    return base / "late"


def token_path() -> pathlib.Path:
    return config_dir() / "sidecar.token"


def read_token() -> str:
    path = token_path()
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SystemExit(
            "Late has no local token yet. Open Late once on this computer, then retry.\n"
            f"Expected {path}"
        ) from exc
    if not token or len(token) > 256:
        raise SystemExit(f"Late token at {path} is empty or unusable. Open Late once, then retry.")
    return token


def daemon_up(timeout: float = 0.4) -> bool:
    try:
        with urllib.request.urlopen(f"{daemon_url()}/health", timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except Exception:
        return False


def build_params(
    *,
    host: str,
    ssh_user: str,
    device_user: str,
    devices: list[dict[str, str]],
    no_jump: bool,
    socks_port: int,
    control_path: str,
    device_password: str,
) -> dict[str, object]:
    params: dict[str, object] = {
        "host": host,
        "ssh_user": ssh_user,
        "device_user": device_user or "admin",
        "no_jump": no_jump,
        "devices": devices,
    }
    if socks_port > 0:
        params["socks_port"] = socks_port
    if control_path:
        params["control_path"] = control_path
    if device_password:
        params["device_password"] = device_password
    return params


def build_rpc(params: dict[str, object], rpc_id: int = 1) -> dict[str, object]:
    return {"id": rpc_id, "method": "clab.open", "params": params}


def open_in_late(params: dict[str, object], timeout: float) -> dict[str, object]:
    if not daemon_up(timeout=min(timeout, 2.0)):
        raise SystemExit(
            "Late is not running on this computer.\n"
            "Start Late, then run this again."
        )
    token = read_token()
    body = json.dumps(build_rpc(params)).encode("utf-8")
    req = urllib.request.Request(
        f"{daemon_url()}/rpc",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Late-Token": token,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        if "unauthorized" in detail.lower():
            raise SystemExit(
                "Late rejected the local token. Restart Late, then retry."
            ) from exc
        raise SystemExit(f"Late refused the request ({exc.code}).") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach Late: {exc.reason}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit("Late returned a response that was not JSON.") from exc
    if not isinstance(parsed, dict):
        raise SystemExit("Late returned an unexpected response.")
    if parsed.get("error"):
        err = parsed["error"]
        message = err.get("message") if isinstance(err, dict) else str(err)
        raise SystemExit(f"Late: {message}")
    result = parsed.get("result")
    if not isinstance(result, dict):
        raise SystemExit("Late did not return session results.")
    return result


def _load_devices(raw: str) -> list[dict[str, str]]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid devices JSON: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise SystemExit("No devices to open.")
    return data


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["probe"]:
        print("yes" if daemon_up() else "no")
        return 0

    parser = argparse.ArgumentParser(description="Open Containerlab nodes in Late")
    parser.add_argument("--host", required=True)
    parser.add_argument("--ssh-user", required=True)
    parser.add_argument("--device-user", default="admin")
    parser.add_argument("--devices-json", required=True)
    parser.add_argument("--socks-port", type=int, default=0)
    parser.add_argument("--control-path", default="")
    parser.add_argument("--no-jump", action="store_true")
    args = parser.parse_args(argv)

    password = os.environ.get("CLAB_DEVICE_PASSWORD", "")
    devices = _load_devices(args.devices_json)
    params = build_params(
        host=args.host,
        ssh_user=args.ssh_user,
        device_user=args.device_user,
        devices=devices,
        no_jump=args.no_jump,
        socks_port=args.socks_port,
        control_path=args.control_path,
        device_password=password,
    )
    # Bound the wait: each node can take the daemon's SSH connect budget.
    timeout = min(180.0, 20.0 * len(devices) + 15.0)
    result = open_in_late(params, timeout)
    opened = int(result.get("opened") or 0)
    failed = int(result.get("failed") or 0)
    folder = result.get("folder") or ""
    print(f"Late folder: {folder}", file=sys.stderr)
    for row in result.get("results") or []:
        if not isinstance(row, dict):
            continue
        name = row.get("name") or "?"
        if row.get("ok"):
            print(f"→ {name} open in Late", file=sys.stderr)
        else:
            err = row.get("error") or "failed"
            print(f"→ {name} failed: {err}", file=sys.stderr)
    print(f"Opened {opened} session(s) in Late ({failed} failed).")
    if opened == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
