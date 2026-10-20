"""Video metadata used by collection and tracking."""

from bilibili_ds import client
from bilibili_ds.errors import InvalidResponseError


class Video:
    def __init__(self, *, bvid, credential=None):
        self.bvid, self.credential = bvid, credential

    async def get_info(self):
        data = await client.request_data('https://api.bilibili.com/x/web-interface/view',
                                         params={'bvid': self.bvid}, credential=self.credential)
        if not isinstance(data.get('stat'), dict) or not data.get('bvid'):
            raise InvalidResponseError()
        return data
