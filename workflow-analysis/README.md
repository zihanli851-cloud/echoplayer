# 综合审查工作流诊断报告

## 工作流信息

- **ID**: 7637135521890959375
- **名称**: zhinengduibi (智能对比)
- **描述**: 错别字与语法校验 && 智能对比和查重

## 工作流结构

```
开始(100001)
    └── question_data [object, required]
        │
        ├──→ 错别字与语法校验(194509) [LLM: GLM-4.7]
        │       └── error_list
        │
        ├──→ 提取题目数组(123238) [Code]
        │       └── question_list
        │           │
        │           └──→ 循环(100170)
        │                   │
        │                   └──→ 知识库检索(168745) [知识库ID: 7637138176842973210]
        │                           └── outputList
        │                               │
        │                               └──→ 智能对比和查重(161200) [LLM: 豆包·2.0·pro]
        │                                       └── reasoning_content
        │
        ├──→ 整合(114761) [Code]
        │       └── output_report [最终输出]
        │           │
        │           └──→ 结束(900001)
```

## 输入参数

```json
{
  "question_data": {
    "questions": [
      {
        "question_no": "1",
        "content": "题目内容..."
      }
    ],
    "subject": "学科名称"
  }
}
```

**必需字段**: `question_data` (object)，内部必须包含 `questions` 数组

## 输出格式

```json
{
  "output_report": {
    "dashboard": {
      "macro_repeat_rate": "重复率%",
      "total_questions_checked": 题目总数
    },
    "error_checklist": [
      {
        "question_number": "题号",
        "suspected_error": "错误词语",
        "correction_suggestion": "修改建议"
      }
    ],
    "plagiarism_details": [
      {
        "question_number": 题号,
        "similarity_level": "相似度",
        "matched_historical_question": "匹配的历史题目",
        "diff_highlight": "差异说明"
      }
    ]
  }
}
```

## 可能导致 5000 错误的原因

| # | 问题 | 节点 | 说明 |
|---|------|------|------|
| **1** | **知识库不存在** | 知识库检索(168745) | 引用了知识库 ID `7637138176842973210`，如果知识库未创建或没有数据，检索会失败 |
| **2** | **大模型不可用** | 错别字校验(194509) | 使用 `GLM-4.7` 模型，如果模型未授权或欠费会失败 |
| **3** | **大模型不可用** | 智能对比(161200) | 使用 `豆包·2.0·pro` 模型，同样可能因授权/欠费失败 |
| **4** | **输入格式错误** | 提取题目(123238) | 如果 `question_data` 不包含 `questions` 数组，会返回空列表，导致循环无输出 |
| **5** | **超时** | 大模型节点 | 设置了 180秒超时，如果模型响应慢可能超时 |

## 修复建议

1. **检查知识库**: 确认知识库 `7637138176842973210` 存在且已上传数据
2. **检查模型授权**: 确认 GLM-4.7 和 豆包·2.0·pro 可用
3. **简化测试**: 在 Coze 平台用简单输入测试
4. **查看调试日志**: 通过 debug_url 查看具体报错节点
