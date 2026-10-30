#!/usr/bin/env bash
# Install clab-ssh for the current user (Linux or macOS).
# Late is the terminal for -t late. This does not install Late.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-${HOME}/.local}"
DEST="${PREFIX}/share/clab-ssh"
BIN="${PREFIX}/bin"

if [[ ! -f "$ROOT/clab-ssh" || ! -d "$ROOT/lib" ]]; then
  echo "clab-ssh: run this from the clab-terminal-launcher checkout." >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "clab-ssh: Python 3 is required." >&2
  exit 1
fi

mkdir -p "$DEST/lib" "$BIN"
install -m 0755 "$ROOT/clab-ssh" "$DEST/clab-ssh"
install -m 0644 "$ROOT/clab-ssh.ps1" "$DEST/clab-ssh.ps1"
install -m 0644 "$ROOT/lib/"*.py "$DEST/lib/"
install -m 0644 "$ROOT/requirements.txt" "$DEST/requirements.txt"
ln -sfn "$DEST/clab-ssh" "$BIN/clab-ssh"

if ! python3 -c 'import cryptography' >/dev/null 2>&1; then
  if ! python3 -m pip install --user -r "$DEST/requirements.txt"; then
    echo "clab-ssh: could not install Python cryptography. The vault needs it:" >&2
    echo "clab-ssh:   python3 -m pip install --user -r ${DEST}/requirements.txt" >&2
  fi
fi

echo "Installed ${BIN}/clab-ssh"
echo "Run: clab-ssh --list-launchers"
echo "Open lab nodes in Late: clab-ssh -t late"
if ! command -v late >/dev/null 2>&1 && [[ ! -x "${HOME}/.local/bin/late" ]]; then
  echo "Late is not on PATH yet. Install Late and open it before clab-ssh -t late."
  echo "native, PuTTY, SecureCRT, Wireshark, and Edgeshark do not need Late."
fi
if [[ ":$PATH:" != *":${BIN}:"* ]]; then
  echo "Add ${BIN} to PATH, then open a new terminal."
fi
