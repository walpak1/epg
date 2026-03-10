import requests
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime, timezone, timedelta
import time

# -----------------------
# Config
# -----------------------
DAYS = 2   # hari ni + esok
MALAYSIA_TZ = timezone(timedelta(hours=8))

# -----------------------
# Time Range
# -----------------------
today = datetime.now(MALAYSIA_TZ)
start_of_day = int(
    datetime(
        today.year, today.month, today.day,
        0, 0, 0, tzinfo=MALAYSIA_TZ
    ).timestamp()
)
end_of_day = int(
    (
        datetime(
            today.year, today.month, today.day,
            23, 59, 59, tzinfo=MALAYSIA_TZ
        ) + timedelta(days=DAYS - 1)
    ).timestamp()
)

print("🇲🇾 Malaysia Now:", today.strftime("%Y-%m-%d %H:%M:%S"))
print(
    f"🗓️ Range {DAYS} hari:",
    datetime.fromtimestamp(start_of_day, MALAYSIA_TZ).strftime("%Y-%m-%d %H:%M:%S"),
    "→",
    datetime.fromtimestamp(end_of_day, MALAYSIA_TZ).strftime("%Y-%m-%d %H:%M:%S")
)

# -----------------------
# API
# -----------------------
CHANNELS_URL = "https://waf-starhub-metadata-api-p001.ifs.vubiquity.com/v3.1/epg/channels"
SCHEDULE_URL = "https://waf-starhub-metadata-api-p001.ifs.vubiquity.com/v3.1/epg/schedules"

def fetch_channels():
    params = {
        "locale": "en-GB",
        "locale_default": "en_US",
        "device": "200",
        "limit": "200",
        "page": "0"
    }
    r = requests.get(CHANNELS_URL, params=params, timeout=20)
    r.raise_for_status()
    return r.json().get("resources", [])

def fetch_schedule(channel_id):
    try:
        params = {
            "locale": "en-GB",
            "locale_default": "en_US",
            "device": 2,
            "in_channel_id": channel_id,
            "gt_end": start_of_day,
            "lt_start": end_of_day,
            "limit": 500,
            "page": 0,
        }
        r = requests.get(SCHEDULE_URL, params=params, timeout=20)
        r.raise_for_status()
        return r.json().get("resources", [])
    except Exception as e:
        print(f"⚠️ Gagal ambil jadual untuk {channel_id}: {e}")
        return []

def format_time_epoch(epoch: int) -> str:
    dt_utc = datetime.fromtimestamp(epoch, tz=timezone.utc)
    dt_my = dt_utc.astimezone(MALAYSIA_TZ)
    return dt_my.strftime("%Y%m%d%H%M%S +0800")

# -----------------------
# BUILD XMLTV (PRETTY)
# -----------------------
def build_xmltv():
    tv = ET.Element("tv", attrib={"generator-info-name": "starhub-epg"})

    channels = fetch_channels()
    print(f"📺 Jumlah channel: {len(channels)}")

    # -------- CHANNELS --------
    for ch in channels:
        cid = ch.get("id")
        title = ch.get("title", f"Channel-{cid}")
        icons = ch.get("pictures", [])
        icon_url = icons[0]["url"] if icons else ""

        channel_xml_id = f"{title.replace(' ', '').replace('-', '')}.sg"

        ch_elem = ET.SubElement(tv, "channel", id=channel_xml_id)
        ET.SubElement(ch_elem, "display-name", lang="en").text = title
        if icon_url:
            ET.SubElement(ch_elem, "icon", src=icon_url)

    # -------- PROGRAMMES --------
    for ch in channels:
        cid = ch.get("id")
        title = ch.get("title", "")
        channel_xml_id = f"{title.replace(' ', '').replace('-', '')}.sg"

        schedules = fetch_schedule(cid) or []
        if not schedules:
            print(f"⚠️ Tiada jadual untuk {title}")
            continue

        print(f"  ⏱️ {title}: {len(schedules)} programmes")

        for s in schedules:
            prog = ET.SubElement(tv, "programme", {
                "start": format_time_epoch(s["start"]),
                "stop": format_time_epoch(s["end"]),
                "channel": channel_xml_id
            })
            ET.SubElement(prog, "title", lang="en").text = s.get("title", "")
            if s.get("description"):
                ET.SubElement(prog, "desc", lang="en").text = s["description"]
            if s.get("pictures"):
                icon_src = s["pictures"][0]["url"].replace("?w=341&h=192", "?w=960&h=540")
                ET.SubElement(prog, "icon", src=icon_src)

        time.sleep(0.5)

    # -------- PRETTY PRINT (MINIDOM) --------
    rough_xml = ET.tostring(tv, encoding="utf-8")
    parsed = minidom.parseString(rough_xml)

    return parsed.toprettyxml(
        indent="  ",
        encoding="utf-8"
    )

def save_xml(content, fname="starhub.xml"):
    with open(fname, "wb") as f:
        f.write(content)
    print(f"✅ Disimpan: {fname}")

# -----------------------
# MAIN
# -----------------------
if __name__ == "__main__":
    xml = build_xmltv()
    save_xml(xml, "starhub.xml")
