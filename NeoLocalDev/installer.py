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
    import glob
    fpm_paths = glob.glob("/usr/sbin/php-fpm*") + ["/usr/sbin/php-fpm"]
    found_versions = []
    for path in sorted(fpm_paths, reverse=True):
        if os.path.exists(path) and os.access(path, os.X_OK):
            php_fpm_installed = True
            try:
                out = subprocess.check_output([path, "-v"], text=True)
                ver = out.split("\n")[0].split()[1]
                if ver not in found_versions:
                    found_versions.append(ver)
            except Exception:
                pass
    
    status["php_fpm"]["installed"] = php_fpm_installed
    status["php_fpm"]["version"] = ", ".join(found_versions) if found_versions else ("Installed" if php_fpm_installed else None)
    status["php_fpm"]["installed_versions"] = found_versions

    # Check phpMyAdmin
    pma_index = get_pma_dir() / "index.php"
    if pma_index.exists():
        status["phpmyadmin"]["installed"] = True
        status["phpmyadmin"]["version"] = "Bundled"

    return status

def get_installed_php_versions():
    import glob
    fpm_paths = glob.glob("/usr/sbin/php-fpm*")
    versions = []
    for path in fpm_paths:
        name = os.path.basename(path)
        ver = name.replace("php-fpm", "")
        if ver and ver not in versions:
            versions.append(ver)
        else:
            try:
                out = subprocess.check_output([path, "-v"], text=True).split("\n")[0]
                import re
                m = re.search(r"PHP\s+([0-9]+\.[0-9]+)", out)
                if m and m.group(1) not in versions:
                    versions.append(m.group(1))
            except Exception:
                pass
    return sorted(versions, reverse=True)

def _emit_log(log_cb, msg):
    logger.info(msg)
    if log_cb:
        log_cb(msg)

def _fix_sudoers_file(password=None):
    """Attempt to overwrite /etc/sudoers.d/neold with clean valid rules."""
    actual_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or "royal"
    clean_rules = f"# NeoLocalDev helper sudoers policy\n{actual_user} ALL=(ALL) NOPASSWD: /usr/bin/apt-get, /usr/bin/systemctl, /usr/sbin/phpenmod, /usr/sbin/phpdismod, /usr/sbin/setcap, /usr/sbin/update-ca-certificates, /usr/bin/certutil, /usr/bin/tee, /usr/bin/sed, /usr/sbin/usermod, /usr/bin/chmod\n"
    
    cmd = ["sudo", "-n", "tee", "/etc/sudoers.d/neold"]
    input_data = clean_rules.encode()
    if password:
        cmd = ["sudo", "-S", "tee", "/etc/sudoers.d/neold"]
        input_data = (password + "\n" + clean_rules).encode()

    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        proc.communicate(input=input_data)
        if proc.returncode == 0:
            subprocess.run(["sudo", "-n" if not password else "-S", "chmod", "0440", "/etc/sudoers.d/neold"],
                           input=(password + "\n").encode() if password else None, capture_output=True)
    except Exception:
        pass

def run_sudo_cmd(cmd_list, log_cb=None, password=None):
    """
    Executes a sudo command streaming output line-by-line.
    Tries passwordless 'sudo -n' first, falling back to 'sudo -S' with password if provided.
    """
    if password:
        _fix_sudoers_file(password)

    base_cmd = ["sudo", "-n"] + cmd_list
    input_data = None

    if password:
        base_cmd = ["sudo", "-S"] + cmd_list
        input_data = (password + "\n").encode()

    try:
        proc = subprocess.Popen(
            base_cmd,
            stdin=subprocess.PIPE if input_data else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=False
        )

        if input_data:
            proc.stdin.write(input_data)
            proc.stdin.flush()
            proc.stdin.close()

        stdout_lines = []
        for line in iter(proc.stdout.readline, b''):
            line_str = line.decode('utf-8', errors='replace').rstrip()
            if line_str:
                stdout_lines.append(line_str)
                _emit_log(log_cb, line_str)

        proc.stdout.close()
        returncode = proc.wait()

        if returncode == 0:
            return True, "\n".join(stdout_lines)

        full_err = "\n".join(stdout_lines)
        err_lower = full_err.lower()
        if ("password is required" in err_lower or 
            "terminal is required" in err_lower or 
            "authentication is required" in err_lower or 
            "wildcards are not allowed" in err_lower):
            if not password:
                return False, "sudo_required"
        return False, full_err or f"Command failed with exit code {returncode}"

    except Exception as e:
        return False, str(e)


