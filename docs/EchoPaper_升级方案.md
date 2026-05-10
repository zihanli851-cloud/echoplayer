# EchoPaper 试卷智能审查系统整体升级方案

版本：v2.0  
日期：2026 年 5 月

## 一、项目定位与现状

EchoPaper 是一个试卷智能审查 MVP，支持上传 A/B 卷 PDF，自动完成文本提取、切题、查重、历史题库比对、错别字检查，并生成结构化审查报告。

当前方案以代码版主链路为主结果，Agent/Coze 版作为对照链路。整体架构已具备核心骨架，但在切题精度、查重能力、持久化和扫描版支持等方面仍有升级空间。

### 1.1 现有核心链路

一次请求同时运行两条链路：

```text
代码版主链路：
pdfplumber 提取文本 -> 规则切题 -> 查重 -> 错字检查 -> 报告生成

Agent/Coze 对照链路：
复用本地文本提取 -> Coze 切题/查重/错字 -> 与代码版对照
```

当前限制：

- 仅支持可直接提取文本的 PDF。
- 切题为基础规则。
- 查重为纯文本相似度。
- 人工复核状态未持久化。
- Agent 链路同步等待，存在超时风险。

### 1.2 现有查重阈值

| 相似度区间 | 判定结果 | 处理方式 |
| --- | --- | --- |
| >= 95% | 高度重复 | 进入主要命中列表，强制标记 |
| 85% - 94% | 疑似重复 | 进入主要命中列表，待人工确认 |
| < 85% | 差异较大 | 不进入主要命中列表 |

## 二、根目录交付包说明

当前根目录有 3 个 zip 包：

| 文件 | 类型 | 用途 |
| --- | --- | --- |
| `minimal_question_split_pack.zip` | 可运行切题引擎包 | 独立 PDF 预处理工具，可从 PDF 提取文本、规则切题，并导出 Coze 知识库上传格式 |
| `Workflow-qieti-draft-4360.zip` | Coze 工作流导出包 | 切题工作流，工作流 ID 为 `7637166446480506899`，名称 `qieti` |
| `Workflow-zhinengduibi-draft-4429.zip` | Coze 工作流导出包 | 综合审查/智能对比工作流，工作流 ID 为 `7637135521890959375`，名称 `zhinengduibi` |

其中，`minimal_question_split_pack.zip` 是当前可直接复用的本地切题引擎；另外两个 zip 是 Coze 平台导出的工作流包，用于 Agent 侧切题、错字校验、智能对比和查重。

## 三、切题引擎现状与升级

### 3.0 开发进度

- [x] ~~接入公式符号清洗 `normalize_formula_glyphs`~~
- [x] ~~接入升级版切题碎片合并逻辑~~
- [x] ~~补充切题引擎回归测试~~
- [x] ~~将 `minimal_question_split_pack` 的 Coze 知识库导出能力接入主项目脚本~~
- [x] ~~PDF 图片对象进入文本链路时保留占位符，避免静默漏图~~
- [x] ~~将低置信度切题结果标记到报告或复核页~~

### 3.1 `minimal_question_split_pack` 包解析

`minimal_question_split_pack.zip` 是一个独立的试卷预处理工具包。经逐文件分析，其实现比主项目中描述的“基础规则切题”更完整，可作为切题引擎的升级版本接入。

包结构：

| 文件 | 功能 | 与主项目的关系 |
| --- | --- | --- |
| `pdf_parser.py` | `pdfplumber` PDF 文本提取，定义 `TextExtractionProvider` 抽象基类 | 接口一致，可替换或复用 |
| `question_splitter.py` | 规则切题、碎片合并、前言剥离、文本规范化 | 同名同接口，但功能更完整 |
| `schemas.py` | `Question` / `UploadedPaper` 数据结构 | 与主项目结构相近 |
| `coze_export.py` | 公式符号清洗、科目推断、Coze 格式导出 | 主项目当前没有该模块，是新增能力 |
| `run_export.py` | 命令行批量导出入口 | 可复用为历史题库批量处理工具 |
| `run_export.bat` | Windows 批处理入口 | 便于非开发用户运行 |
| `requirements.txt` | 最小依赖 | 独立运行所需依赖 |

### 3.2 升级版切题器关键能力

相比主项目基础规则版，该包的切题器多出以下能力：

1. 碎片合并  
   PDF 提取后，选项 A-D 可能被切成多个“题”。升级版切题器会根据题号是否相同、选项块是否完整、内容是否为悬挂片段等信号，将碎片合并回同一道完整题目。

2. 文本规范化  
   针对 PDF 排版导致的题号粘连问题，通过正则在明显题号前插入换行。例如将 `...选A。3.下列...` 规范化为题号独立成行，提升后续识别准确率。

3. 前言剥离  
   维护“命题教师”“试卷说明”“考生注意事项”等触发词，跳过封面、表头、考试说明，直到识别到第一个真实题号才开始切题。

4. 大题标题过滤  
   识别“选择题”“填空题”“简答题”“计算题”等大题标题。遇到这些行时仅作为结构标题处理，不误判为题目。

5. 公式符号修复  
   `coze_export.py` 中的 `normalize_formula_glyphs` 内置 PDF 私有区字符映射，可将常见乱码恢复为 `∧`、`∨`、`→`、`∈`、`⊆`、`∩`、`∪` 等正常 Unicode 符号，对数学、逻辑类试卷尤其重要。

### 3.3 接入方式

由于该包与主项目部分接口名称一致，建议以“替换切题实现 + 复用公式清洗函数”的方式接入：

```python
from coze_export import normalize_formula_glyphs

text, page_count = CodePdfParser().extract(pdf_path)
text = normalize_formula_glyphs(text)
questions = RuleQuestionSplitter().split(text, paper_id)
```

注意：

- `coze_export.py` 中的 `sanitize_block` 和 `_build_question_line` 是为 Coze 知识库格式设计的，不需要进入主项目报告链路。
- 主项目报告链路只需要复用 PDF 解析、切题和公式符号清洗能力。
- 若直接替换主项目文件，应先跑现有切题、历史题库和报告相关测试，确保数据模型字段兼容。

### 3.4 本轮已完成代码改动

本轮已将 `minimal_question_split_pack.zip` 中适合主项目的切题能力合并进现有代码链路，未引入新的运行时依赖。

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/services/question_splitter.py` | 新增 `FORMULA_GLYPH_MAP` 和 `normalize_formula_glyphs` | 在切题前修复 PDF 私有区公式符号，将 `\uf0xx` 类乱码恢复为常见数学/逻辑符号 |
| `app/services/question_splitter.py` | 增强中文题号、阿拉伯题号正则 | 支持 `1)`、`1）`、中文题号后接逗号/冒号/括号等更多 PDF 抽取形态 |
| `app/services/question_splitter.py` | 新增选项标签与碎片识别规则 | 识别 `[A]`、`A.`、`A、` 等选项标签，以及短数字片段、悬挂词结尾片段 |
| `app/services/question_splitter.py` | 新增 `_coalesce_fragmented_questions` | 规则切题后，把明显被 PDF 抽取拆散的选项片段合并回上一道题 |
| `tests/test_question_splitter.py` | 新增碎片合并测试 | 验证 `A/B` 与 `C/D` 被误切成两题时可合并为一道完整题 |
| `tests/test_question_splitter.py` | 新增公式符号清洗测试 | 验证 `\uf0ae`、`\uf0ce`、`\uf0c7` 等私有区字符可恢复为 `→`、`∈`、`∩` |
| `tests/test_question_splitter.py` | 新增短题防误合并测试 | 验证 `1. 是 / 2. 否` 这类独立短题不会被碎片合并逻辑错误合并 |
| `tests/test_question_splitter.py` | 新增中文题号扩展标点测试 | 验证 `一：`、`二）`、`三，` 与文档描述一致，能被识别为题号 |

关键实现逻辑：

```text
原始 PDF 文本
  -> normalize_formula_glyphs 修复公式私有区符号
  -> normalize_question_text 规范换行和题号粘连
  -> strip_preamble_lines 剥离试卷前言
  -> 规则切题生成初始 Question 列表
  -> _coalesce_fragmented_questions 合并明显碎片
  -> 返回稳定的 Question 列表
