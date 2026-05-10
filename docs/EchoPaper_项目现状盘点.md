# EchoPaper 项目现状盘点

日期：2026-05-10  
范围：当前代码仓库 `c:\Users\mao\Desktop\echoplayer`

## 一、项目概述

EchoPaper 是一个基于 FastAPI + Jinja2 + SQLite 的试卷智能审查系统 MVP。系统面向 A/B 卷 PDF 审查场景，当前以“代码版主链路”为稳定输出，以“Coze Agent 链路”为异步对照输出，支持 PDF 文本提取、切题、查重、历史题库比对、错别字检查、人工复核、报告展示和报告导出。

当前产品形态是一个本地 Web 应用：教师上传 A 卷或 A/B 卷 PDF 后，系统先生成代码版审查报告；Coze Agent 链路在后台运行，完成后可在报告页追加展示 Agent 明细。

## 二、技术栈

| 类别 | 当前技术 |
| --- | --- |
| Web 框架 | FastAPI `0.115.12` |
| 模板引擎 | Jinja2 `3.1.6` |
| Web 服务 | uvicorn `0.34.0` |
| 数据库 | SQLite |
| PDF 文本提取 | pdfplumber `0.11.5` |
| 文本相似度 | rapidfuzz `3.13.0` + 本地字符 n-gram 轻量向量 |
| 文件上传 | python-multipart `0.0.20` |
| HTTP 调用 | requests `2.32.3`、httpx `0.28.1` |
| 环境变量 | python-dotenv `1.0.1` |
| 测试 | pytest `8.3.5` |
| Agent 工作流 | Coze Workflow API，Nuwa 保留兼容 |
| 可选 OCR | Tesseract OCR Provider，依赖 `pytesseract`、`pdf2image` 和系统 Tesseract/Poppler，默认未启用 |

## 三、系统架构

### 3.1 总体链路

```text
用户上传 A/B 卷 PDF
  -> FastAPI Web 路由接收表单和文件
  -> 保存到 data/temp_uploads 临时目录
  -> 加载历史题库 data/datasets/history_bank
  -> 代码版主链路同步运行
       -> RoutedPdfParser 提取 PDF 文本/图片占位/OCR 文本
       -> RuleQuestionSplitter 规则切题
       -> CodeSimilarityComparator 查重和历史题库比对
       -> LocalSpellcheckProvider 本地错字/标点检查
       -> ReportBuilder 生成报告上下文
       -> ReviewStore 写入 SQLite 复核会话和报告快照
  -> Agent/Coze 链路提交后台任务
       -> 复制 PDF 到 data/agent_jobs/{job_id}
       -> Coze 切题/错字/查重能力异步运行
       -> 任务状态、摘要和完整 payload 写入 SQLite
  -> Jinja2 渲染报告页
  -> 前端轮询 /api/agent-jobs/{job_id} 回填 Agent 明细
```

### 3.2 主要模块

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| 应用入口 | `app/main.py` | 初始化 FastAPI、运行目录、SQLite、历史题库服务、Agent Job Store |
| Web 路由 | `app/routes/web.py` | 首页、上传审查、历史题库管理、复核状态、报告快照、PDF 导出、Agent job 查询 |
| Coze 路由 | `app/routes/coze.py` | Coze 工作流执行、切题接口和健康检查 |
| Nuwa 路由 | `app/routes/nuwa.py` | 兼容旧 Nuwa 工作流接口 |
| PDF 解析 | `app/services/pdf_parser.py` | pdfplumber 文本提取、图片占位符、OCR 路由和解析诊断 |
| OCR | `app/services/ocr.py` | 可插拔 OCR Provider，当前支持 Tesseract 配置入口 |
| 切题 | `app/services/question_splitter.py` | 规则切题、前言剥离、题号识别、碎片合并、公式符号清洗、Coze 切题解析 |
| 查重 | `app/services/comparator.py` | 卷内查重、A/B 交叉查重、历史题库比对、Coze/Nuwa 智能比对解析 |
| 历史题库 | `app/services/history_bank.py` | 扫描历史 PDF、解析题目、科目推断、缓存和摘要 |
| 历史索引 | `app/services/history_vector_index.py` | 本地轻量字符 n-gram 索引构建、持久化和复用 |
| 错别字检查 | `app/services/spellcheck/*` | 本地规则检查、Coze 检查、Nuwa 检查和跳过占位 Provider |
| 双链路编排 | `app/services/dual_run.py` | 代码版与 Agent 版 Pipeline 编排、超时和错误元数据 |
| Agent 后台任务 | `app/services/agent_jobs.py` | 后台线程任务、PDF 复制保护、状态查询、过期工作目录清理 |
| 报告构建 | `app/services/report_builder.py` | Dashboard、查重表、错字表、双链路对照、导出 payload |
| PDF 报告 | `app/services/report_pdf.py` | 无外部依赖的摘要版 PDF 生成 |
| 持久化 | `app/services/review_store.py` | 复核会话、复核项、报告快照、导出历史、Agent job 状态和 payload |
| Coze 导出 | `app/services/coze_export.py` | 将历史题库 PDF 导出为 Coze 知识库“一题一行”文本 |

