# Hermes ↔ Second Brain 연결

이 연결은 메일 전용이 아닙니다. 조사, 회의, 업무 요청, 코드 분석 등에서
Hermes가 선택한 Wiki를 참고하고, 결과·원본·실행 근거를 다시 보내는 공통 도구입니다.
Second Brain이 기존 프롬프트·등록 검사 규칙에 따라 지식 후보를 작성합니다.

## 시작

1. `http://127.0.0.1:10221`에 본인 계정으로 로그인하고 **에이전트 연결**을 엽니다.
2. **내 지식** 또는 **내 지식+공유 지식**을 선택하고 참고 개인 Wiki를 하나 이상
   선택합니다. 후자는 권한 있는 파트·팀·담당 뷰 하나를 함께 지정합니다.
   기본 등록 개인 Wiki와 선택 가능한 추가 Wiki, 필요하면 본인 조직 등록을 허용합니다.
   폴더는 Wiki ID로 지정하며 서버의 임의 파일 경로를 열지 않습니다.
3. 필요한 경우 Notion 기록 위치를 선택하고 연결을 만듭니다.
   표시된 토큰을 본인 Hermes 프로필의 별도 파일에 저장합니다.
4. [hermes_mcp.py](../../agent_integrations/hermes_mcp.py) **한 파일만** Hermes가
   실행되는 PC로 복사합니다. 예: `D:/Hermes/tools/hermes_mcp.py`.
   `code_assist_v1` 폴더, `contracts.py`, `__init__.py`, Wiki·DB는 필요하지 않습니다.
   직접 옮기는 대신 아래의 서버 다운로드 설치를 사용할 수도 있습니다.
5. [config.example.yaml](config.example.yaml)의 `second_brain` 항목을 해당
   Hermes 프로필의 `mcp_servers`에 추가합니다. 기존 항목은 유지합니다.
   Python·어댑터·토큰 경로는 모두 Hermes가 실행되는 PC의 경로입니다.
6. 연결을 확인하고 Hermes를 다시 시작합니다.

현재 로컬 설치는 `HERMES_HOME=D:\Hermes`이므로 설정 파일은
`D:\Hermes\config.yaml`, 토큰 파일은 `D:\Hermes\second-brain.token`을
사용합니다. 다른 Hermes 프로필에서는 해당 프로필의 실제 경로로 바꿉니다.
아래 예시는 어댑터를 `D:/Hermes/tools/hermes_mcp.py`로 복사한 경우입니다.
기존에 서버 프로젝트 안의 어댑터를 지정한 설정도 계속 사용할 수 있습니다.

```powershell
$env:SECOND_BRAIN_URL = 'http://127.0.0.1:10221'
$env:SECOND_BRAIN_TOKEN_FILE = 'D:/Hermes/second-brain.token'
& 'D:/Hermes/hermes-agent/venv/Scripts/python.exe' 'D:/Hermes/tools/hermes_mcp.py' --check
```

MCP 실행에는 `mcp` 2.x가 설치된 Python을 사용합니다. 현재 로컬 Hermes 환경에서
초기화·도구 조회·인증 API 호출을 검증했습니다. `--check`, `--drain`은 표준
라이브러리만 사용합니다. 원격 서버 연결은 HTTPS가 필요합니다.

### 다른 PC에서 연결

2026-09-11 단일 파일 수정부터 어댑터는 서버 Python 패키지를 가져오지 않습니다.
클라이언트에 필요한 프로그램은 위 파일과 MCP 2.x가 설치된 Python뿐입니다.
토큰 파일을 쓰면 그 파일도 클라이언트에 저장합니다. 토큰 직접 입력을 쓰려면
`SECOND_BRAIN_TOKEN_FILE` 대신 `SECOND_BRAIN_TOKEN`을 설정하며 둘을 함께 쓰지 않습니다.

