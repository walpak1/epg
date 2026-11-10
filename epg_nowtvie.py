import os
import re
import gzip
import time
import argparse
import urllib.parse
import requests
from requests.adapters import HTTPAdapter
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from o11_v2 import DoHResolver
except ImportError:
    DoHResolver = None

USE_DOH = False
DOH_URL = "https://dns.controld.com/3wbdloonaf/dns-query"
if USE_DOH and DoHResolver:
    DoHResolver(DOH_URL)

# -----------------------
# Config
# -----------------------
DAYS = 2                      # Bilangan hari EPG (1 = hari ini sahaja, 2 = hari ini + esok, 0 = semua yang tersedia ~3-4 hari)
TERRITORY = "IE"              # IE (NOW TV Ireland)
BASE_DOMAIN = "nowtv.com"
LANGUAGE = "en-GB"            # SkyOTT IE memerlukan x-skyott-language = en-GB
XML_LANG = "en"
TVG_SUFFIX = "ie"
OUTPUT_FILE = "nowtvie.xml"
MALAYSIA_TZ = timezone(timedelta(hours=8))
MAX_WORKERS = 8               # Bilangan threads serentak untuk muat turun tetingkap masa EPG

# -----------------------
# Proxy Config (Toggle)
# -----------------------
USE_PROXY = False             # Set True untuk guna HTTP Proxy, False untuk direct
PROXY_RAW = ""                # Format: host:port:user:pass atau http://...

# -----------------------
# SkyOTT / NOW TV IE Config (Tanpa Akaun / Tanpa configie1.json)
# -----------------------
CHANNEL_GUIDE_URL = f"https://mobile.clients.{BASE_DOMAIN}/bff/channel_guide"

# Semua segmen kandungan IE supaya 100% saluran (Entertainment, Cinema, Sky Sports, UHD, TNT & Premier Sports, Kids, News) dipulangkan tanpa akaun
ALL_SEGMENTS = ",".join([
    "SPORTS_CORE",
    "SPORTS_COMPLETE",
    "SPORTS_SELECT",
    "SPORTS_FOOTBALL",
    "SPORTS_EVENTS",
    "SPORTS_EVENTS_SELECT",
    "SPORTS_EVENTS_FOOTBALL",
    "SPORTS_EVENTS_COMPLETE",
    "SPORTS_EVENTS_EXCLUSIVE",
    "SPORTS_EXTRA",
    "SPORTS_EXTRA_EXCLUSIVE",
    "SPORTS_BONUS",
    "SPORTS",
    "SPORTS_ESSENTIALS",
    "SPORTSVOD",
    "SPORTS_HIGHLIGHTS",
    "SUPERSPORT",
    "ENTERTAINMENT",
    "MOVIES",
    "CINEMA",
    "KIDS",
    "NEWS",
    "HAYU",
    "HBOMAX",
    "SHORTFORM",
    "SSN",
])


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

    if proxy_str.startswith(("http://", "https://", "socks5://")):
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


def format_time_sec(epoch_sec: int) -> str:
    """Convert Unix seconds timestamp to XMLTV format (YYYYMMDDHHMMSS +0800)."""
    if not epoch_sec:
        return ""
    dt_utc = datetime.fromtimestamp(epoch_sec, tz=timezone.utc)
    dt_local = dt_utc.astimezone(MALAYSIA_TZ)
    return dt_local.strftime("%Y%m%d%H%M%S +0800")


def make_tvg_id(clean_title: str) -> str:
    """Generate tvg-id matching nowtvie1.php / nowie.php logic: Alphanumeric(cleanTitle).ie."""
    alnum = re.sub(r"[^a-zA-Z0-9]", "", clean_title or "")
    if not alnum:
        alnum = "UnknownChannel"
    return f"{alnum}.{TVG_SUFFIX}"


def get_time_range(days: int):
    """
    Calculate start and end epoch (seconds) based on days setting in Malaysia Time.
    If days <= 0, returns (None, None) for unlimited range.
    """
    if not days or days <= 0:
        return None, None

    now = datetime.now(MALAYSIA_TZ)
    start_dt = datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=MALAYSIA_TZ)
    end_dt = datetime(now.year, now.month, now.day, 23, 59, 59, tzinfo=MALAYSIA_TZ) + timedelta(days=days - 1)
    return int(start_dt.timestamp()), int(end_dt.timestamp())


