#!/usr/bin/env bash
set -euo pipefail

BOLD='\033[1m'; DIM='\033[2m'; BLUE='\033[0;34m'; CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo -e "${BLUE}${BOLD}  ⚡ Neo LocalDev helper — Setup${NC}"
echo -e "${DIM}  Lightweight Local Development Gateway & Reverse Proxy${NC}"
echo ""

if [ "$(uname)" != "Linux" ]; then
    echo -e "${RED}This script is for Linux only.${NC}"
    exit 1
fi

# Detect actual non-root user calling sudo
ACTUAL_USER="${SUDO_USER:-$(whoami)}"

# 1. Install python dependencies via apt (failsafe and avoids pip3 PATH issues under sudo)
echo -e "${CYAN}[1/4]${NC} Installing system Python dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3-psutil python3-yaml

# 2. Install mkcert if not present
echo -e "${CYAN}[2/4]${NC} Checking mkcert..."
if ! command -v mkcert &>/dev/null; then
    echo -e "   Downloading and installing mkcert..."
    curl -sJLO "https://dl.filippo.io/mkcert/latest?for=linux/amd64"
    chmod +x mkcert-v*-linux-amd64
    sudo mv mkcert-v*-linux-amd64 /usr/local/bin/mkcert
    echo -e "   ${GREEN}✓${NC} mkcert installed successfully"
else
    echo -e "   ${GREEN}✓${NC} mkcert already installed"
fi

# Trust CA as the actual user (not root) so Chrome/Firefox NSS stores are updated correctly
echo -e "   Trusting local CA in system and browser stores (as $ACTUAL_USER)..."
USER_HOME=$(getent passwd "$ACTUAL_USER" | cut -d: -f6)
sudo -u "$ACTUAL_USER" HOME="$USER_HOME" /usr/local/bin/mkcert -install
echo -e "   ${GREEN}✓${NC} CA trust installed for $ACTUAL_USER"

# 3. Install neold globally
echo -e "${CYAN}[3/4]${NC} Installing neold command globally..."
sudo cp "${PROJECT_DIR}/neold" /usr/local/bin/neold
sudo sed -i "s|# Find NeoLocalDev package directory|export PYTHONPATH=\"${PROJECT_DIR}:\${PYTHONPATH:-}\"\n# Find NeoLocalDev package directory|g" /usr/local/bin/neold
sudo chmod +x /usr/local/bin/neold
sudo rm -f /usr/local/bin/devctl
echo -e "   ${GREEN}✓${NC} neold installed to /usr/local/bin/neold (removed old devctl with dynamic path)"

# Configure MariaDB root user to allow passwordless local connections for development
if command -v mysql &>/dev/null; then
    echo -e "   Configuring MariaDB root user..."
    sudo mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED VIA mysql_native_password USING ''; FLUSH PRIVILEGES;" 2>/dev/null || true
fi

# Configure passwordless sudo for NeoLocalDev helper tasks (managing PHP extensions and restarting FPM/MariaDB)
echo -e "   Configuring passwordless sudo rules for PHP/DB management..."
SUDOERS_FILE="/etc/sudoers.d/neold"
sudo bash -c "cat << 'EOF' > $SUDOERS_FILE
# NeoLocalDev helper sudoers policy
$ACTUAL_USER ALL=(ALL) NOPASSWD: /usr/sbin/phpenmod, /usr/sbin/phpdismod, /usr/bin/systemctl restart php*-fpm, /usr/bin/systemctl restart mariadb, /usr/bin/apt-get install -y php-*, /usr/bin/apt-get install -y php[0-9].*
EOF"
sudo chmod 0440 "$SUDOERS_FILE"

# 4. Initial configuration as the actual user
echo -e "${CYAN}[4/4]${NC} Running initial configuration..."
sudo -u "$ACTUAL_USER" HOME="$USER_HOME" python3 -m NeoLocalDev.cli setup
echo -e "   ${GREEN}✓${NC} Configuration ready"

# Add dev.local to /etc/hosts if not already present
DOMAIN="dev.local"
HOSTS_ENTRY="127.0.0.1 ${DOMAIN}"
if ! grep -qF "$HOSTS_ENTRY" /etc/hosts; then
    echo "$HOSTS_ENTRY" | sudo tee -a /etc/hosts > /dev/null
    echo -e "   ${GREEN}✓${NC} Added ${DOMAIN} to /etc/hosts"
else
    echo -e "   ${GREEN}✓${NC} ${DOMAIN} already in /etc/hosts"
fi

echo ""
echo -e "${BLUE}${BOLD}  ╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}${BOLD}  ║     Neo LocalDev helper — Ready!         ║${NC}"
echo -e "${BLUE}${BOLD}  ╚══════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Run:${NC}       ${CYAN}neold start${NC}  (Starts Gateway + API + Watcher)"
echo -e "  ${BOLD}Status:${NC}    ${CYAN}neold status${NC}"
echo -e "  ${BOLD}Dashboard:${NC} ${CYAN}https://dev.local/admin/${NC}"
echo ""
echo -e "  ${DIM}Open the dashboard to install/start Caddy, MariaDB, and phpMyAdmin.${NC}"
echo ""
