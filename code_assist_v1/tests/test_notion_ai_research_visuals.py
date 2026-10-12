import pytest
from code_assist_v1.notion_ai_research_visuals import diagram, render_visuals, normalize_visuals, operator_order_issues, scope_visual_issues, formula_schematic, quote_visual_issues, mechanism_visual, quoted_mechanisms
from code_assist_v1.notion_ai_report import report_blocks


def visual():
    return {'table':{'title':'비교 조건','columns':['방법','설정'],
        'rows':[{'cells':['원래 방법','동일한 조건 | A'],'evidence_ids':['E1']},
                {'cells':['개선 방법','동일한 조건 A'],'evidence_ids':['E2']}]},
        'figure':{'title':'연산 순서','caption':'각 방법의 실제 연산 순서입니다.',
            'lanes':[{'label':'원래 방법','steps':['입력 변환','활성화'],'evidence_ids':['E1']},
                     {'label':'개선 방법','steps':['활성화','출력 변환'],'evidence_ids':['E2']}]}}


def test_visuals_preserve_tables_and_schematics_in_native_page_blocks():
    text=render_visuals(visual(),{'E1':'E0000000001','E2':'E0000000002'})
    blocks=report_blocks(text)
    table=next(b for b in blocks if b['type']=='simple_table')
    assert table['props']['rows'][1]==['원래 방법','동일한 조건 | A','[E0000000001]']
    figure=next(b for b in blocks if b['type']=='equation')
    assert r'\boxed{\text{입력 변환}}' in figure['content'] and r'\downarrow' in figure['content']
    assert r'\begin{array}{cc}' in figure['content']


def test_visuals_reject_unknown_proofs_and_inconsistent_columns():
    item=visual(); item['table']['rows'][0]['evidence_ids']=['E99']
    with pytest.raises(ValueError,match='citation'): render_visuals(item,{'E1':'E0000000001','E2':'E0000000002'})
    item=visual(); item['table']['rows'][0]['cells'].append('fabricated column')
    with pytest.raises(ValueError,match='table'): render_visuals(item,{'E1':'E0000000001','E2':'E0000000002'})


def test_diagram_labels_cannot_inject_tex_commands_or_break_array_structure():
    text=diagram([{'label':r'\href{https://evil}{x}', 'steps':['A & B',r'x}\end{array}']}])
    assert r'\href{' not in text and r'\textbackslash{}href\{' in text
    assert r'A \& B' in text and text.count(r'\end{array}')==1


def test_duplicate_proof_column_and_caption_aliases_are_normalized():
    item=visual(); item['table']['columns'].append('근거')
    for row in item['table']['rows']: row['cells'].append('E1, E2')
    item['figure']['caption']+=' (E1, E2 참조)'
    cleaned=normalize_visuals(item)
    assert len(cleaned['table']['columns'])==2 and 'E1' not in cleaned['figure']['caption']
    item['table']['rows'][0]['cells'][0]='E1 proves everything'
    with pytest.raises(ValueError,match='aliases'): normalize_visuals(item)


def test_attention_diagram_reversal_is_rejected_even_if_model_critic_accepts_it():
    quote='GAT (Original): e(hi,hj) = LeakyReLU(a⊤ [W hi || W hj]). GATv2 (Revised): e(hi,hj) = a⊤ LeakyReLU(W [hi || hj]).'
    item=visual(); item['figure']['lanes']=[{'label':'GAT','steps':['선형 변환 W','LeakyReLU 활성화','내적 a^T'], 'evidence_ids':['E1']},
        {'label':'GATv2','steps':['선형 변환 W','내적 a^T','LeakyReLU 활성화'], 'evidence_ids':['E1']}]
    evidence=[{'id':'E0000000001','quote':quote}]; aliases={'E1':'E0000000001'}
    assert len(operator_order_issues(item,evidence,aliases))==2
    item['figure']['lanes'][0]['steps']=['선형 변환 W','내적 a^T','LeakyReLU 활성화']
    item['figure']['lanes'][1]['steps']=['선형 변환 W','LeakyReLU 활성화','내적 a^T']
    assert operator_order_issues(item,evidence,aliases)==[]
    item['figure']['lanes'][0]['steps']=['선형 변환 W','LeakyReLU 활성화','스코어 계산 a^T']
    assert operator_order_issues(item,evidence,aliases)


