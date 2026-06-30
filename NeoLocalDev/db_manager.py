import subprocess
import logging
import shutil
import psutil

logger = logging.getLogger("devpoka")

def run_mysql_cmd(query):
    """Executes a MySQL command using mysql CLI."""
    try:
        cmd = ["mysql", "-u", "root", "-e", query]
        out = subprocess.check_output(cmd, text=True, stderr=subprocess.STDOUT)
        return {"ok": True, "output": out}
    except subprocess.CalledProcessError as e:
        logger.error(f"MySQL query error: {e.output}")
        return {"ok": False, "error": e.output}

def is_db_running():
    for conn in psutil.net_connections(kind="tcp"):
        if conn.status == "LISTEN" and conn.laddr.port == 3306:
            return True
    return False

def control_service(action):
    """Controls MariaDB service using systemctl."""
    # Action can be: start, stop, restart
    try:
        subprocess.check_call(["sudo", "systemctl", action, "mariadb"])
        return {"ok": True}
    except Exception as e:
        logger.error(f"Failed to {action} MariaDB: {e}")
        return {"ok": False, "error": str(e)}

def list_databases():
    res = run_mysql_cmd("SHOW DATABASES;")
    if not res["ok"]:
        return []
    lines = res["output"].strip().split("\n")
    # First line is header "Database"
    if len(lines) > 1:
        return lines[1:]
    return []

def create_database(name):
    res = run_mysql_cmd(f"CREATE DATABASE IF NOT EXISTS `{name}`;")
    return res["ok"]

def drop_database(name):
    res = run_mysql_cmd(f"DROP DATABASE IF EXISTS `{name}`;")
    return res["ok"]

def import_sql(db_name, filepath):
    try:
        with open(filepath, "r") as f:
            cmd = ["mysql", "-u", "root", db_name]
            subprocess.run(cmd, stdin=f, check=True, capture_output=True)
            return True
    except Exception as e:
        logger.error(f"Failed to import SQL: {e}")
        return False

def export_sql(db_name, filepath):
    try:
        with open(filepath, "w") as f:
            cmd = ["mysqldump", "-u", "root", db_name]
            subprocess.run(cmd, stdout=f, check=True, capture_output=True)
            return True
    except Exception as e:
        logger.error(f"Failed to export SQL: {e}")
        return False

def list_users():
    res = run_mysql_cmd("SELECT User, Host FROM mysql.user;")
    if not res["ok"]:
        return []
    lines = res["output"].strip().split("\n")
    users = []
    if len(lines) > 1:
        for line in lines[1:]:
            parts = line.split()
            if len(parts) >= 2:
                users.append({"user": parts[0], "host": parts[1]})
    return users

def create_user(username, password, host="localhost"):
    query = f"CREATE USER '{username}'@'{host}' IDENTIFIED BY '{password}'; GRANT ALL PRIVILEGES ON *.* TO '{username}'@'{host}' WITH GRANT OPTION; FLUSH PRIVILEGES;"
    res = run_mysql_cmd(query)
    return res["ok"]

def drop_user(username, host="localhost"):
    query = f"DROP USER '{username}'@'{host}'; FLUSH PRIVILEGES;"
    res = run_mysql_cmd(query)
    return res["ok"]
