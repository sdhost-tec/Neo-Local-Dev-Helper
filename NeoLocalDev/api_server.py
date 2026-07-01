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
from .html_assets import GUARDIAN_UNAUTHORIZED_HTML, GUARDIAN_APPROVED_HTML

logger = logging.getLogger("devpoka.api")

API_PORT = 9199
START_TIME = time.time()
ACTIVE_SESSIONS = set()
PENDING_GUARDIAN_REQUESTS = set()
PROCESS_CACHE = {}

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

class RestoredProcess:
    def __init__(self, pid):
        self.pid = pid
    def poll(self):
        try:
            p = psutil.Process(self.pid)
            if p.is_running() and p.status() != psutil.STATUS_ZOMBIE:
                return None
            return 0
        except Exception:
            return 0

def get_spawned_json_path():
    return Path(cfgmod.get_config_dir()) / "spawned_projects.json"

def save_spawned_projects():
    try:
        path = get_spawned_json_path()
        data = {}
        for pid, p in SPAWNED_PROJECTS.items():
            data[str(pid)] = {
                "name": p["name"],
                "path": p["path"],
                "command": p["command"],
                "start_time": p["start_time"],
                "log_file_path": p.get("log_file_path", "")
            }
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Failed to save spawned projects: {e}")

def load_spawned_projects():
    global SPAWNED_PROJECTS
    try:
        path = get_spawned_json_path()
        if not path.exists():
            return
        with open(path, "r") as f:
            data = json.load(f)
        for pid_str, info in data.items():
            pid = int(pid_str)
            try:
                proc = psutil.Process(pid)
                cwd = proc.cwd()
                if os.path.exists(info["path"]) and os.path.abspath(cwd) == os.path.abspath(info["path"]):
                    log_file = None
                    if info.get("log_file_path"):
                        try:
                            log_file = open(info["log_file_path"], "a", buffering=1)
                        except Exception:
                            pass
                    SPAWNED_PROJECTS[pid] = {
                        "process": RestoredProcess(pid),
                        "name": info["name"],
                        "path": info["path"],
                        "command": info["command"],
                        "start_time": info["start_time"],
                        "log_file": log_file,
                        "log_file_path": info.get("log_file_path", "")
                    }
            except Exception:
                pass
    except Exception as e:
        logger.error(f"Failed to load spawned projects: {e}")

load_spawned_projects()


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
    latencies = []
    status_codes = {}
    lock = threading.Lock()
    start_total = time.time()
    
    # Try to find PID for CPU tracking
    target_pid = None
    from urllib.parse import urlparse
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc
        if ":" in netloc:
            port = int(netloc.split(":")[-1])
            active = detectormod.detect_running_projects()
            for p in active:
                if p.get("secure_port") == port or p.get("port") == port:
                    target_pid = p.get("pid")
                    break
    except Exception:
        pass

    cpu_samples = []
    stop_sampling = False
    
    def sample_cpu():
        if not target_pid:
            return
        try:
            if target_pid not in PROCESS_CACHE:
                PROCESS_CACHE[target_pid] = psutil.Process(target_pid)
            proc = PROCESS_CACHE[target_pid]
            # Warm up calls
            proc.cpu_percent(interval=None)
            for child in proc.children(recursive=True):
                try:
                    c_pid = child.pid
                    if c_pid not in PROCESS_CACHE:
                        PROCESS_CACHE[c_pid] = child
                    PROCESS_CACHE[c_pid].cpu_percent(interval=None)
                except Exception:
                    pass
        except Exception:
            pass

        while not stop_sampling:
            time.sleep(0.15)
            if stop_sampling:
                break
            try:
                proc = PROCESS_CACHE[target_pid]
                cpu = proc.cpu_percent(interval=None)
                for child in proc.children(recursive=True):
                    try:
                        c_pid = child.pid
                        if c_pid not in PROCESS_CACHE:
                            PROCESS_CACHE[c_pid] = child
                        cpu += PROCESS_CACHE[c_pid].cpu_percent(interval=None)
                    except Exception:
                        pass
                cpu_samples.append(round(cpu, 1))
            except Exception:
                pass
                
    sampler_thread = None
    if target_pid:
        sampler_thread = threading.Thread(target=sample_cpu, daemon=True)
        sampler_thread.start()

    def fetch_one():
        start = time.time()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'NeoLD Stress Tester'})
            with urllib.request.urlopen(req, timeout=3) as r:
                r.read()
                elapsed = time.time() - start
                with lock:
                    latencies.append(elapsed)
                    status_codes[200] = status_codes.get(200, 0) + 1
        except urllib.error.HTTPError as e:
            with lock:
                status_codes[e.code] = status_codes.get(e.code, 0) + 1
        except Exception:
            with lock:
                status_codes["Error"] = status_codes.get("Error", 0) + 1
            
    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(fetch_one) for _ in range(num_requests)]
        concurrent.futures.wait(futures)
        
    stop_sampling = True
    if sampler_thread:
        sampler_thread.join(timeout=1.0)
        
    total_duration = time.time() - start_total
    rps = num_requests / total_duration if total_duration > 0 else 0
    avg_latency = (sum(latencies) / len(latencies)) * 1000 if latencies else 0
    min_latency = min(latencies) * 1000 if latencies else 0
    max_latency = max(latencies) * 1000 if latencies else 0
    
    success_count = status_codes.get(200, 0)
    failure_count = sum(v for k, v in status_codes.items() if k != 200)
    
    return {
        "success": success_count,
        "failures": failure_count,
        "total": num_requests,
        "rps": round(rps, 1),
        "duration_seconds": round(total_duration, 2),
        "avg_latency_ms": round(avg_latency, 2),
        "min_latency_ms": round(min_latency, 2),
        "max_latency_ms": round(max_latency, 2),
        "status_codes": status_codes,
        "success_rate": round((success_count / num_requests) * 100, 2) if num_requests else 0,
        "cpu_samples": cpu_samples
    }


