#!/usr/bin/env python3
import sys
import re
import os
import json
import subprocess
import signal
import time
import socket
import ipaddress
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone


DEFAULT_VPN_PORT = 9735
DATA_DIR = os.getenv("DATA_DIR", "/data")
CONFIG_PATH = os.path.join(DATA_DIR, "tunnelsatsv3.conf")
APP_CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
META_FILE_PATH = os.path.join(DATA_DIR, "tunnelsats-meta.json")
TUNNELSATS_API_URL = "https://tunnelsats.com/api/public/v1"

_enabled_cache = None
_enabled_cache_mtime = 0

def parse_config_comments(config_content):
    meta = {}
    for line in config_content.splitlines():
        line = line.strip()
        if match := re.match(r"^#\s*Valid Until:\s*(.+)", line, re.IGNORECASE):
            meta["expiresAt"] = match.group(1).strip()
        elif match := re.match(r"^#\s*(?:VPNPort|Port Forwarding):\s*(\d+)", line, re.IGNORECASE):
            meta["vpnPort"] = int(match.group(1))
    return meta

def is_valid_iso_expiry(expiry_str):
    if not expiry_str:
        return False
    try:
        # ISO format like "2026-10-02T21:18:07.000Z"
        datetime.fromisoformat(expiry_str.replace("Z", "+00:00"))
        return True
    except Exception:
        return False

def atomic_write_json(filepath, data):
    tmp_path = filepath + ".tmp"
    try:
        with open(tmp_path, 'w') as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, filepath)
    except Exception as e:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass
        raise e

def get_default_gateway():
    if hasattr(get_default_gateway, "_cache"):
        return get_default_gateway._cache
    try:
        with open("/proc/net/route", "r") as f:
            for line in f.read().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "00000000":
                    hex_gw = parts[2]
                    octets = [int(hex_gw[i:i+2], 16) for i in range(0, 8, 2)]
                    octets.reverse()
                    get_default_gateway._cache = ".".join(map(str, octets))
                    return get_default_gateway._cache
    except Exception:
        pass
    return None

def get_wg_pubkey():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                config_content = f.read()
            private_key_match = re.search(r'^\s*(?!#|;)\s*PrivateKey\s*=\s*(.+)', config_content, re.IGNORECASE | re.MULTILINE)
            if private_key_match:
                proc = subprocess.run(["wg", "pubkey"], input=private_key_match.group(1).strip().encode(), capture_output=True)
                if proc.returncode == 0:
                    return proc.stdout.decode().strip()
        except Exception:
            pass
    return "Unknown"

def lazy_sync(wg_pubkey):
    if not wg_pubkey or wg_pubkey == "Unknown" or wg_pubkey == "Not available":
        return

    import urllib.request
    import urllib.error
    url = f"{TUNNELSATS_API_URL}/subscription/status"
    data = json.dumps({"wgPublicKey": wg_pubkey}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"TunnelSats-StartOS/{get_package_version()}"
        },
        method="POST"
    )
    
    meta = {}
    loaded_existing = False
    try:
        # Load existing metadata if it exists
        if os.path.exists(META_FILE_PATH):
            try:
                with open(META_FILE_PATH, 'r') as f:
                    meta = json.load(f)
                    loaded_existing = True
            except Exception:
                pass

        response_data = None
        api_success = False
        import urllib.error
        for attempt in range(5):
            try:
                with urllib.request.urlopen(req, timeout=10) as response:
                    response_data = json.loads(response.read().decode("utf-8"))
                    api_success = True
                    break
            except urllib.error.HTTPError as e:
                if 400 <= e.code < 500:
                    raise e
                if attempt == 4:
                    raise e
                time.sleep(5)
            except Exception as e:
                if attempt == 4:
                    raise e
                time.sleep(5)

        if api_success and response_data and isinstance(response_data, dict):
            expiry = response_data.get("expiry")
            if expiry and is_valid_iso_expiry(expiry):
                meta["expiresAt"] = expiry
            
            server_domain = response_data.get("server_domain")
            if server_domain:
                meta["serverDomain"] = server_domain
            
            vpn_port = response_data.get("vpn_port")
            if vpn_port:
                meta["vpnPort"] = vpn_port
                
            meta["lastSync"] = datetime.now(timezone.utc).isoformat()
            meta["syncSuccess"] = True

        # Fallback to comments parsing if config file exists and we don't have expiresAt
        if not meta.get("expiresAt") and os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'r') as f:
                    config_content = f.read()
                parsed = parse_config_comments(config_content)
                expiry = parsed.get("expiresAt")
                if expiry and is_valid_iso_expiry(expiry):
                    meta["expiresAt"] = expiry
            except Exception:
                pass

        # Write metadata back atomically if we successfully loaded/updated something
        if meta or not loaded_existing:
            meta["syncError"] = None
            atomic_write_json(META_FILE_PATH, meta)
            
    except Exception as e:
        err_msg = str(e)
        print(f"Error during lazy subscription sync: {err_msg}", file=sys.stderr)
        meta["syncSuccess"] = False
        meta["syncError"] = err_msg
        meta["lastSyncAttempt"] = datetime.now(timezone.utc).isoformat()
        
        # Fallback to comments parsing on error if file does not have expiry
        if not meta.get("expiresAt") and os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'r') as f:
                    config_content = f.read()
                parsed = parse_config_comments(config_content)
                expiry = parsed.get("expiresAt")
                if expiry and is_valid_iso_expiry(expiry):
                    meta["expiresAt"] = expiry
            except Exception:
                pass
        try:
            atomic_write_json(META_FILE_PATH, meta)
        except Exception:
            pass

