"""Evidence-bound report tables and diagrams; no model HTML or executable code."""
import re
import unicodedata
from copy import deepcopy


def normalize_visuals(result):
    result=deepcopy(result)
    table=result['table']
    for kind,label in (('table','표'),('figure','그림')):
        result[kind]['title']=re.sub(r'^'+label+r'\s*(?:\d+\s*)?[.:]\s*','',result[kind]['title'])
    keep=[i for i,title in enumerate(table['columns']) if title.strip().casefold() not in
        {'근거','출처','인용','evidence','sources','source','references','reference','citations'}]
    if any(title.count('(')!=title.count(')') or title.count('（')!=title.count('）') for title in table['columns']):
        raise ValueError('research table heading has an unfinished parenthetical condition')
    if len(keep)!=len(table['columns']) and all(len(row['cells'])==len(table['columns']) for row in table['rows']):
        table['columns']=[table['columns'][i] for i in keep]
        for row in table['rows']: row['cells']=[row['cells'][i] for i in keep]
    result['figure']['caption']=re.sub(r'\((?:\s*E\d+[\s,;]*)+(?:참조|references?)?\)',
        '',result['figure']['caption']).strip()
    texts=[table['title'],*table['columns'],*[x for row in table['rows'] for x in row['cells']],
        result['figure']['title'],result['figure']['caption'],
        *[x for lane in result['figure']['lanes'] for x in [lane['label'],*lane['steps']]]]
    if any(re.search(r'\bE\d+\b',text) for text in texts):
        raise ValueError('research visual exposes evidence aliases')
    return result


def empty_visuals():
    return {'table':{'title':'','columns':[],'rows':[]},
        'figure':{'kind':'comparison','title':'','caption':'','lanes':[]}}


def diagram_caption(kind):
    return {'comparison':'각 열은 원문에서 확인한 구성 요소·속성을 묶어 비교합니다. 아래 항목에서 요소별 근거를 확인할 수 있습니다.',
        'sequence':'각 열의 연산을 위에서 아래로 읽습니다. 아래 항목에서 연산별 원문 근거를 확인할 수 있습니다.',
        'timeline':'각 열을 위에서 아래로 읽으면 원문에서 확인한 발표 연도의 순서입니다. 아래 항목에서 문헌별 근거를 확인할 수 있습니다.'}[kind]


def formula_schematic(evidence, aliases, question):
    """Narrow fallback for explicitly labeled, single-activation score formulas.

    Only literal aᵀ activation(W...) or activation(aᵀ[W...]) forms are used.
    The diagram adds no softmax, aggregation, theorem or performance claims.
    """
    if not re.search(r'연산.{0,6}순서|operator.{0,6}order|operation.{0,6}order|정적.{0,6}동적|static.{0,12}dynamic',question,re.I):
        return None
    labels=re.compile(r'\b([A-Za-z][A-Za-z0-9_-]*)\s*(?:\([^):]{0,100}\))?\s*[:：]\s*e\s*\([^)]{1,90}\)\s*=')
    lanes={}
    inverse={v:k for k,v in aliases.items()}
    for item in evidence:
        matches=list(labels.finditer(item['quote']))
        for i,match in enumerate(matches):
            formula=item['quote'][match.end():min(match.end()+180,matches[i+1].start() if i+1<len(matches) else len(item['quote']))]
            compact=re.sub(r'\s+','',formula).replace('·','').replace('⊤','ᵀ')
            a=r'a(?:ᵀ|\^\{?T\}?)'
            act=r'(LeakyReLU|sigmoid|tanh|ReLU)'
            projected=re.match(a+act+r'\(W',compact)
            activated=re.match(act+r'\('+a+r'[\[(]W',compact)
            if not (projected or activated): continue
            depth=0; stop=None
            for j in range(compact.index('('),len(compact)):
                if compact[j]=='(': depth+=1
                elif compact[j]==')':
                    depth-=1
                    if depth==0:
                        stop=j+1
                        break
            if stop is None or len(re.findall(act,compact[:stop]))!=1: continue
            activation=(projected or activated)[1]
            steps=['입력 변환 W',activation+' 활성화','점수 투영 aᵀ'] if projected else ['입력 변환 W','점수 투영 aᵀ',activation+' 활성화']
            qid=inverse[item['id']]
            lanes.setdefault(match[1],{'label':match[1],'steps':steps,'evidence_ids':[qid],
                'step_evidence_ids':[[qid] for _ in steps]})
    values=list(lanes.values())[:3]
    if len(values)<2 or len({tuple(x['steps']) for x in values})<2: return None
    if not all(re.search(r'(?<![A-Za-z0-9_])'+re.escape(x['label'])+r'(?![A-Za-z0-9_])',question,re.I) for x in values): return None
    return {'kind':'sequence','title':'원문 점수 수식의 연산 순서 비교',
        'caption':diagram_caption('sequence'),'lanes':values}


