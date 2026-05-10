# EchoPaper 纯代码审查开发要求

版本：v1.0  
日期：2026-05-10

## 一、文档目的

本文档用于明确 EchoPaper 后续从“代码主链路 + Coze/工作流辅助”升级为“纯代码审查系统”的开发要求，作为后续设计、开发、联调、测试和验收的统一基线。

本文档重点约束以下方向：

- 审查能力全部以本地代码实现为主，不再依赖 Coze 工作流完成核心切题与审查。
- 系统核心服务对象为学校期末试卷审查场景。
- ~~历史题库以课程为核心检索维度，不再按学期或 A/B 卷做默认强筛选。~~
- ~~切题策略必须从“编号即切题”调整为“大题优先、小题内聚”。~~

## 当前开发进度（2026-05-10）

### 已完成

- ~~第一优先级：切题规则改造为大题优先~~
- ~~第一优先级：历史库双源接入~~
- ~~第一优先级：课程级历史检索~~
- ~~第二优先级：原题识别模板化增强（基础版）~~
- ~~第二优先级：历史库重建脚本与迁移校验~~
- ~~第二优先级：Word 文档接入（docx 审查入口）~~
- ~~第三优先级：OCR 实战验收与图片题增强（基础版）~~
- ~~报告页对“疑似原题”结果的专门分区展示~~

### 未完成

- ~~图片题、图表题、复杂公式题增强~~
- ~~报告细节优化与 PDF 质量提升~~

## 本轮开发实现逻辑（2026-05-10）

### 1. 大题优先切题

- 已重写 `app/services/question_splitter.py`。
- 默认模式为 `major_question_mode`，只把 `一、二、三`、`1.`、`2.` 等大题编号作为真正切题边界。
- `（1）（2）（3）` 默认视为当前大题内部小问，直接并入当前题目内容，不再拆成独立题。
- 预留 `subquestion_expand_mode`，后续如需展开小问可直接启用。
- 已增加试卷表头清洗逻辑，导入后会优先过滤学校名、学院名、课程名、考试说明、姓名、学号、任课教师等封面/表头信息，避免被并入第一题。
- 增加了卷首说明剔除、题型标题跳过、低置信度兜底切题与人工复核提示。

### 2. 历史库双源统一接入

- 已重写 `app/services/history_bank.py`。
- 历史库优先读取 `historicdatabase/txt` 中的题目级文本。
- 同时扫描 `historicdatabase/pdf`，通过文件名归一生成统一 `source_key`，将 txt 与 pdf 关联到同一份历史试卷记录。
- 当 txt 可直接提供题目时，优先使用 txt；当 txt 缺失或不可用时，再回退到 pdf 解析 + 本地切题。
- 每道历史题已补充 `source_key`、`course`、`source_txt_path`、`source_pdf_path` 等元数据，方便后续重建、追溯和报告展示。

### 3. 课程级历史检索

- 历史题库默认按课程过滤，不再按学期或 A/B 卷做强过滤。
- 当前实现中，上传试卷的 `subject/course` 会作为主筛选条件传入历史比对流程。
- 同课程不同学期的题目会继续保留在默认比对范围内，符合文档要求。

### 4. 原题识别模板化增强

- 新增 `app/services/question_normalizer.py`。
- 已实现基础模板化归一策略：
  - 数字归一
  - 年份归一
  - 分值归一
  - 常见单位归一
  - 常见参数表达式归一
- `app/services/comparator.py` 已升级为同时计算：
  - `literal_score`
  - `template_score`
  - `final_score`
  - `is_same_source_question`
- 当前规则下，当模板分明显高于字面分时，会将结果标记为“疑似原题”或同源题。

### 5. 数据结构演化

- 已扩展 `app/models/schemas.py`。
- `Question` 新增：
  - `source_key`
  - `course`
  - `source_txt_path`
  - `source_pdf_path`
- `SimilarityMatch` 新增：
  - `literal_score`
  - `template_score`
  - `final_score`
  - `is_same_source_question`

### 6. 当前验证结果

- 已通过针对性测试：
  - `tests/test_question_splitter.py`
  - `tests/test_history_bank.py`
  - `tests/test_comparator.py`
  - `tests/test_history_vector_index.py`
