import os
import psutil
import logging
from pathlib import Path

logger = logging.getLogger("devpoka")

RESERVED_PORTS = {80, 443, 3306, 9000, 9199, 3001, 3002, 3003, 3004, 3005, 3006, 3007, 3008, 3009, 3010}

def get_process_info(pid):
    try:
        p = psutil.Process(pid)
        cwd = p.cwd()
        cmdline = p.cmdline()
        exe = p.exe()
        return p, cwd, cmdline, exe
    except Exception:
        return None, None, None, None

def detect_framework_and_runtime(cwd, cmdline, exe):
    framework = "Generic"
    runtime = "Unknown"

    if not cwd or not os.path.exists(cwd):
        # Fallbacks based on cmdline/exe name
        cmd_str = " ".join(cmdline).lower()
        if "node" in cmd_str:
            runtime = "Node.js"
        elif "python" in cmd_str or "uvicorn" in cmd_str:
            runtime = "Python"
        elif "go" in cmd_str:
            runtime = "Go"
        elif "bun" in cmd_str:
            runtime = "Bun"
        elif "deno" in cmd_str:
            runtime = "Deno"
        return framework, runtime

    cwd_path = Path(cwd)

    # PHP / Laravel / WordPress
    if (cwd_path / "artisan").exists():
        runtime = "PHP"
        framework = "Laravel"
        return framework, runtime
    if (cwd_path / "wp-config.php").exists():
        runtime = "PHP"
        framework = "WordPress"
        return framework, runtime

    # Node / React / Vue / Next / Express
    if (cwd_path / "package.json").exists():
        runtime = "Node.js"
        cmd_str = " ".join(cmdline).lower()
        if "next" in cmd_str:
            framework = "Next.js"
        elif "nuxt" in cmd_str:
            framework = "Nuxt"
        elif "vite" in cmd_str:
            framework = "Vite"
        elif "express" in cmd_str:
            framework = "Express"
        else:
            framework = "Node App"
        return framework, runtime

    # Python / FastAPI / Django / Flask
    if (cwd_path / "manage.py").exists():
        runtime = "Python"
        framework = "Django"
        return framework, runtime
    
    cmd_str = " ".join(cmdline).lower()
    if "fastapi" in cmd_str or "uvicorn" in cmd_str:
        runtime = "Python"
        framework = "FastAPI"
        return framework, runtime
    if "flask" in cmd_str:
        runtime = "Python"
        framework = "Flask"
        return framework, runtime
    if "python" in cmd_str:
        runtime = "Python"
        return framework, runtime

    # Bun / Deno
    if "bun" in cmd_str:
        runtime = "Bun"
        return framework, runtime
    if "deno" in cmd_str:
        runtime = "Deno"
        return framework, runtime

    # Go
    if "go" in cmd_str or "go.mod" in os.listdir(cwd):
        runtime = "Go"
        return framework, runtime

    return framework, runtime

IGNORE_PROCESSES = {
    "language_server", "antigravity-ide", "gnome-remote-desktop", "gnome-remote-de",
    "vscode", "chrome", "firefox", "copilot", "node-lsp", "tabnine", "python-lsp",
    "unattended-upgrades"
}

def detect_running_projects():
    projects = []
    seen_ports = set()

    try:
        connections = psutil.net_connections(kind="tcp")
    except Exception as e:
        logger.error(f"Failed to fetch net connections: {e}")
        return []

    # Filter only LISTEN connections
    listen_conns = [c for c in connections if c.status == "LISTEN"]

    for conn in listen_conns:
        port = conn.laddr.port
        if port in RESERVED_PORTS or port in seen_ports:
            continue
        
        # Skip ephemeral high ports unless they belong to recognizable runtimes
        # Standard web dev ports are usually < 30000 or specific like 3000, 5000, 8000, 8080
        if port > 30000:
            # Let's still scan them, but apply strict process filters
            pass

        seen_ports.add(port)
        pid = conn.pid
        if not pid:
            continue

        p, cwd, cmdline, exe = get_process_info(pid)
        if not p:
            continue

        # Ignore IDE background and system processes
        p_name = p.name().lower()
        cmd_str = " ".join(cmdline).lower() if cmdline else ""
        if any(ignored in p_name or ignored in cmd_str for ignored in IGNORE_PROCESSES):
            continue

        framework, runtime = detect_framework_and_runtime(cwd, cmdline, exe)
        project_name = Path(cwd).name if cwd else f"app-{port}"


        projects.append({
            "name": project_name,
            "path": cwd or "Unknown",
            "port": port,
            "pid": pid,
            "framework": framework,
            "runtime": runtime
        })

    # Deduplicate: if same project path shows up multiple times, keep only the primary (lowest) port
    seen_paths = {}
    deduped = []
    for proj in projects:
        proj_path = proj["path"]
        if proj_path not in seen_paths:
            seen_paths[proj_path] = True
            deduped.append(proj)
    projects = deduped

    # Sort by port to ensure stable sequential mapping
    projects.sort(key=lambda x: x["port"])

    # Assign secure ports starting from 3001
    start_secure_port = 3001
    for i, proj in enumerate(projects):
        proj["secure_port"] = start_secure_port + i

    return projects

def check_port_conflict(port):
    try:
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status == "LISTEN" and conn.laddr.port == port:
                return True
    except Exception:
        pass
    return False
