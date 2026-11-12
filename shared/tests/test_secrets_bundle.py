# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""The sealed secrets bundle (ADR-053 §5).

`infra/backup.sh` backed up PostgreSQL and the object store but not `infra/.env`
(every KEK) nor `secrets/`: a restore onto a new server got the data and not the
keys to read it. The functions in `infra/lib-secrets.sh` are *run* here with a real
`gpg` (sealing, opening, a wrong password, a tampered byte, a stale bundle), not
searched for as text. Without gpg they skip locally and fail under CI.

Mutations that must die: make `secrets_bundle_state` ignore mtimes; drop the
`cmp`/`diff` in `compare_secrets_bundle`; drop a directory from `SECRETS_DIRS`;
drop the `secretos=` line from the seal or the `exit 6`; drop
`INTELLIGENCE_CREDENTIAL_KEK` from `app`.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests._compose import COMPOSE_PROD, RAIZ, servicios

LIB = RAIZ / "infra" / "lib-secrets.sh"
BACKUP = RAIZ / "infra" / "backup.sh"
DRILL = RAIZ / "infra" / "verify-restore-semanal.sh"
PASS = "correct horse battery staple"


@pytest.fixture
def gpg_home(tmp_path: Path) -> Path:
    if shutil.which("gpg") is None:
        if os.environ.get("CI"):
            pytest.fail("the secrets bundle tests need gpg in CI")
        pytest.skip("gpg is not installed")
    home = tmp_path / "g"
    home.mkdir(mode=0o700)
    return home


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "infra").mkdir(parents=True)
    (root / "infra" / ".env").write_text("SIGNING_KEY_ENCRYPTION_KEY=abc\n")
    (root / "secrets" / "signing" / "alcaldia").mkdir(parents=True)
    (root / "secrets" / "signing" / "alcaldia" / "seal.p12").write_bytes(b"\x00seal")
    (root / "secrets" / "ca-users-public").mkdir(parents=True)
    (root / "secrets" / "ca-users-public" / "ca-users.crt").write_text("cert")
    (tmp_path / "pass").write_text(PASS)
    (tmp_path / "wrong").write_text("not the passphrase at all")
    (tmp_path / "bundles").mkdir()
    return root


def _sh(script: str, home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", f". '{LIB}'\n{script}"],
        capture_output=True,
        text=True,
        env={**os.environ, "GNUPGHOME": str(home)},
    )


def _seal(tree: Path, home: Path, name: str = "secrets-20261111_000000.tar.gpg") -> Path:
    out = tree.parent / "bundles" / name
    r = _sh(f"seal_secrets_bundle '{tree}' '{tree}/infra/.env' '{out}' '{tree.parent}/pass'", home)
    assert r.returncode == 0, r.stderr
    return out


def test_seal_open_and_compare(tree: Path, gpg_home: Path) -> None:
    bundle = _seal(tree, gpg_home)
    assert oct(bundle.stat().st_mode & 0o777) == "0o600"
    assert _sh(f"secrets_bundle_looks_sealed '{bundle}'", gpg_home).returncode == 0
    r = _sh(f"compare_secrets_bundle '{bundle}' '{tree.parent}/pass' '{tree}' '{tree}/infra/.env'", gpg_home)
    assert r.returncode == 0, r.stderr


def test_wrong_password_does_not_open(tree: Path, gpg_home: Path) -> None:
    bundle = _seal(tree, gpg_home)
    r = _sh(f"compare_secrets_bundle '{bundle}' '{tree.parent}/wrong' '{tree}' '{tree}/infra/.env'", gpg_home)
    assert r.returncode != 0
    assert "no se abre" in r.stderr


def test_a_tampered_bundle_does_not_open(tree: Path, gpg_home: Path) -> None:
    bundle = _seal(tree, gpg_home)
    data = bytearray(bundle.read_bytes())
    data[-20] ^= 0xFF
    bundle.write_bytes(bytes(data))
    r = _sh(f"compare_secrets_bundle '{bundle}' '{tree.parent}/pass' '{tree}' '{tree}/infra/.env'", gpg_home)
    assert r.returncode != 0


def test_a_bundle_that_differs_from_the_live_secrets_fails(tree: Path, gpg_home: Path) -> None:
    bundle = _seal(tree, gpg_home)
    (tree / "secrets" / "signing" / "alcaldia" / "seal.p12").write_bytes(b"\x00other seal")
    r = _sh(f"compare_secrets_bundle '{bundle}' '{tree.parent}/pass' '{tree}' '{tree}/infra/.env'", gpg_home)
    assert r.returncode != 0
    assert "secrets/signing" in r.stderr


def test_state_absent_ok_stale(tree: Path, gpg_home: Path) -> None:
    state = f"secrets_bundle_state '{tree.parent}/bundles' '{tree}' '{tree}/infra/.env'"
    assert _sh(state, gpg_home).stdout.strip() == "AUSENTE"
    bundle = _seal(tree, gpg_home)
    assert _sh(state, gpg_home).stdout.strip() == "OK"
    later = bundle.stat().st_mtime + 5
    os.utime(tree / "secrets" / "ca-users-public" / "ca-users.crt", (later, later))
    assert _sh(state, gpg_home).stdout.strip() == "DESACTUALIZADO"


def test_the_bundle_covers_every_mounted_secret() -> None:
    """Every `../secrets/<dir>` the production manifest mounts is in `SECRETS_DIRS`."""
    declared = set(re.search(r"^SECRETS_DIRS=\((.*)\)$", LIB.read_text(), re.M)[1].split())
    mounted = {
        "secrets/" + vol.split(":")[0].removeprefix("../secrets/").split("/")[0]
        for svc in servicios(COMPOSE_PROD).values()
        for vol in svc.get("volumes") or []
        if isinstance(vol, str) and vol.startswith("../secrets/")
    }
    assert mounted, "the manifest mounts no secrets: the check would be empty"
    assert mounted <= declared, f"mounted but not sealed: {sorted(mounted - declared)}"


def test_backup_carries_the_bundle_and_says_so() -> None:
    text = BACKUP.read_text(encoding="utf-8")
    assert '. "$SCRIPT_DIR/lib-secrets.sh"' in text
    assert 'echo "secretos=$SECRETOS"' in text
    assert re.search(r'if \[ "\$SECRETOS" != "OK" \]; then\n(?:.*\n){1,3}\s+exit 6\n', text)


def test_weekly_drill_checks_the_bundle() -> None:
    text = DRILL.read_text(encoding="utf-8")
    assert "secrets_bundle_looks_sealed" in text
    assert "compare_secrets_bundle" in text
    assert '[ "$SELLO_SECRETOS" != "OK" ]' in text


def test_intelligence_kek_reaches_the_app_in_production() -> None:
    """`init_tenant.py` runs in `app` and refuses to create an entity without it."""
    env = servicios(COMPOSE_PROD)["app"].get("environment") or {}
    value = env.get("INTELLIGENCE_CREDENTIAL_KEK", "") if isinstance(env, dict) else ""
    assert value.startswith("${INTELLIGENCE_CREDENTIAL_KEK:?"), value
