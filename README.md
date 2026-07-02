# last30days-cn-feeds

中心化 feed 数据仓库，为 `last30days-cn` Claude skill 提供 X 推文、播客转录等结构化数据。

## 架构

```
GitHub Actions (每天 6:23 UTC)
  → scripts/generate-feed.py (用 X_BEARER_TOKEN 抓推文 + 播客 RSS)
  → data/feeds/*.json (commit 到本仓库)
  → 本地 Claude agent fetch GitHub raw URL 消费数据
```

## 文件说明

| 路径 | 说明 |
|---|---|
| `scripts/generate-feed.py` | 中心端 feed 生成脚本 |
| `config/sources.json` | X 账号 + 播客配置 |
| `data/feeds/feed-x.json` | X 推文数据 |
| `data/feeds/feed-podcasts.json` | 播客数据 |
| `data/feeds/feed-youtube.json` | YouTube 数据 |
| `data/feeds/feed-github.json` | GitHub 数据 |
| `data/feeds/feed-web.json` | Web 数据 |

## 本地使用

```bash
LAST30DAYS_CN_FEED_BASE=https://raw.githubusercontent.com/gengleilei/last30days-cn-feeds/main/data/feeds \
  python3 scripts/prepare-research.py "your topic" --days 30
```

## Secrets

- `X_BEARER_TOKEN` — X API v2 Bearer Token（必需）
- `POD2TXT_API_KEY` — pod2txt API key（可选，用于播客转录）
