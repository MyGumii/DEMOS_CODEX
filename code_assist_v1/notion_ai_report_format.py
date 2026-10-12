"""Consistent display of deep-report coverage; stored answers remain unchanged."""
from copy import deepcopy
import re


def coverage_reason(reason, status):
    reason=str(reason or '').strip()
    if (not re.search(r'[가-힣]',reason)
            or re.search(r'\b(?:coverage|partial|missing|sufficient|source_count)\b',reason,re.I)
            or '_coverage_' in reason
            or (len(reason)>140 and not re.search(r'[.!?。！？]$|(?:다|요|함|음|임)$',reason))):
        return {'sufficient':'계획한 조사 항목의 근거 검토를 완료했습니다.',
            'partial':'확보한 원문은 있으나 일부 설명 또는 요청 범위의 확인을 완료하지 못했습니다.',
            'missing':'이 조사 항목을 뒷받침하는 근거를 충분히 확인하지 못했습니다.'}.get(status,'추가 확인이 필요합니다.')
    return reason


def present_report(message):
    research=message.get('context_info',{}).get('research',{})
    if research.get('depth')!='deep':
        return message
    result=deepcopy(message)
    coverage=result['context_info']['research'].get('coverage',[])
    text=result.get('text','')
    prefix,separator,tail=text.rpartition('\n\n## 조사 범위 점검\n\n')
    scope,limits_separator,limits=tail.partition('\n\n## 확인 범위와 한계')
    for row in coverage:
        original=row.get('reason','')
        cleaned=coverage_reason(original,row.get('status'))
        if separator and original!=cleaned:
            scope=scope.replace(' — '+original,' — '+cleaned,1)
        row['reason']=cleaned
    if separator:
        result['text']=prefix+separator+scope+limits_separator+limits
    return result
