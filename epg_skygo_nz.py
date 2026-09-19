import os
import sys
import time
import uuid
import json
import argparse
import urllib.parse
from urllib.parse import parse_qs, urlparse
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET
from xml.dom import minidom
import requests
from requests.adapters import HTTPAdapter

try:
    from o11_v2 import DoHResolver
except ImportError:
    DoHResolver = None

USE_DOH = False
DOH_URL = "https://dns.controld.com/1rmnvexb4iv"
if USE_DOH and DoHResolver:
    DoHResolver(DOH_URL)

# -----------------------
# Config & Defaults
# -----------------------
DAYS = 7                      # Bilangan hari EPG (1 = hari ini, 2 = hari ini + esok, 7 = 1 minggu, 0 = 7 hari penuh)
OUTPUT_XML = "skygo.nz.xml"
MALAYSIA_TZ = timezone(timedelta(hours=8))
MAX_WORKERS = 8               # Bilangan threads serentak untuk window grid
GRID_WINDOW_HOURS = 24

# -----------------------
# Proxy Config (Toggle)
# -----------------------
USE_PROXY = False             # Set True untuk guna HTTP Proxy, False untuk direct
PROXY_RAW = ""

# -----------------------
# Sky Go NZ Endpoints & Config
# -----------------------
GRAPHQL_URL = "https://api.skyone.co.nz/exp/graph"
DEFAULT_APP_ID = "skyGo-web"
DEFAULT_PRODUCT_ID = "skyGo"
DEFAULT_APP_VERSION = "1.14.49"

REQUEST_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.skygo.co.nz",
    "Referer": "https://www.skygo.co.nz/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "x-sky-app-id": DEFAULT_APP_ID,
    "x-product-id": DEFAULT_PRODUCT_ID,
    "x-sky-app-version": DEFAULT_APP_VERSION,
    "x-sky-device-id": uuid.uuid4().hex,
}

GET_ALL_CHANNELS_QUERY = """
query GetAllChannels {
  experience(appId: SKY_GO_WEB) {
    channels {
      __typename
      ... on LinearChannel {
        id
        title
        number
        dvbTriplet
        tileImage {
          uri
        }
      }
    }
  }
}
"""

GET_SLOTS_QUERY = """
query getSlots($from: DateTime!, $to: DateTime!) {
  channels {
    __typename
    ... on LinearChannel {
      id
      title
      number
      tileImage {
        uri
      }
      slots(from: $from, to: $to) {
        id
        start
        end
        programme {
          __typename
          id
          title
          synopsis
          ... on Movie {
            heroImage {
              uri
            }
            tileImage {
              uri
            }
          }
          ... on Episode {
            image {
              uri
            }
            show {
              id
              title
              synopsis
              heroImage {
                uri
              }
              tileImage {
                uri
              }
            }
          }
          ... on PayPerViewEventProgram {
            image {
              uri
            }
            event {
              heroImage {
                uri
              }
            }
          }
        }
      }
    }
  }
}
"""


# -----------------------
# Helper Functions
# -----------------------
def log(msg):
    """Print message with immediate flush for real-time progress."""
    print(msg, flush=True)


def build_proxy_dict(proxy_str: str) -> dict:
    """Parse host:port:user:pass or standard proxy URL into requests proxies dict."""
    if not proxy_str:
        return {}

    proxy_str = proxy_str.strip()
    if not proxy_str:
        return {}

    if proxy_str.startswith("http://") or proxy_str.startswith("https://") or proxy_str.startswith("socks5://"):
        return {"http": proxy_str, "https": proxy_str}

    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, pwd = parts
        user_enc = urllib.parse.quote(user)
        pwd_enc = urllib.parse.quote(pwd)
        p_url = f"http://{user_enc}:{pwd_enc}@{host}:{port}"
        return {"http": p_url, "https": p_url}
    elif len(parts) == 2:
        host, port = parts
        p_url = f"http://{host}:{port}"
        return {"http": p_url, "https": p_url}

    return {}


def xml_clean(text):
    """Strip control characters that are illegal in XML 1.0."""
    if not isinstance(text, str):
        return str(text) if text is not None else ""
    return "".join(
        ch for ch in text
        if ch == "\t" or ch == "\n" or ch == "\r" or ord(ch) >= 0x20
    ).strip()


