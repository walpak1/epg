import requests
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime, timezone, timedelta
import math
import time
import re

# ==================================================
# CONFIG
# ==================================================
DAYS = 2                      # hari ini + esok
OUTPUT_FILE = "unifi.xml"
MALAYSIA_TZ = timezone(timedelta(hours=8))

BASE_URL = "https://data-store-cdn.api.tmcms.quickplay.com/content/epg"

HEADERS = {
    "Origin": "https://unifitv.com.my",
    "Referer": "https://unifitv.com.my/",
    "User-Agent": "Mozilla/5.0"
}

PARAMS_BASE = {
    "reg": "my",
    "dt": "web",
    "client": "tm-unifitv-web",
    "seg": "cohort4",
    "pf": "regular",
    "locale": "en",
    "pageSize": 100
}

ICON_URL_TEMPLATE = (
    "https://image-resizer-cloud-cdn.api.tmcms.quickplay.com/image/"
    "{cid}/0-16x9.png?width=1230&updatedTime=0&dt=Web"
)
CID_FROM_URL_PATTERN = re.compile(r"/image/([A-Za-z0-9-]+)/")

# ==================================================
# TIME RANGE (MY TIME)
# ==================================================
now_my = datetime.now(MALAYSIA_TZ)
start_my = datetime(
    now_my.year, now_my.month, now_my.day,
    0, 0, 0, tzinfo=MALAYSIA_TZ
)

print("🇲🇾 Malaysia Now:", now_my.strftime("%Y-%m-%d %H:%M:%S"))
print(
    f"🗓️ Range {DAYS} hari: "
    f"{start_my.strftime('%Y-%m-%d')} hingga "
    f"{(start_my + timedelta(days=DAYS-1)).strftime('%Y-%m-%d')}"
)

# ==================================================
# HELPERS
# ==================================================
def to_xmltv_time(iso_utc: str) -> str:
    dt = datetime.fromisoformat(iso_utc.replace("Z", ""))
    dt += timedelta(hours=8)
    return dt.strftime("%Y%m%d%H%M%S +0800")


def build_icon_url(cid: str) -> str:
    return ICON_URL_TEMPLATE.format(cid=cid)


def extract_cid_from_url(url: str) -> str:
    match = CID_FROM_URL_PATTERN.search(url or "")
    if not match:
        return ""
    return (match.group(1) or "").strip()


def pick_channel_cid(ch: dict) -> str:
    for key in ("cid", "cs", "acs"):
        value = str(ch.get(key, "")).strip()
        if value:
            return value
    return ""


def pick_programme_cid(airing: dict) -> str:
    pgm = airing.get("pgm", {})

    for key in ("cid", "cs", "acs"):
        value = str(pgm.get(key, "")).strip()
        if value:
            return value

    for key in ("img", "image", "poster", "thumbnail"):
        value = pgm.get(key)
        if isinstance(value, str):
            cid = extract_cid_from_url(value)
            if cid:
                return cid

    media_list = pgm.get("media", [])
    if isinstance(media_list, list):
        for item in media_list:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or item.get("src")
            if isinstance(url, str):
                cid = extract_cid_from_url(url)
                if cid:
                    return cid

    return ""


def fetch_airings_by_day(days: int):
    all_airings = []

    for d in range(days):
        day_start_my = start_my + timedelta(days=d)
        day_end_my = day_start_my + timedelta(days=1)

        day_start_utc = (
            day_start_my.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        day_end_utc = (
            day_end_my.astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )

        print(f"📡 Fetching EPG: {day_start_my.strftime('%Y-%m-%d')}")

        page = 1
        total_pages = 1

        while page <= total_pages:
            params = PARAMS_BASE.copy()
            params.update({
                "start": day_start_utc,
                "end": day_end_utc,
                "pageNumber": page
            })

            r = requests.get(
                BASE_URL,
                headers=HEADERS,
                params=params,
                timeout=30
            )
            r.raise_for_status()
            js = r.json()

            if page == 1:
                count = js["header"]["count"]
                rows = js["header"]["rows"]
                total_pages = math.ceil(count / rows)

            for block in js.get("data", []):
                all_airings.extend(block.get("airing", []))

            page += 1
            time.sleep(0.3)

    return all_airings


# ==================================================
# BUILD XMLTV (PRETTY)
# ==================================================
def build_xmltv():
    tv = ET.Element("tv", attrib={"generator-info-name": "unifi-epg"})

    airings = fetch_airings_by_day(DAYS)
    print(f"📡 Total airings: {len(airings)}")

    channels = {}
    programmes = []

    for a in airings:
        ch = a.get("ch", {})

        channel_key = ch.get("acs") or ch.get("cs") or ch.get("cid")
        if not channel_key:
            continue

        channel_id = channel_key
        channel_name = ch.get("lon", [{}])[0].get("n", channel_key)

        if channel_id not in channels:
            channel_cid = pick_channel_cid(ch)
            channels[channel_id] = {
                "name": channel_name,
                "icon": build_icon_url(channel_cid) if channel_cid else ""
            }

        programmes.append({
            "channel": channel_id,
            "start": to_xmltv_time(a["sc_st_dt"]),
            "stop": to_xmltv_time(a["sc_ed_dt"]),
            "title": a.get("pgm", {}).get("lon", [{}])[0].get("n", ""),
            "desc": a.get("pgm", {}).get("lod", [{}])[0].get("n", ""),
            "icon": channels[channel_id]["icon"]
        })

    # -------- CHANNELS FIRST (SORTED) --------
    for cid in sorted(channels.keys()):
        ch_el = ET.SubElement(tv, "channel", id=cid)
        ET.SubElement(ch_el, "display-name", lang="en").text = channels[cid]["name"]
        if channels[cid]["icon"]:
            ET.SubElement(ch_el, "icon", src=channels[cid]["icon"])
        ET.SubElement(ch_el, "url").text = "https://playtv.unifi.com.my"

    # -------- SORT PROGRAMMES --------
    programmes.sort(key=lambda x: (x["channel"], x["start"]))

    # -------- PROGRAMMES --------
    for p in programmes:
        pr = ET.SubElement(tv, "programme", {
            "start": p["start"],
            "stop": p["stop"],
            "channel": p["channel"]
        })
        ET.SubElement(pr, "title", lang="en").text = p["title"]
        if p["desc"]:
            ET.SubElement(pr, "desc", lang="en").text = p["desc"]
        if p["icon"]:
            ET.SubElement(pr, "icon", src=p["icon"])

    # -------- PRETTY PRINT XML --------
    rough_xml = ET.tostring(tv, encoding="utf-8")
    parsed = minidom.parseString(rough_xml)

    return parsed.toprettyxml(
        indent="  ",
        encoding="utf-8"
    )


# ==================================================
# MAIN
# ==================================================
if __name__ == "__main__":
    xml = build_xmltv()
    with open(OUTPUT_FILE, "wb") as f:
        f.write(xml)

    print(f"✅ Saved: {OUTPUT_FILE}")
