import json
import logging
import socket
import subprocess
import sys
import time
import os
import secrets
import signal
import threading
import urllib.request
import urllib.error
import concurrent.futures
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

import psutil

from . import config as cfgmod
from . import certs as certsmod
from . import detector as detectormod
from . import db_manager as dbmod
from . import installer as instmod

logger = logging.getLogger("devpoka.api")

API_PORT = 9199
START_TIME = time.time()
ACTIVE_SESSIONS = set()
PENDING_GUARDIAN_REQUESTS = set()

def load_sessions():
    global ACTIVE_SESSIONS
    sess_file = Path(cfgmod.get_config_dir()) / "sessions.json"
    if sess_file.exists():
        try:
            ACTIVE_SESSIONS = set(json.loads(sess_file.read_text()))
        except Exception:
            ACTIVE_SESSIONS = set()

def save_sessions():
    sess_file = Path(cfgmod.get_config_dir()) / "sessions.json"
    try:
        sess_file.write_text(json.dumps(list(ACTIVE_SESSIONS)))
    except Exception:
        pass

load_sessions()
SPAWNED_PROJECTS = {}

def discover_projects_in_root(root_path):
    if not root_path:
        return []
    root = Path(root_path)
    if not root.exists() or not root.is_dir():
        return []
    
    cfg = cfgmod.load_config()
    renames = cfg.get("renamed_projects", {})
    
    discovered = []
    try:
        for item in root.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                p_type = "Generic"
                cmds = []
                
                if (item / "package.json").exists():
                    p_type = "Node"
                    cmds = ["npm run dev", "npm start", "npm install", "npm run build"]
                elif (item / "requirements.txt").exists() or (item / "pyproject.toml").exists() or (item / "manage.py").exists():
                    p_type = "Python"
                    cmds = ["python3 main.py", "python3 app.py", "python3 manage.py runserver", "pip install -r requirements.txt"]
                elif (item / "composer.json").exists() or (item / "artisan").exists():
                    p_type = "PHP"
                    cmds = ["php artisan serve", "php -S 127.0.0.1:8000"]
                elif (item / "index.html").exists():
                    p_type = "Static HTML"
                    cmds = ["python3 -m http.server 8080"]
                else:
                    cmds = ["python3 -m http.server 8080"]
                    
                path_str = str(item)
                name = renames.get(path_str, item.name)
                discovered.append({
                    "name": name,
                    "path": path_str,
                    "type": p_type,
                    "commands": cmds
                })
    except Exception as e:
        logger.error(f"Error discovering projects: {e}")
    return discovered

def detect_single_project(dir_path):
    path = Path(dir_path)
    if not path.exists() or not path.is_dir():
        return None
    p_type = "Generic"
    cmds = []
    
    if (path / "package.json").exists():
        p_type = "Node"
        cmds = ["npm run dev", "npm start", "npm install", "npm run build"]
    elif (path / "requirements.txt").exists() or (path / "pyproject.toml").exists() or (path / "manage.py").exists():
        p_type = "Python"
        cmds = ["python3 main.py", "python3 app.py", "python3 manage.py runserver", "pip install -r requirements.txt"]
    elif (path / "composer.json").exists() or (path / "artisan").exists():
        p_type = "PHP"
        cmds = ["php artisan serve", "php -S 127.0.0.1:8000"]
    elif (path / "index.html").exists():
        p_type = "Static HTML"
        cmds = ["python3 -m http.server 8080"]
    else:
        cmds = ["python3 -m http.server 8080"]
        
    return {
        "name": path.name,
        "path": str(path),
        "type": p_type,
        "commands": cmds
    }

def run_stress_test(url, num_requests=100, concurrency=10):
    success = 0
    failures = 0
    latencies = []
    
    def fetch_one():
        nonlocal success, failures
        start = time.time()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'NeoLD Stress Tester'})
            with urllib.request.urlopen(req, timeout=3) as r:
                r.read()
                latencies.append(time.time() - start)
                success += 1
        except Exception:
            failures += 1
            
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(fetch_one) for _ in range(num_requests)]
        concurrent.futures.wait(futures)
        
    avg_latency = (sum(latencies) / len(latencies)) * 1000 if latencies else 0
    return {
        "success": success,
        "failures": failures,
        "total": num_requests,
        "avg_latency_ms": round(avg_latency, 2),
        "success_rate": round((success / num_requests) * 100, 2) if num_requests else 0
    }

def get_spawned_projects():
    # Clean up dead processes
    dead_pids = []
    for pid, proc in list(SPAWNED_PROJECTS.items()):
        if proc["process"].poll() is not None:
            dead_pids.append(pid)
    for pid in dead_pids:
        try:
            del SPAWNED_PROJECTS[pid]
        except KeyError:
            pass
        
    res = []
    for pid, p in SPAWNED_PROJECTS.items():
        pids = [pid]
        try:
            proc = psutil.Process(pid)
            for child in proc.children(recursive=True):
                pids.append(child.pid)
        except Exception:
            pass
        res.append({
            "pid": pid,
            "pids": pids,
            "name": p["name"],
            "path": p["path"],
            "command": p["command"],
            "uptime": int(time.time() - p["start_time"])
        })
    return res

def check_auth(headers):
    auth = headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        token = auth.split(" ")[1]
        if token in ACTIVE_SESSIONS:
            return True
    return False

def get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"

def kill_process(pid):
    try:
        proc = psutil.Process(pid)
        proc.terminate()
        proc.wait(timeout=3)
        return True
    except Exception:
        try:
            psutil.Process(pid).kill()
            return True
        except Exception:
            return False

class DashboardAPIHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logger.info(f"{self.client_address[0]} - {format % args}")

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_html(self, html, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(html.encode())

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length > 0:
            return json.loads(self.rfile.read(length))
        return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def _path(self):
        p = self.path.split("?")[0]
        prefix = "/admin"
        if p.startswith(prefix):
            p = p[len(prefix):] or "/"
        return p

    def _parse_query(self):
        if "?" not in self.path:
            return {}
        import urllib.parse
        qs = self.path.split("?", 1)[1]
        params = {}
        for part in qs.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                params[urllib.parse.unquote(k)] = urllib.parse.unquote(v)
        return params

    def do_GET(self):
        cfg = cfgmod.load_config()
        path = self._path()

        if path == "/admin/neo.png" or path == "/neo.png":
            png_path = Path(__file__).resolve().parent.parent / "neo.png"
            if png_path.exists():
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(png_path.read_bytes())
            else:
                self.send_response(404)
                self.end_headers()
            return

        if path == "/guardian/unauthorized":
            client_ip = self.headers.get("X-Forwarded-For", self.client_address[0])
            # Strip port if present
            client_ip = client_ip.split(",")[0].strip().split(":")[0]
            html = GUARDIAN_UNAUTHORIZED_HTML.replace("{client_ip}", client_ip)
            self._send_html(html)
            return

        if path.startswith("/api/"):
            if not check_auth(self.headers):
                self._send_json({"ok": False, "error": "unauthorized"}, 401)
                return

        if path == "/api/status":
            projects = detectormod.detect_running_projects()
            system_status = instmod.inspect_system()
            lan_ip = get_lan_ip()
            
            # Additional running states
            system_status["caddy"]["running"] = self._caddy_running()
            system_status["mariadb"]["running"] = dbmod.is_db_running()
            system_status["php_fpm"]["running"] = self._php_fpm_running()
            system_status["phpmyadmin"]["running"] = system_status["php_fpm"]["running"] and system_status["phpmyadmin"]["installed"]

            # System resource metrics
            stats = {
                "cpu": psutil.cpu_percent(),
                "ram": psutil.virtual_memory().percent,
                "disk": psutil.disk_usage('/').percent
            }

            self._send_json({
                "ok": True,
                "domain": cfg["domain"],
                "lan_ip": lan_ip,
                "system": system_status,
                "projects": projects,
                "stats": stats,
                "allowed_ips": cfg.get("allowed_ips", ["127.0.0.1", "::1"]),
                "uptime": time.time() - START_TIME
            })

        elif path == "/api/db/databases":
            if not dbmod.is_db_running():
                self._send_json({"ok": False, "error": "Database server is not running"}, 400)
                return
            dbs = dbmod.list_databases()
            self._send_json({"ok": True, "databases": dbs})

        elif path == "/api/db/users":
            if not dbmod.is_db_running():
                self._send_json({"ok": False, "error": "Database server is not running"}, 400)
                return
            users = dbmod.list_users()
            self._send_json({"ok": True, "users": users})

        elif path == "/api/logs":
            qs = self._parse_query()
            log_type = qs.get("type", "access")
            lines_count = int(qs.get("lines", "100"))
            fname = {"access": "caddy-access.log", "error": "caddy.log", "api": "api.log"}
            log_file = Path(cfg["log_dir"]) / fname.get(log_type, "caddy-access.log")
            lines = []
            if log_file.exists():
                lines = log_file.read_text().splitlines()[-lines_count:]
            self._send_json({"ok": True, "lines": lines, "file": log_file.name, "count": len(lines)})

        elif path == "/api/projects/discover":
            root_path = cfg.get("projects_root", "")
            discovered = discover_projects_in_root(root_path)
            custom_list = cfg.get("custom_projects", [])
            renames = cfg.get("renamed_projects", {})
            for p in custom_list:
                p["name"] = renames.get(p["path"], p["name"])
            self._send_json({
                "ok": True, 
                "projects_root": root_path, 
                "projects": discovered,
                "custom_projects": custom_list
            })

        elif path == "/api/fs/list":
            qs = self._parse_query()
            target_path = qs.get("path", "").strip()
            if not target_path:
                target_path = str(Path.home())
            
            p = Path(target_path)
            if not p.exists() or not p.is_dir():
                self._send_json({"ok": False, "error": "Invalid directory path"}, 400)
                return
            
            subdirs = []
            try:
                parent = str(p.parent) if p.parent != p else ""
                for item in sorted(p.iterdir(), key=lambda x: x.name.lower()):
                    try:
                        if item.is_dir() and not item.name.startswith("."):
                            subdirs.append({
                                "name": item.name,
                                "path": str(item)
                            })
                    except Exception:
                        pass
                self._send_json({
                    "ok": True,
                    "current": str(p),
                    "parent": parent,
                    "dirs": subdirs
                })
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)

        elif path == "/api/project/spawned/list":
            self._send_json({"ok": True, "projects": get_spawned_projects()})

        elif path == "/api/guardian/pending":
            self._send_json({"ok": True, "requests": list(PENDING_GUARDIAN_REQUESTS)})

        elif path == "/api/php/extensions":
            exts = get_php_extensions()
            self._send_json({"ok": True, "extensions": exts})

        elif path == "/api/project/monitor":
            qs = self._parse_query()
            pid = int(qs.get("pid", "0"))
            if not pid:
                self._send_json({"ok": False, "error": "PID is required"}, 400)
                return
            try:
                proc = psutil.Process(pid)
                cpu = proc.cpu_percent(interval=None)
                # Convert RAM to percent of system total
                mem = (proc.memory_info().rss / psutil.virtual_memory().total) * 100
                for child in proc.children(recursive=True):
                    try:
                        cpu += child.cpu_percent(interval=None)
                        mem += (child.memory_info().rss / psutil.virtual_memory().total) * 100
                    except Exception:
                        pass
                self._send_json({"ok": True, "cpu": round(cpu, 1), "ram": round(mem, 1)})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 400)

        elif path == "/api/project/logs":
            qs = self._parse_query()
            pid = int(qs.get("pid", "0"))
            if not pid:
                self._send_json({"ok": False, "error": "PID is required"}, 400)
                return
            log_file = Path(cfg["log_dir"]) / f"project-{pid}.log"
            lines = []
            if log_file.exists():
                try:
                    lines = log_file.read_text().splitlines()[-100:]
                except Exception as e:
                    lines = [f"Error reading log file: {e}"]
            else:
                lines = ["No log output captured yet."]
            self._send_json({"ok": True, "lines": lines})

        elif path == "/" or path == "":
            self._send_html(DASHBOARD_HTML)

        else:
            self._send_json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        cfg = cfgmod.load_config()
        path = self._path()

        if path == "/guardian/request":
            body = self._read_body()
            ip = body.get("ip")
            if ip:
                ip = ip.split(":")[0].strip()
                PENDING_GUARDIAN_REQUESTS.add(ip)
                self._send_json({"ok": True})
            else:
                self._send_json({"ok": False, "error": "IP is required"}, 400)
            return

        if path == "/api/login":
            body = self._read_body()
            username = body.get("username")
            password = body.get("password")
            expected_user = cfg.get("admin_username", "admin")
            expected_pass = cfg.get("admin_password", "admin")
            if username == expected_user and password == expected_pass:
                token = secrets.token_hex(24)
                ACTIVE_SESSIONS.add(token)
                save_sessions()
                self._send_json({"ok": True, "token": token})
            else:
                self._send_json({"ok": False, "error": "Invalid username or password"}, 401)
            return

        if path.startswith("/api/"):
            if not check_auth(self.headers):
                self._send_json({"ok": False, "error": "unauthorized"}, 401)
                return

        if path == "/api/security/update":
            body = self._read_body()
            new_user = body.get("username")
            new_pass = body.get("password")
            if not new_user or not new_pass:
                self._send_json({"ok": False, "error": "Username and password are required"}, 400)
                return
            
            cfg["admin_username"] = new_user
            cfg["admin_password"] = new_pass
            cfgmod.save_config(cfg)
            self._send_json({"ok": True, "message": "Security settings updated successfully"})
            return

        if path == "/api/guardian/approve":
            body = self._read_body()
            ip = body.get("ip")
            if not ip:
                self._send_json({"ok": False, "error": "IP is required"}, 400)
                return
            allowed = cfg.get("allowed_ips", ["127.0.0.1", "::1"])
            if ip not in allowed:
                allowed.append(ip)
                cfg["allowed_ips"] = allowed
                cfgmod.save_config(cfg)
                # Remove from pending requests
                PENDING_GUARDIAN_REQUESTS.discard(ip)
                # Reload proxy to apply the new IP whitelist immediately
                self._trigger_reload()
            self._send_json({"ok": True, "message": f"IP '{ip}' approved successfully."})
            return

        elif path == "/api/guardian/reject":
            body = self._read_body()
            ip = body.get("ip")
            if not ip:
                self._send_json({"ok": False, "error": "IP is required"}, 400)
                return
            PENDING_GUARDIAN_REQUESTS.discard(ip)
            self._send_json({"ok": True, "message": f"Request from '{ip}' rejected."})
            return

        elif path == "/api/php/extension/toggle":
            body = self._read_body()
            name = body.get("name")
            enable = body.get("enable")
            password = body.get("sudo_password")
            if not name or enable is None:
                self._send_json({"ok": False, "error": "Name and enable status are required"}, 400)
                return
            ok, msg = toggle_php_extension(name, enable, password)
            if ok:
                self._send_json({"ok": True, "message": msg})
            else:
                self._send_json({"ok": False, "error": msg}, 500)
            return

        elif path == "/api/php/extension/install":
            body = self._read_body()
            name = body.get("name")
            password = body.get("sudo_password")
            if not name:
                self._send_json({"ok": False, "error": "Extension name is required"}, 400)
                return
            ok, msg = install_php_extension(name, password)
            if ok:
                toggle_php_extension(name, True, password)
                self._send_json({"ok": True, "message": msg})
            else:
                self._send_json({"ok": False, "error": msg}, 500)
            return

        if path == "/api/project/stop":
            body = self._read_body()
            pid = body.get("pid")
            name = body.get("name", "Unknown")
            if not pid:
                self._send_json({"ok": False, "error": "PID is required"}, 400)
                return
            ok = kill_process(pid)
            if ok:
                # Trigger proxy reload to update list immediately
                self._trigger_reload()
                self._send_json({"ok": True, "message": f"Project '{name}' stopped successfully."})
            else:
                self._send_json({"ok": False, "error": f"Failed to stop project '{name}'."})

        elif path == "/api/service/control":
            body = self._read_body()
            service = body.get("service")
            action = body.get("action")
            if service not in ["caddy", "mariadb"]:
                self._send_json({"ok": False, "error": "Unsupported service"}, 400)
                return
            if action not in ["start", "stop", "restart"]:
                self._send_json({"ok": False, "error": "Invalid action"}, 400)
                return

            if service == "caddy":
                ok = self._control_caddy(action)
            else:
                res = dbmod.control_service(action)
                ok = res["ok"]
            
            if ok:
                self._send_json({"ok": True, "message": f"Service '{service}' {action}ed successfully."})
            else:
                self._send_json({"ok": False, "error": f"Failed to {action} service '{service}'"})

        elif path == "/api/service/install":
            body = self._read_body()
            component = body.get("component")
            
            # Map dashboard IDs to installer components
            mapping = {
                "pma": "phpmyadmin",
                "php": "php_fpm",
                "mariadb": "mariadb",
                "caddy": "caddy"
            }
            mapped_comp = mapping.get(component, component)
            
            funcs = {
                "caddy": instmod.install_caddy,
                "mariadb": instmod.install_mariadb,
                "php_fpm": instmod.install_php_fpm,
                "phpmyadmin": instmod.install_phpmyadmin
            }
            if mapped_comp not in funcs:
                self._send_json({"ok": False, "error": f"Invalid component: {component}"}, 400)
                return
            
            ok = funcs[mapped_comp]()

            if ok:
                # Install trust if caddy installed
                if component == "caddy":
                    certsmod.trust_system_and_browsers()
                self._trigger_reload()
                self._send_json({"ok": True, "message": f"Component '{component}' installed successfully."})
            else:
                self._send_json({"ok": False, "error": f"Failed to install component '{component}'."})

        elif path == "/api/db/create":
            body = self._read_body()
            name = body.get("name")
            if not name:
                self._send_json({"ok": False, "error": "Database name is required"}, 400)
                return
            ok = dbmod.create_database(name)
            if ok:
                self._send_json({"ok": True, "message": f"Database '{name}' created successfully."})
            else:
                self._send_json({"ok": False, "error": f"Failed to create database '{name}'."})

        elif path == "/api/db/drop":
            body = self._read_body()
            name = body.get("name")
            if not name:
                self._send_json({"ok": False, "error": "Database name is required"}, 400)
                return
            ok = dbmod.drop_database(name)
            if ok:
                self._send_json({"ok": True, "message": f"Database '{name}' dropped successfully."})
            else:
                self._send_json({"ok": False, "error": f"Failed to drop database '{name}'."})

        elif path == "/api/db/user/create":
            body = self._read_body()
            username = body.get("username")
            password = body.get("password")
            host = body.get("host", "localhost")
            if not username or not password:
                self._send_json({"ok": False, "error": "Username and password are required"}, 400)
                return
            ok = dbmod.create_user(username, password, host)
            if ok:
                self._send_json({"ok": True, "message": f"Database user '{username}' created successfully."})
            else:
                self._send_json({"ok": False, "error": f"Failed to create database user."})

        elif path == "/api/db/user/drop":
            body = self._read_body()
            username = body.get("username")
            host = body.get("host", "localhost")
            if not username:
                self._send_json({"ok": False, "error": "Username is required"}, 400)
                return
            ok = dbmod.drop_user(username, host)
            if ok:
                self._send_json({"ok": True, "message": f"Database user '{username}' dropped successfully."})
            else:
                self._send_json({"ok": False, "error": f"Failed to drop database user."})

        elif path == "/api/projects/root/update":
            body = self._read_body()
            root_path = body.get("projects_root", "").strip()
            cfg["projects_root"] = root_path
            cfgmod.save_config(cfg)
            self._send_json({"ok": True, "message": "Projects root directory updated successfully"})
            return

        elif path == "/api/project/spawn":
            body = self._read_body()
            name = body.get("name", "Unknown")
            proj_path = body.get("path")
            command = body.get("command")
            if not proj_path or not command:
                self._send_json({"ok": False, "error": "Path and command are required"}, 400)
                return
            
            try:
                import re
                safe_name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
                log_dir = Path(cfg["log_dir"])
                log_dir.mkdir(parents=True, exist_ok=True)
                log_file_path = log_dir / f"project-{safe_name}.log"
                log_file = open(log_file_path, "w", buffering=1)
                
                env = os.environ.copy()
                env["HOST"] = "127.0.0.1"
                env["ADDR"] = "127.0.0.1"
                
                proc = subprocess.Popen(
                    command,
                    shell=True,
                    cwd=proj_path,
                    stdout=log_file,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    env=env
                )
                
                SPAWNED_PROJECTS[proc.pid] = {
                    "process": proc,
                    "name": name,
                    "path": proj_path,
                    "command": command,
                    "start_time": time.time(),
                    "log_file": log_file,
                    "log_file_path": str(log_file_path)
                }
                
                self._send_json({"ok": True, "message": f"Project '{name}' launched successfully (PID: {proc.pid})", "pid": proc.pid})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
            return

        elif path == "/api/project/spawned/stop":
            body = self._read_body()
            pid = int(body.get("pid", "0"))
            if not pid:
                self._send_json({"ok": False, "error": "PID is required"}, 400)
                return
            
            if pid in SPAWNED_PROJECTS:
                proc_info = SPAWNED_PROJECTS[pid]
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                    time.sleep(0.5)
                    if proc_info["process"].poll() is None:
                        os.killpg(os.getpgid(pid), signal.SIGKILL)
                    
                    try:
                        proc_info["log_file"].close()
                    except Exception:
                        pass
                        
                    del SPAWNED_PROJECTS[pid]
                    self._send_json({"ok": True, "message": f"Project stopped successfully"})
                except Exception as e:
                    kill_process(pid)
                    try:
                        proc_info["log_file"].close()
                    except Exception:
                        pass
                    if pid in SPAWNED_PROJECTS:
                        del SPAWNED_PROJECTS[pid]
                    self._send_json({"ok": True, "message": f"Stopped via fallback signal: {e}"})
            else:
                ok = kill_process(pid)
                self._send_json({"ok": ok, "message": "Process terminate signal sent"})
            return

        elif path == "/api/project/stresstest":
            body = self._read_body()
            url = body.get("url")
            if not url:
                self._send_json({"ok": False, "error": "URL is required"}, 400)
                return
            
            try:
                results = run_stress_test(url)
                self._send_json({"ok": True, "results": results})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
            return

        elif path == "/api/project/custom/add":
            body = self._read_body()
            proj_path = body.get("path", "").strip()
            if not proj_path:
                self._send_json({"ok": False, "error": "Project path is required"}, 400)
                return
            
            details = detect_single_project(proj_path)
            if not details:
                self._send_json({"ok": False, "error": "Invalid directory or directory does not exist"}, 400)
                return
            
            custom_list = cfg.get("custom_projects", [])
            custom_list = [p for p in custom_list if p["path"] != proj_path]
            custom_list.append(details)
            cfg["custom_projects"] = custom_list
            cfgmod.save_config(cfg)
            
            self._send_json({"ok": True, "message": f"Project '{details['name']}' added successfully", "project": details})
            return

        elif path == "/api/project/custom/remove":
            body = self._read_body()
            proj_path = body.get("path", "").strip()
            if not proj_path:
                self._send_json({"ok": False, "error": "Project path is required"}, 400)
                return
            
            custom_list = cfg.get("custom_projects", [])
            custom_list = [p for p in custom_list if p["path"] != proj_path]
            cfg["custom_projects"] = custom_list
            cfgmod.save_config(cfg)
            
        elif path == "/api/project/rename":
            body = self._read_body()
            proj_path = body.get("path", "").strip()
            new_name = body.get("new_name", "").strip()
            if not proj_path or not new_name:
                self._send_json({"ok": False, "error": "Path and new name are required"}, 400)
                return
            
            renames = cfg.get("renamed_projects", {})
            renames[proj_path] = new_name
            cfg["renamed_projects"] = renames
            cfgmod.save_config(cfg)
            
            self._send_json({"ok": True, "message": "Project renamed successfully"})
            return

        else:
            self._send_json({"ok": False, "error": "not found"}, 404)

    def _caddy_running(self):
        pid_file = Path(cfgmod.get_config_dir()) / "caddy.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip())
                p = psutil.Process(pid)
                if "caddy" in p.name() and p.is_running():
                    return True
            except Exception:
                pass
        return False

    def _php_fpm_running(self):
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status == "LISTEN" and conn.laddr.port == 9000:
                return True
        return False

    def _control_caddy(self, action):
        cfg = cfgmod.load_config()
        pid_file = Path(cfgmod.get_config_dir()) / "caddy.pid"
        
        if action == "stop":
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    p = psutil.Process(pid)
                    p.terminate()
                    p.wait(timeout=3)
                except Exception:
                    pass
                pid_file.unlink(missing_ok=True)
            return True
        elif action == "start":
            # Run Caddy using cli loader
            self._trigger_reload()
            return True
        elif action == "restart":
            self._control_caddy("stop")
            time.sleep(0.5)
            self._trigger_reload()
            return True
        return False

    def _trigger_reload(self):
        project_dir = Path(__file__).resolve().parent.parent
        subprocess.Popen(
            [sys.executable, "-m", "NeoLocalDev.cli", "reload"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(project_dir),
        )

def _get_php_version():
    php_dir = Path("/etc/php")
    if not php_dir.exists():
        return None
    versions = sorted([d.name for d in php_dir.iterdir() if d.is_dir() and d.name.replace(".", "").isdigit()], reverse=True)
    if versions:
        return versions[0]
    return None

def get_php_extensions():
    version = _get_php_version()
    if not version:
        return []
    
    php_dir = Path("/etc/php")
    mods_dir = php_dir / version / "mods-available"
    fpm_dir = php_dir / version / "fpm" / "conf.d"
    
    common_exts = {
        "bcmath", "curl", "gd", "intl", "mbstring", "xml", "zip", "sqlite3", 
        "mysql", "redis", "imagick", "xdebug", "gmp", "soap", "opcache", 
        "apcu", "memcached", "mongodb", "mailparse", "yaml"
    }
    
    extensions = []
    installed_names = set()
    
    if mods_dir.exists():
        for item in mods_dir.glob("*.ini"):
            name = item.stem
            installed_names.add(name)
            enabled = False
            if fpm_dir.exists():
                for f in fpm_dir.glob(f"*-{name}.ini"):
                    enabled = True
                    break
            if name == "mysqlnd" and not enabled:
                enabled = True
            
            extensions.append({
                "name": name,
                "installed": True,
                "enabled": enabled
            })
            
    for name in common_exts:
        if name not in installed_names:
            extensions.append({
                "name": name,
                "installed": False,
                "enabled": False
            })
            
    return sorted(extensions, key=lambda x: x["name"])

def run_sudo_command(cmd_list, password=None):
    # Try passwordless first
    try:
        proc = subprocess.run(["sudo", "-n"] + cmd_list, capture_output=True, text=True)
        if proc.returncode == 0:
            return True, proc.stdout
    except Exception:
        pass
    
    if password:
        try:
            proc = subprocess.run(["sudo", "-S"] + cmd_list, input=password + "\n", capture_output=True, text=True)
            if proc.returncode == 0:
                return True, proc.stdout
            else:
                err = proc.stderr or "Incorrect password"
                return False, err
        except Exception as e:
            return False, str(e)
            
    return False, "sudo_required"

def toggle_php_extension(name, enable, password=None):
    version = _get_php_version()
    if not version:
        return False, "PHP is not installed"
    
    cmd = "phpenmod" if enable else "phpdismod"
    ok, err = run_sudo_command([cmd, name], password)
    if not ok:
        return False, err
        
    ok, err = run_sudo_command(["systemctl", "restart", f"php{version}-fpm"], password)
    if not ok:
        return False, err
        
    return True, f"Extension '{name}' {'enabled' if enable else 'disabled'} successfully. PHP-FPM restarted."

def install_php_extension(name, password=None):
    version = _get_php_version()
    if not version:
        return False, "PHP is not installed"
    
    ok, err = run_sudo_command(["apt-get", "install", "-y", f"php{version}-{name}"], password)
    if ok:
        return True, f"Extension '{name}' installed successfully."
        
    if err == "sudo_required":
        return False, "sudo_required"
        
    ok, err2 = run_sudo_command(["apt-get", "install", "-y", f"php-{name}"], password)
    if ok:
        return True, f"Extension '{name}' installed successfully."
        
    return False, err2

# Web Dashboard GUI design with sleek glassmorphism and tailored dark aesthetic
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Neo LocalDev helper</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" type="image/png" href="/admin/neo.png">
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');
:root {
  --bg: #070814;
  --surface: rgba(15, 17, 35, 0.7);
  --surface-hover: rgba(24, 27, 54, 0.85);
  --border: rgba(99, 102, 241, 0.2);
  --border-light: rgba(255, 255, 255, 0.04);
  --text: #f1f5f9;
  --muted: #64748b;
  --accent: #6366f1;
  --accent-glow: rgba(99, 102, 241, 0.3);
  --accent2: #06b6d4;
  --green: #10b981;
  --green-glow: rgba(16, 185, 129, 0.2);
  --red: #f43f5e;
  --red-glow: rgba(244, 63, 94, 0.2);
  --radius: 16px;
  --radius-sm: 10px;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 9px; }
::-webkit-scrollbar-thumb:hover { background: var(--muted); }
body {
  font-family: 'Outfit', sans-serif;
  background: var(--bg);
  background-image: radial-gradient(circle at 10% 20%, rgba(99, 102, 241, 0.06) 0%, transparent 40%),
                    radial-gradient(circle at 90% 80%, rgba(6, 182, 212, 0.05) 0%, transparent 45%);
  color: var(--text);
  min-height: 100vh;
  padding-bottom: 2rem;
}
.container { max-width: 1200px; margin: 0 auto; padding: 2rem; }

/* Login Overlay */
.login-overlay {
  position: fixed; inset: 0; z-index: 1000;
  background: rgba(7, 8, 20, 0.85); backdrop-filter: blur(20px);
  display: flex; align-items: center; justify-content: center;
  transition: opacity 0.3s ease;
}
.login-card {
  width: 100%; max-width: 400px; padding: 2.5rem;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: 0 20px 50px rgba(0,0,0,0.6);
  text-align: center;
}
.login-card h2 {
  font-size: 1.75rem; font-weight: 800; margin-bottom: 0.5rem;
  background: linear-gradient(135deg, #a5b4fc, #06b6d4);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.login-card p { color: var(--muted); font-size: 0.875rem; margin-bottom: 2rem; }
.form-group { margin-bottom: 1.25rem; text-align: left; }
.form-group label { display: block; font-size: 0.85rem; color: var(--muted); margin-bottom: 0.5rem; }
.input-control {
  width: 100%; padding: 0.75rem 1rem;
  background: #0f1123; border: 1px solid var(--border);
  border-radius: var(--radius-sm); color: var(--text); font-family: inherit;
  transition: border-color 0.2s;
}
.input-control:focus { outline: none; border-color: var(--accent); }
select.input-control option {
  background: #0f1123;
  color: var(--text);
}

/* Resource Gauges */
.metrics-row {
  display: flex; gap: 2rem; justify-content: center; margin-bottom: 2.5rem;
  flex-wrap: wrap;
}
.metric-circle-card {
  background: var(--surface); border: 1px solid var(--border-light);
  padding: 1.5rem; border-radius: var(--radius); text-align: center;
  width: 160px; display: flex; flex-direction: column; align-items: center; gap: 0.75rem;
  box-shadow: 0 4px 20px rgba(0,0,0,0.15);
}
.circle-chart {
  position: relative; width: 100px; height: 100px;
}
.circle-chart svg {
  transform: rotate(-90deg);
}
.circle-chart-bg {
  fill: none; stroke: rgba(255,255,255,0.02); stroke-width: 3.8;
}
.circle-chart-circle {
  fill: none; stroke-width: 3.8;
  stroke-linecap: round; transition: stroke-dasharray 0.3s ease;
}
.circle-chart-text {
  position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
  font-family: 'JetBrains Mono', monospace; font-size: 1.1rem; font-weight: 700; color: var(--text);
}
.metric-label { font-size: 0.85rem; color: var(--muted); font-weight: 600; }

/* Top Header */
.header-bar {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 2rem; padding: 1.25rem 2rem;
  background: var(--surface); backdrop-filter: blur(20px);
  border-radius: var(--radius); border: 1px solid var(--border);
  box-shadow: 0 10px 40px rgba(0,0,0,0.4);
}
.header-bar h1 {
  font-size: 1.5rem; font-weight: 800;
  background: linear-gradient(135deg, #a5b4fc, #06b6d4);
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
}
.header-bar span.subtitle {
  font-weight: 400; font-size: 0.85rem; color: var(--muted); margin-left: 0.5rem;
}

/* Tabs System */
.tabs-nav {
  display: flex; gap: 0.5rem; border-bottom: 1px solid var(--border-light);
  margin-bottom: 2rem; padding-bottom: 0.5rem;
  flex-wrap: wrap;
}
.tab-btn {
  padding: 0.75rem 1.25rem; background: transparent; border: none;
  color: var(--muted); font-family: inherit; font-size: 0.95rem; font-weight: 600;
  cursor: pointer; border-radius: var(--radius-sm); transition: all 0.2s;
  display: flex; align-items: center; gap: 0.5rem;
}
.tab-btn:hover { background: rgba(255,255,255,0.03); color: var(--text); }
.tab-btn.active {
  background: rgba(99, 102, 241, 0.1); color: #a5b4fc; border: 1px solid var(--border);
}
.tab-pane { display: none; }
.tab-pane.active { display: block; animation: fadeIn 0.3s ease; }

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(5px); }
  to { opacity: 1; transform: translateY(0); }
}

/* Panels */
.panel {
  background: var(--surface); backdrop-filter: blur(16px);
  border-radius: var(--radius); border: 1px solid var(--border-light);
  padding: 1.75rem; margin-bottom: 2rem;
  box-shadow: 0 4px 30px rgba(0,0,0,0.2);
}
.panel-title {
  font-size: 1.1rem; font-weight: 600; margin-bottom: 1.25rem;
  display: flex; align-items: center; gap: 0.5rem;
}

/* Table Style */
table.dev-table {
  width: 100%; border-collapse: collapse; text-align: left;
}
table.dev-table th, table.dev-table td {
  padding: 0.85rem 1rem; border-bottom: 1px solid var(--border-light);
}
table.dev-table th {
  font-weight: 600; color: var(--muted); font-size: 0.85rem; text-transform: uppercase;
}
table.dev-table td { font-size: 0.9rem; }

/* Checklist Style */
.comp-list { display: flex; flex-direction: column; gap: 1rem; }
.comp-item {
  display: flex; justify-content: space-between; align-items: center;
  padding: 0.85rem 1.2rem; background: rgba(255,255,255,0.02);
  border-radius: var(--radius-sm); border: 1px solid var(--border-light);
}
.comp-info { display: flex; align-items: center; gap: 0.75rem; }
.comp-name { font-weight: 600; font-size: 0.95rem; }
.comp-version { font-size: 0.75rem; color: var(--muted); }
.status-badge {
  padding: 0.25rem 0.75rem; border-radius: 99px; font-size: 0.75rem; font-weight: 600;
  display: inline-flex; align-items: center; gap: 0.35rem;
}
.status-badge-ok { background: rgba(16, 185, 129, 0.1); color: var(--green); }
.status-badge-err { background: rgba(239, 68, 68, 0.1); color: var(--red); }
.status-badge::before { content: ''; width: 6px; height: 6px; border-radius: 50%; }
.status-badge-ok::before { background: var(--green); }
.status-badge-err::before { background: var(--red); }

/* Buttons */
.btn {
  padding: 0.5rem 1rem; border-radius: var(--radius-sm); font-family: inherit;
  font-size: 0.85rem; font-weight: 600; cursor: pointer; border: 1px solid transparent;
  transition: all 0.2s; display: inline-flex; align-items: center; justify-content: center; gap: 0.35rem;
}
.btn-primary { background: var(--accent); color: white; box-shadow: 0 4px 14px var(--accent-glow); }
.btn-primary:hover { background: #4f46e5; }
.btn-outline { background: transparent; border-color: var(--border); color: var(--text); }
.btn-outline:hover { background: rgba(255,255,255,0.05); }
.btn-danger { background: rgba(244, 63, 94, 0.1); border-color: rgba(244, 63, 94, 0.2); color: var(--red); }
.btn-danger:hover { background: var(--red); color: white; }
.btn-sm { padding: 0.35rem 0.75rem; font-size: 0.75rem; }

/* Database Grid */
.db-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 2rem; }
@media (max-width: 768px) { .db-grid { grid-template-columns: 1fr; } }
.db-list, .user-list { display: flex; flex-direction: column; gap: 0.5rem; max-height: 300px; overflow-y: auto; padding-right: 0.5rem; }

/* Log Console */
.console {
  background: #03040b; border: 1px solid var(--border);
  border-radius: var(--radius-sm); padding: 1.25rem; font-family: 'JetBrains Mono', monospace;
  font-size: 0.8rem; line-height: 1.5; color: #a5b4fc;
  overflow-y: auto; max-height: 500px; white-space: pre-wrap;
}

/* Toast Notification */
.toast-container {
  position: fixed; bottom: 2rem; right: 2rem; z-index: 1100;
  display: flex; flex-direction: column; gap: 0.75rem;
}
.toast {
  padding: 1rem 1.5rem; border-radius: var(--radius-sm); background: var(--surface);
  border: 1px solid var(--border); color: var(--text); font-size: 0.9rem; font-weight: 500;
  box-shadow: 0 10px 30px rgba(0,0,0,0.5); display: flex; align-items: center; gap: 0.5rem;
  animation: slideIn 0.3s ease forwards;
}
.toast-error { border-color: var(--red); color: #fda4af; }
@keyframes slideIn {
  from { transform: translateX(100%); opacity: 0; }
  to { transform: translateX(0); opacity: 1; }
}

.logout-btn { background: transparent; border: none; color: var(--muted); cursor: pointer; font-size: 0.85rem; display: flex; align-items: center; gap: 0.35rem; }
.logout-btn:hover { color: var(--red); }

/* PHP Extension Card & iOS Switch styling */
.ext-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid rgba(255, 255, 255, 0.04);
  padding: 0.85rem 1.25rem;
  border-radius: 12px;
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}
.ext-card:hover {
  transform: translateY(-2px);
  border-color: rgba(99, 102, 241, 0.35);
  background: rgba(99, 102, 241, 0.05);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
}

.switch {
  position: relative;
  display: inline-block;
  width: 44px;
  height: 24px;
}
.switch input {
  opacity: 0;
  width: 0;
  height: 0;
}
.slider {
  position: absolute;
  cursor: pointer;
  top: 0; left: 0; right: 0; bottom: 0;
  background-color: #1e293b;
  transition: .3s cubic-bezier(0.4, 0, 0.2, 1);
  border-radius: 24px;
  border: 1px solid rgba(255, 255, 255, 0.05);
}
.slider:before {
  position: absolute;
  content: "";
  height: 18px;
  width: 18px;
  left: 2px;
  bottom: 2px;
  background-color: #94a3b8;
  transition: .3s cubic-bezier(0.4, 0, 0.2, 1);
  border-radius: 50%;
  box-shadow: 0 2px 4px rgba(0,0,0,0.2);
}
input:checked + .slider {
  background-color: var(--accent);
  border-color: rgba(99, 102, 241, 0.3);
}
input:checked + .slider:before {
  transform: translateX(20px);
  background-color: #fff;
}

/* Custom Modal */
.modal-overlay {
  position: fixed; inset: 0; z-index: 1200; background: rgba(0,0,0,0.7);
  display: none; align-items: center; justify-content: center; backdrop-filter: blur(10px);
}
.modal-card {
  width: 90%; max-width: 500px; padding: 2rem; background: var(--surface);
  border: 1px solid var(--border); border-radius: var(--radius);
  box-shadow: 0 20px 60px rgba(0,0,0,0.7);
}
</style>
</head>
<body>

<!-- Login Page -->
<div class="login-overlay" id="loginOverlay">
  <div class="login-card" style="text-align:center; display:flex; flex-direction:column; align-items:center">
    <img src="/admin/neo.png" alt="Neo Logo" style="height:70px; width:auto; border-radius:12px; margin-bottom:1.25rem; box-shadow: 0 4px 15px rgba(0,0,0,0.3)">
    <h2>Neo LocalDev helper</h2>
    <p>Sign in to access your local gateway control center</p>
    <form id="loginForm" onsubmit="handleLogin(event)" style="width:100%">
      <div class="form-group">
        <label for="username">Username</label>
        <input type="text" id="username" class="input-control" value="admin" required autocomplete="username">
      </div>
      <div class="form-group">
        <label for="password">Password</label>
        <input type="password" id="password" class="input-control" required autocomplete="current-password" autofocus>
      </div>
      <button type="submit" class="btn btn-primary" style="width:100%; padding:0.85rem">Sign In</button>
    </form>
  </div>
</div>

<!-- Main App Container -->
<div class="container" id="appContainer" style="display:none">
  <div class="header-bar">
    <div style="display:flex; align-items:center; gap:1rem">
      <img src="/admin/neo.png" alt="Neo Logo" style="height:44px; width:auto; border-radius:8px; box-shadow: 0 2px 8px rgba(0,0,0,0.3)">
      <h1>Neo LocalDev helper <span class="subtitle">• Control Center</span></h1>
    </div>
    <div style="display:flex; align-items:center; gap:1.5rem">
      <button class="btn btn-outline btn-sm" onclick="loadStatus()">🔄 Refresh</button>
      <button class="logout-btn" onclick="handleLogout()">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"></path><polyline points="16 17 21 12 16 7"></polyline><line x1="21" y1="12" x2="9" y2="12"></line></svg>
        Logout
      </button>
    </div>
  </div>

  <!-- Resource Metrics Row -->
  <div class="metrics-row">
    <!-- CPU -->
    <div class="metric-circle-card">
      <div class="circle-chart">
        <svg viewBox="0 0 36 36" width="100" height="100">
          <path class="circle-chart-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
          <path id="cpuCircle" class="circle-chart-circle" stroke-dasharray="0, 100" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" style="stroke: var(--accent);" />
        </svg>
        <div class="circle-chart-text" id="cpuText">0%</div>
      </div>
      <div class="metric-label">CPU Usage</div>
    </div>

    <!-- RAM -->
    <div class="metric-circle-card">
      <div class="circle-chart">
        <svg viewBox="0 0 36 36" width="100" height="100">
          <path class="circle-chart-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
          <path id="ramCircle" class="circle-chart-circle" stroke-dasharray="0, 100" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" style="stroke: var(--accent2);" />
        </svg>
        <div class="circle-chart-text" id="ramText">0%</div>
      </div>
      <div class="metric-label">RAM Usage</div>
    </div>

    <!-- Disk -->
    <div class="metric-circle-card">
      <div class="circle-chart">
        <svg viewBox="0 0 36 36" width="100" height="100">
          <path class="circle-chart-bg" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" />
          <path id="diskCircle" class="circle-chart-circle" stroke-dasharray="0, 100" d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831" style="stroke: var(--green);" />
        </svg>
        <div class="circle-chart-text" id="diskText">0%</div>
      </div>
      <div class="metric-label">Disk Storage</div>
    </div>
  </div>

  <!-- Tabs Navigation -->
  <div class="tabs-nav">
    <button class="tab-btn active" onclick="switchTab('servers')">🌐 Active Proxies</button>
    <button class="tab-btn" onclick="switchTab('launcher')">🚀 Project Launcher</button>
    <button class="tab-btn" onclick="switchTab('database')">🗄️ Database Manager</button>
    <button class="tab-btn" onclick="switchTab('services')">⚡ Service Controls</button>
    <button class="tab-btn" onclick="switchTab('logs')">📋 Logs & Settings</button>
  </div>

  <!-- Tab 1: Active Proxies -->
  <div class="tab-pane active" id="tab-servers">
    <div class="panel">
      <div class="panel-title">📡 Discovered Development Servers</div>
      <div style="overflow-x:auto">
        <table class="dev-table">
          <thead>
            <tr>
              <th>Project</th>
              <th>Framework</th>
              <th>Runtime</th>
              <th>Internal Port</th>
              <th>Secure HTTPS URL</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody id="projectsTableBody">
            <!-- Filled dynamically -->
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Tab 2: Project Launcher (Spawner) -->
  <div class="tab-pane" id="tab-launcher">
    <div style="display:grid; grid-template-columns: 1fr 1fr; gap:2rem; margin-bottom:2rem">
      <!-- Auto-Discover Root -->
      <div class="panel" style="margin-bottom:0">
        <div class="panel-title">📁 Auto-Discover Root Directory</div>
        <form onsubmit="saveProjectsRoot(event)" style="display:flex; gap:0.5rem; align-items:center">
          <input type="text" id="projectsRootInput" class="input-control" placeholder="e.g. /home/royal/projects" style="flex:1">
          <button type="button" class="btn btn-outline" onclick="openExplorer('projectsRootInput')" style="padding:0.75rem">📁 Browse</button>
          <button type="submit" class="btn btn-primary">Scan & Save</button>
        </form>
      </div>

      <!-- Add Single Project -->
      <div class="panel" style="margin-bottom:0">
        <div class="panel-title">➕ Add Individual Project Folder</div>
        <form onsubmit="addCustomProject(event)" style="display:flex; gap:0.5rem; align-items:center">
          <input type="text" id="singleProjectPathInput" class="input-control" placeholder="e.g. /home/royal/projects/my-node-app" style="flex:1">
          <button type="button" class="btn btn-outline" onclick="openExplorer('singleProjectPathInput')" style="padding:0.75rem">📁 Browse</button>
          <button type="submit" class="btn btn-primary">Add Project</button>
        </form>
      </div>
    </div>

    <div style="display:grid; grid-template-columns:1.2fr 1fr; gap:2rem" id="launcherGrid">
      <!-- Discovered Folders -->
      <div class="panel">
        <div class="panel-title">🔎 Available Projects</div>
        <div style="max-height:500px; overflow-y:auto; display:flex; flex-direction:column; gap:1rem" id="discoveredProjectsList">
          <div style="color:var(--muted); text-align:center; padding:2rem">Set root folder or add an individual folder above!</div>
        </div>
      </div>

      <!-- Spawned Running Projects -->
      <div class="panel">
        <div class="panel-title">🟢 Launched Projects (Background)</div>
        <div style="max-height:500px; overflow-y:auto; display:flex; flex-direction:column; gap:1rem" id="spawnedProjectsList">
          <div style="color:var(--muted); text-align:center; padding:2rem">No launched projects running in the background.</div>
        </div>
      </div>
    </div>
  </div>

  <!-- Tab 3: Database Manager -->
  <div class="tab-pane" id="tab-database">
    <div class="db-grid">
      <div class="panel">
        <div class="panel-title">📁 Databases</div>
        <div style="display:flex; gap:0.5rem; margin-bottom:1rem">
          <input type="text" id="newDbName" class="input-control" placeholder="New DB Name..." style="padding:0.4rem 0.8rem; font-size:0.85rem">
          <button class="btn btn-primary btn-sm" onclick="createDb()">Create</button>
        </div>
        <div class="db-list" id="dbList">
          <!-- Filled dynamically -->
        </div>
      </div>

      <div class="panel">
        <div class="panel-title">👥 Database Users</div>
        <div class="user-list" id="userList">
          <!-- Filled dynamically -->
        </div>
      </div>
    </div>
  </div>

  <!-- Tab 4: Service Controls -->
  <div class="tab-pane" id="tab-services">
    <div class="panel" style="max-width:600px; margin:0 auto">
      <div class="panel-title">🛠️ Services Checklist</div>
      <div class="comp-list">
        <!-- Caddy -->
        <div class="comp-item">
          <div class="comp-info">
            <div class="comp-name">Caddy Proxy</div>
            <div class="comp-version" id="caddyVersion">-</div>
          </div>
          <div style="display:flex; align-items:center; gap:1rem">
            <span class="status-badge status-badge-err" id="caddyBadge">Stopped</span>
            <div id="caddyControls"></div>
          </div>
        </div>

        <!-- MariaDB -->
        <div class="comp-item">
          <div class="comp-info">
            <div class="comp-name">MariaDB Server</div>
            <div class="comp-version" id="mariadbVersion">-</div>
          </div>
          <div style="display:flex; align-items:center; gap:1rem">
            <span class="status-badge status-badge-err" id="mariadbBadge">Stopped</span>
            <div id="mariadbControls"></div>
          </div>
        </div>

        <!-- PHP-FPM -->
        <div class="comp-item">
          <div class="comp-info">
            <div class="comp-name">PHP-FPM Manager</div>
            <div class="comp-version" id="phpVersion">-</div>
          </div>
          <div style="display:flex; align-items:center; gap:1rem">
            <span class="status-badge status-badge-err" id="phpBadge">Stopped</span>
            <div id="phpControls"></div>
          </div>
        </div>

        <!-- phpMyAdmin -->
        <div class="comp-item">
          <div class="comp-info">
            <div class="comp-name">phpMyAdmin Client</div>
            <div class="comp-version" id="pmaVersion">-</div>
          </div>
          <div style="display:flex; align-items:center; gap:1rem">
            <span class="status-badge status-badge-err" id="pmaBadge">Stopped</span>
            <div id="pmaControls"></div>
          </div>
      </div>
    </div>

    <!-- PHP Extensions Manager Panel -->
    <div class="panel" style="max-width:800px; margin:2rem auto 0 auto">
      <div class="panel-title" style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:1rem">
        <span>🐘 PHP Extensions Manager</span>
        <input type="text" id="phpExtSearch" class="input-control" placeholder="🔍 Search extensions..." oninput="filterPhpExtensions()" style="max-width:250px; padding:0.4rem 0.75rem; font-size:0.85rem; margin:0">
      </div>
      <div id="phpExtensionsContainer" style="display:grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap:1rem; margin-top:1.5rem; max-height:400px; overflow-y:auto; padding-right:0.5rem">
        <div style="grid-column:1/-1; text-align:center; color:var(--muted); padding:2rem">Loading extensions list...</div>
      </div>
    </div>
  </div>

  <!-- Tab 5: Logs & Settings -->
  <div class="tab-pane" id="tab-logs">
    <div style="display:grid; grid-template-columns: 1fr 1fr; gap:2rem">
      <div class="panel">
        <div class="panel-title">🔒 Update Gateway Credentials</div>
        <form onsubmit="updateSecurity(event)" style="display:flex; flex-direction:column; gap:1.25rem">
          <div class="form-group">
            <label for="newUsername">New Username</label>
            <input type="text" id="newUsername" class="input-control" required autocomplete="username" placeholder="e.g. admin">
          </div>
          <div class="form-group">
            <label for="newPassword">New Password</label>
            <input type="password" id="newPassword" class="input-control" required autocomplete="new-password" placeholder="••••••••">
          </div>
          <button type="submit" class="btn btn-primary" style="padding:0.75rem">Save Credentials</button>
        </form>
      </div>

      <div class="panel">
        <div class="panel-title">🔗 LAN and Routing Info</div>
        <div style="display:flex; flex-direction:column; gap:1rem; font-size:0.9rem">
          <div>Domain: <code id="netDomain">-</code></div>
          <div>LAN Address: <code id="netLan">-</code></div>
          <div>Dashboard URL: <code id="netDash">-</code></div>
        </div>
      </div>

      <div class="panel">
        <div class="panel-title">🛡️ Neo The Guardian LocalDev</div>
        <div style="display:flex; flex-direction:column; gap:1rem; font-size:0.9rem">
          <div>Approved Device IPs: <ul id="guardianApprovedList" style="margin: 0.5rem 0 0 0; padding-left: 1.2rem; color: var(--text-muted)"></ul></div>
          <div style="margin-top: 0.5rem; font-weight:600">Pending Access Requests:</div>
          <div id="guardianPendingList" style="display:flex; flex-direction:column; gap:0.5rem">
            <span style="color: var(--text-muted); font-style: italic">No pending requests</span>
          </div>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-title" style="display:flex; justify-content:space-between; align-items:center">
        <span>📋 System Logs</span>
        <div style="display:flex; gap:0.5rem">
          <button class="btn btn-outline btn-sm" onclick="loadLogs('access')">Caddy Access</button>
          <button class="btn btn-outline btn-sm" onclick="loadLogs('error')">Caddy Error</button>
          <button class="btn btn-outline btn-sm" onclick="loadLogs('api')">Helper API</button>
        </div>
      </div>
      <div class="console" id="logConsole">Click a log type above to view...</div>
    </div>
  </div>

  <!-- Footer section -->
  <div style="margin-top: 3rem; padding: 2rem 0; border-top: 1px solid var(--border-light); display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 1.5rem; font-size: 0.85rem; color: var(--muted)">
    <div>
      Developed by <a href="https://sdhost.org" target="_blank" style="color: var(--accent); text-decoration: none; font-weight: 500">sdhost.org</a> - &copy; 2026 Royal Service & Tec Solutions. All rights reserved.<br>
      <span style="font-size:0.8rem">Protected by <a href="https://neo.sdhost.org/" target="_blank" style="color: var(--red); text-decoration: none; font-weight: 500">🛡️ NEO THE GUARDIAN</a></span>
    </div>
    <div style="display: flex; gap: 1rem; align-items: center; flex-wrap: wrap">
      <span style="font-weight: 600; color: #fff">ادعمنا / Support Us:</span>
      <a href="https://paypal.me/IbrahimHagIbrahim" target="_blank" style="color: #fff; text-decoration: none; background: rgba(99,102,241,0.08); padding: 0.4rem 0.8rem; border-radius: 6px; border: 1px solid var(--border); font-size: 0.8rem; font-weight: 500">☕ PayPal</a>
      <a href="https://ko-fi.com/sdhost" target="_blank" style="color: #fff; text-decoration: none; background: rgba(244,63,94,0.08); padding: 0.4rem 0.8rem; border-radius: 6px; border: 1px solid rgba(244,63,94,0.25); font-size: 0.8rem; font-weight: 500">🎗️ Ko-fi</a>
    </div>
  </div>
</div>

<!-- Stress Test Result Modal -->
<div class="modal-overlay" id="stressModal">
  <div class="modal-card">
    <h3 style="font-size:1.25rem; font-weight:700; margin-bottom:1rem; background:linear-gradient(135deg, #a5b4fc, #06b6d4); -webkit-background-clip:text; -webkit-text-fill-color:transparent">🚀 Stress Test Results</h3>
    <div style="display:flex; flex-direction:column; gap:0.75rem; margin-bottom:1.5rem" id="stressResultsBody">
      <!-- Loader or results -->
    </div>
    <div style="text-align:right">
      <button class="btn btn-outline" onclick="closeStressModal()">Close</button>
    </div>
  </div>
</div>

<!-- File Explorer Modal -->
<div class="modal-overlay" id="explorerModal">
  <div class="modal-card" style="max-width: 600px; display:flex; flex-direction:column; max-height:80vh">
    <h3 style="font-size:1.25rem; font-weight:700; margin-bottom:1rem; background:linear-gradient(135deg, #a5b4fc, #06b6d4); -webkit-background-clip:text; -webkit-text-fill-color:transparent">📁 Select Directory</h3>
    
    <!-- Path Breadcrumbs -->
    <div style="font-size:0.85rem; background:rgba(0,0,0,0.2); padding:0.6rem 0.8rem; border-radius:6px; margin-bottom:1rem; display:flex; align-items:center; gap:0.5rem; overflow-x:auto; white-space:nowrap">
      <span style="color:var(--muted)">Current:</span>
      <code id="explorerPath" style="color:var(--accent2)">/home/royal</code>
    </div>

    <!-- Directories list -->
    <div style="flex:1; overflow-y:auto; display:flex; flex-direction:column; gap:0.35rem; margin-bottom:1.5rem; min-height:250px; background:rgba(0,0,0,0.1); border-radius:6px; padding:0.5rem" id="explorerDirs">
      <!-- Filled dynamically -->
    </div>

    <div style="display:flex; justify-content:space-between; align-items:center">
      <button class="btn btn-outline" id="explorerUpBtn" onclick="explorerGoUp()">⬆️ Go Up</button>
      <div style="display:flex; gap:0.5rem">
        <button class="btn btn-outline" onclick="closeExplorerModal()">Cancel</button>
        <button class="btn btn-primary" onclick="explorerConfirm()">Select Current Folder</button>
      </div>
    </div>
  </div>
</div>

<div class="toast-container" id="toastContainer"></div>

<script>
const API = window.location.origin + '/admin/api';
let projectsListGlobal = [];

function showToast(msg, isError = false) {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = 'toast' + (isError ? ' toast-error' : '');
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 4000);
}

// Auth Helper Functions
async function apiCall(endpoint, method = 'GET', body = null) {
  const headers = {
    'Content-Type': 'application/json',
  };
  const token = localStorage.getItem('neold_token');
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const options = { method, headers };
  if (body) {
    options.body = JSON.stringify(body);
  }

  try {
    const res = await fetch(API + endpoint, options);
    if (res.status === 401) {
      localStorage.removeItem('neold_token');
      showLoginScreen();
      return { ok: false, error: 'Unauthorized' };
    }
    return await res.json();
  } catch (e) {
    showToast('Failed to connect to gateway API server.', true);
    return { ok: false, error: e.message };
  }
}

function checkAuthentication() {
  const token = localStorage.getItem('neold_token');
  if (token) {
    document.getElementById('loginOverlay').style.display = 'none';
    document.getElementById('appContainer').style.display = 'block';
    loadStatus();
    loadLauncherTab();
  } else {
    showLoginScreen();
  }
}

function showLoginScreen() {
  document.getElementById('loginOverlay').style.display = 'flex';
  document.getElementById('appContainer').style.display = 'none';
}

async function handleLogin(e) {
  e.preventDefault();
  const username = document.getElementById('username').value;
  const password = document.getElementById('password').value;
  
  const r = await fetch(API + '/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password })
  });

  if (r.status === 200) {
    const data = await r.json();
    localStorage.setItem('neold_token', data.token);
    document.getElementById('password').value = '';
    checkAuthentication();
    showToast('Signed in successfully.');
  } else {
    showToast('Invalid username or password.', true);
  }
}

