from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from flask import Flask

from code_assist_v1 import outlook_integration as outlook
from code_assist_v1 import routes_outlook
from code_assist_v1.routes_outlook import register_outlook_routes


class OutlookRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_data_dir = outlook.USER_DATA_DIR
        outlook.USER_DATA_DIR = self.temp_dir.name
        self._original_current_user_id = routes_outlook.current_user_id
        routes_outlook.current_user_id = lambda: "outlook_route_tester"
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY="outlook-route-test")
        register_outlook_routes(app)
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["code_assist_user"] = {
                "id": "outlook_route_tester",
                "display_name": "Outlook Route Tester",
            }

    def tearDown(self) -> None:
        routes_outlook.current_user_id = self._original_current_user_id
        outlook.USER_DATA_DIR = self.original_data_dir
        self.temp_dir.cleanup()

    def test_connect_queues_initial_sync_and_can_disconnect(self) -> None:
        connector = {
            "available": True,
            "profile_name": "Outlook",
            "inbox_count": 42,
            "unread_count": 7,
        }
        job = {"job_id": "job-test", "status": "queued"}
        with (
            patch("code_assist_v1.routes_outlook.probe_connector", return_value=connector),
            patch("code_assist_v1.routes_outlook.job_manager.submit", return_value=job) as submit,
            patch("code_assist_v1.routes_outlook.wake_outlook_scheduler"),
        ):
            connected = self.client.put(
                "/api/notion/outlook/settings",
                json={
                    "enabled": True,
                    "connector": "classic_outlook",
                    "daily_digest_enabled": True,
                    "daily_digest_time": "09:00",
                    "sync_interval_seconds": 300,
                },
            )

        self.assertEqual(connected.status_code, 200)
        payload = connected.get_json()
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["job"]["job_id"], "job-test")
        self.assertEqual(payload["settings"]["daily_digest_time"], "09:00")
        submit.assert_called_once()

        with patch("code_assist_v1.routes_outlook.wake_outlook_scheduler"):
            disconnected = self.client.put(
                "/api/notion/outlook/settings",
                json={"enabled": "false"},
            )
        self.assertEqual(disconnected.status_code, 200)
        self.assertFalse(disconnected.get_json()["enabled"])

    def test_connection_failure_keeps_integration_disabled(self) -> None:
        with patch(
            "code_assist_v1.routes_outlook.probe_connector",
            return_value={"available": False, "error": "Outlook is not running"},
        ):
            response = self.client.put(
                "/api/notion/outlook/settings",
                json={"enabled": True},
            )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(outlook.get_settings("outlook_route_tester")["enabled"])
        self.assertIn("Outlook", response.get_json()["error"])

    def test_sync_and_digest_require_connection(self) -> None:
        self.assertEqual(self.client.post("/api/notion/outlook/sync").status_code, 409)
        self.assertEqual(self.client.post("/api/notion/outlook/digest").status_code, 409)

    def test_graph_oauth_start_callback_and_disconnect(self) -> None:
        connector = {
            "available": True,
            "authenticated": True,
            "connector": "microsoft_graph",
            "account": {"id": "entra-user", "email": "tester@example.com"},
            "inbox_count": 15,
            "unread_count": 3,
        }
        with patch(
            "code_assist_v1.routes_outlook.begin_authorization",
            return_value="https://login.microsoftonline.com/tenant/oauth2/v2.0/authorize?state=safe",
        ):
            started = self.client.get("/api/notion/outlook/oauth/start")
        self.assertEqual(started.status_code, 302)
        self.assertTrue(started.headers["Location"].startswith("https://login.microsoftonline.com/"))
        self.assertEqual(started.headers["Cache-Control"], "no-store")
        self.assertEqual(started.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(outlook.get_settings("outlook_route_tester")["connector"], "microsoft_graph")
        self.assertFalse(outlook.get_settings("outlook_route_tester")["enabled"])

        job = {"job_id": "graph-initial-job", "status": "queued"}
        with (
            patch("code_assist_v1.routes_outlook.complete_authorization", return_value=connector),
            patch("code_assist_v1.routes_outlook.job_manager.submit", return_value=job) as submit,
            patch("code_assist_v1.routes_outlook.wake_outlook_scheduler"),
        ):
            callback = self.client.get(
                "/api/notion/outlook/oauth/callback?code=authorization-code&state=safe",
            )
        self.assertEqual(callback.status_code, 302)
        self.assertTrue(callback.headers["Location"].endswith("/?outlook=connected"))
        self.assertEqual(callback.headers["Cache-Control"], "no-store")
        self.assertEqual(callback.headers["Referrer-Policy"], "no-referrer")
        self.assertTrue(outlook.get_settings("outlook_route_tester")["enabled"])
        self.assertEqual(submit.call_args.kwargs["metadata"]["connector"], "microsoft_graph")

        with (
            patch("code_assist_v1.routes_outlook.disconnect_graph") as disconnect,
            patch("code_assist_v1.routes_outlook.wake_outlook_scheduler"),
        ):
            disconnected = self.client.delete("/api/notion/outlook/connection")
        self.assertEqual(disconnected.status_code, 200)
        self.assertFalse(disconnected.get_json()["enabled"])
        disconnect.assert_called_once_with("outlook_route_tester")


if __name__ == "__main__":
    unittest.main()
