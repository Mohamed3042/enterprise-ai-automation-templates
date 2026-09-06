"""Create (or reuse) the public demo as a Render web service through the API, then measure it.

    python deploy/render/create_service.py            # create or reuse, wait for live, measure
    python deploy/render/create_service.py --check    # measure only, no writes
    python deploy/render/create_service.py --redeploy # trigger a new deploy of the existing service

Authentication: ``RENDER_API_KEY`` in the environment (on Windows the user-level variable is
read as a fallback). The key is never printed. Render requires payment information on file
before it creates any service, Free plan included — measured 2026-09-06 as ``402 Payment
information is required`` from ``POST /v1/services``; add a card at
https://dashboard.render.com/billing first. The Free instance itself costs nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.render.com/v1"
SERVICE_NAME = "atmpl-governed-automation"
REPO = "https://github.com/Mohamed3042/enterprise-ai-automation-templates"
DOCKERFILE = "./deploy/render/Dockerfile"
REGION = "frankfurt"
HEALTH = "/health"


def read_key() -> str:
    key = os.environ.get("RENDER_API_KEY", "").strip()
    if not key and sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
                key = str(winreg.QueryValueEx(k, "RENDER_API_KEY")[0]).strip()
        except OSError:
            key = ""
    if not key:
        raise SystemExit(
            "NO_KEY: set RENDER_API_KEY (Render dashboard -> Account Settings -> API Keys)"
        )
    return key


def call(key: str, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return e.code, raw.decode(errors="replace")[:500]


def http_get(url: str, timeout: float = 60.0) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, headers={"accept": "application/json"}), timeout=timeout
        ) as r:
            return r.status, r.read(200).decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(200).decode(errors="replace")
    except Exception as e:  # noqa: BLE001
        return 0, type(e).__name__


def find_service(key: str) -> dict | None:
    status, body = call(key, "GET", "/services?limit=100")
    if status != 200:
        raise SystemExit(f"SERVICES {status}: {body}")
    for item in body:
        svc = item.get("service", item)
        if svc.get("name") == SERVICE_NAME:
            return svc
    return None


def create_service(key: str) -> dict:
    status, owners = call(key, "GET", "/owners?limit=20")
    if status != 200 or not owners:
        raise SystemExit(f"OWNERS {status}: {owners}")
    owner = [o.get("owner", o) for o in owners][0]
    body = {
        "type": "web_service",
        "name": SERVICE_NAME,
        "ownerId": owner["id"],
        "repo": REPO,
        "branch": "main",
        "autoDeploy": "yes",
        "serviceDetails": {
            "runtime": "docker",
            "plan": "free",
            "region": REGION,
            "healthCheckPath": HEALTH,
            "envSpecificDetails": {"dockerfilePath": DOCKERFILE, "dockerContext": "."},
        },
    }
    status, resp = call(key, "POST", "/services", body)
    if status not in (200, 201):
        raise SystemExit(f"CREATE {status}: {json.dumps(resp)[:600]}")
    svc = resp.get("service", resp)
    print(f"created {svc['name']} id={svc['id']} url={svc.get('serviceDetails', {}).get('url')}")
    return svc


def wait_live(key: str, svc: dict, minutes: int = 20) -> bool:
    deadline = time.time() + minutes * 60
    last = ""
    while time.time() < deadline:
        status, deploys = call(key, "GET", f"/services/{svc['id']}/deploys?limit=1")
        d = (
            deploys[0].get("deploy", deploys[0])
            if status == 200 and deploys
            else {"status": f"http {status}"}
        )
        st = d.get("status", "?")
        if st != last:
            print(f"deploy {d.get('id', '?')} -> {st}", flush=True)
            last = st
        if st == "live":
            return True
        if st in ("build_failed", "update_failed", "canceled", "deactivated", "pre_deploy_failed"):
            print(f"FAILED: https://dashboard.render.com/web/{svc['id']}")
            return False
        time.sleep(20)
    print(f"still {last} after {minutes} minutes")
    return False


def measure(url: str) -> int:
    for _ in range(12):
        code, head = http_get(f"{url}{HEALTH}")
        print(f"GET {url}{HEALTH} -> {code} {head}", flush=True)
        if code == 200:
            return 0
        time.sleep(10)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="measure the live URL only")
    parser.add_argument(
        "--redeploy", action="store_true", help="trigger a new deploy of the existing service"
    )
    args = parser.parse_args()

    key = read_key()
    svc = find_service(key)
    if args.check:
        if not svc:
            print("not created")
            return 1
        return measure(svc["serviceDetails"]["url"])
    if args.redeploy:
        if not svc:
            raise SystemExit("nothing to redeploy: the service does not exist")
        status, resp = call(
            key, "POST", f"/services/{svc['id']}/deploys", {"clearCache": "do_not_clear"}
        )
        if status not in (200, 201):
            raise SystemExit(f"REDEPLOY {status}: {resp}")
        print(f"redeploy {resp.get('id')} started")
    elif svc:
        print(f"reusing {svc['name']} id={svc['id']} url={svc['serviceDetails'].get('url')}")
    else:
        svc = create_service(key)
    if not wait_live(key, svc):
        return 1
    return measure(svc["serviceDetails"]["url"])


if __name__ == "__main__":
    sys.exit(main())
