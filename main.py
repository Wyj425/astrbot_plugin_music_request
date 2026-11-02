"""QQ song requests, isolated candidate sessions and bounded search audit."""
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path
from collections import OrderedDict
from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Star, Context, register
from .core import parse, rank, confident, Song, PLATFORMS, session_key, Intent
from .providers import MusicSearch
from .cards import Cards

HELP = ('点歌：点歌 晴天 / 我要听周杰伦的晴天\n'
        '指定平台：QQ音乐点歌 晴天 / 网易云点歌 稻香 / 酷狗点歌 晴天\n'
        '选版本：选歌 2 / 换一个版本\n'
        '歌曲需要会员时，进入原平台按你的账号权限播放。')

@register('music_request', 'Wyj425', 'QQ、网易云和酷狗跨平台点歌，原生或签名链接卡片', '0.2.1')
class MusicRequest(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.searcher = MusicSearch(config)
        self.cards = Cards(config,context)
        self.root = Path('data/plugin_data/music_request')
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / 'music.db')
        self.db.execute('CREATE TABLE IF NOT EXISTS pending(scope TEXT,sender TEXT,expires REAL,body TEXT,position INTEGER,PRIMARY KEY(scope,sender))')
        self.db.execute('CREATE TABLE IF NOT EXISTS audit(at REAL,scope TEXT,sender TEXT,query TEXT,platform TEXT,song_id TEXT,status TEXT,elapsed REAL)')
        self.db.execute('DELETE FROM pending WHERE expires<?', (time.time(),))
        self.db.execute('DELETE FROM audit WHERE at<?', (time.time()-14*86400,))
        self.db.commit()
        self.seen = OrderedDict()
        self.cooldowns = OrderedDict()
        self.busy = set()
        self.slots = asyncio.Semaphore(4)

    async def terminate(self):
        await self.searcher.close()
        self.db.close()

    def allowed(self, event):
        if not self.config.get('enabled', True): return False
        if event.get_platform_id() != self.config.get('platform_id', ''): return False
        if str(event.get_sender_id()) == str(event.get_self_id()): return False
        if event.is_stopped() or event.get_extra('group_media_reply_blocked'): return False
        group = str(event.get_group_id() or '')
        if group:
            if not self.config.get('enable_groups', True): return False
            if group in list(map(str, self.config.get('disabled_groups', []))): return False
            # Use the live router instance so per-group mention policies take
            # effect immediately, including edits through its custom page.
            meta = self.context.get_registered_star('group_media_router')
            router = getattr(meta, 'star_cls', None) if meta else None
            if router and hasattr(router, '_at_only_decision'):
                if router._at_only_decision(event) is False: return False
            else:
                try:
                    from data.plugins.group_media_router.rules import resolve
                    from data.plugins.group_media_router.mention_policy import mentions_bot
                    path = Path('data/config/group_media_router_config.json')
                    cfg = json.loads(path.read_text(encoding='utf-8-sig'))
                    raw = event.message_obj.raw_message
                    segments = raw.get('message', []) if hasattr(raw, 'get') else []
                    if str(event.get_sender_id()) in resolve(cfg, group)['at_only_users'] and not mentions_bot(segments, event.get_self_id()): return False
                except FileNotFoundError: pass
                except Exception:
                    logger.warning('[music_request] reply policy unavailable, skipping group request')
                    return False
        elif not self.config.get('enable_private', True): return False
        return True

    def audit(self, event, intent, status, elapsed=0, song=None):
        self.db.execute('INSERT INTO audit VALUES(?,?,?,?,?,?,?,?)', (time.time(), event.unified_msg_origin,
            str(event.get_sender_id()), intent.query[:100], song.platform if song else intent.platform,
            song.id if song else '', status, round(elapsed, 2)))
        self.db.execute('DELETE FROM audit WHERE rowid NOT IN (SELECT rowid FROM audit ORDER BY rowid DESC LIMIT 5000)')
        self.db.commit()
        logger.info('[music_request] status=%s platform=%s elapsed=%.2fs', status, song.platform if song else intent.platform, elapsed)

    def key(self, event): return session_key(event.unified_msg_origin, event.get_sender_id())

    def save(self, event, songs, position=-1):
        self.db.execute('DELETE FROM pending WHERE expires<?', (time.time(),))
        ttl = max(1, min(30, int(self.config.get('selection_minutes', 5)))) * 60
        self.db.execute('INSERT OR REPLACE INTO pending VALUES(?,?,?,?,?)', (*self.key(event), time.time()+ttl,
            json.dumps([s.to_dict() for s in songs[:10]], ensure_ascii=False), position))
        self.db.execute('DELETE FROM pending WHERE rowid NOT IN (SELECT rowid FROM pending ORDER BY expires DESC LIMIT 1024)')
        self.db.commit()

    def pending(self, event):
        row = self.db.execute('SELECT body,position FROM pending WHERE scope=? AND sender=? AND expires>?', (*self.key(event), time.time())).fetchone()
        return ([Song(**r) for r in json.loads(row[0])], row[1]) if row else ([], -1)

    async def send(self, event, segments):
        group = str(event.get_group_id() or '')
        action, target = ('send_group_msg', {'group_id': int(group)}) if group else ('send_private_msg', {'user_id': int(event.get_sender_id())})
        return await asyncio.wait_for(event.bot.call_action(action, **target, message=segments), 35)

    async def text(self, event, text):
        return await self.send(event, [{'type': 'text', 'data': {'text': text}}])

    async def deliver(self, event, song):
        try: await self.searcher.detail(song)
        except Exception: pass  # Search result/link remains usable without details.
        try:
            mode, payload = await self.cards.build(song)
        except Exception:
            mode, payload = 'link', None
        if payload:
            # A send timeout is ambiguous. Do not send a second message or retry.
            result = await self.send(event, [{'type': 'json', 'data': {'data': payload}}])
        else:
            result = await self.text(event, '【' + PLATFORMS[song.platform] + '】' + song.title + '\n' + song.artist + '\n' + song.url)
        if not isinstance(result, dict) or result.get('message_id') is None:
            raise RuntimeError('send_unconfirmed')
        event.set_extra('music_request_sent', True)
        return mode

    async def execute(self, event, intent):
        started = time.monotonic()
        if intent.action == 'help': await self.text(event, HELP); return 'help'
        if intent.action == 'invalid': await self.text(event, '歌名太长了，请只发送歌名和歌手。'); return 'invalid'
        if intent.action in ('choose', 'next'):
            songs, position = self.pending(event)
            if not songs:
                await self.text(event, '没有你当前有效的选歌列表，请先「点歌 歌名」。'); return 'expired'
            index = intent.number-1 if intent.action == 'choose' else position+1
            if index < 0 or index >= len(songs):
                await self.text(event, '请发送「选歌 1」至「选歌 ' + str(len(songs)) + '」，或重新点歌。'); return 'invalid_choice'
            mode = await self.deliver(event, songs[index])
            self.save(event, songs, index)
            self.audit(event, intent, 'accepted_' + mode, time.monotonic()-started, songs[index])
            return 'accepted_' + mode
        if not intent.query:
            await self.text(event, '想听哪首？可以说「我要听周杰伦的晴天」。'); return 'need_query'
        platforms = [intent.platform] if intent.platform else self.config.get('platform_order', ['qq', '163', 'kugou'])
        platforms = list(dict.fromkeys(x for x in platforms if x in PLATFORMS and self.config.get('enable_' + x, True)))
        if not platforms:
            await self.text(event, '这个音乐平台目前未启用。'); return 'disabled_platform'
        results = await asyncio.gather(*(self.searcher.search(p, intent.query) for p in platforms), return_exceptions=True)
        songs = rank([s for value in results if isinstance(value, list) for s in value], intent, platforms)
        if not songs:
            failed = [PLATFORMS[p] for p, r in zip(platforms, results) if isinstance(r, Exception)]
            text = '本次没有找到匹配的歌曲，请试试「点歌 歌名 歌手」。'
            if failed: text += '\n暂时无法搜索：' + '、'.join(failed)
            self.db.execute('DELETE FROM pending WHERE scope=? AND sender=?', self.key(event)); self.db.commit()
            await self.text(event, text); self.audit(event, intent, 'no_match', time.monotonic()-started); return 'no_match'
        songs = songs[:max(1, min(5, int(self.config.get('candidate_count', 5))))]
        if confident(songs):
            mode = await self.deliver(event, songs[0]); self.save(event, songs, 0)
            self.audit(event, intent, 'accepted_' + mode, time.monotonic()-started, songs[0]); return 'accepted_' + mode
        self.save(event, songs)
        rows = ['找到这些版本，请发送「选歌 序号」：']
        rows.extend(f'{i}. {s.title} — {s.artist}（{PLATFORMS[s.platform]}）' for i, s in enumerate(songs, 1))
        if intent.artist: rows.append('列表仅保留歌手匹配的结果。')
        await self.text(event, '\n'.join(rows)); self.audit(event, intent, 'selection', time.monotonic()-started)
        return 'selection'

    async def handle(self, event, intent):
        key = self.key(event)
        mid = str(getattr(event.message_obj, 'message_id', '') or '')
        seen_key = (event.unified_msg_origin, mid)
        if mid and seen_key in self.seen: return 'duplicate'
        if key in self.busy:
            return 'busy'  # Do not produce additional messages for repeated clicks.
        now = time.monotonic()
        if now - self.cooldowns.get(key, -1e9) < max(1, int(self.config.get('cooldown_seconds', 3))): return 'cooldown'
        if mid:
            self.seen[seen_key] = now
            while len(self.seen) > 2048: self.seen.popitem(last=False)
        self.cooldowns[key] = now
        while len(self.cooldowns) > 2048: self.cooldowns.popitem(last=False)
        self.busy.add(key)
        try:
            async with self.slots: return await self.execute(event, intent)
        except Exception as exc:
            # Do not retry after an ambiguous delivery error. Keep detailed
            # private information out of plugin logs.
            self.audit(event, intent, 'error_' + type(exc).__name__)
            logger.warning('[music_request] operation failed type=%s', type(exc).__name__)
            return 'error'
        finally:
            self.busy.discard(key)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=sys.maxsize-1)
    async def request_song(self, event: AstrMessageEvent):
        intent = parse(event.message_str or '')
        if intent is None or not self.allowed(event): return
        # The router has already enforced mention-only users. An explicit song
        # request is now owned by this plugin even if random chat was rejected.
        event.set_extra('group_media_probability_blocked', False)
        await self.handle(event, intent)
        event.stop_event()

    @filter.llm_tool(name='request_music')
    async def request_music(self, event: AstrMessageEvent, query: str, platform: str = ''):
        '''为用户明确要求的歌曲搜索并发送音乐卡片；歌名不明确时不要调用。不要用于歌曲知识问答。此工具已负责发送结果。

        Args:
            query(string): 用户要听的歌名，可附歌手，例如周杰伦的晴天。
            platform(string): 指定 qq、163、kugou，留空跨平台搜索。
        '''
        if not self.allowed(event) or event.get_extra('group_media_probability_blocked', False): return '当前会话规则不允许发送。'
        if platform and platform not in PLATFORMS: return '平台应为qq、163或kugou。'
        if not query or len(query) > 100: return '请明确歌名。'
        intent = parse('点歌 ' + query)
        if not intent: return '请明确歌名。'
        intent.platform = platform
        result = await self.handle(event, intent)
        event.set_extra('music_request_tool_handled', result != 'error')
        return '点歌处理结果：' + result + '。已负责发送，不要重复发送歌曲或附加语音；accepted仅表示适配器提交成功，不代表用户已收到。'

    @filter.on_decorating_result(priority=sys.maxsize-2)
    async def prevent_duplicate_reply(self, event):
        if event.get_extra('music_request_tool_handled', False):
            result = event.get_result()
            if result and hasattr(result, 'chain'): result.chain.clear()
