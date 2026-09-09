"""Auth/API regressions. No production data is changed; external calls are mocked."""
import asyncio
from datetime import datetime, timezone, timedelta
import unittest
from unittest.mock import AsyncMock, patch

import bcrypt
import httpx
from fastapi.testclient import TestClient
from starlette.requests import Request

import deps
import main
from database.auth import db_verify_password
from routes import report, auth
from services import powerbi
from scripts.check_capacity import classify_capacity

USER = dict(id=1, username="viewer", display_name="Viewer", is_admin=False,
            is_active=True, can_upload=False, department="Sales", pbi_username="viewer")
REPORT = dict(id=1, name="Test", status="active", pbi_report_id="report-id",
              pbi_workspace_id="workspace-id", pbi_dataset_id="dataset-id", tab_type="report")


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_bearer_never_uses_another_accounts_cookie(self):
        for header in ("Bearer invalid", "Bearer ", "Basic x"):
            request = Request(dict(type="http", headers=[(b"authorization", header.encode())],
                                   session={"username": "admin"}))
            with patch("deps.db_get_user") as lookup:
                self.assertIsNone(await deps.current_user(request))
                lookup.assert_not_called()

    async def test_expired_bearer_never_uses_cookie(self):
        token = deps.issue_tab_token("viewer")
        request = Request(dict(type="http", headers=[(b"authorization", f"Bearer {token}".encode())],
                               session={"username": "admin"}))
        with patch("deps.TAB_TOKEN_MAX_AGE", -1):
            self.assertIsNone(await deps.current_user(request))

    async def test_bearer_identity_has_priority(self):
        token = deps.issue_tab_token("viewer")
        request = Request(dict(type="http", headers=[(b"authorization", f"Bearer {token}".encode())],
                               session={"username": "admin"}))
        with patch("deps.db_get_user", return_value=USER) as lookup:
            self.assertEqual(await deps.current_user(request), USER)
            lookup.assert_called_once_with("viewer")
            deps.verify_csrf(request, "")

    async def test_cookie_requires_csrf(self):
        request = Request(dict(type="http", headers=[], session={"csrf_token": "expected"}))
        with self.assertRaises(Exception) as err:
            deps.verify_csrf(request, "wrong")
        self.assertEqual(err.exception.status_code, 403)

    async def test_forwarded_header_does_not_override_peer(self):
        request = Request(dict(type="http", headers=[(b"x-forwarded-for", b"fake")], client=("192.0.2.5", 1)))
        self.assertEqual(deps.get_client_ip(request), "192.0.2.5")

    async def test_long_password_is_rejected_without_bcrypt_exception(self):
        self.assertIsNone(db_verify_password({"password": "unused"}, "한" * 25))

    async def test_sso_token_uses_fragment(self):
        request = Request(dict(type="http", headers=[], query_string=b"code=fake&state=fake",
                               session={"sso_flow": {}}, client=("127.0.0.1", 1)))
        request.session["sso_flow"] = {"state": "fake"}
        with patch.object(auth, "complete_auth_flow", return_value={"id_token_claims": {"preferred_username": "viewer@example.test"}}), \
             patch.object(auth, "db_get_user_by_email", return_value=USER), \
             patch.object(auth, "db_sso_record_login"):
            response = await auth.auth_callback(request)
        self.assertTrue(response.headers["location"].startswith("/#sso_token="))
        self.assertNotIn("?sso_token", response.headers["location"])


