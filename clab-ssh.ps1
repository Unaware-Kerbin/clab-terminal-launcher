#Requires -Version 5.1
<#
.SYNOPSIS
  Discover Containerlab devices and open SSH sessions (Windows).

.EXAMPLE
  .\clab-ssh.ps1
  .\clab-ssh.ps1 -List
  .\clab-ssh.ps1 -Launcher native -Nodes pe-1,ce-1
  .\clab-ssh.ps1 -Launcher securecrt -JumpSession "Home Lab\CLAB"
#>
[CmdletBinding()]
param(
  [Alias("H")][string]$HostAddress = $(if ($env:CLAB_HOST) { $env:CLAB_HOST } else { "" }),
  [Alias("U")][string]$SshUser = $(if ($env:SSH_USER) { $env:SSH_USER } else { "" }),
  [Alias("u")][string]$DeviceUser = $(if ($env:CLAB_USER) { $env:CLAB_USER } else { "admin" }),
  [Alias("t")]
  [string]$Launcher = $(if ($env:CLAB_LAUNCHER) { $env:CLAB_LAUNCHER } else { "" }),
  [Alias("l")][switch]$List,
  [switch]$ListLaunchers,
  [switch]$NoJump,
  [switch]$SaveDeviceCreds,
  [switch]$ForgetDeviceCreds,
  [switch]$NoDeviceCreds,
  [string]$AsbruBin = $(if ($env:ASBRU_BIN) { $env:ASBRU_BIN } else { "" }),
  [string]$AsbruCfgDir = $(if ($env:ASBRU_CFG_DIR) { $env:ASBRU_CFG_DIR } else { (Join-Path $HOME "asbru-clab") }),
  [string]$SecureCrtBin = $(if ($env:SECURECRT_BIN) { $env:SECURECRT_BIN } else { "" }),
  [string]$PuttyBin = $(if ($env:PUTTY_BIN) { $env:PUTTY_BIN } else { "" }),
  [string]$JumpSession = "",
  [string[]]$Nodes = @()
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LibDir = Join-Path $ScriptDir "lib"
$DiscoverPy = Join-Path $LibDir "discover.py"
$AsbruPy = Join-Path $LibDir "asbru_config.py"
$UserConfigPy = Join-Path $LibDir "user_config.py"
$CredentialsPy = Join-Path $LibDir "credentials.py"
$HostFromFlag = $PSBoundParameters.ContainsKey("HostAddress")
$SshUserFromFlag = $PSBoundParameters.ContainsKey("SshUser")

function Find-Python {
  foreach ($c in @("python", "python3", "py")) {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) {
      if ($c -eq "py") { return @("py", "-3") }
      return @($cmd.Source)
    }
  }
  throw "Python not found. Install Python 3 and ensure it is on PATH."
}

function Invoke-PyScript {
  param([Parameter(Mandatory)][string]$Script, [Parameter(Mandatory)][string[]]$ScriptArgs)
  $pArgs = @()
  if ($script:Py.Length -gt 1) { $pArgs += $script:Py[1..($script:Py.Length - 1)] }
  $pArgs += $Script
  $pArgs += $ScriptArgs
  & $script:Py[0] $pArgs
}

function Invoke-UserConfig {
  param([Parameter(Mandatory)][string[]]$ConfigArgs)
  Invoke-PyScript -Script $UserConfigPy -ScriptArgs $ConfigArgs
}

function Get-UserConfigValue([string]$Key) {
  $val = Invoke-UserConfig @("get", $Key) 2>$null
  if ($null -eq $val) { return "" }
  return ([string]$val).Trim()
}

function Set-UserConfigValue([string]$Key, [string]$Value) {
  Invoke-UserConfig @("set", $Key, $Value) | Out-Null
}

function Invoke-Credentials {
  param([Parameter(Mandatory)][string[]]$CredArgs)
  Invoke-PyScript -Script $CredentialsPy -ScriptArgs $CredArgs
}

function Get-SecureStringText([Security.SecureString]$Secure) {
  $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure)
  try {
    return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  }
}