def test_table_formulas_render_as_math_and_diagram_labels_use_readable_symbols():
    item=visual(); item['table']['rows'][0]['cells'][0]='e(h_i,h_j) = LeakyReLU(a^T [W h_i || W h_j])'
    item['figure']['lanes'][0]['steps']=['입력 h_i, h_j','내적 a^T']
    text=render_visuals(item,{'E1':'E0000000001','E2':'E0000000002'})
    assert r'$e(h_i,h_j)' in text and r'\operatorname{LeakyReLU}' in text and r'\parallel' in text
    assert 'hᵢ, hⱼ' in text and 'aᵀ' in text
    item['table']['rows'][0]['cells'][0]='a^T LeakyReLU(W [hi || hj])'
    text=render_visuals(item,{'E1':'E0000000001','E2':'E0000000002'})
    assert r'$a^{\top} \operatorname{LeakyReLU}(W [h_i \parallel  h_j])$' in text


def test_independent_comparison_attributes_do_not_gain_arrows():
    item=visual(); item['figure']['kind']='comparison'
    for lane in item['figure']['lanes']:
        lane['evidence_ids']=['E1','E2']
        lane['step_evidence_ids']=[['E1'],['E2']]
    text=render_visuals(item,{'E1':'E0000000001','E2':'E0000000002'})
    assert r'\downarrow' not in text and '입력 변환: [E0000000001]' in text
    item['figure']['lanes'][0]['step_evidence_ids']=[['E1']]
    with pytest.raises(ValueError,match='step citations'):
        render_visuals(item,{'E1':'E0000000001','E2':'E0000000002'})


def test_comparison_cannot_disguise_reversed_input_transform_or_unqualified_universality():
    item=visual(); item['figure']['kind']='comparison'
    item['figure']['title']='연산 순서 비교'
    item['figure']['lanes']=[{'label':'GATv2','steps':['LeakyReLU 활성화','선형 변환 (W)'], 'evidence_ids':['E1']}]
    evidence=[{'id':'E0000000001','quote':'GATv2 (Revised): e(hi,hj) = a⊤ LeakyReLU(W [hi || hj]).'}]
    assert any('입력 선형 변환' in x for x in operator_order_issues(item,evidence,{'E1':'E0000000001'}))
    item['figure']['lanes'][0]['steps'].reverse()
    item['figure']['lanes'][0]['steps'].append('점수 투영 a^T')
    assert operator_order_issues(item,evidence,{'E1':'E0000000001'})==[]
    item['table']['rows'][0]['cells']=['표현력','보편적 근사기 (Universal Approximator)']
    assert any('보편' in x for x in operator_order_issues(item,evidence,{'E1':'E0000000001'}))
    item['table']['rows'][0]['cells']=['점수 함수의 표현력','보편 근사성(원문의 조건에서)']
    assert operator_order_issues(item,evidence,{'E1':'E0000000001'})==[]


def test_timeline_requires_ordered_years_and_does_not_invent_missing_dates():
    lanes=[{'label':'문헌 발표 순서','steps':['2018 원래 방법','2022 개선 방법','2025 관련 확장']}]
    assert r'\downarrow' in diagram(lanes,'timeline')
    lanes[0]['steps']=['2025 관련 확장','2018 원래 방법']
    with pytest.raises(ValueError,match='chronology'): diagram(lanes,'timeline')
    lanes[0]['steps']=['기초 방법','최근 개선']
    with pytest.raises(ValueError,match='chronology'): diagram(lanes,'timeline')


def test_caption_cannot_claim_unpictured_steps_or_link_parallel_inputs():
    item=visual(); item['figure']['caption']='Softmax 정규화를 포함한 전체 과정'
    assert operator_order_issues(item,[],{})
    item['figure']['caption']='두 입력과 활성화'
    item['figure']['lanes'][0]['steps']=['노드 i의 입력 특징','노드 j의 입력 특징']
    assert operator_order_issues(item,[],{})
    item['figure']['kind']='comparison'
    assert operator_order_issues(item,[],{})==[]


