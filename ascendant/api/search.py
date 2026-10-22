"""Bounded video searches, with dates interpreted in Bilibili's timezone."""

from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

from ascendant.api.signing import signed_data
from ascendant.errors import InvalidResponseError


class SearchObjectType(Enum):
    VIDEO = 'video'


class OrderVideo(Enum):
    TOTALRANK = 'totalrank'
    PUBDATE = 'pubdate'
    CLICK = 'click'


async def search_by_type(*, keyword, search_type=SearchObjectType.VIDEO, order_type=OrderVideo.TOTALRANK,
                         page=1, page_size=20, time_start=None, time_end=None, video_zone_type=None, credential=None):
    params = {'keyword': keyword, 'search_type': search_type.value, 'order': order_type.value,
              'page': page, 'page_size': page_size, 'duration': 0}
    if video_zone_type:
        params['tids'] = video_zone_type
    if time_start and time_end:
        for name, value in (('pubtime_begin_s', time_start), ('pubtime_end_s', time_end)):
            params[name] = int(datetime.strptime(value, '%Y-%m-%d').replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp())
    data = await signed_data('https://api.bilibili.com/x/web-interface/wbi/search/type', params, credential)
    if not isinstance(data.get('result'), list) or any(not isinstance(row, dict) for row in data['result']):
        raise InvalidResponseError()
    return data
