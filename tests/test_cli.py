from pathlib import Path

import yaml

from atmpl.catalog import PROJECT_ROOT
from atmpl.cli import main


def test_redteam_cli_reports_all_blocked(capsys):
    exit_code = main(["redteam"])

    assert exit_code == 0
    assert "REDTEAM: 24/24 BLOCKED" in capsys.readouterr().out


def test_init_then_resolve_cli(tmp_path: Path, capsys):
    assert main(["init", "retail", "--org", "Synthetic CLI Org", "--output", str(tmp_path)]) == 0
    answers = next(tmp_path.glob("*/answers.yaml"))
    valid = yaml.safe_load(
        (PROJECT_ROOT / "demos" / "retail" / "eu.answers.yaml").read_text(encoding="utf-8")
    )
    valid["organization"]["name"] = "Synthetic CLI Org"
    answers.write_text(yaml.safe_dump(valid), encoding="utf-8")
    compiled = tmp_path / "compiled.yaml"

    assert main(["resolve", "retail", str(answers), "--output", str(compiled)]) == 0
    assert compiled.exists()
    assert "COMPILED:" in capsys.readouterr().out

