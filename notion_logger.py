# -*- coding: utf-8 -*-
"""
Claude Code Stop Hook → Notion 작업 로그 DB에 자동 저장
실행 전 notion_setup.py 를 한 번 실행해 NOTION_LOG_DB_ID를 생성해야 합니다.
"""

import os
import sys
import json
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))

NOTION_API_KEY  = os.getenv("NOTION_API_KEY")
NOTION_LOG_DB_ID = os.getenv("NOTION_LOG_DB_ID")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
USE_HAIKU_SUMMARY = os.getenv("NOTION_USE_HAIKU", "false").lower() == "true"
DEBUG_MODE        = os.getenv("NOTION_LOGGER_DEBUG", "false").lower() == "true"

KST = timezone(timedelta(hours=9))

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# notion_setup.py 에서 정의한 multi_select 옵션 이름과 일치해야 함
KNOWN_TOOLS = {"Write", "Edit", "Read", "Bash", "Glob", "Grep", "WebFetch", "TodoWrite"}


# ── JSONL 트랜스크립트 로드 ────────────────────────────────────────────────────

def load_transcript(raw: dict) -> list:
    """transcript_path의 JSONL 파일을 읽어 메시지 리스트 반환"""
    transcript_path = raw.get("transcript_path", "")
    if transcript_path and os.path.exists(transcript_path):
        messages = []
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return messages
    # 폴백: 인라인 transcript (이전 방식 호환)
    return raw.get("transcript", [])


# ── 데이터 파싱 ────────────────────────────────────────────────────────────────

def parse_hook_data(raw: dict, transcript: list) -> dict:
    cwd        = raw.get("cwd", "unknown")
    session_id = raw.get("session_id", "unknown")
    project_name = os.path.basename(cwd) if cwd != "unknown" else "unknown"

    tools_used    = set()
    files_touched = set()
    user_messages = []

    for entry in transcript:
        # JSONL 포맷: {"type":"user"|"assistant", "message": {"role":..., "content":[...]}}
        entry_type = entry.get("type", "")
        if entry_type not in ("user", "assistant"):
            continue

        msg     = entry.get("message", entry)  # 폴백: 인라인 포맷 호환
        role    = msg.get("role", entry_type)
        content = msg.get("content", "")

        if isinstance(content, str):
            if role == "user":
                user_messages.append(content[:300])
            continue

        if not isinstance(content, list):
            continue

        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")

            if btype == "tool_use":
                tools_used.add(block.get("name", ""))
                inp = block.get("input", {})
                for key in ("file_path", "path"):
                    if key in inp:
                        files_touched.add(inp[key])

            if role == "user" and btype == "text":
                user_messages.append(block.get("text", "")[:300])

    user_count = sum(1 for e in transcript if e.get("type") == "user")

    # 프로젝트 디렉토리 내에서 현재 존재하는 파일만 포함, 임시/시스템 파일 제외
    cwd_norm = os.path.normcase(os.path.abspath(cwd))
    EXCLUDE_EXTS = {".log", ".jsonl"}
    EXCLUDE_PREFIXES = {"hook_", "debug_", "test_", "check_"}

    def _is_project_file(f: str) -> bool:
        try:
            if not os.path.exists(f):
                return False
            if not os.path.normcase(os.path.abspath(f)).startswith(cwd_norm):
                return False
            name = os.path.basename(f)
            if any(name.startswith(p) for p in EXCLUDE_PREFIXES):
                return False
            if os.path.splitext(name)[1] in EXCLUDE_EXTS:
                return False
            return True
        except (ValueError, OSError):
            return False

    filtered_files = sorted({f for f in files_touched if _is_project_file(f)})

    return {
        "session_id":    session_id,
        "project_name":  project_name,
        "cwd":           cwd,
        "tools_used":    sorted(tools_used),
        "files_touched": filtered_files,
        "first_request": user_messages[0] if user_messages else "(내용 없음)",
        "message_count": user_count,
    }


# ── Haiku 요약 (선택) ──────────────────────────────────────────────────────────

