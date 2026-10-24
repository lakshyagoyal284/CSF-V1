<#
.SYNOPSIS
    Disables RFC 1323/7323 TCP Timestamps on Windows to remediate OpenVAS Uptime Disclosure.

.DESCRIPTION
    Checks for Administrator elevation, auto-elevates if necessary, and executes:
    netsh int tcp set global timestamps=disabled
#>

[CmdletBinding()]
param()

$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $IsAdmin) {
    Write-Host "[*] Elevation required. Requesting Administrator privileges..." -ForegroundColor Yellow
    try {
        $process = Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs -PassThru -Wait
        exit $process.ExitCode
    } catch {
        Write-Error "[ERROR] Failed to obtain elevation: $_"
        exit 1
    }
}

Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "Disabling RFC 1323 TCP Timestamps on Windows (OpenVAS Fix)" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan

try {
    $result = netsh int tcp set global timestamps=disabled
    Write-Host $result -ForegroundColor Green
    Write-Host "`n[SUCCESS] RFC 1323 TCP Timestamps disabled." -ForegroundColor Green
    Write-Host "`nCurrent TCP Global Parameters:" -ForegroundColor Cyan
    netsh int tcp show global
} catch {
    Write-Error "[ERROR] Failed to execute netsh command: $_"
    exit 1
}
