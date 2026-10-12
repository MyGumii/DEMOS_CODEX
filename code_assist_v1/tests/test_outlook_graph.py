from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from code_assist_v1 import outlook_graph as graph


class _FakeMsalApp:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id

    def initiate_auth_code_flow(self, **_kwargs):
        return {
            "auth_uri": f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/authorize?state=test-state",
            "state": "test-state",
            "code_verifier": "pkce-secret-value",
        }

    def acquire_token_by_auth_code_flow(self, _flow, _response, **_kwargs):
        return {
            "access_token": "graph-access-token-secret",
            "id_token_claims": {
                "tid": self.tenant_id,
                "oid": "entra-user-id",
                "name": "Graph Tester",
                "preferred_username": "tester@example.com",
            },
        }

    def get_accounts(self, **_kwargs):
        return [{"home_account_id": "home-account"}]


class OutlookGraphTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = graph.USER_DATA_DIR
        graph.USER_DATA_DIR = self.temp_dir.name
        self.tenant_id = "11111111-1111-4111-8111-111111111111"
        self.client_id = "22222222-2222-4222-8222-222222222222"
        self.environment = {
            "CODE_ASSIST_MS_TENANT_ID": self.tenant_id,
            "CODE_ASSIST_MS_CLIENT_ID": self.client_id,
            "CODE_ASSIST_MS_CLIENT_SECRET": "client-secret-not-written-to-user-data",
            "CODE_ASSIST_MS_REDIRECT_URI": "https://notion.example.com/api/notion/outlook/oauth/callback",
            "CODE_ASSIST_MS_TOKEN_KEY": Fernet.generate_key().decode("ascii"),
        }
        self.env_patch = patch.dict(os.environ, self.environment)
        self.env_patch.start()
        self.user_id = "graph_tester"

    def tearDown(self) -> None:
        self.env_patch.stop()
        graph.USER_DATA_DIR = self.original_data_dir
        self.temp_dir.cleanup()

    def _complete_login(self) -> dict:
        fake_app = _FakeMsalApp(self.tenant_id)
        with patch.object(graph, "_msal_app", return_value=fake_app):
            graph.begin_authorization(self.user_id)
            with patch.object(
                graph,
                "_request_with_token",
                return_value={"totalItemCount": 20, "unreadItemCount": 4},
            ):
                return graph.complete_authorization(
                    self.user_id,
                    {"code": "authorization-code", "state": "test-state"},
                )

    def test_configuration_is_single_tenant_and_never_exposes_secret(self) -> None:
        public = graph.public_configuration()
        self.assertTrue(public["configured"])
        self.assertTrue(public["single_tenant"])
        self.assertEqual(public["permissions"], graph.GRAPH_PERMISSION_DESCRIPTION)
        self.assertNotIn("client_secret", public)
        self.assertNotIn(self.environment["CODE_ASSIST_MS_CLIENT_SECRET"], str(public))

    def test_oauth_flow_and_token_cache_are_encrypted_and_user_scoped(self) -> None:
        fake_app = _FakeMsalApp(self.tenant_id)
        with patch.object(graph, "_msal_app", return_value=fake_app):
            authorization_url = graph.begin_authorization(self.user_id)
        self.assertIn(self.tenant_id, authorization_url)
        flow_path = graph._flow_path(self.user_id)
        with open(flow_path, "rb") as handle:
            encrypted_flow = handle.read()
        self.assertNotIn(b"pkce-secret-value", encrypted_flow)
        self.assertNotIn(b"test-state", encrypted_flow)

        result = self._complete_login()
        self.assertTrue(result["authenticated"])
        self.assertEqual(result["account"]["email"], "tester@example.com")
        token_path = graph._token_path(self.user_id)
        with open(token_path, "rb") as handle:
            encrypted_token = handle.read()
        self.assertNotIn(b"graph-access-token-secret", encrypted_token)
        self.assertNotIn(b"tester@example.com", encrypted_token)
        self.assertTrue(graph.has_token(self.user_id))
        self.assertFalse(graph.has_token("another_user"))

        status = graph.connection_status(self.user_id)
        self.assertTrue(status["authenticated"])
        self.assertNotIn("cache", str(status))
        graph.disconnect(self.user_id)
        self.assertFalse(graph.has_token(self.user_id))

    def test_expired_flow_is_rejected_and_deleted(self) -> None:
        fake_app = _FakeMsalApp(self.tenant_id)
        with patch.object(graph, "_msal_app", return_value=fake_app):
            graph.begin_authorization(self.user_id)
        record = graph._read_encrypted(graph._flow_path(self.user_id))
        record["created_at"] = int(time.time()) - graph.OAUTH_FLOW_TTL_SECONDS - 1
        graph._write_encrypted(graph._flow_path(self.user_id), record)
        with self.assertRaises(graph.GraphOAuthError):
            graph.complete_authorization(self.user_id, {"code": "code", "state": "test-state"})
        self.assertFalse(os.path.exists(graph._flow_path(self.user_id)))

    def test_delta_messages_are_normalized_without_attachment_content(self) -> None:
        graph_message = {
            "id": "immutable-message-id",
            "internetMessageId": "<message@example.com>",
            "conversationId": "conversation-id",
            "subject": "Review requested",
            "from": {"emailAddress": {"name": "Owner", "address": "owner@example.com"}},
            "toRecipients": [{"emailAddress": {"name": "Tester", "address": "tester@example.com"}}],
            "ccRecipients": [],
            "receivedDateTime": "2026-08-02T00:00:00Z",
            "lastModifiedDateTime": "2026-08-02T00:01:00Z",
            "isRead": False,
            "importance": "high",
            "body": {"contentType": "text", "content": "Please review this."},
            "hasAttachments": True,
        }
        response = {
            "value": [graph_message],
            "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$deltatoken=opaque",
        }
        with (
            patch.object(graph, "graph_get", return_value=response) as getter,
            patch.object(graph, "_attachment_metadata", return_value=[{"name": "proposal.pdf", "size": 20}]),
        ):
            payload = graph.fetch_delta(
                self.user_id,
                since_utc="2026-08-01T00:00:00+00:00",
                limit=10,
            )

        self.assertTrue(payload["cursor_complete"])
        self.assertEqual(payload["messages"][0]["sender_address"], "owner@example.com")
        self.assertTrue(payload["messages"][0]["unread"])
        self.assertEqual(payload["messages"][0]["attachments"][0]["name"], "proposal.pdf")
        params = getter.call_args.kwargs["params"]
        self.assertIn("receivedDateTime ge", params["$filter"])
        self.assertEqual(graph.GRAPH_SCOPES, ("Mail.Read",))
        self.assertNotIn("Mail.ReadWrite", graph.GRAPH_SCOPES)
        self.assertNotIn("Mail.Send", graph.GRAPH_SCOPES)

    def test_html_body_is_converted_to_safe_text(self) -> None:
        body = graph._message_body_text({
            "contentType": "html",
            "content": "<html><head><style>.hidden{}</style></head><body><p>Hello <b>team</b></p><script>steal()</script><p>Review today.</p></body></html>",
        })
        self.assertEqual(body, "Hello team\nReview today.")
        self.assertNotIn("steal", body)

    def test_expired_delta_cursor_restarts_from_bounded_lookback(self) -> None:
        response = {
            "value": [],
            "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$deltatoken=fresh",
        }
        with patch.object(
            graph,
            "graph_get",
            side_effect=[graph.GraphRequestError("expired", status_code=410, code="syncStateNotFound"), response],
        ) as getter:
            payload = graph.fetch_delta(
                self.user_id,
                since_utc="2026-08-01T00:00:00+00:00",
                limit=10,
                cursor_url="https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta?$deltatoken=expired",
            )
        self.assertTrue(payload["cursor_reset"])
        self.assertIn("receivedDateTime ge", getter.call_args_list[1].kwargs["params"]["$filter"])

    def test_external_delta_url_is_rejected(self) -> None:
        with self.assertRaises(graph.GraphRequestError):
            graph._validate_graph_url("https://evil.example.com/steal")


if __name__ == "__main__":
    unittest.main()
