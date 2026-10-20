"""Creator metadata and paginated uploads."""

from enum import Enum
import json
import re

from bilibili_ds import client
from bilibili_ds.api.signing import signed_data
from bilibili_ds.errors import InvalidResponseError, ResponseCodeException


class VideoOrder(Enum):
    PUBDATE = 'pubdate'
    FAVORITE = 'stow'
    VIEW = 'click'


class User:
    def __init__(self, uid, credential=None):
        self.uid, self.credential = int(uid), credential

    async def get_access_id(self):
        session = client.get_session(self.credential)
        if self.uid not in session.webids:
            response = await client.request_response(f'https://space.bilibili.com/{self.uid}/dynamic', credential=self.credential)
            # Bilibili has used both render-data spellings. Absence is allowed,
            # as in the old SDK; an API rejection is surfaced without a retry.
            match = re.search(r'<script[^>]*id=["\']__RENDER_DATA__["\'][^>]*>\s*|window\._render_data_\s*=\s*', response.text)
            access_id = None
            if match:
                try:
                    data = json.JSONDecoder().raw_decode(response.text[match.end():])[0]
                    access_id = data.get('access_id')
                    if access_id is not None and not isinstance(access_id, str):
                        raise ValueError
                except (ValueError, AttributeError):
                    raise InvalidResponseError() from None
            session.webids[self.uid] = access_id
        return session.webids[self.uid]

    async def get_videos(self, *, pn=1, ps=30, order=VideoOrder.PUBDATE, tid=0, keyword=''):
        params = {'mid': self.uid, 'ps': ps, 'tid': tid, 'pn': pn, 'keyword': keyword,
                  'order': order.value, 'platform': 'web', 'w_webid': await self.get_access_id()}
        data = await signed_data('https://api.bilibili.com/x/space/wbi/arc/search', params, self.credential)
        rows = data.get('list')
        if not isinstance(rows, dict) or not isinstance(rows.get('vlist'), list) or any(not isinstance(row, dict) for row in rows['vlist']):
            raise InvalidResponseError()
        return data

    async def get_user_info(self):
        return await signed_data('https://api.bilibili.com/x/space/wbi/acc/info',
            {'mid': self.uid, 'w_webid': await self.get_access_id()}, self.credential)

    async def get_relation_info(self):
        return await client.request_data('https://api.bilibili.com/x/relation/stat',
                                         params={'vmid': self.uid}, credential=self.credential)

    async def get_up_stat(self):
        return await client.request_data('https://api.bilibili.com/x/space/upstat',
                                         params={'mid': self.uid}, credential=self.credential)


async def get_self_info(credential):
    if credential is None or not credential.has_sessdata():
        raise ResponseCodeException(-101)
    return await client.request_data('https://api.bilibili.com/x/space/myinfo', credential=credential)