`SECOND_BRAIN_URL`에는 해당 서버의 접속 가능한 HTTPS 원점 주소를 넣습니다
(예: `https://brain.example.com`, 설명용 주소). `/api/integrations/v1` 경로는
어댑터가 붙입니다. 다른 PC에서 `127.0.0.1`은 그 클라이언트 자신을 가리킵니다.
현재 개발 실행기는 10221을 호스트의 loopback에만 바인딩하므로 다른 PC 접근에는
별도의 HTTPS 서버 노출 설정이 필요합니다. 토큰은 접속할 서버의 본인 계정에서
발급한 것을 사용합니다. 이 변경이 서버를 외부에 자동 공개하지는 않습니다.

Hermes에 실제 서버 주소·어댑터 파일 경로·토큰 파일 경로를 알려주고, 기존 설정을
보존하면서 `mcp_servers.second_brain`을 구성해 `--check`로 인증까지 확인하도록
요청하면 됩니다. 토큰 원문은 대화에 넣을 필요가 없습니다.

### 파일을 옮기지 않고 프롬프트로 설치

서버는 공용 클라이언트 파일과 설치 안내를 다음 경로로 제공합니다.

- `/static/integrations/hermes/install.md`: Hermes에 전달할 설치 절차
- `/static/integrations/hermes/manifest.json`: 클라이언트 버전 계약·크기·SHA-256
- `/static/integrations/hermes/hermes_mcp.py`: 검증한 단일 파일 어댑터
- `/static/integrations/hermes/second-brain/SKILL.md`: 필요할 때 호출하는 Hermes 스킬

설치 안내·프로그램 다운로드에는 로그인이 필요하지 않습니다. 계정과 Wiki API는
기존 토큰 인증을 그대로 요구합니다. 새 API나 인증 예외를 추가하지 않고 기존
정적 파일 제공 경로를 사용합니다. 설치 안내는 [client_install.md](client_install.md)가
원본입니다. 프로그램은 서버에서 실행되는 것이 아니라 클라이언트로 내려받아
Hermes가 실행합니다.

Hermes의 대화창에 아래를 붙여 넣고 주소와 토큰 위치를 바꿉니다.

```text
이 PC의 Hermes에 Second Brain 연결을 설치해줘.
서버 주소: https://실제서버주소
토큰 파일: 이 PC에 저장한 토큰 파일의 절대 경로

위 서버의 /static/integrations/hermes/install.md를 읽고 진행해줘.
서버에서 어댑터를 다운로드하고 manifest의 SHA-256을 확인해줘.
기존 Hermes 설정을 백업하고 다른 연결을 유지하면서 MCP를 설정해줘.
--check 인증과 실제 MCP 도구 목록·상태 호출을 확인하고 결과를 알려줘.
토큰 원문은 출력하지 말고, Hermes 재시작이 필요하면 알려줘.
```

토큰을 직접 제공하려면 `토큰 파일` 대신 `토큰 값`을 지정할 수 있습니다.
설치 절차는 이를 로컬 토큰 파일에 저장해 사용하도록 합니다. 이미 어댑터를 복사한
경우에는 `어댑터 파일: 로컬 절대 경로`를 추가하면 다운로드를 생략합니다.
10221의 같은 PC에서는 서버 주소를 `http://127.0.0.1:10221`로 사용할 수 있습니다.
원격 HTTPS 진입 주소를 만드는 작업은 별도입니다.

서버 배포 시에는 `static/integrations/hermes/`의 클라이언트 파일과 스킬도 포함합니다. 어댑터·스킬이나
설치 안내를 수정하면 `python -m code_assist_v1.tools.package_hermes_client`로
재생성하고 `--check`로 원본과 일치하는지 확인합니다. 테스트도 오래된 배포 파일을
검출합니다. 저장소의 공용 코드 파일만 패키징하며 계정·토큰·서버 설정은 포함하지
않습니다.