```

碎片合并触发条件：

- 下一段与当前段题号相同。
- 当前题选项块不完整，下一段以选项标签开头。

审查修正：

- 已移除“当前题过短就直接并入下一题”的宽松规则，避免把真实短题误合并。
- 中文题号正则已补齐逗号、冒号、右括号等扩展标点，避免文档和实际行为不一致。

验证结果：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_question_splitter.py -q
# 12 passed

.\.venv\Scripts\python.exe -m pytest tests -q
# 38 passed
```

### 3.5 Coze 知识库导出能力接入

本轮已将 `minimal_question_split_pack.zip` 中的 Coze 知识库导出能力接入主项目，形成可复用服务和命令行脚本。该能力用于把本地历史题库 PDF 转为 Coze 适合上传的“一题一行”文本格式。

新增文件：

| 文件 | 用途 | 逻辑说明 |
| --- | --- | --- |
| `app/services/coze_export.py` | Coze 知识库导出服务 | 复用主项目 `CodePdfParser`、`RuleQuestionSplitter`、`normalize_formula_glyphs`，避免形成孤立切题逻辑 |
| `scripts/export_history_bank_to_coze.py` | 命令行导出入口 | 默认读取 `data/datasets/history_bank`，输出到 `data/exports/coze_history_bank`；支持 `--limit` 小批量烟测 |
| `tests/test_coze_export.py` | 导出服务测试 | 覆盖一题一行格式、公式符号修复、科目推断、manifest 汇总、进度回调、limit 限制和真实 split 调用 |

导出格式：

```text
###QUESTION### paper_id: H1 | paper_label: 示例试卷 | subject: 离散数学 | question_no: 1 | order: 1 | content: 题目内容 [NL] A. 选项 ###END###
```

使用方式：

```powershell
# 默认导出 data/datasets/history_bank
.\.venv\Scripts\python.exe scripts\export_history_bank_to_coze.py

# 指定输入目录和输出目录
.\.venv\Scripts\python.exe scripts\export_history_bank_to_coze.py "D:\history_pdf" --output-dir "D:\coze_export"

# 强制指定科目
.\.venv\Scripts\python.exe scripts\export_history_bank_to_coze.py "D:\history_pdf" --output-dir "D:\coze_export" --subject "离散数学"

# 只导出前 2 份 PDF，用于快速验证
.\.venv\Scripts\python.exe scripts\export_history_bank_to_coze.py --output-dir data\exports\coze_history_bank_smoke --limit 2
```

输出结果：

- 每个 PDF 生成一个 `.coze.txt` 文件。
- 同时生成 `manifest.json`，记录成功数量、失败数量、来源 PDF、输出路径、页数和题目数。
- PDF 解析失败时不会中断整个批次，会在 manifest 中记录失败原因。

审查修正：

- 修复脚本直接运行时 `ModuleNotFoundError: No module named 'app'` 的问题，脚本现在会把项目根目录加入 `sys.path`。
- 新增 `--limit` 参数和逐文件进度输出，避免真实历史题库较大时看起来像卡死。
- 科目推断已避免把 `echoplayer`、`history_bank`、`tmp` 等通用目录名误判为学科。
- 已用真实 `data/datasets/history_bank` 做 `--limit 2` 烟测，成功生成 `.coze.txt` 和 `manifest.json`。

验证结果：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_coze_export.py -q
# 7 passed

.\.venv\Scripts\python.exe -m py_compile scripts\export_history_bank_to_coze.py app\services\coze_export.py
# passed

.\.venv\Scripts\python.exe -m pytest tests -q
# 45 passed
```

### 3.6 图片题占位符修正

审查发现：当前切题引擎和 Coze 导出最终都输出纯文本。如果 PDF 中存在图片对象，旧逻辑只读取 `page.extract_text()`，会把图片对象静默漏掉。尤其是图片题、配图题、扫描页，会导致 `.coze.txt` 和报告里完全看不到这部分内容。

本轮已完成“图片不静默丢失”的第一步修正：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/services/pdf_parser.py` | 新增图片占位符拼接 | 每页读取 `page.images`，生成 `[IMAGE page=... index=... bbox=...]` 并拼入提取文本 |
| `tests/test_pdf_parser.py` | 新增 PDF 图片占位测试 | 覆盖文字+图片页、纯图片页，确保图片对象不会被丢弃 |
| `tests/test_coze_export.py` | 新增导出保留图片占位测试 | 验证 `.coze.txt` 里会保留 `[IMAGE ...]` |

占位符示例：

```text
[IMAGE page=1 index=1 bbox=12.35,20,300.6,420.2]
```

当前效果：

- 文字版 PDF 中的配图不会再完全消失。
- 纯图片页会输出图片占位符，不会被当作“无文本”直接丢弃。
- Coze 导出的 `.coze.txt` 能看到图片位置，后续人工或 Agent 可以知道该题有图片。

当前限制：

- 这不是 OCR，不会识别图片里的文字。
- 如果题干本身在图片里，当前只能保留图片占位符，不能切出图片内题目。
- 真正的图片识别仍需 Sprint 2 接入 OCR 或多模态识别引擎。

验证结果：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_pdf_parser.py tests\test_coze_export.py -q
# 10 passed

.\.venv\Scripts\python.exe -m pytest tests -q
# 48 passed
```

## 四、Coze 工作流现状

### 4.1 切题工作流

文件：`Workflow-qieti-draft-4360.zip`

| 项 | 值 |
| --- | --- |
| 工作流 ID | `7637166446480506899` |
| 名称 | `qieti` |
| 描述 | 切题 |
| 输入变量 | `paper_text_data` |
| 结束节点输出 | `split_result` |

输入示例：

```json
{
  "paper_text_data": {
    "paper_id": "A",
    "subject": "chinese",
    "filename": "paper_a.pdf",
    "content": "试卷全文..."
  }
}
```

预期输出：

```json
{
  "split_result": {
    "paper_id": "A",
    "subject": "chinese",
    "questions": [
      {
        "question_id": "A-1",
        "question_no": "1",
        "order": 1,
        "content": "题目内容"
      }
    ]
  }
}
```

### 4.2 综合审查 / 智能对比工作流

文件：`Workflow-zhinengduibi-draft-4429.zip`

| 项 | 值 |
| --- | --- |
| 工作流 ID | `7637135521890959375` |
| 名称 | `zhinengduibi` |
| 描述 | 错别字与语法校验 && 智能对比和查重 |
| 输入变量 | `question_data` |
| 结束节点输出 | `output_report` |
| 知识库 ID | `7637138176842973210` |

输入示例：

```json
{
  "question_data": {
    "paper_id": "A",
    "subject": "chinese",
    "questions": [
      {
        "question_id": "A-1",
        "question_no": "1",
        "order": 1,
        "content": "请简述春天的特点。"
      }
    ]
  }
}
```

预期输出：

```json
{
  "output_report": {
    "dashboard": {
      "macro_repeat_rate": "0%",
      "total_questions_checked": 1
    },
    "error_checklist": [],
    "plagiarism_details": []
  }
}
```

需要重点关注：

- 知识库 `7637138176842973210` 必须存在且有可检索数据。
- 模型授权、额度和超时配置会直接影响工作流稳定性。
- 工作流输出字段应与主项目解析逻辑保持一致，尤其是 `output_report`、`error_checklist`、`plagiarism_details`。

## 五、完整升级方案

### 5.0 开发进度

- [x] ~~切题引擎替换：公式符号清洗 + 碎片合并~~
- [x] ~~低置信度切题结果标记：无明确题号兜底切题会进入报告复核提示~~
- [x] ~~人工复核状态持久化到 SQLite~~
- [x] ~~解析路由器：识别文字版、含图片、疑似扫描 PDF，并输出诊断提示~~
- [x] ~~OCR Provider 可插拔链路接入：扫描倾向 PDF 可调用 OCR Provider 并追加识别文本~~
- [ ] OCR 运行环境安装与真实样本验收：Tesseract/Poppler 或 PaddleOCR
- [x] ~~历史题库查重第一阶段：文本相似度 + 本地轻量向量相似度双通道命中~~
- [x] ~~向量查重索引第一阶段：持久化轻量历史题库索引 + 查重复用索引~~
- [ ] FAISS / sentence-transformers 真实语义索引接入与验收
- [x] ~~历史题库管理页第一阶段：上传、查看、刷新本地历史题库 PDF~~
- [x] ~~历史题库管理页第二阶段：科目/关键词筛选 + 单文件安全删除~~
- [x] ~~Agent/Coze 链路异步化第一阶段：代码版先返回，Agent 后台任务 + 状态查询~~
- [x] ~~Agent 后台任务持久化第一阶段：任务状态与完成摘要写入 SQLite~~
- [x] ~~Agent 完整结果持久化第一阶段：保存 result_payload，接口可返回完整 Agent 明细~~
- [x] ~~Agent 明细回填第一阶段：报告页轮询完成后展示 Agent 切题/重复/错字明细~~
- [x] ~~Agent job 工作目录清理第一阶段：过期 completed/failed 目录自动清理，SQLite 记录保留~~
- [x] ~~报告快照持久化第一阶段：export_payload 按 session_id 写入 SQLite，并提供 JSON 恢复 API~~
- [x] ~~报告 PDF 导出第一阶段：后端生成审查摘要 PDF + 页面下载按钮~~

### 5.1 架构升级总览

升级后的完整处理链路建议如下：

```text
用户上传 A/B 卷 PDF
  -> 解析路由器检测 PDF 类型
  -> 文字版走 pdfplumber，扫描版走 OCR
  -> 公式符号清洗 normalize_formula_glyphs
  -> 升级版切题引擎
  -> 并行执行卷内查重、A/B 交叉查重、历史题库检索、错字和语法检查
  -> 结果写入 SQLite
  -> 报告生成器输出 Dashboard、错字清单、重复题明细、导出数据
  -> Agent/Coze 对照链路异步运行
  -> Agent 结果完成后通过 WebSocket 或轮询补充到报告页
