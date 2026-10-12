import json
from code_assist_v1.notion_ai_math import repair_math
from code_assist_v1.notion_ai_chat import normalize_result


def test_real_json_control_escapes_recover_only_inside_math():
    text = '$\beta_{ij} = \frac{x}{y}, \tau, \nabla x, x\right)$'
    # These Python strings reproduce control characters produced by JSON's
    # single-backslash escapes, rather than already-correct TeX commands.
    expected = r'$\beta_{ij} = \frac{x}{y}, \tau, \nabla x, x\right)$'
    assert repair_math(text) == expected
    assert normalize_result({"answer": text, "citations": [], "artifact": None}, [], "chat")["answer"] == expected
    assert json.loads(json.dumps(repair_math(text))) == expected


def test_code_prose_and_valid_multiline_math_are_preserved():
    value = '본문\ttext\nnew line\n`$\beta$`\n```\n$\frac{x}{y}$\n```\n$$x +\n y$$'
    assert repair_math(value) == value
    valid = r'변환 $\beta_{ij}$와 $\frac{x}{y}$'
    assert repair_math(valid) == valid
