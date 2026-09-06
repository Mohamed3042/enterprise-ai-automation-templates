from sqlalchemy import func, select

from atmpl.engine.database import Organization, RedTeamResult, Run, Stage, Workflow


def test_all_three_demos_and_mixed_states_are_seeded(seeded_engine):
    with seeded_engine.database.session() as session:
        assert session.scalar(select(func.count(Organization.id))) == 3
        assert session.scalar(select(func.count(Workflow.id))) == 5
        assert session.scalar(select(func.count(Run.id))) == 8
        assert session.scalar(select(func.count(RedTeamResult.id))) == 24
        states = set(session.scalars(select(Run.status)).all())

    assert {"active", "completed", "escalated"} <= states


def test_identical_retail_input_routes_differently_by_region(seeded_engine):
    with seeded_engine.database.session() as session:
        eu = session.scalar(
            select(Stage).where(
                Stage.run_id == "run_retail_eu_1042",
                Stage.stage_key == "classify_refund",
            )
        )
        us = session.scalar(
            select(Stage).where(
                Stage.run_id == "run_retail_us_1042",
                Stage.stage_key == "classify_refund",
            )
        )

    assert eu.input_data["synthetic_case"] == us.input_data["synthetic_case"]
    assert eu.draft["content"]["recommendation"] == "ROUTE_PRIVACY_REVIEW"
    assert us.draft["content"]["recommendation"] == "ROUTE_STANDARD_MANAGER"


def test_bank_ai_never_records_terminal_approve_or_reject(seeded_engine):
    with seeded_engine.database.session() as session:
        stages = session.scalars(
            select(Stage).where(Stage.run_id.in_(["run_bank_override", "run_bank_pending"]))
        ).all()

    ai_recommendations = [
        stage.draft["content"]["recommendation"]
        for stage in stages
        if stage.draft and stage.draft.get("content", {}).get("recommendation")
    ]
    assert ai_recommendations
    assert all("APPROVE" not in value and "REJECT" not in value for value in ai_recommendations)
    terminal = next(
        stage
        for stage in stages
        if stage.stage_key == "terminal_decision" and stage.decision
    )
    assert terminal.decision["actor"] == "Mariam Synthetic"
    assert terminal.decision["decision"] == "reject"


def test_ministry_seed_is_utf8_bilingual(seeded_engine):
    with seeded_engine.database.session() as session:
        run = session.get(Run, "run_ministry_ar_math")
        submit = next(stage for stage in run.stages if stage.stage_key == "submit_lesson")

    assert "خطة درس الرياضيات" in run.title
    assert submit.input_data["subject_en"] == "Mathematics"
    assert submit.input_data["subject_ar"] == "الرياضيات"


def test_seeding_and_exercising_write_only_under_var(tmp_path, monkeypatch):
    """A read-only root filesystem is the Kubernetes default this repository ships.

    `/app/var` is the one writable mount, so the whole start-up path — migrate, seed, run the
    red team, exercise one AI stage per demo — must touch nothing else in the working
    directory. Two pod crash-loops in CI were caused by code that did.
    """
    from atmpl.demos import exercise_demos, seed_demo_data

    monkeypatch.chdir(tmp_path)
    (tmp_path / "var").mkdir()

    engine = seed_demo_data(tmp_path / "var" / "demo.db", tmp_path / "var" / "audit.jsonl")
    outcomes = exercise_demos(engine)

    assert [entry.get("status") for entry in outcomes] == ["draft_ready"] * 3
    assert sorted(path.name for path in tmp_path.iterdir()) == ["var"]
