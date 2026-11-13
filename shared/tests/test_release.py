# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Versioned releases and deploy (ADR-053 §3).

* Every tag `X.Y.Z` builds the images of `scripts/release.py plan` (read from
  `infra/docker-compose.build.yml`) and writes `release-<version>.yml`, where
  every image is pinned by digest. Nothing is deployed by a loose SHA.
* `ORPYCA_TAG` is gone; the manifest asks for `ORPYCA_VERSION`.
* The `deploy` job runs `infra/deploy.sh`, whose first step is the backup: if
  the backup fails, nothing is deployed.
* The version is a query: `/health` returns it and the migrator records it in
  `public.schema_version`.

Mutations that must die: leave an image without digest in the release file;
move the backup after `up -d` in `deploy.sh` or drop its `exit 1`; drop
`StrictHostKeyChecking yes`; drop `"version"` from `liveness`; drop the
`schema_version` write from the runner.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess

import pytest

from tests._compose import (
    COMPOSE_PROD,
    RAIZ,
    RELEASE_WORKFLOW,
    disparadores,
    guion,
    job,
    servicios,
    servicios_propios,
)

_spec = importlib.util.spec_from_file_location("release", RAIZ / "scripts" / "release.py")
assert _spec and _spec.loader
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)

DEPLOY = RAIZ / "infra" / "deploy.sh"
DIGEST = "sha256:" + "a" * 64


def _con_placeholder() -> set[str]:
    return {
        m["svc"]
        for s in servicios(COMPOSE_PROD).values()
        if (m := release.PLACEHOLDER.search(str(s.get("image", ""))))
    }


def test_the_plan_builds_every_image_the_manifest_names() -> None:
    planned = {e["service"] for e in release.plan()}
    assert _con_placeholder() <= planned, f"not built: {sorted(_con_placeholder() - planned)}"
    assert {*servicios_propios(), "frontend", "app", "worker"} <= planned
    for e in release.plan():
        assert (RAIZ / e["dockerfile"]).is_file(), e
        assert (RAIZ / e["context"]).is_dir(), e


def test_the_release_file_pins_every_image_by_digest() -> None:
    digests = {s: DIGEST for s in _con_placeholder()}
    text = release.manifest("1.0.0", "ghcr.io/o/r", digests)
    images = re.findall(r"^\s+image: (\S+)$", text, re.M)
    own = [i for i in images if i.startswith("ghcr.io/o/r/")]
    assert len(own) == sum(1 for s in servicios(COMPOSE_PROD).values() if release.PLACEHOLDER.search(str(s.get("image", ""))))
    assert all(re.fullmatch(r"ghcr\.io/o/r/[a-z0-9-]+:1\.0\.0@sha256:[0-9a-f]{64}", i) for i in own), own
    assert "ORPYCA_VERSION" not in text and "ORPYCA_REGISTRY" not in text


@pytest.mark.parametrize(
    ("version", "digests"),
    [("1.0.0", {}), ("v1.0", None), ("1.0.0", {"app": "latest"})],
)
def test_the_release_file_refuses_what_it_cannot_pin(version: str, digests: dict | None) -> None:
    base = {s: DIGEST for s in _con_placeholder()}
    if digests == {}:
        base.pop("app")
    elif digests:
        base.update(digests)
    with pytest.raises(SystemExit):
        release.manifest(version, "ghcr.io/o/r", base)