function handleLogout() {
  localStorage.removeItem('neold_token');
  showLoginScreen();
  showToast('Logged out successfully.');
}

// Tab switcher
function switchTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));
  
  // Find button and pane to activate
  const targetBtn = Array.from(document.querySelectorAll('.tab-btn')).find(b => b.textContent.toLowerCase().includes(tabId));
  if (targetBtn) targetBtn.classList.add('active');
  
  const targetPane = document.getElementById('tab-' + tabId);
  if (targetPane) targetPane.classList.add('active');

  // Trigger loads on tab switch
  if (tabId === 'database') {
    loadDatabases();
    loadUsers();
  } else if (tabId === 'logs') {
    loadLogs('api');
  } else if (tabId === 'launcher') {
    loadLauncherTab();
  } else if (tabId === 'services') {
    loadPhpExtensions();
  }
}

let phpExtensionsListGlobal = [];

async function loadPhpExtensions() {
  const container = document.getElementById('phpExtensionsContainer');
  if (!container) return;
  
  const res = await apiCall('/php/extensions');
  if (res.ok && res.extensions) {
    phpExtensionsListGlobal = res.extensions;
    renderPhpExtensions(phpExtensionsListGlobal);
  } else {
    container.innerHTML = `<div style="grid-column:1/-1; text-align:center; color:var(--muted); padding:2rem">Failed to load PHP extensions. Is PHP-FPM installed?</div>`;
  }
}

