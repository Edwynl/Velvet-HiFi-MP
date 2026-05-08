"""
VELVET UPnP/DLNA Media Server
Enables Marantz Model M1 (HEOS), Cambridge Audio EXN100 (StreamMagic),
and any UPnP/DLNA renderer to browse and play from VELVET.

Implements:
  • SSDP – device advertisement & discovery (UDP multicast 239.255.255.250:1900)
  • UPnP Device Description XML
  • ContentDirectory:1 service (Browse/Search)
  • ConnectionManager:1 service
  • HTTP media streaming (delegates to VELVET server.py)
"""

import asyncio
import os
import socket
import struct
import threading
import time
import sqlite3
import xml.etree.ElementTree as ET
import urllib.request
import re
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, quote
import logging

import env_loader  # noqa: F401 - load .env for direct module use

log = logging.getLogger("velvet.upnp")

# ─── Config ────────────────────────────────────────────────────────────────────

SSDP_ADDR   = "239.255.255.250"
SSDP_PORT   = 1900
UPNP_PORT   = 8766           # Separate port for UPnP XML/control
MAIN_PORT   = 8765           # MusicIQ streaming server port

# Configurable device identification (can be set via environment variables)
DEVICE_UUID = os.environ.get("VELVET_UPNP_DEVICE_UUID") or os.environ.get("UPNP_DEVICE_UUID", "uuid:velvet-00000000-0000-0000-0000-000000000001")
DEVICE_NAME = os.environ.get("VELVET_UPNP_FRIENDLY_NAME") or os.environ.get("UPNP_DEVICE_NAME", "VELVET")
DB_PATH     = Path(os.environ.get("VELVET_DATA_DIR") or os.environ.get("DATA_DIR", "velvet_data")) / "library.db"

def get_all_local_ips():
    ips = []
    try:
        # Method 1: Hostname lookup
        addr_info = socket.getaddrinfo(socket.gethostname(), None)
        for item in addr_info:
            ip = item[4][0]
            if "." in ip and not ip.startswith("127."):
                if ip not in ips: ips.append(ip)
    except: pass
    
    try:
        # Method 2: Hostname EX lookup (often better on Windows)
        _, _, addresses = socket.gethostbyname_ex(socket.gethostname())
        for ip in addresses:
            if ip not in ips: ips.append(ip)
    except: pass
    
    # Method 3: UDP connect method (Reliable for determining outbound interface)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        primary = s.getsockname()[0]
        if primary not in ips: ips.insert(0, primary)
        s.close()
    except: pass
        
    return list(set(ips)) or ["127.0.0.1"]

def get_local_ip():
    ips = get_all_local_ips()
    # Try to pick the most likely "real" IP (192.168.x.x or 10.x.x.x)
    for ip in ips:
        if ip.startswith("192.168.") or ip.startswith("10."):
            return ip
    return ips[0] if ips else "127.0.0.1"

LOCAL_IP = get_local_ip()
BASE_URL = f"http://{LOCAL_IP}:{UPNP_PORT}"

# ─── DLNA Discovery & Casting (Control Point) ──────────────────────────────

discovered_devices = {} # { udn: { name, location, control_url, ... } }
_discovery_lock = threading.Lock()


def get_cached_renderers():
    """Return the most recently discovered renderers without blocking on a scan."""
    return list(discovered_devices.values())


def refresh_renderers_async(timeout=3.0):
    """Kick off a background renderer refresh if one is not already running."""
    if _discovery_lock.locked():
        return False

    def _runner():
        refresh_renderers(timeout=timeout)

    threading.Thread(target=_runner, daemon=True).start()
    return True


def refresh_renderers(timeout=3.0):
    """Scan local network for DLNA MediaRenderers on all interfaces."""
    with _discovery_lock:
        devices = {}
        local_ips = get_all_local_ips()

        # Try two search targets: specific renderer and all devices
        search_targets = [
            "urn:schemas-upnp-org:device:MediaRenderer:1",
            "ssdp:all"
        ]

        for source_ip in local_ips:
            for st in search_targets:
                msg = (
                    "M-SEARCH * HTTP/1.1\r\n"
                    f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
                    'MAN: "ssdp:discover"\r\n'
                    "MX: 2\r\n"
                    f"ST: {st}\r\n"
                    "\r\n"
                ).encode()

                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                sock.settimeout(1.5) # Shorter timeout per interface/target
                try:
                    # Bind to specific interface IP
                    sock.bind((source_ip, 0))
                    sock.sendto(msg, (SSDP_ADDR, SSDP_PORT))

                    start_time = time.time()
                    while time.time() - start_time < 0.8:
                        try:
                            data, addr = sock.recvfrom(4096)
                            text = data.decode("utf-8", errors="replace")

                            # We only care about MediaRenderer responses
                            is_renderer = "MediaRenderer" in text or "AVTransport" in text
                            if not is_renderer and st == "ssdp:all":
                                # If we searched for all, we still only want to keep renderers
                                # This filters out the huge amount of other UPnP traffic
                                continue

                            m = re.search(r"LOCATION: (.*?)\r\n", text, re.I)
                            if m:
                                loc = m.group(1).strip()
                                desc = _fetch_device_info(loc)
                                if desc:
                                    devices[desc["udn"]] = desc
                        except socket.timeout:
                            break
                except Exception as e:
                    log.debug(f"Discovery error on {source_ip}: {e}")
                finally:
                    sock.close()

        global discovered_devices
        discovered_devices = devices
        return list(devices.values())