def test_orpyca_tag_is_gone() -> None:
    # Solo lo versionado: los .pyc y demás restos de una corrida no cuentan.
    versionados = subprocess.run(
        ["git", "ls-files", "infra", "shared/tests", ".github", "docs/es/deployment.md", "docs/en/deployment.md"],
        cwd=RAIZ, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert versionados, "git ls-files no devolvió nada"
    hallados = [
        rel for rel in versionados
        if rel != "shared/tests/test_release.py"
        and "ORPYCA_TAG" in (RAIZ / rel).read_text(encoding="utf-8", errors="ignore")
    ]
    assert hallados == []
    assert not (RAIZ / ".gitlab-ci.yml").exists(), "CI vive en .github/workflows (ADR-053 §3)"


def test_a_tag_triggers_the_release() -> None:
    assert disparadores(RELEASE_WORKFLOW)["push"]["tags"] == ["[0-9]+.[0-9]+.[0-9]+"]
    assert job(RELEASE_WORKFLOW, "ci")["uses"] == "./.github/workflows/ci.yml"
    assert job(RELEASE_WORKFLOW, "images")["needs"] == "ci"
    assert "--build-arg \"ORPYCA_VERSION=$VERSION\"" in guion(job(RELEASE_WORKFLOW, "images"))
    assert "scripts/release.py manifest" in guion(job(RELEASE_WORKFLOW, "images"))


def test_deploy_is_ssh_to_a_known_host_and_runs_deploy_sh() -> None:
    deploy = job(RELEASE_WORKFLOW, "deploy")
    assert deploy["needs"] == "images"
    assert deploy["environment"] == "${{ matrix.target }}", "one environment (and its secrets) per installation"
    script = guion(deploy)
    assert "StrictHostKeyChecking yes" in script and "DEPLOY_KNOWN_HOSTS" in script
    assert "StrictHostKeyChecking no" not in script
    assert "./infra/deploy.sh" in script


def test_deploy_sh_blocks_on_the_backup() -> None:
    text = DEPLOY.read_text(encoding="utf-8")
    backup = text.index('"$SCRIPT_DIR/backup.sh"')
    assert re.search(r'if ! ENV_FILE=.*"\$SCRIPT_DIR/backup.sh"; then\n(?:.*\n){1,2}\s+exit 1\n', text)
    assert backup < text.index('"${COMPOSE[@]}" pull') < text.index('"${COMPOSE[@]}" up -d')
    assert "set -euo pipefail" in text


def test_deploy_sh_refuses_a_failed_backup(tmp_path) -> None:
    """Run it: a backup that exits 6 (no secrets bundle) stops before Docker is touched."""
    infra = tmp_path / "infra"
    infra.mkdir()
    (infra / "deploy.sh").write_text(DEPLOY.read_text(encoding="utf-8"))
    (infra / "backup.sh").write_text("#!/usr/bin/env bash\nexit 6\n")
    (infra / "release-1.0.0.yml").write_text("services: {}\n")
    (infra / ".env").write_text("")
    docker = tmp_path / "bin" / "docker"
    docker.parent.mkdir()
    docker.write_text(f"#!/usr/bin/env bash\ntouch '{tmp_path}/docker-was-called'\n")
    for f in (infra / "deploy.sh", infra / "backup.sh", docker):
        f.chmod(0o755)
    r = subprocess.run(
        ["bash", str(infra / "deploy.sh"), "1.0.0"],
        capture_output=True, text=True, timeout=30,
        env={"PATH": f"{docker.parent}:/usr/bin:/bin", "BACKUP_DIR": str(tmp_path / "b")},
    )
    assert r.returncode == 1 and "NO se despliega" in r.stderr, r.stderr
    assert not (tmp_path / "docker-was-called").exists()
    assert not (infra / ".deployed-version").exists()


def test_the_version_is_in_health_and_in_the_database() -> None:
    from orpycamcp_common import health

    assert health.liveness("x")["version"] == health.version()
    runner = (RAIZ / "services" / "docuboto" / "migrations" / "runner.py").read_text(encoding="utf-8")
    assert "INSERT INTO public.schema_version" in runner and "await conn.execute(RECORD_VERSION" in runner
    assert (RAIZ / "services" / "docuboto" / "migrations" / "global" / "060_schema_version.sql").is_file()
    for f in [RAIZ / "infra" / "Dockerfile.app", RAIZ / "infra" / "Dockerfile.worker"]:
        assert "ARG ORPYCA_VERSION" in f.read_text(encoding="utf-8"), f
