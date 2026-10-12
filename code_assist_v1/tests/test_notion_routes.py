from __future__ import annotations

import io
import base64
import tempfile
import unittest
import zipfile

from flask import Flask

from code_assist_v1 import notion_store as ns
from code_assist_v1 import routes_notion
from code_assist_v1.routes_notion import register_notion_routes


class NotionRouteIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_user_data_dir = ns.USER_DATA_DIR
        ns.USER_DATA_DIR = self.temp_dir.name
        self._route_user_id = "route_tester"
        self._original_current_user_id = routes_notion.current_user_id
        routes_notion.current_user_id = lambda: self._route_user_id
        app = Flask(__name__)
        app.config.update(TESTING=True, SECRET_KEY="notion-route-test")
        register_notion_routes(app)
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session["code_assist_user"] = {"id": "route_tester", "display_name": "Route Tester"}

    def tearDown(self) -> None:
        routes_notion.current_user_id = self._original_current_user_id
        ns.USER_DATA_DIR = self.original_user_data_dir
        self.temp_dir.cleanup()

    def test_ui_icon_public_and_downloaded_html_keep_page_icon_and_file_scope(self) -> None:
        from PIL import Image
        page = self.client.post('/api/notion/pages', json={'title':'Icon export','icon':'👋'}).get_json()['page']
        url = f"/api/notion/pages/{page['id']}"
        exported = self.client.get(url + '/export_html')
        self.assertEqual(exported.status_code, 200)
        self.assertIn(b'data:image/svg+xml;base64,', exported.data)
        self.assertIn(b'CC BY 4.0', exported.data)
        self.client.patch(url, json={'icon':''})
        self.assertNotIn(b'<div class="page-icon">', self.client.get(url + '/export_html').data)
        token = self.client.post(url + '/public_link').get_json()['token']
        self.assertNotIn(b'<div class="page-icon">', self.client.get(f'/public/notion/{token}').data)
        self.client.patch(url, json={'icon':'ni:flask:blue'})
        self.assertIn(b'stroke="#337eb6"', self.client.get(url + '/export_html').data)

        stream = io.BytesIO(); Image.new('RGB',(2,2),'blue').save(stream,format='PNG')
        raw = stream.getvalue()
        uploaded = self.client.post(url + '/files', data={'file':(io.BytesIO(raw),'icon.png')}, content_type='multipart/form-data')
        self.assertEqual(uploaded.status_code, 200)
        file_id = uploaded.get_json()['file']['id']
        self.client.patch(url, json={'icon':'nf:'+file_id})
        self.assertIn(base64.b64encode(raw), self.client.get(url + '/export_html').data)
        public = self.client.get(f'/public/notion/{token}')
        self.assertIn(f'/public/notion/{token}/files/{file_id}'.encode(), public.data)
        self.assertEqual(self.client.get(f'/public/notion/{token}/files/{file_id}').data, raw)
        other = self.client.post('/api/notion/pages', json={'title':'Foreign icon','icon':'nf:'+file_id}).get_json()['page']
        other_url = f"/api/notion/pages/{other['id']}"
        self.assertNotIn(base64.b64encode(raw), self.client.get(other_url+'/export_html').data)
        other_token = self.client.post(other_url+'/public_link').get_json()['token']
        self.assertNotIn(f'/files/{file_id}'.encode(), self.client.get(f'/public/notion/{other_token}').data)
        self.assertIn(self.client.get(f'/public/notion/{other_token}/files/{file_id}').status_code, (403,404))
        self._route_user_id = 'unshared-reader'
        self.assertIn(self.client.get(url+'/export_html').status_code, (403,404))

    def test_page_blocks_search_comments_history_export_and_lock(self) -> None:
        created_response = self.client.post(
            "/api/notion/pages",
            json={"title": "Exact project alpha", "icon": "🧪"},
        )
        self.assertEqual(created_response.status_code, 200)
        page_id = created_response.get_json()["page"]["id"]

        blocks = [
            {"type": "heading1", "content": "Roadmap", "props": {}},
            {"type": "paragraph", "content": "exact phrase appears in this body", "props": {}},
            {"type": "toggle", "content": "Details", "props": {"open": False}},
            {"type": "calendar", "content": "Dates", "props": {"rows": []}},
        ]
        saved = self.client.put(f"/api/notion/pages/{page_id}/blocks", json={"blocks": blocks})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual([item["type"] for item in saved.get_json()["blocks"]], [item["type"] for item in blocks])

        quoted = self.client.get('/api/notion/search?q=%22exact+phrase%22&sort=updated_desc&date=week')
        self.assertEqual(quoted.status_code, 200)
        self.assertEqual([item["id"] for item in quoted.get_json()["items"]], [page_id])
        missing = self.client.get('/api/notion/search?q=%22phrase+missing%22')
        self.assertEqual(missing.get_json()["items"], [])

        comment = self.client.post(
            f"/api/notion/pages/{page_id}/comments",
            json={
                "content": "Please review",
                "block_id": saved.get_json()["blocks"][1]["id"],
                "anchor": {"start": 0, "end": 5, "text": "exact"},
            },
        )
        self.assertEqual(comment.status_code, 200)
        comment_id = comment.get_json()["comment"]["id"]
        reply = self.client.post(
            f"/api/notion/pages/{page_id}/comments",
            json={"content": "Thread reply", "parent_id": comment_id},
        )
        self.assertEqual(reply.status_code, 200)
        reaction = self.client.post(
            f"/api/notion/pages/{page_id}/comments/{comment_id}/reactions",
            json={"emoji": "🎉"},
        )
        self.assertEqual(reaction.status_code, 200)
        self.assertEqual(reaction.get_json()["comment"]["reactions"]["🎉"], ["route_tester"])
        comments = self.client.get(f"/api/notion/pages/{page_id}/comments").get_json()["items"]
        self.assertEqual(comments[0]["content"], "Please review")
        self.assertEqual(comments[0]["anchor"]["text"], "exact")
        self.assertEqual(comments[0]["reply_count"], 1)
        self.assertEqual(comments[1]["parent_id"], comment_id)

        versions = self.client.get(f"/api/notion/pages/{page_id}/versions")
        self.assertGreaterEqual(len(versions.get_json()["items"]), 2)
        historical_id = versions.get_json()["items"][-1]["id"]
        historical = self.client.get(f"/api/notion/pages/{page_id}/versions/{historical_id}")
        self.assertEqual(historical.status_code, 200)
        self.assertEqual(historical.get_json()["page_id"], page_id)
        self.assertFalse(historical.get_json()["current"])
        updates = self.client.get(f"/api/notion/pages/{page_id}/updates?limit=1")
        self.assertEqual(updates.status_code, 200)
        self.assertTrue(updates.get_json()["items"][0]["preview"]["items"])
        self.assertTrue(updates.get_json()["next_before_id"])
        exported = self.client.get(f"/api/notion/pages/{page_id}/export_markdown?download=1")
        self.assertEqual(exported.status_code, 200)
        self.assertIn("text/markdown", exported.content_type)
        self.assertIn(b"Roadmap", exported.data)

        imported = self.client.post(
            "/api/notion/import_markdown",
            json={"title": "Imported", "markdown": "# Heading\n\n- item", "parent_id": page_id},
        )
        self.assertEqual(imported.status_code, 200)
        self.assertEqual(imported.get_json()["page"]["parent_id"], page_id)
        self.assertEqual(imported.get_json()["blocks"][0]["type"], "heading1")

        locked = self.client.patch(f"/api/notion/pages/{page_id}", json={"settings": {"locked": True}})
        self.assertEqual(locked.status_code, 200)
        lock_updates = self.client.get(f"/api/notion/pages/{page_id}/updates?limit=1")
        self.assertEqual(lock_updates.status_code, 200)
        self.assertEqual(lock_updates.get_json()["items"][0]["preview"]["items"], [
            {"kind": "locked", "text": "페이지 잠금"},
        ])
        upload = self.client.post(
            f"/api/notion/pages/{page_id}/files",
            data={"file": (io.BytesIO(b"locked"), "locked.txt")},
            content_type="multipart/form-data",
        )
        self.assertEqual(upload.status_code, 403)

    def test_form_view_supports_authenticated_and_public_submissions(self) -> None:
        created = self.client.post("/api/notion/pages", json={"title": "Survey"}).get_json()
        page_id = created["page"]["id"]
        saved = self.client.put(
            f"/api/notion/pages/{page_id}/blocks",
            json={"blocks": [
                {
                    "type": "form",
                    "content": "Survey",
                    "props": {
                        "view": "form",
                        "form_title": "Quick survey",
                        "form_required_properties": ["name"],
                        "properties": [
                            {"id": "name", "name": "Name", "type": "title"},
                            {"id": "place", "name": "Place", "type": "place"},
                        ],
                        "rows": [],
                    },
                },
                {"id": "tabs_route", "type": "tabs", "content": "", "props": {"tabs": [{"id": "first", "name": "First"}, {"id": "second", "name": "Second"}], "active_tab_id": "first"}},
                {"type": "paragraph", "content": "First tab content", "props": {"tab_group": "tabs_route", "tab_id": "first"}},
                {"type": "paragraph", "content": "Second tab content", "props": {"tab_group": "tabs_route", "tab_id": "second"}},
            ]},
        ).get_json()
        block_id = saved["blocks"][0]["id"]
        authenticated = self.client.post(
            f"/api/notion/pages/{page_id}/forms/{block_id}/submit",
            json={"values": {"name": "Signed in", "place": "Seoul"}},
        )
        self.assertEqual(authenticated.status_code, 200)
        self.assertEqual(authenticated.get_json()["row"]["place"], "Seoul")

        public_link = self.client.post(f"/api/notion/pages/{page_id}/public_link").get_json()
        token = public_link["token"]
        public_page = self.client.get(f"/public/notion/{token}")
        self.assertEqual(public_page.status_code, 200)
        self.assertIn(f"/public/notion/{token}/forms/{block_id}".encode(), public_page.data)
        self.assertIn(f"/public/notion/{token}/duplicate".encode(), public_page.data)
        self.assertIn(b"public-tabs", public_page.data)
        self.assertIn(b"First tab content", public_page.data)
        self.assertIn(b"Second tab content", public_page.data)
        public_submit = self.client.post(
            f"/public/notion/{token}/forms/{block_id}",
            data={"f_name": "Anonymous", "f_place": "Busan"},
        )
        self.assertEqual(public_submit.status_code, 303)
        self.assertIn("submitted=1", public_submit.headers["Location"])
        rows = self.client.get(f"/api/notion/pages/{page_id}").get_json()["blocks"][0]["props"]["rows"]
        self.assertEqual([item["name"] for item in rows], ["Signed in", "Anonymous"])

        disabled = self.client.patch(
            f"/api/notion/pages/{page_id}/public_link",
            json={"allow_duplicate": False},
        )
        self.assertEqual(disabled.status_code, 200)
        self.assertFalse(disabled.get_json()["allow_duplicate"])
        self.assertNotIn(f"/public/notion/{token}/duplicate".encode(), self.client.get(f"/public/notion/{token}").data)
        self.assertEqual(self.client.post(f"/public/notion/{token}/duplicate").status_code, 403)
        self.client.patch(f"/api/notion/pages/{page_id}/public_link", json={"allow_duplicate": True})

        with self.client.session_transaction() as session:
            session.pop("code_assist_user", None)
        self._route_user_id = None
        login_redirect = self.client.post(f"/public/notion/{token}/duplicate")
        self.assertEqual(login_redirect.status_code, 303)
        self.assertTrue(login_redirect.headers["Location"].startswith("/login?next="))
        with self.client.session_transaction() as session:
            session["code_assist_user"] = {"id": "route_reader", "display_name": "Route Reader"}
        self._route_user_id = "route_reader"
        duplicate = self.client.post(f"/public/notion/{token}/duplicate")
        self.assertEqual(duplicate.status_code, 303)
        self.assertTrue(duplicate.headers["Location"].startswith("/page/"))
        copied_page_id = duplicate.headers["Location"].split("/page/", 1)[1]
        copied = self.client.get(f"/api/notion/pages/{copied_page_id}")
        self.assertEqual(copied.status_code, 200)
        self.assertEqual(copied.get_json()["page"]["title"], "Survey")

    def test_csv_import_creates_typed_database_rows_and_pages(self) -> None:
        response = self.client.post(
            "/api/notion/import_csv",
            json={
                "title": "Imported tasks",
                "csv": (
                    "Name,Amount,Due,Done,Site,Email\n"
                    "Alpha,12.5,2026-08-01,true,https://example.com,alpha@example.com\n"
                    "Beta,7,2026-08-02,false,https://example.org,beta@example.org\n"
                ),
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        block = data["blocks"][0]
        self.assertEqual(block["type"], "table")
        self.assertEqual(
            [item["type"] for item in block["props"]["properties"]],
            ["title", "number", "date", "checkbox", "url", "email"],
        )
        self.assertEqual(block["props"]["rows"][0]["title"], "Alpha")
        self.assertEqual(block["props"]["rows"][0][block["props"]["properties"][1]["id"]], 12.5)
        self.assertTrue(all(item["page_id"] for item in block["props"]["rows"]))
        children = [item for item in self.client.get("/api/notion/pages").get_json()["items"] if item["parent_id"] == data["page"]["id"]]
        self.assertEqual({item["title"] for item in children}, {"Alpha", "Beta"})
        csv_export = self.client.get(f"/api/notion/pages/{data['page']['id']}/export_csv?block_id={block['id']}")
        self.assertEqual(csv_export.status_code, 200)
        self.assertIn("text/csv", csv_export.content_type)
        self.assertTrue(csv_export.data.startswith("\ufeffName,Amount".encode("utf-8")))
        self.assertIn(b"Alpha,12.5", csv_export.data)
        amount_id = block["props"]["properties"][1]["id"]
        block["props"]["views"] = [{
            "id": "view-csv-alpha", "name": "Alpha only", "type": "table",
            "filters": [{"property": "title", "operator": "equals", "value": "Alpha"}],
            "sorts": [], "property_visibility": {amount_id: False},
        }]
        saved = self.client.put(f"/api/notion/pages/{data['page']['id']}/blocks", json={"blocks": [block]})
        self.assertEqual(saved.status_code, 200)
        view_export = self.client.get(
            f"/api/notion/pages/{data['page']['id']}/export_csv?block_id={block['id']}&view_id=view-csv-alpha")
        self.assertEqual(view_export.status_code, 200)
        self.assertIn(b"Alpha", view_export.data)
        self.assertNotIn(b"Beta", view_export.data)
        self.assertNotIn(b"Amount", view_export.data)
        self.assertEqual(self.client.get(
            f"/api/notion/pages/{data['page']['id']}/export_csv?block_id={block['id']}&view_id=unknown-view").status_code, 404)
        database_html = self.client.get(
            f"/api/notion/pages/{data['page']['id']}/export_html?block_id={block['id']}&view_id=view-csv-alpha")
        self.assertEqual(database_html.status_code, 200)
        self.assertIn(b"Alpha", database_html.data)
        self.assertNotIn(b"Beta", database_html.data)
        self.assertNotIn(b"Amount", database_html.data)
        zipped_html = self.client.get(
            f"/api/notion/pages/{data['page']['id']}/export_html?block_id={block['id']}&view_id=view-csv-alpha&bundle=1")
        self.assertEqual(zipped_html.status_code, 200)
        self.assertIn("application/zip", zipped_html.content_type)
        self.assertEqual(self.client.get(
            f"/api/notion/pages/{data['page']['id']}/export_html?block_id={block['id']}"
            "&bundle=1&page_content=exclude_files_images").status_code, 200)
        self.assertEqual(self.client.get(
            f"/api/notion/pages/{data['page']['id']}/export_html?block_id={block['id']}"
            "&bundle=1&page_content=invalid").status_code, 400)
        with zipfile.ZipFile(io.BytesIO(zipped_html.data)) as archive:
            names = archive.namelist()
            self.assertEqual({name.rsplit(".", 1)[-1] for name in names}, {"html", "csv"})
            html_name = next(name for name in names if name.endswith(".html"))
            csv_name = next(name for name in names if name.endswith(".csv"))
            self.assertIn(b"Alpha", archive.read(html_name))
            self.assertNotIn(b"Beta", archive.read(html_name))
            exported_csv = archive.read(csv_name)
            self.assertTrue(exported_csv.startswith(b"\xef\xbb\xbfName,Due,Done,Site,Email\r\nAlpha,"))
            self.assertNotIn(b"Beta", exported_csv)
            self.assertNotIn(b"Amount", exported_csv)
        markdown_bundle = self.client.get(
            f"/api/notion/pages/{data['page']['id']}/export_html?block_id={block['id']}&view_id=view-csv-alpha&bundle=markdown_csv")
        self.assertEqual(markdown_bundle.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(markdown_bundle.data)) as archive:
            names = archive.namelist()
            self.assertEqual(len(names), 2)
            self.assertTrue(all(name.endswith(".csv") for name in names))
            current_name = next(name for name in names if not name.endswith("_all.csv"))
            all_name = next(name for name in names if name.endswith("_all.csv"))
            self.assertNotIn(b"Beta", archive.read(current_name))
            self.assertIn(b"Beta", archive.read(all_name))
            self.assertNotIn(b"Amount", archive.read(current_name))
            self.assertIn(b"Amount", archive.read(all_name))
        self.assertEqual(self.client.get(
            f"/api/notion/pages/{data['page']['id']}/export_html?block_id={block['id']}&view_id=unknown-view").status_code, 404)
        html_export = self.client.get(f"/api/notion/pages/{data['page']['id']}/export_html")
        self.assertEqual(html_export.status_code, 200)
        self.assertIn("text/html", html_export.content_type)
        self.assertIn(b"Notion Workspace HTML", html_export.data)

    def test_personal_database_html_zip_keeps_attached_file_and_relative_link(self) -> None:
        created = self.client.post('/api/notion/pages', json={'title': 'Personal asset export'})
        self.assertEqual(created.status_code, 200)
        page_id = created.get_json()['page']['id']
        payload = b'\x89PNG\r\n\x1a\n' + b'isolated-personal-export'
        uploaded = self.client.post(f'/api/notion/pages/{page_id}/files',
            data={'file': (io.BytesIO(payload), 'receipt.png')},
            content_type='multipart/form-data')
        self.assertEqual(uploaded.status_code, 200)
        file_id = uploaded.get_json()['file']['id']
        block = {'id': 'personal-db-asset', 'type': 'table', 'content': 'Asset records',
            'props': {'properties': [
                {'id': 'title', 'name': 'Name', 'type': 'title'},
                {'id': 'files', 'name': 'Files', 'type': 'files'}],
                'rows': [{'id': 'asset-row', 'title': 'Saved row',
                    'files': [{'file_id': file_id, 'original_name': 'receipt.png'}],
                    'body': {'blocks': [{'id': 'asset-image', 'type': 'image',
                        'content': 'Saved image', 'props': {'file_id': file_id,
                            'name': 'receipt.png'}}]}}]}}
        saved = self.client.put(f'/api/notion/pages/{page_id}/blocks',
            json={'blocks': [block]})
        self.assertEqual(saved.status_code, 200)
        exported = self.client.get(f'/api/notion/pages/{page_id}/export_html',
            query_string={'block_id': block['id'], 'bundle': '1'})
        self.assertEqual(exported.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(exported.data)) as archive:
            assets = [name for name in archive.namelist() if name.startswith('assets/')]
            self.assertEqual(len(assets), 1)
            self.assertEqual(archive.read(assets[0]), payload)
            html = archive.read(next(name for name in archive.namelist()
                if name.endswith('.html'))).decode('utf-8')
            self.assertIn(f'src="{assets[0]}"', html)
            self.assertGreaterEqual(html.count(f'href="{assets[0]}"'), 2)
        excluded = self.client.get(f'/api/notion/pages/{page_id}/export_html',
            query_string={'block_id': block['id'], 'bundle': '1',
                'page_content': 'exclude_files_images'})
        self.assertEqual(excluded.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(excluded.data)) as archive:
            self.assertFalse(any(name.startswith('assets/') for name in archive.namelist()))
        other = self.client.post('/api/notion/pages',
            json={'title': 'Unrelated personal file'}).get_json()['page']['id']
        foreign = self.client.post(f'/api/notion/pages/{other}/files',
            data={'file': (io.BytesIO(b'unrelated-bytes'), 'unrelated.txt')},
            content_type='multipart/form-data')
        self.assertEqual(foreign.status_code, 200)
        foreign_id = foreign.get_json()['file']['id']
        block['props']['rows'][0]['files'] = [{'file_id': foreign_id}]
        block['props']['rows'][0]['body']['blocks'][0]['props']['file_id'] = foreign_id
        self.assertEqual(self.client.put(f'/api/notion/pages/{page_id}/blocks',
            json={'blocks': [block]}).status_code, 200)
        cross_page = self.client.get(f'/api/notion/pages/{page_id}/export_html',
            query_string={'block_id': block['id'], 'bundle': '1'})
        self.assertEqual(cross_page.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(cross_page.data)) as archive:
            assets = [name for name in archive.namelist() if name.startswith('assets/')]
            self.assertEqual(len(assets), 1)
            self.assertEqual(archive.read(assets[0]), b'unrelated-bytes')
        ns.share_page(self._route_user_id, page_id, 'limited_reader', role='view')
        self._route_user_id = 'limited_reader'
        restricted = self.client.get(f'/api/notion/pages/{page_id}/export_html',
            query_string={'block_id': block['id'], 'bundle': '1'})
        self.assertIn(restricted.status_code, (403, 404))


if __name__ == "__main__":
    unittest.main()
