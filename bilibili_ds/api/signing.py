"""WBI query signing; key permutation from the preserved upstream reference."""

import hashlib
import time
from urllib.parse import urlencode, urlsplit

from bilibili_ds import client
from bilibili_ds.api.credentials import NAV_URL
from bilibili_ds.errors import InvalidResponseError

MIXIN_ORDER = (46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,
               37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52)


def mixin_key(data):
    try:
        images = data['wbi_img']
        key = ''.join(urlsplit(images[name]).path.rsplit('/', 1)[-1].split('.')[0] for name in ('img_url', 'sub_url'))
        if len(key) != 64:
            raise ValueError
        return ''.join(key[index] for index in MIXIN_ORDER)[:32]
    except (KeyError, TypeError, ValueError, AttributeError):
        raise InvalidResponseError() from None


def sign_params(params, key, *, timestamp=None):
    result = {name: ''.join(c for c in str(value) if c not in "!'()*")
              for name, value in params.items() if value is not None and name not in ('w_rid', 'wts')}
    result['wts'] = str(int(time.time()) if timestamp is None else timestamp)
    result.setdefault('web_location', '1550101')
    result = dict(sorted(result.items()))
    result['w_rid'] = hashlib.md5((urlencode(result) + key).encode()).hexdigest()
    return result


async def signed_data(url, params, credential=None):
    session = client.get_session(credential)
    async with session.signing_lock:
        if not session.mixin_key:
            nav = await client.request_data(NAV_URL, credential=credential, allowed_codes=(-101,))
            session.mixin_key = mixin_key(nav)
    return await client.request_data(url, params=sign_params(params, session.mixin_key), credential=credential)
