# EchoPaper 项目规范

## 项目概述
EchoPaper 是一个基于 FastAPI + Jinja2 + SQLite 的试卷智能审查系统 MVP。核心功能是上传 A/B 卷 PDF，进行文本提取、切题、卷内查重、A/B 交叉查重、历史题库比对、本地错别字检查，并生成审查报告。

## 技术栈
- **框架**: FastAPI 0.115.12
- **模板引擎**: Jinja2 3.1.6
- **数据库**: SQLite
- **PDF 处理**: pdfplumber 0.11.5
- **文本相似度**: rapidfuzz 3.13.0
- **Web 服务器**: uvicorn 0.34.0
- **测试**: pytest 8.3.5
- **AI 智能体**: Coze Workflow API

## 目录结构
```
/workspace/projects/
├─ app/
│  ├─ main.py           # FastAPI 应用入口
│  ├─ models/
│  │  └─ schemas.py    # Pydantic 模型
│  ├─ routes/
│  │  ├─ web.py        # Web 页面路由
│  │  ├─ coze.py       # Coze 智能体路由
│  │  └─ nuwa.py       # Nuwa 智能体路由（保留兼容）
│  ├─ services/
│  │  ├─ comparator.py         # 相似度比较
│  │  ├─ coze_service.py      # Coze 服务封装
│  │  ├─ dual_run.py          # 双链路运行
│  │  ├─ history_bank.py      # 历史题库
│  │  ├─ nuwa_service.py      # Nuwa 服务（保留兼容）
│  │  ├─ pdf_parser.py        # PDF 解析
│  │  ├─ question_splitter.py # 题目切分
│  │  ├─ report_builder.py    # 报告构建
│  │  └─ spellcheck/          # 错别字检查
│  │     ├─ base.py
│  │     ├─ local_provider.py
│  │     ├─ coze_provider.py  # Coze 错字检查
│  │     └─ nuwa_provider.py  # Nuwa 错字检查
│  └─ utils/
│     └─ file_manager.py
├─ templates/           # Jinja2 模板
├─ data/               # 数据目录（SQLite、临时上传、历史题库）
├─ scripts/             # 脚本（部署+预览）
├─ tests/              # 单元测试
├─ .env                # 环境变量配置
└─ requirements.txt
```

## 关键入口 / 核心模块
- **启动入口**: `uvicorn app.main:app --host 0.0.0.0 --port 5000`
- **Web 路由**: `app/routes/web.py` - 试卷上传、报告展示
- **Coze 路由**: `app/routes/coze.py` - Coze 智能体 API
- **Nuwa 路由**: `app/routes/nuwa.py` - Nuwa 智能体 API（保留兼容）
- **双链路服务**: `app/services/dual_run.py` - 代码版与 Coze 智能体版并行执行

## Coze 智能体配置
Coze Workflow API 用于 Agent 版智能审查能力。

### 必需的环境变量
创建 `.env` 文件（参考 `.env.example`）：

```bash
# Coze API 配置
COZE_API_URL=https://api.coze.cn/v3/workflows/run
COZE_WORKFLOW_ID=7637135521890959375       # 综合审查工作流
COZE_SPLIT_WORKFLOW_ID=7637166446480506899 # 切题工作流
COZE_BOT_TOKEN=your_bot_token_here         # 在 Coze 个人中心 -> API 管理 创建
COZE_TIMEOUT=60
```

### Coze 工作流 ID
| 工作流 | Workflow ID | 用途 |
|--------|-------------|------|
| 综合审查 | `7637135521890959375` | 切题+错字检查+比对一体化 |
| 切题 | `7637166446480506899` | 智能识别试卷题目 |

### Coze API 端点
- **工作流执行**: `POST /v3/workflows/run`
- **认证方式**: `Authorization: Bearer {COZE_BOT_TOKEN}`

### Coze Provider
| Provider | 说明 |
|----------|------|
| `CozeSpellcheckProvider` | Coze 智能体错字检查 |
| `CozeService` | Coze Workflow API 封装 |

## 运行与预览

### 预览链路（Coze 平台）
- **预览命令**: `scripts/coze-preview-run.sh`
- **预览端口**: `5000`（固定）
- **预览方式**: FastAPI + Jinja2 动态渲染

### 本地运行
- **本地运行**: `uvicorn app.main:app --reload`
- **本地访问**: `http://127.0.0.1:5000`
- **部署运行**: `bash scripts/deploy_run.sh`

## 项目配置说明
- **project_type**: `web`（支持 Coze 平台预览）
- **preview_enable**: `enabled`
- **预览服务**: 通过 `uvicorn` 启动 FastAPI 应用，暴露 5000 端口

## 用户偏好与长期约束
- 仅支持可直接提取文本的 PDF；扫描版图片 PDF 暂未接入 OCR
- 切题逻辑是规则驱动，识别 `一、二、三` 等中文数字序号
- 历史题库存放在 `data/datasets/history_bank/` 目录
- Agent 版默认使用 Coze 智能体；Nuwa 保留作为备用

## 常见问题和预防
- PDF 无法解析：检查是否为扫描版图片 PDF
- 端口占用：确保 5000 端口未被占用
- 数据库锁定：`data/echopaper.db` 可能被其他进程占用
- Coze 调用失败：检查 `COZE_BOT_TOKEN` 是否正确配置
