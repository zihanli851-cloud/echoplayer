# Coze 工作流测试集

这些测试集按当前两个工作流导出包生成：

- 切题工作流：`7637166446480506899`
- 综合审查 / 查重工作流：`7637135521890959375`

## 目录

- `api_payloads/qieti_*.json`：切题工作流 API 请求体
- `api_payloads/zhinengduibi_*.json`：综合审查 / 查重工作流 API 请求体
- `expected_outputs/*.json`：本地项目期望的最小输出结构

## 使用方式

在 Coze 调试页测试时，只复制 `parameters` 里的内容即可。

通过本地脚本测试时，先确认 `.env` 里有：

```env
COZE_API_URL=https://api.coze.cn/v1/workflow/run
COZE_BOT_TOKEN=你的新 token
COZE_SPLIT_WORKFLOW_ID=7637166446480506899
COZE_COMPARE_WORKFLOW_ID=7637135521890959375
```

然后运行：

```powershell
.\.venv\Scripts\python.exe scripts\run_coze_workflow_tests.py
```

## 最小验收标准

切题工作流成功时，Coze 返回的 `data` 解析后必须包含：

```json
{
  "split_result": {
    "paper_id": "A",
    "subject": "chinese",
    "questions": []
  }
}
```

综合审查 / 查重工作流成功时，Coze 返回的 `data` 解析后必须包含：

```json
{
  "output_report": {
    "error_checklist": [],
    "plagiarism_details": []
  }
}
```

数组可以为空。先保证工作流返回 `code=0`，再优化识别效果。
