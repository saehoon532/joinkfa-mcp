# Render 배포 및 실세션 검증

현재 서비스: `https://joinkfa-mcp.onrender.com/mcp`

현재 변경은 로컬 구현이며 배포되지 않았습니다. 브라우저 로그인은 이 원격 서버에 로그인한 것이 아닙니다. 기존 플러그인의 앱 ID와 서버 주소는 유지합니다.

## 1. 공개 모드로 회귀 검사

별도 검토 브랜치에서 테스트 후 기존 Render 빌드 `pip install -r requirements.txt`, 시작 `python server.py`를 사용합니다. `PORT`는 Render가 제공합니다. 기본 `KFA_PRIVATE_ENABLED=0`에서는 기존 두 도구만 등록되며 세션 파일을 읽지 않습니다.

## 2. MCP 호출자 인증 먼저 구성

현재 선택한 배포 방식은 GitHub OAuth입니다. GitHub OAuth 앱의 Homepage는
`https://joinkfa-mcp.onrender.com`, Callback은
`https://joinkfa-mcp.onrender.com/auth/callback`으로 등록합니다.

Render Environment에 아래 항목을 설정합니다.

- `KFA_PRIVATE_ENABLED=1`
- `KFA_AUTH_MODE=github`
- `KFA_OWNER_SUB=74544620` (연결 저장소 소유자의 검증된 숫자 GitHub ID)
- `KFA_GITHUB_CLIENT_ID`: 등록된 OAuth 앱 ID
- `KFA_GITHUB_CLIENT_SECRET`: Render의 비밀 환경변수에만 저장
- `KFA_SESSION_FILE=/etc/secrets/kfa-session.json`

GitHub 모드는 FastMCP OAuth proxy의 PKCE/동의 화면/토큰 검증을 사용합니다.
`read:user`만 요청하고 저장소 권한은 요청하지 않습니다. 모든 GitHub 사용자가
접근할 수 있는 것이 아니라 위 숫자 ID의 계정만 허용합니다. JOIN KFA 세션 파일이
없어도 OAuth 서버는 시작되지만 조회는 `SESSION_NOT_CONFIGURED`로 차단됩니다.
기본 OAuth 상태 저장소는 암호화된 로컬 디스크이며 Render 무료 인스턴스 재배포 시
유실될 수 있으므로 재연결이 필요합니다. 지속 보관 서비스 추가는 별도 작업입니다.

아래는 선택 가능한 기존 외부 JWT 모드 설명입니다. GitHub 모드에는 외부 공개키·issuer 설정이 필요하지 않습니다.

비공개 데이터 배포 전 신뢰하는 OAuth/OIDC 제공자에서 RS256 access token을 발급하도록 구성합니다. `.env.example`의 issuer/audience/소유자 sub와 **공개키**를 설정하고 `assignments:read` scope를 요구합니다. 공개키는 `/etc/secrets/mcp-public-key.pem`에 둘 수 있습니다. 개인키를 이 서버에 넣지 않습니다.

이 구현은 JWT resource server이고 OAuth 로그인/동적 클라이언트 등록 서버는 아닙니다. ChatGPT 연결의 OAuth 사용 가능 여부와 제공자의 authorization/token/discovery 설정을 별도로 검증해야 합니다. 기존 무인증 연결이 비공개 도구에 그대로 접근할 수 있다고 가정하지 마세요. OAuth UI를 구성하기 전에는 적법한 JWT를 가진 테스트 클라이언트에서만 비공개 기능을 검증합니다.

## 3. JOIN KFA 세션은 별도로 승인·설정

현재 브라우저 쿠키를 자동 수집하거나 Render에 보내는 기능은 없습니다. 사용자 로그인 세션을 서버가 재사용하면 서버 운영자/호스팅 환경에도 계정 접근권이 생깁니다. 그 권한 이전을 명시적으로 결정한 뒤, 운영자만 접근하는 비밀 저장소를 통해 세션을 마련해야 합니다. 비밀번호 로그인 API를 추측하거나 CAPTCHA/MFA를 우회하지 마세요.

세션 파일은 다음 **스키마**를 따릅니다. 아래 값은 실제 자격증명이 아니므로 실행할 수 없습니다.

```json
{
  "name": "본인 표시 이름",
  "user_id": "본인 서버 사용자 ID",
  "expires_at": 0,
  "cookies": {"실제 쿠키 이름": "실제 쿠키 값"}
}
```

`expires_at`은 UTC Unix 초이며 현재로부터 최대 8시간 이내여야 합니다. 실제 upstream 만료를 연장하는 설정이 아닙니다. 갱신 시 새로 로그인하고 파일을 안전하게 교체합니다. 로컬은 소유자 전용 파일 권한(Unix 0600, Windows 사용자 전용 ACL)을 적용하고 Render는 Secret File `/etc/secrets/kfa-session.json`으로 설정합니다. 소스/로그/채팅에 붙여넣지 마세요. 전체 HAR/브라우저 프로필은 필요하지 않습니다.

서비스가 세션 파일을 재사용할 권한이 승인되지 않았다면 비공개 모드를 켜지 말고, 현재 로그인 브라우저를 통한 조회를 사용합니다.

## 4. 스테이징에서 검증

모든 설정이 갖춰진 후 `KFA_PRIVATE_ENABLED=1`로 시작합니다. 설정이 빠지면 서버가 실패하도록 되어 있습니다. 운영 원본에 덮어 배포하기 전에:

1. 무인증/잘못된 서명/다른 sub/잘못된 scope로 `/mcp`에 요청하면 거부되는지 검사합니다.
2. `referee_auth_status`에서 본인 이름을 확인합니다. 실패하면 공개 페이지/빈 목록으로 대신 성공 처리하지 않습니다.
3. 브라우저에서 확인한 대회 한 개로 조회합니다. 실제 경기 ID, 날짜, 역할을 화면과 비교합니다. 테스트용 모의 데이터를 실검증으로 대신하지 않습니다.
4. 쿠키 만료·접근 거부·형식 변경이 최종 0건을 반환하지 않는지 확인합니다.
5. 그 다음 2026년 대회 목록 전체로 범위를 넓힙니다. `complete=true` 전에는 최종 건수라고 보고하지 않습니다. `coverage_note`와 동명이인 한계를 함께 표시합니다.

작업 상태가 프로세스 메모리에 있으므로 **1개 worker/1개 instance**로 검증합니다. 재시작하면 새 job을 시작합니다. 장기 운영에는 사용자별 암호화된 세션 보관, 세션 폐기, 감사 기록, 원본 사이트 이용 허용 범위 검토 및 durable job 저장소가 추가로 필요합니다.

## 5. 반영과 롤백

실세션 검증을 통과한 커밋을 기존 Render 서비스에 배포하고 연결 도구 목록을 갱신합니다. 기존 앱 연결 식별자는 변경하지 않습니다. 문제 발생 시 `KFA_PRIVATE_ENABLED=0`으로 전환하고 재배포하면 공개 기능만 남습니다. 세션 Secret File을 제거하고 JOIN KFA에서 해당 세션을 종료합니다.

현재 작업은 인증 정보 이전이나 운영 배포를 수행하지 않았습니다. 따라서 아직 2026년 엄세훈의 최종 배정 경기 수를 제공할 수 없습니다.
