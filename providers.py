"""Search public platform endpoints with TLS verification and bounded requests."""
import asyncio
import json
import time
from collections import OrderedDict
from .core import Song, clean
import aiohttp

class SearchError(RuntimeError): pass

def https(url):
    url = str(url or '').replace('{size}', '400')
    if url.startswith('//'): url = 'https:' + url
    return url.replace('http://', 'https://', 1) if url.startswith(('http://', 'https://')) else ''

class MusicSearch:
    def __init__(self, config):
        self.config = config
        self.cache = OrderedDict()
        self.inflight = {}
        self.semaphore = asyncio.Semaphore(6)
        self.status = {}

    async def close(self):
        tasks = list(self.inflight.values())
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def request(self, url, *, params=None, headers=None, method='GET', payload=None):
        async with self.semaphore:
            timeout = max(3, min(20, int(self.config.get('search_timeout_seconds', 8))))
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout),
                    headers={'User-Agent': 'Mozilla/5.0', **(headers or {})}) as client:
                async with client.request(method, url, params=params, json=payload, allow_redirects=False) as response:
                    if response.status != 200: raise SearchError('HTTP_' + str(response.status))
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(16384):
                        size += len(chunk)
                        if size > 2 * 1024 * 1024: raise SearchError('response_too_large')
                        chunks.append(chunk)
                    return json.loads(b''.join(chunks))

    async def search(self, platform, query):
        key = (platform, query.casefold().strip())
        cached = self.cache.get(key)
        if cached and cached[0] > time.time():
            return [Song(**x) for x in cached[1]]
        if key not in self.inflight:
            self.inflight[key] = asyncio.create_task(self._load(key, query))
        rows = await asyncio.shield(self.inflight[key])
        return [Song(**x.to_dict()) for x in rows]

    async def _load(self, key, query):
        started = time.monotonic()
        try:
            rows = await getattr(self, 'search_' + key[0])(query)
            self.status[key[0]] = {'status': 'ok' if rows else 'empty', 'elapsed': round(time.monotonic()-started, 2), 'count': len(rows)}
            ttl = max(60, min(86400, int(self.config.get('cache_minutes', 30)) * 60)) if rows else 30
            self.cache[key] = (time.time() + ttl, [x.to_dict() for x in rows])
            while len(self.cache) > 512: self.cache.popitem(last=False)
            return rows
        except Exception as exc:
            self.status[key[0]] = {'status': type(exc).__name__, 'elapsed': round(time.monotonic()-started, 2), 'count': 0}
            raise SearchError(key[0] + '_unavailable') from None
        finally:
            self.inflight.pop(key, None)

    async def search_qq(self, query):
        data = await self.request('https://c.y.qq.com/splcloud/fcgi-bin/smartbox_new.fcg',
            params={'key': query, 'format': 'json'}, headers={'Referer': 'https://y.qq.com/'})
        if data.get('code') != 0: raise SearchError('qq_api_error')
        rows = data.get('data', {}).get('song', {}).get('itemlist', [])
        return [Song('qq', str(r['id']), clean(r['name']), clean(r.get('singer')), 
            'https://y.qq.com/n/ryqq/songDetail/' + r['mid'], mid=r['mid'])
            for r in rows[:10] if r.get('mid') and str(r.get('id', '')).isdigit()]

    async def search_163(self, query):
        data = await self.request('https://music.163.com/api/search/get/web',
            params={'s': query, 'type': 1, 'limit': 10, 'offset': 0}, headers={'Referer': 'https://music.163.com/'})
        if data.get('code') != 200: raise SearchError('163_api_error')
        return [Song('163', str(r['id']), clean(r['name']), '/'.join(clean(a['name']) for a in r.get('artists', [])),
            'https://music.163.com/song?id=' + str(r['id']), album=clean(r.get('album', {}).get('name')))
            for r in data.get('result', {}).get('songs', [])[:10] if str(r.get('id', '')).isdigit()]

    async def search_kugou(self, query):
        data = await self.request('https://songsearch.kugou.com/song_search_v2',
            params={'keyword': query, 'page': 1, 'pagesize': 10, 'platform': 'WebFilter', 'format': 'json'},
            headers={'Referer': 'https://www.kugou.com/'})
        if data.get('status') != 1: raise SearchError('kugou_api_error')
        return [Song('kugou', clean(r['FileHash']), clean(r['SongName']), clean(r.get('SingerName')),
            'https://www.kugou.com/song/#hash=' + clean(r['FileHash']) + '&album_id=' + str(r.get('AlbumID', '0')),
            cover=https(r.get('Image')), album=clean(r.get('AlbumName')))
            for r in data.get('data', {}).get('lists', [])[:10] if r.get('FileHash')]

    async def detail(self, song):
        if song.platform == 'qq':
            payload = {'comm': {'ct': 24, 'cv': 0}, 'songinfo': {'module': 'music.pf_song_detail_svr',
                'method': 'get_song_detail_yqq', 'param': {'song_mid': song.mid}}}
            data = await self.request('https://u.y.qq.com/cgi-bin/musicu.fcg', params={'data': json.dumps(payload)},
                headers={'Referer': 'https://y.qq.com/'})
            info = data.get('songinfo', {}).get('data', {}).get('track_info', {})
            album = info.get('album', {})
            if album.get('mid'):
                song.cover = 'https://y.gtimg.cn/music/photo_new/T002R300x300M000' + album['mid'] + '.jpg'
                song.album = clean(album.get('name'))
        elif song.platform == '163':
            data = await self.request('https://music.163.com/api/song/detail/',
                params={'ids': '[' + song.id + ']', 'id': song.id}, headers={'Referer': 'https://music.163.com/'})
            info = next((r for r in data.get('songs', []) if str(r.get('id')) == song.id), {})
            song.cover = https(info.get('album', {}).get('picUrl'))
        return song