- 本轮验证命令：
  - `python -m pytest tests/test_question_splitter.py tests/test_history_bank.py tests/test_comparator.py tests/test_history_vector_index.py -q --basetemp .pytest_tmp`

### 7. Word 文档接入（本轮新增）

- 新增 `app/services/document_parser.py`，作为统一文档解析入口。
- 当前已支持：
  - `pdf`
  - `docx`
- 当前对 `.doc` 的处理策略是显式报错并提示先转换为 `.docx`，避免静默失败。
- 审查入口 `app/routes/web.py` 已放开为 `PDF 或 DOCX` 上传。
- 当前阶段的接入范围是“待审查试卷上传入口”，历史库管理与历史库重建仍暂时以 `historicdatabase/txt + pdf` 为主。
- 已补充解析/切题回归测试：
  - `tests/test_document_parser.py`
  - `tests/test_question_splitter.py`
- 本轮补充验证命令：
  - `python -m pytest tests/test_document_parser.py tests/test_question_splitter.py -q --basetemp .pytest_tmp`

### 8. 历史库重建脚本与迁移校验（本轮新增）

- 新增 `app/services/history_bank_rebuilder.py`，负责从 `historicdatabase/pdf` 重新生成 `historicdatabase/txt`。
- 重建时以 `source_key` 归一匹配旧 txt，不要求 txt 与 pdf 必须处于同一层级目录，只要同名归一后能对上，就会先备份旧 txt 再覆盖生成新结果。
- 备份目录固定写入 `historicdatabase/_rebuild_backups/<run_id>/...`，保留原相对路径结构，便于后续人工抽查或回退。
- 每次重建都会输出 `rebuild_manifest.json`，记录：
  - 本次 `run_id`
  - 根目录
  - 备份目录
  - 总 PDF 数
  - 成功/失败数量
  - 每份试卷对应的 `source_pdf`、`target_txt`、`backup_txt`、`question_count`、`page_count`、`status`、`error`
- 新增 `scripts/rebuild_history_bank.py`，支持直接命令行执行历史库重建。
- 脚本支持 `--dry-run`，可只做“PDF 提取 + 新切题规则验算”，不落盘新 txt、不生成备份、不写 manifest，适合迁移前预校验。
- 当前这套实现满足文档里“可回退、可验证”的要求：
  - 可回退：旧 txt 先备份
  - 可验证：有 manifest 和 dry-run
  - 可追溯：每条记录都保留源 pdf / 目标 txt / 备份 txt 信息
- 本轮补充验证测试：
  - `tests/test_history_bank_rebuilder.py`
  - `tests/test_history_bank.py`
- 本轮补充验证命令：
  - `python -m pytest tests/test_history_bank_rebuilder.py tests/test_history_bank.py -q --basetemp .pytest_tmp`

### 9. OCR 实战验收与图片题增强（本轮新增，基础版）

- 现有 `app/services/pdf_parser.py` 的图片占位符与 OCR 尝试逻辑已正式接入主审查链路，不再只是底层解析能力。
- 本轮把 PDF 解析阶段的诊断信息透传到 `UploadedPaper`：
  - `image_count`
  - `ocr_attempted`
  - `ocr_succeeded`
  - `requires_manual_review`
  - `parse_note`
- 当前策略为：
  - 可直接提取文本的 PDF：正常走文本解析
  - 含图片对象但文本较少的 PDF：尝试 OCR
  - OCR 成功：保留 `[IMAGE ...]` 占位符，并把 `[OCR_TEXT]` 识别文本追加到正文
  - OCR 未配置 / OCR 失败：不中断整条审查链路，继续保留图片占位符，同时在报告中明确提示人工复核
- 本轮补充了“单份试卷级别”的解析说明，避免多卷上传时把别卷的 OCR 诊断串到当前卷里。
- 报告页与历史快照页已新增“解析风险提示”区域，集中展示：
  - 哪一卷含图片对象
  - 图片数量
  - OCR 是否触发
  - OCR 是否成功
  - 当前风险等级
  - 是否建议直接查看原卷 PDF
