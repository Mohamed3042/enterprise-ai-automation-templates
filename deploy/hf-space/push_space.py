"""Create (or update) the public Hugging Face Docker Space and upload this folder.

    python deploy/hf-space/push_space.py            # create + upload + report the URL
    python deploy/hf-space/push_space.py --check    # is it live? no writes

Authentication: `hf auth login` (a write token), or `HF_TOKEN` in the environment. The token
is never printed and never written to the repository. The same script runs in CI, where the
token comes from the `HF_TOKEN` repository secret.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SPACE_ID = "Medo4334/atmpl-governed-automation"
SPACE_URL = f"https://huggingface.co/spaces/{SPACE_ID}"
APP_URL = "https://medo4334-atmpl-governed-automation.hf.space"
FOLDER = Path(__file__).resolve().parent


def check(url: str = APP_URL, attempts: int = 1, delay: float = 20.0) -> int:
    """Measure the live URL. A Space that answers 200 is the only proof that counts."""
    import httpx

    for attempt in range(1, attempts + 1):
        try:
            response = httpx.get(url, timeout=30, follow_redirects=True)
        except httpx.HTTPError as exc:
            print(f"[{attempt}/{attempts}] {type(exc).__name__}: {exc}")
        else:
            print(f"[{attempt}/{attempts}] GET {url} -> HTTP {response.status_code}")
            if response.status_code == 200:
                marker = "Ship governed automation" in response.text
                print(f"dashboard marker present: {marker}")
                return 0 if marker else 1
        if attempt < attempts:
            time.sleep(delay)
    return 1


def push() -> int:
    from huggingface_hub import HfApi
    from huggingface_hub.errors import HfHubHTTPError

    api = HfApi()
    try:
        identity = api.whoami()
    except Exception as exc:  # noqa: BLE001 - any auth failure is the same instruction
        print(f"NOT AUTHENTICATED: {type(exc).__name__}: {exc}")
        print("Run `hf auth login` with a WRITE token, or set HF_TOKEN, then run this again.")
        return 2
    print(f"AUTHENTICATED AS: {identity.get('name')}")

    api.create_repo(
        repo_id=SPACE_ID,
        repo_type="space",
        space_sdk="docker",
        private=False,
        exist_ok=True,
    )
    print(f"SPACE: {SPACE_URL}")
    try:
        api.upload_folder(
            repo_id=SPACE_ID,
            repo_type="space",
            folder_path=str(FOLDER),
            # The uploader itself is not part of the Space image.
            ignore_patterns=["push_space.py", "__pycache__/*"],
            commit_message="Deploy the read-only ATMPL demo",
        )
    except HfHubHTTPError as exc:
        print(f"UPLOAD FAILED: {exc}")
        return 1
    print("UPLOADED: README.md + Dockerfile")
    print(f"BUILDING: {SPACE_URL} (a Docker Space takes a few minutes on first build)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Only measure the live URL.")
    parser.add_argument("--attempts", type=int, default=1)
    args = parser.parse_args(argv)
    if args.check:
        return check(attempts=args.attempts)
    exit_code = push()
    if exit_code:
        return exit_code
    return check(attempts=max(args.attempts, 20))


if __name__ == "__main__":
    sys.exit(main())