def get_spawned_projects():
    # Clean up dead processes
    dead_pids = []
    for pid, proc in list(SPAWNED_PROJECTS.items()):
        if proc["process"].poll() is not None:
            dead_pids.append(pid)
    if dead_pids:
        for pid in dead_pids:
            try:
                del SPAWNED_PROJECTS[pid]
            except KeyError:
                pass
        save_spawned_projects()
        
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

def clean_ip(ip_str):
    if not ip_str:
        return ""
    ip_str = ip_str.split(",")[0].strip()
    if ip_str.startswith("["):
        end_idx = ip_str.find("]")
        if end_idx != -1:
            return ip_str[1:end_idx]
    if ":" in ip_str:
        if ip_str.count(":") == 1:
            return ip_str.split(":")[0]
    return ip_str

def is_valid_ip(ip):
    import ipaddress
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False

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
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_html(self, html, status=200):
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(html.encode())
        except (BrokenPipeError, ConnectionResetError):
            pass

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

        # Serve static dashboard files (Vue app)
        if path.startswith("/admin/static/") or path.startswith("/static/"):
            rel = path.replace("/admin/static/", "").replace("/static/", "")
            static_dir = Path(__file__).resolve().parent / "static"
            file_path = static_dir / rel
            if file_path.exists() and file_path.is_file():
                ext = file_path.suffix.lower()
                content_types = {
                    ".html": "text/html; charset=utf-8",
                    ".js":   "application/javascript",
                    ".css":  "text/css",
                    ".png":  "image/png",
                    ".svg":  "image/svg+xml",
                }
                ct = content_types.get(ext, "application/octet-stream")
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(file_path.read_bytes())
            else:
                self.send_response(404)
                self.end_headers()
            return

        if path == "/admin/rootCA.pem" or path == "/rootCA.pem":
            pem_path = Path(__file__).resolve().parent.parent / "rootCA.pem"
            if not pem_path.exists():
                # Try to export it on-demand
                project_root = str(Path(__file__).resolve().parent.parent)
                certsmod.export_rootca_to_project(project_root)
            if pem_path.exists():
                self.send_response(200)
                self.send_header("Content-Type", "application/x-pem-file")
                self.send_header("Content-Disposition", 'attachment; filename="rootCA.pem"')
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(pem_path.read_bytes())
            else:
                self._send_json({"ok": False, "error": "rootCA.pem not found. Run: neold cert-trust"}, 404)
            return

        if path == "/guardian/unauthorized":
            client_ip = clean_ip(self.headers.get("X-Forwarded-For", self.client_address[0]))
            cfg = cfgmod.load_config()
            allowed = cfg.get("allowed_ips", ["127.0.0.1", "::1"])
            if client_ip in allowed:
                html = GUARDIAN_APPROVED_HTML.replace("{client_ip}", client_ip)
                self._send_html(html)
                return
            html = GUARDIAN_UNAUTHORIZED_HTML.replace("{client_ip}", client_ip)
            self._send_html(html)
            return

        if path == "/guardian/check":
            client_ip = clean_ip(self.headers.get("X-Forwarded-For", self.client_address[0]))
            cfg = cfgmod.load_config()
            allowed = cfg.get("allowed_ips", ["127.0.0.1", "::1"])
            self._send_json({"ok": True, "approved": client_ip in allowed})
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

            # rootCA.pem availability
            project_root = str(Path(__file__).resolve().parent.parent)
            rootca_path = Path(project_root) / "rootCA.pem"
            rootca_available = rootca_path.exists()

            # Build allowed IPs with expiry info
            import datetime
            ip_expiry = cfg.get("ip_expiry", {})
            now = datetime.datetime.now(datetime.timezone.utc)
            allowed_devices = []
            for ip in list(cfg.get("allowed_ips", ["127.0.0.1", "::1"])):
                expiry_str = ip_expiry.get(ip)
                if expiry_str:
                    try:
                        exp = datetime.datetime.fromisoformat(expiry_str)
                        if exp.tzinfo is None:
                            exp = exp.replace(tzinfo=datetime.timezone.utc)
                        if exp < now:
                            cfg["allowed_ips"].remove(ip)
                            ip_expiry.pop(ip, None)
                            cfg["ip_expiry"] = ip_expiry
                            cfgmod.save_config(cfg)
                            continue
                        remaining = (exp - now).days
                        allowed_devices.append({"ip": ip, "expiry": expiry_str, "remaining_days": remaining})
                    except Exception:
                        allowed_devices.append({"ip": ip, "expiry": None, "remaining_days": None})
                else:
                    allowed_devices.append({"ip": ip, "expiry": None, "remaining_days": None})

            self._send_json({
                "ok": True,
                "domain": cfg["domain"],
                "lan_ip": lan_ip,
                "system": system_status,
                "projects": projects,
                "stats": stats,
                "allowed_ips": cfg.get("allowed_ips", ["127.0.0.1", "::1"]),
                "allowed_devices": allowed_devices,
                "uptime": time.time() - START_TIME,
                "rootca_available": rootca_available,
                "admin_username": cfg.get("admin_username", "admin")
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

        elif path == "/api/certs/export":
            project_root = str(Path(__file__).resolve().parent.parent)
            result = certsmod.export_rootca_to_project(project_root)
            if result:
                self._send_json({"ok": True, "path": result, "message": "rootCA.pem exported successfully."})
            else:
                self._send_json({"ok": False, "error": "Failed to export rootCA.pem. Make sure mkcert is installed and 'neold cert-trust' has been run."}, 500)
            return

        elif path == "/api/logs":
            qs = self._parse_query()
            log_type = qs.get("type", "access")
            lines_count = int(qs.get("lines", "100"))
            fname = {"access": "caddy-access.log", "error": "caddy.log", "api": "api.log"}
            log_file = Path(cfg["log_dir"]) / fname.get(log_type, "caddy-access.log")
            lines = []
            if log_file.exists():
                lines = log_file.read_text(errors="replace").splitlines()[-lines_count:]
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
                if pid not in PROCESS_CACHE:
                    PROCESS_CACHE[pid] = psutil.Process(pid)
                proc = PROCESS_CACHE[pid]
                cpu = proc.cpu_percent(interval=None)
                mem = (proc.memory_info().rss / psutil.virtual_memory().total) * 100
                for child in proc.children(recursive=True):
                    try:
                        c_pid = child.pid
                        if c_pid not in PROCESS_CACHE:
                            PROCESS_CACHE[c_pid] = child
                        cpu += PROCESS_CACHE[c_pid].cpu_percent(interval=None)
                        mem += (PROCESS_CACHE[c_pid].memory_info().rss / psutil.virtual_memory().total) * 100
                    except Exception:
                        pass
                
                # Cleanup dead keys in cache
                dead_pids = [k for k in PROCESS_CACHE.keys() if not psutil.pid_exists(k)]
                for k in dead_pids:
                    PROCESS_CACHE.pop(k, None)
                    
                self._send_json({"ok": True, "cpu": round(cpu, 1), "ram": round(mem, 1)})
            except psutil.NoSuchProcess:
                PROCESS_CACHE.pop(pid, None)
                self._send_json({"ok": False, "error": "Process has exited"}, 404)
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
            # Serve Vue dashboard from static/index.html
            static_index = Path(__file__).resolve().parent / "static" / "index.html"
            if static_index.exists():
                self._send_html(static_index.read_text())
            else:
                self._send_json({"ok": False, "error": "Dashboard static files not found"}, 404)

        else:
            self._send_json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        cfg = cfgmod.load_config()
        path = self._path()

        if path == "/guardian/request":
            body = self._read_body()
            ip = body.get("ip")
            if ip:
                ip = clean_ip(ip)
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
            duration = body.get("duration", "permanent")  # "1d", "7d", "30d", "permanent"
            if not ip:
                self._send_json({"ok": False, "error": "IP is required"}, 400)
                return
            if not is_valid_ip(ip):
                self._send_json({"ok": False, "error": "Invalid IP address format"}, 400)
                return
            allowed = cfg.get("allowed_ips", ["127.0.0.1", "::1"])
            if ip not in allowed:
                allowed.append(ip)
                cfg["allowed_ips"] = allowed

            # Store expiry info if not permanent
            if duration != "permanent":
                days = {"1d": 1, "7d": 7, "30d": 30}.get(duration, 0)
                if days > 0:
                    import datetime
                    expiry = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)).isoformat()
                    ip_expiry = cfg.get("ip_expiry", {})
                    ip_expiry[ip] = expiry
                    cfg["ip_expiry"] = ip_expiry
                    label = f"{days} day{'s' if days > 1 else ''}"
                else:
                    label = "permanent"
            else:
                # Remove any existing expiry for this IP
                ip_expiry = cfg.get("ip_expiry", {})
                ip_expiry.pop(ip, None)
                cfg["ip_expiry"] = ip_expiry
                label = "permanent"

            cfgmod.save_config(cfg)
            PENDING_GUARDIAN_REQUESTS.discard(ip)
            self._trigger_reload()
            self._send_json({"ok": True, "message": f"IP '{ip}' approved ({label})."})
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

        elif path == "/api/guardian/revoke":
            body = self._read_body()
            ip = body.get("ip")
            if not ip:
                self._send_json({"ok": False, "error": "IP is required"}, 400)
                return
            # Never allow revoking localhost/server IP
            protected = {"127.0.0.1", "::1"}
            if ip in protected:
                self._send_json({"ok": False, "error": "Cannot revoke localhost access."}, 400)
                return
            allowed = cfg.get("allowed_ips", ["127.0.0.1", "::1"])
            if ip in allowed:
                allowed.remove(ip)
                cfg["allowed_ips"] = allowed
            ip_expiry = cfg.get("ip_expiry", {})
            ip_expiry.pop(ip, None)
            cfg["ip_expiry"] = ip_expiry
            cfgmod.save_config(cfg)
            self._trigger_reload()
            self._send_json({"ok": True, "message": f"IP '{ip}' access revoked."})
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

        elif path == "/api/logs/clear":
            body = self._read_body()
            log_type = body.get("type", "api")
            fname = {"access": "caddy-access.log", "error": "caddy.log", "api": "api.log"}
            log_file = Path(cfg["log_dir"]) / fname.get(log_type, "api.log")
            try:
                if log_file.exists():
                    log_file.write_text("")
                self._send_json({"ok": True, "message": f"Log '{log_type}' cleared."})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, 500)
            return

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
                save_spawned_projects()
                
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
                    save_spawned_projects()
                    self._send_json({"ok": True, "message": f"Project stopped successfully"})
                except Exception as e:
                    kill_process(pid)
                    try:
                        proc_info["log_file"].close()
                    except Exception:
                        pass
                    if pid in SPAWNED_PROJECTS:
                        del SPAWNED_PROJECTS[pid]
                    save_spawned_projects()
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
            
            num_requests = int(body.get("requests", 100))
            concurrency = int(body.get("concurrency", 10))
            try:
                results = run_stress_test(url, num_requests=num_requests, concurrency=concurrency)
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

# HTML templates extracted to html_assets.py

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