def format_subscription_expiry():
    if not os.path.exists(META_FILE_PATH):
        return "Unknown"
    
    try:
        with open(META_FILE_PATH, 'r') as f:
            meta = json.load(f)
        expires_at = meta.get("expiresAt")
        if not expires_at:
            return "Unknown"
            
        try:
            expiry_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expiry_dt.tzinfo is None:
                expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
            
            now = datetime.now(timezone.utc)
            if expiry_dt < now:
                return f"Expired (on {expiry_dt.strftime('%Y-%m-%d')})"
                
            delta = expiry_dt - now
            days = delta.days
            hours = delta.seconds // 3600
            
            if days > 0:
                return f"Active (Expires in {days}d {hours}h)"
            else:
                minutes = (delta.seconds % 3600) // 60
                return f"Active (Expires in {hours}h {minutes}m)"
        except Exception:
            return f"Expires: {expires_at}"
    except Exception:
        pass
    return "Unknown"

def subscription_sync_loop():
    try:
        time.sleep(5)
    except KeyboardInterrupt:
        return
    while True:
        sync_success = False
        try:
            pubkey = get_wg_pubkey()
            if pubkey and pubkey != "Unknown":
                lazy_sync(pubkey)
                if os.path.exists(META_FILE_PATH):
                    try:
                        with open(META_FILE_PATH, 'r') as f:
                            meta = json.load(f)
                        if meta.get("syncSuccess") and meta.get("expiresAt"):
                            sync_success = True
                    except Exception:
                        pass
        except Exception as e:
            print(f"Error in subscription sync loop: {e}", file=sys.stderr)
        
        try:
            sleep_time = 86400 if sync_success else 300
            time.sleep(sleep_time)
        except KeyboardInterrupt:
            break

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_package_version_cache = None

def get_package_version():
    global _package_version_cache
    if _package_version_cache is not None:
        return _package_version_cache

    env_ver = os.environ.get("PACKAGE_VERSION")
    if env_ver:
        _package_version_cache = env_ver.partition(':')[0]
        return _package_version_cache

    vpath = os.path.join(os.path.dirname(__file__), "version.json")
    if os.path.exists(vpath):
        try:
            with open(vpath, "r") as f:
                data = json.load(f)
                ver = data.get("semver") or data.get("version")
                if ver:
                    _package_version_cache = ver.partition(':')[0]
                    return _package_version_cache
        except Exception:
            pass

    _package_version_cache = "0.4.0"
    return _package_version_cache

class DashboardHTTPRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        path_only = self.path.partition('?')[0].partition('#')[0]
        if path_only == "/api/status":
            # Prevent unauthorized container-to-container scraping from within the same network
            client_ip = self.client_address[0]
            if client_ip.startswith("::ffff:"):
                client_ip = client_ip[7:]
            is_local = client_ip in ("127.0.0.1", "::1", "localhost")
            
            # Identify the network gateway IP
            gateway_ip = get_default_gateway()
            
            # Resolve the StartOS host/proxy IP
            embassy_ip = None
            try:
                import socket
                embassy_ip = socket.gethostbyname("embassy")
            except Exception:
                pass
                
            # Enforce that remote connections must strictly originate from the host gateway or the embassy proxy IP
            is_trusted_proxy = (
                (gateway_ip and client_ip == gateway_ip) or
                (embassy_ip and client_ip == embassy_ip)
            )
            
            if not is_local and not is_trusted_proxy:
                self.send_error(403, "Access denied")
                return

            host_header = self.headers.get("Host", "").lower()
            if host_header.startswith('['):
                host_name = host_header.partition(']')[0] + ']'
            else:
                host_name = host_header.partition(':')[0]

            if is_local:
                is_allowed_host = host_name in ("localhost", "127.0.0.1", "[::1]")
            else:
                allowed_suffixes = (".local", ".lan", ".onion")
                is_allowed_host = any(host_name.endswith(suffix) for suffix in allowed_suffixes)
                if not is_allowed_host:
                    ip_str = host_name
                    if ip_str.startswith('[') and ip_str.endswith(']'):
                        ip_str = ip_str[1:-1]
                    import ipaddress
                    try:
                        is_allowed_host = ipaddress.ip_address(ip_str).is_private
                    except ValueError:
                        pass
            
            if not is_allowed_host:
                self.send_error(403, "Access denied")
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            
            status_data = get_status()
            target_host, target_port = get_target_details()
            
            response = {
                "version": status_data.get("version", get_package_version()),
                "enabled": status_data.get("enabled", is_enabled()),
                "configured": status_data.get("configured", False),
                "allow_ipv6": status_data.get("allow_ipv6", is_allow_ipv6()),
                "status": status_data.get("status", "stopped"),
                "vpn_connected": status_data.get("vpn_connected", False),
                "handshake": status_data.get("handshake", "none"),
                "subscription_linked": status_data.get("subscription_linked", False),
                "pubkey": status_data.get("pubkey", get_wg_pubkey()),
                "expires_at": status_data.get("expires_at", "Unknown"),
                "days_remaining": status_data.get("days_remaining"),
                "expiry_formatted": status_data.get("expiry_formatted", "Unknown"),
                "target_host": target_host,
                "target_port": target_port,
                "vpn_port": status_data.get("vpn_port", DEFAULT_VPN_PORT),
                "public_ip": status_data.get("public_ip", "Unknown"),
                "server": status_data.get("server", "Unknown"),
                "vpn_ip": status_data.get("vpn_ip", "None"),
                "internal_octet": status_data.get("internal_octet", "Unknown"),
                "last_sync": status_data.get("last_sync"),
                "egress": status_data.get("egress"),
                "egress_tunneled": status_data.get("egress_tunneled", False),
            }
            self.wfile.write(json.dumps(response).encode("utf-8"))
            return

        web_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "web"))
        target_path = path_only.lstrip("/")
        if not target_path or target_path == "":
            target_path = "index.html"
            
        safe_path = os.path.abspath(os.path.join(web_dir, target_path))
        if os.path.commonpath([web_dir, safe_path]) != web_dir:
            self.send_error(403, "Access denied")
            return

        if os.path.exists(safe_path) and os.path.isfile(safe_path):
            self.send_response(200)
            if safe_path.endswith(".html"):
                self.send_header("Content-Type", "text/html")
            elif safe_path.endswith(".css"):
                self.send_header("Content-Type", "text/css")
            elif safe_path.endswith(".js"):
                self.send_header("Content-Type", "application/javascript")
            elif safe_path.endswith(".svg"):
                self.send_header("Content-Type", "image/svg+xml")
            elif safe_path.endswith(".png"):
                self.send_header("Content-Type", "image/png")
            elif safe_path.endswith(".ico"):
                self.send_header("Content-Type", "image/x-icon")
            else:
                self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            
            with open(safe_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_error(404, "File not found")

def web_server_thread():
    try:
        server = ThreadingHTTPServer(("0.0.0.0", 80), DashboardHTTPRequestHandler)
        print("Web UI Dashboard server running on port 80...")
        server.serve_forever()
    except Exception as e:
        print(f"Failed to start web server on port 80: {e}", file=sys.stderr)

def is_enabled():
    global _enabled_cache, _enabled_cache_mtime
    try:
        if os.path.exists(APP_CONFIG_PATH):
            try:
                mtime = os.path.getmtime(APP_CONFIG_PATH)
                if mtime != _enabled_cache_mtime:
                    with open(APP_CONFIG_PATH, 'r') as f:
                        config_data = json.load(f)
                    if "enabled" in config_data:
                        val = config_data["enabled"]
                        _enabled_cache = val if val is not None else False
                    else:
                        _enabled_cache = None
                    _enabled_cache_mtime = mtime
                if _enabled_cache is not None:
                    return _enabled_cache
            except Exception:
                pass
        # Default to True if a v3 config exists (upgrade path/existing configurations)
        if os.path.exists(CONFIG_PATH):
            return True
    except Exception as e:
        print(f"Error checking enabled status: {e}", file=sys.stderr)
    return False

def is_allow_ipv6():
    try:
        if os.path.exists(APP_CONFIG_PATH):
            with open(APP_CONFIG_PATH, 'r') as f:
                config_data = json.load(f)
                return config_data.get("allow-ipv6", False)
    except Exception as e:
        print(f"Error checking allow-ipv6 status: {e}", file=sys.stderr)
    return False

def extract_vpn_port(config_content):
    try:
        # Match either "# VPNPort: 12345" or "# Port Forwarding: 12345"
        match = re.search(r'#\s*(?:VPNPort|Port Forwarding):\s*(\d+)', config_content, re.IGNORECASE)
        if match:
            return int(match.group(1))
    except (ValueError, IndexError):
        pass
    return DEFAULT_VPN_PORT

def get_target_details():
    """
    Returns (target_host, target_port) based on the target node config.
    """
    env_addr = os.environ.get("TARGET_NODE_ADDR")
    if env_addr:
        try:
            host, port = env_addr.split(":")
            return host, int(port)
        except Exception as e:
            print(f"Error parsing TARGET_NODE_ADDR '{env_addr}': {e}", file=sys.stderr)

    target = "lnd"
    try:
        if os.path.exists(APP_CONFIG_PATH):
            with open(APP_CONFIG_PATH, 'r') as f:
                config_data = json.load(f)
                target = config_data.get("target-node", "lnd")
    except Exception as e:
        print(f"Error reading target node from config: {e}", file=sys.stderr)

    # Map to StartOS service ID and default port
    if target in ("cln", "c-lightning"):
        hostname = "c-lightning.embassy"
    else:
        hostname = "lnd.embassy"
    return hostname, 9735



def get_wg_ip():
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r") as f:
                config_content = f.read()
            match = re.search(r"^\s*(?!#|;)\s*Address\s*=\s*([0-9\.]+)", config_content, re.IGNORECASE | re.MULTILINE)
            if match:
                return match.group(1)
    except Exception as e:
        print(f"Error parsing WG IP: {e}", file=sys.stderr)
    return None

def shutdown_handler(signum, frame):
    print("Received shutdown signal. Stopping TunnelSats companion services...")
    sys.exit(0)

def get_subscription_info():
    if not os.path.exists(META_FILE_PATH):
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH, 'r') as f:
                    config_content = f.read()
                parsed = parse_config_comments(config_content)
                expiry = parsed.get("expiresAt")
                if expiry and is_valid_iso_expiry(expiry):
                    expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
                    if expiry_dt.tzinfo is None:
                        expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    delta = expiry_dt - now
                    is_expired = delta.total_seconds() <= 0
                    return {
                        "linked": True,
                        "expiresAt": expiry,
                        "daysRemaining": max(0, delta.days) if not is_expired else 0,
                        "formatted": f"Active (Expires in {delta.days}d)" if not is_expired else f"Expired on {expiry_dt.strftime('%Y-%m-%d')}",
                        "isExpired": is_expired,
                        "lastSync": None,
                        "syncError": None,
                        "syncSuccess": False
                    }
            except Exception:
                pass

        return {
            "linked": False,
            "expiresAt": None,
            "daysRemaining": None,
            "formatted": "Unconfigured",
            "isExpired": False,
            "lastSync": None,
            "syncError": None,
            "syncSuccess": False
        }

    try:
        with open(META_FILE_PATH, 'r') as f:
            meta = json.load(f)
        expires_at = meta.get("expiresAt")
        last_sync = meta.get("lastSync")
        sync_error = meta.get("syncError")
        sync_success = meta.get("syncSuccess", False)

        if not expires_at:
            return {
                "linked": False,
                "expiresAt": None,
                "daysRemaining": None,
                "formatted": f"Sync failed: {sync_error}" if sync_error else "Pending subscription synchronization",
                "isExpired": False,
                "lastSync": last_sync,
                "syncError": sync_error,
                "syncSuccess": sync_success
            }
        
        expiry_dt = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if expiry_dt.tzinfo is None:
            expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        delta = expiry_dt - now
        days = delta.days
        is_expired = delta.total_seconds() <= 0
        
        if is_expired:
            formatted = f"Expired on {expiry_dt.strftime('%Y-%m-%d')}"
        elif days > 0:
            formatted = f"Active (Expires in {days}d {delta.seconds // 3600}h)"
        else:
            formatted = f"Active (Expires in {delta.seconds // 3600}h {(delta.seconds % 3600) // 60}m)"
            
        return {
            "linked": True,
            "expiresAt": expires_at,
            "daysRemaining": max(0, days) if not is_expired else 0,
            "formatted": formatted,
            "isExpired": is_expired,
            "lastSync": last_sync,
            "syncError": sync_error,
            "syncSuccess": sync_success
        }
    except Exception as e:
        return {
            "linked": False,
            "expiresAt": None,
            "daysRemaining": None,
            "formatted": f"Error: {e}",
            "isExpired": False,
            "lastSync": None,
            "syncError": str(e),
            "syncSuccess": False
        }

