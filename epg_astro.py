from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse
import xml.etree.ElementTree as ET
from xml.dom import minidom
# from astrosmart import authorization
import requests

AUTHORIZATION_URL = (
    "https://sg-sg-sg.astro.com.my:9443/oauth2/authorize?client_id=browser&state=bootup&redirect_uri"
    "=https%3A%2F%2Fastrogo.astro.com.my&response_type=token&prompt=none"
)
CHANNELS_URL = "https://sg-sg-sg.astro.com.my:9443/ctap/r1.6.0/channels"
SCHEDULE_URL = "https://api-ivp.astro.com.my/ctap/r1.6.0/shared/grid"

# Tukar ikut keperluan.
DAYS = 2  # 1 = hari ni, 2 = hari ni + esok
GRID_WINDOW_HOURS = 24
SCHEDULE_PAGE_LIMIT = 200
OUTPUT_XML = "astro.xml"
MALAYSIA_TZ = timezone(timedelta(hours=8))
DEFAULT_CLIENT_TOKEN = (
    "v:1!r:80300!ur:SABAH!community:Malaysia Live!t:k!dt:STB"
)
FALLBACK_CLIENT_TOKENS = [
    # Removed fallback tokens as per request
]

REQUEST_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;"
        "q=0.8,application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": (
        "device_id=ba3e53b9-219c-4d9f-ae77-a5bbc1cbb1a9; "
        "browser_pickled_session=86373642.ba3e53b9-219c-4d9f-ae77-a5bbc1cbb1a9"
    ),
    "Referer": "https://astrogo.astro.com.my/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
}


def get_bearer_token() -> str:
    response = requests.get(
        AUTHORIZATION_URL,
        headers=REQUEST_HEADERS,
        allow_redirects=False,
        timeout=30,
    )
    response.raise_for_status()

    redirect_location = response.headers.get("Location", "")
    parsed_redirect_url = urlparse(redirect_location)
    query_parameters = parse_qs(parsed_redirect_url.fragment)
    access_token = query_parameters.get("access_token", [None])[0]

    if not access_token:
        raise RuntimeError("Tak jumpa access_token dalam redirect response.")

    return f"Bearer {access_token}"


def format_time_utc_to_my(xml_time: str) -> str:
    # Input API biasa: 2026-02-24T22:00:00.000Z
    dt_utc = parse_astro_datetime(xml_time)
    dt_my = dt_utc.astimezone(MALAYSIA_TZ)
    return dt_my.strftime("%Y%m%d%H%M%S +0800")


def parse_astro_datetime(value: str) -> datetime:
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"Format tarikh tak dikenali: {value}")


def pick_channel_icon(channel_data: dict) -> str:
    media_list = channel_data.get("media") or []
    for item in media_list:
        if item.get("type") == "regular" and item.get("url"):
            return item["url"]
    for item in media_list:
        if item.get("url"):
            return item["url"]
    return ""


def pick_program_icon(program_data: dict) -> str:
    media_list = program_data.get("media") or []
    candidates = [item for item in media_list if item.get("url")]
    if not candidates:
        return ""

    def area(item: dict) -> int:
        try:
            return int(item.get("width") or 0) * int(item.get("height") or 0)
        except (TypeError, ValueError):
            return 0

    best = max(candidates, key=area)
    return best.get("url", "")


def fetch_channels(authorization: str) -> list[dict]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en",
        "Authorization": authorization,
        "User-Agent": REQUEST_HEADERS["User-Agent"],
    }
    params = {"limit": 255, "offset": 0, "isPlayable": "false"}
    response = requests.get(CHANNELS_URL, headers=headers, params=params, timeout=30)
    response.raise_for_status()
    return response.json().get("channels", [])