def build_skyott_headers() -> dict:
    """Build anonymous SkyOTT IE BFF headers (no user token or mTLS cert required)."""
    return {
        "accept-encoding": "gzip",
        "user-agent": "NowottAndroid-GB/6.4.13-124060413",
        "x-skyott-ab-clip": "planPickerTemplateBdisposition:control_1;por50:control",
        "x-skyott-ab-recs": "aae2edashboard:control_android;skeletor:variation1",
        "x-skyott-activeterritory": TERRITORY,
        "x-skyott-client-version": "6.9.200",
        "x-skyott-device": "TV",
        "x-skyott-endorsements": 'epgAssetTile,videoFormat; caps="UHD",colorSpace; caps="SDR,HDR,HLG,DV,DolbyVision",audioFormat; caps="Dolby51,DolbyAtmos,Stereo"',
        "x-skyott-language": LANGUAGE,
        "x-skyott-platform": "ANDROIDTV",
        "x-skyott-proposition": "NOWOTT",
        "x-skyott-provider": "NOWTV",
        "x-skyott-territory": TERRITORY,
    }


def align_5min(dt: datetime) -> datetime:
    """Align UTC datetime down to nearest 5 minutes as required by SkyOTT channel_guide."""
    return dt.replace(minute=dt.minute - (dt.minute % 5), second=0, microsecond=0)


def compute_fetch_windows(days: int) -> list:
    """
    Compute 24h UTC startTime checkpoints within SkyOTT API limits
    (API allows startTime between -1 day in past and +2 days in future).
    """
    now_utc = align_5min(datetime.now(timezone.utc))
    min_allowed = align_5min(now_utc - timedelta(hours=23))
    max_allowed = align_5min(now_utc + timedelta(hours=47, minutes=50))

    if days and days > 0:
        start_sec, end_sec = get_time_range(days)
        target_start = datetime.fromtimestamp(start_sec, tz=timezone.utc)
        target_end = datetime.fromtimestamp(end_sec, tz=timezone.utc)
    else:
        target_start = min_allowed
        target_end = max_allowed + timedelta(hours=24)

    cur = max(align_5min(target_start), min_allowed)
    windows = []

    while cur < target_end:
        clamped = min(cur, max_allowed)
        if not windows or clamped > windows[-1]:
            windows.append(clamped)
        if clamped >= max_allowed:
            break
        cur = cur + timedelta(hours=23)

    if not windows:
        windows.append(now_utc)

    return windows


# -----------------------
# Scraping & Fetching
# -----------------------
def init_session(headers: dict, proxies: dict = None):
    """Create high-speed configured requests session with connection pooling."""
    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=32, pool_maxsize=32, max_retries=2)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(headers)
    if proxies:
        session.proxies.update(proxies)
    return session


def fetch_guide_window(session, start_dt_utc: datetime) -> tuple:
    """Fetch a single 24h channel_guide window."""
    start_str = start_dt_utc.strftime("%Y-%m-%dT%H:%M+00:00")
    params = {
        "startTime": start_str,
        "assetsPerChannelCapLimit": "200",
        "period": "24h",
        "playout_content_segments": ALL_SEGMENTS,
        "discovery_content_segments": ALL_SEGMENTS,
    }

    last_err = None
    for attempt in range(3):
        try:
            r = session.get(CHANNEL_GUIDE_URL, params=params, timeout=20)
            if r.status_code == 200:
                return start_str, r.json(), None
            last_err = f"HTTP {r.status_code}: {r.text[:160]}"
        except Exception as e:
            last_err = str(e)
        if attempt < 2:
            time.sleep(0.5 * (attempt + 1))

    return start_str, None, last_err