def summarize_with_haiku(parsed: dict, transcript: list) -> str:
    if not ANTHROPIC_API_KEY:
        return "(ANTHROPIC_API_KEY 미설정)"

    texts = []
    for entry in transcript[-40:]:
        entry_type = entry.get("type", "")
        if entry_type not in ("user", "assistant"):
            continue
        msg     = entry.get("message", entry)
        role    = msg.get("role", entry_type)
        content = msg.get("content", "")
        if isinstance(content, str) and content.strip():
            texts.append(f"{role}: {content[:300]}")
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    texts.append(f"{role}: {block.get('text','')[:300]}")

    prompt = (
        f"Claude Code 작업 세션 (프로젝트: {parsed['project_name']}):\n\n"
        + "\n".join(texts)
        + "\n\n위 작업을 3~5문장 한국어 평문으로 요약해주세요. "
        + "마크다운 헤더(#)나 볼드(**) 없이 순수 텍스트로만 작성하세요. "
        + "무엇을 만들었는지, 어떤 문제를 해결했는지 중심으로."
    )

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 500,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["content"][0]["text"]
    except Exception as e:
        return f"(요약 실패: {e})"


# ── Notion 블록 빌더 ───────────────────────────────────────────────────────────

def rich_text(text: str) -> dict:
    return {"type": "text", "text": {"content": text[:2000]}}


def build_page_blocks(parsed: dict, summary: str) -> list:
    now_str = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    blocks  = []

    blocks.append({
        "object": "block", "type": "callout",
        "callout": {
            "icon": {"type": "emoji", "emoji": "📋"},
            "rich_text": [rich_text(
                f"프로젝트: {parsed['project_name']}  |  "
                f"메시지: {parsed['message_count']}개  |  "
                f"기록: {now_str} KST"
            )],
            "color": "gray_background",
        },
    })

    blocks.append({"object": "block", "type": "heading_2",
                   "heading_2": {"rich_text": [rich_text("📝 작업 요약")]}})
    blocks.append({"object": "block", "type": "paragraph",
                   "paragraph": {"rich_text": [rich_text(summary)]}})

    blocks.append({"object": "block", "type": "heading_2",
                   "heading_2": {"rich_text": [rich_text("💬 첫 번째 요청")]}})
    blocks.append({"object": "block", "type": "quote",
                   "quote": {"rich_text": [rich_text(parsed["first_request"])]}})

    if parsed["tools_used"]:
        blocks.append({"object": "block", "type": "heading_2",
                       "heading_2": {"rich_text": [rich_text("🛠️ 사용한 도구")]}})
        for tool in parsed["tools_used"]:
            blocks.append({"object": "block", "type": "bulleted_list_item",
                           "bulleted_list_item": {"rich_text": [rich_text(tool)]}})

    if parsed["files_touched"]:
        blocks.append({"object": "block", "type": "heading_2",
                       "heading_2": {"rich_text": [rich_text("📁 수정/생성된 파일")]}})
        for f in parsed["files_touched"][:20]:
            blocks.append({"object": "block", "type": "bulleted_list_item",
                           "bulleted_list_item": {"rich_text": [rich_text(f)]}})

    blocks.append({"object": "block", "type": "divider", "divider": {}})
    blocks.append({"object": "block", "type": "paragraph",
                   "paragraph": {"rich_text": [rich_text(f"📂 {parsed['cwd']}")],
                                 "color": "gray"}})
    return blocks


# ── 로컬 .md 파일 저장 ────────────────────────────────────────────────────────────

def save_md_log(parsed: dict, summary: str, notion_url: str = "") -> str:
    """logs/ 폴더에 세션 로그를 .md 파일로 저장, 파일 경로 반환"""
    now      = datetime.now(KST)
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M")

    logs_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(logs_dir, exist_ok=True)

    # 같은 날 같은 프로젝트 세션이 여러 개일 경우 덮어쓰지 않고 번호 부여
    base_name = f"{date_str}_{parsed['project_name']}"
    file_path = os.path.join(logs_dir, f"{base_name}.md")
    if os.path.exists(file_path):
        idx = 2
        while os.path.exists(os.path.join(logs_dir, f"{base_name}_{idx}.md")):
            idx += 1
        file_path = os.path.join(logs_dir, f"{base_name}_{idx}.md")

    tools_str = ", ".join(parsed["tools_used"]) if parsed["tools_used"] else "없음"
    files_str = "\n".join(f"- {f}" for f in parsed["files_touched"]) or "없음"
    notion_line = f"\n**Notion:** {notion_url}" if notion_url else ""

    content = f"""# [{date_str}] {parsed['project_name']} 작업 로그

**날짜:** {date_str} {time_str} KST{notion_line}
**메시지 수:** {parsed['message_count']}개
**사용 도구:** {tools_str}

## 작업 요약

{summary}

## 첫 번째 요청

> {parsed['first_request']}

## 수정/생성된 파일

{files_str}

---

📂 `{parsed['cwd']}`
"""

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)

    return file_path