- 当前这一版满足文档里“图片对象不能静默丢失”“OCR 失败不能拖垮全系统”“扫描卷需明确风险提示”的基础要求。
- 本轮补充验证测试：
  - `tests/test_ocr.py`
  - `tests/test_dual_run_parse_metadata.py`
  - `tests/test_report_builder_parse_quality.py`
- 本轮补充验证命令：
  - `python -m pytest tests/test_dual_run_parse_metadata.py tests/test_report_builder_parse_quality.py tests/test_ocr.py -q --basetemp .pytest_tmp`

### 10. 报告页“疑似原题 / 同源题”专门分区展示（本轮新增）

- 已重写并收口 `app/services/report_builder.py`，统一整理报告上下文，避免原有编码残留影响后续扩展。
- 当前报告构建阶段会单独筛出以下结果进入“疑似原题 / 同源题”分区：
  - `is_same_source_question == true`
  - 或 `level == 疑似原题`
- 该分区与普通“重复题明细”分开展示，避免同一条结果在页面中重复出现两次。
- 分区内会额外展示：
  - 综合分 `score/final_score`
  - 字面分 `literal_score`
  - 模板分 `template_score`
  - 类型标记（`疑似原题` / `同源题`）
  - 一段自动解释文案，说明为什么被判为模板型原题风险
- 当前判断解释逻辑为：
  - 若 `is_same_source_question == true`，优先解释为“题干结构一致，但参数/数字变化较大”
  - 否则解释为“模板分达到原题阈值，但字面分未完全重合”
- `templates/report.html` 已新增专门板块，支持页面直接人工复核。
- `templates/report_snapshot.html` 已同步支持历史快照查看与导出后回看。
- 导出 JSON 现已新增 `same_source_matches` 字段，方便后续做 PDF 渲染优化或外部审计。
- 当前实现已经满足文档中“报告页对疑似原题结果做专门分区展示”的要求。
- 本轮补充验证测试：
  - `tests/test_report_builder_same_source.py`
  - `tests/test_comparator.py`
  - `tests/test_dual_run.py`
- 本轮补充验证命令：
  - `python -m pytest tests/test_dual_run.py tests/test_review_documents.py tests/test_document_parser.py tests/test_pdf_parser.py tests/test_ocr.py tests/test_dual_run_parse_metadata.py tests/test_report_builder_parse_quality.py tests/test_report_builder_same_source.py tests/test_history_bank_rebuilder.py tests/test_history_bank.py tests/test_comparator.py -q --basetemp .pytest_tmp`

### 11. 报告细节优化与 PDF 质量提升（本轮新增）

- 已重写 `app/services/report_pdf.py` 的导出摘要拼装逻辑，不再只导出基础总览，而是与网页报告当前核心信息保持对齐。
- 当前 PDF 导出已补齐以下专门区块：
  - 解析风险提示
  - 低置信度切题提示
  - 疑似原题 / 同源题
  - 查重摘要
  - 错字检查摘要
  - 历史题库摘要
  - 双链路模块状态
- `same_source_matches` 已正式进入 PDF 导出链路，导出时会额外展示：
  - `same_source_flag`
  - `final_score`
  - `literal_score`
  - `template_score`
  - `reason`
  - `review_status`
- `parse_quality` 已正式进入 PDF 导出链路，导出时会明确写出：
  - 哪一份试卷存在解析风险
  - OCR 状态
  - 图片数量
  - 风险等级
  - 风险原因
  - 解析备注
- `question_quality` 已正式进入 PDF 导出链路，导出时会列出低置信度切题题号、置信度、预警文案与内容预览，方便纸面复核。
- 本轮同时修正了 PDF 导出测试策略：由于当前手写 PDF 文本流对 `pdfplumber` 的提取兼容性有限，测试已增加内容流十六进制文本回退解析，确保验证的是“导出文本确实写入 PDF”，而不是只验证“文件能打开”。
- 本轮补充验证测试：
  - `tests/test_report_pdf.py`
  - `tests/test_report_builder_same_source.py`
  - `tests/test_report_builder_parse_quality.py`
- 本轮补充验证命令：
  - `python -m pytest tests/test_report_pdf.py tests/test_report_builder_same_source.py tests/test_report_builder_parse_quality.py -q --basetemp .pytest_tmp`

### 12. 图片题、图表题、复杂公式题增强（本轮新增）

