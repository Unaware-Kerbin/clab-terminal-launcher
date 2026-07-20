# clab-ssh

Discover running Containerlab nodes and open SSH sessions in the terminal of your choice on **Linux, macOS, or Windows**.

| Entry point | Platform |
|---|---|
| `./clab-ssh` | Linux, macOS, WSL |
| `.\clab-ssh.ps1` | Windows PowerShell |

The lab host is never hardcoded — you enter it once and it's saved locally. Jump-host passwords are never stored; device passwords can optionally live in a local encrypted vault. Nothing personal is ever written to tracked files.

## Requirements

- OpenSSH client (`ssh`)
- Python 3 — plus `pyyaml` and `cryptography` for the encrypted vault / Ásbrú (`pip install pyyaml cryptography`)
- `clab` on the lab host
- Whichever terminal app you launch (see the matrix below)
- Optional: `sshpass` for device-password autofill with the `native` launcher; PuTTY 0.77+ for PuTTY autofill

## Quick start

```bash
chmod +x clab-ssh
./clab-ssh --list-launchers   # what's available on THIS machine
./clab-ssh -l                 # list running nodes
./clab-ssh -t native          # open all nodes
./clab-ssh -t asbru pe-1 ce-1 # open specific nodes in Ásbrú
```

```powershell
.\clab-ssh.ps1 -ListLaunchers
.\clab-ssh.ps1 -List
.\clab-ssh.ps1 -Launcher powershell -Nodes pe-1,ce-1
```

On first run you're prompted for the host and SSH username, which are then saved to `~/.config/clab-ssh/config`.

## Launchers

Any launcher can be selected on any OS with `-t NAME` / `-Launcher NAME` (or `CLAB_LAUNCHER`). If the app isn't installed you get a clear error or a documented fallback.

| Launcher | Name | Linux | macOS | Windows | WSL | Jump without a saved session |
|---|---|:--:|:--:|:--:|:--:|---|
| Ásbrú | `asbru` | ✓ | ✓ | ✓ | ✓ | built-in (SOCKS tunnel) |
| SecureCRT | `securecrt` | ✓ | ✓ | ✓ | ✓ | falls back to OpenSSH `-J` |
| PuTTY | `putty` | ✓ | rare | ✓ | ✓ | `-proxycmd` + OpenSSH |
| Native OpenSSH | `native` | ✓ | ✓ | ✓ | ✓ | `ssh -J` |
| Windows Terminal | `wt` | WSL | — | ✓ | ✓ | `ssh -J` |
| PowerShell | `powershell` | `pwsh` | `pwsh` | ✓ | ✓ | `ssh -J` |
| cmd.exe | `cmd` | — | — | ✓ | ✓ | `ssh -J` |

**Defaults:** Linux → `asbru` if installed, else `native`; macOS → `native`; Windows → `securecrt` → `putty` → `native`.

`native` auto-picks the first terminal it finds (Ptyxis, GNOME Terminal, Konsole, XFCE/MATE, Kitty, Alacritty, `x-terminal-emulator`, iTerm/Terminal.app, or Windows Terminal/PowerShell under WSL). Override the spawn command with `TERMINAL_CMD`.

## Jump host

All sessions tunnel through `user@<lab-host>`. The jump password is entered once per run; on Linux/macOS the native and PuTTY launchers reuse that single authenticated tunnel for every session.

```bash
./clab-ssh --no-jump -t native                     # connect directly to mgmt IPs
./clab-ssh -t putty                                 # jump works with no saved session
./clab-ssh -t securecrt --jump-session "Home Lab\CLAB-Host"
./clab-ssh -H lab.example.com                       # set/update the saved host
```

```powershell
.\clab-ssh.ps1 -NoJump -Launcher native
.\clab-ssh.ps1 -Launcher securecrt -JumpSession "CLAB-Host"
.\clab-ssh.ps1 -HostAddress lab.example.com
```

## Device password autofill

