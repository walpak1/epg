import base64
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

# -----------------------
# Auth
# -----------------------
url = "https://api.vidio.com/auth"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Referer": "https://www.vidio.com/",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.9"
}

response = requests.post(url, headers=headers).json()
apikey = response["api_key"]

# contoh key & iv (guna yang sebenar)
key = b"dPr0QImQ7bc5o9LMntNba2DOsSbZcjUh"
iv = b"C8RWsrtFsoeyCyPt"

cipher = AES.new(key, AES.MODE_CBC, iv)
ciphertext = cipher.encrypt(pad(apikey.encode(), AES.block_size))
ciphertext_b64 = base64.b64encode(ciphertext).decode()

# -----------------------
# Config
# -----------------------
DAYS = 2
MALAYSIA_TZ = timezone(timedelta(hours=8))

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Content-Type": "application/vnd.api+json",
    "Origin": "https://tv.vidio.com",
    "Referer": "https://tv.vidio.com/",
    "User-Agent": "tv-android/2.49.13",
    "X-Api-Key": ciphertext_b64,
    "X-API-PLATFORM": "tv-android",
    "X-Requested-With": "com.vidio.android.tv",
    "X-Secure-Level": "2"
}

# -----------------------
# Time Range
# -----------------------
today = datetime.now(MALAYSIA_TZ)
start_of_day = today.replace(hour=0, minute=0, second=0, microsecond=0)

dates_to_fetch = [
    (start_of_day + timedelta(days=i)).strftime("%Y-%m-%d")
    for i in range(DAYS)
]

print("🇲🇾 Malaysia Now:", today.strftime("%Y-%m-%d %H:%M:%S"))
print("📅 Dates:", dates_to_fetch)

# -----------------------
# API helpers
# -----------------------
def fetch_channels():
    url = "https://api.vidio.com/livestreamings?stream_type=tv_stream"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()

    channels = []
    for item in r.json().get("data", []):
        channels.append({
            "id": item["id"],
            "name": item["attributes"]["title"],
            "icon": item["attributes"].get("square_image", {}).get("url", "")
        })
    return channels

def fetch_schedule(channel_id, date):
    url = f"https://api.vidio.com/livestreamings/{channel_id}/schedules"
    params = {"filter[date]": date}
    try:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        r.raise_for_status()
        return r.json().get("data", [])
    except Exception as e:
        print(f"⚠️ Gagal ambil {channel_id} {date}: {e}")
        return []

def format_time_iso(ts: str) -> str:
    dt = datetime.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S")
    dt = dt.replace(tzinfo=timezone(timedelta(hours=7)))  # Jakarta
    dt_my = dt.astimezone(MALAYSIA_TZ)
    return dt_my.strftime("%Y%m%d%H%M%S +0800")

# -----------------------
# XML Builder (PRETTY)
# -----------------------
def build_xmltv() -> bytes:
    tv = ET.Element("tv", attrib={"generator-info-name": "vidio-epg"})

    channels = fetch_channels()
    print(f"📺 Jumlah channel: {len(channels)}")

    # -------- CHANNELS --------
    for ch in channels:
        ch_elem = ET.SubElement(tv, "channel", id=f"vidio-{ch['id']}")
        ET.SubElement(ch_elem, "display-name", lang="id").text = ch["name"]
        if ch.get("icon"):
            ET.SubElement(ch_elem, "icon", src=ch["icon"])

    # -------- PROGRAMMES (parallel fetch) --------
    tasks = []
    ch_lookup = {ch["id"]: ch["name"] for ch in channels}

    with ThreadPoolExecutor(max_workers=15) as executor:
        for ch in channels:
            for date in dates_to_fetch:
                tasks.append(executor.submit(fetch_schedule, ch["id"], date))

        all_results = {}
        for i, future in enumerate(tasks):
            ch_id = channels[i // len(dates_to_fetch)]["id"]
            date = dates_to_fetch[i % len(dates_to_fetch)]
            schedules = future.result()
            all_results.setdefault(ch_id, []).extend(schedules)

    for ch in channels:
        schedules = all_results.get(ch["id"], [])
        for item in schedules:
            attr = item.get("attributes", {})
            start = attr.get("start_time")
            end = attr.get("end_time")
            if not start or not end:
                continue

            prog = ET.SubElement(tv, "programme", {
                "start": format_time_iso(start),
                "stop": format_time_iso(end),
                "channel": f"vidio-{ch['id']}"
            })

            ET.SubElement(prog, "title", lang="id").text = attr.get("title", "")
            if attr.get("description"):
                ET.SubElement(prog, "desc", lang="id").text = attr["description"]
            if attr.get("image_landscape_url"):
                ET.SubElement(prog, "icon", src=attr["image_landscape_url"])

        print(f"  ⏱️ {ch['name']}: {len(schedules)} programmes")

    total_programmes = sum(len(all_results.get(ch["id"], [])) for ch in channels)
    print(f"\n📊 Jumlah programme: {total_programmes}")

    # -------- PRETTY XML (MINIDOM) --------
    rough = ET.tostring(tv, encoding="utf-8")
    parsed = minidom.parseString(rough)
    return parsed.toprettyxml(indent="  ", encoding="utf-8")

# -----------------------
# Save
# -----------------------
def save_xml(content: bytes, fname="vidio.xml"):
    with open(fname, "wb") as f:
        f.write(content)
    print(f"✅ Disimpan: {fname}")

# -----------------------
# Main
# -----------------------
if __name__ == "__main__":
    xml = build_xmltv()
    save_xml(xml, "vidio.xml")
