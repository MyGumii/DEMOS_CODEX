# UI1 local golden journey — canonical publication result

기준 시각: 2026-08-14 23:12 KST  
프로필: `free-owner-chrome-desktop-ko-KR`  
상태: local green, REF-FREE-A/B reference pending

## 결과

`UI1-FREE-DESKTOP-GOLDEN-JOURNEY`의 로컬 실제 Chrome 경로가 합성 데이터와 격리된 data root에서 통과한다.

- sidebar 접기 후 포커스는 보이는 restore 버튼으로 이동한다.
- sidebar 복원 후 포커스는 보이는 collapse 버튼으로 이동한다.
- search를 연 뒤 Escape를 누르면 시작 control인 search trigger로 포커스가 돌아온다.
- 한국어 title/paragraph는 저장 후 reload에서 정확히 복원된다.
- Second Brain preview 취소는 canonical item/revision을 만들지 않는다.
- 최초 승인은 canonical item 1개와 immutable revision 1개를 만든다.
- 변경 없는 같은 페이지의 재승인은 item/revision을 추가하지 않는다.
- canonical `source_artifact.metadata_json.notion_provenance`는 같은 로그인 세션에서 다시 캡처한 Notion source snapshot의 owner/page/revision/updated/hash와 정확히 일치한다.

## 구현 경계

이 결과를 위해 legacy Markdown writer의 `_unique_name()`이나 graph/index projection을 idempotency authority로 바꾸지 않았다. 테스트 전용 `ui_local_fixture`가 다음 기존 구성 요소를 격리 root 아래에서 조립한다.

1. canonical schema migrator와 SQLite authority;
2. 합성 `ui1-owner` account와 personal space;
3. 기존 `PublicationPreviewService`, `PublicationService`, `PublicationApiService`;
4. 기존 standalone P4.5 publication route registrar;
5. 기존 Notion page snapshot capture를 `AuthorizedNotionPageSnapshot`으로 변환하는 test-only adapter.

일반 `app_notion.create_app()` 호출, production feature flag, live catalog, legacy Knowledge files에는 변화가 없다. canonical probe 실패 시 legacy로 다시 쓰는 경로도 추가하지 않았다.

## 로컬 증거

| scenario | capture SHA-256 |
|---|---|
| `UI-SHELL-002` | `sha256:e1264295173e4c39994aff5b8e6785e4395ac8b0a5f16ac7c4ec030459a79785` |
| `UI-SEARCH-001` | `sha256:a920c1c018b1ec3d5a9e08296959760ef8605c4632500155dd3a674423c857e0` |
| `UI-EDITOR-001` | `sha256:2a0fda9dcc026a7a2e22ae3563ceb13e36d645de9d55a49a180236982cf7c575` |

Second Brain aggregate:

```json
{"approve_item_delta":1,"approve_revision_delta":1,"cancel_item_delta":0,"cancel_revision_delta":0,"replay_item_delta":0,"replay_revision_delta":0,"source_snapshot_matches":1}
```

모든 값은 identifier-free aggregate다. raw DOM, network log, cookie, token, password, page ID, workspace ID, filesystem path를 artifact로 저장하지 않았다.

## 검증

- real Chrome golden journey: `2 passed in 31.39s`;
- focused canonical approval journey: `1 passed in 13.08s`;
- Node UI contracts: 6/6 pass;
- Notion route/atomic-save/publication adapter: 25 pass;
- canonical publication routes + unchanged Notion no-op: 9 pass;
- workspace route/navigation/auth isolation: 41 pass;
- Second Brain publication restart/chat characterization: 5 pass;
- scenario/evidence/differential verifier: 35 pass;
- Python compile and `node --check`: pass.

## 남은 gate

Chrome Browser control은 브라우저 선택 전 초기화 과정에서 `failed to write kernel assets: 지정된 경로를 찾을 수 없습니다. (os error 3)`로 중단됐다. Notion tab, workspace switcher, 개인 workspace, 개인 content에는 접근하지 않았다.

따라서 이 문서는 local H1 acceptance만 증명한다. `live-characterized` 또는 `differential-passed`를 주장하지 않는다. 브라우저 연결이 복구되면 새 Free workspace A/B에서 C0 안전 확인 후 동일 scenario를 관찰하고 첫 reference/local differential을 생성해야 한다.