def discover_renderers(timeout=3.0):
    """Backward-compatible alias for an explicit renderer refresh."""
    return refresh_renderers(timeout=timeout)

def _fetch_device_info(url):
    """Download device description XML and extract control URLs."""
    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:
            data = resp.read().decode("utf-8")
        
        root = ET.fromstring(data)
        ns = {"u": "urn:schemas-upnp-org:device-1-0"}
        
        device = root.find(".//u:device", ns)
        if device is None: return None
        
        name = device.findtext("u:friendlyName", namespaces=ns)
        udn  = device.findtext("u:UDN", namespaces=ns)
        
        # Find AVTransport service
        avt = root.find(".//u:service[u:serviceType='urn:schemas-upnp-org:service:AVTransport:1']", ns)
        if avt is None: return None
        
        ctrl_url = avt.findtext("u:controlURL", namespaces=ns)
        # Handle relative URLs
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if not ctrl_url.startswith("http"):
            ctrl_url = base + ("/" if not ctrl_url.startswith("/") else "") + ctrl_url
            
        return {
            "name": name,
            "udn": udn,
            "location": url,
            "control_url": ctrl_url,
            "last_seen": time.time()
        }
    except Exception as e:
        log.debug(f"Failed to fetch device info from {url}: {e}")
        return None

def soap_request(url, service, action, args_xml):
    """Send a SOAP control command to a DLNA player."""
    body = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body>'
        f'<u:{action} xmlns:u="{service}">'
        '<InstanceID>0</InstanceID>'
        + args_xml +
        f'</u:{action}>'
        '</s:Body></s:Envelope>'
    )
    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPACTION": f'"{service}#{action}"',
        "User-Agent": "VELVET/1.0 DLNA/1.0",
    }
    try:
        req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            response = resp.read()
            log.info(f"SOAP {action} response: {response[:500] if response else 'empty'}")
            return response
    except urllib.error.HTTPError as e:
        log.error(f"SOAP HTTP Error ({action}): {e.code} {e.reason}")
        try:
            error_body = e.read().decode('utf-8', errors='ignore')
            log.error(f"SOAP Error body: {error_body[:500]}")
        except:
            pass
        return None
    except Exception as e:
        log.error(f"SOAP Error ({action}): {e}")
        return None

def cast_track(device_udn, track_url, meta_xml=""):
    """Set URI and Play on a remote DLNA renderer."""
    dev = discovered_devices.get(device_udn)
    if not dev:
        discover_renderers(timeout=1.5)
        dev = discovered_devices.get(device_udn)
        if not dev: return False

    ctrl_url = dev["control_url"]
    svc = "urn:schemas-upnp-org:service:AVTransport:1"

    log.info(f"[CAST] Device: {dev.get('name', device_udn)}")
    log.info(f"[CAST] Control URL: {ctrl_url}")
    log.info(f"[CAST] Stream URL: {track_url}")

    import time

    # Prefer full metadata first so album art and track details show up on capable renderers.
    args_full = f'<CurrentURI>{_esc(track_url)}</CurrentURI><CurrentURIMetaData>{_esc(meta_xml)}</CurrentURIMetaData>'
    log.info(f"[CAST] Sending SetAVTransportURI (full metadata)...")
    result = soap_request(ctrl_url, svc, "SetAVTransportURI", args_full)

    if result is None:
        log.info(f"[CAST] First attempt failed, trying Stop then retry...")
        time.sleep(1)
        soap_request(ctrl_url, svc, "Stop", "")
        time.sleep(2)

        log.info(f"[CAST] Retrying SetAVTransportURI with full metadata...")
        result = soap_request(ctrl_url, svc, "SetAVTransportURI", args_full)

    if result is None:
        # Try with empty DIDL
        minimal_didl = '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/"></DIDL-Lite>'
        args_minimal = f'<CurrentURI>{_esc(track_url)}</CurrentURI><CurrentURIMetaData>{_esc(minimal_didl)}</CurrentURIMetaData>'
        log.info(f"[CAST] Retrying with empty DIDL-Lite...")
        result = soap_request(ctrl_url, svc, "SetAVTransportURI", args_minimal)

    if result is None:
        # Last try with no metadata for strict renderers that reject DIDL entirely.
        args = f'<CurrentURI>{_esc(track_url)}</CurrentURI><CurrentURIMetaData></CurrentURIMetaData>'
        log.info(f"[CAST] Retrying SetAVTransportURI (no metadata fallback)...")
        result = soap_request(ctrl_url, svc, "SetAVTransportURI", args)

    if result is None:
        log.error(f"[CAST] All SetAVTransportURI attempts failed")
        return False

    log.info(f"[CAST] SetAVTransportURI succeeded")
    time.sleep(2)

    # Try Play
    log.info(f"[CAST] Sending Play...")
    soap_request(ctrl_url, svc, "Play", '<Speed>1</Speed>')

    log.info(f"[CAST] Cast sequence completed")
    return True