토큰은 발급 직후 표시되며, 이후에도 **연결 설정 → 내 연결 → 토큰 보기**에서
같은 값을 다시 확인하고 복사할 수 있습니다. 본인 로그인과 현재 연결 권한이
필요하며 만료·폐기된 연결은 표시할 수 없습니다. **토큰 숨기기** 또는 탭 이동·창
닫기로 표시를 지웁니다. 조회만으로 만료 시각이나 토큰 값은 바뀌지 않습니다.
서버에는 토큰 원문을 추가 저장하지 않으며 조회 이력에도 토큰 값을 남기지 않습니다.
생성·조회 요청의 동일한 `operation_id` 재시도는 현재 권한을 다시 확인합니다.
유효 기간은 기본 30일이고,
화면에서 즉시 폐기할 수 있습니다. 자동 연장을 켜면 성공한 Hermes API 사용 시점부터
설정 기간만큼 갱신하며 같은 토큰을 유지합니다. 기존 연결에서도 자동 연장을 켜고
끌 수 있습니다. 만료·폐기·계정 권한 변경은 자동 연장으로 복구하지 않습니다.
계정 ID, 다른 Wiki ID, 토큰을 모델이 도구 인수로 바꿔서 권한을 넓힐 수 없습니다.

## 필요할 때 스킬로 사용

[second-brain/SKILL.md](second-brain/SKILL.md)를 실제 Hermes 프로필의
`skills/second-brain/SKILL.md`에 설치합니다. 로컬 설치 위치는
`D:/Hermes/skills/second-brain/SKILL.md`입니다. 이미 MCP가 연결되어 있으면
토큰과 YAML을 다시 설정할 필요가 없습니다. 서버 다운로드 설치도 이 스킬을 포함합니다.

Hermes **대화창**에서 다음을 각각 실행합니다.

```text
/reload-skills
/second-brain 우리 프로젝트 기준으로 이 요청의 처리 방안을 확인해줘
```

관련 후속 질문은 명령 없이 이어서 입력합니다. 이미 받은 근거로 설명할 수 있으면
추가 호출하지 않고, 새로운 근거·최신 확인·Second Brain의 종합이 필요할 때 조회합니다.
“Second Brain 그만” 또는 “일반 대화로”로 종료합니다. 무관한 주제나 새 대화에 상시
조회 규칙을 적용하지 않습니다. 조회만 한 결과가 자동으로 Notion이나 Wiki에 저장되지는
않으며 저장 요청이나 해당 자동화의 승인 범위에 따라 기존 축적 도구를 사용합니다.

이는 모델에게 주는 대화 규칙으로 강제 라우터가 아닙니다. MCP 시작 시 초기 연결 비용은
남으며, Ask의 새 요청에는 Hermes가 필요한 앞선 맥락을 함께 전달합니다. 서버의 같은
대화를 자동 재개하는 기능은 아닙니다.

## 도구

| 도구 | 역할 |
|---|---|
| `second_brain_status` | 연결 상태·권한 확인 |
| `second_brain_search` | 선택한 참고 Wiki 검색, 문서·리비전·출처 반환 |
| `second_brain_read` | 검색에서 얻은 문서 본문 읽기 |
| `second_brain_ask` | 선택한 Wiki를 참고해 Second Brain이 답변 작성 |
| `second_brain_submit` | 원본·제안·액션을 작업으로 접수 |
| `second_brain_run` | 상태·원본·근거·정제 결과 조회 |
| `second_brain_refine` | Second Brain 정제 실행 또는 결과 확인 |
| `second_brain_record_notion` | 선택한 Notion 위치에 현재 버전 기록 |
| `second_brain_outlook_inbox` | 선택적으로 Outlook 받은 메일 읽기 |
| `second_brain_observe_outlook_reply` | 서버가 보낸 회신을 직접 조회해 근거 기록 |

쓰기에는 호출자가 만든 UUID `operation_id`를 사용하고, 통신 실패 재시도에서는
같은 ID와 내용을 유지합니다. 접수 시 반환되는 `run_id`를 후속 도구에 전달합니다.
이미 등록한 작업의 후속 결과는 새 작업의 `followup_of`에 이전 ID를 넣습니다.
UI의 **후속 결과 기록 만들기**도 같은 방식입니다. 과거에 등록한 문서는 덮어쓰지 않습니다.

### Ask 사용