function Save-DeviceCredentialsInteractive {
  $defaultUser = $DeviceUser
  $enteredUser = Read-Host "Device SSH username [$defaultUser]"
  if (-not [string]::IsNullOrWhiteSpace($enteredUser)) { $defaultUser = $enteredUser.Trim() }
  $pw1 = Read-Host "Device SSH password" -AsSecureString
  $pw2 = Read-Host "Confirm device password" -AsSecureString
  $p1 = Get-SecureStringText $pw1
  $p2 = Get-SecureStringText $pw2
  if ($p1 -ne $p2 -or [string]::IsNullOrEmpty($p1)) {
    throw "Device passwords do not match or are empty."
  }
  $vp1 = Read-Host "Vault passphrase (protects stored credentials)" -AsSecureString
  $vp2 = Read-Host "Confirm vault passphrase" -AsSecureString
  $v1 = Get-SecureStringText $vp1
  $v2 = Get-SecureStringText $vp2
  if ($v1 -ne $v2 -or [string]::IsNullOrEmpty($v1)) {
    throw "Vault passphrases do not match or are empty."
  }
  $json = Invoke-Credentials @("set", "--user", $defaultUser, "--password", $p1, "--passphrase", $v1)
  return ($json | Select-Object -Last 1 | ConvertFrom-Json)
}

function Unlock-DeviceCredentials {
  $vp = Read-Host "Vault passphrase" -AsSecureString
  $phrase = Get-SecureStringText $vp
  $json = Invoke-Credentials @("get", "--passphrase", $phrase)
  if ($LASTEXITCODE -ne 0) { throw "Could not unlock credential vault." }
  return ($json | Select-Object -Last 1 | ConvertFrom-Json)
}

# Resolve device credentials from the local vault (may prompt to create/unlock).
# Shared by every launcher that can autofill. Returns @{ user=...; password=... };
# password is empty when the vault is skipped or unavailable.
function Resolve-DeviceCredentials {
  $result = @{ user = $DeviceUser; password = "" }
  if ($NoDeviceCreds) {
    Write-Host "Skipping device credential vault (-NoDeviceCreds)."
    return $result
  }
  $exists = (Invoke-Credentials @("exists") | Select-Object -Last 1).ToString().Trim()
  try {
    if ($SaveDeviceCreds -or $exists -eq "0") {
      if (-not $SaveDeviceCreds -and $exists -eq "0") {
        $ans = Read-Host "Save encrypted device credentials for autofill? [Y/n]"
        if ([string]::IsNullOrWhiteSpace($ans)) { $ans = "Y" }
        if ($ans -notmatch '^(y|yes)$') {
          Write-Host "Continuing without saved device credentials (you'll be prompted)."
          return $result
        }
      }
      $creds = Save-DeviceCredentialsInteractive
      $result.user = $creds.device_user
      $result.password = $creds.device_password
    } else {
      try {
        $creds = Unlock-DeviceCredentials
        $result.user = $creds.device_user
        $result.password = $creds.device_password
      } catch {
        Write-Host $_.Exception.Message -ForegroundColor Yellow
        $ans = Read-Host "Re-save credentials? [y/N]"
        if ($ans -match '^(y|yes)$') {
          $creds = Save-DeviceCredentialsInteractive
          $result.user = $creds.device_user
          $result.password = $creds.device_password
        } else {
          Write-Host "Continuing without autofill."
        }
      }
    }
  } catch {
    Write-Host "Credential vault error: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "Continuing without autofill."
  }
  return $result
}

# Write a password to a private temp file for PuTTY -pwfile (keeps it out of the
# process list). Scheduled for deletion shortly after launch. Returns the path.
function New-PwFile([string]$Password) {
  if ([string]::IsNullOrEmpty($Password)) { return $null }
  $f = [System.IO.Path]::GetTempFileName()
  Set-Content -Path $f -Value $Password -NoNewline -Encoding ASCII
  Start-Job -ScriptBlock {
    param($p) Start-Sleep -Seconds 15; Remove-Item $p -Force -ErrorAction SilentlyContinue
  } -ArgumentList $f | Out-Null
  return $f
}

function Find-Ssh {
  $cmd = Get-Command ssh -ErrorAction SilentlyContinue
  if (-not $cmd) { throw "ssh not found. Install Windows OpenSSH Client." }
  return $cmd.Source
}

function Get-SecureCrtPath {
  if ($SecureCrtBin) { return $SecureCrtBin }
  $candidates = @(
    "${env:ProgramFiles}\VanDyke Software\SecureCRT\SecureCRT.exe",
    "${env:ProgramFiles(x86)}\VanDyke Software\SecureCRT\SecureCRT.exe",
    "SecureCRT.exe",
    "securecrt"
  )
  foreach ($c in $candidates) {
    if (Test-Path $c) { return $c }
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
  }
  throw "SecureCRT not found. Set SECURECRT_BIN or -SecureCrtBin."
}

