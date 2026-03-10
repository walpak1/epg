import requests
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime, timezone, timedelta
from time import sleep
import os

# -----------------------
# Config
# -----------------------
DAYS = 2
MALAYSIA_TZ = timezone(timedelta(hours=8))

USE_PROXY = True
proxy_config = {
    'http': 'http://TYezHxvTVerMkKh7FYvRcK5H:T@rlgor1000T@my-kua.pvdata.host:8080',
    'https': 'http://TYezHxvTVerMkKh7FYvRcK5H:T@rlgor1000T@my-kua.pvdata.host:8080'
} if USE_PROXY else None

# -----------------------
# Time window
# -----------------------
today = datetime.now(MALAYSIA_TZ)
start_of_day = int(
    datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=MALAYSIA_TZ).timestamp()
) * 1000
end_of_day = int(
    (
        datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=MALAYSIA_TZ)
        + timedelta(days=DAYS - 1)
    ).timestamp()
) * 1000

def xmltv_time_ms(ms_epoch: int) -> str:
    dt = datetime.fromtimestamp(ms_epoch / 1000, tz=MALAYSIA_TZ)
    return dt.strftime("%Y%m%d%H%M%S +0800")

def mana2_channel_id_from_title(title: str) -> str:
    base = "".join(ch for ch in title if ch.isalnum())
    return f"{base}.mana2"

def req_get(url, **kwargs):
    for i in range(3):
        try:
            r = requests.get(
                url,
                timeout=kwargs.pop("timeout", 30),
                proxies=proxy_config,
                **kwargs
            )
            r.raise_for_status()
            return r
        except Exception:
            if i == 2:
                raise
            sleep(1 + i)

# -----------------------
# Auth / Headers
# -----------------------
token_resp = req_get(
    "https://mytv-api.revlet.net/service/api/v1/get/token",
    params={
        "tenant_code": "mytv",
        "box_id": "a67f6063-aa4f-f31f-c1bd-be8155fbb0e5",
        "product": "mytv",
        "device_id": "5",
        "display_lang_code": "ENG",
        "device_sub_type": "Chrome,140.0.0.0,Windows",
        "timezone": "Asia/Kuala_Lumpur",
    },
).json()

sid = token_resp["response"]["sessionId"]

baseheaders = {
    "Host": "mytv-api.revlet.net",
    "Connection": "keep-alive",
    "Box-Id": "a67f6063-aa4f-f31f-c1bd-be8155fbb0e5",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "application/json, text/plain, */*",
    "Session-Id": sid,
    "Tenant-Code": "mytv",
    "Origin": "https://www.mana2.my",
    "Referer": "https://www.mana2.my/",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.9",
}

# -----------------------
# Fetch channels
# -----------------------
channels_url = "https://mytv-api.revlet.net/service/api/v1/tvguide/channels"
tabs_resp = req_get(channels_url, headers=baseheaders).json()
data_items = tabs_resp.get("response", {}).get("data", [])
if not data_items:
    raise SystemExit("No channels returned")

ids = []
channel_map = {}

for item in data_items:
    cid = item.get("id")
    display = item.get("display") or {}
    title = display.get("title") or f"Channel-{cid}"
    raw_img = display.get("imageUrl") or ""
    img_url = raw_img.replace("common,", "common/")

    xml_id = mana2_channel_id_from_title(title)
    ids.append(str(cid))
    channel_map[str(cid)] = (title, img_url, xml_id)

channel_ids_str = ",".join(ids)

print(f"📺 Channels fetched: {len(ids)}")

# -----------------------
# Fetch schedules
# -----------------------
url = "https://mytv-api.revlet.net/service/api/v1/static/tvguide"
params = {
    "start_time": str(start_of_day),
    "end_time": str(end_of_day),
    "page": "0",
    "channel_ids": channel_ids_str,
}
resp = req_get(url, params=params, headers=baseheaders, timeout=60).json()

# -----------------------
# Build XMLTV
# -----------------------
tv = ET.Element("tv", attrib={"generator-info-name": "mana2-epg"})

# Channels
for _, (title, icon_url, xml_id) in channel_map.items():
    ch_elem = ET.SubElement(tv, "channel", id=xml_id)
    ET.SubElement(ch_elem, "display-name", lang="en").text = title
    if icon_url:
        if not icon_url.startswith("http"):
            icon_url = "https://d229kpbsb5jevy.cloudfront.net/mytv/content/" + icon_url.lstrip("/")
        ET.SubElement(ch_elem, "icon", src=icon_url)

# Programmes
prog_count = 0
for ch in resp.get("response", {}).get("data") or []:
    api_channel_id = str(ch.get("channelId", ""))
    if api_channel_id not in channel_map:
        continue
    _, _, xml_id = channel_map[api_channel_id]

    for p in ch.get("programs") or []:
        disp = p.get("display") or {}
        title = disp.get("title") or ""
        desc = disp.get("subtitle2") or disp.get("subtitle1") or ""
        markers = disp.get("markers") or {}

        start_ms = int(
            markers.get("startTime", {}).get("value", 0)
            or p.get("target", {}).get("pageAttributes", {}).get("startTime", 0)
        )
        end_ms = int(
            markers.get("endTime", {}).get("value", 0)
            or p.get("target", {}).get("pageAttributes", {}).get("endTime", 0)
        )
        if not start_ms or not end_ms:
            continue

        prog = ET.SubElement(tv, "programme", {
            "start": xmltv_time_ms(start_ms),
            "stop": xmltv_time_ms(end_ms),
            "channel": xml_id
        })
        ET.SubElement(prog, "title", lang="en").text = title
        if desc:
            ET.SubElement(prog, "desc", lang="en").text = desc
        if disp.get("imageUrl"):
            icon_src = disp["imageUrl"]
            if not icon_src.startswith("http"):
                icon_src = "https://d229kpbsb5jevy.cloudfront.net/mytv/content/" + icon_src.replace("common,", "common/").lstrip("/")
            ET.SubElement(prog, "icon", src=icon_src)

        prog_count += 1

# -----------------------
# Save XML (MINIDOM)
# -----------------------
rough = ET.tostring(tv, encoding="utf-8")
parsed = minidom.parseString(rough)
pretty = parsed.toprettyxml(indent="  ", encoding="utf-8")

with open("mana2.xml", "wb") as f:
    f.write(pretty)

print(f"✅ Disimpan: mana2.xml")
print(f"📦 Total programmes: {prog_count}")