def fetch_schedule_pages(
    authorization: str,
    start_utc: datetime,
    end_utc: datetime,
    client_token: str,
) -> list[dict]:
    headers = {
        "Authorization": authorization,
        "Accept": "application/json",
        "Accept-Language": "en",
        "User-Agent": REQUEST_HEADERS["User-Agent"],
    }

    merged_channels: dict[str, dict] = {}
    seen_programmes: dict[str, set[str]] = {}
    cursor = start_utc

    while cursor < end_utc:
        remaining_seconds = int((end_utc - cursor).total_seconds())
        window_hours = min(
            GRID_WINDOW_HOURS,
            max(1, (remaining_seconds + 3599) // 3600),
        )
        start_iso = cursor.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        params = {
            "startDateTime": start_iso,
            "channelId": "",
            "limit": "255",
            "genreId": "",
            "isPlayable": "false",
            "duration": "24",
            "clientToken": client_token,
        }

        response = requests.get(SCHEDULE_URL, params=params, headers=headers, timeout=30)
        if response.status_code == 400:
            raise RuntimeError(
                f"Grid API 400 untuk startDateTime={params['startDateTime']}, "
                f"duration={params['duration']}. Body: {response.text[:300]}"
            )
        response.raise_for_status()
        payload = response.json()
        channels = payload.get("channels", [])

        count = payload.get("count", len(channels))
        total = payload.get("total", count)
        if total and count and count < total:
            print(
                f"Amaran: window {params['startDateTime']} return {count}/{total} channel."
            )

        for ch in channels:
            channel_id = str(ch.get("id", "")).strip()
            if not channel_id:
                continue

            if channel_id not in merged_channels:
                merged_channels[channel_id] = {"id": channel_id, "schedule": []}
                seen_programmes[channel_id] = set()

            schedule_list = merged_channels[channel_id]["schedule"]
            seen_keys = seen_programmes[channel_id]

            for item in ch.get("schedule", []):
                key = f"{item.get('id', '')}|{item.get('startDateTime', '')}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                schedule_list.append(item)

        cursor += timedelta(hours=window_hours)

    for ch in merged_channels.values():
        ch["schedule"].sort(key=lambda item: item.get("startDateTime", ""))

    return list(merged_channels.values())


def merge_schedule_payload(
    base_payload: list[dict],
    extra_payload: list[dict],
) -> list[dict]:
    merged_channels: dict[str, dict] = {}
    seen_programmes: dict[str, set[str]] = {}

    def add_payload(payload: list[dict]) -> None:
        for ch in payload:
            channel_id = str(ch.get("id", "")).strip()
            if not channel_id:
                continue

            if channel_id not in merged_channels:
                merged_channels[channel_id] = {"id": channel_id, "schedule": []}
                seen_programmes[channel_id] = set()

            schedule_list = merged_channels[channel_id]["schedule"]
            seen_keys = seen_programmes[channel_id]
            for item in ch.get("schedule", []):
                key = f"{item.get('id', '')}|{item.get('startDateTime', '')}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                schedule_list.append(item)

    add_payload(base_payload)
    add_payload(extra_payload)

    for ch in merged_channels.values():
        ch["schedule"].sort(key=lambda item: item.get("startDateTime", ""))

    return list(merged_channels.values())


def build_channel_labels(channels_payload: list[dict]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for ch in channels_payload:
        source_channel_id = str(ch.get("id", "")).strip()
        if not source_channel_id:
            continue

        logical_number = ch.get("logicalChannelNumber")
        channel_name = (ch.get("name") or "").strip()
        if logical_number is not None and str(logical_number).strip():
            label_prefix = str(logical_number).strip()
        else:
            label_prefix = source_channel_id

        if channel_name:
            labels[source_channel_id] = f"{label_prefix}.astro ({channel_name})"
        else:
            labels[source_channel_id] = f"{label_prefix}.astro"

    return labels


def find_missing_channel_ids(
    channel_ids: set[str],
    schedule_payload: list[dict],
) -> list[str]:
    scheduled_channel_ids: set[str] = set()
    for ch in schedule_payload:
        source_channel_id = str(ch.get("id", "")).strip()
        if not source_channel_id:
            continue
        if ch.get("schedule"):
            scheduled_channel_ids.add(source_channel_id)

    return sorted(channel_ids - scheduled_channel_ids)


def print_missing_channels(missing_ids: list[str], channel_labels: dict[str, str]) -> None:
    if not missing_ids:
        return

    print(f"Channel tiada programme: {len(missing_ids)}")
    for source_channel_id in missing_ids:
        print(f"- {channel_labels.get(source_channel_id, source_channel_id)}")


def build_xmltv(channels_payload: list[dict], schedule_payload: list[dict]) -> bytes:
    tv = ET.Element("tv", attrib={"generator-info-name": "astro-epg"})
    channel_map: dict[str, dict] = {}

    for ch in channels_payload:
        source_channel_id = str(ch.get("id", "")).strip()
        if not source_channel_id:
            continue

        logical_number = ch.get("logicalChannelNumber")
        if logical_number is not None and str(logical_number).strip():
            xml_channel_id = f"{str(logical_number).strip()}.astro"
        else:
            xml_channel_id = f"{source_channel_id}.astro"

        display_name = ch.get("name") or f"Channel-{source_channel_id}"
        icon_url = pick_channel_icon(ch)

        channel_elem = ET.SubElement(tv, "channel", id=xml_channel_id)
        ET.SubElement(channel_elem, "display-name").text = display_name
        if icon_url:
            ET.SubElement(channel_elem, "icon", src=icon_url)

        channel_map[source_channel_id] = {
            "xml_channel_id": xml_channel_id,
            "display_name": display_name,
        }

    for schedule_channel in schedule_payload:
        source_channel_id = str(schedule_channel.get("id", "")).strip()
        if not source_channel_id:
            continue

        mapped = channel_map.get(source_channel_id)
        if mapped:
            xml_channel_id = mapped["xml_channel_id"]
        else:
            xml_channel_id = f"{source_channel_id}.astro"

        schedules = schedule_channel.get("schedule", [])
        for item in schedules:
            start_str = item.get("startDateTime")
            duration = int(item.get("duration", 0) or 0)
            if not start_str:
                continue

            start_utc = parse_astro_datetime(start_str)
            stop_utc = start_utc + timedelta(seconds=duration)
            stop_str = stop_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

            programme = ET.SubElement(
                tv,
                "programme",
                {
                    "start": format_time_utc_to_my(start_str),
                    "stop": format_time_utc_to_my(stop_str),
                    "channel": xml_channel_id,
                },
            )
            ET.SubElement(programme, "title").text = item.get("title", "")

            synopsis = item.get("synopsis")
            if synopsis:
                ET.SubElement(programme, "desc").text = synopsis

            icon_url = pick_program_icon(item)
            if icon_url:
                ET.SubElement(programme, "icon", src=icon_url)

    rough_xml = ET.tostring(tv, encoding="utf-8")
    parsed = minidom.parseString(rough_xml)
    return parsed.toprettyxml(indent="  ", encoding="utf-8")


def save_xml(content: bytes, filename: str) -> None:
    with open(filename, "wb") as handle:
        handle.write(content)


def build_grid_range_utc(days: int) -> tuple[datetime, datetime]:
    days = max(1, int(days))
    now_my = datetime.now(MALAYSIA_TZ)
    start_my = now_my.replace(hour=0, minute=0, second=0, microsecond=0)
    end_my = start_my + timedelta(days=days)
    return start_my.astimezone(timezone.utc), end_my.astimezone(timezone.utc)


def main() -> None:
    authorization = get_bearer_token()
    print("Bearer token OK")

    channels_payload = fetch_channels(authorization)
    print(f"Jumlah channel: {len(channels_payload)}")
    channel_labels = build_channel_labels(channels_payload)
    all_channel_ids = set(channel_labels.keys())

    start_utc, end_utc = build_grid_range_utc(DAYS)
    print(
        "Range EPG (MY): "
        f"{start_utc.astimezone(MALAYSIA_TZ).strftime('%Y-%m-%d %H:%M:%S')} -> "
        f"{end_utc.astimezone(MALAYSIA_TZ).strftime('%Y-%m-%d %H:%M:%S')}"
    )
    schedule_payload = fetch_schedule_pages(
        authorization,
        start_utc,
        end_utc,
        DEFAULT_CLIENT_TOKEN,
    )
    missing_channel_ids = find_missing_channel_ids(all_channel_ids, schedule_payload)

    # Removed fallback logic as per request

    print(f"Jumlah channel schedule: {len(schedule_payload)}")
    total_programme = sum(
        len(ch.get("schedule", []))
        for ch in schedule_payload
    )
    print(f"Jumlah schedule programme: {total_programme}")
    print_missing_channels(missing_channel_ids, channel_labels)

    xml_data = build_xmltv(channels_payload, schedule_payload)
    save_xml(xml_data, OUTPUT_XML)
    print(f"Simpan XML: {OUTPUT_XML}")


if __name__ == "__main__":
    main()