def test_actual_ambiguous_operation_diagram_cannot_hide_w_and_score_projection():
    item=visual(); item['figure']['kind']='comparison'
    item['figure']['title']='GAT와 GATv2 연산 순서 비교'
    item['figure']['caption']='GATv2는 비선형 활성화 후 선형 변환을 수행한다.'
    item['figure']['lanes']=[{'label':'GATv2','steps':['입력 노드','concatenation 및 비선형 활성화','선형 변환 및 스코어 계산'],'evidence_ids':['E1']}]
    evidence=[{'id':'E0000000001','quote':'GATv2 (Revised): e(hi,hj) = a⊤ LeakyReLU(W [hi || hj]).'}]
    issues=operator_order_issues(item,evidence,{'E1':'E0000000001'})
    assert any('각각 명시' in x for x in issues) and any('선형 변환 전후' in x for x in issues)
    item['figure']['caption']='입력 변환 W와 점수 투영 aᵀ를 구분한 순서 비교'
    item['figure']['lanes'][0]['steps']=['입력 변환 W','LeakyReLU 활성화','점수 투영 aᵀ']
    assert operator_order_issues(item,evidence,{'E1':'E0000000001'})==[]


def test_unknown_derivation_cannot_become_a_negative_table_claim():
    item=visual(); item['table']={'title':'관련성','columns':['방법','GAT 직접 수정 여부'],
        'rows':[{'cells':['GraphGPS','아니오 (독립 프레임워크)'],'evidence_ids':['E1']}]}
    support={'missing_aspects':['GAT 직접 수정 여부','관련 연구 분기 여부']}
    assert scope_visual_issues(item,support)
    item['table']['rows'][0]['cells'][1]='현재 근거로 미확인'
    assert scope_visual_issues(item,support)==[]


def test_approximation_domain_and_conditional_complexity_cannot_be_dropped():
    item=visual(); aliases={'E1':'E0000000001','E2':'E0000000002'}
    item['table']['rows'][0]['cells']=['GAT','모든 함수를 근사 불가']
    assert any('대상 함수' in x for x in operator_order_issues(item,[],aliases))
    item['table']['rows'][0]['cells']=['GraphGPS','O(N + E)']
    evidence=[{'id':'E0000000001','quote':'Prior models cost O(N 2). The scalable architecture costs O(N + E).'}]
    assert any('구성 조건' in x for x in operator_order_issues(item,evidence,aliases))
    item['table']['rows'][0]['cells'][1]='초록에서 주장한 확장 가능한 구성: O(N + E), 구현 조건 미확인'
    assert operator_order_issues(item,evidence,aliases)==[]


def test_formula_fallback_uses_only_explicit_original_operator_order_and_proofs():
    quote='GAT (Original): e(hi,hj) = LeakyReLU(a⊤ [W hi || W hj]). GATv2 (Revised): e(hi,hj) = a⊤ LeakyReLU(W [hi || hj]).'
    evidence=[{'id':'E0000000001','quote':quote}]; aliases={'E1':'E0000000001'}
    result=formula_schematic(evidence,aliases,'GAT와 GATv2의 점수 수식 연산 순서')
    assert result['lanes'][0]['steps']==['입력 변환 W','점수 투영 aᵀ','LeakyReLU 활성화']
    assert result['lanes'][1]['steps']==['입력 변환 W','LeakyReLU 활성화','점수 투영 aᵀ']
    assert all(x==['E1'] for lane in result['lanes'] for x in lane['step_evidence_ids'])
    item=visual(); item['figure']=result
    assert operator_order_issues(item,evidence,aliases)==[]
    assert formula_schematic(evidence,aliases,'2026년의 확장성 실측 결과') is None
    assert formula_schematic(evidence,aliases,'GAT와 GATv2의 시간 복잡도 수식') is None
    assert formula_schematic(evidence,aliases,'GraphGPS와 Primphormer 연산 순서 비교') is None
    evidence[0]['quote']=quote.replace('LeakyReLU(W [hi || hj])','LeakyReLU(W sigmoid([hi || hj]))')
    assert formula_schematic(evidence,aliases,'GAT와 GATv2 연산 순서') is None


