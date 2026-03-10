import requests
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
from datetime import datetime, timezone, timedelta
import pytz
import os
import time

# -----------------------
# Config
# -----------------------
EPG_URL = "https://api.v3.singtelcast.com/v1/channels/epg/"
CHANNELS_URL = "https://api.v3.singtelcast.com/v1/channels"
PROXY = 'http://TYezHxvTVerMkKh7FYvRcK5H:T@rlgor1000T@sg-sin.pvdata.host:8080'
PROXIES = {'http': PROXY, 'https': PROXY}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "x-api-key": "weLnqiyqPWw6zQuVf9tXbpssrL2VVDTbzHiVbSnw",
    "Origin": "https://watchcast.singtel.com",
    "Referer": "https://watchcast.singtel.com/",
}


# -----------------------
# Helper: request retry
# -----------------------
def safe_request(url, retries=3, **kwargs):
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, timeout=25, **kwargs)
            r.raise_for_status()
            return r
        except Exception as e:
            print(f"⚠️ Attempt {attempt}/{retries} failed for {url}: {e}")
            if attempt < retries:
                time.sleep(3)
    raise Exception(f"❌ Failed to fetch {url} after {retries} attempts")


# -----------------------
# Channel list (with proper mapping)
# -----------------------
def fetch_free_channels():
    r = safe_request(CHANNELS_URL, headers=HEADERS, proxies=PROXIES)
    data = r.json().get("data", [])
    # hanya yang ada epgChannelId
    return [c for c in data if 'epgChannelId' in c]


# -----------------------
# Build multi EPG (5 × 6 jam)
# -----------------------
def build_epg_multi():
    tz = pytz.timezone("Asia/Kuala_Lumpur")
    now_local = datetime.now(tz)
    base_hour = (now_local.hour // 6) * 6
    start_local = now_local.replace(hour=base_hour, minute=0, second=0, microsecond=0)

    print("🚀 Building rolling EPG fragment...\n")
    print(f"🕒 Malaysia/Singapore Now: {now_local.strftime('%Y-%m-%d %H:%M:%S')}")

    tv = ET.Element("tv")

    # ✅ Channel map
    channels = fetch_free_channels()
    channel_map = {}
    for ch in channels:
        title = ch.get("title", f"Channel-{ch.get('number')}").strip()
        epg_id = ch.get("epgChannelId")
        thumbnail = ch.get("thumbnailUrl", "")
        channel_map[epg_id] = {"title": title, "icon": thumbnail}

        ch_elem = ET.SubElement(tv, "channel", id=title)
        ET.SubElement(ch_elem, "display-name").text = title
        if thumbnail:
            ET.SubElement(ch_elem, "icon", src=thumbnail)

    total_added = 0

    # 🔁 loop 5 windows (6h × 5 = 30 jam)
    for i in range(5):
        start = start_local + timedelta(hours=i * 6)
        end = start + timedelta(hours=6) - timedelta(seconds=1)

        start_utc = start - timedelta(hours=8)
        end_utc = end - timedelta(hours=8)

        print("\n───────────────────────────────")
        print(f"🗓️ Window {i+1} (Local): {start.strftime('%Y-%m-%d %H:%M:%S')} → {end.strftime('%H:%M:%S')}")
        print(f"🌐 UTC window: {start_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z')} → {end_utc.strftime('%Y-%m-%dT%H:%M:%S.999Z')}")

        url = (
            f"{EPG_URL}?offset=0&limit=10000"
            f"&startdate={start_utc.strftime('%Y-%m-%dT%H:%M:%S.000Z')}"
            f"&enddate={end_utc.strftime('%Y-%m-%dT%H:%M:%S.999Z')}"
        )

        r = safe_request(url, headers=HEADERS, proxies=PROXIES)
        progs = r.json().get("data", [])
        print(f"📡 {len(progs)} programmes fetched.")

        for prog in progs:
            epg_id = prog.get("epgChannelId")
            if epg_id not in channel_map:
                continue
            channel_info = channel_map[epg_id]
            channel = channel_info["title"]
            icon_src = channel_info["icon"]

            start_iso = prog.get("startDate")
            if not start_iso:
                continue
            try:
                start_dt = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
            except Exception:
                continue
            duration = prog.get("duration", 0)
            stop_dt = start_dt + timedelta(seconds=duration)

            prog_elem = ET.SubElement(tv, "programme", {
                "start": start_dt.strftime("%Y%m%d%H%M%S +0000"),
                "stop": stop_dt.strftime("%Y%m%d%H%M%S +0000"),
                "channel": channel
            })
            ET.SubElement(prog_elem, "title", lang="en").text = prog.get("title", "")
            if prog.get("subtitle"):
                ET.SubElement(prog_elem, "sub-title", lang="en").text = prog["subtitle"]
            if prog.get("description"):
                ET.SubElement(prog_elem, "desc", lang="en").text = prog["description"]
            if icon_src:
                ET.SubElement(prog_elem, "icon", src=icon_src)
            total_added += 1

        time.sleep(1)

    print(f"\n📊 Added total {total_added} programmes across 5 windows (30h fragment)")
    return tv


# -----------------------
# Merge 24h rolling XML
# -----------------------
def merge_xml(existing_path, new_tv):
    if not os.path.exists(existing_path):
        return new_tv

    try:
        old_tree = ET.parse(existing_path)
        old_root = old_tree.getroot()
    except Exception:
        print("⚠️ Existing file corrupt or unreadable, starting fresh.")
        return new_tv

    existing_channels = {ch.attrib["id"] for ch in old_root.findall("channel")}
    for ch in new_tv.findall("channel"):
        if ch.attrib["id"] not in existing_channels:
            old_root.append(ch)

    old_programs = {
        (p.attrib["start"], p.attrib["channel"]) for p in old_root.findall("programme")
    }
    for prog in new_tv.findall("programme"):
        key = (prog.attrib["start"], prog.attrib["channel"])
        if key not in old_programs:
            old_root.append(prog)

    now_epoch = datetime.now(timezone.utc).timestamp()
    cutoff = now_epoch + 24 * 3600
    for prog in list(old_root.findall("programme")):
        dt_stop = datetime.strptime(prog.attrib["stop"][:14], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        if dt_stop.timestamp() < now_epoch or dt_stop.timestamp() > cutoff:
            old_root.remove(prog)

    return old_root


# -----------------------
# Save XML
# -----------------------
def save_xml(tree, fname="singtel.xml"):
    xml_str = ET.tostring(tree, encoding="unicode", method="xml")
    dom = minidom.parseString(xml_str)
    pretty_xml = dom.toprettyxml(indent="  ")
    # Remove extra blank lines to clean up spacing
    import re
    cleaned_xml = re.sub(r'\n\s*\n', '\n', pretty_xml)
    with open(fname, "w", encoding="utf-8") as f:
        f.write(cleaned_xml)
    print(f"✅ Saved: {fname}")


# -----------------------
# Run
# -----------------------
fragment = build_epg_multi()
merged = merge_xml("singtel.xml", fragment)
save_xml(merged, "singtel.xml")