### 3.3 数据目录

| 路径 | 用途 |
| --- | --- |
| `data/echopaper.db` | SQLite 主库 |
| `data/temp_uploads/` | 当前请求上传 PDF 临时目录，请求结束后清理 |
| `data/datasets/history_bank/` | 历史题库 PDF 存放目录 |
| `data/index/history_bank_lightweight_index.json` | 历史题库轻量向量索引 |
| `data/agent_jobs/{job_id}/` | Agent 后台任务独立工作目录 |
| `data/exports/coze_history_bank/` | Coze 知识库导出结果 |

## 四、目前已实现的功能

### 4.1 上传和审查入口

- 已实现首页表单，支持录入教师姓名、教师工号、科目。
- 已实现 A 卷必传、B 卷可选的 PDF 上传。
- 已实现 PDF 文件类型校验。
- 已实现请求级临时文件目录创建、保存和清理。

### 4.2 PDF 解析

- 已实现基于 `pdfplumber` 的文本版 PDF 提取。
- 已实现每页图片对象检测，并将图片写成 `[IMAGE page=... index=... bbox=...]` 占位符，避免配图题静默丢失。
- 已实现 `RoutedPdfParser` 解析诊断，可提示文字版、含图片、疑似扫描版、纯图片页等情况。
- 已实现 OCR Provider 接口和 Tesseract Provider 配置入口。
- 已实现 OCR 成功时追加 `[OCR_TEXT]` 文本，失败时保留图片占位并记录错误说明。

### 4.3 题目切分

- 已实现规则切题，支持中文数字题号、阿拉伯数字题号、括号小题号等常见形式。
- 已实现试卷前言剥离，可跳过命题教师、试卷说明、考生注意事项等非题目内容。
- 已实现大题标题过滤，避免把“选择题”“填空题”“简答题”等标题误判为题目。
- 已实现 PDF 抽取后题号粘连的文本规范化。
- 已实现选项碎片合并，可将被拆散的 A/B/C/D 选项合回同一道题。
- 已实现公式私有区字符修复，例如将部分 PDF 乱码恢复为 `∧`、`∨`、`→`、`∈`、`⊆`、`∩`、`∪` 等符号。
- 已实现低置信度切题结果标记，并在报告中形成待人工复核提示。

### 4.4 查重和历史题库比对

- 已实现 A 卷卷内查重。
- 已实现 B 卷卷内查重。
- 已实现 A/B 卷交叉查重。
- 已实现上传试卷与历史题库比对。
- 已实现相似度阈值分级：`>=95%` 为高度重复，`85%-94%` 为疑似重复。
- 已实现每道上传题最多保留 3 条历史命中。
- 已实现 `rapidfuzz` 文本相似度和本地字符 n-gram 轻量向量双通道命中。
- 已实现历史题库轻量索引持久化，索引可落盘复用。
- 已实现查重文本规范化，过滤 `[IMAGE ...]` 和 `[OCR_TEXT]` 标记，降低图片占位符造成误报的风险。

### 4.5 历史题库管理