# ── Notion DB row 생성 ─────────────────────────────────────────────────────────

def create_db_entry(parsed: dict, summary: str) -> str:
    """데이터베이스에 행(row) 추가 후 페이지 URL 반환"""
    now      = datetime.now(KST)
    date_str = now.strftime("%Y-%m-%d")
    title    = f"[{date_str}] {parsed['project_name']}"

    # DB에 정의된 multi_select 옵션에 없는 도구는 필터링
    known_tools = [t for t in parsed["tools_used"] if t in KNOWN_TOOLS]
    # 알 수 없는 도구는 "기타"로 묶어서 요약에만 표시 (multi_select 오류 방지)

    payload = {
        "parent": {"database_id": NOTION_LOG_DB_ID},
        "icon":   {"type": "emoji", "emoji": "🤖"},
        "properties": {
            "작업 제목": {
                "title": [{"type": "text", "text": {"content": title}}]
            },
            "날짜": {
                "date": {"start": now.strftime("%Y-%m-%dT%H:%M:%S+09:00")}
            },
            "프로젝트": {
                "select": {"name": parsed["project_name"][:100]}
            },
            "사용 도구": {
                "multi_select": [{"name": t} for t in known_tools]
            },
            "메시지 수": {
                "number": parsed["message_count"]
            },
            "요약": {
                "rich_text": [{"type": "text", "text": {"content": summary[:2000]}}]
            },
        },
        "children": build_page_blocks(parsed, summary),
    }

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json=payload,
        timeout=30,
    )
    resp.raise_for_status()
    page_id = resp.json()["id"]
    return f"https://notion.so/{page_id.replace('-', '')}"


# ── 메인 ──────────────────────────────────────────────────────────────────────

def write_run_log(msg: str):
    """Hook 발동 여부를 항상 기록 (디버그와 무관하게)"""
    log_path = os.path.join(os.path.dirname(__file__), "logs", "hook_run.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")


def main():
    write_run_log("[시작]")

    if not NOTION_API_KEY or not NOTION_LOG_DB_ID:
        write_run_log("[종료] 환경변수 미설정")
        sys.exit(0)

    try:
        raw_input = sys.stdin.read()
        if not raw_input.strip():
            write_run_log("[종료] stdin 비어있음")
            sys.exit(0)
        raw = json.loads(raw_input)
        write_run_log(f"[파싱] session={raw.get('session_id','?')[:8]}")
    except Exception as e:
        write_run_log(f"[오류] stdin 파싱 실패: {e}")
        sys.exit(0)

    try:
        # JSONL 파일에서 트랜스크립트 로드
        transcript = load_transcript(raw)
        write_run_log(f"[로드] transcript {len(transcript)}줄")

        if DEBUG_MODE:
            debug_path = os.path.join(os.path.dirname(__file__), "hook_debug.json")
            with open(debug_path, "w", encoding="utf-8") as f:
                json.dump({"raw": raw, "transcript_count": len(transcript)}, f, ensure_ascii=False, indent=2)

        parsed = parse_hook_data(raw, transcript)
        write_run_log(f"[파싱] 메시지={parsed['message_count']} 도구={len(parsed['tools_used'])}")

        if parsed["message_count"] == 0:
            write_run_log("[종료] 메시지 없음")
            sys.exit(0)

        if USE_HAIKU_SUMMARY:
            summary = summarize_with_haiku(parsed, transcript)
        else:
            tools_str = ", ".join(parsed["tools_used"]) if parsed["tools_used"] else "없음"
            summary   = f"사용 도구: {tools_str} / 수정 파일 {len(parsed['files_touched'])}개"

        write_run_log(f"[요약] {summary[:60]}...")

        notion_url = ""
        try:
            notion_url = create_db_entry(parsed, summary)
            write_run_log(f"[Notion 완료] {notion_url}")
        except requests.HTTPError as e:
            write_run_log(f"[Notion 실패] {e.response.status_code}: {e.response.text[:100]}")

        md_path = save_md_log(parsed, summary, notion_url)
        write_run_log(f"[MD 완료] {md_path}")

    except Exception as e:
        import traceback
        write_run_log(f"[오류] {e}")
        write_run_log(traceback.format_exc().replace('\n', ' | '))


if __name__ == "__main__":
    main()
