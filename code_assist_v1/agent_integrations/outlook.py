"""Optional read-only email workflow. Absence of proof remains unknown."""
from datetime import datetime
from urllib.parse import quote, urlsplit

from .contracts import IntegrationError, encoded, integer, text


class OutlookConnector:
    def __init__(self, graph=None):
        if graph is None:
            from .. import outlook_graph as graph
        self.graph = graph

    def inbox(self, owner, since_utc, limit=50, cursor_url=""):
        try:
            since = datetime.strptime(since_utc, "%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError):
            raise IntegrationError("since_utc는 UTC 시간(YYYY-MM-DDTHH:MM:SSZ)이어야 합니다.") from None
        integer(limit,"limit",1,100)
        if cursor_url:
            parsed = urlsplit(text(cursor_url,"cursor_url",16000))
            if parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com" or parsed.path.lower() != "/v1.0/me/mailfolders/inbox/messages/delta" or parsed.fragment:
                raise IntegrationError("받은 편지함의 서버 반환 커서만 사용할 수 있습니다.")
        return self.graph.fetch_delta(owner,since_utc=since.strftime("%Y-%m-%dT%H:%M:%SZ"),limit=limit,cursor_url=cursor_url)

    def observe_reply(self, owner, original_id, candidate_id):
        headers={"Prefer":'IdType="ImmutableId"'}
        def message(mid):
            return self.graph.graph_get(owner,"/me/messages/"+quote(text(mid,"message_id",2000),safe=""),
                params={"$select":"id,internetMessageId,internetMessageHeaders,conversationId,parentFolderId,isDraft,sentDateTime"},headers=headers)
        original, reply = message(original_id), message(candidate_id)
        folder = self.graph.graph_get(owner,"/me/mailFolders/sentitems",params={"$select":"id"},headers=headers)
        in_reply_to = [h.get("value","").strip() for h in reply.get("internetMessageHeaders",[])
                       if str(h.get("name","")).lower()=="in-reply-to"]
        proven = (reply.get("isDraft") is False and bool(reply.get("sentDateTime"))
                  and bool(folder.get("id")) and reply.get("parentFolderId")==folder["id"]
                  and bool(original.get("internetMessageId")) and original["internetMessageId"] in in_reply_to
                  and bool(original.get("conversationId")) and original["conversationId"]==reply.get("conversationId"))
        if not proven:
            return {"status":"unknown","reason":"일치하는 보낸 회신을 확인하지 못했습니다. 미실행으로 판단하지 않습니다."}
        return dict(status="provider_observed_reply",provider="microsoft_graph",
                    original_message_id=original["id"],reply_message_id=reply["id"],sent_at=reply["sentDateTime"],
                    observation="사용자 사서함의 보낸 편지함에서 원본에 대한 직접 회신을 확인함. 수신·업무 완료는 확인하지 않음.")
