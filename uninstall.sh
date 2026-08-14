#!/usr/bin/env bash
# ─── Neo LocalDev Helper — Full Uninstaller ───
set -euo pipefail

BOLD='\033[1m'; DIM='\033[2m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

echo ""
echo -e "${RED}${BOLD}  ╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${RED}${BOLD}  ║          Neo LocalDev helper — Uninstaller           ║${NC}"
echo -e "${RED}${BOLD}  ╚══════════════════════════════════════════════════════╝${NC}"
echo -e "${DIM}  This will remove all configurations, certificates, binaries, and services.${NC}"
echo ""

# Skip prompt if -y or --yes flag passed
if [ "${1:-}" != "-y" ] && [ "${1:-}" != "--yes" ]; then
    read -r -p "  Are you sure you want to uninstall? [y/N]: " CONFIRM
    CONFIRM=$(echo "$CONFIRM" | tr '[:upper:]' '[:lower:]')
    if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "yes" ]; then
        echo -e "\n  ${YELLOW}Uninstallation aborted.${NC}\n"
        exit 0
    fi
fi

echo ""

# Identify user and directories safely
ACTUAL_USER="${SUDO_USER:-$(whoami)}"
USER_HOME=$(getent passwd "$ACTUAL_USER" 2>/dev/null | cut -d: -f6 || echo "")
if [ -z "$USER_HOME" ]; then
    USER_HOME="$HOME"
fi
CONFIG_DIR="${USER_HOME}/.NeoLocalDev"
CONFIG_FILE="${CONFIG_DIR}/config.yml"

# Fix/Remove invalid sudoers file immediately to avoid sudo syntax error spam
sudo rm -f /etc/sudoers.d/neold 2>/dev/null || true

# ─────────────────────────────────────────────────────────────
# 1. Stop all background processes & services
# ─────────────────────────────────────────────────────────────
echo -e "${CYAN}[1/5]${NC} Stopping active processes and services..."

# Stop systemd units if present
sudo systemctl stop neold.service 2>/dev/null || true
sudo systemctl disable neold.service 2>/dev/null || true
for svc in /etc/systemd/system/neo-localdev@*.service /etc/systemd/system/neo-localdev.service; do
    if [ -f "$svc" ]; then
        sudo systemctl stop "$(basename "$svc")" &>/dev/null || true
        sudo systemctl disable "$(basename "$svc")" &>/dev/null || true
    fi
done

# Terminate specific NeoLocalDev daemons safely
pkill -9 -f "NeoLocalDev\.api_server" 2>/dev/null || true
pkill -9 -f "NeoLocalDev\.watcher_daemon" 2>/dev/null || true
pkill -9 -f "caddy run.*NeoLocalDev" 2>/dev/null || true

# Release allocated ports with timeout
if command -v fuser &>/dev/null; then
    timeout 2 sudo fuser -k 9199/tcp 2>/dev/null || true
    timeout 2 sudo fuser -k 2019/tcp 2>/dev/null || true
    timeout 2 sudo fuser -k 443/tcp 2>/dev/null || true
    timeout 2 sudo fuser -k 80/tcp 2>/dev/null || true
fi

echo -e "   ${GREEN}✓${NC} All background services stopped and ports released"

# ─────────────────────────────────────────────────────────────
# 2. Remove Systemd services and global binaries
# ─────────────────────────────────────────────────────────────
echo -e "${CYAN}[2/5]${NC} Cleaning systemd services and global commands..."

sudo rm -f /etc/systemd/system/neold.service /etc/systemd/system/neo-localdev.service
sudo systemctl daemon-reload &>/dev/null || true

sudo rm -f /usr/local/bin/neold /usr/local/bin/devctl
sudo rm -f /usr/local/bin/node[0-9]* /usr/local/bin/npm[0-9]* /usr/local/bin/npx[0-9]* 2>/dev/null || true

echo -e "   ${GREEN}✓${NC} Binaries and systemd units removed"

# ─────────────────────────────────────────────────────────────
# 3. Clean hosts entries and sudoers policy
# ─────────────────────────────────────────────────────────────
echo -e "${CYAN}[3/5]${NC} Restoring hosts file and sudoers policy..."

if [ -f "$CONFIG_FILE" ]; then
    MAIN_DOMAIN=$(grep '^domain:' "$CONFIG_FILE" 2>/dev/null | awk '{print $2}' || echo "")
    if [ -n "$MAIN_DOMAIN" ]; then
        sudo sed -i "/ ${MAIN_DOMAIN}$/d" /etc/hosts 2>/dev/null || true
    fi
fi
# Remove dev.local / dev.lo.com entries
sudo sed -i '/127\.0\.0\.1.*dev\.lo\.com/d' /etc/hosts 2>/dev/null || true
sudo sed -i '/127\.0\.0\.1.*\.local$/d' /etc/hosts 2>/dev/null || true
sudo sed -i '/dev\.local/d' /etc/hosts 2>/dev/null || true

sudo rm -f /etc/sudoers.d/neold

echo -e "   ${GREEN}✓${NC} Hosts mappings and sudoers policy cleaned"

# ─────────────────────────────────────────────────────────────
# 4. Remove configuration, runtimes, and local data
# ─────────────────────────────────────────────────────────────
echo -e "${CYAN}[4/5]${NC} Removing user configurations and cached runtimes..."

if [ -n "$USER_HOME" ] && [ "$USER_HOME" != "/" ]; then
    sudo rm -rf "${USER_HOME}/.NeoLocalDev"
    sudo rm -rf "${USER_HOME}/neo-certs"
fi

echo -e "   ${GREEN}✓${NC} Data directory ~/.NeoLocalDev removed"

# ─────────────────────────────────────────────────────────────
# 5. Clean Certificate Authority (CA) trust
# ─────────────────────────────────────────────────────────────
echo -e "${CYAN}[5/5]${NC} Cleaning SSL certificates and trust stores..."

if command -v mkcert &>/dev/null; then
    timeout 2 sudo -u "$ACTUAL_USER" HOME="$USER_HOME" mkcert -uninstall &>/dev/null || true
    if [ -n "$USER_HOME" ] && [ "$USER_HOME" != "/" ]; then
        rm -rf "${USER_HOME}/.local/share/mkcert" 2>/dev/null || true
        rm -rf "${USER_HOME}/.config/mkcert" 2>/dev/null || true
    fi
fi

# Clean CA from Linux trust stores
sudo rm -f /usr/local/share/ca-certificates/neo-localdev-ca.crt 2>/dev/null || true
sudo rm -f /usr/local/share/ca-certificates/rootCA.pem 2>/dev/null || true
if command -v update-ca-certificates &>/dev/null; then
    sudo update-ca-certificates &>/dev/null || true
fi

echo -e "   ${GREEN}✓${NC} SSL certificates and local trust removed"

# ─────────────────────────────────────────────────────────────
# Completion
# ─────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}  ╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}${BOLD}  ║       ✅ Neo LocalDev Successfully Uninstalled       ║${NC}"
echo -e "${GREEN}${BOLD}  ╚══════════════════════════════════════════════════════╝${NC}"
echo -e "${DIM}  All files, daemons, configs, and ports have been completely restored.${NC}"
echo ""
