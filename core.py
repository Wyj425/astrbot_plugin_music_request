"""Deterministic request parsing and ranking; no model calls."""
from dataclasses import dataclass, asdict
from difflib import SequenceMatcher
import html
import re

PLATFORMS = {'qq': 'QQ音乐', '163': '网易云音乐', 'kugou': '酷狗音乐'}
ALIASES = {'qq音乐': 'qq', 'qq': 'qq', '网易云音乐': '163', '网易云': '163',
           '网易': '163', '酷狗音乐': 'kugou', '酷狗': 'kugou'}
VERSIONS = ('live', '现场', '伴奏', '翻唱', 'remix', 'dj', '纯音乐', '加速', '慢速', '深情版', '原唱')

def clean(value):
    return html.unescape(re.sub(r'<[^>]+>', '', str(value or ''))).strip()

def norm(value):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', clean(value).lower()).replace('_', '')

@dataclass
class Song:
    platform: str
    id: str
    title: str
    artist: str
    url: str
    cover: str = ''
    album: str = ''
    mid: str = ''
    score: float = 0

    def to_dict(self):
        return asdict(self)

@dataclass
class Intent:
    action: str
    query: str = ''
    platform: str = ''
    title: str = ''
    artist: str = ''
    number: int = 0

def parse(text):
    text = re.sub(r'^\[CQ:at,[^]]+\]\s*', '', text.strip()).lstrip('/').strip()
    if re.fullmatch(r'(?:点歌帮助|音乐帮助)', text): return Intent('help')
    if re.fullmatch(r'(?:换一个版本|换一首版本|换个版本)', text): return Intent('next')
    match = re.fullmatch(r'(?:选歌|选择歌曲)\s*(\d{1,2})', text)
    if match: return Intent('choose', number=int(match[1]))
    platform = ''
    prefix = '|'.join(sorted(map(re.escape, ALIASES), key=len, reverse=True))
    match = re.match(r'^(' + prefix + r')\s*', text, re.I)
    if match:
        platform = ALIASES[match[1].lower()]; text = text[match.end():]
    match = re.match(r'^(?:帮我|请|给我)?\s*(点歌|搜歌|搜索歌曲|我要听|我想听|想听|来一首|放一首|播放歌曲)\s*[:：]?\s*(.*)$', text)
    if not match: return None
    query = match[2].strip()
    match2 = re.match(r'^(' + prefix + r')\s*', query, re.I)
    if match2:
        platform = ALIASES[match2[1].lower()]; query = query[match2.end():]
    if query.startswith(('你说', '你讲', '你解释', '你的意见', '一下你的', '听你的')): return None
    query = re.sub(r'(?:这首歌|这首歌曲|这首|这歌|这首音乐)(?:吧|呀|啊)?[。！!？?]*$', '', query).strip()
    query = re.sub(r'[。！!？?]+$', '', query).strip()
    query = query.strip('《》“”"')
    if query in ('歌', '音乐', '歌曲', '一首歌', '首歌'): query = ''
    if len(query) > 100: return Intent('invalid')
    artist, title = '', query
    # Only the explicit possessive form is split. Free-form queries are matched
    # against actual returned titles/artists instead of guessing a singer.
    match = re.fullmatch(r'(.{1,35}?)的[《“"]?(.+?)[》”"]?', query)
    if match:
        artist, title = (x.strip('《》“”" ') for x in match.groups())
    return Intent('search', query=' '.join([artist, title]).strip() if artist else query,
                  platform=platform, title=title, artist=artist)

def rank(songs, intent, order):
    query, title, artist = norm(intent.query), norm(intent.title), norm(intent.artist)
    ranked = []
    for song in songs:
        st, sa = norm(song.title), norm(song.artist)
        if not st: continue
        if artist and artist not in sa: continue
        if title == st:
            score = 100 if artist else 96
        elif query in (st + sa, sa + st):
            score = 100
        else:
            similarity = max(SequenceMatcher(None, query, st).ratio(),
                             SequenceMatcher(None, query, st + sa).ratio(),
                             SequenceMatcher(None, query, sa + st).ratio())
            score = 70 * similarity + (12 if title and title in st else 0)
        for version in VERSIONS:
            if version in song.title.lower() and version not in intent.query.lower(): score -= 18
        if artist and title == st: score = 100
        song.score = round(score, 1)
        if score >= 35: ranked.append(song)
    ranked.sort(key=lambda s: (-s.score, order.index(s.platform) if s.platform in order else 99))
    seen, unique = set(), []
    for song in ranked:
        key = (norm(song.title), norm(song.artist))
        if key not in seen:
            unique.append(song); seen.add(key)
    return unique

def confident(songs):
    return bool(songs and songs[0].score >= 90 and
                (len(songs) == 1 or songs[0].score - songs[1].score >= 8))

def session_key(origin, sender):
    return str(origin), str(sender)
