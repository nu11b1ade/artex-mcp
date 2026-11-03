"""Stdio MCP integration against a fixture HTTP server, including both auth layers."""
import asyncio
import base64
import json
import os
import re
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


class ArtexFixture(BaseHTTPRequestHandler):
    initialized = False
    token = 0
    cookie = 0
    login_count = 0
    write_count = 0
    handler_rejections = 0
    password = "initial-test-password"

    def log_message(self, *_):
        pass

    def reply(self, code, data=None, **headers):
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False).encode()
            headers["Content-Type"] = "application/json"
        data = data or b""
        self.send_response(code)
        self.send_header("Content-Length", str(len(data)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def dispatch(self):
        state = type(self)
        path, _, query = self.path.partition("?")
        data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        auth = self.headers.get("Authorization", "")
        cookies = self.headers.get("Cookie", "")
        new_cookie = {}
        if not state.cookie or f"artex_basic_auth=gate-{state.cookie}" not in cookies:
            expected = "Basic " + base64.b64encode(b"edge:edge-password").decode()
            if auth != expected:
                return self.reply(401, {"error": "请先完成 HTTP Basic Auth 验证"},
                                  **{"WWW-Authenticate": 'Basic realm="Restricted"'})
            state.cookie += 1
            new_cookie["Set-Cookie"] = f"artex_basic_auth=gate-{state.cookie}; Path=/; HttpOnly"
        if path == "/api/basic-auth/check":
            return self.reply(204, **new_cookie)
        if path == "/api/auth/status":
            return self.reply(200, {"initialized": state.initialized})
        if path in ("/api/auth/login", "/api/auth/init"):
            payload = json.loads(data)
            if payload["password"] != state.password:
                return self.reply(401, {"error": "wrong password"})
            if path.endswith("/init"):
                if state.initialized:
                    return self.reply(409, {"error": "already initialized"})
                state.initialized = True
            else:
                assert payload["username"] == "ARTEX"
                state.login_count += 1
            state.token += 1
            return self.reply(200, {"token": f"token-{state.token}"})
        if path not in {"/", "/login", "/function/tasks", "/browser-only"} and (
            not state.token or auth != f"Bearer token-{state.token}"
        ):
            return self.reply(401, {"error": "token 无效或已过期"})
        if path == "/api/auth/change-password":
            payload = json.loads(data)
            assert payload["old_password"] == state.password
            state.password = payload["new_password"]
            return self.reply(200, {"ok": True})
        if path == "/api/settings/basic-auth":
            payload = json.loads(data)
            assert payload == {"username": "edge", "password": "edge-password"}
            state.cookie += 1
            return self.reply(200, {"enabled": True, "username": "edge", "password_set": True},
                              **{"Set-Cookie": f"artex_basic_auth=gate-{state.cookie}; Path=/; HttpOnly"})
        if path == "/api/version":
            return self.reply(200, {"version": "v0.3.26-fixture"})
        if path == "/api/tasks/t-1/control":
            assert json.loads(data) == {"action": "pause"}
            state.write_count += 1
            return self.reply(200, {"status": "paused"})
        if path == "/api/repeater/send":
            state.handler_rejections += 1
            return self.reply(401, {"error": "handler rejected a request"})
        if path == "/api/skills/upload":
            assert query == "overwrite=true"
            assert b'name="file"; filename="test.zip"' in data and b"upload payload" in data
            return self.reply(200, {"name": "test"})
        if path == "/api/workspace/download":
            assert query == "path=a.bin"
            return self.reply(200, b"\x00\xffARTEX", **{"Content-Type": "application/octet-stream"})
        if path == "/api/logs/stream":
            assert self.headers.get("Last-Event-ID") == "40"
            return self.reply(200, b': heartbeat\r\n\r\nid: 41\r\nevent: log\r\ndata: {"seq":41}\r\n\r\n',
                              **{"Content-Type": "text/event-stream"})
        if path in ("/", "/login", "/function/tasks", "/browser-only"):
            return self.reply(200, b"""<html><body><h1>ARTEX fixture</h1>
                <button onclick="localStorage.setItem('mode','dark');this.textContent='Dark'">Theme</button>
                <p id="auth"></p><script>
                document.getElementById('auth').textContent = localStorage.getItem('artex_token') ?
                    'Signed in' : 'Signed out';
                </script></body></html>""", **{"Content-Type": "text/html"})
        return self.reply(404, {"error": "fixture route not found"})

    do_GET = do_POST = do_PUT = dispatch


class Integration(unittest.IsolatedAsyncioTestCase):
    async def test_protocol_auth_routes_files_and_events(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ArtexFixture)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as work:
                await self.session(server, Path(work))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    async def session(self, server, work):
        admin_file = work / "admin.secret"
        admin_file.write_text(ArtexFixture.password)
        transfer = work / "files"
        transfer.mkdir()
        (transfer / "test.zip").write_bytes(b"upload payload")
        (transfer / "outside").symlink_to(admin_file)
        env = {
            "ARTEX_URL": f"http://127.0.0.1:{server.server_port}",
            "ARTEX_BASIC_USER": "edge",
            "ARTEX_BASIC_PASSWORD": "edge-password",
            "ARTEX_ADMIN_PASSWORD_FILE": str(admin_file),
            "ARTEX_NEW_ADMIN_PASSWORD": "new-test-password",
            "ARTEX_NEW_BASIC_PASSWORD": "edge-password",
            "ARTEX_TRANSFER_DIR": str(transfer),
        }
        if executable := os.environ.get("TEST_CHROMIUM_EXECUTABLE"):
            env["ARTEX_CHROMIUM_EXECUTABLE"] = executable
        params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "artex_mcp.py")], env=env)
        async with asyncio.timeout(55), stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            self.assertEqual(len((await session.list_tools()).tools), 10)

            async def call(name, **kwargs):
                result = await session.call_tool(name, kwargs)
                self.assertFalse(result.isError, result.content)
                return result.structuredContent or json.loads(result.content[0].text)

            self.assertFalse((await call("artex_status"))["initialized"])
            unauthorized = await session.call_tool("artex_call", {"method": "GET", "route": "/api/tasks"})
            self.assertTrue(unauthorized.isError)
            refused = await session.call_tool("artex_initialize")
            self.assertTrue(refused.isError)
            self.assertTrue((await call("artex_initialize", confirm=True))["authenticated"])
            self.assertEqual((await call("artex_status"))["version"]["version"], "v0.3.26-fixture")
            self.assertEqual((await call("artex_find_routes", query="side-questions"))["total"], 14)
            self.assertEqual((await call("artex_find_routes"))["total"], 315)
            self.assertTrue((await call("artex_change_password", confirm=True))["changed"])
            await call("artex_configure_basic_auth", username="edge", replace_password=True, confirm=True)
            await call("artex_logout")
            self.assertFalse((await call("artex_status"))["authenticated"])
            await call("artex_login")

            ArtexFixture.token += 1  # Simulate a JWT rejected before the handler.
            control = await call("artex_call", method="POST", route="/api/tasks/{id}/control",
                                 path_params={"id": "t-1"}, body={"action": "pause"})
            self.assertEqual(control["data"], {"status": "paused"})
            self.assertEqual(ArtexFixture.write_count, 1)
            previous_logins = ArtexFixture.login_count
            handler_error = await session.call_tool("artex_call", {"method": "POST", "route": "/api/repeater/send"})
            self.assertTrue(handler_error.isError)
            self.assertEqual(ArtexFixture.handler_rejections, 1)
            self.assertEqual(ArtexFixture.login_count, previous_logins)

            upload = await call("artex_call", method="POST", route="/api/skills/upload",
                                query={"overwrite": True}, upload_files=["test.zip"])
            self.assertEqual(upload["data"], {"name": "test"})
            escape = await session.call_tool("artex_call", {"method": "POST", "route": "/api/skills/upload",
                                                           "upload_files": ["outside"]})
            self.assertTrue(escape.isError)
            download = await call("artex_call", method="GET", route="/api/workspace/download",
                                  query={"path": "a.bin"}, save_as="out.bin")
            self.assertEqual(download["bytes"], 7)
            self.assertEqual((transfer / "out.bin").read_bytes(), b"\x00\xffARTEX")
            sse = await call("artex_read_events", route="/api/logs/stream", last_event_id="40")
            self.assertEqual(sse["events"], [{"event": "log", "id": "41", "data": {"seq": 41}}])
            self.assertEqual(sse["since"], 41)
            if executable:
                await call("artex_browser", action="sign_in")
                await call("artex_browser", action="navigate", value="/browser-only")
                snapshot = await call("artex_browser", action="snapshot")
                self.assertIn("Signed in", snapshot["snapshot"]["data"])
                await call("artex_browser", action="click", selector="button")
                snapshot = await call("artex_browser", action="snapshot")
                self.assertIn("Dark", snapshot["snapshot"]["data"])
                shot = await session.call_tool("artex_browser", {"action": "screenshot"})
                self.assertEqual(shot.content[0].type, "image")
                await call("artex_logout")
                self.assertFalse((await call("artex_status"))["authenticated"])
                await call("artex_browser", action="close")

    async def test_browser_does_not_write_token_after_cross_origin_redirect(self):
        sys.path.insert(0, str(ROOT))
        from browser_ui import BrowserUI

        browser = BrowserUI("https://artex.example", {})
        browser.context = Mock(add_cookies=AsyncMock())
        browser.page = Mock(
            url="https://redirect.example/landing",
            goto=AsyncMock(return_value=Mock(ok=True, url="https://redirect.example/landing")),
            evaluate=AsyncMock(),
        )
        with self.assertRaisesRegex(ValueError, "left the configured Web origin"):
            await browser.sign_in("fixture-token", None)
        browser.page.evaluate.assert_not_awaited()

    def test_every_catalog_route_can_be_invoked(self):
        sys.path.insert(0, str(ROOT))
        from artex_mcp import ROUTES, operation

        self.assertEqual(len(ROUTES), 315)
        for key, meta in ROUTES.items():
            params = {name: "sample/nested.txt" if wildcard else "42"
                      for name, wildcard in re.findall(r"\{(\w+)(\.\.\.)?\}", meta["path"])}
            with self.subTest(key=key):
                if meta.get("tool"):
                    with self.assertRaisesRegex(ValueError, meta["tool"]):
                        operation(meta["method"], meta["path"], params)
                else:
                    actual, _, path = operation(meta["method"], meta["path"], params)
                    self.assertEqual(actual, key)
                    self.assertNotIn("{", path)


if __name__ == "__main__":
    unittest.main()