def get_status():
    enabled = is_enabled()
    has_config = os.path.exists(CONFIG_PATH)
    
    vpn_ip = get_wg_ip() if (enabled and has_config) else None
    internal_octet = vpn_ip.split('.')[-1] if vpn_ip else "Unknown"
    
    vpn_port = DEFAULT_VPN_PORT
    server_domain = "Unknown"
    if has_config:
        try:
            with open(CONFIG_PATH, "r") as f:
                content = f.read()
            vpn_port = extract_vpn_port(content)
            match = re.search(r"^\s*(?!#|;)\s*Endpoint\s*=\s*([^\s#:]+)", content, re.IGNORECASE | re.MULTILINE)
            if match:
                server_domain = match.group(1)
        except Exception:
            pass

    sub_info = get_subscription_info()
    
    if not enabled:
        status = "disabled"
    elif not has_config:
        status = "unconfigured"
    elif sub_info["isExpired"]:
        status = "expired"
    elif sub_info["linked"]:
        status = "running"
    elif sub_info.get("syncError"):
        status = "sync_error"
    else:
        status = "pending_sync"
        
    is_active = (enabled and has_config and sub_info["linked"] and not sub_info["isExpired"])

    egress_status = get_egress_status()
    egress_state = egress_status.get("state", "unverified")

    return {
        "status": status,
        "enabled": enabled,
        "configured": has_config,
        "gateway_mode": "host_managed",
        "subscription_active": is_active,
        "subscription_linked": sub_info["linked"],
        "expires_at": sub_info["expiresAt"] or "Unknown",
        "days_remaining": sub_info["daysRemaining"],
        "expiry_formatted": sub_info["formatted"],
        "vpn_ip": vpn_ip or "None",
        "vpn_port": vpn_port,
        "public_ip": server_domain,
        "server": server_domain,
        "internal_octet": internal_octet,
        "pubkey": get_wg_pubkey() if has_config else "None",
        "last_sync": sub_info["lastSync"],
        "sync_error": sub_info.get("syncError"),
        "version": get_package_version(),
        "allow_ipv6": is_allow_ipv6(),
        "egress": egress_status,
        "egress_tunneled": egress_state == "tunneled",
    }