def cast_control(device_udn, action):
    """Send Play, Pause, or Stop to device."""
    dev = discovered_devices.get(device_udn)
    if not dev: return False
    
    svc = "urn:schemas-upnp-org:service:AVTransport:1"
    args = ""
    if action == "Play": args = '<Speed>1</Speed>'
    
    res = soap_request(dev["control_url"], svc, action, args)
    return res is not None

def get_transport_info(device_udn):
    """Get current state (PLAYING, PAUSED_PLAYBACK, STOPPED, etc)."""
    dev = discovered_devices.get(device_udn)
    if not dev: return "STOPPED"
    
    svc = "urn:schemas-upnp-org:service:AVTransport:1"
    res = soap_request(dev["control_url"], svc, "GetTransportInfo", "")
    if res:
        try:
            body = res.decode("utf-8")
            return _parse_soap_arg(body, "CurrentTransportState")
        except: pass
    return "UNKNOWN"

def get_position_info(device_udn):
    """Get current position and duration. Returns (rel_time_sec, duration_sec)."""
    dev = discovered_devices.get(device_udn)
    if not dev: return 0, 0
    
    svc = "urn:schemas-upnp-org:service:AVTransport:1"
    res = soap_request(dev["control_url"], svc, "GetPositionInfo", "")
    if res:
        try:
            body = res.decode("utf-8")
            rel_time = _parse_soap_arg(body, "RelTime")
            duration = _parse_soap_arg(body, "TrackDuration")
            return _parse_time(rel_time), _parse_time(duration)
        except: pass
    return 0, 0

def _parse_time(t_str):
    """Parse UPnP time string H:MM:SS or H:MM:SS.mmm to seconds."""
    if not t_str or ":" not in t_str: return 0
    try:
        parts = t_str.split(":")
        if len(parts) == 3:
            h, m, s = parts
            return int(h) * 3600 + int(m) * 60 + float(s)
    except: pass
    return 0

# ─── Database helpers ─────────────────────────────────────────────────────────

def db_query(sql, params=()):
    try:
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning(f"DB error: {e}")
        return []

# ─── DIDL-Lite builder ────────────────────────────────────────────────────────

DIDL_NS = {
    "DIDL-Lite": "urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/",
    "dc":        "http://purl.org/dc/elements/1.1/",
    "upnp":      "urn:schemas-upnp-org:metadata-1-0/upnp/",
}

def _didl_wrap(items_xml: str) -> str:
    return (
        '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:upnp="urn:schemas-upnp-org:metadata-1-0/upnp/">'
        + items_xml +
        '</DIDL-Lite>'
    )


def _get_dlna_profile(mime: str, fmt: str) -> str:
    """
    Get the correct DLNA.ORG_PN profile for a given MIME type and format.
    Returns empty string if no specific profile needed.
    """
    # Map format to DLNA profile
    dlna_profiles = {
        "FLAC": "FLAC",
        "MP3": "MP3",
        "M4A": "M4A", "AAC": "M4A",
        "OGG": "OGG",
        "OPUS": "OPUS",
        "WAV": "LPCM",
        "AIFF": "LPCM",
        "DSD": "DSD", "DSF": "DSD", "DFF": "DSD",
        "WV": "WV",
        "APE": "APE",
    }
    profile = dlna_profiles.get(fmt.upper(), "")
    if profile:
        return f"DLNA.ORG_PN={profile};DLNA.ORG_OP=01;DLNA.ORG_FLAGS=01700000000000000000000000000000"
    # Fallback: generic DLNA flags without specific profile
    return "DLNA.ORG_OP=01;DLNA.ORG_FLAGS=01700000000000000000000000000000"

def _esc(s):
    if not s:
        return ""
    return (str(s)
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))

def container_xml(id_, parent_id, title, child_count):
    return (
        f'<container id="{id_}" parentID="{parent_id}" childCount="{child_count}" '
        f'restricted="1" searchable="1">'
        f'<dc:title>{_esc(title)}</dc:title>'
        f'<upnp:class>object.container.storageFolder</upnp:class>'
        f'</container>'
    )

