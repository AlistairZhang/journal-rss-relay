# Journal RSS Relay

## 中文期刊

| 期刊 | RSS 订阅链接 |
| --- | --- |
| 经济研究 | https://alistairzhang.github.io/journal-rss-relay/jingji-yanjiu.xml |
| 数经技经 | https://alistairzhang.github.io/journal-rss-relay/shujing-jijing.xml |
| 经济地理 | https://alistairzhang.github.io/journal-rss-relay/jingji-dili.xml |
| 管理世界 | https://alistairzhang.github.io/journal-rss-relay/guanli-shijie.xml |
| 中国工业经济 | https://alistairzhang.github.io/journal-rss-relay/zhongguo-gongye-jingji.xml |
| 世界经济 | https://alistairzhang.github.io/journal-rss-relay/shijie-jingji.xml |

## 英文期刊

| Journal | RSS Feed |
| --- | --- |
| Econometrica | https://alistairzhang.github.io/journal-rss-relay/econometrica.xml |
| Journal of Political Economy（原文） | https://alistairzhang.github.io/journal-rss-relay/jpe.xml |
| JPE译版-祥仔 | https://alistairzhang.github.io/journal-rss-relay/jpe-zh.xml |
| American Economic Review（原文） | https://alistairzhang.github.io/journal-rss-relay/aer.xml |
| AER译版-祥仔 | https://alistairzhang.github.io/journal-rss-relay/aer-zh.xml |

## 翻译接口设置

译文版使用兼容 OpenAI `chat/completions` 格式的接口。请在仓库的
`Settings → Secrets and variables → Actions` 中手动添加：

- Secret：`TRANSLATION_API_KEY`（密钥，只保存在 GitHub 加密 Secret 中）
- Variable：`TRANSLATION_API_URL`（完整的 HTTPS 接口网址）
- Variable：`TRANSLATION_MODEL`（模型名称）

程序不会把密钥写入代码、配置、RSS、缓存或日志。已有译文会从
`translation_cache.json` 复用；只有新文章出现时才调用翻译接口。
