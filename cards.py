"""Build cards before sending; never mutate content after signing."""
import json
import time
from pathlib import Path
from urllib.parse import urlsplit
import aiohttp
from .core import PLATFORMS, norm

class CardError(RuntimeError): pass

class Cards:
    def __init__(self, config, context):
        self.config = config
        self.context = context
        self.native_disabled_until = {}

    async def build(self, song):
        from .services import service
        mode = self.config.get('card_mode', '自动')
        if mode == '普通链接': return 'link', None
        if mode == '自动':
            try: return 'native', await service(self.context,'card').native_music(song)
            except Exception: pass
        # Signed link cards depend only on the independent QQ card service.
        from .services import service
        if not song.cover: raise CardError('cover_unavailable')
        news = {'title': '【' + PLATFORMS[song.platform] + '】' + song.title,
            'desc': song.artist + ((' · ' + song.album) if song.album else ''),
            'preview': song.cover, 'jumpUrl': song.url, 'tag': PLATFORMS[song.platform]}
        result = await service(self.context,'card').sign(news)
        return 'signed_link', result['_signed_json']