- 已在 `app/services/report_builder.py` 新增题目级复杂内容识别逻辑，不再只停留在“整份试卷存在图片/OCR 风险”的卷级提示。
- 当前新增 `complex_question_quality` 导出字段，并同步接入：
  - 网页报告
  - 历史快照页
  - PDF 导出
- 当前题目级识别规则包括：
  - `图片题`：题干内出现 `[IMAGE ...]` 占位符
  - `OCR回填`：题干内出现 `[OCR_TEXT]` 标记
  - `图表题`：命中 `如图 / 下图 / 图表 / 表格 / 如下表 / 折线图 / 柱状图 / 电路图` 等图表关键词
  - `复杂公式题`：命中公式关键词，或存在较多公式符号、等式表达、分式表达
- 当前复杂题复核提示会为每道题输出：
  - `paper_label`
  - `question_no`
  - `flag_summary`
  - `review_level`
  - `detail`
  - `reason`
  - `recommendation`
  - `content_preview`
- 当前风险分级逻辑为：
  - 同时命中 `图片题 + OCR回填`：`高风险`
  - 命中 `图片题` 或 `图表题`：`需复核`
  - 仅命中复杂公式特征：`提示`
- 这样可以把文档里“对图片题、扫描题、复杂公式题给出建议查看原卷 PDF”的要求，落实到具体题号级别，而不是只在整卷摘要里做泛化提示。
- 本轮补充验证测试：
  - `tests/test_report_builder_complex_questions.py`
  - `tests/test_report_builder_parse_quality.py`
  - `tests/test_report_pdf.py`
- 本轮补充验证命令：
  - `python -m pytest tests/test_report_builder_complex_questions.py tests/test_report_builder_parse_quality.py tests/test_report_pdf.py -q --basetemp .pytest_tmp`

## 二、业务定位

EchoPaper 的目标是服务学院课程期末试卷审查，帮助教学管理或命题教师完成以下工作：

- 检查当前学期 A 卷、B 卷之间的重复情况。
- 检查当前试卷与历年试卷之间的重复情况。
- 识别“题干基本一致，仅修改数字、数据、参数”的原题或同源题。
- 检查试卷中的错别字、重复字、标点错误、常见表达错误。
- 对包含公式、图表、图片、扫描内容的试卷提供尽可能稳定的解析与风险提示。
- 输出结构化审查报告，支持网页展示与 PDF 导出。

## 三、总体建设原则

### 3.1 纯代码优先

- 切题、查重、错别字检查、历史题库检索、报告生成必须由本地代码主链路完成。
- 不再将 Coze、Nuwa 或其他工作流作为核心依赖。
- 即使后续保留智能体或外部模型能力，也只能作为可选增强，不得阻塞主流程。

### 3.2 学校试卷场景优先

- 优先适配学院课程期末试卷，而不是通用文档审查。
- 默认假设试卷存在 A/B 卷、题号、分值、题型、公式、图表、代码、表格等教学材料特征。
- 所有切题和查重策略必须围绕“学校试卷结构”设计。

### 3.3 稳定性优先于花哨能力

- 优先保证批量导入、整卷审查、历史比对、报告生成闭环稳定可用。
- 图片题、扫描题、复杂表格题允许先输出“需人工复核”提示，但不能静默丢失。
- 不允许为了追求一步到位而引入无法稳定运行的外部依赖链路。

## 四、业务功能要求

### 4.1 当前学期 A/B 卷审查

系统必须支持：

- 上传 A 卷 PDF。
- 可选上传 B 卷 PDF。
- 对 A 卷内部题目进行重复检查。
- 对 B 卷内部题目进行重复检查。
- 对 A 卷与 B 卷之间进行交叉重复检查。

输出结果至少包括：

- 高度重复题目对。
- 疑似重复题目对。
- 每组命中题目的原文对照。
- 相似度分值。
- 人工复核状态。

### 4.2 历年试题重复检查

系统必须支持：

- 以整套试卷方式上传待审查试卷。
- 自动切题后，逐题与历史题库进行比对。
- 输出每道题在历史题库中的前若干条命中结果。
- 支持教师通过报告快速查看命中的历史来源题目。

### 4.3 原题识别