function Get-PuttyPath {
  if ($PuttyBin) { return $PuttyBin }
  $candidates = @(
    "${env:ProgramFiles}\PuTTY\putty.exe",
    "${env:ProgramFiles(x86)}\PuTTY\putty.exe",
    "putty.exe",
    "putty"
  )
  foreach ($c in $candidates) {
    if (Test-Path $c) { return $c }
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
  }
  throw "PuTTY not found. Set PUTTY_BIN or -PuttyBin."
}

function Get-DefaultLauncher {
  try { Get-SecureCrtPath | Out-Null; return "securecrt" } catch {}
  try { Get-PuttyPath | Out-Null; return "putty" } catch {}
  return "native"
}

function Get-SshArgList([string]$Ip) {
  $args = @("-o", "StrictHostKeyChecking=accept-new", "-l", $DeviceUser)
  if (-not $NoJump) {
    $args += @("-J", "${SshUser}@${HostAddress}")
  }
  $args += $Ip
  return $args
}

function Get-SshCommandString([string]$Ip) {
  $parts = @("ssh") + (Get-SshArgList $Ip)
  return ($parts | ForEach-Object {
    if ($_ -match '[\s"]') { '"' + ($_ -replace '"', '\"') + '"' } else { $_ }
  }) -join " "
}

# --- main ---
$Py = Find-Python
$ListLaunchersPy = Join-Path $LibDir "list_launchers.py"

if ($ListLaunchers) {
  Invoke-PyScript -Script $ListLaunchersPy -ScriptArgs @()
  return
}

if ($ForgetDeviceCreds) {
  Invoke-Credentials @("forget") | Out-Null
  Write-Host "Device credential vault removed (if it existed)."
  if (-not $SaveDeviceCreds -and -not $Launcher -and -not $List -and $Nodes.Count -eq 0) {
    return
  }
}

Find-Ssh | Out-Null

# Resolve host: env / -H / saved config / prompt (then persist)
if ([string]::IsNullOrWhiteSpace($HostAddress)) {
  $HostAddress = Get-UserConfigValue "CLAB_HOST"
}
if ([string]::IsNullOrWhiteSpace($HostAddress)) {
  $HostAddress = (Read-Host "Containerlab host IP/hostname").Trim()
  if ([string]::IsNullOrWhiteSpace($HostAddress)) {
    throw "Host is required."
  }
  Set-UserConfigValue "CLAB_HOST" $HostAddress
  $cfgPath = (Invoke-UserConfig @("path")).ToString().Trim()
  Write-Host "Saved host to $cfgPath"
} elseif ($HostFromFlag) {
  Set-UserConfigValue "CLAB_HOST" $HostAddress
}

if ([string]::IsNullOrWhiteSpace($SshUser)) {
  $SshUser = Get-UserConfigValue "SSH_USER"
}
if ([string]::IsNullOrWhiteSpace($SshUser)) {
  $default = $env:USERNAME
  $entered = Read-Host "Username for Containerlab host $HostAddress [$default]"
  if ([string]::IsNullOrWhiteSpace($entered)) { $SshUser = $default } else { $SshUser = $entered }
  Set-UserConfigValue "SSH_USER" $SshUser
} elseif ($SshUserFromFlag) {
  Set-UserConfigValue "SSH_USER" $SshUser
}

$discoverArgs = @($DiscoverPy, "--host", $HostAddress, "--ssh-user", $SshUser, "--format", "jsonl")
foreach ($n in $Nodes) {
  if ($n) { $discoverArgs += @("--filter", $n) }
}

