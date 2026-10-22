"""Shared connection checks and bounded wire formats. No network or disk I/O."""

from datetime import datetime, timezone
import ipaddress
import math
import re
from urllib.parse import urlparse

from ascendant import config
from ascendant.distributions import metric

VERSION = 1
CAPABILITIES = ("videos", "creator", "selection", "pacing")
MAX_BODY = 16 * 1024 * 1024


class ProtocolError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def local_operator(handler):
    """Node administration is local-only, even if the dashboard binds to the LAN."""
    host = urlparse("http://" + handler.headers.get("Host", "")).hostname
    if not ipaddress.ip_address(handler.client_address[0]).is_loopback or host not in {"localhost", "127.0.0.1", "::1"}:
        raise ProtocolError("Open node controls on localhost on this Mac.", 403)
    origin = handler.headers.get("Origin")
    if origin and urlparse(origin).netloc != handler.headers.get("Host"):
        raise ProtocolError("This control request must come from the local node page.", 403)


def json_request(handler):
    if handler.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
        raise ProtocolError("Use application/json for node requests.", 415)


def coordinator_url(value):
    parsed = urlparse(str(value or "").strip())
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("Enter a valid connection-service port.") from None
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in {"", "/"} or port == 0):
        raise ValueError("Enter the main Mac's connection URL, such as http://192.168.1.20:8010.")
    return f"{parsed.scheme}://{parsed.netloc}"


def frequency(value):
    if isinstance(value, bool):
        raise ValueError("Request pacing must be a number from 0.1 to 4 requests/second.")
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError("Request pacing must be a number from 0.1 to 4 requests/second.") from None
    if not math.isfinite(result) or not .1 <= result <= 4:
        raise ValueError("Request pacing must be a number from 0.1 to 4 requests/second.")
    return result


def video_ids(value):
    text = str(value or "")
    if len(text) > 100000:
        raise ValueError("Enter at most 1,000 video links or BV IDs.")
    entries = [entry for entry in re.split(r"[\s,;]+", text.strip()) if entry]
    found = []
    for entry in entries:
        matches = re.findall(r"(?i)(?<![0-9a-z])BV[0-9A-Za-z]{10}(?![0-9a-z])", entry)
        if len(matches) != 1:
            raise ValueError(f"Invalid video link or BV ID: {entry[:80]}")
        bvid = "BV" + matches[0][2:]
        if bvid not in found:
            found.append(bvid)
    if not found or len(found) > 1000:
        raise ValueError("Enter 1–1,000 video links or BV IDs.")
    return found


def integer(value, label, maximum=500):
    text = str(value)
    if not text.isascii() or not text.isdigit() or not 1 <= int(text) <= maximum:
        raise ValueError(f"{label} must be a whole number from 1 to {maximum}.")
    return int(text)


def workspace_selection(value):
    from ascendant.selection import parse_datetime_input
    from ascendant.web.serializers import parse_range_number
    if not isinstance(value, dict):
        raise ValueError('Choose a dataset collection range.')
    kind = value.get('kind')
    if kind == 'position':
        start = integer(value.get('start'), 'Start number', 1000000)
        end = integer(value.get('end'), 'End number', 1000000)
        if not 1 <= end - start + 1 <= 500:
            raise ValueError('A remote number range must request 1–500 valid videos.')
        return {'kind': kind, 'start': start, 'end': end}
    if kind == 'published':
        start, end = str(value.get('start_time') or ''), str(value.get('end_time') or '')
        first, last = parse_datetime_input(start), parse_datetime_input(end, end_of_day=True)
        if first is None or last is None or first > last:
            raise ValueError('Enter a valid published-time range, with start before end.')
        return {'kind': kind, 'start_time': start, 'end_time': end}
    if kind == 'metric':
        field = value.get('metric')
        if field not in {row[0] for row in config.VIDEO_STAT_FIELDS}:
            raise ValueError('Choose a valid metric for remote collection.')
        boundaries = {}
        for name in ('minimum', 'maximum'):
            raw = value.get(name)
            number = parse_range_number(raw)
            if raw not in (None, '') and number is None:
                raise ValueError('Metric boundaries must be nonnegative numbers.')
            boundaries[name] = number
        if all(number is None for number in boundaries.values()):
            raise ValueError('Enter at least one metric boundary.')
        if boundaries['minimum'] is not None and boundaries['maximum'] is not None and boundaries['minimum'] >= boundaries['maximum']:
            raise ValueError('Minimum metric must be smaller than maximum.')
        return {'kind': kind, 'metric': field, **boundaries}
    raise ValueError('Choose a supported dataset selection mode.')


def wire_video(item):
    """Only data needed for analysis crosses the wire; credentials never do."""
    if not isinstance(item, dict) or not re.fullmatch(r"BV[0-9A-Za-z]{10}", str(item.get("bvid") or "")):
        raise ValueError("A node returned an invalid video identity.")
    owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
    stat = {key: metric(item, key) for _, _, key in config.VIDEO_STAT_FIELDS}
    if any(value is not None and value > 9007199254740991 for value in stat.values()):
        raise ValueError("A node returned an out-of-range metric.")
    published = item.get("pubdate") or item.get("created")
    if isinstance(published, bool) or not isinstance(published, (int, float)) or not math.isfinite(published) or not 0 <= published <= 253402300799:
        published = None
    return {"bvid": item["bvid"], "title": str(item.get("title") or "(no title)")[:1000],
            "pubdate": int(published) if published is not None else None,
            "owner": {"mid": str(owner.get("mid") or "")[:32] or None, "name": str(owner.get("name") or "Unknown")[:100]},
            "stat": stat, "detail_error": str(item.get("detail_error") or "")[:300]}
