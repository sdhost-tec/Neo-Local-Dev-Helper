"""
NeoLocalDev.watcher_daemon
Runs as a standalone background process spawned by cmd_start().
Watches for changes in running dev projects and reloads Caddy proxy config.
"""
import logging
import sys
import time
from pathlib import Path

from . import config as cfgmod
from . import certs as certsmod
from . import detector as detectormod
from . import proxy as proxymod
from .detector import detect_running_projects


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHER] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("NeoLocalDev.watcher_daemon")


def _get_caddy_pid():
    pid_file = Path(cfgmod.get_config_dir()) / "caddy.pid"
    if pid_file.exists():
        try:
            import psutil
            pid = int(pid_file.read_text().strip())
            p = psutil.Process(pid)
            if p.is_running():
                return pid
        except Exception:
            pass
    return None


def _reload_proxy(projects):
    import shutil
    import subprocess
    import psutil

    cfg = cfgmod.load_config()
    domain = cfg["domain"]
    ssl_cert, ssl_key = certsmod.ensure_certs(domain, cfg["ssl_dir"])

    caddyfile = proxymod.generate_caddyfile(
        domain, ssl_cert, ssl_key,
        cfg["caddy_dir"], cfg["log_dir"],
        projects=projects,
    )

    caddy_pid = _get_caddy_pid()
    if caddy_pid:
        # Reload Caddy gracefully using SIGUSR1
        try:
            import signal
            import psutil
            proc = psutil.Process(caddy_pid)
            proc.send_signal(signal.SIGUSR1)
            logger.info("Caddy reloaded via SIGUSR1")
            return
        except Exception as e:
            logger.warning(f"SIGUSR1 reload failed: {e}, restarting Caddy...")

    # Caddy not running — restart it
    caddy_bin = shutil.which("caddy") or cfg.get("caddy_path", "caddy")
    log_dir = cfg.get("log_dir", str(Path.home() / ".NeoLocalDev" / "logs"))
    caddy_log = Path(log_dir) / "caddy.log"
    caddy_log.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [caddy_bin, "run", "--config", caddyfile],
        stdout=open(caddy_log, "a"),
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    pid_file = Path(cfgmod.get_config_dir()) / "caddy.pid"
    pid_file.write_text(str(proc.pid))
    logger.info(f"Caddy restarted (PID: {proc.pid})")


def main():
    logger.info("Watcher daemon started.")
    cfg = cfgmod.load_config()
    interval = cfg.get("watcher_interval", 3)
    last_projects = detect_running_projects()

    while True:
        try:
            current_projects = detect_running_projects()

            has_changed = False
            if len(current_projects) != len(last_projects):
                has_changed = True
            else:
                for p1, p2 in zip(current_projects, last_projects):
                    if (p1["name"] != p2["name"] or
                            p1["port"] != p2["port"] or
                            p1["pid"] != p2["pid"]):
                        has_changed = True
                        break

            if has_changed:
                logger.info(f"Change detected ({len(last_projects)} → {len(current_projects)} projects), reloading proxy...")
                last_projects = current_projects
                _reload_proxy(current_projects)

            time.sleep(interval)
        except Exception as e:
            logger.error(f"Watcher error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
