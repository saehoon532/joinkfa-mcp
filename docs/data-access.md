# 실제 JOIN KFA 데이터 접근 조사 — 2026-10-20

## 관찰

로그인된 사용자의 승인된 브라우저에서 확인한 사이트는 Nexacro 17 앱입니다. 최상위 주소는 `https://www.joinkfa.com/`로 유지됩니다. 경기 목록은 iframe, 경기 상세는 Nexacro 화면으로 렌더링됩니다.

사용자가 제공한 경기 상세 화면과 현재 브라우저의 표시 값이 일치했습니다. DOM상 하단 `Div00`에 경기번호/대회명/일시/주심/부심/대기심이 있습니다.

## 공개 원본 코드에서 확인한 요청

| 목적 | 요청 | 근거 |
|---|---|---|
| 대회 목록 | JSON POST `/portal/mat/getMatchList.do` | [matchPortal.jsp](https://www.joinkfa.com/service/portal/matchPortal.jsp?mgctype=S) |
| 월별 경기 | JSON POST `/portal/mat/getMatchSingleList.do` | [matchSingle.jsp](https://www.joinkfa.com/service/match/matchSingle.jsp) |
| 경기 상세 | XML POST `/generate/MAP_04_001/SEARCH00.do?CALL_TYPE=NEXACRO` | [MAT_04_001.xfdl.js](https://www.joinkfa.com/MAT/MAT_04_001.xfdl.js) |
| 전송 규격 | XML, `GP_EMPL_ID` 헤더 | [Transaction.js](https://www.joinkfa.com/nexacro17lib/component/extPrototype/Transaction.js) |
| 본인 배정 알림 | XML `/generate/REF_54_001/getSEARCH00.do?CALL_TYPE=NEXACRO` | [REF_54_001.xfdl.js](https://www.joinkfa.com/REF/REF_54_001.xfdl.js) |

`MAT_04_001` 파일의 실제 form name은 `MAP_04_001`입니다. 경로를 오타로 보고 MAT로 바꾸면 안 됩니다.

대회 목록: `v_CURPAGENUM`, `v_ROWCOUNTPERPAGE`, `v_YEAR`, `v_STYLE`, `v_MGC_IDX`, `v_AREACODE`, `v_SIGUNGU_CODE`, `v_ITEM_CD`, `v_TITLE`, `v_TEAMID`, `v_USER_ID`, `v_ORDERBY`. 응답은 `matchList`, `totalCount`; 대회 ID는 `IDX`.

경기 목록: `v_MATCH_IDX`, `v_YEAR_MONTH`, `v_TEAMID`, `v_CURPAGENUM`, `v_ROWCOUNTPERPAGE`, `v_USER_ID`, `v_ORDERBY`. 응답은 `singleList`, 개별 경기 ID는 `IDX`. 원본 UI는 한 달 최대 1000개로 요청하므로 그 경계에 도달하면 이 구현은 완전성을 주장하지 않고 중단합니다.

상세: `dsReqParam`의 `v_MATCH_IDX`, `v_SINGLE_IDX`, `v_USER_ID`. `dsCommonInfo`의 `REFEREE_MAIN`, `REFEREE_SUB1`, `REFEREE_SUB2`, `REFEREE_STANDBY`, `MATCH_CHECK_TIME`, `TITLE`, `MATCH_NUMBER`. UI는 SUB1에 합쳐진 부심 이름을 표시합니다.

`GP_EMPL_ID`는 user ID의 base64이며 **인증 수단이 아닙니다**. 세션 쿠키와 서버 권한 검사가 필요합니다. 사용자 ID를 MCP 인자로 받아 다른 사람으로 변경하지 않습니다.

본인 배정 알림 화면은 `dsSearch` (`V_YEAR`, `V_ACCEPT_STAT`, `V_ASSIGN_STAT`, `V_CURPAGENUM`, `V_ROWCOUNTPERPAGE`, `V_REF_NO`, `V_USRKEY`)를 사용합니다. 이것은 경기 상세 역할 조회와 다른 데이터입니다. 해당 개인정보 식별자를 브라우저에서 추출하거나 다른 계정 ID를 시도하지 않았으며 이 API는 구현 범위에서 제외했습니다.

## 미확인 사항

- 공식 OAuth/API 제공 여부 및 비밀번호/SSO/MFA 로그인 요청 규격.
- 서버 측 HTTP 세션 이식 가능 여부, 추가 CSRF 요구, IP/기기 결합.
- 실응답의 XML namespace/오류 처리와 인증 만료 응답의 모든 경우.
- 원본 대회 목록의 비공개 경기 누락 범위, 취소된 경기 처리, 동명이인 식별.

따라서 로그인 엔드포인트를 추측해 만들지 않았습니다. 코드의 인증 상태 확인은 UI에서 관찰한 내정보 title과 Logout 요소를 엄격하게 검사하며, 레이아웃이 다르면 안전하게 실패합니다. 실세션 통합 테스트에서 필요한 규격을 확인한 후 수정해야 합니다.
