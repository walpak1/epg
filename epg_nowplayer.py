import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from xml.dom import minidom

import requests


BASE_URL = "https://catalogapi.nowtv.now.com/"
CHANNELS_URL = BASE_URL + "CatalogEngine/getLiveChannelList"
EPG_URL = BASE_URL + "CatalogEngine/getEPGDetail"

DAYS = 2
CHUNK_SIZE = 50
HK_TZ = timezone(timedelta(hours=8))
DEVICE_ID = "34e9507e-33e9-319f-baea-c2ebad9e8fd3"

HEADERS = {
    "BaseUrlName": BASE_URL,
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 16; AOSP TV on x86 Build/BT2A.251018.001.A1; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/148.0.7778.120 "
        "Mobile Safari/537.36"
    ),
    "Content-Type": "application/json; charset=UTF-8",
    "Host": "catalogapi.nowtv.now.com",
    "Connection": "Keep-Alive",
    "Accept-Encoding": "gzip",
}

session = requests.Session()
session.headers.update(HEADERS)


def caller_reference_no() -> str:
    return f"{DEVICE_ID}---{int(time.time() * 1000)}"


def clean_text(value) -> str:
    return str(value or "").strip()


def xml_channel_id(name: str, used_ids: set[str]) -> str:
    base = re.sub(r"\s+", "", name).strip(".") or "unknown"
    candidate = f"{base}.nowplayer"
    if candidate not in used_ids:
        used_ids.add(candidate)
        return candidate

    index = 2
    while f"{base}{index}.nowplayer" in used_ids:
        index += 1

    candidate = f"{base}{index}.nowplayer"
    used_ids.add(candidate)
    return candidate


def channel_icon(channel_id: str) -> str:
    if not channel_id:
        return ""
    return f"https://images.now-tv.com/shares/channelPreview/img/en_hk/color/ch{channel_id}_1600_1150"


def fetch_channels():
    payload = {
        "appId": "15",
        "callerReferenceNo": caller_reference_no(),
        "deviceId": DEVICE_ID,
        "lang": "en_us",
        "secureCookie": "",
        "token": "",
    }
    response = session.post(CHANNELS_URL, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()

    channels = []
    used_xml_ids = set()
    seen_channel_ids = set()

    for item in data.get("channelList", []):
        channel_id = clean_text(item.get("channelId"))
        name = clean_text(item.get("name"))
        if not channel_id or not name or channel_id in seen_channel_ids:
            continue

        seen_channel_ids.add(channel_id)
        channels.append(
            {
                "id": channel_id,
                "name": name,
                "xml_id": xml_channel_id(name, used_xml_ids),
                "icon": channel_icon(channel_id),
            }
        )

    return channels


def chunks(items, size):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def fetch_epg(channel_ids):
    all_epg = {}

    for channel_id_chunk in chunks(channel_ids, CHUNK_SIZE):
        payload = {
            "callerReferenceNo": caller_reference_no(),
            "channelIdList": channel_id_chunk,
            "endDay": DAYS - 1,
            "lang": "en_us",
            "startDay": 0,
        }
        response = session.post(EPG_URL, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()

        for detail in data.get("epgDetail", []):
            channel_id = clean_text(detail.get("channelId"))
            all_epg.setdefault(channel_id, []).extend(detail.get("programs") or [])

    return all_epg


def format_time(epoch_ms) -> str:
    timestamp = int(epoch_ms) / 1000
    return datetime.fromtimestamp(timestamp, HK_TZ).strftime("%Y%m%d%H%M%S +0800")


def build_xmltv() -> bytes:
    tv = ET.Element("tv", attrib={"generator-info-name": "nowplayer-epg"})
    channels = fetch_channels()

    for channel in channels:
        channel_elem = ET.SubElement(tv, "channel", id=channel["xml_id"])
        ET.SubElement(channel_elem, "display-name", lang="en").text = channel["name"]
        if channel["icon"]:
            ET.SubElement(channel_elem, "icon", src=channel["icon"])

    epg_by_channel = fetch_epg([channel["id"] for channel in channels])
    total_programmes = 0

    for channel in channels:
        seen_programmes = set()
        for program in epg_by_channel.get(channel["id"], []):
            title = clean_text(program.get("name"))
            start = program.get("start")
            stop = program.get("end")
            if not title or not start or not stop:
                continue

            programme_key = (start, stop, title)
            if programme_key in seen_programmes:
                continue
            seen_programmes.add(programme_key)

            programme = ET.SubElement(
                tv,
                "programme",
                {
                    "start": format_time(start),
                    "stop": format_time(stop),
                    "channel": channel["xml_id"],
                },
            )
            ET.SubElement(programme, "title", lang="en").text = title

            description = clean_text(program.get("cid"))
            if description and description.lower() != title.lower():
                ET.SubElement(programme, "desc", lang="en").text = description

            if channel["icon"]:
                ET.SubElement(programme, "icon", src=channel["icon"])

            total_programmes += 1

    print(f"Jumlah channel: {len(channels)}")
    print(f"Jumlah hari: {DAYS}")
    print(f"Jumlah programme: {total_programmes}")

    rough = ET.tostring(tv, encoding="utf-8")
    parsed = minidom.parseString(rough)
    return parsed.toprettyxml(indent="  ", encoding="utf-8")


def save_xml(content: bytes, filename="nowplayer.xml"):
    with open(filename, "wb") as file_handle:
        file_handle.write(content)
    print(f"Disimpan: {filename}")


if __name__ == "__main__":
    xml = build_xmltv()
    save_xml(xml)