- 已实现历史题库页面 `/history-bank`。
- 已实现历史题库 PDF 上传。
- 已实现非 PDF 文件跳过。
- 已实现同名文件自动生成唯一文件名。
- 已实现历史题库 PDF 删除，并防止路径穿越。
- 已实现历史题库摘要、科目推断、关键词/科目筛选。
- 已实现历史题库缓存失效和刷新。

### 4.6 错别字和标点检查

- 已实现本地错别字检查 Provider。
- 已覆盖常见错别字词典、重复标点、配对标点错误、重复字等规则。
- 已实现错字问题结构化输出，包括问题类型、原文片段、问题文本、建议、置信度等字段。
- 已实现 Coze/Nuwa 错字检查 Provider 兼容结构。
- 已实现 Coze 错字检查未启用时的跳过占位 Provider，避免把本地结果伪装成 Agent 成功。

### 4.7 Coze Agent 链路

- 已实现 Coze Workflow API 服务封装。
- 已实现 Coze 切题工作流调用和结果解析。
- 已实现 Coze 智能比对调用和 `plagiarism_details` 解析。
- 已实现 Coze 错字检查调用和结构化解析。
- 已实现 Agent 链路异步化：代码版报告先返回，Coze Agent 后台运行。
- 已实现 Agent job 状态查询接口 `/api/agent-jobs/{job_id}`。
- 已实现 Agent job 状态、摘要和完整结果 payload 写入 SQLite。
- 已实现服务重启或内存任务丢失后的 SQLite fallback 查询。
- 已实现报告页轮询 Agent job，并在完成后回填 Agent 切题、查重和错字明细。
- 已实现 Agent job 工作目录过期清理。

### 4.8 报告和导出

- 已实现审查报告页，包含 Dashboard、历史题库状态、切题质量、错字清单、重复明细、人工复核区、双链路对照和 Agent 状态。
- 已实现报告快照持久化。
- 已实现报告快照 API 和快照页面 `/reports/{session_id}`。
- 已实现 JSON 导出 payload。
- 已实现摘要版 PDF 导出接口 `/api/reports/export-pdf`。
- 已实现 PDF 导出历史记录写入 SQLite。
- 已实现中文文件名下载头兼容处理。

### 4.9 人工复核持久化

- 已实现复核会话 `review_sessions`。
- 已实现复核项 `review_items`。
- 已实现复核状态选项：待确认、确认重复、排除误报。
- 已实现复核状态更新接口 `PATCH /api/review-items/{item_id}`。
- 已实现报告明细行绑定复核项 ID。

### 4.10 Coze 知识库导出

- 已实现历史题库 PDF 导出为 Coze 知识库文本。
- 已实现一题一行格式：`###QUESTION### ... ###END###`。
- 已实现批量导出脚本 `scripts/export_history_bank_to_coze.py`。
- 已实现 `manifest.json` 汇总成功、失败、页数、题目数、输出路径。
- 已实现 `--limit`、指定输入目录、指定输出目录、指定科目等参数。

### 4.11 自动化测试

- 当前测试覆盖 PDF 解析、OCR 配置、切题、查重、历史题库、历史索引、Coze 导出、双链路、Agent job、报告快照、复核持久化和 PDF 导出等核心模块。
- `docs/EchoPaper_升级方案.md` 中最近记录的全量测试结果为 `101 passed`。

## 五、没有实现或尚未完整实现的功能