function renderPhpExtensions(list) {
  const container = document.getElementById('phpExtensionsContainer');
  if (!container) return;

  if (list.length === 0) {
    container.innerHTML = `<div style="grid-column:1/-1; text-align:center; color:var(--muted); padding:2rem">لا توجد إضافات تطابق البحث.</div>`;
    return;
  }

  container.innerHTML = list.map(ext => {
    let actionHtml = '';
    if (!ext.installed) {
      actionHtml = `<button class="btn btn-primary btn-sm" onclick="installPhpExtension('${ext.name}')" style="font-size:0.75rem; padding:0.25rem 0.5rem">📥 تثبيت</button>`;
    } else {
      actionHtml = `
        <label class="switch" style="position:relative; display:inline-block; width:44px; height:24px">
          <input type="checkbox" ${ext.enabled ? 'checked' : ''} onchange="togglePhpExtension('${ext.name}', this.checked)" style="opacity:0; width:0; height:0">
          <span class="slider" style="position:absolute; cursor:pointer; top:0; left:0; right:0; bottom:0; background-color:${ext.enabled ? '#6366f1' : '#1e293b'}; transition:0.3s; border-radius:24px"></span>
        </label>
      `;
    }

    return `
      <div class="ext-card">
        <div>
          <div style="font-weight:600; font-size:0.9rem; color:#f3f4f6">${ext.name}</div>
          <div style="font-size:0.75rem; font-weight:500; color:${ext.installed ? '#10b981' : 'var(--muted)'}">${ext.installed ? 'مثبتة ✓' : 'غير مثبتة'}</div>
        </div>
        <div>
          ${actionHtml}
        </div>
      </div>
    `;
  }).join('');
}

