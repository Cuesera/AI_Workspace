# -*- coding: utf-8 -*-
"""
Claude Code 작업 로그 데이터베이스 초기 생성 스크립트
한 번만 실행하면 됩니다.
"""

import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")

NOTION_API_KEY     = os.getenv("NOTION_API_KEY")
NOTION_HUB_PAGE_ID = os.getenv("NOTION_HUB_PAGE_ID")

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

DB_TITLE = "Claude Code 작업 로그"


def find_existing_db() -> str | None:
    """허브 페이지 하위에 동일 이름 DB가 있으면 ID 반환"""
    resp = requests.get(
        f"https://api.notion.com/v1/blocks/{NOTION_HUB_PAGE_ID}/children",
        headers=HEADERS,
    )
    resp.raise_for_status()
    for block in resp.json().get("results", []):
        if block["type"] == "child_database":
            if block["child_database"].get("title") == DB_TITLE:
                return block["id"]
    return None


def create_log_database() -> str:
    """작업 로그 전용 데이터베이스 생성 후 ID 반환"""
    payload = {
        "parent": {"type": "page_id", "page_id": NOTION_HUB_PAGE_ID},
        "icon":   {"type": "emoji", "emoji": "🤖"},
        "title": [{"type": "text", "text": {"content": DB_TITLE}}],
        "properties": {
            "작업 제목": {"title": {}},
            "날짜":     {"date": {}},
            "프로젝트": {
                "select": {
                    "options": [
                        {"name": "AI_Workspace", "color": "blue"},
                    ]
                }
            },
            "사용 도구": {
                "multi_select": {
                    "options": [
                        {"name": "Write",  "color": "green"},
                        {"name": "Edit",   "color": "yellow"},
                        {"name": "Read",   "color": "gray"},
                        {"name": "Bash",   "color": "orange"},
                        {"name": "Glob",   "color": "pink"},
                        {"name": "Grep",   "color": "purple"},
                        {"name": "WebFetch","color": "red"},
                        {"name": "TodoWrite","color": "blue"},
                    ]
                }
            },
            "메시지 수": {"number": {"format": "number"}},
            "요약":     {"rich_text": {}},
        },
    }

    resp = requests.post(
        "https://api.notion.com/v1/databases",
        headers=HEADERS,
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def update_env_file(db_id: str):
    """NOTION_LOG_DB_ID를 .env 파일에 자동 추가"""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    with open(env_path, "r", encoding="utf-8") as f:
        content = f.read()

    if "NOTION_LOG_DB_ID" in content:
        # 이미 있으면 값만 교체
        lines = content.splitlines()
        new_lines = [
            f"NOTION_LOG_DB_ID={db_id}" if l.startswith("NOTION_LOG_DB_ID=") else l
            for l in lines
        ]
        updated = "\n".join(new_lines) + "\n"
    else:
        updated = content.rstrip("\n") + f"\nNOTION_LOG_DB_ID={db_id}\n"

    with open(env_path, "w", encoding="utf-8") as f:
        f.write(updated)


def main():
    print("=" * 50)
    print("Claude Code 작업 로그 DB 초기 설정")
    print("=" * 50)

    if not NOTION_API_KEY or not NOTION_HUB_PAGE_ID:
        print("[오류] .env 파일에 NOTION_API_KEY / NOTION_HUB_PAGE_ID를 설정해주세요.")
        return

    # 기존 DB 확인
    print(f"\n허브 페이지에서 '{DB_TITLE}' DB 검색 중...")
    existing_id = find_existing_db()

    if existing_id:
        print(f"  이미 존재하는 DB 발견: {existing_id}")
        db_id = existing_id
    else:
        print(f"  DB 생성 중...")
        db_id = create_log_database()
        print(f"  DB 생성 완료: {db_id}")

    # .env 자동 업데이트
    update_env_file(db_id)
    print(f"\n.env 파일에 NOTION_LOG_DB_ID 저장 완료")
    print(f"\n설정 완료! 이제 Claude Code 세션이 끝날 때마다 자동 기록됩니다.")
    print(f"DB URL: https://notion.so/{db_id.replace('-', '')}")


if __name__ == "__main__":
    main()