def track_xml(track, parent_id):
    tid   = track["id"]
    title = _esc(track.get("title") or "Unknown")
    artist= _esc(track.get("artist_name") or "")
    album = _esc(track.get("album_title") or "")
    dur   = _format_duration(track.get("duration"))
    fmt   = (track.get("format") or "FLAC").upper()
    sr    = track.get("sample_rate") or 44100
    bd    = track.get("bit_depth") or 16
    ch    = track.get("channels") or 2
    size  = track.get("file_size") or 0
    album_id = track.get("album_id") or 0

    # MIME type
    mime_map = {
        "FLAC": "audio/flac", "MP3": "audio/mpeg",
        "M4A":  "audio/mp4",  "AAC": "audio/aac",
        "OGG":  "audio/ogg",  "OPUS": "audio/opus",
        "WAV":  "audio/wav",  "AIFF": "audio/aiff",
        "DSD":  "audio/x-dsf", "DSF": "audio/x-dsf",
        "DFF":  "audio/x-dff", "WV":  "audio/x-wavpack",
        "APE":  "audio/x-ape",
    }
    mime = mime_map.get(fmt, "audio/flac")
    stream_url = f"http://{LOCAL_IP}:{MAIN_PORT}/api/stream/{tid}"
    cover_url  = f"http://{LOCAL_IP}:{MAIN_PORT}/api/covers/{album_id}/thumb?size=medium" if album_id else ""

    # Use correct DLNA profile for the format
    dlna_profile = _get_dlna_profile(mime, fmt)
    res_attrs = f'protocolInfo="http-get:*:{mime}:{dlna_profile}"'
    if size:
        res_attrs += f' size="{size}"'
    if dur:
        res_attrs += f' duration="{dur}"'
    # Only add audio attributes for formats that support them
    if fmt.upper() in ("FLAC", "WAV", "AIFF", "DSD", "DSF", "DFF"):
        res_attrs += f' sampleFrequency="{sr}" bitsPerSample="{bd}" nrAudioChannels="{ch}"'

    art_xml = f'<upnp:albumArtURI>{_esc(cover_url)}</upnp:albumArtURI>' if cover_url else ""

    return (
        f'<item id="t{tid}" parentID="{parent_id}" restricted="1">'
        f'<dc:title>{title}</dc:title>'
        f'<dc:creator>{artist}</dc:creator>'
        f'<upnp:artist>{artist}</upnp:artist>'
        f'<upnp:album>{album}</upnp:album>'
        f'<upnp:class>object.item.audioItem.musicTrack</upnp:class>'
        f'{art_xml}'
        f'<upnp:trackNumber>{track.get("track_number") or 0}</upnp:trackNumber>'
        f'<res {res_attrs}>{_esc(stream_url)}</res>'
        f'</item>'
    )

def _format_duration(seconds):
    if not seconds:
        return None
    s = int(seconds)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h}:{m:02d}:{sec:02d}.{ms:03d}"

# ─── ContentDirectory Browse logic ───────────────────────────────────────────

# Container ID scheme:
#   "0"           → root
#   "artists"     → All Artists container
#   "albums"      → All Albums container
#   "tracks"      → All Tracks container
#   "ar_{id}"     → Artist albums container
#   "al_{id}"     → Album tracks container

def browse(object_id, browse_flag, start, count):
    """Returns (result_xml, num_returned, total_matches)"""

    if browse_flag == "BrowseMetadata":
        return _browse_metadata(object_id)
    else:
        return _browse_children(object_id, start, count)

def _browse_metadata(oid):
    if oid == "0":
        xml = container_xml("0", "-1", DEVICE_NAME, 3)
        return _didl_wrap(xml), 1, 1
    if oid == "artists":
        n = len(db_query("SELECT id FROM artists"))
        xml = container_xml("artists", "0", "Artists", n)
        return _didl_wrap(xml), 1, 1
    if oid == "albums":
        n = len(db_query("SELECT id FROM albums"))
        xml = container_xml("albums", "0", "Albums", n)
        return _didl_wrap(xml), 1, 1
    if oid == "tracks":
        n = len(db_query("SELECT id FROM tracks"))
        xml = container_xml("tracks", "0", "All Tracks", n)
        return _didl_wrap(xml), 1, 1
    if oid.startswith("ar_"):
        aid = oid[3:]
        rows = db_query("SELECT name FROM artists WHERE id=?", (aid,))
        name = rows[0]["name"] if rows else "Artist"
        n = len(db_query("SELECT id FROM albums WHERE artist_id=?", (aid,)))
        xml = container_xml(oid, "artists", name, n)
        return _didl_wrap(xml), 1, 1
    if oid.startswith("al_"):
        alid = oid[3:]
        rows = db_query(
            "SELECT al.title, ar.name as artist_name FROM albums al "
            "JOIN artists ar ON al.artist_id=ar.id WHERE al.id=?", (alid,)
        )
        name = f"{rows[0]['artist_name']} – {rows[0]['title']}" if rows else "Album"
        n = len(db_query("SELECT id FROM tracks WHERE album_id=?", (alid,)))
        xml = container_xml(oid, f"ar_x", name, n)
        return _didl_wrap(xml), 1, 1
    if oid.startswith("t"):
        tid = oid[1:]
        rows = db_query(
            "SELECT t.*, ar.name as artist_name, al.title as album_title "
            "FROM tracks t JOIN artists ar ON t.artist_id=ar.id "
            "JOIN albums al ON t.album_id=al.id WHERE t.id=?", (tid,)
        )
        if rows:
            xml = track_xml(rows[0], "tracks")
            return _didl_wrap(xml), 1, 1
    return _didl_wrap(""), 0, 0