Hermes가 직접 자료를 종합하려면 `search`로 후보를 찾고 필요한 문서만 `read`합니다.
Second Brain에게 Wiki 맥락을 바탕으로 설명·비교·판단을 요청하려면 `ask`를 사용합니다.
기존 단일 Wiki 연결은 기존 검색·대화 문맥 구성·답변 생성 경로를 사용합니다.
새 세트 연결은 선택한 개인 Wiki들과 공유 뷰를 검색해 한 번의 모델 호출로 종합합니다.
검색은 기본 16 KiB의 전체 응답 예산을 공유하며 Wiki 폴더 전체를 보내지 않습니다.

`second_brain_ask`에는 UUID `operation_id`와 질문 `message`를 전달합니다.
HTTP에서는 `POST /api/integrations/v1/knowledge/ask`에 같은 JSON을 보냅니다.

- `state=completed`: `answer`에 답변 Markdown, `response`에 기존 출처·모델 메타데이터,
  기존 연결은 `conversation_id`에 저장된 대화 ID를 반환합니다. 세트 연결은
  `conversation_id=null`, `response.kind=knowledge_set_answer`이며 연동 장부에
  답변·출처·선택 범위가 저장됩니다. 단일 Wiki에 묶인 일반 Agent 대화와 구분됩니다.
- 통신 실패 후에는 같은 ID와 질문을 재사용합니다. 완료된 요청은 모델을 다시 호출하지
  않고 해당 요청에 저장된 본문을 반환합니다. 같은 ID로 질문·조회 범위를 바꾸면 충돌입니다.
- 새 질문에는 새 ID를 사용합니다. 현재 각 새 요청은 독립 요청이며 이전 질문의
  대화 이력을 자동으로 이어받지 않습니다.
- 실패·취소 상태에서는 `answer`가 `null`입니다. 실패에는 `error`가 포함됩니다.
  완료 기록의 본문을 읽지 못하면 `503 ANSWER_UNAVAILABLE`로 응답합니다.

`response`의 출처 정보는 모델에 제공된 자료와 인용 추적 정보입니다. 답변의 모든
주장이 사실로 검증되었다는 의미는 아닙니다. Ask 답변을 다시 제출할 때도 에이전트
결과와 원본 근거를 구분하고 아래 정제·검토 절차를 적용합니다.

2026-09-11 수정부터 API와 MCP 모두 답변 본문을 반환합니다. 이전 안내에 따라
`tools.exclude`에 `second_brain_ask`를 넣었다면 그 항목을 제거합니다. 연결 권한에
`knowledge:ask`도 있어야 합니다. 현재 에이전트 연결 화면에서 새로 만든 연결은
이 권한을 포함합니다.

원본에는 `id`, `kind`, `title`, `text`를 넣습니다. `kind`는 `document`, `email`,
`meeting`, `user_input`, `agent_output`, `execution_report` 중 하나입니다.
선택 필드는 `uri`, `external_id`, `revision`입니다. 액션은 `description`,
`completion_condition`, 선택적으로 `source_id`를 갖습니다. 도구의 제안 또는
실행 보고가 제출되었다는 이유만으로 서버 관측이나 완료 사실이 되지 않습니다.

## 자동 처리와 검토

10221은 접수된 작업을 서버 큐에서 정제합니다. 다른 배포에서는
`CODE_ASSIST_AGENT_WORKER=1`로 활성화하거나 다음 소비 명령을 사용할 수 있습니다.

```powershell
& 'D:/Hermes/hermes-agent/venv/Scripts/python.exe' 'D:/Hermes/tools/hermes_mcp.py' --drain
```

기본 경로는 **접수 → Second Brain 정제 → 사용자 검토 → Wiki 등록 제출**입니다.
제안, 실행 승인, 사용자 완료 보고, 서버 회신 관측, 실제 업무 결과를 구분합니다.
승인과 메모는 완료 근거가 아니며, 증거가 없으면 미확인 상태를 유지합니다.
근거가 추가되면 이전 초안을 무효화하고 다시 정제합니다.

