# EchoPaper MVP

一个基于 FastAPI + Jinja2 + SQLite 的试卷智能审查系统 MVP。

当前版本聚焦一个本地可运行闭环：

- 上传 A/B 卷 PDF
- 同一次请求内双跑代码版与 Agent 版能力链路；如果 Coze 返回错误或 Agent 链路未返回结果，报告会显示具体错误，不再用代码版结果伪装成 Agent 成功
- 提取 PDF 文本
- 基础切题
- 卷内查重、A/B 交叉查重、历史题库比对
- 本地错别字检查
- 生成代码主视图报告，并展示 Agent 对照结果或 Coze 错误说明

## 技术栈

- FastAPI
- Jinja2
- SQLite
- pdfplumber
- rapidfuzz
- pytest

## 项目结构

```text
EchoPaper/
├─ app/
│  ├─ main.py
│  ├─ models/
│  │  └─ schemas.py
│  ├─ routes/
│  │  └─ web.py
│  ├─ services/
│  │  ├─ comparator.py
│  │  ├─ history_bank.py
│  │  ├─ pdf_parser.py
│  │  ├─ question_splitter.py
│  │  ├─ report_builder.py
│  │  └─ spellcheck/
│  │     ├─ base.py
│  │     ├─ local_provider.py
│  │     └─ nuwa_provider.py
│  └─ utils/
│     └─ file_manager.py
├─ templates/
│  ├─ index.html
│  └─ report.html
├─ tests/
│  ├─ test_comparator.py
│  ├─ test_history_bank.py
│  ├─ test_dual_run.py
│  └─ test_question_splitter.py
└─ requirements.txt
```

## 安装步骤

```powershell
cd c:\Users\mao\Desktop\EchoPaper
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

## 启动命令

```powershell
uvicorn app.main:app --reload
```

启动后打开：

```text
http://127.0.0.1:8000
```

## 示例使用

1. 打开首页
2. 输入教师姓名、教师工号
3. 选择科目
4. 上传 A 卷 PDF
5. 如需双卷比对，再上传 B 卷 PDF
6. 点击“提交并生成审查报告”
7. 系统会自动把上传试卷与 `data/datasets/history_bank` 中的往年试卷做文本相似度比对
8. 查看代码主视图 Dashboard、历史题库状态、错字清单、重复明细和人工复核区
9. 查看 Agent 对照摘要、差异状态、Coze 错误说明和当前请求的 JSON 导出

## 运行测试

```powershell
pytest
```

## MVP 当前限制

- 当前仅支持可直接提取文本的 PDF；扫描版图片 PDF 暂未接入 OCR
- 切题逻辑是规则驱动，只识别：
  - `一、二、三`
  - `1. 2. 3.`
  - `（1）（2）`
- 相似度当前基于 rapidfuzz 文本比对，不包含语义向量检索
- 历史题库比对当前直接扫描本地 `history_bank` 目录，命中结果按“每道上传题最多保留 3 条历史命中”做展示裁剪
- 本地错别字检查只覆盖：
  - 常见错别字词典
  - 重复标点
  - 配对标点错误
  - 重复字
- 报告页里的人工复核状态目前仅前端展示，尚未回写数据库
- Agent 版默认调用 Coze；如果 Coze 工作流报错、超时或缺少约定输出，报告页会显示 Agent 未返回和具体错误说明
- 文件处理完成后会清理临时目录，因此当前不会保留上传原文件

## 下一阶段扩展建议

- 接入 OCR，支持扫描版 PDF
- 强化切题规则，支持更多学科题型和版式
- 增加历史题库比对
- 将人工复核状态写入 SQLite
- 继续调通 Coze 切题、错字和查重工作流，替换或补充本地规则检查