def scope_visual_issues(result, support):
    """A visual must not turn an explicitly unknown derivation into a fact."""
    missing=' '.join((support or {}).get('missing_aspects',[]))
    if not re.search(r'직접\s*(?:수정|개선|계승|파생)|관련\s*연구\s*분기|direct.{0,15}(?:revision|derivation)',missing,re.I):
        return []
    table=result['table']
    relation=[i for i,x in enumerate(table['columns']) if re.search(r'GAT.{0,12}(?:관련|직접|수정|후속)|직접.{0,10}(?:수정|계승|개선|파생)|direct.{0,15}(?:revision|derivation)',x,re.I)]
    if any(not re.search(r'미확인|근거\s*(?:없음|미확보|부족)|확인하지\s*못|unverified|not established',row['cells'][i],re.I)
           for row in table['rows'] for i in relation):
        return ['장의 근거 적합성 검사에서 미확인인 연구 관계를 표에서 단정했습니다. 해당 열을 제외하거나 미확인으로 표시하세요.']
    return []


def quote_visual_issues(result, evidence, aliases, *, literal_steps=True):
    """Bound generated labels and numeric expressions to their specific quotes."""
    def normalized(value):
        value=re.sub(r'-\s*\n\s*','',str(value))
        value=unicodedata.normalize('NFKC',value).replace('′',"'")
        value=re.sub(r'\^\{?(\d+)\}?',r'\1',value)
        return re.sub(r'\s+','',value).casefold()
    proofs={e['id']:e['quote'] for e in evidence}
    issues=[]
    def quotes(ids): return ' '.join(proofs.get(aliases.get(x),'') for x in ids)
    def values(text,proof):
        for value in re.findall(r'O\s*\([^\n)]{1,100}\)|\d+(?:\.\d+)?\s*(?:%|ms\b|GB\b)',text):
            if normalized(value) not in normalized(proof):
                issues.append('표·그림의 수치 또는 복잡도 표현이 해당 항목의 원문 인용에 없습니다: '+value)
    for row in result['table']['rows']:
        values(' '.join(row['cells']),quotes(row['evidence_ids']))
    figure=result['figure']
    if literal_steps and figure['lanes'] and figure.get('kind')=='sequence':
        issues.append('원문 설명의 속성을 임의의 실행 순서로 연결하지 마세요. 구성 요소는 비교 도식으로 표시하세요.')
    for lane in figure['lanes']:
        for step,ids in zip(lane['steps'],lane.get('step_evidence_ids',[])):
            proof=quotes(ids)
            values(step,proof)
            if literal_steps and not any(normalized(step) in normalized(proofs.get(aliases.get(x),'')) for x in ids):
                issues.append('그림 레이블은 해당 노드의 원문에 있는 짧은 구절을 그대로 사용해야 합니다. 불확실한 번역·새 수치·의미 변경을 넣지 마세요: '+step)
    return issues