function filterPhpExtensions() {
  const query = document.getElementById('phpExtSearch').value.trim().toLowerCase();
  const filtered = phpExtensionsListGlobal.filter(ext => ext.name.toLowerCase().includes(query));
  renderPhpExtensions(filtered);
}

let cachedSudoPassword = '';

function openSudoModal(onConfirm, onCancel) {
  const modal = document.getElementById('sudoModal');
  const input = document.getElementById('sudoPasswordInput');
  const confirmBtn = document.getElementById('sudoConfirmBtn');
  
  if (!modal || !input || !confirmBtn) return;
  
  input.value = '';
  modal.style.display = 'flex';
  input.focus();
  
  const newConfirmBtn = confirmBtn.cloneNode(true);
  confirmBtn.parentNode.replaceChild(newConfirmBtn, confirmBtn);
  
  newConfirmBtn.onclick = () => {
    const val = input.value.trim();
    modal.style.display = 'none';
    if (val) {
      onConfirm(val);
    } else {
      onCancel();
    }
  };
  
  input.onkeydown = (e) => {
    if (e.key === 'Enter') {
      newConfirmBtn.click();
    }
  };
}

function closeSudoModal() {
  const modal = document.getElementById('sudoModal');
  if (modal) modal.style.display = 'none';
}