class RouteTests(unittest.TestCase):
    def setUp(self):
        # No context manager: lifespan recovery/sync/backup jobs must not run.
        self.client = TestClient(main.app)
        self.headers = {"Authorization": f"Bearer {deps.issue_tab_token('viewer')}"}

    def tearDown(self):
        self.client.close()

    def test_help_is_available_before_login_and_has_diagram(self):
        response = self.client.get("/help")
        self.assertEqual(response.status_code, 200)
        self.assertIn("/static/guide/viewer-flow.svg", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["referrer-policy"], "no-referrer")
        self.assertEqual(self.client.get("/static/guide/viewer-flow.svg").status_code, 200)

    def test_anonymous_embed_denied(self):
        self.assertEqual(self.client.get("/api/embed/1").status_code, 401)

    def test_non_admin_cannot_access_admin(self):
        with patch("deps.db_get_user", return_value=USER):
            self.assertEqual(self.client.get("/api/admin/reports", headers=self.headers).status_code, 403)

    def test_disabled_user_denied(self):
        with patch("deps.db_get_user", return_value=None):
            self.assertEqual(self.client.get("/api/embed/1", headers=self.headers).status_code, 401)

    def test_forbidden_report_never_generates_token(self):
        with patch("deps.db_get_user", return_value=USER), patch.object(report, "db_can_view_report", return_value=False), \
             patch.object(report, "get_embed_token", new_callable=AsyncMock) as token:
            self.assertEqual(self.client.get("/api/embed/1", headers=self.headers).status_code, 403)
            token.assert_not_awaited()

    def test_allowed_viewer_without_upload_right_can_embed(self):
        with patch("deps.db_get_user", return_value=USER), patch.object(report, "db_can_view_report", return_value=True), \
             patch.object(report, "get_embed_token", new_callable=AsyncMock, return_value={"embed_token": "test-only"}), \
             patch.object(report, "_build_get_filter", new_callable=AsyncMock, return_value=None):
            response = self.client.get("/api/embed/1", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_view_only_user_cannot_upload(self):
        with patch("deps.db_get_user", return_value=USER):
            response = self.client.post("/api/upload", headers=self.headers, files={"file": ("test.pbix", b"PKtest")})
            self.assertEqual(response.status_code, 403)

    def test_string_false_is_not_treated_as_allow(self):
        with patch("deps.db_get_user", return_value={**USER, "is_admin": True}):
            for path, body in [("/api/admin/reports/1/access/1", {"can_view": "false"}),
                               ("/api/admin/reports/1/department-access", {"can_view": "false", "department": "Sales"})]:
                self.assertEqual(self.client.post(path, headers=self.headers, json=body).status_code, 400)


class EmbedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        powerbi._embed_cache.clear()
        powerbi._fetch_locks.clear()

    async def test_disabled_report_cannot_reuse_cached_token(self):
        powerbi._set_cached_token(1, "viewer", "cached", "url", 9999999999)
        with patch.object(powerbi, "db_get_report", return_value={**REPORT, "status": "deleted"}):
            with self.assertRaises(Exception) as err:
                await powerbi.get_embed_token(1, "viewer")
        self.assertEqual(err.exception.status_code, 404)

    async def test_rls_identity_is_per_user_and_cache_is_separate(self):
        requests = []
        def handler(req):
            requests.append(req)
            if req.url.path.endswith("GenerateToken"):
                return httpx.Response(200, json={"token": "mock-token", "expiration": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()})
            if "/datasets/" in req.url.path:
                return httpx.Response(200, json={"isEffectiveIdentityRequired": True, "isEffectiveIdentityRolesRequired": True})
            return httpx.Response(200, json={"datasetId": "dataset-id", "embedUrl": "https://example.test/embed"})
        client_type = httpx.AsyncClient
        with patch.object(powerbi, "db_get_report", return_value=REPORT), patch.object(powerbi, "get_access_token", return_value="mock"), \
             patch.object(powerbi.httpx, "AsyncClient", side_effect=lambda **kw: client_type(transport=httpx.MockTransport(handler), **kw)):
            await powerbi.get_embed_token(1, "viewer")
            await powerbi.get_embed_token(1, "viewer")
            await powerbi.get_embed_token(1, "other-viewer")
        import json
        bodies = [json.loads(r.content) for r in requests if r.method == "POST"]
        self.assertEqual(len(requests), 6)
        self.assertEqual([b["identities"][0]["username"] for b in bodies], ["viewer", "other-viewer"])
        self.assertEqual(bodies[0]["identities"][0]["roles"], [powerbi.config.PBI_RLS_ROLE_NAME])

    async def test_capacity_check_distinguishes_ppu_from_supported_sku(self):
        workspace = {"isOnDedicatedCapacity": True, "capacityId": "CAP"}
        self.assertEqual(classify_capacity(workspace, [{"id": "cap", "sku": "PP3"}])[0], 2)
        self.assertEqual(classify_capacity(workspace, [{"id": "cap", "sku": "F2"}])[0], 0)
        self.assertEqual(classify_capacity(workspace, [])[0], 3)


if __name__ == "__main__":
    unittest.main()