def mechanism_visual(result):
    """Keep architecture boxes separate from ambiguous cost/limitation snippets.

    Quantitative comparison needs a table's explicit method and condition cells.
    Removing a cost suffix never adds text to the original quoted phrase.
    """
    result=deepcopy(result)
    figure=result['figure']
    if figure.get('kind')!='comparison': return result
    for lane in figure['lanes']:
        steps=[]; proofs=[]
        for step,ids in zip(lane['steps'],lane.get('step_evidence_ids',[])):
            step=re.split(r'\s+with\s+(?:linear|quadratic|constant)\s+complexity\b',step,flags=re.I)[0]
            if re.search(r'complexity|복잡도|O\s*\(|\d+(?:\.\d+)?\s*%',step,re.I): continue
            steps.append(step); proofs.append(ids)
        lane['steps']=steps; lane['step_evidence_ids']=proofs
    return result


def quoted_mechanisms(result, evidence, aliases):
    """Replace paraphrased box labels with short, literal source phrases.

    Only consecutive matching words are selected (articles may be restored,
    singular/plural may match). No translation or new assertion is generated.
    Unmatched labels are omitted rather than displayed as grounded facts.
    """
    result=mechanism_visual(result)
    figure=result['figure']
    if figure.get('kind')!='comparison': return result
    proofs={e['id']:e['quote'] for e in evidence}
    weak={'a','an','the','we','and','or','to','of','for','in','as','from','with','that','this','these'}
    def words(value):
        text=re.sub(r'\s+',' ',re.sub(r'-\s*\n\s*','',str(value)))
        terms=[]
        for found in re.finditer(r'\S+',text):
            term=unicodedata.normalize('NFKC',found[0]).strip('.,;:()[]').casefold()
            if term in {'a','an','the'}: continue
            if len(term)>5 and term.endswith('s') and not term.endswith('ss'): term=term[:-1]
            terms.append((term,found.start(),found.end()))
        return text,terms
    def phrase(label,quote):
        text,original=words(quote); _,wanted=words(label)
        best=''; score=0
        for i,word in enumerate(wanted):
            for j,source in enumerate(original):
                count=0
                while count<4 and i+count<len(wanted) and j+count<len(original) and wanted[i+count][0]==original[j+count][0]:
                    count+=1
                    if sum(x[0] not in weak for x in original[j:j+count])<2 or original[j+count-1][0] in weak: continue
                    span=text[original[j][1]:original[j+count-1][2]].strip('.,;:()[]')
                    if len(span)<=60 and count>score and not re.search(r'[.;!?]',span):
                        best=span; score=count
        return best
    for lane in figure['lanes']:
        labels=[]; ids=[]
        for step,qids in zip(lane['steps'],lane.get('step_evidence_ids',[])):
            options=[phrase(step,proofs.get(aliases.get(x),'')) for x in qids]
            selected=max(options,key=len,default='')
            if selected and selected not in labels:
                labels.append(selected); ids.append(qids)
        lane['steps']=labels; lane['step_evidence_ids']=ids
    figure['title']='원문에서 확인한 구성 요소 비교'
    return result