```

### 5.2 解析路由器

在 `CodePdfParser` 前增加一个路由判断层：

- 先用 `pdfplumber` 尝试提取文本。
- 若每页平均提取字符数低于阈值，则判定为扫描版。
- 扫描版转交 OCR 引擎处理。

建议配置：

```env
OCR_FALLBACK_THRESHOLD=50
OCR_ENGINE=paddleocr
```

推荐 OCR 方案：

| 方案 | 特点 |
| --- | --- |
| PaddleOCR | 中文识别精度更好，适合试卷场景 |
| Tesseract / pytesseract | 部署轻量，但中文复杂版式效果可能弱一些 |

#### 本轮已完成代码改动

本轮已完成解析路由器的第一阶段：检测 PDF 是否可直接提取文字、是否包含图片对象、是否疑似扫描版，并把诊断结果写入报告模块元数据。OCR 识别图片内文字尚未接入。

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/services/pdf_parser.py` | 新增 `PdfTextSnapshot` | 记录提取文本、页数、文字字符数、图片对象数量 |
| `app/services/pdf_parser.py` | 新增 `RoutedPdfParser` | 在不改变 `(text, page_count)` 返回接口的前提下，输出解析诊断 `provider_note` |
| `app/services/pdf_parser.py` | `AgentPdfParser` 透传本地解析提示 | Agent 链路复用本地 PDF 解析时，也能在报告里看到文字/图片/OCR 风险提示 |
| `app/routes/web.py` | Web 链路改用 `RoutedPdfParser` | 代码版和 Agent 版都经过解析路由器 |
| `tests/test_pdf_parser.py` | 新增解析路由器测试 | 覆盖文字版、含图片、疑似扫描、纯图片页、多文件提示累积 |

诊断逻辑：

```text
pdfplumber 提取文字 + 读取 page.images
  -> 统计 text_char_count / page_count / image_count
  -> 文字充足：标记为可直接提取文字
  -> 有图片且文字少：标记为疑似扫描版或图片题较多
  -> 纯图片页：保留 [IMAGE ...] 占位符，并提示需要 OCR
```

当前报告提示示例：

```text
A.pdf: 该 PDF 可直接提取文字。
B.pdf: 该 PDF 未提取到文字，但检测到图片对象；当前仅保留图片占位符，需要接入 OCR 后才能识别图片内文字。
```

当前限制：

- 解析路由器只负责检测和提示，不负责 OCR 识别。
- 图片内文字仍需要后续接入 PaddleOCR 或 Tesseract。
- 当前切题仍基于文本和 `[IMAGE ...]` 占位符，不能从图片里自动切题。

验证结果：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_pdf_parser.py -q
# 7 passed

.\.venv\Scripts\python.exe -m pytest tests -q
# 53 passed
```

#### OCR Provider 接入

本轮已完成 OCR 的可插拔链路接入。解析路由器检测到疑似扫描版或图片题较多时，会尝试调用 OCR Provider；若识别成功，会把识别文本追加到原文本末尾，格式为：

```text
[OCR_TEXT]
OCR 识别出的题干内容...
```

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/services/ocr.py` | 新增 OCR Provider 接口 | 定义 `OcrProvider`、`TesseractOcrProvider`、`UnavailableOcrProvider` |
| `app/services/pdf_parser.py` | `RoutedPdfParser` 调用 OCR Provider | 当 PDF 文字少且包含图片时，尝试 OCR；成功则追加 `[OCR_TEXT]`，失败则保留图片占位并记录原因 |
| `app/routes/web.py` | Web 链路读取 OCR 环境配置 | 通过 `build_ocr_provider_from_env()` 注入 OCR Provider |
| `scripts/export_history_bank_to_coze.py` | 历史题库导出脚本接入 OCR Provider | 导出 Coze 知识库时同样复用 OCR 路由链路 |
| `tests/test_ocr.py` | OCR Provider 配置测试 | 覆盖禁用、Tesseract 配置、未知引擎 |
| `tests/test_pdf_parser.py` | OCR 路由测试 | 覆盖 OCR 成功追加文本、OCR 失败保留图片占位 |
| `tests/test_coze_export.py` | Coze 导出默认解析器测试 | 验证直接调用 `export_pdf_to_coze_txt()` 也会默认走 `RoutedPdfParser` |

环境变量：

```env
# 关闭 OCR，默认行为
OCR_ENGINE=none

# 使用 Tesseract OCR
OCR_ENGINE=tesseract
OCR_DPI=200
OCR_LANG=chi_sim+eng
```

Tesseract 模式需要额外安装：

```powershell
pip install pytesseract pdf2image
```

并在系统中安装：

- Tesseract OCR
- 中文语言包 `chi_sim`
- Poppler

当前机器检查结果：

```text
pytesseract: 未安装
pdf2image: 未安装
PIL: 已安装
fitz: 未安装
```

因此当前代码链路已接入，但本机尚未完成真实 OCR 环境验收。

审查修正：

- 修复 OCR 提示文案自相矛盾的问题：OCR 成功时不再同时出现“OCR 未接入”和“OCR 已识别”。
- Coze 导出服务的默认解析器已改为 `RoutedPdfParser(ocr_provider=build_ocr_provider_from_env())`，避免 Web/脚本走 OCR 路由，但直接调用服务函数时绕过 OCR 路由。
- 未配置 OCR 时，扫描倾向 PDF 会明确提示 `OCR Provider 未配置，已回退到图片占位符。`

验证结果：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ocr.py tests\test_pdf_parser.py -q
# 14 passed

.\.venv\Scripts\python.exe scripts\export_history_bank_to_coze.py --help
# passed

