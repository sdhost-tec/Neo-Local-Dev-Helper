import time
import logging
import threading
from .detector import detect_running_projects

logger = logging.getLogger("devpoka")

class ProjectWatcher:
    def __init__(self, cfg, on_change):
        self.cfg = cfg
        self.on_change = on_change
        self._running = False
        self._thread = None
        self._last_projects = []

    def start(self):
        self._last_projects = detect_running_projects()
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        logger.info("Project auto-discovery watcher started")

    def stop(self):
        self._running = False
        logger.info("Project auto-discovery watcher stopped")

    def _run(self):
        interval = self.cfg.get("watcher_interval", 3)
        while self._running:
            try:
                current_projects = detect_running_projects()
                
                # Check for changes in project ports or presence
                has_changed = False
                if len(current_projects) != len(self._last_projects):
                    has_changed = True
                else:
                    # Compare each project by name, port, secure_port, and PID
                    for p1, p2 in zip(current_projects, self._last_projects):
                        if (p1["name"] != p2["name"] or 
                            p1["port"] != p2["port"] or 
                            p1["secure_port"] != p2["secure_port"] or
                            p1["pid"] != p2["pid"]):
                            has_changed = True
                            break

                if has_changed:
                    logger.info("Change in running projects detected, reloading proxy...")
                    self._last_projects = current_projects
                    self.on_change(current_projects)

                time.sleep(interval)
            except Exception as e:
                logger.error(f"ProjectWatcher error: {e}")
                time.sleep(5)
