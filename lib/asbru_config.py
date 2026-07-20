#!/usr/bin/env python3
"""Write Ásbrú Connection Manager config for Containerlab devices.

Usage:
  python3 lib/asbru_config.py \\
    --cfg-dir ~/asbru-clab \\
    --host 192.0.2.10 \\
    --ssh-user labuser \\
    --device-user admin \\
    --devices-json '[{"long":"...","short":"pe-1","kind":"...","ip":"..."}]'

Prints space-separated connection UUIDs on stdout.
"""

from __future__ import annotations

import argparse
import binascii
import copy
import hashlib
import json
import os
import pathlib
import sys
import uuid

# Ásbrú / PAC Manager obfuscation (NOT strong crypto — known key).
# Matches lib/PACUtils.pm Crypt::CBC Blowfish + opensslv1 + fixed salt.
_ASBRU_CIPHER_KEY = b"PAC Manager (David Torrejon Vaquerizas, david.tv@gmail.com)"
_ASBRU_FIXED_SALT = (12345678).to_bytes(8, "little")  # pack('Q', 12345678)


def _evp_bytes_to_key(password: bytes, salt: bytes, key_len: int, iv_len: int) -> tuple[bytes, bytes]:
    dtot = b""
    d = b""
    while len(dtot) < key_len + iv_len:
        d = hashlib.md5(d + password + salt).digest()
        dtot += d
    return dtot[:key_len], dtot[key_len : key_len + iv_len]


def _pkcs7_pad(data: bytes, block: int = 8) -> bytes:
    n = block - (len(data) % block)
    return data + bytes([n] * n)


def asbru_encrypt_hex(plaintext: str) -> str:
    """Encode a password the way Ásbrú stores it in asbru.yml (encrypt_hex)."""
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.decrepit.ciphers.algorithms import Blowfish
        from cryptography.hazmat.primitives.ciphers import Cipher, modes
    except ImportError as exc:
        raise SystemExit(
            "cryptography is required to encode Ásbrú passwords.\n"
            "Install with: pip install cryptography"
        ) from exc

    raw = plaintext.encode("utf-8")
    # Blowfish max key length 56 — matches Crypt::CBC opensslv1 for this cipher
    key, iv = _evp_bytes_to_key(_ASBRU_CIPHER_KEY, _ASBRU_FIXED_SALT, 56, 8)
    cipher = Cipher(Blowfish(key), modes.CBC(iv), backend=default_backend())
    enc = cipher.encryptor()
    ct = enc.update(_pkcs7_pad(raw)) + enc.finalize()
    return binascii.hexlify(b"Salted__" + _ASBRU_FIXED_SALT + ct).decode("ascii")


