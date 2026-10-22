"""Operation-scoped HTTPX transport and request pacing for Bilibili."""

import asyncio
from dataclasses import dataclass, field
import re

import httpx

from ascendant import state
from ascendant.errors import InvalidResponseError, NetworkException, ResponseCodeException, TransportRequestError, integer_code
from ascendant.fetch_context import REQUEST_DELAY, check_canceled

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Referer': 'https://www.bilibili.com/',
}
ALLOWED_HOSTS = {'api.bilibili.com', 'passport.bilibili.com', 'space.bilibili.com'}


@dataclass
class Session:
    http: httpx.AsyncClient
    mixin_key: str = ''
    webids: dict = field(default_factory=dict)
    pace_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    signing_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    next_request_at: float = 0.0


# An operation owns one event loop. Account keys prevent cookie leakage when a
# loop handles more than one identity. Cleanup always removes the complete loop.
_sessions = {}

ENDPOINT_OPERATIONS = {
    '/x/web-interface/nav': 'navigation', '/x/space/wbi/arc/search': 'creator_videos',
    '/x/space/wbi/acc/info': 'creator_profile', '/x/relation/stat': 'followers',
    '/x/space/upstat': 'creator_stats', '/x/space/myinfo': 'account_profile',
    '/x/web-interface/view': 'video_detail', '/x/web-interface/wbi/search/type': 'search',
    '/x/web-interface/popular/series/one': 'weekly',
    '/x/passport-login/web/qrcode/generate': 'qr_generate', '/x/passport-login/web/qrcode/poll': 'qr_poll',
}


def describe_failure(exc, url, credential):
    target = httpx.URL(url)
    operation = ENDPOINT_OPERATIONS.get(target.path)
    if target.host == 'space.bilibili.com' and re.fullmatch(r'/\d+/dynamic', target.path):
        operation = 'creator_page'
    mode = 'cookies' if credential is not None and credential.has_sessdata() else 'guest'
    return exc.with_request_context(operation, mode)


def response_code(response):
    try:
        envelope = response.json()
    except (ValueError, UnicodeError):
        return None
    return integer_code(envelope.get('code')) if isinstance(envelope, dict) else None


def request_delay_seconds() -> float:
    check_canceled()
    if REQUEST_DELAY.get() is not None:
        return REQUEST_DELAY.get()
    return 1.0 / state.REQUEST_FREQUENCY


def get_session(credential=None) -> Session:
    loop = asyncio.get_running_loop()
    cookies = credential.get_cookies() if credential is not None else {}
    key = tuple(sorted(cookies.items()))
    pool = _sessions.setdefault(loop, {})
    if key not in pool:
        jar = httpx.Cookies()
        for name, value in cookies.items():
            jar.set(name, value, domain='.bilibili.com', path='/')
        pool[key] = Session(httpx.AsyncClient(
            headers=HEADERS, cookies=jar, timeout=20, http2=False,
            follow_redirects=False, limits=httpx.Limits(max_connections=10, max_keepalive_connections=5)))
    return pool[key]


async def request_response(url, *, params=None, credential=None):
    target = httpx.URL(url)
    if target.scheme != 'https' or target.host not in ALLOWED_HOSTS or target.userinfo:
        raise ValueError('Unsupported Bilibili endpoint.')
    check_canceled()
    session = get_session(credential)
    # Also pace the extra nav/page requests needed for signing, including when
    # detail requests run concurrently. Callers retain their progress pacing.
    async with session.pace_lock:
        loop = asyncio.get_running_loop()
        delay = session.next_request_at - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)
        check_canceled()
        session.next_request_at = loop.time() + request_delay_seconds()
    try:
        response = await session.http.get(url, params=params)
    except (httpx.TransportError, TimeoutError) as exc:
        raise describe_failure(TransportRequestError(exc), url, credential) from None
    if response.status_code != 200:
        raise describe_failure(NetworkException(response.status_code, code=response_code(response)), url, credential)
    return response


async def request_data(url, *, params=None, credential=None, allowed_codes=()):
    response = await request_response(url, params=params, credential=credential)
    try:
        envelope = response.json()
    except (ValueError, UnicodeError):
        raise describe_failure(InvalidResponseError(), url, credential) from None
    if not isinstance(envelope, dict) or integer_code(envelope.get('code')) is None:
        raise describe_failure(InvalidResponseError(), url, credential)
    code = envelope['code']
    if code != 0 and code not in allowed_codes:
        raise describe_failure(ResponseCodeException(code, status=response.status_code), url, credential)
    data = envelope.get('data')
    if not isinstance(data, dict):
        raise describe_failure(InvalidResponseError(), url, credential)
    return data


async def close_bilibili_client() -> None:
    """Evict before closing, including cancellation; leave other loops alone."""
    pool = _sessions.pop(asyncio.get_running_loop(), {})
    if pool:
        # Start every close even when one transport fails or the caller cancels.
        await asyncio.gather(*(session.http.aclose() for session in pool.values()), return_exceptions=True)


def configure_bilibili_client() -> None:
    """Clients are created lazily with fixed, application-owned settings."""
