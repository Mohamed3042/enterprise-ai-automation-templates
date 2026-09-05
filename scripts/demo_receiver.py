"""A tiny signed-webhook receiver, so the outbound path is visible end to end.

Standard library only, on purpose: `docker compose --profile receiver up` runs it inside a
plain python image with no install step. It verifies the signature exactly the way
docs/webhooks.md tells an integrator to, and prints what it received.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = os.getenv("ATMPL_RECEIVER_SECRET", "")
TOLERANCE = int(os.getenv("ATMPL_RECEIVER_TOLERANCE", "300"))
PORT = int(os.getenv("ATMPL_RECEIVER_PORT", "8181"))


def verified(signature: str | None, body: bytes) -> tuple[bool, str]:
    if not SECRET:
        return False, "no ATMPL_RECEIVER_SECRET configured"
    if not signature:
        return False, "missing X-ATMPL-Signature"
    try:
        parts = dict(item.split("=", 1) for item in signature.split(",") if "=" in item)
        timestamp = int(parts["t"])
        presented = parts["v1"]
    except (KeyError, ValueError):
        return False, "malformed signature header"
    if abs(time.time() - timestamp) > TOLERANCE:
        return False, "timestamp outside tolerance"
    expected = hmac.new(
        SECRET.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, presented):
        return False, "signature mismatch"
    return True, "verified"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        body = self.rfile.read(int(self.headers.get("content-length", 0)))
        ok, reason = verified(self.headers.get("X-ATMPL-Signature"), body)
        event = self.headers.get("X-ATMPL-Event", "?")
        delivery = self.headers.get("X-ATMPL-Delivery", "?")
        try:
            data = json.loads(body or b"{}").get("data", {})
        except json.JSONDecodeError:
            data = {"raw": body[:200].decode("utf-8", "replace")}
        mark = "OK " if ok else "BAD"
        print(f"[{mark}] {event} delivery={delivery} ({reason}) {data}", flush=True)
        self.send_response(200 if ok else 401)
        self.send_header("content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"received" if ok else b"rejected")

    def do_GET(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"ATMPL demo webhook receiver. POST signed deliveries to /hook.\n")

    def log_message(self, *args) -> None:
        return


if __name__ == "__main__":
    print(f"receiver listening on :{PORT} (secret {'set' if SECRET else 'NOT SET'})", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
