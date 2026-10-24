#!/bin/bash
# Disables RFC 1323/7323 TCP Timestamps on Linux to remediate OpenVAS Uptime Information Disclosure

set -e

if [ "$EUID" -ne 0 ]; then
  echo "[ERROR] Please run as root (sudo ./disable_tcp_timestamps.sh)"
  exit 1
fi

echo "Disabling net.ipv4.tcp_timestamps dynamically..."
sysctl -w net.ipv4.tcp_timestamps=0

echo "Persisting configuration in /etc/sysctl.d/99-disable-tcp-timestamps.conf..."
cat << 'EOF' > /etc/sysctl.d/99-disable-tcp-timestamps.conf
# Disables RFC 1323/7323 TCP Timestamps to prevent uptime disclosure (OpenVAS remediation)
net.ipv4.tcp_timestamps = 0
EOF

sysctl --system

echo "[SUCCESS] net.ipv4.tcp_timestamps is now: $(sysctl -n net.ipv4.tcp_timestamps)"
