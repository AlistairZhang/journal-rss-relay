# RSS 订阅链接汇总

本项目把期刊官网、出版机构和文献数据库中的当期题录整理为可直接订阅的 RSS。当前维护 9 个期刊来源，共生成 11 个 RSS 文件；其中 American Economic Review 和 Journal of Political Economy 同时提供英文原版与中文译版。

## 中文期刊

| 期刊 | RSS 订阅链接 |
| --- | --- |
| 经济研究 | https://alistairzhang.github.io/journal-rss-relay/jingji-yanjiu.xml |
| 数量经济技术经济研究 | https://alistairzhang.github.io/journal-rss-relay/shuliang-jingji-jishu-jingji-yanjiu.xml |
| 经济地理 | https://alistairzhang.github.io/journal-rss-relay/jingji-dili.xml |
| 管理世界 | https://alistairzhang.github.io/journal-rss-relay/guanli-shijie.xml |
| 中国工业经济 | https://alistairzhang.github.io/journal-rss-relay/zhongguo-gongye-jingji.xml |
| 世界经济 | https://alistairzhang.github.io/journal-rss-relay/shijie-jingji.xml |

## 英文期刊

| Journal | RSS Feed |
| --- | --- |
| Econometrica | https://alistairzhang.github.io/journal-rss-relay/econometrica.xml |
| Journal of Political Economy | https://alistairzhang.github.io/journal-rss-relay/Journal-of-Political-Economy.xml |
| Journal of Political Economy中文版 | https://alistairzhang.github.io/journal-rss-relay/Journal-of-Political-Economy-zh.xml |
| American Economic Review | https://alistairzhang.github.io/journal-rss-relay/American-Economic-Review.xml |
| American Economic Review中文版 | https://alistairzhang.github.io/journal-rss-relay/American-Economic-Review-zh.xml |

## 自动更新与翻译

- GitHub Actions 约每 3 天自动检查一次全部期刊，也可以在 Actions 页面手动运行。当前计划为 UTC 每月 1、4、7……日 22:15，即北京时间次日 06:15。月内通常相隔 72 小时，但跨月时可能缩短或延长，GitHub 排队也可能造成延迟，因此不是严格的“每 72 小时”。
- 每轮运行都会重新获取各期刊的最新题录，完成字段规范化和完整性校验后再统一发布。任一必要来源连续重试后仍失败，整轮不会用残缺文件覆盖线上已有 RSS。
- JPE 和 AER 都是先生成当轮英文 RSS，再逐篇检查翻译缓存。缓存以 DOI（无 DOI 时使用 GUID）区分文章，并比较英文标题与摘要的哈希值；只有新文章、DOI/GUID 改变，或英文标题/摘要发生变化时，才调用翻译 API。每个需要更新的条目分别翻译标题和摘要。
- 作者、日期、卷期页码、栏目或链接发生变化时，不会重新翻译标题和摘要；译版正常生成时，中文版会复制这些最新的原版元数据。更换 API 网址或模型也不会自动重译已有缓存；如需全部重译，需要删除相应缓存记录。
- 翻译 API Key 只保存在 GitHub Actions Secret 中，接口网址和模型名称保存在 Repository Variables 中，不会写入代码、RSS 或日志。翻译失败时不会保存半篇译文；已有中文版会尽量保留，并由原文与译文一致性检查决定本轮是否可以发布。
- 每次成功检查都会刷新 RSS 的 `lastBuildDate`，所以仓库出现新的自动提交并不一定表示期刊新增了文章。

## 期刊信息

### 数据来源与处理方式

