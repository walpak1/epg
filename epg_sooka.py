import time
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional
import requests
import os

# -----------------------
# Config
# -----------------------
DAYS = 2
MALAYSIA_TZ = timezone(timedelta(hours=8))
USER_AGENT = "com.astro.sott/12635023 (Linux; Android 14; en_US; AOSP TV on x86; Build/UTT1.240131.001.F1)"

proxy_config = {
    'http': 'http://TYezHxvTVerMkKh7FYvRcK5H:T@rlgor1000T@id-jak.pvdata.host:8080',
    'https': 'http://TYezHxvTVerMkKh7FYvRcK5H:T@rlgor1000T@id-jak.pvdata.host:8080'
}

CHANNEL_ID_OVERRIDES = {
    "tvN Movies HD": "tvNMoviesHD"
}

# -----------------------
# Helpers
# -----------------------
def normalize_channel_id(title: str) -> str:
    base = "".join(ch for ch in title if ch.isalnum())
    return CHANNEL_ID_OVERRIDES.get(title) or (base if base else "Channel")

def epoch_to_xmltv(epoch_ms: int) -> str:
    dt_utc = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc)
    dt_my = dt_utc.astimezone(MALAYSIA_TZ)
    dt_my = dt_my.replace(second=0, microsecond=0)
    return dt_my.strftime("%Y%m%d%H%M%S +0800")

def best_channel_icon(item: dict) -> Optional[str]:
    for k in ("channelLogo", "boxCoverLogo", "boxCoverImage"):
        if item.get(k):
            return item[k]
    return None

def best_event_icon(evt: dict) -> Optional[str]:
    for k in ("boxCoverLogo", "boxCoverImage", "thumbnailImage", "posterImage"):
        if evt.get(k) and evt[k].startswith("http"):
            return evt[k]
    return None

def build_headers_base():
    return {
        "accept": "application/json, text/plain, */*",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "environmentcode": "MAIN",
        "Host": "api.vr.ctrp.sooka.my",
        "language": "eng",
        "local": "MYS",
        "platform": "SOOKA_ATV",
        "User-Agent": USER_AGENT,
    }

# -----------------------
# API
# -----------------------
def fetch_mybox(cdn_key: str, date_str: str) -> List[dict]:
    url = "https://api.vr.ctrp.sooka.my/content-detail-service/pub/v1/mybox"
    headers = build_headers_base()
    headers["x-api-key"] = cdn_key
    params = {"date": date_str, "offset": 0, "limit": 200}

    r = requests.get(url, headers=headers, params=params, timeout=30, proxies=proxy_config)
    r.raise_for_status()
    js = r.json()

    if not js.get("status"):
        return []

    return js.get("data", {}).get("meta", {}).get("meta", []) or []

# -----------------------
# XML Builder
# -----------------------
def build_xmltv() -> bytes:
    today_my = datetime.now(MALAYSIA_TZ)
    print("🇲🇾 Malaysia Now:", today_my.strftime("%Y-%m-%d %H:%M:%S"))

    cfg_url = "https://api.vr.ctrp.sooka.my/config-service/pub/v1/platform-configs"
    cfg_headers = {
        "platform": "SOOKA_ATV",
        "User-Agent": USER_AGENT
    }

    cfg = requests.get(cfg_url, headers=cfg_headers, proxies=proxy_config, timeout=30).json()
    cdn_key = cfg["data"][0]["data"]["cdnAuthKey"]

    tv = ET.Element("tv", attrib={"generator-info-name": "sooka-epg"})
    emitted_channels: Dict[str, str] = {}

    for d in range(DAYS):
        date_dt = today_my + timedelta(days=d)
        date_str = date_dt.strftime("%d-%m-%Y")
        print(f"📅 Fetching {date_str}")

        items = fetch_mybox(cdn_key, date_str)
        channels = [x for x in items if x.get("contentType") == "channel"]

        # -------- CHANNELS --------
        for ch in channels:
            ch_id = ch.get("contentId") or ch.get("vrContentId") or ch.get("id")
            if ch_id in emitted_channels:
                continue

            title = ch.get("title") or ch.get("defaultTitle") or "Channel"
            xml_id = normalize_channel_id(title)
            emitted_channels[ch_id] = xml_id

            ce = ET.SubElement(tv, "channel", id=xml_id)
            ET.SubElement(ce, "display-name", lang="en").text = title

            icon = best_channel_icon(ch)
            if icon:
                ET.SubElement(ce, "icon", src=icon)

        # -------- PROGRAMMES --------
        for ch in channels:
            ch_id = ch.get("contentId") or ch.get("vrContentId") or ch.get("id")
            xml_id = emitted_channels.get(ch_id)
            if not xml_id:
                continue

            events = ch.get("channelDay") or []
            events.sort(key=lambda e: e.get("eventStartUtc") or 0)

            for evt in events:
                start_ms = evt.get("eventStartUtc")
                end_ms = evt.get("eventEndUtc")
                if not start_ms or not end_ms:
                    dur = evt.get("duration")
                    if dur:
                        end_ms = start_ms + dur * 1000
                    else:
                        continue

                p = ET.SubElement(tv, "programme", {
                    "start": epoch_to_xmltv(start_ms),
                    "stop": epoch_to_xmltv(end_ms),
                    "channel": xml_id
                })

                ET.SubElement(p, "title", lang="en").text = evt.get("title") or ""
                desc = evt.get("description") or evt.get("shortDescription")
                if desc:
                    ET.SubElement(p, "desc", lang="en").text = desc[:200]

                icon = best_event_icon(evt)
                if icon:
                    ET.SubElement(p, "icon", src=icon)

        time.sleep(0.2)

    # -------- PRETTY XML (MINIDOM FIXED) --------
    rough = ET.tostring(tv, encoding="utf-8")
    parsed = minidom.parseString(rough)

    # buang whitespace text node (ini yang selalu rosakkan indent)
    for node in parsed.childNodes:
        if node.nodeType == node.TEXT_NODE and not node.data.strip():
            node.data = ""

    pretty = parsed.toprettyxml(
        indent="  ",
        newl="\n",
        encoding="utf-8"
    )

    return pretty

# -----------------------
# Save
# -----------------------
def save_xml(content: bytes, fname="sooka.xml"):
    with open(fname, "wb") as f:
        f.write(content)
    print(f"✅ Saved: {fname}")
    print("📦 File size:", os.path.getsize(fname))

# -----------------------
# Main
# -----------------------
if __name__ == "__main__":
    xml = build_xmltv()
    save_xml(xml, "sooka.xml")


