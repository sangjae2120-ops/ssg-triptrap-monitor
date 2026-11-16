#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SSG.COM 스토케 트립트랩 하이체어 이유식 세트(일반 우드) 특가 모니터링 스크립트
----------------------------------------------------------------------
설치 필요 패키지:
    pip install requests beautifulsoup4

사용 방법:
    - 상시 실행: python ssg_triptrap_monitor.py
    - 1회 실행(GitHub Actions / 클라우드 스케줄러용): python ssg_triptrap_monitor.py --once
"""

import argparse
import json
import os
import random
import re
import time
from datetime import datetime
from typing import Dict, List, Optional
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# ============================================================
# 1. 사용자 맞춤 설정 (CONFIG) - 일반 우드 이유식 세트 / 50만원대
# ============================================================
SEARCH_KEYWORDS = [
    "스토케 트립트랩 이유식 세트",
    "스토케 트립트랩 베이비세트 트레이",
]

# 목표 특가 상한선: 50만원대 (599,900원 이하일 때 알림, 정가 680,000원 기준)
TARGET_PRICE = 599_900

# 단품(의자 본품 43만원, 베이비세트 16만원, 트레이 9만원 등) 제외를 위한 최소 가격 하한선
MIN_VALID_PRICE = 450_000

# 제목에 반드시 포함되어야 하는 키워드
REQUIRED_KEYWORDS = ["트립트랩"]

# 이유식 세트 구성을 확인하기 위한 키워드 (하나라도 포함되면 통과)
SET_KEYWORDS = ["이유식", "베이비세트", "베이비 세트"]

# 제외할 키워드 (오크 우드, 신생아/뉴본 세트, 단품 액세서리 등 제외)
EXCLUDE_KEYWORDS = [
    "오크",
    "오크우드",
    "OAK",
    "신생아",
    "뉴본",
    "쿠션단품",
    "트레이단품",
    "하네스단품",
    "대여",
    "렌탈",
]

# 조회 주기 (초) - 상시 실행 시 180~300초 사이 랜덤 대기
CHECK_INTERVAL_MIN_SEC = 180
CHECK_INTERVAL_MAX_SEC = 300

# 텔레그램 알림 설정 (환경변수 또는 직접 입력)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# 디스코드 웹훅 알림 설정 (환경변수 또는 직접 입력)
DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# 중복 알림 방지용 상태 저장 파일
STATE_FILE = "alerted_items.json"

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
    'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    'Sec-Ch-Ua-Mobile': '?0',
    'Sec-Ch-Ua-Platform': '"Windows"',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'none',
    'Sec-Fetch-User': '?1',
    'Upgrade-Insecure-Requests': '1'
}


# ============================================================
# 2. 알림 발송 함수
# ============================================================
def send_notification(message: str) -> None:
    """콘솔 출력 및 설정된 채널(텔레그램/디스코드)로 알림을 전송합니다."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n[{now_str}] 🔔 트립트랩 이유식 세트 50만원대 특가 감지!\n{message}\n" + "=" * 60)

    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            tg_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            resp = requests.post(
                tg_url,
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": message,
                    "disable_web_page_preview": False,
                },
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            print(f"[경고] 텔레그램 알림 전송 실패: {exc}")

    if DISCORD_WEBHOOK_URL:
        try:
            resp = requests.post(
                DISCORD_WEBHOOK_URL,
                json={"content": message},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as exc:
            print(f"[경고] 디스코드 알림 전송 실패: {exc}")


# ============================================================
# 3. 상태 저장 및 불러오기 (중복 알림 방지)
# ============================================================
def load_alerted_state() -> Dict[str, int]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_alerted_state(state: Dict[str, int]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        print(f"[경고] 상태 파일 저장 실패: {exc}")


# ============================================================
# 4. SSG.COM 상품 검색 및 조건 필터링
# ============================================================
def parse_price(price_text: str) -> Optional[int]:
    digits = re.sub(r"[^\d]", "", price_text)
    return int(digits) if digits else None


def is_matching_product(title: str, price: int) -> bool:
    """일반 우드 트립트랩 이유식 세트 & 50만원대 조건을 검사합니다."""
    if price < MIN_VALID_PRICE or price > TARGET_PRICE:
        return False

    normalized = title.upper().replace(" ", "")

    # 필수 키워드 확인 (트립트랩)
    for req in REQUIRED_KEYWORDS:
        if req.upper().replace(" ", "") not in normalized:
            return False

    # 이유식 세트 또는 베이비세트 포함 여부 확인
    if not any(k.upper().replace(" ", "") in normalized for k in SET_KEYWORDS):
        return False

    # 오크 우드 및 신생아 세트 제외
    for exc in EXCLUDE_KEYWORDS:
        if exc.upper().replace(" ", "") in normalized:
            return False

    return True


def fetch_ssg_products(keyword: str) -> List[Dict]:
    """SSG.COM 검색 결과 페이지에서 상품 목록을 추출합니다."""
    encoded_query = quote(keyword)
    url = f"https://www.ssg.com/search.ssg?target=all&query={encoded_query}"

    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    products: List[Dict] = []

    item_elements = soup.select("li.cunit_t232, div.ssgitem_unit, li[data-unittype='item']")

    for item in item_elements:
        item_id = item.get("data-item-id") or item.get("id") or ""

        title_el = item.select_one(
            ".cunit_info .title em.tx_ko, .ssgitem_tit, .cunit_info .title a"
        )
        if not title_el:
            continue
        title = " ".join(title_el.get_text(strip=True).split())

        price_el = item.select_one(
            ".opt_price .ssg_price, .ssgitem_price .ssg_price, em.ssg_price"
        )
        if not price_el:
            continue
        price = parse_price(price_el.get_text())
        if price is None:
            continue

        soldout_el = item.select_one(".cunit_soldout, .ssgitem_soldout")
        if soldout_el:
            continue

        link_el = item.select_one("a.clickable, a[href*='itemView.ssg']")
        href = link_el.get("href", "") if link_el else ""
        if href.startswith("//"):
            link = "https:" + href
        elif href.startswith("/"):
            link = "https://www.ssg.com" + href
        elif href.startswith("http"):
            link = href
        else:
            link = url

        if not item_id:
            item_id = link

        products.append(
            {
                "id": str(item_id),
                "title": title,
                "price": price,
                "link": link,
            }
        )

    return products


def check_once(alerted_items: Dict[str, int]) -> bool:
    """1회 검색 및 조건 확인을 수행하고 상태 변경 여부를 반환합니다."""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    seen_ids = set()
    all_products: List[Dict] = []

    for kw in SEARCH_KEYWORDS:
        items = fetch_ssg_products(kw)
        for item in items:
            if item["id"] not in seen_ids:
                seen_ids.add(item["id"])
                all_products.append(item)

    print(f"[{now_str}] 조회 완료: 중복 제외 총 {len(all_products)}개 상품 확인")

    state_updated = False
    for item in all_products:
        item_id = item["id"]
        title = item["title"]
        price = item["price"]
        link = item["link"]

        if is_matching_product(title, price):
            prev_price = alerted_items.get(item_id)
            if prev_price is None or price < prev_price:
                msg = (
                    f"[SSG 트립트랩 이유식 세트 특가 알림]\n"
                    f"• 상품명: {title}\n"
                    f"• 현재가: {price:,}원 (목표: {TARGET_PRICE:,}원 이하 / 일반 우드)\n"
                    f"• 바로가기: {link}"
                )
                send_notification(msg)
                alerted_items[item_id] = price
                state_updated = True

    return state_updated


# ============================================================
# 5. 메인 실행부
# ============================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="SSG 트립트랩 이유식 세트 특가 모니터")
    parser.add_argument(
        "--once",
        action="store_true",
        help="1회만 조회 후 종료 (GitHub Actions 등 외부 스케줄러 사용 시)",
    )
    args = parser.parse_args()

    print("🚀 SSG.COM '트립트랩 이유식 세트(일반 우드)' 50만원대 특가 모니터링")
    print(f"   - 감시 가격 범위: {MIN_VALID_PRICE:,}원 ~ {TARGET_PRICE:,}원 (오크/신생아 제외)")

    alerted_items = load_alerted_state()

    if args.once:
        try:
            if check_once(alerted_items):
                save_alerted_state(alerted_items)
        except Exception as exc:
            print(f"[오류] 조회 중 예외 발생: {exc}")
        return

    while True:
        try:
            if check_once(alerted_items):
                save_alerted_state(alerted_items)
        except Exception as exc:
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{now_str}] [오류] 조회 중 예외 발생: {exc}")

        sleep_sec = random.uniform(CHECK_INTERVAL_MIN_SEC, CHECK_INTERVAL_MAX_SEC)
        time.sleep(sleep_sec)


if __name__ == "__main__":
    main()