def url_clean(url):
    """Clean and encode whitespace in URLs."""
    if not url:
        return ""
    return xml_clean(url).replace(" ", "%20")


def parse_iso_datetime(value: str) -> datetime:
    """Parse ISO 8601 UTC timestamp."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        iso_clean = value.replace("Z", "+00:00")
        return datetime.fromisoformat(iso_clean)
    except Exception:
        raise ValueError(f"Format tarikh tak dikenali: {value}")


def format_time_utc_to_my(xml_time: str) -> str:
    """Convert UTC ISO timestamp to XMLTV format (YYYYMMDDHHMMSS +0800)."""
    dt_utc = parse_iso_datetime(xml_time)
    return dt_utc.astimezone(MALAYSIA_TZ).strftime("%Y%m%d%H%M%S +0800")


def pick_channel_icon(channel_data: dict) -> str:
    """Select logo icon for channel."""
    tile_image = channel_data.get("tileImage") or {}
    if isinstance(tile_image, dict) and tile_image.get("uri"):
        return tile_image["uri"]
    return ""


def pick_program_icon(slot_data: dict) -> str:
    """Select best available thumbnail/poster for programme."""
    prog = slot_data.get("programme") or {}
    show = prog.get("show") or {}
    event = prog.get("event") or {}

    # 1. Episode image or direct programme image
    img = prog.get("image") or {}
    if isinstance(img, dict) and img.get("uri"):
        return img["uri"]

    # 2. Movie/Show Hero image
    hero = prog.get("heroImage") or show.get("heroImage") or event.get("heroImage") or {}
    if isinstance(hero, dict) and hero.get("uri"):
        return hero["uri"]

    # 3. Movie/Show Tile image
    tile = prog.get("tileImage") or show.get("tileImage") or {}
    if isinstance(tile, dict) and tile.get("uri"):
        return tile["uri"]

    return ""


# -----------------------
# Scraping & Fetching
# -----------------------
def init_session(proxies: dict = None):
    """Create high-speed configured requests session with connection pooling."""
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=30, pool_maxsize=30, max_retries=3)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    if proxies:
        session.proxies.update(proxies)
    return session


def fetch_channels(session: requests.Session) -> list:
    """Fetch all linear channel metadata from Sky Go NZ GraphQL API."""
    params = {
        "query": GET_ALL_CHANNELS_QUERY,
        "operationName": "GetAllChannels",
    }
    response = session.get(GRAPHQL_URL, headers=REQUEST_HEADERS, params=params, timeout=20)
    response.raise_for_status()
    raw_channels = response.json().get("data", {}).get("experience", {}).get("channels", [])
    linear_channels = [c for c in raw_channels if c.get("__typename") == "LinearChannel"]
    # Sort channels by logical channel number
    linear_channels.sort(key=lambda c: c.get("number") if c.get("number") is not None else 9999)
    return linear_channels


def fetch_schedule_parallel(
    session: requests.Session,
    start_utc: datetime,
    end_utc: datetime,
    max_workers: int = MAX_WORKERS,
) -> list:
    """
    Fetch all schedule time windows simultaneously in parallel (Turbo Mode).
    """
    # Generate time windows (e.g. 24h chunks)
    windows = []
    cursor = start_utc
    while cursor < end_utc:
        dur = min(GRID_WINDOW_HOURS, max(1, int((end_utc - cursor).total_seconds() + 3599) // 3600))
        w_end = cursor + timedelta(hours=dur)
        windows.append((cursor, w_end))
        cursor = w_end

    total_windows = len(windows)
    log(f"[*] Fetching {total_windows} schedule window(s) in parallel (threads={min(total_windows, max_workers)})...")

    def fetch_window(w_start_dt, w_end_dt):
        vars_payload = {
            "from": w_start_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "to": w_end_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }
        params = {
            "query": GET_SLOTS_QUERY,
            "variables": json.dumps(vars_payload),
            "operationName": "getSlots",
        }
        resp = session.get(GRAPHQL_URL, headers=REQUEST_HEADERS, params=params, timeout=25)
        resp.raise_for_status()
        channels_data = resp.json().get("data", {}).get("channels", [])
        linear_data = [c for c in channels_data if c.get("__typename") == "LinearChannel"]
        return linear_data

    merged_channels: dict[str, dict] = {}
    seen_programmes: dict[str, set[str]] = {}

    with ThreadPoolExecutor(max_workers=min(total_windows, max_workers)) as executor:
        futs = {executor.submit(fetch_window, w[0], w[1]): w for w in windows}
        for future in as_completed(futs):
            ch_list = future.result()

            for ch in ch_list:
                channel_id = str(ch.get("id", "")).strip()
                if not channel_id:
                    continue
                merged_channels.setdefault(channel_id, {
                    "id": channel_id,
                    "title": ch.get("title"),
                    "number": ch.get("number"),
                    "tileImage": ch.get("tileImage"),
                    "slots": [],
                })
                seen_programmes.setdefault(channel_id, set())

                for slot in ch.get("slots", []):
                    slot_id = slot.get("id") or ""
                    slot_start = slot.get("start") or ""
                    key = f"{slot_id}|{slot_start}"
                    if key not in seen_programmes[channel_id]:
                        seen_programmes[channel_id].add(key)
                        merged_channels[channel_id]["slots"].append(slot)

    # Sort slots chronologically
    for ch in merged_channels.values():
        ch["slots"].sort(key=lambda s: s.get("start", ""))

    return list(merged_channels.values())


# -----------------------
# XMLTV Builder
# -----------------------
def build_xmltv(channels_payload: list, schedule_payload: list) -> tuple:
    """Construct XMLTV ElementTree from collected Sky Go NZ channels and schedules."""
    tv = ET.Element("tv", attrib={"generator-info-name": "skygo.nz-epg"})
    channel_map: dict[str, str] = {}
    total_programmes = 0

    # 1. Channels
    for ch in channels_payload:
        source_channel_id = str(ch.get("id", "")).strip()
        if not source_channel_id:
            continue
        channel_number = ch.get("number")
        xml_channel_id = (
            str(channel_number).strip()
            if channel_number is not None and str(channel_number).strip()
            else source_channel_id
        )
        display_name = ch.get("title") or f"Channel-{source_channel_id}"

        channel_elem = ET.SubElement(tv, "channel", id=xml_channel_id)
        ET.SubElement(channel_elem, "display-name", lang="en").text = xml_clean(display_name)
        icon_url = pick_channel_icon(ch)
        if icon_url:
            ET.SubElement(channel_elem, "icon", src=url_clean(icon_url))
        channel_map[source_channel_id] = xml_channel_id

    # 2. Programmes
    for schedule_channel in schedule_payload:
        source_channel_id = str(schedule_channel.get("id", "")).strip()
        if not source_channel_id:
            continue
        channel_number = schedule_channel.get("number")
        xml_channel_id = channel_map.get(
            source_channel_id,
            str(channel_number).strip() if channel_number is not None else source_channel_id
        )
        slots = schedule_channel.get("slots", [])
        total_programmes += len(slots)

        for slot in slots:
            start_str = slot.get("start")
            stop_str = slot.get("end")
            if not start_str or not stop_str:
                continue

            prog = slot.get("programme") or {}
            show_data = prog.get("show") or {}

            # Title: show title jika ada (cth: Space Invaders), jika tiada guna prog title (cth: Murphy's War)
            show_title = xml_clean(show_data.get("title") or "")
            prog_title = xml_clean(prog.get("title") or "")
            main_title = show_title if show_title else (prog_title or "No Title")

            # Synopsis / Description
            synopsis = prog.get("synopsis") or show_data.get("synopsis") or ""

            # Build XML element (Simple programme: title, desc, icon)
            programme = ET.SubElement(tv, "programme", {
                "start": format_time_utc_to_my(start_str),
                "stop": format_time_utc_to_my(stop_str),
                "channel": xml_channel_id,
            })

            ET.SubElement(programme, "title", lang="en").text = main_title
            if synopsis:
                ET.SubElement(programme, "desc", lang="en").text = xml_clean(synopsis)

            icon_url = pick_program_icon(slot)
            if icon_url:
                ET.SubElement(programme, "icon", src=url_clean(icon_url))

    pretty_xml = minidom.parseString(ET.tostring(tv, encoding="utf-8")).toprettyxml(indent="  ", encoding="utf-8")
    return pretty_xml, total_programmes


def save_xml(content: bytes, filepath: str = OUTPUT_XML):
    """Save XML bytes to disk."""
    with open(filepath, "wb") as f:
        f.write(content)
    log(f"[+] Successfully saved EPG to: {filepath}")


def build_grid_range_utc(days: int) -> tuple:
    """Calculate UTC start and end bounds based on Malaysia local day range."""
    now_my = datetime.now(MALAYSIA_TZ)
    start_my = now_my.replace(hour=0, minute=0, second=0, microsecond=0)
    num_days = 7 if (not days or days <= 0) else days
    return start_my.astimezone(timezone.utc), (start_my + timedelta(days=num_days)).astimezone(timezone.utc)


# -----------------------
# Main Entry Point
# -----------------------
def main():
    parser = argparse.ArgumentParser(description="Sky Go NZ XMLTV EPG Generator (Turbo Mode)")
    parser.add_argument(
        "--days",
        "-d",
        type=int,
        default=DAYS,
        help=f"Number of days to include in EPG (e.g. 1 for today only, 2 for today+tomorrow, 7 for 1 week, 0 for all 7 days). Default: {DAYS}",
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=MAX_WORKERS,
        help=f"Number of parallel worker threads (default: {MAX_WORKERS})",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="Custom proxy in host:port:user:pass or http://... format. Overrides default proxy.",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Disable proxy and connect directly.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=OUTPUT_XML,
        help=f"Output XML file path (default: {OUTPUT_XML})",
    )
    args = parser.parse_args()

    days = args.days
    start_utc, end_utc = build_grid_range_utc(days)

    use_proxy = False if args.no_proxy else (USE_PROXY or bool(args.proxy) or bool(os.environ.get("HTTP_PROXY")))
    proxy_str = args.proxy or os.environ.get("SKYGO_PROXY") or PROXY_RAW
    proxies = build_proxy_dict(proxy_str) if use_proxy else {}

    log("=" * 60)
    log(" Sky Go NZ - XMLTV EPG Generator (Turbo Mode)")
    log("=" * 60)
    now_dt = datetime.now(MALAYSIA_TZ)
    log(f"Malaysia Now : {now_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    start_str = start_utc.astimezone(MALAYSIA_TZ).strftime('%Y-%m-%d %H:%M:%S')
    end_str = (end_utc - timedelta(seconds=1)).astimezone(MALAYSIA_TZ).strftime('%Y-%m-%d %H:%M:%S')
    log(f"Range {days if days > 0 else 7} hari : {start_str} -> {end_str}")
    log(f"Threads      : {args.workers} concurrent workers")
    log(f"Proxy Status : {'ENABLED (' + proxy_str.split('@')[-1].split(':')[0] + ')' if use_proxy else 'DISABLED (Direct)'}")
    log(f"Output File  : {args.output}")
    log("-" * 60)

    t_start = time.time()
    session = init_session(proxies=proxies)

    log("[*] Fetching Sky Go NZ channels list...")
    channels_payload = fetch_channels(session)
    log(f"[+] Loaded {len(channels_payload)} channels.")

    schedule_payload = fetch_schedule_parallel(
        session=session,
        start_utc=start_utc,
        end_utc=end_utc,
        max_workers=args.workers,
    )

    scheduled_channel_ids = {str(ch.get("id", "")).strip() for ch in schedule_payload if ch.get("slots")}
    failed_channel_names = [
        ch.get("title") or f"Channel-{ch.get('id')}"
        for ch in channels_payload
        if str(ch.get("id", "")).strip() not in scheduled_channel_ids
    ]

    xml_data, total_programmes = build_xmltv(channels_payload, schedule_payload)
    save_xml(xml_data, args.output)
    t_end = time.time()

    log("-" * 60)
    log(f"[+] Total Channels Extracted  : {len(channels_payload)}")
    log(f"[+] Channels with Schedule    : {len(scheduled_channel_ids)}")
    log(f"[+] Total Programmes Extracted: {total_programmes}")
    log(f"[+] Time Elapsed              : {t_end - t_start:.2f} saat (~ {(t_end - t_start)/60:.1f} min)")
    if failed_channel_names:
        log(f"[!] Channels without schedule : {len(failed_channel_names)} ({', '.join(failed_channel_names[:5])}...)")
    log("=" * 60)


if __name__ == "__main__":
    main()