# ---------------------------------------------------------------------------
# Egress health check (fail-closed detection, not enforcement)
#
# The package pivoted to a host-managed gateway: outbound tunneling is a
# manual opt-in (System > Gateways) and is fail-open by default. This change
# promotes the manual egress probe from verify.sh (curl api.ipify.org vs the
# parsed tunnel Endpoint) into a recurring health check so the gap stays
# VISIBLE: an explicit 'outbound not tunneled' warning state is surfaced in
# get_status()/api/status, the `bridge.py health egress` command, and the web
# dashboard badge. Detection only — we never mutate routing.
# ---------------------------------------------------------------------------

_egress_probe_cache = None
_egress_probe_lock = threading.Lock()  # serializes probe writers
_EGRESS_PROBE_INTERVAL = 300  # seconds between background probes

EGRESS_TUNNELED_MSG = "Outbound traffic is tunneled via TunnelSats."
EGRESS_NOT_TUNNELED_MSG = (
    "Outbound traffic is NOT tunneled via TunnelSats: the tunnel is "
    "fail-open. Verify the host-managed gateway in System > Gateways."
)
EGRESS_UNVERIFIED_MSG = (
    "Outbound egress could not be verified. Confirm the tunnel gateway is "
    "active in System > Gateways."
)


def _is_literal_ip(value):
    """True when value parses as an IPv4/IPv6 address literal (no DNS needed)."""
    if not value:
        return False
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def get_expected_tunnel_ip():
    """Parse the tunnel server host from the WireGuard Endpoint in CONFIG_PATH.

    Mirrors verify.sh: the Endpoint host (which may be a hostname like
    'ch1.tunnelsats.com' or a literal IP) is the expected egress IP. A
    hostname is resolved via DNS. Returns:
        {"raw_host": str|None, "resolved_ip": str|None, "is_hostname": bool}
    """
    if not os.path.exists(CONFIG_PATH):
        return {"raw_host": None, "resolved_ip": None, "is_hostname": False}
    try:
        with open(CONFIG_PATH, "r") as f:
            content = f.read()
        match = re.search(
            r"^\s*(?!#|;)\s*Endpoint\s*=\s*(\S+)", content,
            re.IGNORECASE | re.MULTILINE,
        )
        if not match:
            return {"raw_host": None, "resolved_ip": None, "is_hostname": False}
        raw_host = match.group(1).strip()
        # Strip an optional port suffix (':51820') and unwrap a bracketed
        # IPv6 literal ('[2001:db8::1]:51820' -> '2001:db8::1'), mirroring
        # verify.sh. The package routes IPv4 only, but a malformed capture
        # must never poison the comparison.
        if raw_host.startswith("["):
            end = raw_host.find("]")
            if end == -1:
                # Malformed bracketed endpoint ('[abc'): never slice it into a
                # garbled host, just report 'unverified'.
                return {"raw_host": None, "resolved_ip": None, "is_hostname": False}
            raw_host = raw_host[1:end]
        elif not _is_literal_ip(raw_host):
            # 'host:port' / 'ipv4:port' -> drop the port. A bare IPv6 literal
            # ('2001:db8::1') is a complete address and must NOT be
            # colon-split, or it would be truncated to '2001'.
            raw_host = raw_host.split(":", 1)[0]
        if not raw_host:
            return {"raw_host": None, "resolved_ip": None, "is_hostname": False}
        try:
            # A literal IP (v4 or v6) Endpoint needs no DNS resolution.
            ipaddress.ip_address(raw_host)
            return {
                "raw_host": raw_host,
                "resolved_ip": raw_host,
                "is_hostname": False,
            }
        except ValueError:
            pass
        try:
            # IPv4-only resolution by design (this package routes IPv4 only).
            # An IPv6-only Endpoint hostname resolves to None and is reported
            # as 'unverified' rather than guessed at.
            resolved = socket.gethostbyname(raw_host)
            return {
                "raw_host": raw_host,
                "resolved_ip": resolved,
                "is_hostname": True,
            }
        except Exception:
            return {
                "raw_host": raw_host,
                "resolved_ip": None,
                "is_hostname": True,
            }
    except Exception as e:
        print(f"Error parsing expected tunnel IP: {e}", file=sys.stderr)
        return {"raw_host": None, "resolved_ip": None, "is_hostname": False}