def test_visual_terms_and_complexity_must_exist_in_each_nodes_own_original_quote():
    item=visual(); item['table']['rows']=[]
    item['figure']['kind']='comparison'
    item['figure']['lanes']=[{'label':'Primphormer','steps':['쌍대-쌍원 프레임','O(N+E) 선형'],
        'evidence_ids':['E1'],'step_evidence_ids':[['E1'],['E1']]}]
    evidence=[{'id':'E0000000001','quote':'We propose a primal-dual framework with linear complexity.'},
        {'id':'E0000000002','quote':'A different method costs O(N+E).'}]
    aliases={'E1':'E0000000001','E2':'E0000000002'}
    issues=quote_visual_issues(item,evidence,aliases)
    assert any('복잡도' in x for x in issues) and any('불확실한 번역' in x for x in issues)
    item['figure']['lanes'][0]['steps']=['primal-dual framework','linear complexity']
    assert quote_visual_issues(item,evidence,aliases)==[]
    item['figure']['kind']='sequence'
    assert any('실행 순서' in x for x in quote_visual_issues(item,evidence,aliases))


def test_numeric_cells_cannot_invent_missing_values_and_preserve_pdf_prime_spacing():
    item=visual(); item['figure']['lanes']=[]
    item['table']['rows']=[{'cells':['방법','O(|V|dd\' + |E|d\')'], 'evidence_ids':['E1']}]
    evidence=[{'id':'E0000000001','quote':'A single head costs O (|V|dd′ + |E|d′).'}]
    aliases={'E1':'E0000000001'}
    assert quote_visual_issues(item,evidence,aliases)==[]
    item['table']['rows'][0]['cells'][1]='92.8%'
    assert quote_visual_issues(item,evidence,aliases)


def test_architecture_diagrams_keep_mechanisms_without_ambiguous_baseline_cost_boxes():
    item=visual(); item['figure']['kind']='comparison'
    item['figure']['lanes']=[{'label':'Framework','steps':['O(N^2) for prior models',
        'primal-dual framework','primal representation with linear complexity'],
        'evidence_ids':['E1'],'step_evidence_ids':[['E1'],['E1'],['E1']]}]
    filtered=mechanism_visual(item)
    assert filtered['figure']['lanes'][0]['steps']==['primal-dual framework','primal representation']
    assert filtered['figure']['lanes'][0]['step_evidence_ids']==[['E1'],['E1']]
    assert 'O(N^2)' in item['figure']['lanes'][0]['steps'][0]


def test_long_original_phrases_wrap_without_injecting_tex_or_losing_diagram_columns():
    text=diagram([{'label':'Method','steps':['local message passing and global attention',
        'computationally efficient attention mechanisms']}],'comparison')
    assert r'\boxed{\begin{array}{c}' in text and r'\text{local message passing and}' in text
    assert r'\text{global attention}' in text


def test_paraphrased_mechanisms_become_original_anchors_without_invented_relations():
    item=visual(); item['table']['rows']=[]; item['figure']['kind']='comparison'
    item['figure']['lanes']=[{'label':'Framework','steps':['primal-dual framework interpreting self-attention as dual',
        'linear attentions like Performer and BigBird integrated','unfounded imaginary mechanism'],
        'evidence_ids':['E1'],'step_evidence_ids':[['E1'],['E1'],['E1']]}]
    evidence=[{'id':'E0000000001','quote':'We propose a primal-dual framework that interprets self-attention on graphs. It supports linear attention such as Performer, and sparse mechanisms such as BigBird.'}]
    aliases={'E1':'E0000000001'}
    result=quoted_mechanisms(item,evidence,aliases)
    assert result['figure']['lanes'][0]['steps']==['primal-dual framework','linear attention']
    assert quote_visual_issues(result,evidence,aliases)==[]
    assert 'BigBird' not in result['figure']['lanes'][0]['steps'][1]


def test_table_heading_cannot_show_a_truncated_configuration_clause():
    item=visual(); item['table']['columns'][1]='Time Complexity (1 Head, d input dim, |V| num'
    with pytest.raises(ValueError,match='unfinished'): normalize_visuals(item)
    item['table']['columns'][1]='Time Complexity (1 Head)'
    assert normalize_visuals(item)['table']['columns'][1]=='Time Complexity (1 Head)'
