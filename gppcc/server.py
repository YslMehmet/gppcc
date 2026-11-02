"""Local web UI + JSON API (stdlib only). Binds to 127.0.0.1."""

from __future__ import annotations

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import config, output
from .engine import Engine

WEB_DIR = Path(__file__).parent / "web"


class ApiError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def make_handler(engine: Engine, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    allowed_origins = {f"http://{h}" for h in allowed_hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "gppcc"

        def log_message(self, *args):  # keep the console quiet
            pass

        # ---------------------------------------------------------- guards
        def _check_request(self, mutating: bool) -> None:
            # Host check blocks DNS-rebinding; Origin check blocks cross-site requests
            # from other web pages – the API can inject keystrokes, so be strict.
            if self.headers.get("Host") not in allowed_hosts:
                raise ApiError(403, "bad host")
            origin = self.headers.get("Origin")
            if mutating:
                if origin not in allowed_origins:
                    raise ApiError(403, "bad origin")
                if "application/json" not in (self.headers.get("Content-Type") or ""):
                    raise ApiError(415, "expected application/json")
            elif origin is not None and origin not in allowed_origins:
                raise ApiError(403, "bad origin")

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 2_000_000:
                raise ApiError(413, "too large")
            raw = self.rfile.read(n) if n else b"{}"
            try:
                return json.loads(raw or b"{}")
            except ValueError:
                raise ApiError(400, "invalid json")

        def _send(self, status: int, payload=None, body: bytes | None = None,
                  ctype: str = "application/json") -> None:
            if body is None:
                body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _handle(self, method: str) -> None:
            try:
                self._check_request(mutating=method != "GET")
                path = unquote(urlparse(self.path).path)
                if path.startswith("/api/"):
                    status, payload = self._api(method, path[5:].strip("/").split("/"))
                    self._send(status, payload)
                elif method == "GET":
                    self._static(path)
                else:
                    raise ApiError(404, "not found")
            except ApiError as e:
                self._send(e.status, {"error": str(e)})
            except (ValueError, KeyError) as e:
                self._send(400, {"error": str(e)})
            except Exception as e:  # pragma: no cover
                self._send(500, {"error": f"{type(e).__name__}: {e}"})

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

        def do_DELETE(self):
            self._handle("DELETE")

        # ---------------------------------------------------------- static
        def _static(self, path: str) -> None:
            if path in ("", "/"):
                path = "/index.html"
            f = (WEB_DIR / path.lstrip("/")).resolve()
            if WEB_DIR.resolve() not in f.parents or not f.is_file():
                raise ApiError(404, "not found")
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            self._send(200, body=f.read_bytes(), ctype=ctype)

        # ------------------------------------------------------------- api
        def _api(self, method: str, parts: list[str]):
            route = (method, parts[0] if parts else "")
            arg = parts[1] if len(parts) > 1 else None
            sub = parts[2] if len(parts) > 2 else None

            if route == ("GET", "state"):
                snap = engine.snapshot()
                snap["permissions"] = output.has_permissions()
                try:
                    x, y = engine.out.position()
                    snap["cursor"] = [round(x), round(y)]
                except Exception:
                    snap["cursor"] = None
                return 200, snap

            if route == ("GET", "meta"):
                return 200, {
                    "inputs": config.INPUTS,
                    "triggers": config.TRIGGERS,
                    "action_types": config.ACTION_TYPES,
                    "mouse_buttons": config.MOUSE_BUTTONS,
                    "macro_ops": config.MACRO_OPS,
                    "sticks": config.STICKS,
                    "keys": output.available_key_names(),
                    "platform": "mac" if output.IS_MAC else "windows" if output.IS_WIN else "other",
                    "defaults": config.DEFAULT_PROFILE,
                    "remap_targets": config.REMAP_TARGETS,
                }

            if route == ("POST", "enabled"):
                engine.set_enabled(bool(self._body().get("enabled")))
                return 200, {"ok": True}

            if route == ("POST", "permissions"):
                output.request_permissions()
                return 200, {"ok": True}

            if route == ("POST", "test-action"):
                b = self._body()
                engine.test_action(b.get("action") or {}, delay=float(b.get("delay", 0)))
                return 200, {"ok": True}

            if route == ("POST", "test-macro"):
                b = self._body()
                engine.test_macro(str(b.get("name", "test")), b.get("macro") or {},
                                  delay=float(b.get("delay", 3)))
                return 200, {"ok": True}

            if route == ("GET", "remap"):
                return 200, {"device": engine.pads.active_name, "remaps": engine.remaps,
                             "targets": config.REMAP_TARGETS}

            if route == ("PUT", "remap"):
                b = self._body()
                device = str(b.get("device") or "").strip()
                if not device:
                    raise ApiError(400, "no device")
                return 200, {"device": device, "map": engine.set_remap(device, b.get("map") or {})}

            if route == ("GET", "profiles"):
                return 200, {"active": engine.active_name, "names": config.list_profiles()}

            if parts[0] == "profile" and arg:
                return self._profile(method, arg, sub)

            raise ApiError(404, "unknown endpoint")

        def _profile(self, method: str, name: str, sub: str | None):
            if not config.valid_name(name):
                raise ApiError(400, "invalid profile name (letters, digits, space, - _ . only)")
            exists = name in config.list_profiles()

            if method == "GET" and sub is None:
                if not exists:
                    raise ApiError(404, "no such profile")
                return 200, config.load_profile(name)

            if method == "PUT" and sub is None:
                body = self._body()
                body["name"] = name
                saved = config.save_profile(body)
                engine.profile_saved(saved)
                return 200, saved

            if method == "DELETE" and sub is None:
                if not exists:
                    raise ApiError(404, "no such profile")
                if len(config.list_profiles()) <= 1:
                    raise ApiError(400, "cannot delete the last profile")
                config.delete_profile(name)
                if engine.active_name == name:
                    engine.switch_profile(config.list_profiles()[0])
                return 200, {"ok": True}

            if method == "POST" and sub == "activate":
                if not exists:
                    raise ApiError(404, "no such profile")
                engine.switch_profile(name)
                return 200, {"ok": True}

            if method == "POST" and sub == "copy":
                new = str(self._body().get("to", "")).strip()
                if not config.valid_name(new):
                    raise ApiError(400, "invalid new name")
                if new in config.list_profiles():
                    raise ApiError(409, "a profile with that name exists")
                p = config.load_profile(name)
                p["name"] = new
                return 200, config.save_profile(p)

            if method == "POST" and sub == "rename":
                new = str(self._body().get("to", "")).strip()
                if not config.valid_name(new):
                    raise ApiError(400, "invalid new name")
                if new in config.list_profiles():
                    raise ApiError(409, "a profile with that name exists")
                p = config.load_profile(name)
                p["name"] = new
                config.save_profile(p)
                config.delete_profile(name)
                if engine.active_name == name:
                    engine.switch_profile(new)
                return 200, {"ok": True}

            raise ApiError(404, "unknown endpoint")

    return Handler


def start_server(engine: Engine, port: int) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(engine, port))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True, name="http").start()
    return httpd