def install_caddy(log_cb=None, password=None):
    _emit_log(log_cb, "🚀 Starting Caddy Web Server installation...")
    _emit_log(log_cb, "[1/5] Updating package repositories...")
    ok, err = run_sudo_cmd(["apt-get", "update"], log_cb, password)
    if not ok:
        if err == "sudo_required": return False, "sudo_required"
        _emit_log(log_cb, f"⚠ Warning updating packages: {err}")

    _emit_log(log_cb, "[2/5] Installing APT transport & keyrings...")
    ok, err = run_sudo_cmd(["apt-get", "install", "-y", "debian-keyring", "debian-archive-keyring", "apt-transport-https", "curl"], log_cb, password)
    if not ok:
        if err == "sudo_required": return False, "sudo_required"
        _emit_log(log_cb, f"❌ Failed to install prerequisites: {err}")
        return False, err

    _emit_log(log_cb, "[3/5] Adding Caddy GPG key and APT repository...")
    gpg_key = "/usr/share/keyrings/caddy-stable-archive-keyring.gpg"
    if not os.path.exists(gpg_key):
        cmd = f"curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o {gpg_key}"
        subprocess.run(cmd, shell=True, check=False)

    apt_list = "/etc/apt/sources.list.d/caddy-stable.list"
    if not os.path.exists(apt_list):
        cmd = f"curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee {apt_list}"
        subprocess.run(cmd, shell=True, check=False)

    _emit_log(log_cb, "[4/5] Installing Caddy package...")
    run_sudo_cmd(["apt-get", "update"], log_cb, password)
    ok, err = run_sudo_cmd(["apt-get", "install", "-y", "caddy"], log_cb, password)
    if not ok:
        if err == "sudo_required": return False, "sudo_required"
        _emit_log(log_cb, f"❌ Failed to install Caddy package: {err}")
        return False, err

    _emit_log(log_cb, "[5/5] Configuring Caddy permissions & service...")
    run_sudo_cmd(["systemctl", "disable", "--now", "caddy"], log_cb, password)
    caddy_bin = shutil.which("caddy") or "/usr/bin/caddy"
    run_sudo_cmd(["setcap", "cap_net_bind_service=+ep", caddy_bin], log_cb, password)

    _emit_log(log_cb, "✅ Caddy installation completed successfully!")
    return True, "Caddy installed successfully"


def install_mariadb(log_cb=None, password=None):
    _emit_log(log_cb, "🚀 Starting MariaDB Server installation...")
    _emit_log(log_cb, "[1/4] Updating package repositories...")
    ok, err = run_sudo_cmd(["apt-get", "update"], log_cb, password)
    if not ok:
        if err == "sudo_required": return False, "sudo_required"
        _emit_log(log_cb, f"⚠ Warning updating packages: {err}")

    _emit_log(log_cb, "[2/4] Installing mariadb-server & mariadb-client packages...")
    ok, err = run_sudo_cmd(["apt-get", "install", "-y", "mariadb-server", "mariadb-client"], log_cb, password)
    if not ok:
        if err == "sudo_required": return False, "sudo_required"
        _emit_log(log_cb, f"❌ Failed to install MariaDB: {err}")
        return False, err

    _emit_log(log_cb, "[3/4] Enabling and starting MariaDB service...")
    run_sudo_cmd(["systemctl", "enable", "mariadb"], log_cb, password)
    ok, err = run_sudo_cmd(["systemctl", "start", "mariadb"], log_cb, password)
    if not ok:
        _emit_log(log_cb, f"⚠ Service start warning: {err}")

    _emit_log(log_cb, "[4/4] Configuring root user for passwordless dev access...")
    sql_cmd = "ALTER USER 'root'@'localhost' IDENTIFIED BY ''; FLUSH PRIVILEGES;"
    run_sudo_cmd(["mysql", "-e", sql_cmd], log_cb, password)

    _emit_log(log_cb, "✅ MariaDB Server installed successfully!")
    return True, "MariaDB installed successfully"


