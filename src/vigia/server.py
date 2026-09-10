"""Servidor HTTP para uso local. Não é um servidor de produção multiusuário."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from vigia.actions import ActionStore
from vigia.analytics import DatasetStore, bulletin


def create_server(root: Path, port: int, data_dir: Path | None = None, runtime_dir: Path | None = None):
    datasets = DatasetStore(data_dir or root / "data")
    actions = ActionStore((runtime_dir or root / "runtime") / "actions.sqlite")
    web = root / "web"

    class Handler(BaseHTTPRequestHandler):
        server_version = "VigiaSaude/0.1"

        def log_message(self, format, *args):
            # Sem URLs de consulta nem conteúdo de ações nos logs.
            pass

        def respond(self, status: int, body: bytes, content_type: str = "application/json; charset=utf-8", filename: str | None = None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'; form-action 'self'")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def json_response(self, status: int, value):
            self.respond(status, json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8"))

        def valid_host(self) -> bool:
            expected = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            return self.headers.get("Host") in expected

        def mutation_allowed(self) -> bool:
            if not self.valid_host() or self.headers.get("X-Vigia-Client") != "local":
                return False
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}:
                return False
            return self.headers.get("Content-Type", "").split(";")[0].strip() == "application/json"

        def read_payload(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise ValueError("Tamanho de requisição inválido.") from exc
            if not 0 < length <= 16384:
                raise ValueError("Corpo ausente ou superior a 16 KiB.")
            self.connection.settimeout(10)
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError("Corpo incompleto.")
            return json.loads(body)

        def do_GET(self):
            if not self.valid_host():
                self.json_response(403, {"error": "Este serviço aceita apenas acesso local."})
                return
            parsed = urlsplit(self.path)
            params = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
            try:
                if parsed.path == "/api/health":
                    self.json_response(200, {"status": "ok", "dataset_available": datasets.load() is not None})
                elif parsed.path == "/api/dashboard":
                    self.json_response(200, datasets.dashboard(params))
                elif parsed.path == "/api/actions":
                    self.json_response(200, {"actions": actions.list()})
                elif parsed.path == "/api/brief":
                    self.respond(200, bulletin(datasets.dashboard(params)).encode("utf-8"), "text/markdown; charset=utf-8", "boletim-vigia-saude.md")
                elif parsed.path in {"/", "/index.html", "/app.js", "/styles.css"}:
                    name = "index.html" if parsed.path == "/" else parsed.path[1:]
                    types = {"index.html": "text/html", "app.js": "text/javascript", "styles.css": "text/css"}
                    self.respond(200, (web / name).read_bytes(), types[name] + "; charset=utf-8")
                elif parsed.path == "/favicon.ico":
                    self.respond(204, b"", "image/x-icon")
                else:
                    self.json_response(404, {"error": "Recurso não encontrado."})
            except (ValueError, TypeError) as exc:
                self.json_response(400, {"error": str(exc)})
            except Exception:
                self.json_response(500, {"error": "Falha ao carregar os dados locais. Confira a validação do snapshot."})

        def mutate(self, updating: bool):
            if not self.mutation_allowed():
                # Consome um corpo pequeno já enviado para evitar TCP reset no
                # Windows antes de o cliente receber a resposta de recusa.
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if 0 < length <= 16384:
                        self.connection.settimeout(1)
                        self.rfile.read(length)
                except (ValueError, OSError):
                    pass
                self.json_response(403, {"error": "Operação permitida apenas pela aplicação local."})
                return
            path = urlsplit(self.path).path
            if (not updating and path != "/api/actions") or (updating and not path.startswith("/api/actions/")):
                self.json_response(404, {"error": "Recurso não encontrado."})
                return
            try:
                payload = self.read_payload()
                if updating:
                    result = actions.update(path.removeprefix("/api/actions/"), payload)
                else:
                    result = actions.create(payload, datasets.municipality_options(), self.headers.get("Idempotency-Key", ""))
                self.json_response(200 if updating else 201, {"action": result})
            except KeyError:
                self.json_response(404, {"error": "Ação não encontrada."})
            except (ValueError, TypeError, UnicodeError) as exc:
                self.json_response(400, {"error": str(exc)})
            except Exception:
                self.json_response(500, {"error": "Não foi possível salvar a ação. Tente novamente com a mesma solicitação."})

        def do_POST(self):
            self.mutate(False)

        def do_PATCH(self):
            self.mutate(True)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def serve(root: Path, port: int = 8765):
    server = create_server(root, port)
    print(f"Vigia Saúde disponível em http://127.0.0.1:{server.server_port}", flush=True)
    print("Uso local. Para encerrar, pressione Ctrl+C.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
