# Adds or removes the Darksiders 2 local-server entries in the system hosts file.
# Usage:
#   powershell -ExecutionPolicy Bypass -File tools\hosts_entries.ps1          (add)
#   powershell -ExecutionPolicy Bypass -File tools\hosts_entries.ps1 -Remove  (remove)

param([switch]$Remove, [switch]$NoPause)

$ErrorActionPreference = 'Stop'

# self-elevate (only when not already elevated)
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $childArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "$PSCommandPath")
    if ($Remove) { $childArgs += '-Remove' }
    if ($NoPause) { $childArgs += '-NoPause' }
    Start-Process powershell -Verb RunAs -Wait -ArgumentList $childArgs
    exit
}

$hostsPath = "$env:SystemRoot\System32\drivers\etc\hosts"
$beginMark = '# >>> DS2 LOCAL SERVER >>>'
$endMark   = '# <<< DS2 LOCAL SERVER <<<'

$entries = @(
    '127.0.0.1 thqc-darksiders2-pc.thqconnect.com',
    '127.0.0.1 prod.thqconnect.com',
    '127.0.0.1 s.thqconnect.com',
    '127.0.0.1 thqconnect.com',
    '127.0.0.1 gds.thqconnect.com',
    '127.0.0.1 ds2.gds.thqconnect.com',
    '127.0.0.1 dev.thqconnect.com',
    '127.0.0.1 staging.thqconnect.com'
)

# --- read current content (raw text, keep everything we don't manage) ---
$content = ''
if ([System.IO.File]::Exists($hostsPath)) {
    $content = [System.IO.File]::ReadAllText($hostsPath)
}

# If the file was wiped (e.g. by an earlier failed write), restore the
# standard Windows header and the pre-existing custom entry.
if (-not $Remove -and $content.Trim().Length -eq 0) {
    Write-Host 'hosts file is empty - restoring standard header'
    $content = @'
# Copyright (c) 1993-2009 Microsoft Corp.
#
# This is a sample HOSTS file used by Microsoft TCP/IP for Windows.
#
# This file contains the mappings of IP addresses to host names. Each
# entry should be kept on an individual line. The IP address should
# be placed in the first column followed by the corresponding host name.
# The IP address and the host name should be separated by at least one
# space. Additionally, comments (such as these) may be inserted on
# individual lines or following the machine name denoted by a '#' symbol.
#
# For example:
#
#      102.54.94.97     rhino.acme.com          # source server
#       38.25.63.10     x.acme.com              # x client host

# localhost name resolution is handled within DNS itself.
#	127.0.0.1       localhost
#	::1             localhost

94.228.116.208	utl-tlt.loc
'@
}

# strip any previous marked block
$sb = New-Object System.Text.StringBuilder
$inBlock = $false
foreach ($line in ($content -split "`r?`n")) {
    if ($line -eq $beginMark) { $inBlock = $true; continue }
    if ($line -eq $endMark)   { $inBlock = $false; continue }
    if (-not $inBlock) { [void]$sb.AppendLine($line) }
}
if (-not $Remove) {
    [void]$sb.AppendLine($beginMark)
    foreach ($e in $entries) { [void]$sb.AppendLine($e) }
    [void]$sb.AppendLine($endMark)
}
$newContent = $sb.ToString()

# --- atomic write with retries (hosts can be briefly locked by Dnscache) ---
$tmpPath = "$env:TEMP\hosts.ds2.tmp"
[System.IO.File]::WriteAllText($tmpPath, $newContent)

$written = $false
for ($i = 1; $i -le 10 -and -not $written; $i++) {
    try {
        # Copy-File replaces content in one shot; falls back to swap on lock
        [System.IO.File]::Copy($tmpPath, $hostsPath, $true)
        $written = $true
    } catch {
        Write-Host "write attempt $i failed: $($_.Exception.Message)"
        Start-Sleep -Milliseconds 400
    }
}

if (-not $written) {
    # last resort: swap via move (works even when target is memory-mapped)
    try {
        Move-Item -Force -Path $tmpPath -Destination $hostsPath
        $written = $true
    } catch {
        Write-Host "FATAL: could not update hosts file: $($_.Exception.Message)"
        exit 1
    }
}

# --- verify ---
$finalSize = (Get-Item $hostsPath).Length
Write-Host "hosts file updated: $finalSize bytes"
if ($finalSize -eq 0) { Write-Host 'FATAL: hosts file is empty after write!'; exit 1 }

if ($Remove) {
    Write-Host 'DS2 hosts entries removed.'
} else {
    Write-Host 'DS2 hosts entries active:'
    foreach ($e in $entries) { Write-Host "  $e" }
}
ipconfig /flushdns | Out-Null
Write-Host 'DNS cache flushed.'
if (-not $NoPause) {
    Write-Host ''
    Write-Host 'Press Enter to close...'
    [void][System.Console]::ReadLine()
}
