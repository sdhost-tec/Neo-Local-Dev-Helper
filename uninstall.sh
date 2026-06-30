#!/usr/bin/env bash
# Neo LocalDev — Full Uninstaller
# Removes: config, certs, runtimes, domains, systemd service, devctl binary
set -euo pipefail

BOLD='\033[1m'; DIM='\033[2m'
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

echo ""
echo -e "${RED}${BOLD}  ⚠ Neo LocalDev — Uninstall${NC}"
echo -e "${DIM}  This will remove all config, certs, domains, runtimes, and binaries.${NC}"
echo ""

read -r -p "  Are you sure? Type 'yes' to continue: " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo -e "  ${YELLOW}Aborted.${NC}"
    exit 1
fi

echo ""

# ─────────────────────────────────────────────────────────────
# 1. Stop services
# ─────────────────────────────────────────────────────────────
echo -e "${CYAN}[1/9]${NC} Stopping services..."
if command -v neold &>/dev/null; then
    neold stop 2>/dev/null || true
elif command -v devctl &>/dev/null; then
    devctl stop 2>/dev/null || true
fi
# Kill any remaining processes
pkill -f "devpoka" 2>/dev/null || true
pkill -f "NeoLocalDev" 2>/dev/null || true
pkill -f "caddy run" 2>/dev/null || true
echo -e "  ${GREEN}✓${NC} Services stopped"

# ─────────────────────────────────────────────────────────────
# 2. Remove systemd service
# ─────────────────────────────────────────────────────────────
echo -e "${CYAN}[2/9]${NC} Removing systemd service..."
for svc in /etc/systemd/system/neo-localdev@*.service; do
    if [ -f "$svc" ]; then
        sudo systemctl stop "$(basename "$svc")" 2>/dev/null || true
        sudo systemctl disable "$(basename "$svc")" 2>/dev/null || true
        sudo rm -f "$svc"
        echo -e "  ${GREEN}✓${NC} Removed: $(basename "$svc")"
    fi
done
sudo systemctl daemon-reload 2>/dev/null || true

# ─────────────────────────────────────────────────────────────
# 3. Remove neold and devctl binaries
# ─────────────────────────────────────────────────────────────
echo -e "${CYAN}[3/9]${NC} Removing binaries..."
sudo rm -f /usr/local/bin/neold
sudo rm -f /usr/local/bin/devctl
echo -e "  ${GREEN}✓${NC} Removed binaries"

# ─────────────────────────────────────────────────────────────
# 4. Remove domains from /etc/hosts (BEFORE deleting config)
# ─────────────────────────────────────────────────────────────
echo -e "${CYAN}[4/9]${NC} Removing domains from /etc/hosts..."
ACTUAL_USER="${SUDO_USER:-$USER}"
USER_HOME=$(getent passwd "$ACTUAL_USER" | cut -d: -f6)
CONFIG_DIR="${USER_HOME}/.NeoLocalDev"
CONFIG_FILE="${CONFIG_DIR}/config.yml"
MAIN_DOMAIN=$(grep '^domain:' "$CONFIG_FILE" 2>/dev/null | awk '{print $2}' || echo "")
if [ -n "$MAIN_DOMAIN" ]; then
    sudo sed -i "/ ${MAIN_DOMAIN}$/d" /etc/hosts 2>/dev/null || true
    echo -e "  ${GREEN}✓${NC} Removed ${MAIN_DOMAIN} from /etc/hosts"
fi
# Remove any .local domains added by projects
sudo sed -i '/\.local$/d' /etc/hosts 2>/dev/null || true
echo -e "  ${DIM}  (removed *.local entries if auto-managed)${NC}"

# ─────────────────────────────────────────────────────────────
# 5. Remove config, certs, registry, databases
# ─────────────────────────────────────────────────────────────
echo -e "${CYAN}[5/9]${NC} Removing configuration and data..."
if [ -d "$CONFIG_DIR" ]; then
    sudo rm -rf "$CONFIG_DIR"
    echo -e "  ${GREEN}✓${NC} Removed ${CONFIG_DIR}"
else
    echo -e "  ${DIM}  Not found${NC}"
fi

# ─────────────────────────────────────────────────────────────
# 6. Remove installed runtimes
# ─────────────────────────────────────────────────────────────
echo -e "${CYAN}[6/9]${NC} Removing installed runtimes..."
RUNTIMES_DIR="${USER_HOME}/.NeoLocalDev/runtimes"
if [ -d "$RUNTIMES_DIR" ]; then
    rm -rf "$RUNTIMES_DIR"
fi
# Remove node version symlinks
sudo rm -f /usr/local/bin/node[0-9]* /usr/local/bin/npm[0-9]* /usr/local/bin/npx[0-9]* 2>/dev/null || true
echo -e "  ${GREEN}✓${NC} Runtimes cleaned"

# ─────────────────────────────────────────────────────────────
# 7. Remove exported certs
# ─────────────────────────────────────────────────────────────
echo -e "${CYAN}[7/9]${NC} Removing exported certificates..."
sudo rm -rf "${USER_HOME}/neo-certs"
echo -e "  ${GREEN}✓${NC} Removed ~/neo-certs"

# 8. Remove CA from system trust store
# ─────────────────────────────────────────────────────────────
echo -e "${CYAN}[8/9]${NC} Removing CA from system trust store..."
# Debian/Ubuntu
sudo rm -f /usr/local/share/ca-certificates/neo-localdev-ca.crt
sudo update-ca-certificates --fresh 2>/dev/null || true
# RHEL/Fedora
sudo rm -f /etc/pki/ca-trust/source/anchors/neo-localdev-ca.crt
sudo update-ca-trust 2>/dev/null || true
# Arch
sudo rm -f /etc/ca-certificates/trust-source/anchors/neo-localdev-ca.crt
sudo trust extract-compat 2>/dev/null || true
# NSS DB (Chrome)
if command -v certutil &>/dev/null; then
    for db in "${USER_HOME}/.pki/nssdb" /etc/pki/nssdb; do
        if [ -d "$db" ]; then
            certutil -D -d "sql:${db}" -n "Neo LocalDev CA" 2>/dev/null || true
        fi
    done
fi
echo -e "  ${GREEN}✓${NC} CA removed from trust stores"

# ─────────────────────────────────────────────────────────────
# 9. Remove sudoers policy
# ─────────────────────────────────────────────────────────────
echo -e "${CYAN}[9/9]${NC} Removing sudoers policy..."
sudo rm -f /etc/sudoers.d/neold
echo -e "  ${GREEN}✓${NC} Sudoers rules removed"

# ─────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}  ✅ Neo LocalDev fully uninstalled.${NC}"
echo -e "${DIM}  Config, certs, runtimes, domains, binaries, and sudoers rules removed.${NC}"
echo -e "${DIM}  Caddy itself was not removed (use 'sudo apt remove caddy' if desired).${NC}"
echo ""