async function togglePhpExtension(name, enable) {
  showToast(`${enable ? 'جاري تفعيل' : 'جاري تعطيل'} الإضافة ${name}...`);
  const res = await apiCall('/php/extension/toggle', 'POST', { name, enable, sudo_password: cachedSudoPassword });
  if (res.ok) {
    showToast(res.message);
    loadPhpExtensions();
  } else if (res.error === 'sudo_required') {
    openSudoModal(async (pwd) => {
      cachedSudoPassword = pwd;
      const retryRes = await apiCall('/php/extension/toggle', 'POST', { name, enable, sudo_password: cachedSudoPassword });
      if (retryRes.ok) {
        showToast(retryRes.message);
      } else {
        showToast(retryRes.error || 'فشلت العملية', true);
        cachedSudoPassword = '';
      }
      loadPhpExtensions();
    }, () => {
      alert(`تم إلغاء العملية. للتفعيل يدوياً، قم بتشغيل هذا الأمر في التيرمنال:\n\nsudo phpenmod ${name} && sudo systemctl restart php-fpm\n\nأو قم بالتعديل على ملف الإعدادات في المسار:\n/etc/php/.../mods-available/${name}.ini`);
      loadPhpExtensions();
    });
  } else {
    showToast(res.error || 'فشلت العملية', true);
    loadPhpExtensions();
  }
}

async function installPhpExtension(name) {
  showToast(`جاري تثبيت الإضافة ${name} (قد يستغرق ذلك بعض الوقت)...`);
  const res = await apiCall('/php/extension/install', 'POST', { name, sudo_password: cachedSudoPassword });
  if (res.ok) {
    showToast(res.message);
    loadPhpExtensions();
  } else if (res.error === 'sudo_required') {
    openSudoModal(async (pwd) => {
      cachedSudoPassword = pwd;
      showToast(`جاري إعادة محاولة التثبيت باستخدام كلمة المرور...`);
      const retryRes = await apiCall('/php/extension/install', 'POST', { name, sudo_password: cachedSudoPassword });
      if (retryRes.ok) {
        showToast(retryRes.message);
      } else {
        showToast(retryRes.error || 'فشل التثبيت', true);
        cachedSudoPassword = '';
      }
      loadPhpExtensions();
    }, () => {
      alert(`تم إلغاء العملية. للتثبيت يدوياً، تشغيل هذا الأمر:\n\nsudo apt install php-${name}\n\nوالتأكد من تفعيلها من mods-available.`);
      loadPhpExtensions();
    });
    showToast(res.error || 'Failed to install extension', true);
    loadPhpExtensions();
  }
}

function updateGauge(id, val) {
  const circle = document.getElementById(id + 'Circle');
  const text = document.getElementById(id + 'Text');
  if (circle && text) {
    circle.setAttribute('stroke-dasharray', `${val}, 100`);
    text.textContent = Math.round(val) + '%';
  }
}

async function loadStatus() {
  if (!localStorage.getItem('neold_token')) return;
  const d = await apiCall('/status');
  if (!d.ok) return;

  projectsListGlobal = d.projects || [];

  // LAN Info
  document.getElementById('netDomain').textContent = `https://${d.domain}`;
  document.getElementById('netLan').textContent = `https://${d.lan_ip}`;
  document.getElementById('netDash').textContent = `https://${d.domain}/admin/`;

  // Gauges
  if (d.stats) {
    updateGauge('cpu', d.stats.cpu);
    updateGauge('ram', d.stats.ram);
    updateGauge('disk', d.stats.disk);
  }

  // Update table
  const tbody = document.getElementById('projectsTableBody');
  if (d.projects.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--muted)">No development servers discovered yet. Run your dev servers (e.g. npm run dev, php artisan serve) and they will show up here automatically!</td></tr>`;
  } else {
    tbody.innerHTML = d.projects.map(p => `
      <tr>
        <td style="font-weight:600; color:#a5b4fc">□ ${p.name}</td>
        <td><span style="background:rgba(99,102,241,0.07); color:#a5b4fc; padding:0.15rem 0.5rem; border-radius:4px; font-size:0.75rem">${p.framework}</span></td>
        <td><span style="color:var(--muted)">${p.runtime}</span></td>
        <td><code style="color:var(--accent2)">:${p.port}</code></td>
        <td>
          <a href="https://${d.domain}:${p.secure_port}" target="_blank" style="color:#a5b4fc; text-decoration:none; font-weight:500">https://${d.domain}:${p.secure_port}</a><br>
          <span style="font-size:0.7rem; color:var(--muted)">LAN: <a href="https://${d.lan_ip}:${p.secure_port}" target="_blank" style="color:var(--muted)">https://${d.lan_ip}:${p.secure_port}</a></span>
        </td>
        <td>
          <button class="btn btn-danger btn-sm" onclick="stopProject(${p.pid}, '${p.name}')">Stop/Kill</button>
        </td>
      </tr>
    `).join('');
  }

  // Update Checklist Badges
  updateChecklistItem('caddy', d.system.caddy);
  updateChecklistItem('mariadb', d.system.mariadb);
  updateChecklistItem('php', d.system.php_fpm);
  updateChecklistItem('pma', d.system.phpmyadmin);
  
  await updateGuardianList(d);
}

async function updateGuardianList(d) {
  const approvedList = document.getElementById('guardianApprovedList');
  if (approvedList && d.allowed_ips) {
    approvedList.innerHTML = d.allowed_ips.map(ip => `<li>${ip}</li>`).join('');
  }

  const pendingList = document.getElementById('guardianPendingList');
  if (pendingList) {
    const res = await apiCall('/guardian/pending');
    if (res.ok && res.requests) {
      if (res.requests.length === 0) {
        pendingList.innerHTML = `<span style="color: var(--muted); font-style: italic">No pending requests</span>`;
      } else {
        pendingList.innerHTML = res.requests.map(ip => `
          <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(255,255,255,0.02); padding:0.5rem; border-radius:6px; border:1px solid rgba(255,255,255,0.04)">
            <code style="color:#a5b4fc">${ip}</code>
            <div style="display:flex; gap:0.4rem">
              <button class="btn btn-primary btn-sm" onclick="approveGuardianIp('${ip}')" style="padding:0.25rem 0.5rem; font-size:0.75rem; background:#10b981">Approve</button>
              <button class="btn btn-outline btn-sm" onclick="rejectGuardianIp('${ip}')" style="padding:0.25rem 0.5rem; font-size:0.75rem; color:#ef4444; border-color:rgba(239,68,68,0.3)">Reject</button>
            </div>
          </div>
        `).join('');
      }
    }
  }
}

async function approveGuardianIp(ip) {
  showToast(`Approving IP: ${ip}...`);
  const res = await apiCall('/guardian/approve', 'POST', { ip });
  if (res.ok) {
    showToast(res.message);
    loadStatus();
  } else {
    showToast(res.error || 'Failed to approve IP', true);
  }
}

async function rejectGuardianIp(ip) {
  const res = await apiCall('/guardian/reject', 'POST', { ip });
  if (res.ok) {
    showToast(res.message);
    loadStatus();
  } else {
    showToast(res.error || 'Failed to reject IP', true);
  }
}

function updateChecklistItem(id, status) {
  const version = document.getElementById(id + 'Version');
  const badge = document.getElementById(id + 'Badge');
  const controls = document.getElementById(id + 'Controls');

  version.textContent = status.installed ? status.version : 'Not installed';

  if (!status.installed) {
    badge.className = 'status-badge status-badge-err';
    badge.textContent = 'Missing';
    controls.innerHTML = `<button class="btn btn-primary btn-sm" onclick="installComponent('${id}')">Install</button>`;
  } else {
    badge.className = 'status-badge ' + (status.running ? 'status-badge-ok' : 'status-badge-err');
    badge.textContent = status.running ? 'Running' : 'Stopped';

    if (id === 'pma') {
      controls.innerHTML = `<a href="/pma/" target="_blank" class="btn btn-primary btn-sm" style="text-decoration:none">🔗 Open</a>`;
    } else {
      controls.innerHTML = `
        <button class="btn btn-outline btn-sm" onclick="controlService('${id}', '${status.running ? 'stop' : 'start'}')">
          ${status.running ? 'Stop' : 'Start'}
        </button>
        <button class="btn btn-outline btn-sm" onclick="controlService('${id}', 'restart')">↻</button>
      `;
    }
  }
}

async function controlService(service, action) {
  showToast(`${action.charAt(0).toUpperCase() + action.slice(1)}ing ${service}...`);
  const r = await apiCall('/service/control', 'POST', { service, action });
  if (r.ok) showToast(r.message);
  else showToast(r.error, true);
  loadStatus();
}

async function installComponent(component) {
  showToast(`Installing ${component}... Please wait.`);
  const r = await apiCall('/service/install', 'POST', { component });
  if (r.ok) showToast(r.message);
  else showToast(r.error, true);
  loadStatus();
}

async function stopProject(pid, name) {
  if (!confirm(`Are you sure you want to stop/kill project: ${name}?`)) return;
  const r = await apiCall('/project/stop', 'POST', { pid, name });
  if (r.ok) showToast(r.message);
  else showToast(r.error, true);
  loadStatus();
}

async function loadDatabases() {
  const d = await apiCall('/db/databases');
  if (d.ok) {
    const list = document.getElementById('dbList');
    list.innerHTML = d.databases.map(db => `
      <div style="display:flex; justify-content:space-between; align-items:center; background:rgba(255,255,255,0.03); padding:0.6rem 0.8rem; border-radius:6px">
        <span style="font-family:'JetBrains Mono'">${db}</span>
        <button class="btn btn-danger btn-sm" onclick="dropDb('${db}')">×</button>
      </div>
    `).join('');
  }
}

async function loadUsers() {
  const d = await apiCall('/db/users');
  if (d.ok) {
    const list = document.getElementById('userList');
    list.innerHTML = d.users.map(u => `
      <div style="background:rgba(255,255,255,0.03); padding:0.6rem 0.8rem; border-radius:6px; font-family:'JetBrains Mono'; font-size:0.85rem">
        ${u.user}@${u.host}
      </div>
    `).join('');
  }
}