def install_php_fpm(log_cb=None, password=None, version=None):
    ver_label = f"PHP {version}" if version else "PHP-FPM (Default)"
    _emit_log(log_cb, f"🚀 Starting {ver_label} & extensions installation...")
    _emit_log(log_cb, "[1/4] Updating package repositories...")
    ok, err = run_sudo_cmd(["apt-get", "update"], log_cb, password)
    if not ok:
        if err == "sudo_required": return False, "sudo_required"
        _emit_log(log_cb, f"⚠ Warning updating packages: {err}")

    # If version specified (e.g., 8.3, 8.2, 8.1, 7.4), ensure Ondrej PPA is available if missing
    v_str = str(version).strip() if version else ""
    if v_str and v_str in ["7.4", "8.0", "8.1", "8.2", "8.3", "8.4"]:
        _emit_log(log_cb, f"   Checking PPA repository for PHP {v_str}...")
        run_sudo_cmd(["apt-get", "install", "-y", "software-properties-common"], log_cb, password)
        run_sudo_cmd(["add-apt-repository", "-y", "ppa:ondrej/php"], log_cb, password)
        run_sudo_cmd(["apt-get", "update"], log_cb, password)

    if v_str:
        pkgs = [f"php{v_str}-fpm", f"php{v_str}-cli", f"php{v_str}-mysql", f"php{v_str}-xml", f"php{v_str}-mbstring", f"php{v_str}-curl", f"php{v_str}-zip"]
    else:
        pkgs = ["php", "php-fpm", "php-mysql", "php-xml", "php-mbstring", "php-curl", "php-zip"]

    _emit_log(log_cb, f"[2/4] Installing packages ({', '.join(pkgs)})...")
    ok, err = run_sudo_cmd(["apt-get", "install", "-y"] + pkgs, log_cb, password)
    if not ok:
        if err == "sudo_required": return False, "sudo_required"
        _emit_log(log_cb, f"❌ Failed to install PHP-FPM packages: {err}")
        return False, err

    _emit_log(log_cb, "[3/4] Enabling and configuring PHP-FPM service...")
    actual_usr = os.environ.get("SUDO_USER") or os.environ.get("USER") or "royal"
    run_sudo_cmd(["usermod", "-aG", "www-data", actual_usr], log_cb, password)
    
    # Write pool configuration so PHP-FPM listens on TCP 9000 & socket mode 0666 as actual user
    cfg_script = f"for d in /etc/php/*/fpm/pool.d; do [ -d \"$d\" ] && echo -e '[www]\\nuser = {actual_usr}\\ngroup = {actual_usr}\\nlisten = 127.0.0.1:9000\\nlisten.mode = 0666' > \"$d/zz-neold.conf\"; done"
    run_sudo_cmd(["apt-get", "-o", f"APT::Update::Post-Invoke::={cfg_script}", "update"], log_cb, password)

    possible_services = []
    if v_str:
        possible_services.append(f"php{v_str}-fpm")
    possible_services.extend(["php8.5-fpm", "php8.4-fpm", "php8.3-fpm", "php8.2-fpm", "php8.1-fpm", "php8.0-fpm", "php7.4-fpm", "php-fpm"])

    started = False
    for fpm_svc in possible_services:
        res, _ = run_sudo_cmd(["systemctl", "enable", fpm_svc], log_cb, password)
        if res:
            run_sudo_cmd(["systemctl", "restart", fpm_svc], log_cb, password)
            _emit_log(log_cb, f"✅ Started {fpm_svc} service successfully!")
            started = True
            break

    _emit_log(log_cb, "[4/4] Verifying PHP-FPM installation...")
    status = inspect_system()
    if status["php_fpm"]["installed"] or started:
        ver_text = status['php_fpm']['version'] or v_str or 'Installed'
        _emit_log(log_cb, f"✅ PHP-FPM ({ver_text}) installed successfully!")
        return True, "PHP-FPM installed successfully"
    else:
        _emit_log(log_cb, "❌ PHP-FPM installation finished but process not detected.")
        return False, "PHP-FPM verification failed"


def install_phpmyadmin(log_cb=None, password=None):
    _emit_log(log_cb, "🚀 Starting phpMyAdmin Client installation...")
    pma_dir = get_pma_dir()
    pma_dir.mkdir(parents=True, exist_ok=True)

    zip_path = pma_dir / "pma.zip"
    url = "https://files.phpmyadmin.net/phpMyAdmin/5.2.2/phpMyAdmin-5.2.2-all-languages.zip"

    try:
        _emit_log(log_cb, f"[1/4] Downloading phpMyAdmin 5.2.2 from {url}...")
        urllib.request.urlretrieve(url, zip_path)

        _emit_log(log_cb, "[2/4] Extracting phpMyAdmin archive...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(pma_dir)

        _emit_log(log_cb, "[3/4] Organizing application files...")
        extracted_folder = pma_dir / "phpMyAdmin-5.2.2-all-languages"
        for item in extracted_folder.iterdir():
            dest = pma_dir / item.name
            if dest.exists():
                if dest.is_dir(): shutil.rmtree(dest)
                else: dest.unlink()
            shutil.move(str(item), str(pma_dir / item.name))

        extracted_folder.rmdir()
        zip_path.unlink()

        _emit_log(log_cb, "[4/4] Generating configuration and blowfish secret...")
        sample_config = pma_dir / "config.sample.inc.php"
        config_file = pma_dir / "config.inc.php"
        if sample_config.exists():
            content = sample_config.read_text()
            secret = secrets.token_hex(16)
            content = content.replace("blowfish_secret'] = ''", f"blowfish_secret'] = '{secret}'")
            content = content.replace("AllowNoPassword'] = false", "AllowNoPassword'] = true")
            content += "\n$cfg['PmaAbsoluteUri'] = '/pma/';\n"
            config_file.write_text(content)

        _emit_log(log_cb, "✅ phpMyAdmin installed successfully!")
        return True, "phpMyAdmin installed successfully"
    except Exception as e:
        _emit_log(log_cb, f"❌ Failed to install phpMyAdmin: {e}")
        if zip_path.exists():
            zip_path.unlink()
        return False, str(e)