$outFile = [System.IO.Path]::GetTempFileName()
$errFile = [System.IO.Path]::GetTempFileName()
try {
  $pArgs = @()
  if ($Py.Length -gt 1) { $pArgs += $Py[1..($Py.Length - 1)] }
  $pArgs += $discoverArgs
  $proc = Start-Process -FilePath $Py[0] -ArgumentList $pArgs -NoNewWindow -Wait -PassThru `
    -RedirectStandardOutput $outFile -RedirectStandardError $errFile
  Get-Content $errFile -ErrorAction SilentlyContinue | ForEach-Object { Write-Host $_ }
  if ($proc.ExitCode -ne 0) { throw "Discovery failed (exit $($proc.ExitCode))." }
  $devices = @()
  Get-Content $outFile | ForEach-Object {
    $t = $_.Trim()
    if ($t) { $devices += ($t | ConvertFrom-Json) }
  }
} finally {
  Remove-Item $outFile, $errFile -ErrorAction SilentlyContinue
}

if ($devices.Count -eq 0) { throw "No running nodes with management IPv4 found." }

Write-Host ""
"{0,-28} {1,-16} {2,-28} {3}" -f "NAME","SHORT","KIND","MGMT IPv4"
"{0,-28} {1,-16} {2,-28} {3}" -f "----","-----","----","--------"
foreach ($d in $devices) {
  "{0,-28} {1,-16} {2,-28} {3}" -f $d.long, $d.short, $d.kind, $d.ip
}
Write-Host ""

if ($List) { return }

if (-not $Launcher) { $Launcher = Get-DefaultLauncher }
$Launcher = $Launcher.ToLowerInvariant()

$secureJump = if ($JumpSession) { $JumpSession } elseif ($env:SECURECRT_JUMP_SESSION) { $env:SECURECRT_JUMP_SESSION } else { "" }
$puttyJump = if ($JumpSession) { $JumpSession } elseif ($env:PUTTY_JUMP_SESSION) { $env:PUTTY_JUMP_SESSION } else { "" }

function Start-NativeSessions([bool]$ForceWt) {
  $wt = Get-Command wt.exe, wt -ErrorAction SilentlyContinue | Select-Object -First 1
  foreach ($d in $devices) {
    Write-Host "→ $($d.short) ($($d.ip)) via native ssh"
    $sshArgs = Get-SshArgList $d.ip
    if ($ForceWt -and -not $wt) { throw "Windows Terminal (wt) not found." }
    if ($wt) {
      $wtArgs = @("new-tab", "--title", "clab:$($d.short)", "ssh") + $sshArgs
      Start-Process -FilePath $wt.Source -ArgumentList $wtArgs
    } else {
      Start-Process -FilePath "ssh" -ArgumentList $sshArgs
    }
    Start-Sleep -Milliseconds 150
  }
  Write-Host "Launched $($devices.Count) native SSH session(s)."
}

Write-Host "Launcher: $Launcher"
Write-Host "Device user: $DeviceUser"
if ($NoJump) { Write-Host "Jump host: (none)" } else { Write-Host "Jump host: ${SshUser}@${HostAddress}" }
Write-Host "Enter jump-host password when prompted (device password autofills for asbru; putty where supported)."
Write-Host ""

switch ($Launcher) {
  "asbru" {
    $creds = Resolve-DeviceCredentials
    if ($creds.user) { $DeviceUser = $creds.user }
    $devicePassword = $creds.password

    $jumpPassword = ""
    if (-not $NoJump) {
      Write-Host "Ásbrú needs the jump-host password once (not stored)."
      $jp = Read-Host "Password for jump host ${SshUser}@${HostAddress}" -AsSecureString
      $jumpPassword = Get-SecureStringText $jp
      if ([string]::IsNullOrEmpty($jumpPassword)) {
        Write-Host "Jump password empty — device autofill will be disabled to avoid a hang." -ForegroundColor Yellow
      }
    }

    $devicesJson = ($devices | ConvertTo-Json -Compress -Depth 5)
    if (-not $devicesJson.StartsWith("[")) { $devicesJson = "[$devicesJson]" }
    $aArgs = @(
      "--cfg-dir", $AsbruCfgDir, "--host", $HostAddress,
      "--ssh-user", $SshUser, "--device-user", $DeviceUser,
      "--devices-json", $devicesJson
    )
    if ($NoJump) { $aArgs += "--no-jump" }
    if ($devicePassword) {
      $env:CLAB_DEVICE_PASSWORD = $devicePassword
    } else {
      Remove-Item Env:CLAB_DEVICE_PASSWORD -ErrorAction SilentlyContinue
    }
    if ($jumpPassword) {
      $env:CLAB_JUMP_PASSWORD = $jumpPassword
    } else {
      Remove-Item Env:CLAB_JUMP_PASSWORD -ErrorAction SilentlyContinue
    }
    try {
      $uuids = Invoke-PyScript -Script $AsbruPy -ScriptArgs $aArgs
      if ($LASTEXITCODE -ne 0) { throw "Failed to write Ásbrú config." }
    } finally {
      Remove-Item Env:CLAB_DEVICE_PASSWORD -ErrorAction SilentlyContinue
      Remove-Item Env:CLAB_JUMP_PASSWORD -ErrorAction SilentlyContinue
      $devicePassword = $null
      $jumpPassword = $null
    }
    $bin = if ($AsbruBin) { $AsbruBin } else {
      $c = Get-Command asbru, asbru-cm -ErrorAction SilentlyContinue | Select-Object -First 1
      if (-not $c) { throw "Ásbrú not found on Windows PATH. Prefer -Launcher securecrt/putty/native." }
      $c.Source
    }
    $startArgs = @("--config-dir=$AsbruCfgDir", "--no-splash")
    foreach ($u in ($uuids -split "\s+")) {
      if ($u) { $startArgs += "--start-uuid=$u" }
    }
    Start-Process -FilePath $bin -ArgumentList $startArgs
    Write-Host "Ásbrú started with $($devices.Count) session(s)."
  }

  "securecrt" {
    if (-not $NoJump -and -not $secureJump) {
      Write-Host "Note: no SecureCRT jump session set; using OpenSSH ProxyJump (native)." -ForegroundColor Yellow
      Write-Host "Set -JumpSession / SECURECRT_JUMP_SESSION for SecureCRT firewall jump." -ForegroundColor Yellow
      Start-NativeSessions $false
      break
    }
    $bin = Get-SecureCrtPath
    foreach ($d in $devices) {
      Write-Host "→ $($d.short) ($($d.ip)) via SecureCRT"
      $args = @("/T")
      if (-not $NoJump) { $args += "/firewall=Session:$secureJump" }
      $args += @("/ssh2", $d.ip, "/l", $DeviceUser, "/P", "22", "/accepthostkeys")
      Start-Process -FilePath $bin -ArgumentList $args
      Start-Sleep -Milliseconds 150
    }
    Write-Host "Launched $($devices.Count) SecureCRT session(s)."
  }

  "putty" {
    $bin = Get-PuttyPath
    $useProxyCmd = (-not $NoJump -and -not $puttyJump)
    if ($useProxyCmd) {
      Write-Host "Note: no PuTTY jump session set; using OpenSSH -proxycmd." -ForegroundColor Yellow
    }
    $pwFile = $null
    $creds = Resolve-DeviceCredentials
    if ($creds.user) { $DeviceUser = $creds.user }
    if ($creds.password) {
      $pwFile = New-PwFile $creds.password
      if ($pwFile) { Write-Host "Device password autofill: enabled (PuTTY -pwfile; requires PuTTY 0.77+)." }
    }
    foreach ($d in $devices) {
      Write-Host "→ $($d.short) ($($d.ip)) via PuTTY"
      $args = @("-ssh", "-P", "22", "-l", $DeviceUser, "-loghost", $d.short)
      if ($pwFile) { $args += @("-pwfile", $pwFile) }
      if (-not $NoJump) {
        if ($useProxyCmd) {
          $args += @("-proxycmd", "ssh -W %host:%port -o StrictHostKeyChecking=accept-new ${SshUser}@${HostAddress}")
        } else {
          $args += @("-load", $puttyJump)
        }
      }
      $args += $d.ip
      Start-Process -FilePath $bin -ArgumentList $args
      Start-Sleep -Milliseconds 150
    }
    Write-Host "Launched $($devices.Count) PuTTY session(s)."
  }

  "native" { Start-NativeSessions $false }
  "wt" { Start-NativeSessions $true }

  "powershell" {
    foreach ($d in $devices) {
      Write-Host "→ $($d.short) ($($d.ip)) via powershell"
      $cmd = Get-SshCommandString $d.ip
      Start-Process powershell -ArgumentList @("-NoExit", "-Command", $cmd)
      Start-Sleep -Milliseconds 150
    }
    Write-Host "Launched $($devices.Count) PowerShell session(s)."
  }

  "cmd" {
    foreach ($d in $devices) {
      Write-Host "→ $($d.short) ($($d.ip)) via cmd"
      $cmd = Get-SshCommandString $d.ip
      Start-Process cmd.exe -ArgumentList @("/k", $cmd)
      Start-Sleep -Milliseconds 150
    }
    Write-Host "Launched $($devices.Count) cmd session(s)."
  }

  default {
    throw "Unknown launcher: $Launcher (use asbru|securecrt|putty|native|wt|powershell|cmd). Try -ListLaunchers."
  }
}