def probe_egress_ip():
    """Fetch the outbound public IPv4 via curl api.ipify.org (like verify.sh).

    Honest live probe — never mocked in production. Returns the egress IPv4
    string, or None if it cannot be determined (curl failure, or a body that
    is not an IPv4 literal).
    """
    try:
        proc = subprocess.run(
            # -4: api.ipify.org is dual-stack but this package routes IPv4
            # only, so the probe must test the IPv4 egress path. Without it a
            # dual-stack host can get an IPv6 answer that inet_aton rejects
            # and be misreported as 'unverified'.
            # --fail: an HTTP 4xx/5xx (or captive-portal) body that happens to
            # be an IPv4 literal must not be mistaken for the egress IP.
            ["curl", "-4", "-s", "--fail", "--connect-timeout", "5",
             "--max-time", "10", "https://api.ipify.org"],
            capture_output=True,
            text=True,
            timeout=12,
        )
        if proc.returncode == 0:
            candidate = proc.stdout.strip()
            try:
                socket.inet_aton(candidate)
                return candidate
            except OSError:
                return None
        return None
    except Exception:
        return None


def compute_egress_state(actual_ip, expected_raw, expected_resolved):
    """Classify outbound egress: 'tunneled' | 'not_tunneled' | 'unverified'.

    'unverified' is the honest indeterminate answer and is treated as FAILURE
    by the health command (fail-closed): we report 'tunneled' only on a
    confirmed match, and 'not_tunneled' only when the expected egress IP is
    actually known.
    """
    if not actual_ip:
        return "unverified"
    if expected_raw and actual_ip == expected_raw:
        return "tunneled"
    if expected_resolved and actual_ip == expected_resolved:
        return "tunneled"
    if not expected_raw and not expected_resolved:
        return "unverified"
    if expected_resolved is None and not _is_literal_ip(expected_raw):
        # Endpoint was a hostname that could not be resolved: the tunnel's
        # egress IP is unknown, so a mismatch proves nothing. Never raise a
        # 'not tunneled' alarm we cannot substantiate.
        return "unverified"
    return "not_tunneled"