def extract_channel_logo(ch_obj: dict) -> str:
    """Extract channel logo matching nowtvie1.php preference (Dark -> Default -> Light)."""
    logo_dict = ch_obj.get("logo") or {}
    logo_url = (
        logo_dict.get("Dark")
        or logo_dict.get("Default")
        or logo_dict.get("Light")
        or ""
    )
    if logo_url:
        return logo_url.replace("{width}", "500").replace("{height}", "500")

    sk = ch_obj.get("serviceKey")
    if sk:
        return f"https://pixel.disco.{BASE_DOMAIN}/logo/skychb_{sk}_darknow/LOGO_CHANNEL_LIGHT/500/500?language={LANGUAGE}&proposition=NOWOTT"
    return ""


def extract_programme_image(data_obj: dict) -> str:
    """Extract best programme thumbnail (prefer 16:9 widescreen, fallback to poster)."""
    images = data_obj.get("images") or {}
    return (
        images.get("16-9")
        or images.get("manhattan_channel")
        or images.get("16-9-SEASON")
        or images.get("16-9-SERIES")
        or images.get("LINEAR_3_4")
        or ""
    )


def fetch_all_channels(days=DAYS, use_proxy=USE_PROXY, proxy_str=PROXY_RAW, max_workers=MAX_WORKERS):
    """Fetch NOW TV IE channel guide windows in parallel and merge channels & schedules."""
    headers = build_skyott_headers()
    proxies = build_proxy_dict(proxy_str) if use_proxy else {}
    session = init_session(headers, proxies=proxies)

    start_limit_sec, end_limit_sec = get_time_range(days)
    windows = compute_fetch_windows(days)

    log(f"[*] Fetching NOW TV {TERRITORY} ({BASE_DOMAIN}) channel guide across {len(windows)} x 24h windows (threads={min(max_workers, len(windows))})...")

    window_results = []
    failed_windows = []

    with ThreadPoolExecutor(max_workers=min(max_workers, len(windows))) as executor:
        future_map = {
            executor.submit(fetch_guide_window, session, dt): dt
            for dt in windows
        }
        for idx, future in enumerate(as_completed(future_map), 1):
            start_str, payload, err = future.result()
            if payload and payload.get("channels"):
                ch_count = len(payload["channels"])
                item_count = sum(len(c.get("scheduleItems", [])) for c in payload["channels"])
                log(f"  [{idx}/{len(windows)}] OK: {start_str} ({ch_count} ch, {item_count} items)")
                window_results.append(payload)
            else:
                log(f"  [{idx}/{len(windows)}] ERR: {start_str} -> {err}")
                failed_windows.append(start_str)

    if not window_results:
        return {}, failed_windows

    channels_map = {}

    for payload in window_results:
        for ch in payload.get("channels", []):
            sk = str(ch.get("serviceKey") or "").strip()
            if not sk:
                continue

            raw_title = ch.get("channelTitle") or ch.get("name") or f"Channel {sk}"
            clean_title = raw_title.split(" - ")[0].strip()
            tvg_id = make_tvg_id(clean_title)
            logo = extract_channel_logo(ch)
            rank = ch.get("rank") or 99999

            if sk not in channels_map:
                channels_map[sk] = {
                    "service_key": sk,
                    "tvg_id": tvg_id,
                    "name": clean_title,
                    "raw_title": raw_title,
                    "rank": rank,
                    "logo": logo,
                    "seen_starts": set(),
                    "programmes": [],
                }
            elif not channels_map[sk]["logo"] and logo:
                channels_map[sk]["logo"] = logo

            ch_entry = channels_map[sk]
            for item in ch.get("scheduleItems", []):
                start_sec = item.get("startTimeUTC")
                dur_sec = item.get("durationSeconds")
                if not start_sec or not dur_sec or dur_sec <= 0:
                    continue

                end_sec = int(start_sec) + int(dur_sec)
                start_sec = int(start_sec)

                if start_limit_sec and end_sec <= start_limit_sec:
                    continue
                if end_limit_sec and start_sec > end_limit_sec:
                    continue

                if start_sec in ch_entry["seen_starts"]:
                    continue
                ch_entry["seen_starts"].add(start_sec)

                data_obj = item.get("data") or {}
                title = (data_obj.get("title") or "").strip()
                if not title:
                    continue

                desc = (data_obj.get("description") or "").strip()
                season_no = data_obj.get("seasonNumber")
                episode_no = data_obj.get("episodeNumber")
                if season_no and episode_no and desc:
                    ep_tag = f"S{int(season_no):02d}E{int(episode_no):02d}"
                    if ep_tag not in desc:
                        desc = f"[{ep_tag}] {desc}"
                elif not desc:
                    desc = title

                img_url = extract_programme_image(data_obj)

                ch_entry["programmes"].append({
                    "start": start_sec,
                    "end": end_sec,
                    "title": title,
                    "desc": desc,
                    "image": img_url,
                })

    return channels_map, failed_windows


