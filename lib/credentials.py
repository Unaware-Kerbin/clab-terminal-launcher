#!/usr/bin/env python3
"""Local encrypted device credentials for Ásbrú autofill.

Stored ONLY under the user's config directory — never in the repo:

  $CLAB_SSH_CONFIG sibling: <config-dir>/credentials.vault
  else ~/.config/clab-ssh/credentials.vault

Envelope (JSON, no plaintext secrets):
  {
    "v": 1,
    "kdf": "pbkdf2_sha256",
    "iters": 600000,
    "salt": "<base64>",
    "token": "<fernet token>"
  }

Fernet payload: {"device_user":"...","device_password":"..."}

Usage:
  python3 lib/credentials.py path
  python3 lib/credentials.py exists
  python3 lib/credentials.py set          # interactive
  python3 lib/credentials.py get          # interactive unlock → JSON on stdout
  python3 lib/credentials.py forget
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import pathlib
import sys

PBKDF2_ITERS = 600_000
VAULT_VERSION = 1


def _need_cryptography():
    try:
        from cryptography.fernet import Fernet, InvalidToken  # noqa: F401
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    except ImportError as exc:
        raise SystemExit(
            "cryptography is required for the credential vault.\n"
            "Install with: pip install cryptography"
        ) from exc
    return Fernet, InvalidToken, hashes, PBKDF2HMAC


def vault_path() -> pathlib.Path:
    """Sibling of the local settings file — never inside the git repo.

    Same location rules as user_config.py:
      $CLAB_SSH_CONFIG  →  <that-file's-parent>/credentials.vault
      else              →  ~/.config/clab-ssh/credentials.vault
    """
    override = os.environ.get("CLAB_SSH_CONFIG", "").strip()
    if override:
        return pathlib.Path(override).expanduser().parent / "credentials.vault"
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return pathlib.Path(xdg).expanduser() / "clab-ssh" / "credentials.vault"
    return pathlib.Path.home() / ".config" / "clab-ssh" / "credentials.vault"


def exists() -> bool:
    return vault_path().is_file()


def _derive_fernet_key(passphrase: str, salt: bytes, iters: int) -> bytes:
    Fernet, _InvalidToken, hashes, PBKDF2HMAC = _need_cryptography()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=iters,
    )
    raw = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(raw)


def save(device_user: str, device_password: str, passphrase: str) -> pathlib.Path:
    Fernet, _InvalidToken, _hashes, _PBKDF2HMAC = _need_cryptography()
    salt = os.urandom(16)
    key = _derive_fernet_key(passphrase, salt, PBKDF2_ITERS)
    f = Fernet(key)
    payload = json.dumps(
        {"device_user": device_user, "device_password": device_password},
        separators=(",", ":"),
    ).encode("utf-8")
    token = f.encrypt(payload).decode("ascii")
    envelope = {
        "v": VAULT_VERSION,
        "kdf": "pbkdf2_sha256",
        "iters": PBKDF2_ITERS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "token": token,
    }
    path = vault_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(envelope, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def load(passphrase: str) -> dict[str, str]:
    Fernet, InvalidToken, _hashes, _PBKDF2HMAC = _need_cryptography()
    path = vault_path()
    if not path.is_file():
        raise FileNotFoundError(f"No credential vault at {path}")
    try:
        envelope = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Corrupt vault file: {path}") from exc
    if envelope.get("kdf") != "pbkdf2_sha256":
        raise ValueError("Unsupported vault KDF")
    iters = int(envelope.get("iters") or PBKDF2_ITERS)
    salt = base64.b64decode(envelope["salt"])
    key = _derive_fernet_key(passphrase, salt, iters)
    f = Fernet(key)
    try:
        raw = f.decrypt(envelope["token"].encode("ascii"))
    except InvalidToken as exc:
        raise ValueError("Wrong vault passphrase (or corrupt vault)") from exc
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict) or "device_user" not in data or "device_password" not in data:
        raise ValueError("Vault payload missing device_user/device_password")
    return {
        "device_user": str(data["device_user"]),
        "device_password": str(data["device_password"]),
    }


def forget() -> bool:
    path = vault_path()
    if path.is_file():
        path.unlink()
        return True
    return False


def _read_line(prompt: str) -> str:
    """Prompt on stderr so stdout stays clean for machine-readable JSON."""
    print(prompt, end="", file=sys.stderr, flush=True)
    return sys.stdin.readline().rstrip("\r\n")


def _prompt_set() -> int:
    default_user = os.environ.get("CLAB_USER", "admin").strip() or "admin"
    user = _read_line(f"Device SSH username [{default_user}]: ").strip() or default_user
    password = getpass.getpass("Device SSH password: ")
    if not password:
        print("Password is required.", file=sys.stderr)
        return 1
    confirm = getpass.getpass("Confirm device password: ")
    if password != confirm:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    phrase = getpass.getpass("Vault passphrase (protects stored credentials): ")
    if not phrase:
        print("Vault passphrase is required.", file=sys.stderr)
        return 1
    phrase2 = getpass.getpass("Confirm vault passphrase: ")
    if phrase != phrase2:
        print("Vault passphrases do not match.", file=sys.stderr)
        return 1
    path = save(user, password, phrase)
    print(f"Saved encrypted credentials to {path}", file=sys.stderr)
    # stdout: JSON only (for shells capturing with $())
    print(json.dumps({"device_user": user, "device_password": password}), flush=True)
    return 0


def _prompt_get() -> int:
    if not exists():
        print("No credential vault found.", file=sys.stderr)
        return 1
    phrase = os.environ.get("CLAB_VAULT_PASSPHRASE", "")
    if not phrase:
        phrase = getpass.getpass("Vault passphrase: ")
    try:
        data = load(phrase)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(data), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="clab-ssh local credential vault")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("path")
    sub.add_parser("exists")
    sub.add_parser("forget")

    set_p = sub.add_parser("set")
    set_p.add_argument("--user", default="")
    set_p.add_argument("--password", default="")
    set_p.add_argument("--passphrase", default="")

    get_p = sub.add_parser("get")
    get_p.add_argument("--passphrase", default="")

    args = parser.parse_args(argv)

    if args.cmd == "path":
        print(vault_path())
        return 0
    if args.cmd == "exists":
        print("1" if exists() else "0")
        return 0
    if args.cmd == "forget":
        removed = forget()
        print("removed" if removed else "absent", file=sys.stderr)
        return 0
    if args.cmd == "set":
        if args.user and args.password and args.passphrase:
            path = save(args.user, args.password, args.passphrase)
            print(path, file=sys.stderr)
            print(
                json.dumps(
                    {"device_user": args.user, "device_password": args.password}
                )
            )
            return 0
        return _prompt_set()
    if args.cmd == "get":
        phrase = args.passphrase or os.environ.get("CLAB_VAULT_PASSPHRASE", "")
        if phrase:
            try:
                print(json.dumps(load(phrase)))
                return 0
            except ValueError as exc:
                print(str(exc), file=sys.stderr)
                return 1
        return _prompt_get()
    return 1


if __name__ == "__main__":
    sys.exit(main())