async function createDb() {
  const name = document.getElementById('newDbName').value.trim();
  if (!name) return;
  const r = await apiCall('/db/create', 'POST', { name });
  if (r.ok) {
    showToast(r.message);
    document.getElementById('newDbName').value = '';
    loadDatabases();
  } else showToast(r.error, true);
}

async function dropDb(name) {
  if (!confirm(`Delete database: ${name}? All data will be lost!`)) return;
  const r = await apiCall('/db/drop', 'POST', { name });
  if (r.ok) loadDatabases();
  else showToast(r.error, true);
}

async function loadLogs(type) {
  const consoleEl = document.getElementById('logConsole');
  consoleEl.textContent = 'Loading ' + type + ' logs...';
  const r = await apiCall('/logs?type=' + type + '&lines=50');
  if (r.ok) {
    consoleEl.textContent = r.lines.length ? r.lines.join('\\n') : '(empty log)';
  } else {
    consoleEl.textContent = 'Failed to load logs';
  }
}

async function updateSecurity(e) {
  e.preventDefault();
  const username = document.getElementById('newUsername').value.trim();
  const password = document.getElementById('newPassword').value.trim();
  if (!username || !password) return;

  showToast('Updating security credentials...');
  const r = await apiCall('/security/update', 'POST', { username, password });
  if (r.ok) {
    showToast(r.message);
    document.getElementById('newUsername').value = '';
    document.getElementById('newPassword').value = '';
    handleLogout();
  } else {
    showToast(r.error, true);
  }
}

// Launcher specific functions
// File explorer variables
let activeExplorerTargetInput = null;
let currentExplorerPath = '';

// File Explorer functions
function openExplorer(targetInputId) {
  activeExplorerTargetInput = targetInputId;
  const initialPath = document.getElementById(targetInputId).value.trim();
  document.getElementById('explorerModal').style.display = 'flex';
  loadExplorerDir(initialPath);
}

function closeExplorerModal() {
  document.getElementById('explorerModal').style.display = 'none';
}

async function loadExplorerDir(path = '') {
  const d = await apiCall('/fs/list?path=' + encodeURIComponent(path));
  if (d.ok) {
    currentExplorerPath = d.current;
    document.getElementById('explorerPath').textContent = d.current;
    
    // Enable/disable up button
    document.getElementById('explorerUpBtn').disabled = !d.parent;
    
    const container = document.getElementById('explorerDirs');
    if (d.dirs.length === 0) {
      container.innerHTML = `<div style="color:var(--muted); text-align:center; padding:2rem">Empty Directory</div>`;
    } else {
      container.innerHTML = d.dirs.map(dir => `
        <div onclick="loadExplorerDir(decodeURIComponent('${encodeURIComponent(dir.path)}'))" style="display:flex; align-items:center; gap:0.5rem; padding:0.5rem; background:rgba(255,255,255,0.02); border-radius:4px; cursor:pointer; font-size:0.9rem" onmouseover="this.style.background='rgba(255,255,255,0.05)'" onmouseout="this.style.background='rgba(255,255,255,0.02)'">
          <span>📁</span>
          <span>${dir.name}</span>
        </div>
      `).join('');
    }
  } else {
    showToast(d.error, true);
  }
}

function explorerGoUp() {
  const parts = currentExplorerPath.split('/');
  if (parts.length > 1) {
    parts.pop();
    const upPath = parts.join('/') || '/';
    loadExplorerDir(upPath);
  }
}

function explorerConfirm() {
  if (activeExplorerTargetInput) {
    document.getElementById(activeExplorerTargetInput).value = currentExplorerPath;
  }
  closeExplorerModal();
}

// Launcher specific functions
let runningProjectPaths = new Set();

async function loadLauncherTab() {
  // Load running projects first so renderDiscovered can use them
  const spawnedRes = await apiCall('/project/spawned/list');
  
  // Synchronously fetch status to get system running projects
  const statusRes = await apiCall('/status');
  if (statusRes.ok && statusRes.projects) {
    window.projectsListGlobal = statusRes.projects;
  }

  runningProjectPaths = new Set();
  let spawnedMap = {}; // path -> pid
  if (spawnedRes.ok && spawnedRes.projects) {
    spawnedRes.projects.forEach(p => {
      runningProjectPaths.add(p.path);
      spawnedMap[p.path] = p.pid;
    });
  }

  if (window.projectsListGlobal) {
    window.projectsListGlobal.forEach(p => {
      if (p.path && p.path !== 'Unknown') {
        runningProjectPaths.add(p.path);
        if (!spawnedMap[p.path]) {
          spawnedMap[p.path] = p.pid;
        }
      }
    });
  }

  const res = await apiCall('/projects/discover');
  if (res.ok) {
    document.getElementById('projectsRootInput').value = res.projects_root || '';
    renderDiscovered(res.projects || [], res.custom_projects || [], spawnedMap);
  }
  // Render spawned list using already-fetched data
  renderSpawnedProjects(spawnedRes);
}

async function saveProjectsRoot(e) {
  e.preventDefault();
  const path = document.getElementById('projectsRootInput').value.trim();
  const res = await apiCall('/projects/root/update', 'POST', { projects_root: path });
  if (res.ok) {
    showToast(res.message);
    loadLauncherTab();
  } else {
    showToast(res.error, true);
  }
}

async function addCustomProject(e) {
  e.preventDefault();
  const path = document.getElementById('singleProjectPathInput').value.trim();
  if (!path) return;
  const res = await apiCall('/project/custom/add', 'POST', { path });
  if (res.ok) {
    showToast(res.message);
    document.getElementById('singleProjectPathInput').value = '';
    loadLauncherTab();
  } else {
    showToast(res.error, true);
  }
}

async function removeCustomProject(path) {
  if (!confirm('Are you sure you want to remove this project from your list?')) return;
  const res = await apiCall('/project/custom/remove', 'POST', { path });
  if (res.ok) {
    showToast(res.message);
    loadLauncherTab();
  } else {
    showToast(res.error, true);
  }
}

async function renameProject(path, currentName) {
  const newName = prompt("Enter new name for project in UI:", currentName);
  if (newName && newName.trim() && newName.trim() !== currentName) {
    const res = await apiCall('/project/rename', 'POST', { path, new_name: newName.trim() });
    if (res.ok) {
      showToast(res.message);
      loadLauncherTab();
    } else {
      showToast(res.error, true);
    }
  }
}

function buildProjectCard(p, spawnedMap) {
  const idSafe = p.path.replace(/[^a-zA-Z0-9]/g, '_');
  const isRunning = spawnedMap.hasOwnProperty(p.path);
  const runningPid = isRunning ? spawnedMap[p.path] : null;
  const encodedPath = encodeURIComponent(p.path);
  const borderColor = isRunning ? 'rgba(74,222,128,0.3)' : 'var(--border-light)';
  const firstCmd = (p.commands && p.commands[0]) ? p.commands[0] : '';

  // --- Header badges ---
  const manualBadge = p.isCustom ? `<span style="color:var(--accent2); font-size:0.75rem; font-weight:400">(Manual)</span>` : '';
  const runningBadge = isRunning ? `<span style="font-size:0.7rem; background:rgba(74,222,128,0.15); color:var(--green); padding:0.15rem 0.5rem; border-radius:20px; border:1px solid rgba(74,222,128,0.3)">● Running</span>` : '';
  const removeBtn = p.isCustom ? `<button onclick="removeCustomProject(decodeURIComponent('${encodedPath}'))" style="background:transparent; border:none; color:var(--red); cursor:pointer; font-size:1.1rem; padding:0 0.2rem">&times;</button>` : '';

  // --- Action area ---
  let controlsHtml = '';
  if (isRunning) {
    controlsHtml = `
      <div style="display:flex; gap:0.5rem; margin-top:0.25rem; align-items:center">
        <span style="font-size:0.75rem; color:var(--muted); flex:1">PID: <code>${runningPid}</code></span>
        <button class="btn btn-danger btn-sm" style="min-width:80px" onclick="stopSpawnedProject(${runningPid})">⏹ Stop</button>
      </div>`;
  } else {
    const cmdOptions = (p.commands || []).map(c => `<option value="${c}">${c}</option>`).join('');
    controlsHtml = `
      <div style="display:flex; flex-direction:column; gap:0.5rem; margin-top:0.25rem">
        <div style="display:flex; gap:0.5rem; align-items:center">
          <span style="font-size:0.8rem; color:var(--muted); min-width:80px">Preset:</span>
          <select class="input-control" style="flex:1; padding:0.35rem; font-size:0.8rem" onchange="document.getElementById('cmd-${idSafe}').value=this.value">
            ${cmdOptions}
          </select>
        </div>
        <div style="display:flex; gap:0.5rem">
          <input type="text" id="cmd-${idSafe}" class="input-control" style="flex:1; padding:0.35rem; font-size:0.8rem" value="${firstCmd}" placeholder="Command to run...">
          <button class="btn btn-primary btn-sm" onclick="launchProject('${p.name}', decodeURIComponent('${encodedPath}'), '${idSafe}')">🚀 Run</button>
        </div>
      </div>`;
  }

  return `
    <div style="background:rgba(255,255,255,0.02); padding:1rem; border-radius:var(--radius-sm); border:1px solid ${borderColor}; display:flex; flex-direction:column; gap:0.5rem">
      <div style="display:flex; justify-content:space-between; align-items:center">
        <strong style="color:#a5b4fc; display:flex; align-items:center; gap:0.5rem">
          ${p.name} ${manualBadge} ${runningBadge}
          <button class="logout-btn" style="display:inline; color:var(--muted); font-size:0.85rem; padding:0" onclick="renameProject(decodeURIComponent('${encodedPath}'), '${p.name}')" title="Rename">✏️</button>
        </strong>
        <div style="display:flex; align-items:center; gap:0.5rem">
          <span style="font-size:0.75rem; background:rgba(99,102,241,0.1); color:#a5b4fc; padding:0.15rem 0.4rem; border-radius:4px">${p.type || 'Project'}</span>
          ${removeBtn}
        </div>
      </div>
      <div style="font-size:0.75rem; color:var(--muted)">Path: <code>${p.path}</code></div>
      ${controlsHtml}
    </div>`;
}

function renderDiscovered(projects, customProjects, spawnedMap) {
  const container = document.getElementById('discoveredProjectsList');
  spawnedMap = spawnedMap || {};
  
  if ((!projects || projects.length === 0) && (!customProjects || customProjects.length === 0)) {
    container.innerHTML = '<div style="color:var(--muted); text-align:center; padding:2rem">No folders discovered. Set a root folder or add an individual folder above!</div>';
    return;
  }

  const all = [
    ...projects.map(function(p) { return Object.assign({}, p, { isCustom: false }); }),
    ...customProjects.map(function(p) { return Object.assign({}, p, { isCustom: true }); })
  ];

  container.innerHTML = all.map(function(p) {
    return buildProjectCard(p, spawnedMap);
  }).join('');
}

async function launchProject(name, path, idSafe) {
  // Check if already running before launching
  if (runningProjectPaths.has(path)) {
    showToast(`"${name}" is already running in the background!`, true);
    return;
  }

  const command = document.getElementById(`cmd-${idSafe}`)?.value.trim();
  if (!command) {
    showToast('Please select or write a command to run!', true);
    return;
  }

  showToast(`Starting ${name} in background...`);
  const res = await apiCall('/project/spawn', 'POST', { name, path, command });
  if (res.ok) {
    showToast(res.message);
    loadLauncherTab();
  } else {
    showToast(res.error, true);
  }
}

async function toggleLogs(pid, name) {
  const el = document.getElementById(`logs-pid-${pid}`);
  if (el.style.display === 'none') {
    el.style.display = 'block';
    el.textContent = 'Loading logs...';
    const res = await apiCall(`/project/logs?pid=${pid}`);
    if (res.ok) {
      el.textContent = res.lines.length ? res.lines.join('\\n') : '(empty log)';
      el.scrollTop = el.scrollHeight;
    } else {
      el.textContent = 'Failed to load logs: ' + res.error;
    }
  } else {
    el.style.display = 'none';
  }
}

