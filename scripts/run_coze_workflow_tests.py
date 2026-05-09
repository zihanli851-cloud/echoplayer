from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
PAYLOAD_DIR = BASE_DIR / "docs" / "coze_workflow_testsets" / "api_payloads"


def main() -> int:
    load_dotenv(BASE_DIR / ".env")
    api_url = os.getenv("COZE_API_URL", "https://api.coze.cn/v1/workflow/run").strip()
    token = os.getenv("COZE_BOT_TOKEN", "").strip()
    if not token or token == "your_bot_token_here":
        print("未配置有效 COZE_BOT_TOKEN。")
        return 1

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    ok_count = 0
    fail_count = 0
    for payload_path in sorted(PAYLOAD_DIR.glob("*.json")):
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        print(f"\n=== {payload_path.name} ===")
        try:
            response = requests.post(api_url, json=payload, headers=headers, timeout=180)
            result = response.json()
        except Exception as exc:
            fail_count += 1
            print(f"REQUEST_ERROR: {exc}")
            continue

        code = result.get("code")
        msg = result.get("msg")
        detail = result.get("detail")
        print(f"status={response.status_code} code={code} msg={msg}")
        if detail:
            print(f"detail={detail}")

        parsed_data = parse_data(result.get("data"))
        if parsed_data is not None:
            print("data_keys=" + ",".join(sorted(parsed_data.keys())))

        expected_key = "split_result" if payload_path.name.startswith("qieti_") else "output_report"
        if code == 0 and isinstance(parsed_data, dict) and expected_key in parsed_data:
            ok_count += 1
            print(f"PASS: found {expected_key}")
        else:
            fail_count += 1
            print(f"FAIL: expected code=0 and data.{expected_key}")

    print(f"\nsummary: passed={ok_count} failed={fail_count}")
    return 0 if fail_count == 0 else 2


def parse_data(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


if __name__ == "__main__":
    raise SystemExit(main())