The device username and password can be stored once in a local encrypted vault and replayed on connect. You never edit code — the first eligible run offers to save them, and you can manage the vault explicitly:

```bash
./clab-ssh -t asbru --save-device-creds    # create/update the vault
./clab-ssh --forget-device-creds           # delete the vault
./clab-ssh -t native --no-device-creds     # skip the vault this run
```

```powershell
.\clab-ssh.ps1 -Launcher putty -SaveDeviceCreds
.\clab-ssh.ps1 -ForgetDeviceCreds
.\clab-ssh.ps1 -Launcher native -NoDeviceCreds
```

The vault (`~/.config/clab-ssh/credentials.vault`) is Fernet-encrypted with a key derived from your passphrase and a random salt (PBKDF2-HMAC-SHA256). You unlock it with that passphrase at launch, and the password is applied only to the device login.

Support varies by launcher and is intentionally limited to methods that don't leak the password into the process list:

| Launcher | Autofill | How |
|---|---|---|
| Ásbrú | yes | encrypted config entry |
| native / `wt` (Linux, macOS) | yes | `sshpass -f` (temp file) — requires `sshpass` |
| PuTTY (native build, incl. Windows) | yes | `-pwfile` (temp file) — requires PuTTY 0.77+ |
| SecureCRT | no | interactive (its `/PASSWORD` would expose the password in `ps`) |
| powershell / cmd / `wt` (Windows `ssh.exe`) | no | Windows OpenSSH has no non-interactive password path |

Where autofill isn't available you're simply prompted as usual. Temp password files are `600` and shredded seconds after launch.

> **Note:** Ásbrú's generated `~/asbru-clab/asbru.yml` also holds a runtime copy of the device password that is only *obfuscated* (public key). The vault is the secure store; treat the YAML as local-only and remove it after use if that matters to you.

## Local settings (never in git)

| File | Purpose |
|---|---|
| `~/.config/clab-ssh/config` | Lab host + jump SSH username |
| `~/.config/clab-ssh/credentials.vault` | Encrypted device user + password |
| `~/asbru-clab/` | Generated Ásbrú runtime config |

Set `CLAB_SSH_CONFIG` to relocate the config file (the vault stays a sibling named `credentials.vault`).

## Options & environment

| Flag / env | Meaning | Default |
|---|---|---|
| `-H` / `CLAB_HOST` | Containerlab host | prompted once, then saved |
| `-U` / `SSH_USER` | Linux user on the clab host | prompted, then saved |
| `-u` / `CLAB_USER` | Device SSH username | `admin` |
| `-t` / `CLAB_LAUNCHER` | Launcher | OS default |
| `-l` / `-List` | List nodes only | |
| `--list-launchers` / `-ListLaunchers` | Show launcher availability | |
| `--no-jump` / `-NoJump` | Connect straight to mgmt IPs | off |
| `--save-device-creds` / `-SaveDeviceCreds` | Create/update the vault | |
| `--forget-device-creds` / `-ForgetDeviceCreds` | Delete the vault | |
| `--no-device-creds` / `-NoDeviceCreds` | Skip the vault this run | |
| `CLAB_SSH_CONFIG` | Override the settings file path | `~/.config/clab-ssh/config` |
| `ASBRU_BIN` / `ASBRU_CFG_DIR` | Ásbrú binary / config dir | auto / `~/asbru-clab` |
| `SECURECRT_BIN` / `SECURECRT_JUMP_SESSION` | SecureCRT path / jump session | |
| `PUTTY_BIN` / `PUTTY_JUMP_SESSION` | PuTTY path / jump session | |
| `TERMINAL_CMD` | Override the native terminal spawn | |

## Layout

```
clab-ssh              # bash CLI
clab-ssh.ps1          # PowerShell CLI
lib/discover.py       # node discovery
lib/asbru_config.py   # Ásbrú YAML writer
lib/list_launchers.py # availability probe
lib/user_config.py    # persistent non-secret settings
lib/credentials.py    # encrypted device vault
```
