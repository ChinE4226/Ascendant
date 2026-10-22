"""Web QR sign-in. Only the QR key and resulting credentials outlive a loop."""

from enum import Enum
from urllib.parse import parse_qs, urlsplit

from ascendant import client
from ascendant.api.credentials import Credential
from ascendant.errors import InvalidResponseError, ResponseCodeException, integer_code


class QrCodeLoginEvents(Enum):
    SCAN = 'scan'
    CONF = 'confirm'
    TIMEOUT = 'timeout'
    DONE = 'done'


class QrCodeLogin:
    def __init__(self):
        self.key = None
        self.url = None
        self.credential = None

    async def generate_qrcode(self):
        try:
            data = await client.request_data('https://passport.bilibili.com/x/passport-login/web/qrcode/generate',
                                             params={'source': 'main-fe-header'})
            if not all(isinstance(data.get(name), str) and data[name] for name in ('url', 'qrcode_key')):
                raise InvalidResponseError()
            target = urlsplit(data['url'])
            if target.scheme != 'https' or not target.hostname or not target.hostname.endswith('.bilibili.com'):
                raise InvalidResponseError()
            self.key, self.url = data['qrcode_key'], data['url']
        finally:
            await client.close_bilibili_client()

    def save_qrcode(self, path):
        import qrcode
        if not self.url:
            raise ValueError('Start QR sign-in first.')
        qrcode.make(self.url).save(path)

    async def check_state(self):
        if not self.key:
            raise ValueError('Start QR sign-in first.')
        try:
            data = await client.request_data('https://passport.bilibili.com/x/passport-login/web/qrcode/poll',
                                             params={'qrcode_key': self.key, 'source': 'main-fe-header'})
            code = integer_code(data.get('code'))
            events = {86101: QrCodeLoginEvents.SCAN, 86090: QrCodeLoginEvents.CONF, 86038: QrCodeLoginEvents.TIMEOUT}
            if code in events:
                return events[code]
            if code is None:
                raise InvalidResponseError()
            if code != 0:
                raise ResponseCodeException(code)
            if not isinstance(data.get('url'), str):
                raise InvalidResponseError()
            # Parse locally; never navigate to a URL containing account cookies.
            cookies = {name: values[0] for name, values in parse_qs(urlsplit(data['url']).query).items()}
            credential = Credential.from_cookies(cookies)
            if not all((credential.sessdata, credential.bili_jct, credential.dedeuserid)):
                raise InvalidResponseError()
            credential.ac_time_value = data.get('refresh_token')
            self.credential = credential
            return QrCodeLoginEvents.DONE
        finally:
            await client.close_bilibili_client()

    def get_credential(self):
        if self.credential is None:
            raise ValueError('QR sign-in is not complete.')
        return self.credential
