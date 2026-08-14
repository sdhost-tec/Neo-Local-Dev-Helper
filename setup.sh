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
USER_HOME=$(getent passwd "$ACTUAL_USER" 2>/dev/null | cut -d: -f6 || echo "")
if [ -z "$USER_HOME" ]; then
    USER_HOME="$HOME"
fi

# Prompt for domain with default dev.lo.com (supports non-interactive mode via DEVPICK_DOMAIN or input)
DEFAULT_DOMAIN="dev.lo.com"
if [ -t 0 ] && [ -z "${DEVPICK_DOMAIN:-}" ]; then
    echo -e "Choose local domain name (Press Enter for default ${CYAN}${DEFAULT_DOMAIN}${NC}):"
    read -rp "Domain [default: ${DEFAULT_DOMAIN}]: " INPUT_DOMAIN
    DOMAIN="${INPUT_DOMAIN:-$DEFAULT_DOMAIN}"
else
    DOMAIN="${DEVPICK_DOMAIN:-$DEFAULT_DOMAIN}"
fi
echo -e "Using local domain: ${CYAN}${DOMAIN}${NC}"
echo ""

# 1. Install system and python dependencies via apt
echo -e "${CYAN}[1/6]${NC} Installing system dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3-psutil python3-yaml curl debian-keyring debian-archive-keyring apt-transport-https libnss3-tools

# 2. Install Caddy (Core Reverse Proxy Gateway)
echo -e "${CYAN}[2/6]${NC} Checking Caddy..."
if ! command -v caddy &>/dev/null; then
    echo -e "   Installing Caddy web server..."
    gpg_key="/usr/share/keyrings/caddy-stable-archive-keyring.gpg"
    if [ ! -f "$gpg_key" ]; then
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o "$gpg_key"
    fi
    apt_list="/etc/apt/sources.list.d/caddy-stable.list"
    if [ ! -f "$apt_list" ]; then
        curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee "$apt_list" >/dev/null
    fi
    sudo apt-get update -qq
    sudo apt-get install -y -qq caddy
    echo -e "   ${GREEN}✓${NC} Caddy installed successfully"
else
    echo -e "   ${GREEN}✓${NC} Caddy already installed"
fi

# Disable default systemd caddy service so neold can manage instances on ports 80/443 without conflict
sudo systemctl disable --now caddy 2>/dev/null || true
CADDY_BIN=$(command -v caddy || echo "/usr/bin/caddy")
if [ -f "$CADDY_BIN" ]; then
    sudo setcap cap_net_bind_service=+ep "$CADDY_BIN" 2>/dev/null || true
fi

# 3. Install mkcert if not present
echo -e "${CYAN}[3/6]${NC} Checking mkcert..."
if ! command -v mkcert &>/dev/null; then
    echo -e "   Downloading and installing mkcert..."
    curl -sJLO "https://dl.filippo.io/mkcert/latest?for=linux/amd64"
    chmod +x mkcert-v*-linux-amd64
    sudo mv mkcert-v*-linux-amd64 /usr/local/bin/mkcert
    echo -e "   ${GREEN}✓${NC} mkcert installed successfully"
else
    echo -e "   ${GREEN}✓${NC} mkcert already installed"
fi

# Trust CA as the actual user so stores are updated correctly
echo -e "   Trusting local CA in system and browser stores (as $ACTUAL_USER)..."
sudo -u "$ACTUAL_USER" HOME="$USER_HOME" /usr/local/bin/mkcert -install 2>/dev/null || /usr/local/bin/mkcert -install
echo -e "   ${GREEN}✓${NC} CA trust installed for $ACTUAL_USER"

# 4. Install neold command globally
echo -e "${CYAN}[4/6]${NC} Installing neold command globally..."
sudo cp "${PROJECT_DIR}/neold" /usr/local/bin/neold
sudo sed -i "s|# Find NeoLocalDev package directory|export PYTHONPATH=\"${PROJECT_DIR}:\${PYTHONPATH:-}\"\n# Find NeoLocalDev package directory|g" /usr/local/bin/neold
sudo chmod +x /usr/local/bin/neold
sudo rm -f /usr/local/bin/devctl
echo -e "   ${GREEN}✓${NC} neold installed to /usr/local/bin/neold"

# Configure MariaDB root user to allow passwordless local connections for development if installed
if command -v mysql &>/dev/null || command -v mariadb &>/dev/null; then
    echo -e "   Configuring MariaDB root user for dev access..."
    sudo mysql -e "ALTER USER 'root'@'localhost' IDENTIFIED BY ''; FLUSH PRIVILEGES;" 2>/dev/null || true
