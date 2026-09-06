"""The Kubernetes manifests, checked without a cluster.

`kubeconform` in CI answers "is this valid Kubernetes?". These tests answer the question
that actually goes wrong in review — "does it still say what we promised?": non-root, a
read-only root filesystem, three probes, resource limits, a default-deny NetworkPolicy, and
a Secret template that carries placeholders rather than somebody's real key.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

K8S = Path(__file__).resolve().parents[1] / "deploy" / "k8s"


def documents(path: Path) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")) if doc]


@pytest.fixture(scope="module")
def deployment() -> dict:
    return documents(K8S / "base" / "deployment.yaml")[0]


@pytest.fixture(scope="module")
def container(deployment) -> dict:
    return deployment["spec"]["template"]["spec"]["containers"][0]


def test_the_pod_runs_as_a_non_root_user(deployment):
    security = deployment["spec"]["template"]["spec"]["securityContext"]

    assert security["runAsNonRoot"] is True
    assert security["runAsUser"] == 10001, "must match the uid the Dockerfile creates"


def test_the_container_drops_every_capability_and_cannot_escalate(container):
    security = container["securityContext"]

    assert security["allowPrivilegeEscalation"] is False
    assert security["readOnlyRootFilesystem"] is True
    assert security["capabilities"]["drop"] == ["ALL"]


def test_a_read_only_root_filesystem_still_has_somewhere_to_write_the_ledger(
    deployment, container
):
    """The hash-chained audit ledger is a file. Without this mount the pod cannot audit."""
    mounts = {mount["mountPath"] for mount in container["volumeMounts"]}
    volumes = {volume["name"] for volume in deployment["spec"]["template"]["spec"]["volumes"]}

    assert "/app/var" in mounts
    assert volumes == {"var", "tmp"}


def test_all_three_probes_are_declared_and_point_at_the_right_paths(container):
    assert container["startupProbe"]["httpGet"]["path"] == "/health"
    assert container["livenessProbe"]["httpGet"]["path"] == "/health"
    assert container["readinessProbe"]["httpGet"]["path"] == "/ready"


def test_the_probe_paths_are_actually_served(client):
    """A probe path that 404s takes the whole Deployment down. Ask the app, not the YAML."""
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/ready").json()["database"] == "reachable"


def test_resources_are_bounded_in_both_directions(container):
    resources = container["resources"]

    assert set(resources) == {"requests", "limits"}
    assert resources["limits"]["memory"].endswith("Mi")


def test_the_network_policy_defaults_to_deny():
    policies = documents(K8S / "base" / "networkpolicy.yaml")
    deny = next(p for p in policies if p["metadata"]["name"].endswith("default-deny"))

    assert sorted(deny["spec"]["policyTypes"]) == ["Egress", "Ingress"]
    assert "ingress" not in deny["spec"], "a default-deny policy names no allowed source"
    assert "egress" not in deny["spec"]


def test_the_secret_template_contains_placeholders_only():
    secret = documents(K8S / "base" / "secret.yaml")[0]

    for name, value in secret["stringData"].items():
        assert value.startswith("REPLACE"), f"{name} looks like a real value, not a template"


@pytest.mark.parametrize("overlay", ["dev", "prod"])
def test_each_overlay_patches_only_what_it_declares(overlay):
    kustomization = documents(K8S / "overlays" / overlay / "kustomization.yaml")[0]
    patch_files = {Path(patch["path"]).name for patch in kustomization["patches"]}
    present = {path.name for path in (K8S / "overlays" / overlay).glob("*.yaml")}

    assert kustomization["resources"][0] == "../../base"
    assert patch_files <= present, "a patch is declared but the file is missing"
    assert kustomization["namespace"]


def test_dev_runs_one_replica_on_sqlite():
    patch = documents(K8S / "overlays" / "dev" / "deployment.yaml")[0]
    container = patch["spec"]["template"]["spec"]["containers"][0]
    env = {item["name"]: item.get("value") for item in container["env"]}

    assert patch["spec"]["replicas"] == 1
    assert env["ATMPL_DATABASE_URL"].startswith("sqlite:///")


def test_prod_runs_two_replicas_and_takes_its_database_url_from_a_secret():
    patch = documents(K8S / "overlays" / "prod" / "deployment.yaml")[0]
    spec = patch["spec"]["template"]["spec"]
    env = {item["name"]: item for item in spec["containers"][0]["env"]}

    assert patch["spec"]["replicas"] == 2
    assert env["ATMPL_DATABASE_URL"]["valueFrom"]["secretKeyRef"]["name"] == "atmpl-database"
    assert "value" not in env["ATMPL_DATABASE_URL"], "a database URL carries a password"


def test_prod_migrates_before_it_serves():
    """Two replicas must not race to build the same schema."""
    patch = documents(K8S / "overlays" / "prod" / "deployment.yaml")[0]
    init = patch["spec"]["template"]["spec"]["initContainers"][0]

    assert init["command"] == ["python", "-m", "atmpl", "db", "upgrade"]


def test_prod_does_not_leave_the_open_demo_api_switched_on():
    config = documents(K8S / "overlays" / "prod" / "configmap.yaml")[0]

    assert config["data"]["ATMPL_DEMO_OPEN_API"] == "0"
    assert config["data"]["ATMPL_AUTO_CREATE_SCHEMA"] == "false"


def test_every_configmap_key_is_a_setting_this_build_understands():
    """A typo in a ConfigMap key is silently ignored by pydantic-settings; catch it here."""
    from atmpl.settings import Settings

    known = {f"ATMPL_{name.upper()}" for name in Settings.model_fields}
    known |= {"ATMPL_LLM_ADAPTER"}  # the documented alias
    for path in K8S.rglob("configmap.yaml"):
        unknown = sorted(set(documents(path)[0]["data"]) - known)
        assert unknown == [], f"{path.name}: unknown settings {unknown}"