| 期刊 | 数据来源 | 处理措施 |
| --- | --- | --- |
| 经济研究 | [《经济研究》AJ-CASS 官网](https://erj.ajcass.com/#/index)及其公开接口 | 先由官网接口识别当前期，再逐篇读取详情；提取标题、作者、摘要、年卷期、编辑日期及可用 DOI。中文作者被拆分为独立创建者并去重；标题、作者、摘要或日期缺失时停止本次更新。当前 RSS 日期采用官网 `editDate`，不一定等同于纸刊出版日。 |
| 数量经济技术经济研究 | [国家哲学社会科学文献中心](https://www.ncpssd.org/)；[期刊官网](https://www.jqte.net/sljjjsjjyj/ch/index.aspx)作为频道主页 | 按刊号 `94503X` 自动发现最新年份和期号，再逐篇读取详情并核对刊名、年份、期号；清理作者后的机构序号并拆分去重，提取摘要、日期、起止页和关键词。摘要缺失时才会用卷期页码代替。 |
| 经济地理 | [期刊官网当期目录](https://www.jjdl.com.cn/CN/current) | 解析 Magtech 当期静态页面，提取标题、作者、摘要、栏目、DOI、链接和页面统一刊出日期；作者拆分去重，栏目写入 RSS 分类。当前没有过滤书评等文献类型，网页引文字段中的卷期页码尚未写入 RSS。 |
| 管理世界 | [国家哲学社会科学文献中心](https://www.ncpssd.org/)；[期刊官网](http://www.mwm.net.cn/web/bqym)作为频道主页 | 按刊号 `95499X` 发现最新期，逐篇读取并校验详情；规范作者，提取标题、摘要、日期、起止页、关键词及来源能够提供的 DOI。当前不按文献类型过滤，因此评论或书评类条目也会保留。 |
| 中国工业经济 | [国家哲学社会科学文献中心](https://www.ncpssd.org/) + [CHNDOI](https://www.chndoi.org/) | 按刊号 `93800A` 获取最新期及文章详情，规范作者并提取摘要、日期、页码和关键词。详情缺少 DOI 时，只在 CHNDOI 登记题名与文章题名一致、且 DOI.org 确认注册机构为 CNKI 后补入，避免按期号和顺序猜测 DOI。 |
| 世界经济 | [期刊官网当期目录](https://sjjj.magtech.com.cn/CN/current) | 解析 Magtech 当期静态页面，提取标题、作者、摘要、栏目、DOI、链接和统一刊出日期；作者拆分去重，栏目写入 RSS 分类。网页引文字段中的卷期页码尚未写入 RSS；未来若必要字段缺失，校验会阻止发布不完整文件。 |
| Econometrica | [IDEAS/RePEc 的 Wiley 系列页](https://ideas.repec.org/s/wly/emetrp.html)；[Wiley 期刊主页](https://onlinelibrary.wiley.com/journal/14680262)作为频道主页 | 从 RePEc 的月份、年份、卷、期分组中选出最新一期，再读取该期列出的每篇文章页；严格校验刊名、卷期、年份、标题、作者、摘要、页码和 DOI，并按首页排序。任一记录的必要字段不完整都会停止本次更新。日期统一记为该期月份第一天。 |
| Journal of Political Economy | [芝加哥大学出版社 eTOC](https://www.journals.uchicago.edu/action/showFeed?type=etoc&feed=rss&jc=jpe) + 出版社 RePEc/ReDIF | 先用 eTOC 确定最新卷期、封面日期和 DOI 集合，再用对应 ReDIF 文件补齐标题、独立作者、完整摘要和页码；要求两边 DOI 集合完全一致、标题匹配、页码有效。排除无作者或无 DOI 的卷首及行政条目，不把 Ahead of Print 混入当前期。中文版仅翻译标题和摘要。 |
| American Economic Review | [美国经济学会当期目录](https://www.aeaweb.org/journals/aer/current-issue)及逐篇文章页 | 从当前期识别卷、期、月份和 DOI，逐篇提取标题、独立作者、完整摘要、页码和 DOI；补全缩写末页，明确排除 `Front Matter`，并校验文章元数据与当前期一致。日期统一记为该期月份第一天。中文版仅翻译标题和摘要。 |

### 当前识别状态

最近一次完整核验：[GitHub Actions 第 11 次运行](https://github.com/AlistairZhang/journal-rss-relay/actions/runs/31602155857)，完成于 **2026-08-12 21:38（北京时间）**。本次 9 个来源均抓取成功，11 个 RSS 均通过结构及中英文一致性校验并成功发布。下表把“官网未提供摘要。”等占位文字按未识别计算；条目数会随期次变化。

| RSS | 当前期次或条目日期 | 条目数 | 标题/作者/日期 | 摘要 | 页码 | DOI | 当前未识别或限制 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |
| 经济研究 | 2026 年第 61 卷第 6 期；`editDate` 2026-08-12 | 11 | 11/11 | 11/11 | 0/11 | 0/11 | 当前未输出页码，官网详情未返回可识别 DOI；RSS 日期是编辑日期。 |
| 数量经济技术经济研究 | 2026 年第 7 期；2026-07-15 | 10 | 10/10 | 10/10 | 10/10 | 0/10 | 国家哲社详情当前没有可识别 DOI，且未配置外部 DOI 补全。 |
| 经济地理 | 2026 年第 46 卷第 5 期；2026-05-26 | 25 | 25/25 | 24/25 | 0/25 | 24/25 | `评《跨域生态环境风险全过程治理机制研究》`是一条书评，官网未提供摘要和 DOI；网页中虽有页码，但当前生成器尚未输出。 |
| 管理世界 | 2026 年第 6 期；2026-06-05 | 15 | 15/15 | 15/15 | 15/15 | 0/15 | 国家哲社详情当前没有可识别 DOI；当前目录保留了 3 条评论或书评式条目。 |
| 中国工业经济 | 2026 年第 6 期；2026-06-30 | 9 | 9/9 | 9/9 | 9/9 | 9/9 | 本表所列字段均完整；DOI 来自详情接口或经过题名及注册机构双重核验后的补全。 |
| 世界经济 | 2026 年第 49 卷第 8 期；2026-08-10 | 7 | 7/7 | 7/7 | 0/7 | 0/7 | 网页中虽有页码，但当前生成器尚未输出；官网当前没有被解析到 DOI。 |
| Econometrica | Vol. 94, No. 4；2026-07-01 | 12 | 12/12 | 12/12 | 12/12 | 12/12 | 本表所列字段均完整；日期是期次月份第一天，不是逐篇在线发布日期。 |
| Journal of Political Economy | Vol. 134, No. 7；2026-07-01 | 7 | 7/7 | 7/7 | 7/7 | 7/7 | 本表所列字段均完整；日期采用期刊封面日期。 |
| Journal of Political Economy中文版 | 同英文原版 | 7 | 7/7 | 7/7（AI 译文） | 7/7 | 7/7 | 题录字段与原版一致；译文未经人工审校。 |
| American Economic Review | Vol. 116, No. 8；2026-08-01 | 11 | 11/11 | 11/11 | 11/11 | 11/11 | 本表所列字段均完整；日期是期次月份第一天。 |
| American Economic Review中文版 | 同英文原版 | 11 | 11/11 | 11/11（AI 译文） | 11/11 | 11/11 | 题录字段与原版一致；译文未经人工审校。 |

本次运行中，JPE 的 7 篇文章和 AER 的 11 篇文章全部命中已有翻译缓存，翻译缓存没有变化，因此没有调用翻译 API。所有现有 RSS 条目的标题、作者、日期、摘要字段和 GUID 均非空；每位作者均以独立的 `dc:creator` 保存，未发现单条内作者重复。

“未识别”只表示上游当前未提供该字段，或程序尚未稳定地把该字段写入 RSS，不表示论文一定不存在相应信息。
