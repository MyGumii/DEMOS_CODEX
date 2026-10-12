"""Bounded first-party page templates; no client-supplied SQL or block payload."""
from .canonical_authority import _clean_blocks, _stable_id

TEMPLATES = ("page", "database", "linked_database", "task_tracker", "meeting_notes", "ai_notes")


def validate(value):
    if not isinstance(value, str) or value not in TEMPLATES:
        raise ValueError("supported page template required")
    return value


def blocks(document_id, title, template, *, linked_source=None):
    validate(template)
    initial = _stable_id("block", document_id, "initial")
    if template == "page":
        return _clean_blocks([{"id":initial,"type":"paragraph","content":"","props":{}}])
    if template == "ai_notes":
        return _clean_blocks([{"id":initial,"type":"ai_notes","content":"","props":{}}])
    if template == "linked_database":
        if (not isinstance(linked_source, dict)
                or set(linked_source) not in ({"document_id", "database_id"}, {"database_id"})):
            raise ValueError("linked database source required")
        view_id = _stable_id("view", document_id, "linked_table")
        return _clean_blocks([{"id":initial,"type":"table","content":"","props":{
            "linked_database":linked_source,
            "views":[{"id":view_id,"name":"표","type":"table","config":{}}],
            "active_view_id":view_id}}])
    if template in {"database", "task_tracker"}:
        return _clean_blocks([{"id":initial,"type":"table","content":title,"props":{
            "database_id":initial,"active_view_id":_stable_id("view",document_id,"table"),"view":"table"}}])
    return _clean_blocks([{"id":_stable_id("block",document_id,"meeting",str(index)),
                          "type":kind,"content":content,"props":{"checked":False} if kind=="todo" else {}}
                         for index,(kind,content) in enumerate([
                             ("heading2","회의 안건"),("bullet",""),("heading2","논의 내용"),
                             ("paragraph",""),("heading2","결정 사항"),("bullet",""),
                             ("heading2","다음 할 일"),("todo","")])])


def install_database(authority, con, document_id, title, template, now, actor):
    """Called only inside the owning page creation transaction, before checkpoint."""
    if template not in {"database", "task_tracker"}:
        return False
    database_id = _stable_id("block",document_id,"initial")
    def schema(kind, target, payload):
        return authority._apply_schema_domain(con, document_id=document_id,database_id=database_id,
            command_kind=kind,target_id=target,payload=payload,expected_target_version=0,created_at=now,actor=actor)
    schema("database.create",database_id,{"title":title})
    properties=[("title","작업 이름" if template=="task_tracker" else "이름","title",{})]
    if template=="task_tracker":
        properties.extend([("status","상태","status",{"options":["시작 전","진행 중","완료"]}),
                           ("due","기한","date",{})])
    for key,name,kind,config in properties:
        schema("database.property.create",_stable_id("prop",document_id,key),{"name":name,"type":kind,"config":config})
    schema("database.view.create",_stable_id("view",document_id,"table"),{"name":"표","type":"table","config":{}})
    if template=="task_tracker":
        schema("database.view.create",_stable_id("view",document_id,"board"),{
            "name":"보드","type":"board","config":{"board_group_property":_stable_id("prop",document_id,"status")}})
    return True