def operator_order_issues(result, evidence, aliases):
    """Compare labeled activation/projection formulas with actual diagram arrows.

    This covers explicit single-activation attention scores, not arbitrary proofs.
    Unknown expressions still require the evidence reviewer.
    """
    proofs={e['id']:e['quote'] for e in evidence}
    def expected(method, ids):
        for qid in ids:
            quote=proofs.get(aliases.get(qid),'')
            found=re.search(re.escape(method)+r'(?![\w])\s*(?:\([^)]*\))?\s*[:：]',quote,re.I)
            if not found: continue
            formula=quote[found.end():found.end()+230]
            act=re.search(r'LeakyReLU|sigmoid|tanh|softmax|\bReLU',formula,re.I)
            projection=re.search(r'\ba\s*(?:⊤|ᵀ|\^\s*\{?T|\^\s*\\top)',formula)
            if act and projection:
                linear=re.search(r'\bW',formula)
                return {'projection':'projection_first' if act.start()<projection.start() else 'activation_first',
                    'input':'linear_first' if linear and linear.start()>act.start() else 'activation_first' if linear else None}
        return None
    issues=[]
    figure=result['figure']
    nodes=' '.join(x for lane in figure['lanes'] for x in lane['steps'])
    # A caption must describe the drawing that actually exists, not an imagined
    # complete pipeline. This caught an actual softmax/output omission.
    for pattern in (r'softmax|소프트맥스',r'LeakyReLU',r'sigmoid',r'tanh'):
        if re.search(pattern,figure['caption'],re.I) and not re.search(pattern,nodes,re.I):
            issues.append('그림 설명에 언급한 연산이 도식에 없습니다: '+pattern)
    for lane in result['figure']['lanes']:
        if figure.get('kind','sequence')=='sequence':
            for left,right in zip(lane['steps'],lane['steps'][1:]):
                if all(re.match(r'(?:노드\s*[ij]의\s*)?입력',x) for x in (left,right)):
                    issues.append('서로 다른 입력을 순차 연산처럼 화살표로 연결했습니다. 병렬 입력은 비교 도식으로 표시하세요.')
        method=re.match(r'[A-Za-z][A-Za-z0-9_-]*',lane['label'])
        order=expected(method[0],lane['evidence_ids']) if method else None
        activations=[i for i,text in enumerate(lane['steps']) if re.search(r'LeakyReLU|sigmoid|tanh|softmax|\bReLU',text,re.I)]
        projections=[i for i,text in enumerate(lane['steps']) if re.search(r'\ba\s*(?:⊤|ᵀ|\^\s*\{?T)',text,re.I)]
        ordered=figure.get('kind','sequence')=='sequence' or bool(re.search(r'연산.{0,8}순서|계산.{0,8}순서|operation.{0,8}order',figure['title']+' '+figure['caption'],re.I))
        linear=[i for i,text in enumerate(lane['steps']) if re.search(r'\bW\b',text)]
        if ordered and order:
            if not activations or not projections or (order['input'] and not linear):
                issues.append(lane['label']+' 연산 순서 도식은 원문에 있는 입력 변환 W, 활성화 함수 이름, 점수 투영 aᵀ를 각각 명시해야 합니다. 모호한 선형 변환·스코어 계산으로 대체하지 마세요.')
            if re.search(r'선형|linear',figure['caption'],re.I) and not (re.search(r'\bW\b',figure['caption']) and re.search(r'\ba\s*(?:⊤|ᵀ|\^\s*\{?T)',figure['caption'])):
                issues.append('그림 설명에서 입력 변환 W와 점수 투영 aᵀ를 구분하지 않은 선형 변환 전후 표현을 사용했습니다.')
        if ordered and order and order['input'] and len(activations)==len(linear)==1:
            actual='linear_first' if linear[0]<activations[0] else 'activation_first'
            if order['input']!=actual:
                issues.append(lane['label']+' 입력 선형 변환 W의 위치가 원문 수식과 다릅니다. 입력 변환 W와 점수 투영 aᵀ를 구분하세요.')
        if ordered and order and len(activations)==len(projections)==1:
            actual='projection_first' if projections[0]<activations[0] else 'activation_first' if activations[0]<projections[0] else 'combined'
            if order['projection']!=actual:
                issues.append(lane['label']+' 도식의 내적과 활성화 순서가 원문 수식과 다릅니다. '+
                    ('내적 다음에 활성화가 와야 합니다.' if order['projection']=='projection_first' else '활성화 다음에 내적이 와야 합니다.'))
    table=result['table']
    for row in table['rows']:
        claim=' '.join(row['cells'])
        if re.search(r'보편|universal approximator|모든\s*함수|all functions',claim,re.I) and not re.search(r'점수\s*함수|연속\s*함수|scor\w* function|function approximation|compact.{0,15}domain|콤팩트',claim,re.I):
            issues.append('표의 보편 근사성 주장은 대상 함수와 적용 조건을 명시해야 합니다. 모델 전체의 보편성으로 표현하지 마세요.')
        quoted=' '.join(proofs.get(aliases.get(x),'') for x in row['evidence_ids'])
        if re.search(r'O\s*\(\s*N\s*\+\s*E\s*\)',claim) and re.search(r'O\s*\(\s*N\s*(?:2|²|\^2)\s*\)',quoted):
            if not re.search(r'선형\s*(?:어텐션|attention)|linear\s*attention|Performer|초록|abstract|구성\s*조건',claim,re.I):
                issues.append('표의 복잡도 근거에는 이차 비용과 선형 비용이 함께 있습니다. 선형 비용을 얻는 구성 조건을 명시하거나 초록의 주장으로 한정하세요. 조건을 확인하지 못했으면 해당 수치를 제외하세요.')
        if not row['cells'] or not re.search(r'LeakyReLU|비선형|활성화',row['cells'][0],re.I): continue
        for title,text in zip(table['columns'][1:],row['cells'][1:]):
            method=re.match(r'[A-Za-z][A-Za-z0-9_-]*',title)
            order=expected(method[0],row['evidence_ids']) if method else None
            if re.search(r'가중치.{0,8}계산.{0,5}(?:전|후)',text):
                issues.append('표에서 학습 파라미터 aᵀ의 점수 투영과 어텐션 계수 계산을 혼동했습니다. 어떤 연산 전후인지 명확히 표시하세요.')
            if order and order['projection']=='projection_first' and re.search(r'내적.{0,12}전|before.{0,20}(?:dot|projection)',text,re.I):
                issues.append(title+' 표에서 활성화를 내적 전으로 표시했지만 원문 수식은 내적 후입니다.')
            elif order and order['projection']=='activation_first' and re.search(r'내적.{0,12}후|after.{0,20}(?:dot|projection)',text,re.I):
                issues.append(title+' 표에서 활성화를 내적 후로 표시했지만 원문 수식은 내적 전입니다.')
    return issues


