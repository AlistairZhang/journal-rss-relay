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

### 统一处理规则

- 中文期刊不查询、不补全、不校验、不输出 DOI，状态表也不汇报 DOI。英文期刊仍保留出版机构提供的 DOI，并用它作为稳定标识和翻译缓存键。
- 每条文献优先链接到对应期刊的官网文章页。只有在题录来源无法可靠映射到官网文章时，才退回期刊官网首页；不再把文献中心页面作为阅读链接，也不会凭猜测构造文章地址。
- 自动过滤书评、读后感、卷首语、编者按、目录、征稿启事和行政信息等非文献条目。过滤采用来源类型、栏目和明确标题模式，不会仅因为标题含“综述”“评述”“Comment”或“Review”就删除正规学术论文。
- 标题、作者、日期、摘要等必要字段缺失，作者重复，文章链接不属于相应期刊官网，或中英文版本元数据不一致时，本次发布会被阻止，线上继续保留上一次有效版本。

### 数据来源与处理方式

| 期刊 | 数据来源 | 处理措施 |
| --- | --- | --- |
| 经济研究 | [《经济研究》AJ-CASS 官网](https://erj.ajcass.com/#/index)及其公开接口 | 先由官网接口识别当前期，再逐篇读取详情；提取标题、作者、摘要、年卷期和编辑日期。中文作者被拆分为独立创建者并去重，条目直接链接官网详情路由。当前 RSS 日期采用官网 `editDate`，不一定等同于纸刊出版日。 |
| 数量经济技术经济研究 | [期刊官网 RSS](https://www.jqte.net/download_upload_file.aspx?file_name=/rss/sljjjsjjyj/cn/current.xml)优先；[国家哲学社会科学文献中心](https://www.ncpssd.org/)兜底 | 优先读取官网当前期题录；GitHub 节点遇到 502 时自动改用文献中心详情。无论采用哪个题录源，都把作者拆分为独立创建者，并将每条链接精确转换到 `www.jqte.net` 官网文章页；既有 GUID 保持不变。 |
| 经济地理 | [期刊官网当期目录](https://www.jjdl.com.cn/CN/current) | 解析 Magtech 当期静态页面，提取标题、作者、摘要、栏目、官网文章链接和统一刊出日期；作者拆分去重，栏目写入 RSS 分类，书评等非文献条目在生成前过滤。网页引文字段中的卷期页码目前尚未写入 RSS。 |
| 管理世界 | [期刊官网当期目录](http://www.mwm.net.cn/web/bqym)优先；[国家哲学社会科学文献中心](https://www.ncpssd.org/)兜底 | 优先抓取官网最新一期及每篇详情页，完整作者和页码从官网标准引文提取，避免“某某 等”造成作者缺失；若 GitHub 节点访问官网超时，则改用文献中心题录，过滤非文献后把条目链接退回期刊官网首页。 |
| 中国工业经济 | [国家哲学社会科学文献中心](https://www.ncpssd.org/) | 按刊号 `93800A` 获取最新期及文章详情，清理作者机构序号，提取标题、作者、摘要、发布日期、页码和关键词。由于文献中心 ID 无法稳定映射到期刊官网文章 ID，条目统一退回[期刊官网首页](https://ciejournal.ajcass.com/)，不再链接文献中心文章页。 |
| 世界经济 | [期刊官网当期目录](https://sjjj.magtech.com.cn/CN/current) | 解析 Magtech 当期静态页面，提取标题、作者、摘要、栏目、官网文章链接和统一刊出日期；作者拆分去重，栏目写入 RSS 分类。网页引文字段中的卷期页码目前尚未写入 RSS。 |
| Econometrica | [IDEAS/RePEc 的 Wiley 系列页](https://ideas.repec.org/s/wly/emetrp.html)；[Econometric Society 期刊主页](https://www.econometricsociety.org/publications/econometrica) | 从 RePEc 的月份、年份、卷、期分组中选出最新一期，再读取该期每篇文章元数据；严格校验刊名、卷期、年份、标题、作者、摘要、页码和 DOI，按首页排序，并将链接直接指向 Econometric Society 官网 DOI 路由。日期统一记为该期月份第一天。 |
| Journal of Political Economy | [芝加哥大学出版社 eTOC](https://www.journals.uchicago.edu/action/showFeed?type=etoc&feed=rss&jc=jpe) + 出版社 RePEc/ReDIF | 先用 eTOC 确定最新卷期、封面日期和 DOI 集合，再用对应 ReDIF 文件补齐标题、独立作者、完整摘要和页码；要求两边 DOI 集合完全一致、标题匹配、页码有效。排除无作者或无 DOI 的卷首及行政条目，不把 Ahead of Print 混入当前期。中文版仅翻译标题和摘要。 |
| American Economic Review | [美国经济学会当期目录](https://www.aeaweb.org/journals/aer/current-issue)及逐篇文章页 | 从当前期识别卷、期、月份和 DOI，逐篇提取标题、独立作者、完整摘要、页码和 DOI；补全缩写末页，明确排除 `Front Matter`，并校验文章元数据与当前期一致。日期统一记为该期月份第一天。中文版仅翻译标题和摘要。 |

### 数据源变更说明

原 Gitee 项目中，《数量经济技术经济研究》和《管理世界》都直接使用期刊官网。2026-08-12 迁移到 GitHub 时，GitHub Actions 访问前者官网 RSS [连续返回 HTTP 502](https://github.com/AlistairZhang/journal-rss-relay/actions/runs/31591318513)，访问后者官网[连续超时](https://github.com/AlistairZhang/journal-rss-relay/actions/runs/31591678651)；为先让自动更新流程跑通，两刊曾临时改用国家哲学社会科学文献中心。这是运行环境兼容处理，不是因为文献中心更权威，也不是数据质量方面的选择。现在采用“官网优先、文献中心兜底”，兼顾官网时效性与定时任务稳定性。

### 当前识别状态

以下状态来自 2026-08-12 22:59（北京时间）完成的 [GitHub Actions 云端全量试跑](https://github.com/AlistairZhang/journal-rss-relay/actions/runs/31609481924)。本次 11 个 RSS 全部生成并通过校验；条目数会随期次变化。

#### 中文期刊

| RSS | 当前期次或条目日期 | 条目数 | 标题/作者/日期 | 摘要 | 页码 | 点击跳转 | 当前未识别或限制 |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |
| 经济研究 | 2026 年第 61 卷第 6 期；`editDate` 2026-08-12 | 11 | 11/11 | 11/11 | 0/11 | 11/11 官网文章路由 | 当前未输出页码；RSS 日期是编辑日期。 |
| 数量经济技术经济研究 | 2026 年第 7 期；2026-07-15 | 10 | 10/10 | 10/10 | 10/10 | 10/10 官网文章页 | 本次云端访问官网 RSS 返回 502，自动改用文献中心题录；文章链接仍精确映射到官网。 |
| 经济地理 | 2026 年第 46 卷第 5 期；2026-05-26 | 24 | 24/24 | 24/24 | 0/24 | 24/24 官网文章页 | 已过滤 1 条书评；网页中虽有页码，但当前生成器尚未输出。 |
| 管理世界 | 2026 年第 6 期；2026-06-05 | 12 | 12/12 | 12/12 | 12/12 | 12/12 期刊官网首页 | 本次云端访问官网超时，自动改用文献中心题录并过滤 3 条书评/读后感；文献中心目前落后于官网，且文章 ID 无法可靠映射，故链接退回官网首页。 |
| 中国工业经济 | 2026 年第 6 期；2026-06-30 | 9 | 9/9 | 9/9 | 9/9 | 9/9 期刊官网首页 | 文献中心当前仍比期刊官网目录滞后一期，且无法可靠映射官网文章 ID。 |
| 世界经济 | 2026 年第 49 卷第 8 期；2026-08-10 | 7 | 7/7 | 7/7 | 0/7 | 7/7 官网文章页 | 网页中虽有页码，但当前生成器尚未输出。 |

#### 英文期刊

| RSS | 当前期次或条目日期 | 条目数 | 标题/作者/日期 | 摘要 | 页码 | DOI | 点击跳转 | 当前未识别或限制 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| Econometrica | Vol. 94, No. 4；2026-07-01 | 12 | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 官网文章页 | 日期是期次月份第一天，不是逐篇在线发布日期。 |
| Journal of Political Economy | Vol. 134, No. 7；2026-07-01 | 7 | 7/7 | 7/7 | 7/7 | 7/7 | 7/7 官网文章页 | 日期采用期刊封面日期。正式学术文章 `A Comment` 有作者、摘要和页码，予以保留。 |
| Journal of Political Economy中文版 | 同英文原版 | 7 | 7/7 | 7/7（AI 译文） | 7/7 | 7/7 | 与原版相同 | 题录字段与原版一致；译文未经人工审校。 |
| American Economic Review | Vol. 116, No. 8；2026-08-01 | 11 | 11/11 | 11/11 | 11/11 | 11/11 | 11/11 官网文章页 | 日期是期次月份第一天；已过滤 `Front Matter`。 |
| American Economic Review中文版 | 同英文原版 | 11 | 11/11 | 11/11（AI 译文） | 11/11 | 11/11 | 与原版相同 | 题录字段与原版一致；译文未经人工审校。 |

“未识别”只表示上游当前未提供该字段，或程序尚未稳定地把该字段写入 RSS，不表示论文一定不存在相应信息。