def write_asbru_config(
    *,
    cfg_dir: pathlib.Path,
    devices: list[dict[str, str]],
    clab_host: str,
    clab_user: str,
    ssh_user: str,
    no_jump: bool,
    device_password: str | None = None,
    jump_password: str | None = None,
    control_path: str | None = None,
    socks_port: int | None = None,
) -> list[str]:
    try:
        import yaml
    except ImportError as exc:
        raise SystemExit("python3-yaml (PyYAML) is required to write Ásbrú config") from exc

    cfg_dir = cfg_dir.expanduser().resolve()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    for sub in ("session_logs", "screenshots", "scripts", "tmp", "bak", "autostart"):
        (cfg_dir / sub).mkdir(exist_ok=True)

    for name in ("asbru.nfreeze", "asbru.freeze", "asbru.dumper"):
        p = cfg_dir / name
        if p.exists():
            try:
                p.unlink()
            except OSError:
                pass

    home = pathlib.Path.home()
    base_candidates = [
        home / "snap/asbru/current/.config/asbru/asbru.yml",
        home / ".config/asbru/asbru.yml",
        pathlib.Path("/snap/asbru/current/bin/res/asbru.yml"),
    ]
    base_path = next((p for p in base_candidates if p.is_file()), None)
    if base_path is None:
        raise SystemExit("Could not find a base Ásbrú asbru.yml to clone")

    cfg = yaml.safe_load(base_path.read_text(encoding="utf-8"))
    if not isinstance(cfg, dict) or "environments" not in cfg:
        raise SystemExit(f"Invalid base Ásbrú config: {base_path}")

    cfg.setdefault("defaults", {})
    cfg["defaults"]["allow more instances"] = 1
    cfg["defaults"]["open connections in tabs"] = 1
    cfg["defaults"]["tabs in main window"] = 1
    cfg["defaults"]["confirm exit"] = 0
    cfg["defaults"]["session logs folder"] = str(cfg_dir / "session_logs")

    envs = cfg.setdefault("environments", {})
    shell = envs.get("__PAC_SHELL__")
    root = envs.get(
        "__PAC__ROOT__",
        {"children": {}, "expect": [], "screenshots": [], "variables": []},
    )
    empty = (shell or {}).get("pass") or "53616c7465645f5f4e61bc00000000005c4aa135f78c9e29"

    group_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"clab-group.{clab_host}"))

    keep = {"__PAC__ROOT__", "__PAC_SHELL__"}
    new_envs = {k: v for k, v in envs.items() if k in keep}
    if shell:
        new_envs["__PAC_SHELL__"] = shell
    new_envs["__PAC__ROOT__"] = (
        root
        if isinstance(root, dict)
        else {
            "children": {},
            "expect": [],
            "screenshots": [],
            "variables": [],
            "pass": empty,
            "passphrase": empty,
        }
    )
    root = new_envs["__PAC__ROOT__"]
    root.setdefault("children", {})
    root["children"] = {"__PAC_SHELL__": 1, group_uuid: 1}

    children: dict[str, int] = {}
    new_envs[group_uuid] = {
        "_is_group": 1,
        "_protected": 0,
        "name": f"clab-{clab_host}",
        "description": f"Containerlab devices on {clab_host}",
        "parent": "__PAC__ROOT__",
        "children": children,
        "screenshots": [],
        "expect": [],
        "variables": [],
    }

    start_uuids: list[str] = []
    for device in devices:
        conn_uuid = str(
            uuid.uuid5(uuid.NAMESPACE_DNS, f"clab.{clab_host}.{device['long']}")
        )
        use_autofill = bool(device_password)
        # Prefer a host-side SOCKS5 tunnel (socks_port). Ásbrú snap cannot attach to
        # a host OpenSSH ControlMaster socket (OpenSSL/ssh build mismatch).
        use_socks = bool(socks_port) and not no_jump
        use_cm = bool(control_path) and not no_jump and not use_socks
        use_asbru_jump = (not no_jump) and (not use_socks) and (not use_cm)

        if use_autofill and use_asbru_jump and not jump_password:
            print(
                "Warning: device password autofill disabled — jump password required "
                "when using Ásbrú jump (otherwise sessions freeze).",
                file=sys.stderr,
            )
            use_autofill = False

        known_hosts = str(cfg_dir / "known_hosts")
        # Ásbrú embeds "options" in a shell command string. Double-quoted
        # ProxyCommand=... values get mangled (ssh then treats the whole
        # ProxyCommand=... string as the hostname). Put jump/SSH settings in an
        # OpenSSH config file and pass only -F <path> (no spaces to quote).
        ssh_config_path = cfg_dir / "ssh_config"
        ssh_config_lines = [
            "# Generated by clab-ssh — do not edit; overwritten on each launch",
            "Host *",
            f"  UserKnownHostsFile {known_hosts}",
            "  GlobalKnownHostsFile /dev/null",
            "  StrictHostKeyChecking accept-new",
            "  UpdateHostKeys no",
            "  PreferredAuthentications password,keyboard-interactive",
        ]
        if use_socks:
            proxy_script = cfg_dir / "clab-socks-proxy"
            proxy_script.write_text(
                "#!/bin/sh\n"
                "# Generated by clab-ssh — SOCKS5 via local jump tunnel\n"
                f'exec nc -X 5 -x 127.0.0.1:{int(socks_port)} "$1" "$2"\n',
                encoding="utf-8",
            )
            try:
                proxy_script.chmod(0o755)
            except OSError:
                pass
            ssh_config_lines.append(f"  ProxyCommand {proxy_script} %h %p")
        elif use_cm:
            # Prefer socks_port; ControlMaster from host SSH often fails under snap.
            ssh_config_lines.append(
                "  ProxyCommand ssh -W %h:%p "
                f"-o ControlPath={control_path} "
                f"-o ControlMaster=no -o BatchMode=yes {ssh_user}@{clab_host}"
            )
        ssh_config_path.write_text("\n".join(ssh_config_lines) + "\n", encoding="utf-8")
        try:
            ssh_config_path.chmod(0o600)
        except OSError:
            pass
        # Leading space is REQUIRED: asbru_conn concatenates this string directly
        # onto the preceding ssh arg with no separator (it assumes options begin
        # with a space, like its own GUI-generated configs). Without it, "-F" is
        # glued to the prior -o value and dropped, so ssh treats the config path
        # as a hostname.
        ssh_opts = f" -F {ssh_config_path}"

        # use proxy: 0=global, 1=SOCKS(Ásbrú-managed), 2=never, 3=jump
        proxy_mode = 3 if use_asbru_jump else 2

        conn = copy.deepcopy(shell) if shell else {}
        conn.update(
            {
                "KPX title regexp": f".*{device['short']}.*",
                "_is_group": 0,
                "_protected": 0,
                "auth fallback": 0,
                "auth type": "userpass" if use_autofill else "manual",
                "autoreconnect": 0,
                "autossh": 0,
                "cluster": [],
                "description": f"{device['long']} ({device['kind']}) via {clab_host}",
                "embed": 0,
                "expect": [],
                "favourite": 0,
                "ip": device["ip"],
                "local after": [],
                "local before": [],
                "local connected": [],
                "mac": "",
                "macros": [],
                "method": "ssh",
                "name": device["short"],
                "title": device["short"],
                "options": ssh_opts,
                "parent": group_uuid,
                "pass": asbru_encrypt_hex(device_password) if use_autofill else empty,
                "passphrase": empty,
                "passphrase user": "",
                "port": 22,
                "user": clab_user,
                "use sudo": 0,
                "use proxy": proxy_mode,
                "proxy ip": "",
                "proxy port": 8080,
                "proxy user": "",
                "proxy pass": "",
                "screenshots": [],
                "variables": [],
                "session logs folder": str(cfg_dir / "session_logs"),
                "terminal options": {
                    "open in tab": 1,
                    "use personal settings": 1,
                    "terminal window hsize": 900,
                    "terminal window vsize": 600,
                },
            }
        )
        if use_asbru_jump:
            conn["jump ip"] = clab_host
            conn["jump port"] = 22
            conn["jump user"] = ssh_user
            conn["jump pass"] = jump_password or ""
            conn["jump key"] = ""
            conn["pseudo jump"] = 0
        else:
            conn["jump ip"] = ""
            conn["jump user"] = ""
            conn["jump pass"] = ""
            conn["jump port"] = 22
            conn["jump key"] = ""
            conn["pseudo jump"] = 0

        children[conn_uuid] = 1
        new_envs[conn_uuid] = conn
        start_uuids.append(conn_uuid)

    cfg["environments"] = new_envs
    cfg["tmp"] = {"changed": 0}
    cfg["__PAC__EXPORTED__FULL__"] = 1

    known_hosts_path = cfg_dir / "known_hosts"
    try:
        known_hosts_path.touch(exist_ok=True)
        known_hosts_path.chmod(0o600)
    except OSError:
        pass

    out = cfg_dir / "asbru.yml"
    with out.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            cfg,
            f,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
            explicit_start=True,
            width=1000,
        )
    try:
        out.chmod(0o600)
    except OSError:
        pass

    print(f"Wrote Ásbrú config: {out}", file=sys.stderr)
    print(f"Connections: {len(start_uuids)}", file=sys.stderr)
    if device_password and (no_jump or jump_password or control_path or socks_port):
        print("Device password autofill: enabled (userpass)", file=sys.stderr)
    if socks_port and not no_jump:
        print(f"Jump: SOCKS via 127.0.0.1:{socks_port}", file=sys.stderr)
    elif control_path and not no_jump:
        print(f"Jump: OpenSSH ControlMaster via {control_path}", file=sys.stderr)
    elif jump_password and not no_jump:
        print("Jump password: set for Ásbrú built-in jump", file=sys.stderr)
    print(f"SSH known_hosts: {known_hosts_path}", file=sys.stderr)
    print(f"Base config: {base_path}", file=sys.stderr)
    return start_uuids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write Ásbrú clab config")
    parser.add_argument("--cfg-dir", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--ssh-user", required=True)
    parser.add_argument("--device-user", default="admin")
    parser.add_argument(
        "--device-password",
        default="",
        help="Device SSH password (Ásbrú userpass autofill). "
        "Prefer env CLAB_DEVICE_PASSWORD over argv.",
    )
    parser.add_argument(
        "--jump-password",
        default="",
        help="Jump-host password (only for Ásbrú built-in jump; prefer --control-path).",
    )
    parser.add_argument(
        "--control-path",
        default="",
        help="OpenSSH ControlMaster socket (discovery / legacy).",
    )
    parser.add_argument(
        "--socks-port",
        type=int,
        default=0,
        help="Local SOCKS port for jump (preferred for Ásbrú snap).",
    )
    parser.add_argument("--no-jump", action="store_true")
    parser.add_argument(
        "--devices-json",
        required=True,
        help='JSON list of {"long","short","kind","ip"}',
    )
    args = parser.parse_args(argv)

    try:
        devices = json.loads(args.devices_json)
    except json.JSONDecodeError as exc:
        print(f"Invalid --devices-json: {exc}", file=sys.stderr)
        return 1

    if not isinstance(devices, list) or not devices:
        print("No devices provided", file=sys.stderr)
        return 1

    device_password = args.device_password or os.environ.get("CLAB_DEVICE_PASSWORD", "")
    jump_password = args.jump_password or os.environ.get("CLAB_JUMP_PASSWORD", "")
    control_path = args.control_path or os.environ.get("CLAB_SSH_CONTROL_PATH", "")
    socks_env = os.environ.get("CLAB_SOCKS_PORT", "").strip()
    socks_port = args.socks_port or (int(socks_env) if socks_env.isdigit() else 0)
    uuids = write_asbru_config(
        cfg_dir=pathlib.Path(args.cfg_dir),
        devices=devices,
        clab_host=args.host,
        clab_user=args.device_user,
        ssh_user=args.ssh_user,
        no_jump=args.no_jump,
        device_password=device_password or None,
        jump_password=jump_password or None,
        control_path=control_path or None,
        socks_port=socks_port or None,
    )
    print(" ".join(uuids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
