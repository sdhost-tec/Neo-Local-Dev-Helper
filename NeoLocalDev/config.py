import os
import yaml
from pathlib import Path

DEFAULT_CONFIG = {
    "domain": "dev.local",
    "auto_detect": True,
    "watcher_interval": 3,
    "caddy_path": "/usr/bin/caddy",
    "ssl_dir": str(Path.home() / ".NeoLocalDev" / "certs"),
    "caddy_dir": str(Path.home() / ".NeoLocalDev" / "caddy"),
    "log_dir": str(Path.home() / ".NeoLocalDev" / "logs"),
    "admin_username": "admin",
    "admin_password": "admin",
    "projects_root": "",
    "custom_projects": [],
    "renamed_projects": {},
    "allowed_ips": ["127.0.0.1", "::1"]
}

def get_config_dir():
    return Path.home() / ".NeoLocalDev"

def get_config_path():
    return get_config_dir() / "config.yml"

def ensure_dirs():
    cfg = get_config_dir()
    dirs = [cfg, cfg / "certs", cfg / "caddy", cfg / "logs"]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

def get_lan_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None

def load_config():
    path = get_config_path()
    ensure_dirs()
    if not path.exists():
        defaults = dict(DEFAULT_CONFIG)
        lan_ip = get_lan_ip()
        if lan_ip and lan_ip not in defaults["allowed_ips"]:
            defaults["allowed_ips"].append(lan_ip)
        with open(path, "w") as f:
            yaml.dump(defaults, f, default_flow_style=False)
        return defaults
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    
    # Auto-whitelist the host's current LAN IP dynamically so admin doesn't get locked out
    lan_ip = get_lan_ip()
    if "allowed_ips" not in merged:
        merged["allowed_ips"] = ["127.0.0.1", "::1"]
    if lan_ip and lan_ip not in merged["allowed_ips"]:
        merged["allowed_ips"].append(lan_ip)
        # Save back immediately so Caddy grabs the updated whitelist on next load/reload
        save_config(merged)
        
    return merged

def save_config(cfg):
    path = get_config_path()
    ensure_dirs()
    with open(path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)