def run_egress_probe():
    """Perform a fresh egress probe and store the result in the cache.

    Writers are serialized by _egress_probe_lock so a forced (health command)
    probe and the background loop cannot race on the shared global. Readers
    deliberately take no lock — a plain reference read cannot tear — so a
    status read is never delayed by an in-flight probe.
    """
    global _egress_probe_cache
    with _egress_probe_lock:
        expected = get_expected_tunnel_ip()
        actual = probe_egress_ip()
        state = compute_egress_state(actual, expected["raw_host"], expected["resolved_ip"])

        if state == "tunneled":
            message = EGRESS_TUNNELED_MSG
        elif state == "not_tunneled":
            message = EGRESS_NOT_TUNNELED_MSG
        else:
            message = EGRESS_UNVERIFIED_MSG

        _egress_probe_cache = {
            "state": state,
            "actual_ip": actual,
            "expected_ip": expected["raw_host"],
            "is_hostname": expected["is_hostname"],
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "message": message,
        }
        return _egress_probe_cache


def _pending_egress_status():
    """Placeholder while the first background probe is still in flight."""
    return {
        "state": "unverified",
        "actual_ip": None,
        "expected_ip": None,
        "is_hostname": False,
        "checked_at": None,
        "pending": True,
        "message": EGRESS_UNVERIFIED_MSG,
    }


def get_egress_status(force=False):
    """Return the egress status.

    Non-forced reads — the /api/status and dashboard path — are guaranteed
    NON-BLOCKING: they return the cached probe result, or a 'pending'
    'unverified' placeholder while the background egress_probe_loop is still
    working on the first probe. Only force=True (the `health egress` command)
    runs a live probe synchronously.
    """
    if force:
        return run_egress_probe()
    cache = _egress_probe_cache  # single atomic reference read
    if cache is None:
        return _pending_egress_status()
    return cache


def egress_probe_loop():
    """Background daemon: periodically refresh the egress probe.

    Runs one probe immediately on startup (so /api/status and the health
    command have a result from t=0 rather than waiting a full interval),
    then refreshes every _EGRESS_PROBE_INTERVAL seconds.
    """
    while True:
        try:
            run_egress_probe()
        except Exception as e:
            print(f"Error in egress probe loop: {e}", file=sys.stderr)
        try:
            time.sleep(_EGRESS_PROBE_INTERVAL)
        except KeyboardInterrupt:
            break