def _browse_children(oid, start, count):
    limit  = count if count > 0 else 9999
    offset = start

    if oid == "0":
        containers = [
            container_xml("artists", "0", "Artists",
                          len(db_query("SELECT id FROM artists"))),
            container_xml("albums",  "0", "Albums",
                          len(db_query("SELECT id FROM albums"))),
            container_xml("tracks",  "0", "All Tracks",
                          len(db_query("SELECT id FROM tracks"))),
        ]
        sliced = containers[offset:offset + limit]
        return _didl_wrap("".join(sliced)), len(sliced), 3

    if oid == "artists":
        rows = db_query(
            "SELECT ar.id, ar.name, COUNT(al.id) as album_count "
            "FROM artists ar LEFT JOIN albums al ON al.artist_id=ar.id "
            "GROUP BY ar.id ORDER BY ar.sort_name "
            f"LIMIT {limit} OFFSET {offset}"
        )
        total = db_query("SELECT COUNT(*) as c FROM artists")[0]["c"]
        xmls  = [container_xml(f"ar_{r['id']}", "artists", r["name"],
                               r["album_count"]) for r in rows]
        return _didl_wrap("".join(xmls)), len(xmls), total

    if oid == "albums":
        rows = db_query(
            "SELECT al.id, al.title, ar.name as artist_name, COUNT(t.id) as tc "
            "FROM albums al JOIN artists ar ON al.artist_id=ar.id "
            "LEFT JOIN tracks t ON t.album_id=al.id "
            "GROUP BY al.id ORDER BY ar.sort_name, al.year "
            f"LIMIT {limit} OFFSET {offset}"
        )
        total = db_query("SELECT COUNT(*) as c FROM albums")[0]["c"]
        xmls  = [container_xml(f"al_{r['id']}", "albums",
                               f"{r['artist_name']} – {r['title']}", r["tc"]) for r in rows]
        return _didl_wrap("".join(xmls)), len(xmls), total

    if oid == "tracks":
        rows = db_query(
            "SELECT t.*, ar.name as artist_name, al.title as album_title "
            "FROM tracks t JOIN artists ar ON t.artist_id=ar.id "
            "JOIN albums al ON t.album_id=al.id ORDER BY ar.sort_name, al.title, t.track_number "
            f"LIMIT {limit} OFFSET {offset}"
        )
        total = db_query("SELECT COUNT(*) as c FROM tracks")[0]["c"]
        xmls  = [track_xml(r, "tracks") for r in rows]
        return _didl_wrap("".join(xmls)), len(xmls), total

    if oid.startswith("ar_"):
        aid = oid[3:]
        rows = db_query(
            "SELECT al.id, al.title, COUNT(t.id) as tc "
            "FROM albums al LEFT JOIN tracks t ON t.album_id=al.id "
            "WHERE al.artist_id=? GROUP BY al.id ORDER BY al.year "
            f"LIMIT {limit} OFFSET {offset}", (aid,)
        )
        total = db_query("SELECT COUNT(*) as c FROM albums WHERE artist_id=?", (aid,))[0]["c"]
        artist_rows = db_query("SELECT name FROM artists WHERE id=?", (aid,))
        aname = artist_rows[0]["name"] if artist_rows else ""
        xmls  = [container_xml(f"al_{r['id']}", oid,
                               f"{aname} – {r['title']}", r["tc"]) for r in rows]
        return _didl_wrap("".join(xmls)), len(xmls), total

    if oid.startswith("al_"):
        alid = oid[3:]
        rows = db_query(
            "SELECT t.*, ar.name as artist_name, al.title as album_title "
            "FROM tracks t JOIN artists ar ON t.artist_id=ar.id "
            "JOIN albums al ON t.album_id=al.id "
            "WHERE t.album_id=? ORDER BY t.disc_number, t.track_number "
            f"LIMIT {limit} OFFSET {offset}", (alid,)
        )
        total = db_query("SELECT COUNT(*) as c FROM tracks WHERE album_id=?", (alid,))[0]["c"]
        xmls  = [track_xml(r, oid) for r in rows]
        return _didl_wrap("".join(xmls)), len(xmls), total

    return _didl_wrap(""), 0, 0

# ─── UPnP HTTP Handler ────────────────────────────────────────────────────────

DEVICE_DESC = f"""<?xml version="1.0"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:MediaServer:1</deviceType>
    <friendlyName>{DEVICE_NAME}</friendlyName>
    <manufacturer>VELVET</manufacturer>
    <modelName>VELVET Personal Server</modelName>
    <modelNumber>1.0</modelNumber>
    <UDN>{DEVICE_UUID}</UDN>
    <serviceList>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ContentDirectory:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:ContentDirectory</serviceId>
        <SCPDURL>/upnp/ContentDirectory.xml</SCPDURL>
        <controlURL>/upnp/control/ContentDirectory</controlURL>
        <eventSubURL>/upnp/event/ContentDirectory</eventSubURL>
      </service>
      <service>
        <serviceType>urn:schemas-upnp-org:service:ConnectionManager:1</serviceType>
        <serviceId>urn:upnp-org:serviceId:ConnectionManager</serviceId>
        <SCPDURL>/upnp/ConnectionManager.xml</SCPDURL>
        <controlURL>/upnp/control/ConnectionManager</controlURL>
        <eventSubURL>/upnp/event/ConnectionManager</eventSubURL>
      </service>
    </serviceList>
  </device>
</root>"""

