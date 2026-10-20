# JOIN KFA MCP

기존 공개 페이지 조회 도구에 **로그인 계정에서 열람 가능한 경기 상세의 심판을 이름으로 검색하는 읽기 전용 기능**을 추가했습니다. 검색 대상은 로그인 계정의 이름과 달라도 됩니다. `start_referee_lookup`의 `month`(1~12)로 조회 월을 제한할 수 있습니다. 호출자는 기존 소유자 OAuth 인증을 통과해야 하며 JOIN KFA의 접근 권한은 변경하지 않습니다.

## 현재 검증 범위

- 로그인된 실제 화면에서 엄세훈 계정과 경기 상세의 심판 표시 구조를 확인했습니다.
- 공식 사이트가 배포하는 화면 코드에서 JSON 목록과 XML 상세 요청 규격을 확인했습니다. [조사 기록](docs/data-access.md)
- 로그인 쿠키를 브라우저에서 추출하거나 Render에 업로드하지 않았습니다.
- 실제 인증 세션을 이용한 HTTP 어댑터 통합 테스트, 2026년 전체 경기 집계, 배포는 **미완료**입니다. 모의 응답 테스트는 실서버 검증과 다릅니다.
- 기본 설정은 기존 공개 도구만 제공합니다. GitHub OAuth(본인 숫자 ID 제한) 또는 외부 JWT로 MCP 호출자를 인증하고, 조회에는 별도 JOIN KFA 세션이 필요합니다.

## 설치 및 테스트

Python 3.12 환경에서:

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
python server.py
```

기존 `list_sources`, `inspect_public_page`와 `/mcp` 주소를 유지합니다.

## 비공개 도구

`KFA_PRIVATE_ENABLED=1`일 때만 등록됩니다. `KFA_AUTH_MODE=github`에서는 GitHub OAuth와 숫자 계정 ID를 검증합니다. 외부 JWT 모드에서는 RS256 서명, issuer, audience, 만료, `assignments:read` scope, 소유자 `sub`를 검증합니다. 로컬/in-process 호출도 인증을 우회할 수 없습니다.

1. `referee_auth_status`: 서버에 설정된 JOIN KFA 세션의 본인 이름과 로그인 여부 확인.
2. `start_referee_lookup(name="엄세훈", year=2026, competition_ids=null)`: 연도별 축구 대회 목록 탐색 시작. 특정 대회 ID 1~20개로 범위를 좁힐 수도 있습니다.
3. `continue_referee_lookup(job_id, request_budget=5)`: 조회를 조금씩 진행. 한 번에 최대 20개 요청. 원본 사이트에는 순차적으로 요청합니다.
4. `referee_lookup_results(job_id, offset=0, limit=50)`: 확인된 경기 목록과 역할별 집계.
5. `cancel_referee_lookup(job_id)`: 메모리의 조회 결과 제거. 원본 배정은 변경하지 않습니다.

작업은 단일 프로세스 메모리에 최대 3개, 1시간 동안 유지됩니다. 서버 재시작/스케일 변경 시 사라집니다. 조회 도중 원본 데이터가 변경되면 새로 시작해야 할 수 있습니다.

## 집계 의미

- `count`는 **선택 범위의 조회가 모두 성공했을 때만** 숫자입니다. 진행 중/인증 만료/접근 거부/형식 변경/페이지 누락 시 `null`입니다. `confirmed_so_far`는 중간 확인 건수입니다.
- 대회 ID + 개별 경기 ID로 중복을 제거하고 경기일 기준 1월 1일~12월 31일을 포함합니다. 미래 경기의 배정도 상세에 표시되면 포함합니다. 역할별 건수는 한 경기에서 역할이 중복되면 전체 경기 수와 다를 수 있습니다.
- 부심 필드에 쉼표로 여러 이름이 들어가는 경우를 처리합니다. 부분 이름은 일치로 세지 않습니다.
- 이름만으로 동명이인을 확정 구별할 수 없습니다. 결과는 **동일 표시 이름 기준**입니다.
- 연도별 축구 대회 목록은 모든 비공개 대회·풋살·협회 내부 배정을 보장하지 않습니다. 완료되어도 해당 조회 범위의 건수이며, 무조건 전국 전체 건수라고 표현하면 안 됩니다.
- 심판 메뉴의 `배정받기` 알림 수는 예비/최종/대회 단위 알림이 섞일 수 있어 이 경기 집계와 별개입니다.

## 인증과 배포

[Render 배포 가이드](docs/deployment.md)를 따르세요. `.env.example`은 설명용이며 자동으로 로드하지 않습니다. 운영 환경변수에 직접 설정합니다. 비밀번호·쿠키·JWT 원문·HAR 파일을 채팅, 저장소, 로그에 넣지 마세요.
