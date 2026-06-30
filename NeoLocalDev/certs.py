import subprocess
import shutil
import socket
import logging
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
        result = subprocess.run([mkcert, "-install"], capture_output=True, text=True, check=True)
        logger.info(result.stdout.strip())
        return True
    except Exception as e:
        logger.error(f"Failed to install CA trust: {e}")
        return False

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