CONTENT_DIR_SCPD = """<?xml version="1.0"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <actionList>
    <action><name>Browse</name>
      <argumentList>
        <argument><name>ObjectID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ObjectID</relatedStateVariable></argument>
        <argument><name>BrowseFlag</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_BrowseFlag</relatedStateVariable></argument>
        <argument><name>Filter</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Filter</relatedStateVariable></argument>
        <argument><name>StartingIndex</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Index</relatedStateVariable></argument>
        <argument><name>RequestedCount</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>SortCriteria</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_SortCriteria</relatedStateVariable></argument>
        <argument><name>Result</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Result</relatedStateVariable></argument>
        <argument><name>NumberReturned</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>TotalMatches</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Count</relatedStateVariable></argument>
        <argument><name>UpdateID</name><direction>out</direction><relatedStateVariable>SystemUpdateID</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action><name>GetSystemUpdateID</name>
      <argumentList>
        <argument><name>Id</name><direction>out</direction><relatedStateVariable>SystemUpdateID</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action><name>GetSortCapabilities</name>
      <argumentList>
        <argument><name>SortCaps</name><direction>out</direction><relatedStateVariable>SortCapabilities</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action><name>GetSearchCapabilities</name>
      <argumentList>
        <argument><name>SearchCaps</name><direction>out</direction><relatedStateVariable>SearchCapabilities</relatedStateVariable></argument>
      </argumentList>
    </action>
  </actionList>
  <serviceStateTable>
    <stateVariable sendEvents="yes"><name>SystemUpdateID</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SortCapabilities</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>SearchCapabilities</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ObjectID</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_BrowseFlag</name><dataType>string</dataType><allowedValueList><allowedValue>BrowseMetadata</allowedValue><allowedValue>BrowseDirectChildren</allowedValue></allowedValueList></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Filter</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Index</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Count</name><dataType>ui4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_SortCriteria</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Result</name><dataType>string</dataType></stateVariable>
  </serviceStateTable>
</scpd>"""

CONN_MGR_SCPD = """<?xml version="1.0"?>
<scpd xmlns="urn:schemas-upnp-org:service-1-0">
  <specVersion><major>1</major><minor>0</minor></specVersion>
  <actionList>
    <action><name>GetProtocolInfo</name>
      <argumentList>
        <argument><name>Source</name><direction>out</direction><relatedStateVariable>SourceProtocolInfo</relatedStateVariable></argument>
        <argument><name>Sink</name><direction>out</direction><relatedStateVariable>SinkProtocolInfo</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action><name>GetCurrentConnectionIDs</name>
      <argumentList>
        <argument><name>ConnectionIDs</name><direction>out</direction><relatedStateVariable>CurrentConnectionIDs</relatedStateVariable></argument>
      </argumentList>
    </action>
    <action><name>GetCurrentConnectionInfo</name>
      <argumentList>
        <argument><name>ConnectionID</name><direction>in</direction><relatedStateVariable>A_ARG_TYPE_ConnectionID</relatedStateVariable></argument>
        <argument><name>RcsID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_RcsID</relatedStateVariable></argument>
        <argument><name>AVTransportID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_AVTransportID</relatedStateVariable></argument>
        <argument><name>ProtocolInfo</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ProtocolInfo</relatedStateVariable></argument>
        <argument><name>PeerConnectionManager</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ConnectionManager</relatedStateVariable></argument>
        <argument><name>PeerConnectionID</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ConnectionID</relatedStateVariable></argument>
        <argument><name>Direction</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_Direction</relatedStateVariable></argument>
        <argument><name>Status</name><direction>out</direction><relatedStateVariable>A_ARG_TYPE_ConnectionStatus</relatedStateVariable></argument>
      </argumentList>
    </action>
  </actionList>
  <serviceStateTable>
    <stateVariable sendEvents="yes"><name>SourceProtocolInfo</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="yes"><name>SinkProtocolInfo</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="yes"><name>CurrentConnectionIDs</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionID</name><dataType>i4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_RcsID</name><dataType>i4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_AVTransportID</name><dataType>i4</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ProtocolInfo</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionManager</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_Direction</name><dataType>string</dataType></stateVariable>
    <stateVariable sendEvents="no"><name>A_ARG_TYPE_ConnectionStatus</name><dataType>string</dataType></stateVariable>
  </serviceStateTable>
</scpd>"""

PROTOCOL_INFO = (
    "http-get:*:audio/flac:*,"
    "http-get:*:audio/mpeg:*,"
    "http-get:*:audio/mp4:*,"
    "http-get:*:audio/ogg:*,"
    "http-get:*:audio/wav:*,"
    "http-get:*:audio/aiff:*,"
    "http-get:*:audio/x-dsf:*,"
    "http-get:*:audio/x-dff:*,"
    "http-get:*:audio/x-wavpack:*"
)