function renderSpawnedProjects(res) {
  const container = document.getElementById('spawnedProjectsList');
  if (!res || !res.ok || !res.projects || res.projects.length === 0) {
    container.innerHTML = `<div style="color:var(--muted); text-align:center; padding:2rem">No launched projects running in the background.</div>`;
    return;
  }

  container.innerHTML = res.projects.map(p => {
    // Match any active proxy in projectsListGlobal that is within our process group pids
    const activeProj = projectsListGlobal.find(ap => p.pids && p.pids.includes(ap.pid));
    const linkBtn = activeProj ? `<a href="https://${window.location.hostname}:${activeProj.secure_port}" target="_blank" class="btn btn-outline btn-sm" style="color:var(--accent2); border-color:var(--accent2); padding:0.15rem 0.5rem; font-size:0.75rem; text-decoration:none; margin-left:0.5rem">🔗 Open Site</a>` : '';

    return `
      <div style="background:rgba(255,255,255,0.02); padding:1rem; border-radius:var(--radius-sm); border:1px solid rgba(74,222,128,0.2); display:flex; flex-direction:column; gap:0.5rem">
        <div style="display:flex; justify-content:space-between; align-items:center">
          <strong style="color:var(--green)">● ${p.name} ${linkBtn}</strong>
          <span style="font-size:0.75rem; color:var(--muted)">PID: <code>${p.pid}</code> | Uptime: ${p.uptime}s</span>
        </div>
        <div style="font-size:0.75rem; color:var(--muted)">Cmd: <code>${p.command}</code></div>
        
        <!-- Live Process Metrics -->
        <div style="display:flex; gap:1.5rem; font-size:0.8rem; background:rgba(0,0,0,0.2); padding:0.4rem 0.75rem; border-radius:6px" id="metrics-pid-${p.pid}">
          <div>CPU: <code style="color:var(--accent2)" id="cpu-pid-${p.pid}">0%</code></div>
          <div>RAM: <code style="color:#a5b4fc" id="ram-pid-${p.pid}">0%</code></div>
        </div>

        <div style="display:flex; gap:0.5rem; margin-top:0.25rem">
          <button class="btn btn-outline btn-sm" onclick="triggerStressTest(${p.pid})">🔥 Stress Test</button>
          <button class="btn btn-outline btn-sm" onclick="toggleLogs(${p.pid}, '${p.name}')">📄 Logs</button>
          <button class="btn btn-danger btn-sm" style="flex:1" onclick="stopSpawnedProject(${p.pid})">⏹ Kill / Stop</button>
        </div>
        
        <div id="logs-pid-${p.pid}" style="display:none; margin-top:0.75rem; background:#03040b; border:1px solid var(--border); border-radius:6px; padding:0.75rem; font-family:'JetBrains Mono', monospace; font-size:0.7rem; max-height:200px; overflow-y:auto; color:#a5b4fc; white-space:pre-wrap"></div>
      </div>
    `;
  }).join('');

  // Start real-time loops for spawned metrics
  res.projects.forEach(p => {
    updateProcessMetricsLoop(p.pid);
  });
}

async function loadSpawnedProjects() {
  const res = await apiCall('/project/spawned/list');
  renderSpawnedProjects(res);
}

async function updateProcessMetricsLoop(pid) {
  if (!document.getElementById(`metrics-pid-${pid}`)) return;
  const res = await apiCall(`/project/monitor?pid=${pid}`);
  if (res.ok) {
    const cpuEl = document.getElementById(`cpu-pid-${pid}`);
    const ramEl = document.getElementById(`ram-pid-${pid}`);
    if (cpuEl && ramEl) {
      cpuEl.textContent = res.cpu + '%';
      ramEl.textContent = res.ram + '%';
    }
  }
}

async function stopSpawnedProject(pid) {
  if (!confirm(`Stop this project (PID: ${pid})?`)) return;
  showToast('Stopping project...');
  const res = await apiCall('/project/spawned/stop', 'POST', { pid });
  if (res.ok) {
    showToast(res.message);
    await loadLauncherTab();
  } else {
    // Fallback: try direct process killing if it survived server restart
    const res2 = await apiCall('/project/stop', 'POST', { pid, name: 'Project' });
    if (res2.ok) {
      showToast(res2.message);
      await loadLauncherTab();
    } else {
      showToast(res2.error || 'Failed to stop project', true);
    }
  }
}

// Stress testing logic
async function triggerStressTest(pid) {
  // Find project secure port from the active proxies
  const activeProj = projectsListGlobal.find(p => p.pid == pid || p.name.toLowerCase().includes(pid));
  let url = '';
  if (activeProj) {
    url = `https://dev.local:${activeProj.secure_port}`;
  } else {
    // Prompt or fallback to localhost port
    const port = prompt("No secure proxy found for this process. Please enter its internal listening port (e.g. 8080):");
    if (!port) return;
    url = `http://127.0.0.1:${port}`;
  }

  showStressModal();
  const body = document.getElementById('stressResultsBody');
  body.innerHTML = `
    <div style="text-align:center; padding:2rem">
      <div style="font-weight:600; color:var(--accent2); margin-bottom:0.5rem">Running concurrent HTTP requests against:</div>
      <code style="color:var(--text)">${url}</code>
      <div style="color:var(--muted); font-size:0.8rem; margin-top:1rem">Sending 100 requests (concurrency: 10). Please wait...</div>
    </div>
  `;

  const res = await apiCall('/project/stresstest', 'POST', { url });
  if (res.ok && res.results) {
    const r = res.results;
    body.innerHTML = `
      <div style="background:rgba(255,255,255,0.02); border:1px solid var(--border-light); padding:1rem; border-radius:6px; display:flex; flex-direction:column; gap:0.50rem">
        <div>Target URL: <code style="color:#a5b4fc">${url}</code></div>
        <div>Total Requests: <strong>${r.total}</strong></div>
        <div style="display:flex; justify-content:space-between">
          <span>Success: <strong style="color:var(--green)">${r.success}</strong></span>
          <span>Failures: <strong style="color:var(--red)">${r.failures}</strong></span>
        </div>
        <div>Success Rate: <strong style="color:${r.success_rate >= 90 ? 'var(--green)' : 'var(--red)'}">${r.success_rate}%</strong></div>
        <div>Average Response Latency: <strong style="color:var(--accent2)">${r.avg_latency_ms} ms</strong></div>
      </div>
    `;
  } else {
    body.innerHTML = `<div style="color:var(--red); text-align:center; padding:1.5rem">Stress test failed: ${res.error}</div>`;
  }
}

function showStressModal() {
  document.getElementById('stressModal').style.display = 'flex';
}
function closeStressModal() {
  document.getElementById('stressModal').style.display = 'none';
}

// Initial authentication checking
checkAuthentication();
setInterval(loadStatus, 4000);
// Periodically update process metrics for background launched projects
setInterval(() => {
  const el = document.getElementById('spawnedProjectsList');
  if (el) {
    const list = el.querySelectorAll('[id^="metrics-pid-"]');
    list.forEach(item => {
      const pid = item.id.split('-').pop();
      updateProcessMetricsLoop(pid);
    });
  }
<!-- Sudo password confirmation modal -->
<div id="sudoModal" style="display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,0.6); backdrop-filter:blur(8px); z-index:9999; justify-content:center; align-items:center">
  <div class="panel" style="max-width:400px; width:90%; padding:2.5rem; border-radius:16px; border:1px solid rgba(239,68,68,0.2); text-align:center; box-shadow: 0 10px 30px rgba(0,0,0,0.5)">
    <div style="font-size:3rem; color:#ef4444; margin-bottom:1rem">🔒</div>
    <div class="panel-title" style="margin-bottom:0.5rem; font-size:1.4rem">صلاحيات المسؤول مطلوبة</div>
    <div style="color:var(--muted); font-size:0.85rem; margin-bottom:1.5rem; line-height:1.4">هذا الإجراء يتطلب صلاحيات السوبر يوزر لتثبيت أو تفعيل الإضافات وإعادة تشغيل PHP-FPM.</div>
    <input type="password" id="sudoPasswordInput" class="input-control" placeholder="أدخل كلمة مرور sudo الخاصة بك..." style="text-align:center; margin-bottom:1.5rem; font-size:1rem; padding:0.75rem">
    <div style="display:flex; gap:0.5rem">
      <button class="btn btn-outline" onclick="closeSudoModal()" style="flex:1; padding:0.6rem">إلغاء</button>
      <button class="btn btn-primary" id="sudoConfirmBtn" style="flex:1; padding:0.6rem; background:#ef4444; border-color:#ef4444">تأكيد</button>
    </div>
  </div>
</div>

</script>
</body>
</html>"""

GUARDIAN_UNAUTHORIZED_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Neo The Guardian LocalDev</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700;800&display=swap');
:root {
  --bg: #070814;
  --surface: rgba(15, 17, 35, 0.7);
  --border: rgba(239, 68, 68, 0.2);
  --accent: #ef4444;
  --text: #f3f4f6;
  --muted: #9ca3af;
}
body {
  margin: 0;
  padding: 0;
  background: var(--bg);
  color: var(--text);
  font-family: 'Outfit', sans-serif;
  display: flex;
  justify-content: center;
  align-items: center;
  min-height: 100vh;
  overflow: hidden;
}
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 3rem;
  border-radius: 16px;
  max-width: 450px;
  width: 90%;
  text-align: center;
  backdrop-filter: blur(12px);
  box-shadow: 0 8px 32px 0 rgba(0, 0, 0, 0.37);
}
.shield {
  font-size: 4rem;
  color: var(--accent);
  margin-bottom: 1rem;
  animation: pulse 2s infinite;
}
@keyframes pulse {
  0% { transform: scale(1); filter: drop-shadow(0 0 2px rgba(239,68,68,0.4)); }
  50% { transform: scale(1.05); filter: drop-shadow(0 0 15px rgba(239,68,68,0.8)); }
  100% { transform: scale(1); filter: drop-shadow(0 0 2px rgba(239,68,68,0.4)); }
}
h1 {
  font-weight: 700;
  font-size: 1.8rem;
  margin-bottom: 0.5rem;
  background: linear-gradient(135deg, #fff 0%, #fca5a5 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
}
p {
  color: var(--muted);
  font-size: 0.95rem;
  line-height: 1.5;
  margin-bottom: 2rem;
}
.ip-box {
  background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.05);
  padding: 0.75rem;
  border-radius: 8px;
  font-family: monospace;
  font-size: 1.1rem;
  color: #fca5a5;
  margin-bottom: 2rem;
}
.btn {
  background: var(--accent);
  color: white;
  border: none;
  padding: 0.85rem 2rem;
  border-radius: 8px;
  font-weight: 600;
  cursor: pointer;
  width: 100%;
  transition: all 0.3s;
}
.btn:hover {
  background: #dc2626;
  box-shadow: 0 0 15px rgba(239,68,68,0.4);
}
.btn:disabled {
  background: rgba(255,255,255,0.1);
  color: var(--muted);
  cursor: not-allowed;
}
</style>
</head>
<body>
<div class="card">
  <div class="shield">🛡️</div>
  <h1>Neo The Guardian</h1>
  <p>Your device is not approved to access this local environment.</p>
  <div class="ip-box" id="ipBox">IP: {client_ip}</div>
  <button class="btn" id="reqBtn" onclick="requestAccess()">Request Access</button>
</div>

<script>
async function requestAccess() {
  const btn = document.getElementById('reqBtn');
  btn.disabled = true;
  btn.textContent = 'Sending Request...';
  
  try {
    const res = await fetch('/guardian/request', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ip: '{client_ip}' })
    });
    const data = await res.json();
    if (data.ok) {
      btn.textContent = 'Request Sent ✔';
      btn.style.background = '#10b981';
      document.querySelector('p').textContent = 'Request sent successfully. Please ask the developer to approve your device in their dashboard.';
    } else {
      btn.textContent = 'Failed to send request';
      btn.disabled = false;
    }
  } catch (e) {
    btn.textContent = 'Connection Error';
    btn.disabled = false;
  }
}
</script>
</body>
</html>"""

from socketserver import ThreadingMixIn

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def run_api_server():
    import signal
    cfg = cfgmod.load_config()
    port = API_PORT

    server = ThreadedHTTPServer(("127.0.0.1", port), DashboardAPIHandler)
    logger.info(f"Dashboard API server running on 127.0.0.1:{port}")

    def shutdown(sig, frame):
        logger.info("Shutting down API server...")
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    run_api_server()