.\.venv\Scripts\python.exe -m pytest tests -q
# 60 passed
```

### 5.3 切题引擎升级

直接使用 `minimal_question_split_pack` 中的升级版切题逻辑：

- 替换或合并主项目 `app/services/question_splitter.py`。
- 引入 `normalize_formula_glyphs`。
- 保持 `RuleQuestionSplitter().split(text, paper_id)` 调用接口不变。
- 保留现有单元测试，并补充选项碎片、前言剥离、公式符号修复用例。

后续可增加轻量 LLM 辅助切题：

- 对无法识别题号的低置信度文本块调用模型。
- 规则结果与模型结果合并。
- 给每道题附加切题置信度，便于人工复核。

#### 本轮补充代码改动：低置信度切题复核提示

本轮补齐“规则兜底切题需要人工优先复核”的闭环：当规则切题没有识别到明确题号，只能把整段文本兜底切成单题时，会给题目写入 `split_confidence=0.35` 和 `split_warning`。报告页新增“切题复核提示”区，集中展示这些低置信度题目，并把同样的数据写入导出 JSON。

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/models/schemas.py` | `Question` 新增 `split_confidence`、`split_warning` | 不改变原有题目字段；正常切题默认置信度 `1.0` |
| `app/services/question_splitter.py` | 记录是否识别到题号 marker | 若未识别到明确题号但仍兜底生成题目，则标记低置信度和复核提示 |
| `app/services/report_builder.py` | 新增 `build_question_quality_rows()` | 从代码版题目中提取低置信度题目，注入模板上下文和导出 payload；非双链路 fallback 也会导出 |
| `templates/report.html` | 新增“切题复核提示”区 | 显示来源、题号、置信度、提示和内容预览 |
| `tests/test_question_splitter.py` | 增加低置信度断言 | 验证无题号文本会被标记为需人工复核，正常题号仍为高置信度 |
| `tests/test_dual_run.py` | 增加报告上下文断言 | 验证低置信度题目进入页面上下文和导出 JSON |

当前逻辑：

```text
RuleQuestionSplitter.split()
  -> normalize + split lines
  -> 若识别到明确题号 marker
       -> 正常生成 Question(split_confidence=1.0)
  -> 若未识别到明确题号，但为了不丢文本兜底生成单题
       -> Question(split_confidence=0.35, split_warning=...)

ReportBuilder.build_template_context()
  -> build_question_quality_rows(code_run_result.questions)
  -> 注入 question_quality_rows
  -> 写入 export_payload.question_quality

report.html
  -> 展示“切题复核提示”
```

当前边界：

- 目前只对“完全没有明确题号 marker 的兜底切题”标低置信度；复杂错切、漏切仍需要后续更细的置信度规则或 LLM 辅助判断。
- Agent 侧题目默认保留自身结果，不用这套规则置信度覆盖。

验证结果：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_question_splitter.py tests\test_dual_run.py -q
# 18 passed
```

### 5.4 查重引擎升级

当前纯文本相似度无法识别“语义相近但措辞不同”的题目。建议升级为文本相似度和语义向量双通道：

```text
文本相似度命中 OR 向量余弦相似度命中 -> 进入候选结果
```

推荐方案：

- 使用 `sentence-transformers` 生成题目向量。
- 使用 `faiss-cpu` 构建本地向量索引。
- 历史题库按科目构建索引文件。
- 每道题最多保留 3 条历史命中，保持报告简洁。

推荐模型：

```text
paraphrase-multilingual-MiniLM-L12-v2
```

该模型支持中文，体积约 120MB，适合作为本地 MVP 向量模型。

#### 本轮已完成代码改动

本轮完成查重升级的第一阶段：在不引入大型模型依赖的前提下，历史题库比对从“纯 rapidfuzz 文本相似度”升级为“文本相似度 + 本地轻量向量相似度双通道命中”。

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/services/comparator.py` | 新增 `lightweight_vector_similarity` | 使用字符 1/2/3-gram Counter 向量和余弦相似度，无需新增依赖 |
| `app/services/comparator.py` | 历史题库比对接入双通道 | `compare_against_history_bank` 同时计算 rapidfuzz 分和轻量向量分，取更高分判断是否命中 |
| `app/services/comparator.py` | 命中来源写入 `match_id` | `match_id` 前缀区分 `history_bank-text-...` 与 `history_bank-vector-...`，便于后续检查 |
| `tests/test_comparator.py` | 新增向量通道测试 | 覆盖轻量向量相似度、中文向量命中、中文无关题不误报、关闭向量通道后不命中 |

当前逻辑：

```text
上传题目 vs 历史题目
  -> rapidfuzz 文本相似度
  -> 字符 n-gram 向量余弦相似度
  -> max(text_score, vector_score) >= threshold 即命中
  -> 每道上传题仍最多保留 top_k 条历史命中
```

当前限制：

- 这是轻量本地向量，不是 sentence-transformers 语义向量。
- 不依赖 FAISS，适合先提升召回能力和验证双通道框架。
- 真正的语义向量检索、索引缓存和大规模历史题库加速仍保留在下一阶段 `sentence-transformers + faiss-cpu`。

审查修正：

- 原测试只覆盖英文重排，中文真实样本在默认阈值下帮助不明显；已补中文重排命中测试和中文无关题不误报测试。
- 轻量向量分数已改为 `max(混合 n-gram 余弦, 单字向量余弦 * 1.15)`，让中文词序变化有实际召回，同时无关短题仍保持低分。
- `match_source` 只写入历史题库命中的 `match_id`，卷内查重和 A/B 交叉查重保持旧 ID 格式，降低对复核状态和导出 JSON 的影响。
- 本轮复查发现 `[IMAGE ...]` 占位符会进入查重文本，纯图片 PDF 之间可能因占位符相同而误判重复；已在 `normalize_for_compare()` 中剔除图片占位符和 `[OCR_TEXT]` 标记，并让空比较文本直接返回 0 分。

验证结果：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_comparator.py -q
# 11 passed

.\.venv\Scripts\python.exe -m pytest tests -q
# 67 passed
```

#### 向量索引第一阶段

本轮继续完成“索引化”的可用第一阶段：历史题库加载成功后，会为历史题目生成轻量字符 n-gram 向量索引，并持久化到 `data/index/history_bank_lightweight_index.json`。后续查重拿到带索引的历史题列表时，会优先通过索引检索命中；如果没有索引，则继续回退到上一阶段的逐题比较逻辑。

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/services/history_vector_index.py` | 新增 `HistoryVectorIndex` | 存储题目元数据、规范化文本、1/2/3-gram 向量和 unigram 向量 |
| `app/services/history_vector_index.py` | 新增索引持久化 | 使用题目 ID + 内容生成 signature，索引文件匹配 signature 时直接复用 |
| `app/services/history_vector_index.py` | 新增 `IndexedHistoryQuestions` | 在普通题目列表上附带 `vector_index`，保持原比较接口兼容 |
| `app/services/history_bank.py` | 历史题库加载后附加索引 | `get_snapshot()` 成功解析题目后自动 build/load 轻量索引 |
| `app/main.py` | 初始化 `data/index` | 主应用启动时准备索引目录，并注入 `HistoryBankService(index_dir=...)` |
| `app/routes/web.py` | 懒加载历史题库服务时传入索引目录 | 避免 fallback 服务绕开索引目录 |
| `app/services/comparator.py` | 历史题库查重优先使用索引 | 若 `history_questions.vector_index` 存在，则调用索引 search；否则回退逐题比较 |
| `tests/test_history_vector_index.py` | 新增索引测试 | 覆盖索引落盘复用、索引命中查重 |
| `tests/test_history_bank.py` | 增加历史题库索引挂载测试 | 验证 `HistoryBankService` 会生成索引文件并把索引附到 questions |

当前逻辑：

```text
HistoryBankService.get_snapshot()
  -> 解析历史 PDF
  -> 切题生成 history questions
  -> build_or_load_history_vector_index()
  -> 写入 data/index/history_bank_lightweight_index.json
  -> snapshot.questions = IndexedHistoryQuestions(..., vector_index=index)

compare_against_history_bank()
  -> 若 history_questions.vector_index 存在
       -> 通过索引计算 text_score / vector_score
       -> 命中 match_id 标记为 history_bank-vector_index 或 history_bank-text_index
  -> 否则回退逐题 rapidfuzz + 轻量向量比较
```

当前限制：