def cell(value):
    return str(value).replace('\n',' ').replace('|',r'\|').strip()


def tex_text(value):
    value=re.sub(r'\b([hae])_([ij])',lambda m:m[1]+{'i':'ᵢ','j':'ⱼ'}[m[2]],str(value))
    value=value.replace('a^T','aᵀ').replace('||','∥')
    escapes={'\\':r'\textbackslash{}','{':r'\{','}':r'\}','%':r'\%','&':r'\&',
        '$':r'\$','#':r'\#','_':r'\_','^':r'\textasciicircum{}','~':r'\textasciitilde{}'}
    return ''.join(escapes.get(c,c) for c in str(value).replace('\n',' '))


def boxed_label(value):
    text=tex_text(value)
    # Wrap the original text before escaping, keeping long primary-source
    # phrases readable inside two/three-column diagrams.
    width=lambda s:sum(2 if unicodedata.east_asian_width(c) in {'W','F'} else 1 for c in s)
    lines=[]; current=''
    for word in str(value).split():
        pieces=[]; piece=''
        for char in word:
            if piece and width(piece+char)>26:
                pieces.append(piece); piece=''
            piece+=char
        if piece: pieces.append(piece)
        for part in pieces:
            if current and width(current+' '+part)>26:
                lines.append(current); current=''
            current=(current+' '+part).strip()
    if current: lines.append(current)
    if len(lines)<2: return r'\boxed{\text{'+text+'}}'
    return r'\boxed{\begin{array}{c}'+r' \\ '.join(r'\text{'+tex_text(x)+'}' for x in lines)+r'\end{array}}'


def diagram(lanes, kind='sequence'):
    """A schematic uses the existing offline math renderer in chat and pages."""
    if kind not in {'sequence','comparison','timeline'} or not 1 <= len(lanes) <= 3 or any(not 2 <= len(lane['steps']) <= 7 for lane in lanes):
        raise ValueError('invalid research diagram')
    if kind=='timeline':
        for lane in lanes:
            years=[re.match(r'\s*(?:19|20)\d{2}\b',x) for x in lane['steps']]
            if not all(years) or [int(x[0]) for x in years]!=sorted(int(x[0]) for x in years):
                raise ValueError('invalid research timeline chronology')
    rows=[' & '.join(r'\textbf{'+tex_text(lane['label'])+'}' for lane in lanes)]
    for index in range(max(len(lane['steps']) for lane in lanes)):
        if index and kind!='comparison':
            rows.append(' & '.join(r'\downarrow' if index < len(lane['steps']) else '' for lane in lanes))
        rows.append(' & '.join(boxed_label(lane['steps'][index])
            if index < len(lane['steps']) else '' for lane in lanes))
    separator=(r' \\[4pt]' if kind=='comparison' else r' \\')+'\n'
    return '$$\n'+r'\begin{array}{'+'c'*len(lanes)+'}\n'+separator.join(rows)+ '\n'+r'\end{array}'+'\n$$'


