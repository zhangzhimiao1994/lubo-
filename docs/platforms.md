# Lubo 平台参考

Lubo 的默认注册表按下表顺序匹配直播间 URL。允许域名必须与 URL 的实际主机名完整匹配；相似域名、带用户信息的 URL 和非 HTTP(S) 地址不会被接受。

| key | 名称 | 允许域名 | backend | Cookie key | Android protocol 行为 |
| --- | --- | --- | --- | --- | --- |
| `douyin` | Douyin / 抖音 | `live.douyin.com`, `v.douyin.com`, `www.douyin.com` | Streamlink | `douyin` | 仅直接 FLV/HTTP；HLS-only 报错且不创建文件 |
| `bilibili` | Bilibili Live / B站直播 | `live.bilibili.com` | Streamlink | `bilibili` | 仅直接 FLV/HTTP；HLS-only 报错且不创建文件 |
| `huya` | Huya / 虎牙 | `huya.com`, `www.huya.com`, `m.huya.com` | Streamlink | `huya` | 仅直接 FLV/HTTP；HLS-only 报错且不创建文件 |
| `douyu` | Douyu / 斗鱼 | `douyu.com`, `www.douyu.com`, `m.douyu.com` | yt-dlp | `douyu` | 仅直接 FLV/HTTP；HLS-only 报错且不创建文件 |

Cookie 从本地 `config/config.ini` 的 `[cookies]` section 按对应 key 读取。只在确有需要时配置自己账号的 Cookie，并将其作为敏感凭据保护。

桌面端把解析出的可用流交给 FFmpeg；Android 端不包含 HLS 处理链，只直接读取合适的 HTTP 流并写为 FLV。平台变更、风控、地区限制、登录要求和房间实际提供的协议都会影响是否可录，因此表中的 adapter 不构成可用性保证。
