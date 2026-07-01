import subprocess
import shutil
import socket
import logging
import os
from pathlib import Path

logger = logging.getLogger("devpoka")

def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None

def ensure_mkcert():
    mkcert = shutil.which("mkcert")
    if not mkcert:
        # Check standard paths
        for path in ["/usr/local/bin/mkcert", "/usr/bin/mkcert"]:
            if Path(path).exists():
                return path
        raise RuntimeError("mkcert not found. Please install it using setup.sh or your package manager.")
    return mkcert

def trust_system_and_browsers():
    try:
        mkcert = ensure_mkcert()
        logger.info("Installing mkcert CA to system and browser trust stores...")

        # Determine the actual non-root user so NSS stores (Chrome/Firefox) get updated correctly.
        # When called via sudo, SUDO_USER points to the real user.
        actual_user = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
        env = os.environ.copy()

        if actual_user and actual_user != "root":
            import pwd
            try:
                pw = pwd.getpwnam(actual_user)
                env["HOME"] = pw.pw_dir
                env["USER"] = actual_user
                env["LOGNAME"] = actual_user
            except KeyError:
                pass

        result = subprocess.run(
            [mkcert, "-install"],
            capture_output=True, text=True, check=True,
            env=env
        )
        logger.info(result.stdout.strip())
        # Export rootCA.pem to project root automatically
        project_root = Path(__file__).resolve().parent.parent
        export_rootca_to_project(str(project_root))
        return True
    except Exception as e:
        logger.error(f"Failed to install CA trust: {e}")
        return False

def get_caroot():
    """Return the mkcert CA root directory path."""
    try:
        mkcert = ensure_mkcert()
        result = subprocess.run([mkcert, "-CAROOT"], capture_output=True, text=True)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def export_rootca_to_project(project_root):
    """
    Copy the mkcert rootCA.pem to the project root directory so
    it can be installed on mobile devices and other LAN machines.
    Returns the destination path or None on failure.
    """
    caroot = get_caroot()
    if not caroot:
        logger.error("Could not determine mkcert CAROOT directory.")
        return None

    src = Path(caroot) / "rootCA.pem"
    if not src.exists():
        logger.error(f"rootCA.pem not found at {src}. Run 'mkcert -install' first.")
        return None

    dest = Path(project_root) / "rootCA.pem"
    try:
        import shutil as _shutil
        _shutil.copy2(str(src), str(dest))
        dest.chmod(0o644)
        logger.info(f"rootCA.pem exported to {dest}")
        return str(dest)
    except Exception as e:
        logger.error(f"Failed to export rootCA.pem: {e}")
        return None


def ensure_certs(domain, ssl_dir):
    ssl_path = Path(ssl_dir)
    ssl_path.mkdir(parents=True, exist_ok=True)

    cert_file = ssl_path / f"{domain}.pem"
    key_file = ssl_path / f"{domain}-key.pem"
    ip_file = ssl_path / "last_ip.txt"

    lan_ip = get_lan_ip()
    last_ip = ip_file.read_text().strip() if ip_file.exists() else None

    # If IP has changed, force regeneration of certificates
    if lan_ip != last_ip:
        logger.info(f"LAN IP changed from {last_ip} to {lan_ip}. Regenerating certificates...")
        cert_file.unlink(missing_ok=True)
        key_file.unlink(missing_ok=True)
        if lan_ip:
            ip_file.write_text(lan_ip)
        else:
            ip_file.unlink(missing_ok=True)

    if cert_file.exists() and key_file.exists():
        logger.info(f"SSL certs already exist and are valid for {domain}")
        return str(cert_file), str(key_file)

    mkcert = ensure_mkcert()
    hosts = [domain, "localhost", "127.0.0.1"]
    if lan_ip:
        hosts.append(lan_ip)

    logger.info(f"Creating SSL certs for {domain} with hosts: {hosts}")
    result = subprocess.run(
        [mkcert, "-cert-file", str(cert_file), "-key-file", str(key_file)] + hosts,
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"mkcert failed: {result.stderr}")

    logger.info(f"SSL certs created successfully for {domain}")
    return str(cert_file), str(key_file)
