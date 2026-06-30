import argparse
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import psutil

from . import config as cfgmod
from . import certs as certsmod
from . import detector as detectormod
from . import proxy as proxymod
from . import db_manager as dbmod
from .api_server import run_api_server
from .watcher import ProjectWatcher

logger = logging.getLogger("devpoka")

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

def get_pid_file(name="caddy"):
    return Path(cfgmod.get_config_dir()) / f"{name}.pid"

def _is_running(name="caddy"):
    pid_file = get_pid_file(name)
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            p = psutil.Process(pid)
            if p.is_running():
                return pid
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            pass
        pid_file.unlink(missing_ok=True)
    return None

def _stop_process(name="caddy"):
    pid = _is_running(name)
    if pid:
        try:
            p = psutil.Process(pid)
            p.terminate()
            p.wait(timeout=5)
        except Exception:
            try:
                psutil.Process(pid).kill()
            except Exception:
                pass
        get_pid_file(name).unlink(missing_ok=True)

def _start_api_server():
    _stop_process("api")
    log_dir = Path(cfgmod.get_config_dir()) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    api_log = log_dir / "api.log"

    proc = subprocess.Popen(
        [sys.executable, "-m", "NeoLocalDev.api_server"],
        stdout=open(api_log, "a"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    get_pid_file("api").write_text(str(proc.pid))
    logger.info(f"API server started (PID: {proc.pid})")
    time.sleep(0.5)
    return proc

def _start_caddy(cfg, caddyfile_path):
    _stop_process("caddy")
    caddy_bin = shutil.which("caddy") or cfg.get("caddy_path", "caddy")
    log_dir = cfg.get("log_dir", str(Path.home() / ".NeoLocalDev" / "logs"))
    caddy_log = Path(log_dir) / "caddy.log"
    caddy_log.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [caddy_bin, "run", "--config", caddyfile_path],
        stdout=open(caddy_log, "a"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    get_pid_file().write_text(str(proc.pid))
    logger.info(f"Caddy started (PID: {proc.pid})")
    time.sleep(1)
    return proc

def _reload_proxy(projects=None):
    cfg = cfgmod.load_config()
    domain = cfg["domain"]
    ssl_cert, ssl_key = certsmod.ensure_certs(domain, cfg["ssl_dir"])

    if projects is None:
        projects = detectormod.detect_running_projects()

    caddyfile = proxymod.generate_caddyfile(
        domain, ssl_cert, ssl_key,
        cfg["caddy_dir"], cfg["log_dir"],
        projects=projects,
    )

    _start_caddy(cfg, caddyfile)
    if not _is_running("api"):
        _start_api_server()
    logger.info("Proxy configuration reloaded and active")

def cmd_setup(args):
    logger.info("Running initial setup...")
    cfgmod.ensure_dirs()
    cfg = cfgmod.load_config()
    domain = cfg["domain"]

    # Trust root CA on setup
    certsmod.trust_system_and_browsers()

    ssl_cert, ssl_key = certsmod.ensure_certs(domain, cfg["ssl_dir"])
    logger.info(f"SSL certs generated successfully at {ssl_cert}")

    projects = detectormod.detect_running_projects()
    caddyfile = proxymod.generate_caddyfile(
        domain, ssl_cert, ssl_key,
        cfg["caddy_dir"], cfg["log_dir"],
        projects=projects,
    )
    logger.info(f"Caddy configuration written to {caddyfile}")

    # Hosts entry check
    hosts_path = "/etc/hosts"
    hosts_entry = f"127.0.0.1 {domain}"
    try:
        with open(hosts_path) as f:
            if hosts_entry not in f.read():
                logger.warning(f"Domain '{domain}' needs mapping in {hosts_path}:")
                logger.warning(f"  Please run: echo '{hosts_entry}' | sudo tee -a {hosts_path}")
    except Exception:
        pass

    logger.info("Setup complete!")
    logger.info(f"Dashboard will be available at: https://{domain}/admin/")

def cmd_start(args):
    logger.info("Starting Neo LocalDev helper...")
    cfg = cfgmod.load_config()
    domain = cfg["domain"]

    ssl_cert, ssl_key = certsmod.ensure_certs(domain, cfg["ssl_dir"])
    projects = detectormod.detect_running_projects()
    caddyfile = proxymod.generate_caddyfile(
        domain, ssl_cert, ssl_key,
        cfg["caddy_dir"], cfg["log_dir"],
        projects=projects,
    )

    # Start MariaDB if stopped and installed
    if not dbmod.is_db_running():
        logger.info("Starting MariaDB database service...")
        dbmod.control_service("start")

    # Only restart Caddy if not already running
    if not _is_running("caddy"):
        _start_caddy(cfg, caddyfile)
    else:
        logger.info("Caddy is already running, skipping restart.")

    # Always ensure API is up
    if not _is_running("api"):
        _start_api_server()
    else:
        logger.info("API server is already running, skipping restart.")

    logger.info(f"Dashboard: https://{domain}/admin/")
    
    # Expose phpMyAdmin link if installed
    pma_dir = Path.home() / ".NeoLocalDev" / "phpmyadmin"
    if (pma_dir / "index.php").exists():
        logger.info(f"phpMyAdmin: https://{domain}/pma/")

    if getattr(args, 'foreground', False):
        # Run watcher in-process (foreground mode)
        logger.info("Running in foreground. Press Ctrl+C to stop.")
        watcher = ProjectWatcher(cfg, _reload_proxy)
        watcher.start()
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            watcher.stop()
            _stop_process("api")
            _stop_process("caddy")
    else:
        # Spawn watcher as a persistent background subprocess so it survives sys.exit(0)
        # Kill any existing watcher daemon first so the new code is loaded
        subprocess.run(["pkill", "-f", "NeoLocalDev.watcher_daemon"], capture_output=True)
        log_dir = Path(cfgmod.get_config_dir()) / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        watcher_log = log_dir / "watcher.log"
        subprocess.Popen(
            [sys.executable, "-m", "NeoLocalDev.watcher_daemon"],
            stdout=open(watcher_log, "a"),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        logger.info("Project auto-discovery watcher started")
        logger.info("DevPoka started in background. Use 'neold stop' to turn it off.")
        sys.exit(0)

def cmd_stop(args):
    logger.info("Stopping Neo LocalDev helper...")
    # Stop watcher daemon subprocess
    subprocess.run(["pkill", "-f", "NeoLocalDev.watcher_daemon"], capture_output=True)
    subprocess.run(["pkill", "-f", "NeoLocalDev.*watcher"], capture_output=True)
    _stop_process("api")
    _stop_process("caddy")
    logger.info("Stopped.")

def cmd_reload(args):
    _reload_proxy()
    logger.info("Reload complete.")

def cmd_status(args):
    cfg = cfgmod.load_config()
    caddy_pid = _is_running("caddy")
    api_pid = _is_running("api")
    lan_ip = certsmod.get_lan_ip()
    db_running = dbmod.is_db_running()
    projects = detectormod.detect_running_projects()

    print(f"\n{'═'*50}")
    print(f"  ⚡ Neo LocalDev helper — Status")
    print(f"{'═'*50}")
    print(f"  Domain:     https://{cfg['domain']}")
    print(f"  LAN Access: https://{lan_ip or 'unknown'}")
    print(f"  Dashboard:  https://{cfg['domain']}/admin/")
    print(f"  Caddy:      {'Running (PID: ' + str(caddy_pid) + ')' if caddy_pid else 'Stopped'}")
    print(f"  API:        {'Running (PID: ' + str(api_pid) + ')' if api_pid else 'Stopped'}")
    print(f"  MariaDB:    {'Running' if db_running else 'Stopped'}")
    
    if projects:
        print("\n  Active Dev Servers (Proxied):")
        for p in projects:
            print(f"    - {p['name']:18s} | Port: {p['port']} ➔ https://{cfg['domain']}:{p['secure_port']} (PID: {p['pid']})")
    else:
        print("\n  Active Dev Servers: None detected.")

    print(f"\n  Config File: {cfgmod.get_config_path()}")
    print(f"{'═'*50}\n")

def cmd_config(args):
    cfg = cfgmod.load_config()
    path = cfgmod.get_config_path()
    print(f"\nConfig file: {path}")
    print(f"{'='*40}")
    for k, v in cfg.items():
        print(f"  {k}: {v}")
    print(f"{'='*40}\n")

def cmd_cert_renew(args):
    logger.info("Renewing SSL certificates...")
    cfg = cfgmod.load_config()
    domain = cfg["domain"]
    ssl_dir = Path(cfg["ssl_dir"])
    cert_file = ssl_dir / f"{domain}.pem"
    key_file = ssl_dir / f"{domain}-key.pem"
    cert_file.unlink(missing_ok=True)
    key_file.unlink(missing_ok=True)
    
    certsmod.ensure_certs(domain, cfg["ssl_dir"])
    logger.info("SSL certificates renewed successfully.")
    
    if _is_running("caddy"):
        _reload_proxy()

def cmd_cert_trust(args):
    logger.info("Repairing system and browser certificate trust...")
    ok = certsmod.trust_system_and_browsers()
    if ok:
        logger.info("Trust stores successfully updated.")
    else:
        logger.error("Failed to repair trust stores.")

def main():
    setup_logging()

    parser = argparse.ArgumentParser(prog="neold", description="Neo LocalDev helper - Lightweight Local Development Gateway")
    parser.add_argument("command", nargs="?", default="status",
                        choices=["setup", "start", "stop", "reload", "status", "config", "cert-renew", "cert-trust"])
    parser.add_argument("-f", "--foreground", action="store_true", help="Keep process alive in foreground")

    args = parser.parse_args()

    commands = {
        "setup": cmd_setup,
        "start": cmd_start,
        "stop": cmd_stop,
        "reload": cmd_reload,
        "status": cmd_status,
        "config": cmd_config,
        "cert-renew": cmd_cert_renew,
        "cert-trust": cmd_cert_trust,
    }

    commands[args.command](args)

if __name__ == "__main__":
    main()

