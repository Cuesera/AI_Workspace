# -*- coding: utf-8 -*-
"""
Notion API 연동 테스트
- "자동화 허브" 페이지 하위의 데이터베이스 목록 조회
"""

import os
import sys
import requests
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding="utf-8")

load_dotenv()

NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_HUB_PAGE_ID = os.getenv("NOTION_HUB_PAGE_ID")

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def get_page_info(page_id: str) -> dict:
    """페이지 기본 정보 조회"""
    url = f"https://api.notion.com/v1/pages/{page_id}"
    response = requests.get(url, headers=HEADERS)
    response.raise_for_status()
    return response.json()


def get_child_databases(page_id: str) -> list[dict]:
    """페이지 하위의 데이터베이스 블록 목록 조회"""
    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    databases = []
    cursor = None

    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor

        response = requests.get(url, headers=HEADERS, params=params)
        response.raise_for_status()
        data = response.json()

        for block in data.get("results", []):
            if block["type"] == "child_database":
                databases.append({
                    "id": block["id"],
                    "title": block["child_database"].get("title", "(제목 없음)"),
                    "created_time": block.get("created_time"),
                    "last_edited_time": block.get("last_edited_time"),
                })

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    return databases


def search_databases_in_page(page_id: str) -> list[dict]:
    """Search API로 페이지 내 데이터베이스 검색 (보조 수단)"""
    url = "https://api.notion.com/v1/search"
    payload = {
        "filter": {"property": "object", "value": "database"},
        "page_size": 100,
    }
    response = requests.post(url, headers=HEADERS, json=payload)
    response.raise_for_status()
    results = response.json().get("results", [])

    # 부모가 해당 페이지인 것만 필터링
    filtered = []
    for db in results:
        parent = db.get("parent", {})
        if parent.get("type") == "page_id" and parent.get("page_id", "").replace("-", "") == page_id.replace("-", ""):
            title_list = db.get("title", [])
            title = title_list[0]["plain_text"] if title_list else "(제목 없음)"
            filtered.append({
                "id": db["id"],
                "title": title,
                "created_time": db.get("created_time"),
                "last_edited_time": db.get("last_edited_time"),
            })
    return filtered


def main():
    # 환경변수 확인
    if not NOTION_API_KEY or NOTION_API_KEY == "your_notion_integration_token_here":
        print("[오류] .env 파일에 NOTION_API_KEY를 설정해주세요.")
        return

    print("=" * 60)
    print("Notion API 연동 테스트")
    print("=" * 60)

    # 1) 페이지 ID가 있으면 직접 하위 블록 조회
    if NOTION_HUB_PAGE_ID and NOTION_HUB_PAGE_ID != "your_automation_hub_page_id_here":
        page_id = NOTION_HUB_PAGE_ID.replace("-", "")

        print(f"\n[1] 페이지 정보 조회 (ID: {NOTION_HUB_PAGE_ID})")
        try:
            page = get_page_info(page_id)
            props = page.get("properties", {})
            title_prop = props.get("title", {})
            title_texts = title_prop.get("title", [])
            page_title = title_texts[0]["plain_text"] if title_texts else "(제목 없음)"
            print(f"  페이지 제목: {page_title}")
        except requests.HTTPError as e:
            print(f"  페이지 조회 실패: {e.response.status_code} - {e.response.text}")
            return

        print(f"\n[2] 하위 데이터베이스 목록 (blocks API)")
        try:
            databases = get_child_databases(page_id)
            if databases:
                for i, db in enumerate(databases, 1):
                    print(f"  {i}. {db['title']}")
                    print(f"     ID: {db['id']}")
                    print(f"     수정일: {db['last_edited_time']}")
            else:
                print("  데이터베이스가 없거나 Integration 연결이 필요합니다.")
        except requests.HTTPError as e:
            print(f"  조회 실패: {e.response.status_code} - {e.response.text}")

    # 2) Search API로 연결된 모든 데이터베이스 조회 (페이지 ID 없어도 동작)
    print(f"\n[3] Search API로 접근 가능한 데이터베이스 전체 목록")
    try:
        url = "https://api.notion.com/v1/search"
        payload = {"filter": {"property": "object", "value": "database"}, "page_size": 20}
        response = requests.post(url, headers=HEADERS, json=payload)
        response.raise_for_status()
        all_dbs = response.json().get("results", [])

        if all_dbs:
            print(f"  총 {len(all_dbs)}개의 데이터베이스에 접근 가능:")
            for i, db in enumerate(all_dbs, 1):
                title_list = db.get("title", [])
                title = title_list[0]["plain_text"] if title_list else "(제목 없음)"
                parent_type = db.get("parent", {}).get("type", "unknown")
                print(f"  {i}. {title} (부모 유형: {parent_type}, ID: {db['id']})")
        else:
            print("  접근 가능한 데이터베이스가 없습니다.")
            print("  → Notion에서 Integration을 해당 페이지/DB에 연결했는지 확인하세요.")
    except requests.HTTPError as e:
        print(f"  Search 실패: {e.response.status_code}")
        error_body = e.response.json()
        print(f"  사유: {error_body.get('message', '')}")

    print("\n" + "=" * 60)
    print("테스트 완료")


if __name__ == "__main__":
    main()