def _soap_response(service, action, body_inner):
    return (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body>'
        f'<u:{action}Response xmlns:u="urn:schemas-upnp-org:{service}">'
        + body_inner +
        f'</u:{action}Response>'
        '</s:Body>'
        '</s:Envelope>'
    )

def _soap_fault(code, desc):
    return (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        '<s:Body><s:Fault>'
        '<faultcode>s:Client</faultcode>'
        f'<faultstring>{desc}</faultstring>'
        '<detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
        f'<errorCode>{code}</errorCode>'
        f'<errorDescription>{desc}</errorDescription>'
        '</UPnPError></detail>'
        '</s:Fault></s:Body></s:Envelope>'
    )

def _parse_soap_arg(body, name):
    """Simple SOAP argument extractor without full XML namespace handling."""
    import re
    pat = rf'<(?:\w+:)?{re.escape(name)}[^>]*>(.*?)</(?:\w+:)?{re.escape(name)}>'
    m = re.search(pat, body, re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else ""

def _soap_action_header(headers):
    sa = headers.get("SOAPACTION", "").strip('"')
    if "#" in sa:
        return sa.split("#")[-1]
    return ""

class UPnPHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # Silence default HTTP logging

    def _send(self, code, content_type, body):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Server", "VELVET/1.0 UPnP/1.0")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p in ("/", "/upnp/device.xml", "/description.xml"):
            self._send(200, "text/xml; charset=utf-8", DEVICE_DESC)
        elif p == "/upnp/ContentDirectory.xml":
            self._send(200, "text/xml; charset=utf-8", CONTENT_DIR_SCPD)
        elif p == "/upnp/ConnectionManager.xml":
            self._send(200, "text/xml; charset=utf-8", CONN_MGR_SCPD)
        else:
            self._send(404, "text/plain", "Not found")

    def do_POST(self):
        p = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length).decode("utf-8", errors="replace")
        action = _soap_action_header(dict(self.headers))

        if p == "/upnp/control/ContentDirectory":
            self._handle_content_dir(action, body)
        elif p == "/upnp/control/ConnectionManager":
            self._handle_conn_mgr(action, body)
        else:
            self._send(404, "text/plain", "Not found")

    def do_SUBSCRIBE(self):
        # Minimal event subscription reply (no actual eventing)
        sid = f"uuid:velvet-event-{int(time.time())}"
        self.send_response(200)
        self.send_header("SID", sid)
        self.send_header("TIMEOUT", "Second-1800")
        self.end_headers()

    def _handle_content_dir(self, action, body):
        if action == "Browse":
            oid      = _parse_soap_arg(body, "ObjectID") or "0"
            flag     = _parse_soap_arg(body, "BrowseFlag") or "BrowseDirectChildren"
            start    = int(_parse_soap_arg(body, "StartingIndex") or "0")
            count    = int(_parse_soap_arg(body, "RequestedCount") or "0")

            try:
                result, returned, total = browse(oid, flag, start, count)
                # Escape DIDL for SOAP
                result_esc = result.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                resp = _soap_response(
                    "service:ContentDirectory:1", "Browse",
                    f"<Result>{result_esc}</Result>"
                    f"<NumberReturned>{returned}</NumberReturned>"
                    f"<TotalMatches>{total}</TotalMatches>"
                    f"<UpdateID>1</UpdateID>"
                )
                self._send(200, "text/xml; charset=utf-8", resp)
            except Exception as e:
                log.error(f"Browse error: {e}")
                self._send(500, "text/xml; charset=utf-8", _soap_fault(501, "Action Failed"))

        elif action == "GetSystemUpdateID":
            resp = _soap_response(
                "service:ContentDirectory:1", "GetSystemUpdateID", "<Id>1</Id>"
            )
            self._send(200, "text/xml; charset=utf-8", resp)

        elif action == "GetSortCapabilities":
            resp = _soap_response(
                "service:ContentDirectory:1", "GetSortCapabilities",
                "<SortCaps>dc:title,dc:creator,upnp:album</SortCaps>"
            )
            self._send(200, "text/xml; charset=utf-8", resp)

        elif action == "GetSearchCapabilities":
            resp = _soap_response(
                "service:ContentDirectory:1", "GetSearchCapabilities",
                "<SearchCaps></SearchCaps>"
            )
            self._send(200, "text/xml; charset=utf-8", resp)

        else:
            self._send(501, "text/xml; charset=utf-8", _soap_fault(401, "Invalid Action"))

    def _handle_conn_mgr(self, action, body):
        if action == "GetProtocolInfo":
            resp = _soap_response(
                "service:ConnectionManager:1", "GetProtocolInfo",
                f"<Source>{PROTOCOL_INFO}</Source><Sink></Sink>"
            )
            self._send(200, "text/xml; charset=utf-8", resp)
        elif action == "GetCurrentConnectionIDs":
            resp = _soap_response(
                "service:ConnectionManager:1", "GetCurrentConnectionIDs",
                "<ConnectionIDs>0</ConnectionIDs>"
            )
            self._send(200, "text/xml; charset=utf-8", resp)
        elif action == "GetCurrentConnectionInfo":
            resp = _soap_response(
                "service:ConnectionManager:1", "GetCurrentConnectionInfo",
                "<RcsID>-1</RcsID><AVTransportID>-1</AVTransportID>"
                f"<ProtocolInfo>{PROTOCOL_INFO}</ProtocolInfo>"
                "<PeerConnectionManager></PeerConnectionManager>"
                "<PeerConnectionID>-1</PeerConnectionID>"
                "<Direction>Output</Direction><Status>OK</Status>"
            )
            self._send(200, "text/xml; charset=utf-8", resp)
        else:
            self._send(501, "text/xml; charset=utf-8", _soap_fault(401, "Invalid Action"))

