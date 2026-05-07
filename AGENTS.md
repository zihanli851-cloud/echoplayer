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

## 目录结构
```
/workspace/projects/
├─ app/
│  ├─ main.py           # FastAPI 应用入口
│  ├─ models/
│  │  └─ schemas.py    # Pydantic 模型
│  ├─ routes/
│  │  ├─ web.py        # Web 页面路由
│  │  └─ nuwa.py       # AI 能力路由
│  ├─ services/
│  │  ├─ comparator.py         # 相似度比较
│  │  ├─ dual_run.py           # 双链路运行
│  │  ├─ history_bank.py       # 历史题库
│  │  ├─ nuwa_service.py       # Nuwa 服务
│  │  ├─ pdf_parser.py         # PDF 解析
│  │  ├─ question_splitter.py  # 题目切分
│  │  ├─ report_builder.py     # 报告构建
│  │  └─ spellcheck/           # 错别字检查
│  │     ├─ base.py
│  │     ├─ local_provider.py
│  │     └─ nuwa_provider.py
│  └─ utils/
│     └─ file_manager.py
├─ templates/           # Jinja2 模板
├─ data/                 # 数据目录（SQLite、临时上传、历史题库）
├─ scripts/              # 部署脚本
├─ tests/                # 单元测试
└─ requirements.txt
```

## 关键入口 / 核心模块
- **启动入口**: `uvicorn app.main:app --host 0.0.0.0 --port 5000`
- **Web 路由**: `app/routes/web.py` - 试卷上传、报告展示
- **AI 路由**: `app/routes/nuwa.py` - Nuwa Agent 对照能力
- **双链路服务**: `app/services/dual_run.py` - 代码版与 Agent 版并行执行

## 运行与预览
- **本地运行**: `uvicorn app.main:app --reload`
- **本地访问**: `http://127.0.0.1:8000`
- **部署运行**: `bash scripts/deploy_run.sh`
- **部署端口**: `5000`（固定）

## 用户偏好与长期约束
- 仅支持可直接提取文本的 PDF；扫描版图片 PDF 暂未接入 OCR
- 切题逻辑是规则驱动，识别 `一、二、三` 等中文数字序号
- 历史题库存放在 `data/datasets/history_bank/` 目录

## 常见问题和预防
- PDF 无法解析：检查是否为扫描版图片 PDF
- 端口占用：确保 5000 端口未被占用
- 数据库锁定：`data/echopaper.db` 可能被其他进程占用