# -----------------------
# XMLTV Builder
# -----------------------
def build_xmltv(channels_map: dict, failed_windows: list, sort_by: str = "rank", id_format: str = "tvg"):
    """Construct XMLTV ElementTree from collected channels and schedules."""
    tv = ET.Element("tv", attrib={"generator-info-name": "nowtvie-epg-grabber"})

    if sort_by == "name":
        sorted_channels = sorted(channels_map.values(), key=lambda c: c["name"].lower())
    else:
        sorted_channels = sorted(channels_map.values(), key=lambda c: (int(c.get("rank", 99999)), c["name"].lower()))

    total_programmes = 0
    channels_with_programmes = 0

    def get_ch_id(ch):
        if id_format == "name":
            return xml_clean(ch["name"])
        if id_format == "key":
            return xml_clean(ch["service_key"])
        return xml_clean(ch["tvg_id"])

    # 1. Output Channel Definitions
    for ch in sorted_channels:
        ch_id = get_ch_id(ch)
        ch_elem = ET.SubElement(tv, "channel", id=ch_id)
        ET.SubElement(ch_elem, "display-name", lang=XML_LANG).text = xml_clean(ch["name"])
        if ch.get("logo"):
            ET.SubElement(ch_elem, "icon", src=url_clean(ch["logo"]))

    # 2. Output Programme Schedules
    for ch in sorted_channels:
        ch_id = get_ch_id(ch)
        programmes = ch.get("programmes", [])

        if programmes:
            channels_with_programmes += 1
            programmes.sort(key=lambda p: p["start"])

            for p in programmes:
                total_programmes += 1
                prog_elem = ET.SubElement(tv, "programme", {
                    "start": format_time_sec(p["start"]),
                    "stop": format_time_sec(p["end"]),
                    "channel": ch_id,
                })

                ET.SubElement(prog_elem, "title", lang=XML_LANG).text = xml_clean(p["title"])

                if p.get("desc"):
                    ET.SubElement(prog_elem, "desc", lang=XML_LANG).text = xml_clean(p["desc"])

                img_url = p.get("image") or ch.get("logo")
                if img_url:
                    ET.SubElement(prog_elem, "icon", src=url_clean(img_url))

    # Pretty Print XML
    rough_xml = ET.tostring(tv, encoding="utf-8")
    try:
        ET.indent(tv, space="  ")
        pretty_xml = ET.tostring(tv, encoding="utf-8", xml_declaration=True)
    except Exception:
        parsed = minidom.parseString(rough_xml)
        pretty_xml = parsed.toprettyxml(indent="  ", encoding="utf-8")

    stats = {
        "channel_count": len(sorted_channels),
        "channels_with_programmes": channels_with_programmes,
        "total_programmes": total_programmes,
        "failed_windows": failed_windows,
    }

    return pretty_xml, stats


def save_xml(content, filepath=OUTPUT_FILE):
    """Save formatted XML bytes to disk (supports .xml and .xml.gz)."""
    if filepath.endswith(".gz"):
        with gzip.open(filepath, "wb") as f:
            f.write(content if isinstance(content, bytes) else content.encode("utf-8"))
    else:
        with open(filepath, "wb") as f:
            f.write(content if isinstance(content, bytes) else content.encode("utf-8"))
    log(f"[+] Successfully saved EPG to: {filepath}")