def validate_config(wg_conf):
    if not wg_conf:
        return
    if not re.search(r'^\s*(?!#|;)\s*PrivateKey\s*=', wg_conf, re.IGNORECASE | re.MULTILINE):
        raise ValueError("Missing 'PrivateKey' property.")
    if not re.search(r'^\s*(?!#|;)\s*Address\s*=', wg_conf, re.IGNORECASE | re.MULTILINE):
        raise ValueError("Missing 'Address' property.")
    if not re.search(r'^\s*(?!#|;)\s*Endpoint\s*=', wg_conf, re.IGNORECASE | re.MULTILINE):
        raise ValueError("Missing 'Endpoint' routing property.")
    if not re.search(r'#\s*(?:VPNPort|Port Forwarding):\s*\d+', wg_conf, re.IGNORECASE):
        raise ValueError("Missing port-forwarding metadata (e.g., # Port Forwarding: XXXXX).")

def main():
    if len(sys.argv) < 2:
        print("Usage: bridge.py <command> [args]")
        sys.exit(1)
    
    command = sys.argv[1]
    
    if command == "start":
        signal.signal(signal.SIGTERM, shutdown_handler)
        signal.signal(signal.SIGINT, shutdown_handler)
        try:
            sync_thread = threading.Thread(target=subscription_sync_loop, daemon=True)
            sync_thread.start()

            egress_thread = threading.Thread(target=egress_probe_loop, daemon=True)
            egress_thread.start()

            web_thread = threading.Thread(target=web_server_thread, daemon=True)
            web_thread.start()

            print("TunnelSats companion service started.")

            while True:
                time.sleep(1)
        except Exception as e:
            stderr = getattr(e, 'stderr', str(e))
            print(f"Failed to start companion service: {stderr}", file=sys.stderr)
            sys.exit(1)
            
    elif command == "stop":
        print("TunnelSats companion service stopped.")
        sys.exit(0)
            
    elif command == "status":
        print(json.dumps(get_status(), indent=2))
        
    elif command == "health":
        target = sys.argv[2] if len(sys.argv) > 2 else "subscription"

        if target == "egress":
            if not is_enabled():
                print(json.dumps({"result": "disabled", "message": "TunnelSats is disabled."}))
                sys.exit(0)
            egress = get_egress_status(force=True)
            state = egress.get("state", "unverified")
            if state == "tunneled":
                print(json.dumps({"result": "ok", "message": egress.get("message", EGRESS_TUNNELED_MSG)}))
                sys.exit(0)
            print(json.dumps({"result": "failure", "message": egress.get("message", EGRESS_UNVERIFIED_MSG)}))
            sys.exit(1)

        if not is_enabled():
            print(json.dumps({"result": "disabled", "message": "TunnelSats is disabled."}))
            sys.exit(0)
            
        if not os.path.exists(CONFIG_PATH):
            print(json.dumps({"result": "ok", "message": "Unconfigured: Add WireGuard configuration in settings"}))
            sys.exit(0)

        sub_info = get_subscription_info()
        if sub_info["isExpired"]:
            print(json.dumps({"result": "failure", "message": f"Subscription expired on {sub_info['expiresAt']}"}))
            sys.exit(1)
        elif sub_info.get("syncError"):
            print(json.dumps({"result": "failure", "message": f"Subscription synchronization failed: {sub_info['syncError']}"}))
            sys.exit(1)
        elif sub_info["linked"]:
            print(json.dumps({"result": "ok", "message": sub_info["formatted"]}))
            sys.exit(0)
        else:
            pubkey = get_wg_pubkey()
            if pubkey and pubkey != "Unknown":
                try:
                    lazy_sync(pubkey)
                    sub_info = get_subscription_info()
                    if sub_info["linked"]:
                        print(json.dumps({"result": "ok", "message": sub_info["formatted"]}))
                        sys.exit(0)
                    elif sub_info.get("syncError"):
                        print(json.dumps({"result": "failure", "message": f"Subscription synchronization failed: {sub_info['syncError']}"}))
                        sys.exit(1)
                except Exception as e:
                    print(json.dumps({"result": "failure", "message": f"Subscription synchronization failed: {e}"}))
                    sys.exit(1)

            print(json.dumps({"result": "failure", "message": "Subscription unverified: Unable to determine validity"}))
            sys.exit(1)



    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

if __name__ == "__main__":
    main()
