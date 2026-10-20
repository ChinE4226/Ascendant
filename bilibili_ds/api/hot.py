"""Weekly-popular video cohorts."""

from bilibili_ds.api.signing import signed_data
from bilibili_ds.errors import InvalidResponseError


async def get_weekly_hot_videos(week=1, *, credential=None):
    data = await signed_data('https://api.bilibili.com/x/web-interface/popular/series/one',
                             {'web_location': '333.934', 'number': week}, credential)
    if not isinstance(data.get('list'), list) or any(not isinstance(row, dict) for row in data['list']):
        raise InvalidResponseError()
    return data
