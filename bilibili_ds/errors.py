"""Safe, consistent descriptions of upstream failures; never include response bodies."""

import httpx

REJECTED_CODES = {-101, -403, -412, -352, -509}
REJECTED_STATUSES = {401, 403, 412, 429}

# Only locally defined labels can enter error notices or persisted history.
REQUEST_OPERATIONS = {
    'creator_page': 'Creator page access ID (space.bilibili.com/{uid}/dynamic)',
    'navigation': 'Account/navigation metadata (api.bilibili.com/x/web-interface/nav)',
    'creator_videos': 'Creator video list (api.bilibili.com/x/space/wbi/arc/search)',
    'creator_profile': 'Creator profile (api.bilibili.com/x/space/wbi/acc/info)',
    'followers': 'Follower counts (api.bilibili.com/x/relation/stat)',
    'creator_stats': 'Creator statistics (api.bilibili.com/x/space/upstat)',
    'account_profile': 'Account profile (api.bilibili.com/x/space/myinfo)',
    'video_detail': 'Video details (api.bilibili.com/x/web-interface/view)',
    'search': 'Video search (api.bilibili.com/x/web-interface/wbi/search/type)',
    'weekly': 'Weekly popular (api.bilibili.com/x/web-interface/popular/series/one)',
    'qr_generate': 'QR sign-in generation (passport.bilibili.com/x/passport-login/web/qrcode/generate)',
    'qr_poll': 'QR sign-in status (passport.bilibili.com/x/passport-login/web/qrcode/poll)',
}
CREDENTIAL_MODES = {'guest': 'guest (no session cookie supplied)',
                    'cookies': 'session cookie supplied (validity not checked)'}


def request_context_message(operation, credential_mode):
    label = REQUEST_OPERATIONS.get(operation) if isinstance(operation, str) else None
    mode = CREDENTIAL_MODES.get(credential_mode) if isinstance(credential_mode, str) else None
    return (f' Request: {label}.' if label else '') + (f' Credentials: {mode}.' if mode else '')


def integer_code(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def request_reason(*, code=None, status=None):
    code, status = integer_code(code), integer_code(status)
    identifiers = ', '.join(filter(None, (
        f'HTTP {status}' if status is not None else '',
        f'API code {code}' if code is not None else '')))
    suffix = f' ({identifiers})' if identifiers else ''
    if code == -101 or status == 401:
        return f'Bilibili requires sign-in{suffix}. Sign in on the fetching Mac and try again.'
    if status == 412 or code == -412:
        return (f'Bilibili rejected the request{suffix}. The response does not establish the exact cause; '
                '412 alone does not prove a request-rate limit. Wait before trying again, '
                'lower collection requests per second if collecting frequently, and check sign-in on the fetching Mac.')
    if status == 429:
        return (f'Bilibili rejected the request{suffix}. The server reported too many requests. '
                'Wait before trying again and lower collection requests per second.')
    if code in REJECTED_CODES or status in REJECTED_STATUSES:
        return (f'Bilibili rejected the request{suffix}. This may be a request-rate or risk-control block. '
                'Wait before trying again, lower collection requests per second, and check sign-in on the fetching Mac.')
    if status == 404 or code == -404:
        return f'Bilibili could not find the video or resource{suffix}. It may be unavailable or private.'
    if status is not None and status >= 500:
        return f'Bilibili returned a server error{suffix}. Try again later.'
    return f'Bilibili returned an error{suffix}.'


class BilibiliRequestError(ValueError):
    """A locally generated message retaining machine-readable upstream codes."""

    def __init__(self, message, *, code=None, status=None):
        super().__init__(message)
        self.code, self.status = integer_code(code), integer_code(status)
        self.operation = self.credential_mode = self.kind = None

    def with_request_context(self, operation, credential_mode):
        if self.operation is None and self.credential_mode is None:
            self.operation = operation if isinstance(operation, str) and operation in REQUEST_OPERATIONS else None
            self.credential_mode = credential_mode if isinstance(credential_mode, str) and credential_mode in CREDENTIAL_MODES else None
            context = request_context_message(self.operation, self.credential_mode).strip()
            self.args = ((context + ' ' if context else '') + str(self),)
        return self


class NetworkException(BilibiliRequestError):
    """An HTTP failure; never retain the upstream response body."""

    def __init__(self, status, _message=None, *, code=None):
        super().__init__(request_reason(status=status, code=code), status=status, code=code)


class ResponseCodeException(BilibiliRequestError):
    """An API failure; upstream messages and payloads are not retained."""

    def __init__(self, code, _message=None, _data=None, *, status=None):
        super().__init__(request_reason(code=code, status=status), code=code, status=status)


class InvalidResponseError(BilibiliRequestError):
    def __init__(self):
        super().__init__('Bilibili returned an invalid response. Collection stopped; try again later.')
        self.kind = 'invalid_response'


class TransportRequestError(BilibiliRequestError):
    """A transport failure has no HTTP response status, including no HTTP 412."""

    def __init__(self, exc):
        super().__init__(request_error_message(exc))
        self.kind = 'timeout' if isinstance(exc, (TimeoutError, httpx.TimeoutException)) else 'network'


def request_error_message(exc):
    if isinstance(exc, BilibiliRequestError):
        return str(exc)
    code, status = integer_code(getattr(exc, 'code', None)), integer_code(getattr(exc, 'status', None))
    if code is not None or status is not None:
        return request_reason(code=code, status=status)
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        return 'The Bilibili request timed out. Check the fetching Mac\'s internet connection and try again later.'
    if isinstance(exc, httpx.TransportError):
        return f'Network request failed ({type(exc).__name__}). Check the fetching Mac\'s internet connection.'
    return f'Bilibili request failed ({type(exc).__name__}). Check availability, sign-in, and connection.'


def public_error_message(exc):
    """Keep local validation messages, but redact upstream response contents."""
    if isinstance(exc, (BilibiliRequestError, httpx.TransportError, TimeoutError)):
        return request_error_message(exc)
    return str(exc)


def check_detail_rejection(item):
    code = integer_code(item.get('detail_error_code'))
    status = integer_code(item.get('detail_error_status'))
    kind = item.get('detail_error_kind')
    if (code is not None and code not in (0, -404)
            or status is not None and status not in (200, 404)
            or kind in ('timeout', 'network', 'invalid_response')):
        identity = item.get('bvid')
        context = f' for {identity}' if isinstance(identity, str) and len(identity) <= 12 and identity.isalnum() else ''
        reason = request_reason(code=code, status=status)
        if kind == 'invalid_response':
            reason = str(InvalidResponseError())
        if code is None and status is None and kind in ('timeout', 'network'):
            reason = ('The video-detail request timed out.' if kind == 'timeout' else 'The video-detail network request failed.')
            reason += ' Check the fetching Mac\'s internet connection and try again later.'
        request_context = request_context_message(item.get('detail_error_operation'), item.get('detail_error_credentials')).strip()
        reason = (request_context + ' ' if request_context else '') + reason
        raise BilibiliRequestError(f'Video collection stopped{context}. {reason}',
                                   code=code, status=status)