系统必须支持识别以下情况：

- 题干基本一致，仅数字变化。
- 题干基本一致，仅年份、分值、样例数据变化。
- 题干基本一致，仅公式中的常量、参数、区间变化。
- 题干主体一致，仅选项内容局部变化。

系统不得仅依赖原始字符串相似度判断原题，必须增加模板化归一或等价特征识别能力。

### 4.4 错别字和表达检查

系统必须支持：

- 常见错别字检测。
- 重复字检测。
- 标点重复检测。
- 标点配对错误检测。
- 课程术语白名单与误报抑制。

后续建议支持：

- 课程术语词典。
- 学科专有名词白名单。
- 公式附近、代码附近、图表附近的误报抑制。

### 4.5 报告输出

系统必须支持：

- 网页报告展示。
- JSON 导出。
- PDF 导出。

报告中必须包含：

- 审查基本信息。
- 切题结果概览。
- A/B 卷重复结果。
- 历史题库命中结果。
- 原题识别结果。
- 错别字问题列表。
- 低置信度题目提示。
- 人工复核状态。

## 五、输入文档要求

### 5.1 支持的文件类型

系统目标应支持：

- PDF
- Word（`.docx`）
- Word 老格式（`.doc`，可通过转换支持）

当前阶段最低要求：

- 优先完成 PDF 全流程闭环。
- 在此基础上新增 Word 文档解析能力。

### 5.2 非纯文本试卷处理要求

试卷可能包含：

- 数学公式
- 逻辑符号
- 代码块
- 图表
- 图片题
- 扫描版页面

系统必须做到：

- 文本能提取的部分优先提取。
- 图片对象不能静默丢失，至少保留占位信息。
- 对疑似扫描版、图片题较多的试卷给出明确风险提示。
- OCR 失败时不得让系统整体失败。

## 六、历史题库建设要求

### 6.1 历史库来源

当前历史库位于 `historicdatabase/`，包含两类素材：

- `historicdatabase/txt`
- `historicdatabase/pdf`

其中：

- `txt` 为已切题的题目级文本结果。
- `pdf` 为原始历史试卷。

### 6.2 历史库接入原则

历史库必须采用“双源接入”：

- ~~`txt` 作为主检索库。~~
- ~~`pdf` 作为原始证据库和补偿数据源。~~

不允许只使用 PDF 实时切题作为历史库唯一来源，也不建议只使用 txt 作为唯一真源。

### 6.3 历史库统一规则

系统必须建立统一历史试卷记录，用于关联同一份试卷的 txt 和 pdf：

- ~~通过文件名归一匹配。~~
- ~~维护统一的 `source_key`。~~
- ~~保存课程、教师、卷别、原始路径等元数据。~~

### 6.4 历史库筛选原则

历史题库默认只按课程筛选。

具体要求：

- ~~课程作为主筛选维度。~~
- 学期信息仅作为展示字段或辅助字段。
- A/B 卷仅作为展示元数据，不参与默认强过滤。
- ~~不得将“同课程但不同学期”排除在默认历史比对范围之外。~~

## 七、切题开发要求

### 7.1 当前问题

现有切题规则存在以下问题：

- 把 `（1）（2）（3）` 这类小题号当作独立题号处理。
- 导致一道大题中的多个小问被拆成多道独立题。
- 该问题已经影响历史库中部分 `.coze.txt` 数据结构。

### 7.2 目标切题策略

后续切题必须采用“大题优先、小题内聚”原则：

- ~~`一、二、三`、`1.`、`2.` 等大题标记作为真正题目边界。~~
- ~~`（1）（2）（3）` 默认视为当前大题的内部小问，不独立生成新题。~~
- ~~同一道编程题、综合题、证明题、案例题内的分问，应尽量保留在同一题目对象中。~~

### 7.3 可选扩展

后续如有需要，可支持两种切题模式：

- ~~`major_question_mode`：大题级切题，默认模式。~~
- ~~`subquestion_expand_mode`：将小题展开，作为可选增强模式。~~

~~默认上线模式必须为大题级切题。~~

### 7.4 历史库重建要求

由于当前历史题库中的部分 txt 已受旧切题规则影响，后续在新切题规则稳定后，必须安排一次历史库重建：

