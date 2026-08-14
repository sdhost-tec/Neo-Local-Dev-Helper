import logging
import socket
from pathlib import Path
from .installer import get_pma_dir

logger = logging.getLogger("devpoka")

def _get_lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(1)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return None

def _get_fastcgi_backend():
    import os
    import glob
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.5)
        res = s.connect_ex(("127.0.0.1", 9000))
        s.close()
        if res == 0:
            return "127.0.0.1:9000"
    except Exception:
        pass

    socks = glob.glob("/run/php/*.sock") + glob.glob("/var/run/php/*.sock")
    for sock in sorted(set(socks)):
        if os.path.islink(sock) and not os.path.exists(sock):
            continue
        try:
            us = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            us.settimeout(0.5)
            c_res = us.connect_ex(sock)
            us.close()
            if c_res == 0:
                return f"unix/{sock}"
        except Exception:
            pass

    for sock in sorted(set(socks)):
        if os.path.exists(sock):
            return f"unix/{sock}"

    return "127.0.0.1:9000"

def generate_caddyfile(domain, ssl_cert, ssl_key, caddy_dir, log_dir, projects=None):
    from .config import load_config
    cfg = load_config()
    allowed_ips = cfg.get("allowed_ips", ["127.0.0.1", "::1"])
    # Convert whitelist array to space-separated IPs/subnets for Caddyfile remote_ip directive
    allowed_ips_str = " ".join(allowed_ips)

    caddyfile_path = Path(caddy_dir) / "Caddyfile"
    lan_ip = _get_lan_ip()
    api_port = 9199
    pma_dir = get_pma_dir()
    fastcgi_target = _get_fastcgi_backend()
    
    # phpMyAdmin block if installed
    pma_block = ""
    if (pma_dir / "index.php").exists():
        pma_block = f"""
    handle_path /pma* {{
        root * {pma_dir}
        file_server
        php_fastcgi {fastcgi_target}
    }}
"""

    # Guardian rule block snippet to inject into main domain and LAN blocks
    guardian_check = f"""
    @unauthorized {{
        not remote_ip {allowed_ips_str}
    }}
    handle @unauthorized {{
        @guardian_pass {{
            path /guardian/*
        }}
        handle @guardian_pass {{
            reverse_proxy 127.0.0.1:{api_port} {{
                header_up X-Forwarded-For {{remote_host}}
            }}
        }}
        handle {{
            rewrite * /guardian/unauthorized
            reverse_proxy 127.0.0.1:{api_port} {{
                header_up X-Forwarded-For {{remote_host}}
            }}
        }}
    }}
"""

    # Generate project blocks on unique secure ports
    project_port_blocks = ""
    if projects:
        for proj in projects:
            sec_port = proj.get("secure_port")
            orig_port = proj.get("port")
            if sec_port and orig_port:
                # Use LAN IP for guardian redirect so phones without dev.local DNS can reach the page
                guardian_redirect_host = lan_ip if lan_ip else domain
                project_port_blocks += f"""
:{sec_port} {{
    tls {ssl_cert} {ssl_key}
    
    @unauthorized {{
        not remote_ip {allowed_ips_str}
    }}
    handle @unauthorized {{
        @guardian_pass {{
            path /guardian/*
        }}
        handle @guardian_pass {{
            reverse_proxy 127.0.0.1:{api_port} {{
                header_up X-Forwarded-For {{remote_host}}
            }}
        }}
        handle {{
            redir https://{guardian_redirect_host}/guardian/unauthorized?from=https://{{host}}{{uri}}
        }}
    }}

    @hmr {{
        path /_next/webpack-hmr*
    }}
    reverse_proxy @hmr 127.0.0.1:{orig_port} {{
        header_up Host localhost:{orig_port}
        header_up Origin http://localhost:{orig_port}
        header_up Referer http://localhost:{orig_port}/
    }}

    reverse_proxy 127.0.0.1:{orig_port}
    log {{
        output file {log_dir}/caddy-access.log
    }}
}}
"""

    # Main domain block
    domain_block = f"""{domain} {{
    tls {ssl_cert} {ssl_key}
{guardian_check}
    handle /admin* {{
        reverse_proxy 127.0.0.1:{api_port}
    }}
{pma_block}
    handle {{
        respond "Neo LocalDev helper is running. Access Dashboard at /admin"
    }}

    log {{
        output file {log_dir}/caddy-access.log
    }}
}}
"""

    # LAN IP block (so other devices can access dashboard/phpmyadmin directly via IP)
    lan_block = ""
    if lan_ip:
        lan_block = f"""
{lan_ip} {{
    tls {ssl_cert} {ssl_key}
{guardian_check}
    handle /admin* {{
        reverse_proxy 127.0.0.1:{api_port}
    }}
{pma_block}
    handle {{
        respond "Neo LocalDev helper is running. Access Dashboard at /admin"
    }}

    log {{
        output file {log_dir}/caddy-access.log
    }}
}}
"""

    # HTTP → HTTPS redirect block for all hostnames
    http_redirect_block = f"""http://{domain} {{
    redir https://{domain}{{uri}} permanent
}}
"""
    if lan_ip:
        http_redirect_block += f"""http://{lan_ip} {{
    redir https://{lan_ip}{{uri}} permanent
}}
"""

    content = http_redirect_block + domain_block + lan_block + project_port_blocks

    caddyfile_path.write_text(content)
    logger.info(f"Caddyfile generated at {caddyfile_path}")
    
    if lan_ip:
        logger.info(f"LAN IP {lan_ip} added for network routing")
    if projects:
        for proj in projects:
            logger.info(f"  Routed: {proj['name']} ({proj['framework']}) -> https://{domain}:{proj['secure_port']}")
            
    return str(caddyfile_path)