# -----------------------
# Main Entry Point
# -----------------------
def main():
    parser = argparse.ArgumentParser(description="NOW TV Ireland (IE) XMLTV EPG Generator (No Account Needed)")
    parser.add_argument(
        "--days",
        "-d",
        type=int,
        default=DAYS,
        help=f"Number of days to include in EPG (1=today, 2=today+tomorrow, 0=all available ~3-4 days). Default: {DAYS}",
    )
    parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=MAX_WORKERS,
        help=f"Number of parallel worker threads (default: {MAX_WORKERS})",
    )
    parser.add_argument(
        "--sort-by",
        choices=["rank", "name"],
        default="rank",
        help="Sort channels by API 'rank' or 'name' (default: rank)",
    )
    parser.add_argument(
        "--id-format",
        choices=["tvg", "name", "key"],
        default="tvg",
        help="Channel XML id format: 'tvg' (matches nowie.php/nowtvie1.php tvg-id e.g. SkySportsMainEvent.ie), 'name', or 'key' (serviceKey). Default: tvg",
    )
    parser.add_argument(
        "--proxy",
        default=None,
        help="Custom proxy in host:port:user:pass or http://... format.",
    )
    parser.add_argument(
        "--no-proxy",
        action="store_true",
        help="Disable proxy and connect directly.",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=OUTPUT_FILE,
        help=f"Output XML file path (e.g. nowtvie.xml or nowtvie.xml.gz, default: {OUTPUT_FILE})",
    )
    args = parser.parse_args()

    days = args.days
    start_sec, end_sec = get_time_range(days)

    use_proxy = False if args.no_proxy else (USE_PROXY or bool(args.proxy) or bool(os.environ.get("NOWTV_PROXY")) or bool(os.environ.get("HTTP_PROXY")))
    proxy_str = args.proxy or os.environ.get("NOWTV_PROXY") or PROXY_RAW

    log("=" * 60)
    log(" NOW TV Ireland (IE) - XMLTV EPG Generator (No Account Mode)")
    log("=" * 60)
    now_dt = datetime.now(MALAYSIA_TZ)
    log(f"Malaysia Now : {now_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    log(f"Territory    : {TERRITORY} ({BASE_DOMAIN} | tvg-id: *.{TVG_SUFFIX})")
    if days > 0 and start_sec and end_sec:
        start_str = datetime.fromtimestamp(start_sec, MALAYSIA_TZ).strftime("%Y-%m-%d %H:%M:%S")
        end_str = datetime.fromtimestamp(end_sec, MALAYSIA_TZ).strftime("%Y-%m-%d %H:%M:%S")
        log(f"Range {days} hari : {start_str} -> {end_str}")
    else:
        log("Range        : Semua jadual yang ada (~3-4 hari dari API)")

    log(f"Channel ID   : {args.id_format}")
    log(f"Threads      : {args.workers} concurrent workers")
    log(f"Proxy Status : {'ENABLED (' + proxy_str.split('@')[-1].split(':')[0] + ')' if use_proxy and proxy_str else 'DISABLED (Direct)'}")
    log(f"Output File  : {args.output}")
    log("-" * 60)

    t_start = time.time()
    channels_map, failed_windows = fetch_all_channels(
        days=days,
        use_proxy=use_proxy,
        proxy_str=proxy_str,
        max_workers=args.workers,
    )

    if not channels_map:
        log("[!] No channels extracted. Please check network connection or proxy.")
        return

    xml_bytes, stats = build_xmltv(
        channels_map,
        failed_windows,
        sort_by=args.sort_by,
        id_format=args.id_format,
    )
    save_xml(xml_bytes, args.output)
    t_end = time.time()

    log("-" * 60)
    log(f"[+] Total Channels Extracted  : {stats['channel_count']}")
    log(f"[+] Channels with Schedule    : {stats['channels_with_programmes']}")
    log(f"[+] Total Programmes Extracted: {stats['total_programmes']}")
    log(f"[+] Time Elapsed              : {t_end - t_start:.2f} saat (~ {(t_end - t_start)/60:.1f} min)")
    if stats["failed_windows"]:
        log(f"[!] Failed Windows            : {', '.join(stats['failed_windows'])}")
    log("=" * 60)


if __name__ == "__main__":
    main()
