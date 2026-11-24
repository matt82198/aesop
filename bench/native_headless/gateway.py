"""Local inference gateway. Exposes only the two streaming generation methods."""
import http.client
import json
import uuid
from http.server import BaseHTTPRequestHandler

UPSTREAM = "127.0.0.1"
ROUTES = {"/v1/chat/completions", "/v1/responses"}


def backend(path, body=None):
    connection = http.client.HTTPConnection(UPSTREAM, 11434, timeout=180)
    connection.request("POST" if body is not None else "GET", path, body,
                       {"Content-Type": "application/json"})
    return connection, connection.getresponse()


def model_digest(model):
    connection, response = backend("/api/tags")
    try:
        tags = json.load(response)["models"]
    finally:
        connection.close()
    match = next(m for m in tags if m["name"] == model and m.get("size", 0) > 0)
    connection, response = backend("/api/show", json.dumps({"model": model}))
    try:
        details = json.load(response)
        if response.status != 200 or details.get("remote_model") or details.get("remote_host"):
            raise ValueError("model must have local weights")
    finally:
        connection.close()
    return match["digest"]


class Gateway(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path != "/v1/models":
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        data = [{"id": self.server.model, "object": "model"}]
        self.wfile.write(json.dumps({"object": "list", "data": data}).encode())

    def do_POST(self):
        if self.path not in ROUTES:
            return self.send_error(404)
        size = int(self.headers.get("Content-Length", "0"))
        if not 0 < size <= 4 * 1024 * 1024:
            return self.send_error(413)
        body = self.rfile.read(size)
        request = json.loads(body)
        if request.get("model") != self.server.model:
            return self.send_error(403)
        request_id = uuid.uuid4().hex
        print(json.dumps({"accepted": request_id, "path": self.path}), flush=True)
        connection, response = backend(self.path, body)
        print(json.dumps({"id": request_id, "path": self.path, "status": response.status}), flush=True)
        try:
            self.send_response(response.status)
            self.send_header("Content-Type", response.getheader("Content-Type", "application/json"))
            self.end_headers()
            while chunk := response.read1(65536):
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            connection.close()