- 当前是“持久化轻量索引”，不是 FAISS，也不是 sentence-transformers 语义向量。
- 本机检查结果：`faiss` 和 `sentence_transformers` 均未安装，因此不能把真正 FAISS 阶段标为完成。
- 索引当前按整个历史题库生成一个文件，尚未按科目拆分。

审查修正：

- 复查发现索引 signature 若只包含 `question_id + content`，历史 PDF 改名但题目内容不变时可能复用旧 `paper_label`；已把 `paper_id`、`paper_label`、`question_no` 一并纳入 signature。
- 索引只是加速能力，不应阻断主报告；已将索引 build/load/save 异常降级为“无索引继续运行”。

验证结果：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_history_vector_index.py tests\test_history_bank.py tests\test_comparator.py -q
# 21 passed

.\.venv\Scripts\python.exe -m pytest tests -q
# 90 passed
```

### 5.5 人工复核持久化

新增 SQLite 表，将前端复核状态写入数据库。

| 表名 | 关键字段 | 用途 |
| --- | --- | --- |
| `review_sessions` | `session_id`, `teacher_id`, `subject`, `created_at`, `paper_a_path`, `paper_b_path` | 本次审查元信息 |
| `review_items` | `item_id`, `session_id`, `question_id`, `status`, `updated_at` | 每道题或每条命中的复核状态 |
| `export_history` | `export_id`, `session_id`, `format`, `exported_at`, `file_path` | 历史导出记录 |
| `report_snapshots` | `session_id`, `payload_json`, `created_at`, `updated_at` | 当前报告 `export_payload` 快照，用于后续恢复 |

接口建议：

```text
PATCH /api/review-items/{item_id}
GET /api/reports/{session_id}
```

请求体：

```json
{
  "status": "确认重复"
}
```

状态枚举：

```text
待确认
确认重复
排除误报
```

#### 本轮已完成代码改动

本轮已完成最小可用闭环：报告生成时创建审查会话和复核项，用户在报告页修改复核状态时通过接口写入 SQLite。

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/services/review_store.py` | 新增 `ReviewStore` | 集中管理 SQLite 建表、审查会话创建、复核项创建、复核状态更新 |
| `app/main.py` | 启动时初始化复核表 | 在原有 `app_meta` 基础上补建 `review_sessions`、`review_items`、`export_history` |
| `app/routes/web.py` | 报告生成后登记 session/items | 根据代码版重复明细创建复核项，并把 `review_session_id`、`review_item_id` 注入模板上下文 |
| `app/routes/web.py` | 新增 `PATCH /api/review-items/{item_id}` | 接收 `status` 并写入 `review_items.status` |
| `templates/report.html` | 复核下拉框增加持久化写回 | 下拉框变更时调用 PATCH 接口，显示“保存中/已保存/保存失败” |
| `tests/test_review_store.py` | 新增持久化测试 | 验证 SQLite 创建、状态更新、API 成功和非法状态拒绝 |
| `tests/test_review_store.py` | 新增模板上下文注入测试 | 验证重复明细行会拿到 `review_item_id`，避免出现表已创建但前端无 id 可写的假闭环 |

#### 本轮补充代码改动：报告快照持久化

本轮继续补齐“历史报告可恢复”的数据底座：报告页生成完成后，会把当前 `export_payload` 按 `review_session.session_id` 写入 SQLite 的 `report_snapshots` 表。保存时机放在 Agent job 信息注入之后，因此异步模式下快照也会包含 `agent_job.job_id`，后续可以继续通过 job 接口回查 Agent 完整结果。

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/services/review_store.py` | 新增 `report_snapshots` 表 | 保存每次报告的完整导出 JSON、创建时间和更新时间 |
| `app/services/review_store.py` | 新增 `upsert_report_snapshot()` / `get_report_snapshot()` | 报告生成后覆盖保存最新快照；查询时反序列化为 JSON payload |
| `app/routes/web.py` | 新增 `_persist_report_snapshot()` | 在 `review_session` 和 `agent_job` 都进入 `export_payload` 后再写入快照，避免保存半成品 |
| `app/routes/web.py` | 新增 `GET /api/reports/{session_id}` | 按 session_id 返回报告快照；不存在时返回 404 |
| `tests/test_review_store.py` | 新增快照读写和 API 测试 | 验证 upsert 覆盖、JSON 回读和查询接口 |
| `tests/test_agent_jobs.py` | 补异步 `/review` 快照断言 | 验证默认异步报告保存的快照包含 Agent job id |

数据库表：

```sql
CREATE TABLE IF NOT EXISTS review_sessions (
    session_id TEXT PRIMARY KEY,
    teacher_id TEXT NOT NULL,
    teacher_name TEXT NOT NULL,
    subject TEXT NOT NULL,
    created_at TEXT NOT NULL,
    paper_a_path TEXT,
    paper_b_path TEXT
);

