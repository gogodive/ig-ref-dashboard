# 레퍼런스 인스타 분석 대시보드 (ig-ref-dashboard)

경쟁/벤치마크 계정의 최신 게시물을 매일 자동 수집·분석하는 에이전트.
자사 [ig-feed-dashboard](https://github.com/gogodive/ig-feed-dashboard)와 같은 구조(GitHub Actions + Pages)로 동작한다.

```
노션(레퍼런스 계정 DB) → Apify 수집 → Claude 분석 → 대시보드(Pages) + 노션(분석로그 DB)
```

- **대시보드**: https://gogodive.github.io/ig-ref-dashboard/ — 매일 07:30 KST 갱신
- **노션 허브**: 🔍 레퍼런스 인스타 자동분석 (분석 대상 계정 관리 + 분석로그)

## 자사 대시보드와 다른 점

| | 자사 (ig-feed-dashboard) | 레퍼런스 (이 저장소) |
|---|---|---|
| 대상 | 자사 7개 계정 | 노션 DB에서 `모니터링` 체크된 타사 계정 |
| 수집 | Meta Graph API (전체 인사이트) | Apify (공개지표: 조회·좋아요·댓글·팔로워) |
| 저장·공유·도달 | ✅ | ❌ 타사 비공개 — 수집 불가 |
| AI 분석 | 없음 | ✅ 새 게시물 한줄 분석 + 🔥히트 심층 분석 + 주간 종합 |

## 동작 방식

매일 07:30 KST (GitHub Actions cron):
1. 노션 '레퍼런스 계정' DB에서 `모니터링=ON` 계정 목록 조회
2. Apify로 계정별 최신 게시물 10개 수집 → `data/{username}.json`에 병합
   (게시 30일 경과 시 지표 동결 `확정`, 이번 수집에 없는 과거 게시물도 60개까지 히스토리 유지)
3. Claude 분석 — **캐시돼 있어 같은 게시물을 두 번 분석하지 않음**:
   - 새 게시물: 한줄 기획 포인트
   - 🔥 히트(조회수 ≥ 계정 중앙값×2): "왜 터졌나" + 자사 적용 아이디어
   - 매주 월요일: 계정별 종합(핵심 인사이트·기획 시사점·반복 주제)
4. 대시보드 렌더 → GitHub Pages 배포
5. 노션 '레퍼런스 분석 로그' DB에 카드 기록 (새 게시물/히트/주간종합 있는 계정만)

## 셋업 (1회)

1. **repo Secrets 등록** (Settings → Secrets and variables → Actions):
   - `APIFY_TOKEN` — https://console.apify.com/ → Settings → API & Integrations
   - `ANTHROPIC_API_KEY` — https://console.anthropic.com/
   - `NOTION_TOKEN` — https://www.notion.so/my-integrations 에서 통합 생성 후,
     노션 '🔍 레퍼런스 인스타 자동분석' 페이지에 Connection으로 연결 필수
2. **Pages 설정**: Settings → Pages → Source = **GitHub Actions**
3. Actions 탭에서 `daily-ref-analysis` workflow_dispatch로 1회 수동 실행 → 배포 확인

## 분석 대상 관리

노션 '레퍼런스 계정' DB에서:
- 계정 추가: 행 추가 + `username`(@ 제외) 입력 + `모니터링` 체크
- 일시 중단: `모니터링` 체크 해제

## 로컬 실행

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export APIFY_TOKEN=... ANTHROPIC_API_KEY=... NOTION_TOKEN=...
python -m src.main --dry-run          # 노션 기록 없이 점검
python -m src.main --only getbarrel   # 특정 계정만
open site/index.html
```

테스트: `pytest -v`

## 비용 가늠

- Apify: 계정 8개 × ~11결과 × 매일 ≈ 월 2,600결과 (actor 요금표 참조, 무료 크레딧 있음)
- Claude: 새 게시물당 1회 + 히트당 1회 + 계정당 주 1회 종합 — 분석 캐시로 재분석 없음