fi

# Configure clean passwordless sudo rules for helper tasks
echo -e "   Configuring passwordless sudo rules for PHP/DB management..."
SUDOERS_FILE="/etc/sudoers.d/neold"
sudo bash -c "cat << EOF > $SUDOERS_FILE
# NeoLocalDev helper sudoers policy
$ACTUAL_USER ALL=(ALL) NOPASSWD: /usr/bin/apt-get, /usr/bin/systemctl, /usr/sbin/phpenmod, /usr/sbin/phpdismod, /usr/sbin/setcap, /usr/sbin/update-ca-certificates, /usr/bin/certutil, /usr/bin/tee, /usr/bin/sed, /usr/sbin/usermod, /usr/bin/chmod
EOF"
sudo chmod 0440 "$SUDOERS_FILE"

# Ensure user is in www-data group for web permissions
sudo usermod -aG www-data "$ACTUAL_USER" 2>/dev/null || true

# Configure PHP-FPM pool settings if installed so Caddy can reach FastCGI
for fpm_dir in /etc/php/*/fpm/pool.d; do
    if [ -d "$fpm_dir" ]; then
        sudo bash -c "cat << EOF > '$fpm_dir/zz-neold.conf'
[www]
user = $ACTUAL_USER
group = $ACTUAL_USER
listen = 127.0.0.1:9000
listen.mode = 0666
EOF"
    fi
done
for fpm_svc in $(systemctl list-unit-files 'php*-fpm.service' 2>/dev/null | awk '{print $1}' | grep -E '^php'); do
    sudo systemctl restart "$fpm_svc" 2>/dev/null || true
done

# 5. Initial configuration as the actual user
echo -e "${CYAN}[5/6]${NC} Running initial configuration..."
sudo -u "$ACTUAL_USER" HOME="$USER_HOME" python3 -m NeoLocalDev.cli setup
sudo -u "$ACTUAL_USER" HOME="$USER_HOME" python3 -c "from NeoLocalDev.config import load_config, save_config; cfg=load_config(); cfg['domain']='${DOMAIN}'; save_config(cfg)"
echo -e "   ${GREEN}✓${NC} Configuration ready for ${DOMAIN}"

# Add chosen domain to /etc/hosts if not already present
HOSTS_ENTRY="127.0.0.1 ${DOMAIN}"
if ! grep -qF "$HOSTS_ENTRY" /etc/hosts; then
    echo "$HOSTS_ENTRY" | sudo tee -a /etc/hosts > /dev/null
    echo -e "   ${GREEN}✓${NC} Added ${DOMAIN} to /etc/hosts"
else
    echo -e "   ${GREEN}✓${NC} ${DOMAIN} already in /etc/hosts"
fi

# 6. Configure systemd service for automatic boot startup
echo -e "${CYAN}[6/6]${NC} Configuring systemd boot service..."
SERVICE_FILE="/etc/systemd/system/neold.service"
sudo bash -c "cat << EOF > $SERVICE_FILE
[Unit]
Description=Neo LocalDev Gateway & Helper Service
After=network-online.target
Wants=network-online.target

[Service]
Type=forking
User=$ACTUAL_USER
Environment=HOME=$USER_HOME
Environment=PYTHONPATH=${PROJECT_DIR}
ExecStart=/usr/local/bin/neold start
ExecStop=/usr/local/bin/neold stop
ExecReload=/usr/local/bin/neold reload
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload 2>/dev/null || true
sudo systemctl enable neold.service 2>/dev/null || true
sudo systemctl restart neold.service 2>/dev/null || true
echo -e "   ${GREEN}✓${NC} Boot autostart service enabled (neold.service)"

echo ""
echo -e "${BLUE}${BOLD}  ╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}${BOLD}  ║         Neo LocalDev helper — Ready!                 ║${NC}"
echo -e "${BLUE}${BOLD}  ╚══════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "  ${BOLD}Boot Autostart:${NC}    ${GREEN}Enabled (neold.service)${NC}"
echo -e "  ${BOLD}Control Command:${NC}   ${CYAN}neold {start|stop|restart|status}${NC}"
echo -e "  ${BOLD}Dashboard (HTTPS):${NC} ${CYAN}https://${DOMAIN}/admin/${NC}"
echo -e "  ${BOLD}Direct API (HTTP):${NC} ${CYAN}http://127.0.0.1:9199/admin/${NC}"
echo ""