CREATE TABLE IF NOT EXISTS review_items (
    item_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    match_id TEXT NOT NULL,
    question_id TEXT NOT NULL,
    status TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES review_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS export_history (
    export_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    format TEXT NOT NULL,
    exported_at TEXT NOT NULL,
    file_path TEXT,
    FOREIGN KEY(session_id) REFERENCES review_sessions(session_id)
);

CREATE TABLE IF NOT EXISTS report_snapshots (
    session_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(session_id) REFERENCES review_sessions(session_id)
);
```

当前实现范围：

- 已支持当前报告页内修改复核状态并落库。
- 已把本次 `review_session_id` 和复核项数量写入导出 JSON。
- 已支持按 `session_id` 保存并查询报告 `export_payload` 快照。
- 当前还没有新增“按 session_id 重新打开历史报告”的 HTML 详情页；如果需要页面刷新后完整恢复可视化报告，下一步应增加 `/reports/{session_id}` 页面或前端恢复入口。

验证结果：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_review_store.py -q
# 4 passed

.\.venv\Scripts\python.exe -m pytest tests -q
# 38 passed

.\.venv\Scripts\python.exe -m pytest tests\test_review_store.py tests\test_agent_jobs.py -q
# 16 passed

.\.venv\Scripts\python.exe -m pytest tests -q
# 99 passed
```

### 5.6 Agent / Coze 链路异步化

将当前同步等待改为真正异步：

1. 代码版主链路完成后立即返回报告页。
2. Agent 链路在后台继续运行。
3. 前端通过 WebSocket 或轮询获取 Agent 结果。
4. Agent 完成后补充对照摘要、Agent 新增项、评分不同项和错误说明。

用户体验从“等待 60 秒才看到报告”变为“先看到代码版结果，Agent 对照稍后刷新”。

#### 本轮已完成代码改动

本轮完成异步化第一阶段：默认情况下 `/review` 先运行代码版主链路并返回报告页，同时把 Agent / Coze 链路提交到后台任务。后台任务会先把上传 PDF 复制到 `data/agent_jobs/{job_id}/`，避免请求结束后 `temp_uploads` 被清理导致 Agent 任务找不到文件。

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/services/agent_jobs.py` | 新增 `AgentJobStore` | 使用线程池管理后台 Agent 任务，记录 queued/running/completed/failed 状态 |
| `app/services/agent_jobs.py` | `_copy_papers_for_job()` | 提交任务前复制 PDF 到 job 目录，解决临时上传目录被清理的问题 |
| `app/services/agent_jobs.py` | `build_agent_pending_result()` | 报告页先展示 Agent 后台运行中的占位结果，并把 job_id 写入提示 |
| `app/main.py` | 初始化 `data/agent_jobs` 和 `AgentJobStore` | 应用启动时准备后台任务目录和内存任务表 |
| `app/routes/web.py` | `/review` 默认异步提交 Agent | 代码版同步生成报告；Agent 版通过 `AgentJobStore.submit()` 后台运行 |
| `app/routes/web.py` | 新增 `GET /api/agent-jobs/{job_id}` | 前端可查询后台任务状态和 Agent 结果摘要 |
| `templates/report.html` | 新增 Agent 后台任务状态面板 | 报告页展示 job_id，并轮询任务状态；完成后显示题目数/重复数/错字数摘要 |
| `templates/index.html` | 更新说明文案 | 首页说明从“同一次请求双跑”改为“代码版先返回，Agent 后台运行” |
| `tests/test_agent_jobs.py` | 新增后台任务测试 | 覆盖 PDF 复制保护、pending 结果、状态接口 |
| `tests/test_dual_run.py` | 兼容同步旧链路测试 | 显式设置 `ENABLE_ASYNC_AGENT=false` 以继续覆盖原同步双跑逻辑 |

#### 本轮补充代码改动：Agent job 持久化

本轮继续补齐异步 Agent 的可恢复查询能力：后台任务提交、运行、完成或失败时，会把任务摘要写入 SQLite 的 `agent_jobs` 表。`GET /api/agent-jobs/{job_id}` 仍优先读取内存中的实时任务；如果服务重启或内存任务表丢失，则回退读取 SQLite 中保存的状态与完成摘要。

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/services/review_store.py` | 新增 `agent_jobs` 表 | 保存 job_id、状态、创建/更新时间、pipeline 名称、试卷数、工作目录、错误信息和结果摘要 |
| `app/services/review_store.py` | 新增 `upsert_agent_job_summary()` / `get_agent_job()` | Agent 任务状态变化时写入 SQLite；查询接口可在内存 miss 时回读 |
| `app/services/agent_jobs.py` | `AgentJobStore` 接收可选 `ReviewStore` | 提交、运行中、完成、失败都会尝试持久化；落库失败不阻断后台任务 |
| `app/services/agent_jobs.py` | 新增 `pipeline_result_summary()` | 统一生成 Agent 完成摘要：题目数、重复数、错字数和模块元数据 |
| `app/main.py` | 复用同一个 `ReviewStore` 初始化 job store | 避免应用启动时创建两个彼此独立的持久化入口 |
| `app/routes/web.py` | job 查询接口增加 SQLite fallback | 内存中没有 job 时读取 `review_store.get_agent_job(job_id)`，避免重启后直接 404 |
| `tests/test_agent_jobs.py` | 新增持久化 fallback 测试 | 验证任务完成后落库，并验证内存空 store 仍能通过接口查到结果摘要 |
| `tests/test_review_store.py` | 新增 agent job 表读写测试 | 验证 queued 到 completed 的 upsert 以及 JSON 摘要回读 |

#### 本轮补充代码改动：Agent 完整结果持久化

本轮继续把 Agent job 从“只可恢复摘要”推进到“可恢复完整明细”的第一阶段：后台 Agent 完成后，会把完整 `PipelineRunResult` 序列化为 `result_payload_json` 存入 SQLite。状态接口在内存任务存在时直接返回实时完整结果；内存不存在时，也能从 SQLite 回读 `result_payload`，为后续页面动态回填 Agent 对照明细提供数据源。

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/services/review_store.py` | `agent_jobs` 增加 `result_payload_json` | 保存上传试卷、题目、查重命中、错字问题、模块元数据和历史题库摘要 |
| `app/services/review_store.py` | 初始化时补列迁移 | 已存在旧 `agent_jobs` 表时自动 `ALTER TABLE` 增加 `result_payload_json`，避免旧库升级后写入失败 |
| `app/services/agent_jobs.py` | 新增 `pipeline_result_payload()` | 将 `PipelineRunResult` 转成可 JSON 化的完整结构 |
| `app/services/agent_jobs.py` | 持久化时写入完整 payload | completed/failed 结果都会与摘要一起写入 SQLite |
| `app/routes/web.py` | job 查询接口返回 `result_payload` | 内存和 SQLite fallback 两条路径都可返回完整 Agent 明细 |
| `tests/test_agent_jobs.py` | 补完整 payload 断言 | 验证内存查询和 SQLite fallback 都返回题目明细 |
| `tests/test_review_store.py` | 补旧表迁移测试 | 验证已有 `agent_jobs` 表缺少新字段时仍可升级并写入完整 payload |

#### 本轮补充代码改动：Agent 明细回填

本轮继续把前端从“只展示 Agent 完成摘要”推进到“消费完整 `result_payload`”：报告页轮询到 Agent completed 后，会读取接口返回的完整明细，在后台任务面板中回填 Agent 模块状态、切题明细、重复题明细和错字明细，并把 `agent_result_payload` 写入当前页面的导出 JSON。

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `templates/report.html` | 新增 `agent-job-detail` 明细容器 | Agent 完成后由 JS 动态填充，不阻塞代码版报告首屏展示 |
| `templates/report.html` | 新增 `renderAgentResultPayload()` | 从 `result_payload.questions`、`similarity_matches`、`spellcheck_issues` 渲染 Agent 明细 |
| `templates/report.html` | 完成后更新导出 JSON | 将 `payload.result_payload` 写入 `exportPayload.agent_result_payload`，避免页面展示与导出数据不一致 |
| `templates/report.html` | 前端 HTML 转义 | 动态渲染 Agent 文本时使用 `escapeHtml()`，避免把题干内容当 HTML 注入 |
| `tests/test_agent_jobs.py` | 增加路由级模板断言 | 验证报告页包含明细容器和渲染函数，并移除“下一阶段接入”的旧提示 |

#### 本轮补充代码改动：Agent job 工作目录清理

本轮补齐后台任务文件目录的生命周期管理：`AgentJobStore` 在提交新任务前会清理超过保留期的 completed/failed 任务工作目录，避免 `data/agent_jobs/{job_id}` 长期堆积。清理只删除工作目录和内存 job，不删除 SQLite 中的任务状态、摘要和 `result_payload`，因此历史 job 仍可通过接口 fallback 查询。

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/services/agent_jobs.py` | 新增 `cleanup_finished_jobs()` | 只清理 completed/failed 且超过保留期的任务，queued/running 一律跳过 |
| `app/services/agent_jobs.py` | 清理前校验路径边界 | 只允许删除 `job_dir` 子目录，避免误删非任务目录；删除失败时跳过，不阻塞新任务提交 |
| `app/services/agent_jobs.py` | `submit()` 前触发清理 | 新任务提交时顺手回收过期工作目录，不影响当前任务运行 |
| `app/main.py` | 新增 `AGENT_JOB_RETENTION_SECONDS` 配置 | 默认保留 7 天；设置为 `none/off/disabled` 可关闭自动清理 |
| `tests/test_agent_jobs.py` | 新增清理测试 | 验证过期 completed 目录被删、running 目录保留、SQLite 记录仍可查询 |

当前逻辑：

```text
POST /review
  -> 保存上传 PDF 到 temp_uploads
  -> 代码版 pipeline 同步运行
  -> AgentJobStore.submit()
      -> 复制 PDF 到 data/agent_jobs/{job_id}/
      -> 写入 SQLite agent_jobs：queued
      -> 后台线程运行 Agent pipeline
      -> 状态变化时 upsert SQLite：running/completed/failed
      -> 完成或失败时写入 result_payload_json
  -> 报告页立即返回代码版结果 + Agent job 面板

GET /api/agent-jobs/{job_id}
  -> 优先读内存任务表
  -> 内存不存在时回退读 SQLite agent_jobs
  -> 返回 queued/running/completed/failed
  -> completed 时返回 Agent 题目数、重复数、错字数和模块元数据摘要
  -> 若有结果，额外返回 result_payload 供页面后续回填完整明细

报告页轮询 completed
  -> 读取 result_payload
  -> 渲染 Agent 模块状态、切题明细、重复明细、错字明细
  -> 同步写入 exportPayload.agent_result_payload

AgentJobStore.submit()
  -> cleanup_finished_jobs()
  -> 删除超过保留期的 completed/failed 工作目录
  -> queued/running 不清理
  -> SQLite agent_jobs 记录保留
```

环境开关：

```env
# 默认开启异步 Agent
ENABLE_ASYNC_AGENT=true

# 如需回退旧同步双跑
ENABLE_ASYNC_AGENT=false

# Agent job 工作目录默认保留 7 天；可填秒数
AGENT_JOB_RETENTION_SECONDS=604800

# 如需关闭自动清理
AGENT_JOB_RETENTION_SECONDS=none
```

当前限制：

- Agent job 状态、完成摘要和完整 `result_payload` 已持久化到 SQLite；报告页已能回填 Agent 明细第一屏，但当前还是追加展示，不会重算整页双链路对照表。
- 后台 job 工作目录已做 completed/failed 过期清理；SQLite 历史记录保留，但目前还没有独立的清理管理页面。

审查修正：

- 设计时发现后台 Agent 不能继续引用 `temp_uploads`，因为请求结束后会清理临时目录；已改为提交任务前复制 PDF 到 `data/agent_jobs/{job_id}/`。
- 初版文案曾暗示“刷新页面可查看完整对照”，但当时只实现状态和摘要查询；后续已补完整 `result_payload` 持久化和报告页明细回填。
- 复查补充了默认异步 `/review` 路由级测试，确认页面会生成 Agent job id，后台任务完成后 `GET /api/agent-jobs/{job_id}` 能返回完成摘要。
- 前一轮复查发现“后台任务可查询”仍依赖内存，服务重启后会变成假闭环；已补 `agent_jobs` SQLite 表和接口 fallback。
- 本轮继续修正“只能恢复摘要”的缺口：已补 `result_payload_json` 和旧表迁移测试；随后又补了报告页对 `result_payload` 的明细消费。
- 本轮继续修正前端展示层缺口：报告页已消费 `result_payload` 并展示 Agent 明细；尚未把这些明细重新合并进既有双链路对照表。
- 本轮继续修正 job 目录堆积问题：已补过期清理和路径边界校验，清理不删除 SQLite 中的 job 记录。
- 复查发现 Windows 上目录占用可能导致 `rmtree` 抛错并阻塞新任务；已改为删除失败时跳过，并补充外部路径拒绝测试。

验证结果：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_agent_jobs.py tests\test_dual_run.py -q
# 9 passed

.\.venv\Scripts\python.exe -m pytest tests\test_agent_jobs.py tests\test_review_store.py -q
# 12 passed

.\.venv\Scripts\python.exe -m pytest tests\test_agent_jobs.py -q
# 7 passed

.\.venv\Scripts\python.exe -m pytest tests -q
# 96 passed
```

### 5.7 报告 PDF 导出

现有导出以 JSON 为主。建议新增 PDF 导出：

| 方案 | 特点 |
| --- | --- |
| WeasyPrint | 可直接将 HTML/CSS 报告渲染为 PDF，适合复用现有模板 |
| reportlab | 稳定、可控，但需要单独写 PDF 布局 |

建议优先使用 WeasyPrint，最大化复用现有 `report.html`。

#### 本轮已完成代码改动

本轮先完成无外部依赖的 PDF 导出第一阶段：报告页可把当前 `export_payload` 提交给后端，后端生成一份结构化审查摘要 PDF 并返回下载。当前版本不是完整 HTML/CSS 复刻版，而是可稳定跑通的摘要版，包含总览、上传试卷、历史题库、查重摘要、错字摘要和双链路模块状态。

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/services/report_pdf.py` | 新增轻量 PDF 生成器 | 不依赖 WeasyPrint/reportlab，直接生成合法 PDF 字节；使用 Type0 中文字体声明承载中文文本 |
| `app/routes/web.py` | 新增 `POST /api/reports/export-pdf` | 接收报告页 `export_payload`，返回 `application/pdf` 下载响应 |
| `app/routes/web.py` | 下载头支持中文文件名 | 使用 `filename*=` UTF-8 编码，避免 HTTP header latin-1 编码错误 |
| `app/services/review_store.py` | 新增 `record_export()` | 若 payload 中带 `review_session.session_id`，则把 PDF 导出记录写入 `export_history` |
| `templates/report.html` | 新增“导出审查 PDF”按钮 | 前端调用后端接口，拿到 blob 后触发下载；原 JSON 导出继续保留 |
| `tests/test_report_pdf.py` | 新增 PDF 生成测试 | 验证返回 `%PDF-1.4`、包含 catalog 和 EOF |
| `tests/test_review_store.py` | 新增导出接口测试 | 验证接口返回 PDF，并记录 `export_history` |

当前逻辑：

```text
点击“导出审查 PDF”
  -> 前端 POST /api/reports/export-pdf
  -> 后端读取 export_payload
  -> 生成结构化摘要 PDF
  -> 如存在 review_session.session_id，写入 export_history
  -> 返回 application/pdf
```

当前限制：

- PDF 第一阶段是摘要版，不是完整复刻 `report.html` 的视觉样式。
- 未引入 WeasyPrint/reportlab，因此复杂 CSS、水印、差异高亮的完整分页渲染仍待后续增强。
- 明细默认只截取部分条目，完整数据仍以 JSON 导出为准。
- 当前验证到 PDF 能被 `pdfplumber` 打开并识别页数；中文文本可搜索/可复制能力尚未验收，后续完整 HTML 渲染版需要补这项。

审查修正：

- 初次实现直接把中文文件名放入 `Content-Disposition filename` 会触发 latin-1 编码错误；已改为 ASCII fallback + `filename*=` UTF-8 标准写法。
- 复查补充了“生成 PDF 能被解析器打开”的测试，避免只检查 `%PDF` 文件头造成假通过。

验证结果：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_report_pdf.py tests\test_review_store.py -q
# 7 passed

.\.venv\Scripts\python.exe -m pytest tests -q
# 81 passed
```

### 5.8 历史题库管理页

新增独立题库管理界面：

- 批量上传历史题库 PDF。
- 查看当前题库文件数、题目数、最近更新时间。
- 按科目筛选题库。
- 删除单个来源文件。
- 手动触发向量索引重建。
- 重建时显示进度，完成后自动生效。

数据存储建议：

```text
data/datasets/history_bank/
data/index/faiss_{subject}.index
SQLite: history_files / history_questions / index_jobs
```

#### 本轮已完成代码改动

本轮先完成历史题库管理页的最小可用闭环：管理员可以通过页面查看当前本地题库扫描结果，批量上传 PDF 到 `data/datasets/history_bank/`，上传后强制刷新题库缓存，后续审查会继续使用刷新后的历史题库参与比对。

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/routes/web.py` | 新增 `GET /history-bank` | 读取 `HistoryBankService.get_snapshot()`，展示题库目录、PDF 数、成功加载数、题目数、加载失败列表 |
| `app/routes/web.py` | 新增 `POST /history-bank/upload` | 支持多文件上传；只接收 PDF；非 PDF 跳过并提示；重名 PDF 自动追加 `_2`、`_3` 避免覆盖 |
| `app/routes/web.py` | 新增历史题库上传辅助函数 | `_safe_history_filename()` 清理路径和非法字符，`_unique_history_bank_path()` 保证上传不覆盖已有文件 |
| `app/main.py` | 历史题库服务默认注入 `RoutedPdfParser` | Web 审查链路扫描历史题库时走图片占位和 OCR 路由 |
| `app/services/history_bank.py` | `HistoryBankService` 默认解析器改为 `RoutedPdfParser` | 修复直接实例化服务时仍绕回旧 `CodePdfParser` 的半接入问题 |
| `app/services/history_bank.py` | 新增快速目录摘要 | `/history-bank` 默认只列目录和已有缓存，避免首次打开同步解析大量 PDF 卡死 |
| `templates/history_bank.html` | 新增历史题库管理页面 | 展示统计卡片、上传表单、已加载试卷表、加载失败表 |
| `tests/test_history_bank_routes.py` | 新增路由测试 | 覆盖页面渲染、刷新参数、多文件上传、非 PDF 跳过、重名文件保存 |

当前逻辑：

```text
打开 /history-bank
  -> 读取 app.state.history_bank_service
  -> 默认快速列出 data/datasets/history_bank/*.pdf 和已有缓存
  -> 点击 refresh=true 时才强制解析 PDF 并刷新题目数/失败原因

上传 PDF 到 /history-bank/upload
  -> 校验扩展名或 content-type
  -> 保存到 history_bank 目录
  -> 遇到同名文件生成 same_2.pdf / same_3.pdf
  -> invalidate_cache() 失效缓存但不立即全量解析题库
  -> 返回管理页并显示上传结果
```

当前限制：

- 已支持删除单个来源 PDF；删除仅限历史题库目录内的 PDF，并会拒绝 `..` 路径穿越和非 PDF 文件。
- 已支持按推断科目筛选和按文件名/试卷名关键词搜索；尚未做独立科目级目录和数据库化题库元数据。
- 尚未做 FAISS 索引重建按钮；当前刷新的是 `HistoryBankService` 的本地 PDF 扫描缓存。
- 上传后会保存 PDF 并刷新，但扫描版 PDF 仍依赖前面 OCR Provider 的真实运行环境；本机尚未完成 Tesseract/Poppler 或 PaddleOCR 真实样本验收。
- 当前本地 `data/datasets/history_bank/` 有较多 PDF；默认页面已避免全量解析，但点击“刷新题库缓存”或正式审查时仍会解析历史题库，后续需要做后台任务或持久化索引。

验证结果：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_history_bank.py tests\test_history_bank_routes.py -q
# 13 passed

.\.venv\Scripts\python.exe -m pytest tests -q
# 99 passed
```

#### 历史题库管理页第二阶段

本轮继续补齐管理页的真实管理能力：列表增加科目推断、科目筛选、关键词搜索和单文件删除。删除操作不直接拼接绝对路径，而是用相对路径回传并在后端解析到题库目录内，拒绝路径穿越、非 PDF 和目录删除。

修改文件：

| 文件 | 改动内容 | 逻辑说明 |
| --- | --- | --- |
| `app/services/history_bank.py` | `HistoryBankSnapshot.filtered_summary()` | 在不重新解析 PDF 的前提下，对已有摘要按科目和关键词过滤 |
| `app/services/history_bank.py` | `infer_history_subject()` | 优先从 `年份+A/B+科目.pdf` 命名中推断科目，无法推断时回退到父目录或 `unknown` |
| `app/services/history_bank.py` | `invalidate_cache()` | 文件删除后只失效缓存，不立即全量解析历史题库，避免管理页卡顿 |
| `app/routes/web.py` | `GET /history-bank` 支持 `subject` 和 `q` | 页面筛选参数会传入 `filtered_summary()` |
| `app/routes/web.py` | 新增 `POST /history-bank/delete` | 删除前通过 `_resolve_history_bank_pdf_path()` 校验路径必须位于历史题库目录内且是 PDF |
| `templates/history_bank.html` | 增加筛选表单、科目列、删除按钮 | 用户可按科目/关键词筛选，并对单份 PDF 执行删除 |
| `tests/test_history_bank.py` | 增加科目推断与筛选测试 | 覆盖文件名科目推断、科目集合、过滤结果 |
| `tests/test_history_bank_routes.py` | 增加删除安全测试 | 覆盖删除成功、路径穿越拒绝、非 PDF 拒绝 |

当前逻辑：

```text
打开 /history-bank?subject=math&q=2025
  -> 默认读取快速目录摘要或缓存
  -> 根据 subject 过滤推断科目
  -> 根据 q 过滤文件名/试卷名

提交 /history-bank/delete
  -> 接收 relative_path
  -> 拒绝绝对路径、..、非 PDF、目录
  -> 只允许删除 history_bank 目录内的 PDF
  -> 删除后 invalidate_cache()
  -> 返回管理页
```

审查修正：

- 复查发现上传历史 PDF 后如果直接 `refresh=True`，会同步解析当前本地 144 份历史 PDF，真实使用时容易卡住；已改为上传后只 `invalidate_cache()` 并快速更新目录列表，用户需要全量解析时再点击“刷新题库缓存”。

验证结果：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_history_bank.py tests\test_history_bank_routes.py -q
# 10 passed

.\.venv\Scripts\python.exe -m pytest tests -q
# 78 passed
```

## 六、实施路线图

| 阶段 | 内容 | 预估工作量 | 优先级 |
| --- | --- | --- | --- |
| Sprint 1 | 切题引擎替换、引入 `normalize_formula_glyphs`、SQLite 持久化建表 | 3 - 4 天 | 高 |
| Sprint 2 | 解析路由器、OCR 接入 | 3 - 4 天 | 高 |
| Sprint 3 | 向量查重、FAISS 索引构建、历史题库语义检索 | 3 - 4 天 | 中 |
| Sprint 4 | 历史题库管理页、Agent 链路异步化 | 3 - 4 天 | 中 |
| Sprint 5 | 报告 PDF 导出、整体测试、稳定性优化 | 2 - 3 天 | 低 |

## 七、依赖清单

| 依赖包 | 用途 | 引入阶段 |
| --- | --- | --- |
| `pdfplumber` | 文字版 PDF 文本提取 | 已有 |
| `PaddleOCR` 或 `pytesseract` | 扫描版 PDF OCR | Sprint 2 |
| `sentence-transformers` | 语义向量生成 | Sprint 3 |
| `faiss-cpu` | 向量索引与检索 | Sprint 3 |
| `WeasyPrint` 或 `reportlab` | 报告 PDF 导出 | Sprint 5 |

## 八、保持不变的部分

- Coze 综合审查工作流：`7637135521890959375`
- Coze 切题工作流：`7637166446480506899`
- 代码版主链路整体架构：`ReportBuilder`、`CodeSimilarityComparator` 等
- 前端报告页的核心视图结构
- `.env` 环境变量配置方式
- 代码版作为主结果、Agent 版作为对照结果的产品定位

## 九、升级后的限制与风险

| 限制项 | 当前状态 | 升级后状态 | 剩余风险 |
| --- | --- | --- | --- |
| 扫描版 PDF | 不支持 | Sprint 2 接入 OCR 后支持 | 复杂版面 OCR 精度有限 |
| 切题精度 | 基础规则，易漏切 | 碎片合并、前言剥离、公式清洗后显著提升 | 极特殊版式仍需补规则 |
| 查重语义理解 | 纯文本相似度 | 文本 + 向量双命中 | 向量模型对专业术语理解有上限 |
| 人工复核持久化 | 仅前端，刷新丢失 | 写入 SQLite | 需要补接口和迁移脚本 |
| Agent 超时 | 同步等待 | 异步后台运行 | Agent 完成时间仍不确定 |
| 历史题库管理 | 无管理界面 | Sprint 4 新增 | 初期需要控制文件和索引一致性 |

## 十、验收标准

### Sprint 1 验收

- 现有可提取文本 PDF 仍可正常生成报告。
- 升级版切题器可识别中文数字题号、阿拉伯数字题号、括号题号。
- 选项碎片合并用例通过。
- 前言剥离用例通过。
- 公式乱码修复用例通过。
- 人工复核状态刷新后不丢失。

### Sprint 2 验收

- 扫描版 PDF 可进入 OCR 路径。
- OCR 结果可继续进入切题、查重和报告链路。
- 文字版 PDF 不受 OCR 分支影响。

### Sprint 3 验收

- 历史题库可构建 FAISS 索引。
- 文本相似度低但语义相近的题目可被召回。
- 每题历史命中数量可控，默认最多 3 条。

### Sprint 4 验收

- 历史题库可通过页面上传、查看、删除。
- Agent 结果不阻塞代码版报告展示。
- Agent 完成后页面可展示对照结果或错误说明。

### Sprint 5 验收

- 报告可导出 PDF。
- JSON 导出继续可用。
- 关键链路有自动化测试覆盖。

## 附录：`minimal_question_split_pack` 核心正则速查

| 正则名称 | 匹配目标 |
| --- | --- |
| `CHINESE_NUMERAL_PATTERN` | 中文数字题号，如 `一、`、`二、`、`三、` |
| `ARABIC_PATTERN` | 阿拉伯数字题号，如 `1.`、`2)`、`3、` |
| `OPTION_LABEL_PATTERN` | 选项标签，如 `[A]`、`A.`、`A、` |
| `PAGE_NUMBER_PATTERN` | 纯数字页码行，用于过滤 |