def render_visuals(result, aliases):
    def cite(values):
        if not values or not set(values).issubset(aliases):
            raise ValueError('unverified research visual citation')
        return ' '.join('['+aliases[value]+']' for value in dict.fromkeys(values))
    output=[]
    table=result['table']
    if table['rows']:
        columns=table['columns']
        if not 2 <= len(columns) <= 4 or any(len(row['cells'])!=len(columns) for row in table['rows']):
            raise ValueError('invalid research visual table')
        output.append('### 표. '+cell(table['title']))
        def table_cell(value):
            value=str(value)
            if (re.fullmatch(r'e\([^\n]+\)\s*=\s*[^\n]+',value) or
                    re.fullmatch(r'(?:a\^T\s*)?LeakyReLU\([^\n]+\)',value)) and 'LeakyReLU' in value and '$' not in value:
                value=re.sub(r'\bh([ij])\b',r'h_\1',value)
                value='$'+value.replace('||',r'\parallel ').replace('LeakyReLU',r'\operatorname{LeakyReLU}').replace('·',r'\cdot ').replace('a^T',r'a^{\top}')+'$'
            return cell(value)
        output.append('| '+' | '.join(cell(x) for x in columns)+' | 근거 |\n| '+
            ' | '.join('---' for _ in range(len(columns)+1))+' |\n'+
            '\n'.join('| '+' | '.join(table_cell(x) for x in row['cells'])+' | '+cite(row['evidence_ids'])+' |'
                for row in table['rows']))
    figure=result['figure']
    if figure['lanes']:
        output.append('### 그림. '+cell(figure['title']))
        output.append(diagram(figure['lanes'],figure.get('kind','sequence')))
        output.append(cell(figure['caption']))
        for lane in figure['lanes']:
            cite(lane['evidence_ids'])
            step_ids=lane.get('step_evidence_ids')
            if step_ids is not None:
                if len(step_ids)!=len(lane['steps']):
                    raise ValueError('invalid research visual step citations')
                if not set(x for ids in step_ids for x in ids).issubset(lane['evidence_ids']):
                    raise ValueError('step citations outside lane evidence')
                output.extend('- '+cell(step)+': '+cite(ids) for step,ids in zip(lane['steps'],step_ids))
            else:
                output.append('- '+cell(lane['label'])+': '+cite(lane['evidence_ids']))
    # No hidden references may be introduced inside labels or captions.
    text='\n\n'.join(output)
    if any(marker not in aliases.values() for marker in re.findall(r'\[(E[^\]]+)\]',text)):
        raise ValueError('unverified research visual citation')
    return text


def source_table(sources):
    if not sources:
        return ''
    lines=['### 표. 수집 자료의 확인 범위','| 자료 | 원문 확보 상태 | 분석한 구간 / 전체 구간 |', '| --- | --- | --- |']
    statuses={'document_text':'원문 텍스트','partial_text':'원문 일부','abstract_text':'초록',
        'search_excerpt':'검색 발췌문','page_excerpt':'페이지 발췌문','internal_excerpt':'내부 발췌문'}
    for source in sources[:12]:
        lines.append('| '+cell(source['title'])+' | '+statuses.get(source.get('content_status'),'확인된 텍스트')+
            ' | '+str(len(source.get('analyzed',[])))+' / '+str(len(source.get('chunks',[])))+' |')
    return '\n'.join(lines)+'\n\n구간 수는 PDF 쪽 수가 아닙니다. 그림·표의 시각적 구조를 분석한 수치도 아닙니다.'
