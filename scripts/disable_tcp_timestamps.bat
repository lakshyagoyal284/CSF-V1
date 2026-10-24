@echo off
:: Disables RFC 1323 TCP Timestamps on Windows to remediate OpenVAS Uptime Information Disclosure
:: Right-click this file and select "Run as administrator"

echo ========================================================
echo Disabling RFC 1323 TCP Timestamps on Windows...
echo ========================================================

net session >nul 2>&1
if %errorLevel% neq 0 (
    echo [ERROR] This script requires Administrator privileges.
    echo Please right-click this script and select "Run as administrator".
    pause
    exit /b 1
)

netsh int tcp set global timestamps=disabled
if %errorLevel% equ 0 (
    echo [SUCCESS] RFC 1323 TCP Timestamps have been disabled successfully.
    echo.
    echo Current TCP Global State:
    netsh int tcp show global
) else (
    echo [ERROR] Failed to disable TCP timestamps.
)

pause
