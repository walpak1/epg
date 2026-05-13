import requests
import xml.etree.ElementTree as ET
from xml.dom import minidom
from datetime import datetime, timezone, timedelta
from time import sleep
import o11

USE_DOH = False
DOH_URL = "https://dns.controld.com/1rmnvexb4iv"
if USE_DOH:
    o11.dns(DOH_URL)

DAYS = 2
MALAYSIA_TZ = timezone(timedelta(hours=8))

USE_PROXY = False
PROXY = ""
PROXIES = {"http": PROXY, "https": PROXY} if USE_PROXY and PROXY else None

API_URL = "https://rtm-epg.glue.live/api/epg/timeline"
IMAGE_BASE_CHANNEL = "https://rtm-images.glueapi.io/320x0"
IMAGE_BASE_PROGRAMME = "https://rtm-images.glueapi.io/800x0"
TYPES = ("tv", "radio")
# Largest `limit` accepted by both tv and radio endpoints (radio 502s at 100).
PAGE_LIMIT = 50

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

session = requests.Session()
today = datetime.now(MALAYSIA_TZ)
day0 = datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=MALAYSIA_TZ)


def xml_id(channel_code: str) -> str:
    return f"{channel_code}.rtm"


def _abs_image(image_path: str, base: str) -> str:
    if not image_path:
        return ""
    if image_path.startswith("http://") or image_path.startswith("https://"):
        return image_path
    if not image_path.startswith("/"):
        image_path = "/" + image_path
    return base + image_path


def channel_icon(image_path: str) -> str:
    return _abs_image(image_path, IMAGE_BASE_CHANNEL)


def programme_icon(image_path: str) -> str:
    return _abs_image(image_path, IMAGE_BASE_PROGRAMME)


def parse_local_dt(value: str):
    """API returns naive ISO timestamps already in the requested timezone (MYT)."""
    if not value:
        return None
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1]
    # Drop fractional seconds if present
    if "." in value:
        head, _, tail = value.partition(".")
        # tail may contain timezone suffix; ignore for simplicity
        value = head
    try:
        dt_naive = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        try:
            dt_naive = datetime.strptime(value, "%Y-%m-%dT%H:%M")
        except ValueError:
            return None
    return dt_naive.replace(tzinfo=MALAYSIA_TZ)


def xmltv_time(dt: datetime) -> str:
    return dt.strftime("%Y%m%d%H%M%S +0800")


def req_get(url, params=None, **kwargs):
    timeout = kwargs.pop("timeout", 60)
    headers = kwargs.pop("headers", None) or {"User-Agent": USER_AGENT, "Accept": "*/*"}
    for i in range(3):
        try:
            r = session.get(url, params=params, timeout=timeout, proxies=PROXIES, headers=headers, **kwargs)
            r.raise_for_status()
            return r
        except Exception:
            if i == 2:
                raise
            sleep(1 + i)


def fetch_day(date_str: str, kind: str) -> list:
    """Fetch all channels for a date.

    Tries `limit=PAGE_LIMIT` first to minimise round-trips. If the server returns
    502 (radio endpoint rejects large `limit` values intermittently), falls back
    to pagination without `limit` (default page size = 10).
    """
    base_params = {
        "sort": "id",
        "dateStart": date_str,
        "dateEnd": date_str,
        "timezone": "8",
        "embed": "channel,program",
        "type": kind,
    }

    def _paginate(use_limit: bool) -> list:
        items = []
        offset = 0
        seen_ids = set()
        while True:
            params = dict(base_params)
            if use_limit:
                params["limit"] = str(PAGE_LIMIT)
            if offset:
                params["offset"] = str(offset)
            payload = req_get(API_URL, params=params).json() or {}
            data = payload.get("data") or []
            if not data:
                break
            new_items = 0
            for item in data:
                ident = item.get("id")
                if ident in seen_ids:
                    continue
                seen_ids.add(ident)
                items.append(item)
                new_items += 1
            total = int(payload.get("count") or 0)
            offset += len(data)
            if new_items == 0 or offset >= total:
                break
        return items

    try:
        return _paginate(use_limit=True)
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        if status != 502:
            raise
        print(f"INFO: {kind} {date_str} limit={PAGE_LIMIT} hit 502, retrying without limit")
        return _paginate(use_limit=False)