- ~~优先以原始 PDF 重跑切题。~~
- ~~重建后的数据作为新历史库主数据。~~
- ~~旧 txt 可保留备查，但不应继续作为唯一主数据。~~

## 八、重复度与原题识别要求

### 8.1 基础重复检测

系统必须保留并增强以下能力：

- 文本相似度计算。
- 卷内题目两两比较。
- A/B 卷交叉比较。
- 上传题与历史题逐题比较。

### 8.2 原题识别增强

系统必须在文本相似度外，增加模板化归一能力。

建议至少支持以下归一策略：

- ~~数字归一。~~
- ~~年份归一。~~
- ~~分值归一。~~
- ~~常见单位归一。~~
- ~~常见公式参数归一。~~

建议输出以下字段：

- ~~`literal_score`~~
- ~~`template_score`~~
- ~~`final_score`~~
- ~~`is_same_source_question`~~

### 8.3 结果分级

建议保留并扩展当前结果分级：

- 高度重复
- 疑似重复
- 疑似原题
- 差异较大

## 九、报告与人工复核要求

### 9.1 报告要求

报告必须适配学校试卷审查习惯，重点展示：

- 当前学期 A/B 卷风险
- 历史题库命中
- 原题识别结论
- 错别字和表达问题
- 图片题/扫描题风险提示

### 9.2 人工复核要求

系统必须支持：

- 对重复题目标记“待确认 / 确认重复 / 排除误报”
- 对低置信度切题结果提示人工复核
- 对图片题、扫描题、复杂公式题给出“建议查看原卷 PDF”

## 十、技术开发要求

### 10.1 模块化要求

建议以以下模块为主进行后续开发：

- `document_parser.py`：统一 PDF/Word 解析
- `question_splitter.py`：大题优先切题
- `question_normalizer.py`：题干模板化归一
- `comparator.py`：重复与原题识别
- `history_repository.py`：历史库双源统一接入
- `report_builder.py`：报告生成

### 10.2 数据结构要求

建议新增或演化以下结构：

- 历史试卷文件记录
- 历史题目记录
- 历史题模板化特征
- 题目级 PDF 来源映射

### 10.3 兼容性要求

- 不得因关闭 Coze 而破坏现有代码版主链路。
- 对已有网页报告、历史报告、导出接口尽量保持兼容。
- 历史库迁移和重建过程需可回退、可验证。

## 十一、明确不采用的方案

以下方案不作为当前阶段主路径：

- 继续以 Coze 工作流完成核心切题和审查
- 默认按学期筛选历史题库
- 默认按 A/B 卷筛选历史题库
- 继续把 `（1）（2）（3）` 默认拆成独立题
- 将扫描题、图片题静默忽略

## 十二、实施优先级

### 第一优先级

- ~~切题规则改造为大题优先~~
- ~~历史库双源接入~~
- ~~课程级历史检索~~

### 第二优先级

- ~~原题识别模板化增强~~
- ~~历史库重建~~
- ~~Word 文档接入~~

### 第三优先级

- ~~OCR 实战验收~~
- ~~图片题、图表题、复杂公式题增强~~
- ~~报告细节优化与 PDF 质量提升~~

## 十三、验收标准

### 13.1 功能验收

- 可上传整套试卷完成审查。
- 可输出 A/B 卷重复结果。
- 可输出历史题库命中结果。
- 可识别一部分“仅改数字”的原题。
- 可输出错别字与标点问题。
- 可导出网页报告和 PDF 报告。

### 13.2 历史库验收

- `historicdatabase/txt` 可作为主题库加载。
- `historicdatabase/pdf` 可与 txt 建立稳定关联。
- 默认仅按课程维度筛选历史结果。

### 13.3 切题验收

- 大题中的 `（1）（2）（3）` 默认不再切成独立题。
- 编程题、综合题、证明题等长题能尽量保持为整题。
- 低置信度题目可在报告中提示人工复核。

## 十四、最终目标

EchoPaper 后续目标不是“一个依赖外部工作流的审查演示系统”，而是：

一个面向学校课程试卷审查、以本地代码为主、支持课程级历史题库比对、支持原题识别、支持复杂试卷逐步增强的稳定审查平台。
