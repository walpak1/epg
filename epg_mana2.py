import requests
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime, timezone, timedelta
from time import sleep
import o11
import epg_tonton, epg_rtmklik

USE_DOH = False
DOH_URL = "https://dns.controld.com/1rmnvexb4iv"
if USE_DOH:
    o11.dns(DOH_URL)
DAYS = 2
MALAYSIA_TZ = timezone(timedelta(hours=8))

USE_PROXY = True
PROXY = "http://TYezHxvTVerMkKh7FYvRcK5H:T@rlgor1000T@my-kua.pvdata.host:8080"

if USE_PROXY:
    PROXIES = {"http": PROXY, "https": PROXY}
else:
    PROXIES = None

session = requests.Session()
today = datetime.now(MALAYSIA_TZ)
start_of_day = int(datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=MALAYSIA_TZ).timestamp()) * 1000
end_of_day = int((datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=MALAYSIA_TZ) + timedelta(days=DAYS - 1)).timestamp()) * 1000


def xmltv_time_ms(ms_epoch: int) -> str:
    return datetime.fromtimestamp(ms_epoch / 1000, tz=MALAYSIA_TZ).strftime("%Y%m%d%H%M%S +0800")


def mana2_channel_id_from_title(title: str) -> str:
    return f"{''.join(ch for ch in title if ch.isalnum())}.mana2"


def req_get(url, **kwargs):
    timeout = kwargs.pop("timeout", 30)
    for i in range(3):
        try:
            r = session.get(url, timeout=timeout, proxies=PROXIES, **kwargs)
            r.raise_for_status()
            return r
        except Exception:
            if i == 2:
                raise
            sleep(1 + i)


def main():
    print("Malaysia Now:", today.strftime("%Y-%m-%d %H:%M:%S"))
    print(f"Range {DAYS} hari:", datetime.fromtimestamp(start_of_day / 1000, MALAYSIA_TZ).strftime("%Y-%m-%d %H:%M:%S"), "->", datetime.fromtimestamp(end_of_day / 1000, MALAYSIA_TZ).strftime("%Y-%m-%d %H:%M:%S"))

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

    data_items = req_get("https://mytv-api.revlet.net/service/api/v1/tvguide/channels", headers=baseheaders).json().get("response", {}).get("data", [])
    if not data_items:
        raise SystemExit("No channels returned")

    channel_map = {}
    ids = []
    for item in data_items:
        cid = item.get("id")
        display = item.get("display") or {}
        title = display.get("title") or f"Channel-{cid}"
        icon_url = (display.get("imageUrl") or "").replace("common,", "common/")
        xml_id = mana2_channel_id_from_title(title)
        ids.append(str(cid))
        channel_map[str(cid)] = (title, icon_url, xml_id)

    resp = req_get(
        "https://mytv-api.revlet.net/service/api/v1/static/tvguide",
        params={"start_time": str(start_of_day), "end_time": str(end_of_day), "page": "0", "channel_ids": ",".join(ids)},
        headers=baseheaders,
        timeout=60,
    ).json()

    tv = ET.Element("tv", attrib={"generator-info-name": "mana2-epg"})
    for _, (title, icon_url, xml_id) in channel_map.items():
        ch_elem = ET.SubElement(tv, "channel", id=xml_id)
        ET.SubElement(ch_elem, "display-name", lang="en").text = title
        if icon_url:
            if not icon_url.startswith("http"):
                icon_url = "https://d229kpbsb5jevy.cloudfront.net/mytv/content/" + icon_url.lstrip("/")
            ET.SubElement(ch_elem, "icon", src=icon_url)

    prog_count = 0
    channels_with_programmes = set()
    for ch in resp.get("response", {}).get("data") or []:
        api_channel_id = str(ch.get("channelId", ""))
        if api_channel_id not in channel_map:
            continue
        _, _, xml_id = channel_map[api_channel_id]

        for p in ch.get("programs") or []:
            disp = p.get("display") or {}
            markers = disp.get("markers") or {}
            start_ms = int(markers.get("startTime", {}).get("value", 0) or p.get("target", {}).get("pageAttributes", {}).get("startTime", 0))
            end_ms = int(markers.get("endTime", {}).get("value", 0) or p.get("target", {}).get("pageAttributes", {}).get("endTime", 0))
            if not start_ms or not end_ms:
                continue

            prog = ET.SubElement(tv, "programme", {"start": xmltv_time_ms(start_ms), "stop": xmltv_time_ms(end_ms), "channel": xml_id})
            ET.SubElement(prog, "title", lang="en").text = disp.get("title") or ""
            desc = disp.get("subtitle2") or disp.get("subtitle1") or ""
            if desc:
                ET.SubElement(prog, "desc", lang="en").text = desc
            if disp.get("imageUrl"):
                icon_src = disp["imageUrl"]
                if not icon_src.startswith("http"):
                    icon_src = "https://d229kpbsb5jevy.cloudfront.net/mytv/content/" + icon_src.replace("common,", "common/").lstrip("/")
                ET.SubElement(prog, "icon", src=icon_src)

            prog_count += 1
            channels_with_programmes.add(api_channel_id)

    failed_channel_names = [
        meta[0]
        for channel_id, meta in channel_map.items()
        if channel_id not in channels_with_programmes
    ]

    print("Tambah tonton EPG...")
    tonton_stats = epg_tonton.populate(tv)

    print("Tambah RTMKlik EPG...")
    rtm_stats = epg_rtmklik.populate(tv)

    children = list(tv)
    for child in children:
        tv.remove(child)
    for child in children:
        if child.tag == "channel":
            tv.append(child)
    for child in children:
        if child.tag == "programme":
            tv.append(child)

    pretty = minidom.parseString(ET.tostring(tv, encoding="utf-8")).toprettyxml(indent="  ", encoding="utf-8")
    with open("mana2.xml", "wb") as f:
        f.write(pretty)

    print(f"Jumlah channel mana2: {len(channel_map)} | tonton: {tonton_stats['channel_count']} | rtmklik: {rtm_stats['channel_count']}")
    print(f"Jumlah hari: {DAYS}")
    print(f"Jumlah programme mana2: {prog_count} | tonton: {tonton_stats['prog_count']} | rtmklik: {rtm_stats['prog_count']}")
    print(f"Channel gagal mana2: {len(failed_channel_names)}")
    for title in failed_channel_names:
        print(f"- {title}")
    print(f"Channel gagal tonton: {len(tonton_stats['failed'])}")
    for title in tonton_stats['failed']:
        print(f"- {title}")
    print(f"Channel gagal rtmklik: {len(rtm_stats['failed'])}")
    for title in rtm_stats['failed']:
        print(f"- {title}")
    print("Disimpan: mana2.xml")


if __name__ == "__main__":
    main()
