"""Cookie compatibility with Ascendant's existing account files."""

from dataclasses import dataclass
from urllib.parse import quote

from ascendant import client

NAV_URL = 'https://api.bilibili.com/x/web-interface/nav'
COOKIE_FIELDS = {'SESSDATA': 'sessdata', 'bili_jct': 'bili_jct', 'DedeUserID': 'dedeuserid',
                 'buvid3': 'buvid3', 'buvid4': 'buvid4', 'ac_time_value': 'ac_time_value'}


@dataclass(repr=False)
class Credential:
    sessdata: str | None = None
    bili_jct: str | None = None
    dedeuserid: str | None = None
    buvid3: str | None = None
    buvid4: str | None = None
    ac_time_value: str | None = None

    def __post_init__(self):
        # Match existing cache/cookie behavior, including QR's decoded URL value.
        if self.sessdata is not None and '%' not in self.sessdata:
            self.sessdata = quote(self.sessdata)

    def get_cookies(self):
        return {cookie: str(getattr(self, attr)) for cookie, attr in COOKIE_FIELDS.items() if getattr(self, attr)}

    @classmethod
    def from_cookies(cls, cookies):
        return cls(**{attr: cookies.get(cookie) for cookie, attr in COOKIE_FIELDS.items()})

    def has_sessdata(self):
        return bool(self.sessdata)

    async def check_valid(self):
        if not self.has_sessdata():
            return False
        data = await client.request_data(NAV_URL, credential=self, allowed_codes=(-101,))
        return data.get('isLogin') is True