def populate(tv: ET.Element):
    channel_meta = {}  # channel_code -> (display_name, icon_url, xml_id, raw_image_path)
    schedules_by_code = {}  # channel_code -> list of (start_dt, end_dt, schedule_dict)

    for kind in TYPES:
        seen_keys = set()
        for d in range(DAYS):
            date_str = (day0 + timedelta(days=d)).strftime("%Y-%m-%d")
            try:
                data = fetch_day(date_str, kind)
            except Exception as exc:
                print(f"WARN: {kind} {date_str} fetch failed: {exc}")
                continue

            for ch in data:
                code = (ch.get("channel") or "").strip()
                if not code:
                    continue
                if code not in channel_meta:
                    raw_image = ch.get("image") or ""
                    channel_meta[code] = (
                        code,
                        channel_icon(raw_image),
                        xml_id(code),
                        raw_image,
                    )
                bucket = schedules_by_code.setdefault(code, [])
                for sc in ch.get("schedule") or []:
                    start_dt = parse_local_dt(sc.get("dateTimeStart"))
                    end_dt = parse_local_dt(sc.get("dateTimeEnd"))
                    if not start_dt or not end_dt:
                        continue
                    key = (sc.get("idEPGProgramSchedule"), start_dt, end_dt)
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    bucket.append((start_dt, end_dt, sc))

    for code, (title, icon_url, ch_xml_id, _raw) in channel_meta.items():
        ch_elem = ET.SubElement(tv, "channel", id=ch_xml_id)
        ET.SubElement(ch_elem, "display-name", lang="en").text = title
        if icon_url:
            ET.SubElement(ch_elem, "icon", src=icon_url)

    prog_count = 0
    channels_with_programmes = set()
    for code, entries in schedules_by_code.items():
        if code not in channel_meta:
            continue
        _, _, ch_xml_id, raw_image = channel_meta[code]
        channel_prog_icon = programme_icon(raw_image)
        entries.sort(key=lambda x: x[0])
        for start_dt, end_dt, sc in entries:
            prog = ET.SubElement(tv, "programme", {
                "start": xmltv_time(start_dt),
                "stop": xmltv_time(end_dt),
                "channel": ch_xml_id,
            })
            title = sc.get("scheduleProgramTitle") or sc.get("programTitle") or ""
            ET.SubElement(prog, "title", lang="en").text = title

            desc = sc.get("scheduleProgramDescription") or sc.get("description")
            if desc:
                ET.SubElement(prog, "desc", lang="en").text = desc

            series = sc.get("scheduleSeries") or sc.get("series") or 0
            episode = sc.get("scheduleEpisodeNumber") or sc.get("episodeNumber") or 0
            try:
                series_idx = max(int(series) - 1, 0)
            except (TypeError, ValueError):
                series_idx = 0
            try:
                episode_idx = max(int(episode) - 1, 0)
            except (TypeError, ValueError):
                episode_idx = 0
            if series or episode:
                ET.SubElement(prog, "episode-num", system="xmltv_ns").text = (
                    f"{series_idx}.{episode_idx}.0"
                )

            # Programme icon: try common keys in the schedule payload, fallback to channel image.
            prog_icon = ""
            for key in ("image", "thumbnail", "thumbnailUrl", "imageUrl", "programImage", "icon"):
                value = sc.get(key)
                if isinstance(value, str) and value:
                    prog_icon = programme_icon(value)
                    break
            if not prog_icon:
                prog_icon = channel_prog_icon
            if prog_icon:
                ET.SubElement(prog, "icon", src=prog_icon)

            prog_count += 1
            channels_with_programmes.add(code)

    failed = [
        meta[0]
        for code, meta in channel_meta.items()
        if code not in channels_with_programmes
    ]
    return {
        "channel_count": len(channel_meta),
        "prog_count": prog_count,
        "failed": failed,
    }


def main():
    print("Malaysia Now:", today.strftime("%Y-%m-%d %H:%M:%S"))
    end = day0 + timedelta(days=DAYS) - timedelta(seconds=1)
    print(
        f"Range {DAYS} hari:",
        day0.strftime("%Y-%m-%d %H:%M:%S"),
        "->",
        end.strftime("%Y-%m-%d %H:%M:%S"),
    )

    tv = ET.Element("tv", attrib={"generator-info-name": "rtmklik-epg"})
    stats = populate(tv)

    pretty = minidom.parseString(ET.tostring(tv, encoding="utf-8")).toprettyxml(
        indent="  ", encoding="utf-8"
    )
    with open("rtmklik.xml", "wb") as f:
        f.write(pretty)

    print(f"Jumlah channel: {stats['channel_count']}")
    print(f"Jumlah hari: {DAYS}")
    print(f"Jumlah programme: {stats['prog_count']}")
    print(f"Channel gagal: {len(stats['failed'])}")
    for title in stats["failed"]:
        print(f"- {title}")
    print("Disimpan: rtmklik.xml")


if __name__ == "__main__":
    main()
