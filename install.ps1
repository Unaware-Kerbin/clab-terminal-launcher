#Requires -Version 5.1
<#
.SYNOPSIS
  Install clab-ssh for the current Windows user.

  Copies clab-ssh.ps1 and lib\ next to each other and puts that folder on the
  user PATH. Late is the terminal for -Launcher late. This does not install Late.
#>
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Dest = if ($env:CLAB_SSH_PREFIX) { $env:CLAB_SSH_PREFIX } else { Join-Path $env:LOCALAPPDATA "clab-ssh" }
$Lib = Join-Path $Dest "lib"

if (-not (Test-Path (Join-Path $Root "clab-ssh.ps1"))) {
  throw "Run this from the clab-terminal-launcher checkout."
}
New-Item -ItemType Directory -Force -Path $Lib | Out-Null
Copy-Item (Join-Path $Root "clab-ssh.ps1") (Join-Path $Dest "clab-ssh.ps1") -Force
Copy-Item (Join-Path $Root "clab-ssh") (Join-Path $Dest "clab-ssh") -Force
Copy-Item (Join-Path $Root "requirements.txt") (Join-Path $Dest "requirements.txt") -Force
Copy-Item (Join-Path $Root "lib\*.py") $Lib -Force

# cmd can run `clab-ssh` without typing the PowerShell extension.
$shim = @"
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0clab-ssh.ps1" %*
"@
Set-Content -Path (Join-Path $Dest "clab-ssh.cmd") -Value $shim -Encoding ascii

$py = Get-Command python, python3, py -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $py) {
  Write-Host "Python 3 was not found. Install it, then: py -3 -m pip install --user -r `"$Dest\requirements.txt`""
} else {
  $probe = if ($py.Name -eq "py") { @("py", "-3", "-c", "import cryptography") } else { @($py.Source, "-c", "import cryptography") }
  & $probe[0] $probe[1..($probe.Length - 1)] 2>$null
  if ($LASTEXITCODE -ne 0) {
    $pip = if ($py.Name -eq "py") { @("py", "-3", "-m", "pip", "install", "--user", "-r", (Join-Path $Dest "requirements.txt")) } else { @($py.Source, "-m", "pip", "install", "--user", "-r", (Join-Path $Dest "requirements.txt")) }
    & $pip[0] $pip[1..($pip.Length - 1)]
    if ($LASTEXITCODE -ne 0) {
      Write-Host "Could not install Python cryptography. The vault needs it:"
      Write-Host "  $($pip -join ' ')"
    }
  }
}

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
$parts = $userPath.Split(";") | Where-Object { $_ -and ($_.TrimEnd("\") -ne $Dest.TrimEnd("\")) }
$newPath = (@($Dest) + $parts) -join ";"
[Environment]::SetEnvironmentVariable("Path", $newPath, "User")
if ($env:Path -notlike "*$Dest*") { $env:Path = "$Dest;$env:Path" }

Write-Host "Installed $Dest\clab-ssh.ps1"
Write-Host "Run: clab-ssh.ps1 -ListLaunchers"
Write-Host "Open lab nodes in Late: clab-ssh.ps1 -Launcher late"
$late = Get-Command late, Late -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $late) {
  Write-Host "Late is not on PATH yet. Install Late and open it before -Launcher late."
  Write-Host "native, PuTTY, SecureCRT, Wireshark, and Edgeshark do not need Late."
}