| 功能 | 当前状态 | 说明 |
| --- | --- | --- |
| 真实生产级 OCR | 部分接入 | 已有 Tesseract Provider 和 OCR 路由，但默认未启用，本机依赖未完整安装；PaddleOCR 未接入 |
| 图片题内容识别 | 未完整实现 | 当前只能保留图片占位符；图片里的题干、选项、公式不能自动识别为结构化题目 |
| 多模态试卷理解 | 未实现 | 暂未使用视觉模型识别图片、表格、几何图、扫描件排版 |
| 语义向量模型查重 | 未实现 | 当前是字符 n-gram 轻量向量，不是 `sentence-transformers`、FAISS 或专业语义检索 |
| FAISS 大规模索引 | 未实现 | 当前索引是 JSON 持久化轻量索引，适合小规模或中等规模验证 |
| 按科目拆分历史索引 | 未实现 | 当前历史题库索引按整体题库生成一个文件 |
| 完整 HTML/CSS 报告 PDF | 未实现 | 当前 PDF 是摘要版，不是 `report.html` 的完整视觉复刻 |
| PDF 水印、分页样式、复杂差异高亮 | 未实现 | 需要后续引入 WeasyPrint、reportlab 或其他渲染方案 |
| Agent 结果重算整页双链路对照 | 未完整实现 | Agent 完成后已追加展示明细，但尚未把结果重新合并进原有全部对照表和 Dashboard |
| Agent 完成实时推送 | 未实现 | 当前使用前端轮询，不是 WebSocket/SSE |
| Agent job 管理页面 | 未实现 | 已有状态接口和自动清理，但没有独立的后台任务列表/清理页面 |

以下为以后迭代升级的内容，本次无需优化
| 历史题库版本管理 | 未实现 | 目前支持上传、删除、刷新，但没有版本、标签、来源、批次管理 |
| 用户账号和权限 | 未实现 | 当前没有登录、角色、权限隔离 |
| 多教师/多机构数据隔离 | 未实现 | SQLite 本地单库，尚无租户隔离设计 |

## 六、效果不好的功能或主要风险

| 功能点 | 当前问题 | 风险 |
| --- | --- | --- |
| 扫描版 PDF 处理 | OCR 默认未启用；Tesseract 对中文复杂试卷版式可能效果一般 | 扫描卷、图片卷无法稳定切题和查重 |
| 图片题 | 只能保留 `[IMAGE ...]` 占位符 | 报告知道“这里有图”，但不知道图里具体内容 |
| 规则切题 | 对常见题号有效，但对非常规版式、跨栏、表格、复杂公式题仍可能漏切或误切 | 后续查重和错字检查会被错误切题放大影响 |
| 本地错字检查 | 规则和词典覆盖有限 | 可能漏报语境性错字，也可能误报正常专业术语 |
| 文本相似度查重 | rapidfuzz 对改写、同义表达、语义相近但字面不同的题召回有限 | 低字面重合的重复题可能漏检 |
| 轻量向量查重 | 字符 n-gram 能提升部分中文重排召回，但不是语义模型 | 专业题目、长题干改写、跨语言表达仍不稳定 |
| Coze Agent 链路 | 依赖外部工作流、Token、网络和工作流输出格式 | 超时、接口错误或返回结构变化会影响 Agent 对照 |
| Agent 明细回填 | 当前是追加展示，不是整页报告重算 | 用户看到的代码版主报告和 Agent 后回填区之间可能仍需人工理解差异 |
| PDF 摘要导出 | 能生成合法摘要 PDF，但不是完整报告复刻 | 适合归档摘要，不适合替代完整 Web 报告 |
| 历史题库加载 | PDF 数量大时仍可能带来解析耗时；轻量索引不是专业检索引擎 | 大规模题库性能和召回质量仍需升级 |
| SQLite 单库 | 适合 MVP 和本地闭环 | 并发、多用户、备份恢复和审计能力有限 |

## 七、当前运行方式

本地启动：

```powershell
uvicorn app.main:app --reload
```

推荐预览端口：

```powershell
uvicorn app.main:app --host 0.0.0.0 --port 5000
```

Coze 相关环境变量：

```env
COZE_API_URL=https://api.coze.cn/v3/workflows/run
COZE_WORKFLOW_ID=7637135521890959375
COZE_SPLIT_WORKFLOW_ID=7637166446480506899
COZE_BOT_TOKEN=your_bot_token_here
COZE_TIMEOUT=60
```

Agent 和 OCR 相关环境变量：

```env
ENABLE_ASYNC_AGENT=true
ENABLE_AGENT_COMPARE=false
ENABLE_AGENT_SPELLCHECK=false
AGENT_TIMEOUT=60
AGENT_JOB_RETENTION_SECONDS=604800

OCR_ENGINE=none
# OCR_ENGINE=tesseract
# OCR_DPI=200
# OCR_LANG=chi_sim+eng
```