# ─── SSDP Advertisement ───────────────────────────────────────────────────────

SSDP_NOTIFY_TYPES = [
    "upnp:rootdevice",
    DEVICE_UUID,
    "urn:schemas-upnp-org:device:MediaServer:1",
    "urn:schemas-upnp-org:service:ContentDirectory:1",
    "urn:schemas-upnp-org:service:ConnectionManager:1",
]

def _notify(sock, nt, nts="ssdp:alive"):
    usn = DEVICE_UUID
    if nt != DEVICE_UUID:
        usn = f"{DEVICE_UUID}::{nt}"
    msg = (
        "NOTIFY * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR}:{SSDP_PORT}\r\n"
        f"CACHE-CONTROL: max-age=1800\r\n"
        f"LOCATION: {BASE_URL}/upnp/device.xml\r\n"
        f"NT: {nt}\r\n"
        f"NTS: {nts}\r\n"
        f"SERVER: Windows/10.0 UPnP/1.0 VELVET/1.0\r\n"
        f"USN: {usn}\r\n"
        "\r\n"
    )
    try:
        sock.sendto(msg.encode(), (SSDP_ADDR, SSDP_PORT))
    except Exception:
        pass

def _handle_msearch(sock, data, addr):
    text = data.decode("utf-8", errors="replace")
    if "M-SEARCH" not in text:
        return
    st = "ssdp:all"
    for line in text.splitlines():
        if line.upper().startswith("ST:"):
            st = line[3:].strip()
            break

    def should_respond(nt):
        return st in ("ssdp:all", nt, "upnp:rootdevice")

    for nt in SSDP_NOTIFY_TYPES:
        if not should_respond(nt):
            continue
        usn = DEVICE_UUID if nt == DEVICE_UUID else f"{DEVICE_UUID}::{nt}"
        resp = (
            "HTTP/1.1 200 OK\r\n"
            "CACHE-CONTROL: max-age=1800\r\n"
            f"DATE: {time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime())}\r\n"
            "EXT:\r\n"
            f"LOCATION: {BASE_URL}/upnp/device.xml\r\n"
            f"SERVER: Windows/10.0 UPnP/1.0 VELVET/1.0\r\n"
            f"ST: {nt}\r\n"
            f"USN: {usn}\r\n"
            "\r\n"
        )
        try:
            sock.sendto(resp.encode(), addr)
        except Exception:
            pass

def run_ssdp():
    """Listen for M-SEARCH and send periodic NOTIFY."""
    # Multicast listener socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except AttributeError:
        pass
    sock.bind(("", SSDP_PORT))
    mreq = struct.pack("4sL", socket.inet_aton(SSDP_ADDR), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(5.0)

    # Separate send socket (avoids multicast loopback issues)
    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    send_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 4)

    # Initial announcements
    for _ in range(3):
        for nt in SSDP_NOTIFY_TYPES:
            _notify(send_sock, nt)
        time.sleep(0.3)

    last_notify = time.time()
    while True:
        # Periodic re-announcement every 5 minutes
        if time.time() - last_notify > 300:
            for nt in SSDP_NOTIFY_TYPES:
                _notify(send_sock, nt)
            last_notify = time.time()

        try:
            data, addr = sock.recvfrom(4096)
            _handle_msearch(send_sock, data, addr)
        except socket.timeout:
            pass
        except Exception as e:
            log.debug(f"SSDP recv error: {e}")

def run_http():
    server = HTTPServer(("0.0.0.0", UPNP_PORT), UPnPHandler)
    server.serve_forever()

# ─── Entry Point ──────────────────────────────────────────────────────────────

def start():
    """Start UPnP server in background threads."""
    http_thread = threading.Thread(target=run_http, daemon=True)
    http_thread.start()

    ssdp_thread = threading.Thread(target=run_ssdp, daemon=True)
    ssdp_thread.start()

    print(f"  [MUSIC] UPnP/DLNA server active on port {UPNP_PORT}")
    print(f"       Device URL: {BASE_URL}/upnp/device.xml")
    print(f"       Visible to: Marantz M1 (HEOS), Cambridge EXN100 (StreamMagic),")
    print(f"                   mconnect, BubbleUPnP, and any DLNA controller")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(f"Starting MusicIQ UPnP/DLNA standalone server...")
    print(f"Local IP: {LOCAL_IP}")
    start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
