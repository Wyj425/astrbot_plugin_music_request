# 配置说明

只在插件自己的配置页填写。不要把真实配置提交到 GitHub。

| 设置 | 默认值 | 说明 |
|---|---|---|
| `enabled` | true | 启用点歌 |
| `platform_id` | "" | QQ接入ID 首次安装请填写当前 AstrBot 中实际配置的接入或提供商 ID。 |
| `enable_groups` | true | 群聊点歌（遵循仅@回复名单） |
| `enable_private` | true | 私聊点歌 |
| `disabled_groups` | [] | 停用点歌的群号 |
| `enable_qq` | true | 搜索QQ音乐 |
| `enable_163` | true | 搜索网易云音乐 |
| `enable_kugou` | true | 搜索酷狗音乐 |
| `platform_order` | ["qq", "163", "kugou"] | 同等匹配度的平台顺序：qq、163、kugou |
| `card_mode` | "自动" | 自动先尝试经校验的原生音乐卡片，失败使用其他来源签名卡片 |
| `search_timeout_seconds` | 8 | 每个平台搜索超时秒数（3—20） |
| `candidate_count` | 5 | 歧义时最多列出候选数（1—5） |
| `cache_minutes` | 30 | 跨会话复用搜索缓存分钟数 |
| `selection_minutes` | 5 | 选歌列表有效分钟数（按群/私聊和用户隔离） |
| `cooldown_seconds` | 3 | 同一用户连续点歌最短间隔秒数 |
