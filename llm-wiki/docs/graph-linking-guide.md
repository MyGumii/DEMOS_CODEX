# Graph Linking Guide

Second Brain 지식 에이전트는 Markdown 본문과 graph edge를 함께 사용합니다. graph edge는 사실 자체가 아니라 "어떤 문서를 함께 읽어야 하는지"를 알려주는 연결 정보입니다.

LMNotebook에서는 보통 문서를 한 개씩 올려 Markdown을 뽑습니다. 따라서 LMNotebook이 모든 문서 간 관계를 확정할 필요는 없습니다. 대신 각 문서에서 나중에 연결 anchor가 될 정보를 최대한 뽑아내야 합니다.

연결 anchor 예:

- 문서명, 프로젝트명, 시스템명, 공정명, 조직명
- 기준일, 버전, 릴리스, 단계, milestone
- 지표명, 리스크명, 요구사항명, 결정 사항명
- "이전 기준", "후속 검토", "참조 문서", "대체 기준" 같은 관계 단서
- source file path, function/class name, config key, command, API endpoint, log signature

## Edge types

### supports

현재 문서가 다른 문서의 주장, 결정, 절차, 수치를 뒷받침할 때 사용합니다.

예:

```yaml
edge_type: supports
reason: "현재 문서의 실험 결과가 이전 설계 기준의 타당성을 뒷받침함"
```

### requires

현재 문서를 이해하거나 실행하기 위해 다른 문서의 선행 지식이 필요할 때 사용합니다.

예:

```yaml
edge_type: requires
reason: "운영 절차를 적용하려면 인터페이스 정의 문서를 먼저 이해해야 함"
```

### supersedes

현재 문서가 이전 문서의 기준, 수치, 정책, 결정 사항을 갱신하거나 대체할 때 사용합니다.

예:

```yaml
edge_type: supersedes
reason: "새 기준일의 리스크 평가가 이전 평가표를 대체함"
```

### contradicts

현재 문서와 이전 문서가 서로 다른 수치, 조건, 결론을 제시할 때 사용합니다. 실제 충돌인지 확실하지 않으면 `related`로 낮춥니다.

예:

```yaml
edge_type: contradicts
reason: "동일 지표에 대해 이전 문서와 다른 기준값을 제시함"
```

### references

현재 문서가 다른 문서를 명시적으로 참조할 때 사용합니다.

예:

```yaml
edge_type: references
reason: "본문에서 해당 분석 보고서를 근거 자료로 명시함"
```

### related

명확한 방향성은 없지만 함께 읽으면 좋은 문서일 때 사용합니다. 너무 많이 쓰면 graph 품질이 떨어지므로 마지막 선택지로 사용합니다.

## Link hint 작성 규칙

1. target 문서명을 확실히 모르면 `unknown`으로 둡니다.
2. reason은 "키워드가 비슷함"이 아니라 실제 연결 근거를 씁니다.
3. 동일 target에 여러 edge type이 가능하면 가장 강한 관계를 하나만 고릅니다.
4. `contradicts`와 `supersedes`는 근거가 명확할 때만 씁니다.
5. 사내 지식 에이전트에서 검색/연결에 필요한 내부 프로젝트명, 시스템명, 문서명, 약어는 원문 표기로 유지합니다.

## 좋은 reason 예시

- "동일 지표의 새 기준값을 제공하여 이전 기준표를 갱신함"
- "절차 실행을 위해 선행 승인 조건이 필요함"
- "실험 결과가 설계 가정의 주요 근거로 사용됨"
- "문서가 해당 정책명을 명시적으로 참조함"

## 나쁜 reason 예시

- "내용이 비슷함"
- "같은 단어가 나옴"
- "관련 있어 보임"
- "중요함"

## Markdown 안에서의 위치

frontmatter의 `related_candidates`와 본문의 `Link Hints For Graph`를 모두 채웁니다.

frontmatter는 기계가 읽기 쉽고, 본문 섹션은 사람이 검토하기 쉽습니다.
