import os
import shutil
import subprocess
import urllib.request
import zipfile
import secrets
from pathlib import Path
import logging

logger = logging.getLogger("devpoka")

def get_pma_dir():
    pma_dir = Path.home() / ".NeoLocalDev" / "phpmyadmin"
    return pma_dir

def inspect_system():
    status = {
        "caddy": {"installed": False, "version": None},
        "mariadb": {"installed": False, "version": None},
        "php_fpm": {"installed": False, "version": None},
        "phpmyadmin": {"installed": False, "version": None}
    }
    
    # Check Caddy
    caddy_path = shutil.which("caddy")
    if caddy_path:
        status["caddy"]["installed"] = True
        try:
            out = subprocess.check_output([caddy_path, "version"], text=True)
            status["caddy"]["version"] = out.split()[0]
        except Exception:
            status["caddy"]["version"] = "Unknown"

    # Check MariaDB / MySQL
    mysql_path = shutil.which("mariadb") or shutil.which("mysql")
    if mysql_path:
        status["mariadb"]["installed"] = True
        try:
            out = subprocess.check_output([mysql_path, "--version"], text=True)
            status["mariadb"]["version"] = out.strip().split("Distrib ")[-1].split(",")[0]
        except Exception:
            status["mariadb"]["version"] = "Unknown"

    # Check PHP-FPM
    php_fpm_installed = False
    for path in ["/usr/sbin/php-fpm", "/usr/sbin/php-fpm8.3", "/usr/sbin/php-fpm8.2", "/usr/sbin/php-fpm8.1", "/usr/sbin/php-fpm8.0", "/usr/sbin/php-fpm7.4"]:
        if os.path.exists(path):
            php_fpm_installed = True
            try:
                out = subprocess.check_output([path, "-v"], text=True)
                status["php_fpm"]["version"] = out.split("\n")[0].split()[1]
            except Exception:
                status["php_fpm"]["version"] = "Unknown"
            break
    status["php_fpm"]["installed"] = php_fpm_installed

    # Check phpMyAdmin
    pma_index = get_pma_dir() / "index.php"
    if pma_index.exists():
        status["phpmyadmin"]["installed"] = True
        status["phpmyadmin"]["version"] = "Bundled"

    return status

def install_caddy():
    logger.info("Installing Caddy...")
    try:
        subprocess.check_call(["sudo", "apt-get", "update", "-qq"])
        subprocess.check_call(["sudo", "apt-get", "install", "-y", "-qq", "debian-keyring", "debian-archive-keyring", "apt-transport-https", "curl"])
        
        # Add Caddy GPG and Apt source
        gpg_key = "/usr/share/keyrings/caddy-stable-archive-keyring.gpg"
        if not os.path.exists(gpg_key):
            cmd1 = "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o " + gpg_key
            subprocess.check_call(cmd1, shell=True)
        
        apt_list = "/etc/apt/sources.list.d/caddy-stable.list"
        if not os.path.exists(apt_list):
            cmd2 = "curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee " + apt_list
            subprocess.check_call(cmd2, shell=True)
            
        subprocess.check_call(["sudo", "apt-get", "update", "-qq"])
        subprocess.check_call(["sudo", "apt-get", "install", "-y", "-qq", "caddy"])
        subprocess.check_call(["sudo", "systemctl", "disable", "caddy"])
        # Grant low-port capability
        caddy_bin = shutil.which("caddy") or "/usr/bin/caddy"
        subprocess.check_call(["sudo", "setcap", "cap_net_bind_service=+ep", caddy_bin])
        return True
    except Exception as e:
        logger.error(f"Failed to install Caddy: {e}")
        return False

def install_mariadb():
    logger.info("Installing MariaDB...")
    try:
        subprocess.check_call(["sudo", "apt-get", "update", "-qq"])
        subprocess.check_call(["sudo", "apt-get", "install", "-y", "-qq", "mariadb-server"])
        subprocess.check_call(["sudo", "systemctl", "enable", "mariadb"])
        subprocess.check_call(["sudo", "systemctl", "start", "mariadb"])
        return True
    except Exception as e:
        logger.error(f"Failed to install MariaDB: {e}")
        return False

def install_php_fpm():
    logger.info("Installing PHP-FPM & extensions...")
    try:
        subprocess.check_call(["sudo", "apt-get", "update", "-qq"])
        # Install standard PHP and PHP-FPM along with common extensions needed by phpMyAdmin
        subprocess.check_call(["sudo", "apt-get", "install", "-y", "-qq", "php", "php-fpm", "php-mysql", "php-xml", "php-mbstring", "php-curl", "php-zip"])
        return True
    except Exception as e:
        logger.error(f"Failed to install PHP-FPM: {e}")
        return False

def install_phpmyadmin():
    logger.info("Installing phpMyAdmin...")
    pma_dir = get_pma_dir()
    pma_dir.mkdir(parents=True, exist_ok=True)
    
    zip_path = pma_dir / "pma.zip"
    url = "https://files.phpmyadmin.net/phpMyAdmin/5.2.2/phpMyAdmin-5.2.2-all-languages.zip"
    
    try:
        logger.info(f"Downloading phpMyAdmin from {url}...")
        urllib.request.urlretrieve(url, zip_path)
        
        logger.info("Extracting archive...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(pma_dir)
            
        # Move files from subdirectory to parent
        extracted_folder = pma_dir / "phpMyAdmin-5.2.2-all-languages"
        for item in extracted_folder.iterdir():
            shutil.move(str(item), str(pma_dir / item.name))
        
        extracted_folder.rmdir()
        zip_path.unlink()
        
        # Configure blowfish_secret
        sample_config = pma_dir / "config.sample.inc.php"
        config_file = pma_dir / "config.inc.php"
        if sample_config.exists():
            content = sample_config.read_text()
            secret = secrets.token_hex(16)
            content = content.replace("blowfish_secret'] = ''", f"blowfish_secret'] = '{secret}'")
            # Allow root login without password on localhost for easy dev access
            content = content.replace("AllowNoPassword'] = false", "AllowNoPassword'] = true")
            # Set absolute URI for proper subfolder routing through gateway proxy
            content += "\n$cfg['PmaAbsoluteUri'] = '/pma/';\n"
            config_file.write_text(content)
            
        logger.info("phpMyAdmin installed successfully.")
        return True
    except Exception as e:
        logger.error(f"Failed to install phpMyAdmin: {e}")
        if zip_path.exists():
            zip_path.unlink()
        return False
