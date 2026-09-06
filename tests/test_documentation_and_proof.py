
import re

from atmpl.catalog import PROJECT_ROOT

REQUIRED_SCREENSHOTS = (
    "dashboard-home.png",
    "bank-human-override.png",
    "ministry-arabic.png",
    "pending-approvals.png",
    "audit-verify.png",
    "redteam-results.png",
    "webhook-started-run.png",
    "webhook-deliveries.png",
    "api-docs.png",
)


def test_readme_has_methodology_diagrams_and_single_quickstart():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert readme.count("```mermaid") == 2
    assert readme.count("python -m atmpl demo up") == 1
    assert "The AI is never the final authority" in readme
    assert "What is real vs simulated" in readme
    assert "VERIFIED" in readme
    assert "[INFERRED]" in readme


def test_readme_verify_block_names_the_command_and_what_to_look_for():
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "## Verify in two minutes" in readme
    assert "docker compose up" in readme
    assert "SEEDED: retail + ministry + bank" in readme
    assert "24 / 24 attacks blocked" in readme
    assert "human:demo" in readme, "the demo-mode boundary sentence must be in the README"


def test_every_real_decision_has_an_adr_and_the_demo_has_a_runbook():
    adrs = sorted(path.name for path in (PROJECT_ROOT / "docs" / "adr").glob("*.md"))

    assert adrs == [
        "0001-api-versioning.md",
        "0002-auth-model.md",
        "0003-webhook-outbox.md",
        "0004-sqlite-and-postgres.md",
        "0005-secrets-provider.md",
    ]
    for name in adrs:
        text = (PROJECT_ROOT / "docs" / "adr" / name).read_text(encoding="utf-8")
        assert "## Context" in text and "## Decision" in text and "## Consequences" in text
    assert (PROJECT_ROOT / "docs" / "demo-runbook.md").exists()
    assert (PROJECT_ROOT / "docs" / "webhooks.md").exists()


def test_env_example_documents_every_setting():
    from atmpl.settings import Settings

    example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    undocumented = [
        f"ATMPL_{name.upper()}"
        for name in Settings.model_fields
        if f"ATMPL_{name.upper()}" not in example
    ]

    assert undocumented == [], f"settings missing from .env.example: {undocumented}"


def test_required_live_screenshots_are_valid_png_files():
    root = PROJECT_ROOT / "docs" / "proof" / "screenshots"
    for filename in REQUIRED_SCREENSHOTS:
        payload = (root / filename).read_bytes()
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
        assert len(payload) > 50_000


def test_runtime_contains_no_high_autoapproval_escape_hatch():
    prohibited = ("auto_approve", "allow_high", "bypass_high", "skip_human")
    runtime = "\n".join(
        path.read_text(encoding="utf-8") for path in (PROJECT_ROOT / "src").rglob("*.py")
    ).lower()

    assert all(token not in runtime for token in prohibited)


def test_repository_has_no_node_build_manifest():
    assert not (PROJECT_ROOT / "package.json").exists()
    assert not (PROJECT_ROOT / "package-lock.json").exists()
    assert not (PROJECT_ROOT / "yarn.lock").exists()


def test_proof_pack_contains_fail_first_and_green_logs():
    proof = PROJECT_ROOT / "docs" / "proof"
    red = (proof / "redteam_red_before.txt").read_text(encoding="utf-8")
    green = (proof / "redteam_green_after.txt").read_text(encoding="utf-8")

    assert "Exit code: 1" in red
    assert "24 failed" in red
    assert "Exit code: 0" in green
    assert "24 passed" in green


def test_ci_runs_lint_tests_and_redteam():
    ci = (PROJECT_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "ruff check ." in ci
    assert "pytest" in ci
    assert "python -m atmpl redteam" in ci


def test_local_markdown_links_resolve():
    markdown_files = [PROJECT_ROOT / "README.md", *(PROJECT_ROOT / "docs").rglob("*.md")]
    missing: list[str] = []
    for document in markdown_files:
        text = document.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^]]*\]\(([^)]+)\)", text):
            if target.startswith(("http://", "https://", "#")):
                continue
            relative = target.split("#", 1)[0]
            if relative and not (document.parent / relative).resolve().exists():
                missing.append(f"{document.relative_to(PROJECT_ROOT)} -> {target}")
    assert not missing, "Missing local links:\n" + "\n".join(missing)