자동 등록을 선택해도 모델의 요약·해석은 자동 확정하지 않습니다. 검증된 원문
인용과 출처만 기록합니다. 인용 일치는 외부 사실의 참·거짓 증명이 아닙니다.
해석·결정·액션 결과·불확실성이 있는 후보는 사용자가 원문과 대조해 검토합니다.
기존 Wiki의 중복·충돌·정제 절차도 그대로 거칩니다. API의 `published`는 등록
트랜잭션이 접수되었다는 뜻이며, 실제 공개 상태는 지식탭에서 확인합니다.

## Notion과 메일 설정

Notion은 이 프로젝트의 `app_notion` 저장소를 사용합니다. notion.so의 외부
Notion API 연결은 이번 어댑터에 포함하지 않습니다.

- 협업 Notion: app_code에도 app_notion과 같은
  `CODE_ASSIST_I0_COLLABORATIVE_SHARING=1`,
  `CODE_ASSIST_COLLABORATION_CATALOG`,
  `CODE_ASSIST_COLLABORATION_USER_DATA_ROOT`를 설정합니다. 이미 검증·마이그레이션된
  해당 환경의 저장소를 지정해야 합니다. `CODE_ASSIST_AGENT_NOTION_MODE=collaborative`.
- 기존 개인 Notion: 해당 환경이 기존 개인 저장소와 atomic save를 사용하는 경우에만
  `CODE_ASSIST_AGENT_NOTION_MODE=legacy`를 명시합니다. 협업 환경에서는 이 우회가 거부됩니다.
- Second Brain과 기존 개인 Notion의 데이터 폴더가 다르면 서버의
  `CODE_ASSIST_AGENT_NOTION_USER_DATA_ROOT`에 Notion의 기존 `user_data` 폴더를 지정합니다.
  이 바인딩은 Notion 호출에만 적용됩니다. 기본 Wiki·로그인 데이터 폴더는 바꾸지 않습니다.
  기존 개인 Notion 선택 목록에는 하위 페이지까지 전체 경로가 표시됩니다.
- 설정하지 않은 Notion 연결은 비활성 상태로 표시됩니다. 협업 모드에서는 편집 가능한
  Workspace를, 기존 개인 모드에서는 본인 소유 페이지를 기록 위치로 선택합니다.
- Notion 기록은 작업 버전별 별도 페이지입니다. 기존 페이지에 사용자가 추가한 내용을
  자동으로 덮어쓰지 않으며 재시도는 같은 페이지를 가리킵니다.
- 서버 Outlook 도구를 사용하려면 `CODE_ASSIST_AGENT_OUTLOOK=1`, 기존 Microsoft
  Graph 앱 설정과 **같은 사용자 계정의 Outlook 로그인**, 연결의 `mail:read`가 필요합니다.
  메일 발송·삭제·읽음 변경 기능은 포함하지 않습니다. 별도의 Hermes 메일 도구를
  사용하는 경우 서버 Outlook 설정 없이도 공통 지식 연결을 사용할 수 있습니다.

[연결 옵션·자동 등록·각 버튼의 상세 안내](connection_controls.md)에 실제 동작과
Second Brain 자체 Outlook 연결에 필요한 서버 설정을 정리했습니다.

## 매시간 메일 예시

[hourly_mail_prompt.md](hourly_mail_prompt.md)를 업무·수신함에 맞게 설정한 뒤 Hermes의
시간당 cron 작업에 사용합니다. 메일 중요도 정책과 실제 메일 계정, 결과 전달 위치는
사용자 설정입니다. 설치 과정에서 cron이나 외부 메시지 발송을 자동으로 시작하지 않습니다.

[knowledge_loop_prompt.md](knowledge_loop_prompt.md)는 메일 외의 작업에도 적용하는
공통 지침입니다. 이 문서 파일은 워크플로 참고 자료이며 Codex 스킬을 설치하지 않습니다.

10011에 패치할 때는 공통 모듈·화면·런타임 등록 코드와 환경 설정을 함께 옮깁니다.
Hermes의 서버 주소를 바꾸고 **10011 본인 계정에서 새 연결을 발급**합니다.
10221의 테스트용 연결 DB·계정·토큰을 운영 데이터에 덮어쓰지 않습니다.
