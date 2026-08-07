# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests del MODELO DE SEGURIDAD de `SubprocessVeraPdfValidator` (E10 Increment
C2b, RF-PRE-02/RF-FIR-15).

veraPDF real (JVM) NO está en la imagen de dev/CI -- estos tests mockean
`asyncio.create_subprocess_exec` (nunca levantan un proceso Java real) para
probar POR COMPORTAMIENTO cada invariante que el docstring del módulo bajo
prueba declara como defensa concreta contra un PDF/entrada adversarial:
no-shell + args fijos, whitelist de flavour, env scrubbeado, timeout+killpg,
cap de salida, parseo defensivo del JSON real de veraPDF, semáforo de
concurrencia, aislamiento del tempdir 0700 y "nunca lanza".

Complementa (sin duplicar):
  - `test_subprocess_verapdf_ci_smoke.py`: ejercita el binario FAKE real
    como proceso externo, gateado por VERAPDF_BINARY -- no corre en el
    carril normal de `pytest -q`.
  - `test_pdfa_factory.py`: `_resolve_verapdf_binary`/`get_pdfa_validator`
    (validación de la ruta del binario, degradación a stub).
  - `test_aip_pdfa.py`: wiring end-to-end del empaquetado del AIP.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import stat
from types import SimpleNamespace

import pytest

from app.services.pdfa import subprocess_verapdf
from app.services.pdfa.subprocess_verapdf import SubprocessVeraPdfValidator

_BINARY = "/opt/verapdf/verapdf"


# --------------------------------------------------------------------------
# Test doubles para `asyncio.create_subprocess_exec` -- NUNCA se levanta un
# proceso real; se sustituye la función que lo invoca por un fake que se
# comporta como un `asyncio.subprocess.Process` en los aspectos que
# `subprocess_verapdf._invoke`/`_drain` observan: `.stdout`/`.stderr` con
# `.read(n)` async, `.pid`, `.returncode`, `.wait()` async.
# --------------------------------------------------------------------------


class FakeStreamReader:
    """Doble de `asyncio.StreamReader` -- sirve `data` en trozos de hasta
    `n` bytes por `read(n)` (igual que un stream real), o bloquea antes del
    primer byte si se le da un `gate` (asyncio.Event) o un `hang=True`
    (simula un proceso colgado que nunca escribe nada)."""

    def __init__(self, data: bytes = b"", *, gate: asyncio.Event | None = None, hang: bool = False) -> None:
        self._buf = data
        self._gate = gate
        self._gate_consumed = False
        self._hang = hang

    async def read(self, n: int = -1) -> bytes:
        if self._hang:
            # Nunca produce nada -- solo el timeout duro del validador debe
            # sacar al llamador de esta espera.
            await asyncio.sleep(3600)
            return b""
        if self._gate is not None and not self._gate_consumed:
            self._gate_consumed = True
            await self._gate.wait()
        if not self._buf:
            return b""
        if n < 0:
            n = len(self._buf)
        chunk, self._buf = self._buf[:n], self._buf[n:]
        return chunk


class FakeProcess:
    """Doble de `asyncio.subprocess.Process`."""

    def __init__(
        self,
        stdout: FakeStreamReader,
        stderr: FakeStreamReader,
        *,
        pid: int = 4242,
        returncode: int | None = 0,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.pid = pid
        self.returncode = returncode
        self.wait_calls = 0

    async def wait(self) -> int:
        self.wait_calls += 1
        return self.returncode or 0


def make_fake_process(stdout_bytes: bytes, stderr_bytes: bytes = b"", **kwargs) -> FakeProcess:
    return FakeProcess(FakeStreamReader(stdout_bytes), FakeStreamReader(stderr_bytes), **kwargs)


def install_fake_subprocess(monkeypatch: pytest.MonkeyPatch, factory) -> list[SimpleNamespace]:
    """Sustituye `asyncio.create_subprocess_exec` dentro del módulo bajo
    prueba. `factory(args, kwargs) -> FakeProcess` decide la respuesta;
    cada invocación queda registrada en la lista devuelta (args/kwargs
    EXACTOS que el validador pasó)."""
    calls: list[SimpleNamespace] = []

    async def fake_exec(*args, **kwargs):
        calls.append(SimpleNamespace(args=args, kwargs=kwargs))
        return factory(args, kwargs)

    monkeypatch.setattr(subprocess_verapdf.asyncio, "create_subprocess_exec", fake_exec)
    return calls


def _report_bytes(
    *,
    compliant: bool | None = None,
    profile: str = "2b",
    task_exception: bool = False,
    validation_result_as_object: bool = False,
    version: str | None = "1.30.2",
) -> bytes:
    """Construye el reporte `--format json` REAL de veraPDF (forma
    confirmada contra un binario 1.30.2 real, no solo su documentación --
    ver también `tests/fakes/fake_verapdf.py`): `buildInformation.
    releaseDetails` es una LISTA de componentes y `jobs[N].validationResult`
    es una LISTA de UN elemento (salvo que se pida la forma "objeto directo"
    con `validation_result_as_object`, que el parser también acepta)."""
    release_details = [{"id": "core", "version": version}] if version else []
    if task_exception:
        job: dict = {
            "itemDetails": {"name": "input.pdf"},
            "taskException": {
                "exception": "Couldn't parse stream",
                "exceptionMessage": "simulado: PDF no parseable",
            },
        }
    else:
        statement = (
            "PDF file is compliant with Validation Profile requirements."
            if compliant
            else "PDF file is not compliant with Validation Profile requirements."
        )
        validation_result = {
            "profileName": f"PDF/A-{profile} validation profile",
            "compliant": compliant,
            "statement": statement,
        }
        job = {
            "itemDetails": {"name": "input.pdf"},
            "validationResult": validation_result if validation_result_as_object else [validation_result],
        }
    report = {
        "report": {
            "buildInformation": {"releaseDetails": release_details},
            "jobs": [job],
        }
    }
    return json.dumps(report).encode("utf-8")


@pytest.fixture(autouse=True)
def _reset_module_semaphore(monkeypatch: pytest.MonkeyPatch) -> None:
    """El semáforo de concurrencia es un singleton A NIVEL DE MÓDULO
    (cacheado por valor de `max_concurrency`, ver docstring de
    `_get_semaphore`) -- resetearlo evita que el estado de un test se filtre
    al siguiente."""
    monkeypatch.setattr(subprocess_verapdf, "_semaphore", None)
    monkeypatch.setattr(subprocess_verapdf, "_semaphore_limit", None)


@pytest.fixture
def tmp_pdf(tmp_path) -> str:
    p = tmp_path / "documento.pdf"
    p.write_bytes(b"%PDF-1.4 contenido de prueba, no importa para estos tests")
    return str(p)


def _validator(**overrides) -> SubprocessVeraPdfValidator:
    params = {
        "binary": _BINARY,
        "timeout_seconds": 5,
        "max_concurrency": 4,
        "jvm_max_heap_mb": 256,
    }
    params.update(overrides)
    return SubprocessVeraPdfValidator(**params)


# --------------------------------------------------------------------------
# 1. No-shell: create_subprocess_exec con lista de args FIJA.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invokes_exec_never_shell_with_fixed_args(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    calls = install_fake_subprocess(monkeypatch, lambda a, k: make_fake_process(_report_bytes(compliant=True)))

    def fail_if_shell_used(*a, **k):
        raise AssertionError("create_subprocess_shell NUNCA debe invocarse (inyección de shell)")

    monkeypatch.setattr(subprocess_verapdf.asyncio, "create_subprocess_shell", fail_if_shell_used)

    validator = _validator()
    result = await validator.validate(tmp_pdf, profile="2b")

    assert result.outcome == "conformant"
    assert len(calls) == 1
    args = calls[0].args
    kwargs = calls[0].kwargs

    assert args[0] == _BINARY
    assert args[1:5] == ("--format", "json", "--flavour", "2b")
    assert len(args) == 6
    assert os.path.basename(args[5]) == "input.pdf"
    assert os.path.isabs(args[5])

    # Nada de banderas de red ni de reescritura del PDF de entrada.
    assert "--update" not in args
    assert not any(isinstance(a, str) and ("http://" in a or "https://" in a) for a in args)

    assert kwargs["stdout"] is subprocess_verapdf.asyncio.subprocess.PIPE
    assert kwargs["stderr"] is subprocess_verapdf.asyncio.subprocess.PIPE
    assert kwargs["start_new_session"] is True


# --------------------------------------------------------------------------
# 2. Whitelist de flavour/profile.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_profile",
    ["4a", "", "2B", "2b ", "2b; rm -rf /", "1a,2b", "../../etc/passwd", "2b\n--update"],
)
async def test_rejects_flavour_outside_whitelist_before_subprocess(
    monkeypatch: pytest.MonkeyPatch, tmp_pdf: str, bad_profile: str
) -> None:
    calls = install_fake_subprocess(monkeypatch, lambda a, k: make_fake_process(_report_bytes(compliant=True)))

    validator = _validator()
    result = await validator.validate(tmp_pdf, profile=bad_profile)

    assert result.outcome == "not_evaluated"
    assert calls == []  # el subprocess NUNCA se invocó
    assert bad_profile in result.detail["motivo"] or "flavour" in result.detail["motivo"]


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", sorted(subprocess_verapdf._VALID_FLAVOURS))
async def test_accepts_every_real_verapdf_flavour(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str, profile: str) -> None:
    calls = install_fake_subprocess(
        monkeypatch, lambda a, k: make_fake_process(_report_bytes(compliant=True, profile=profile))
    )

    validator = _validator()
    result = await validator.validate(tmp_pdf, profile=profile)

    assert result.outcome == "conformant"
    assert calls[0].args[4] == profile


# --------------------------------------------------------------------------
# 3. ENV scrubbeado (CRÍTICO).
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_child_env_only_has_path_and_heap_never_process_secrets(
    monkeypatch: pytest.MonkeyPatch, tmp_pdf: str
) -> None:
    # Siembra el entorno del proceso PADRE con secretos reales del servicio
    # -- si el hijo los heredara, aparecerían en `env` capturado abajo.
    monkeypatch.setenv("DATABASE_URL", "postgresql://orpycamcp:leak-me@postgres:5432/orpycamcp_db")
    monkeypatch.setenv("MINIO_SECRET_KEY", "super-secreto-minio")
    monkeypatch.setenv("KEYCLOAK_CLIENT_SECRET", "super-secreto-keycloak")
    monkeypatch.setenv("INTERNAL_SERVICE_TOKEN", "super-secreto-interno")

    calls = install_fake_subprocess(monkeypatch, lambda a, k: make_fake_process(_report_bytes(compliant=True)))

    validator = _validator(jvm_max_heap_mb=384)
    result = await validator.validate(tmp_pdf, profile="2b")

    assert result.outcome == "conformant"
    assert len(calls) == 1
    env = calls[0].kwargs["env"]

    # Solo estas dos llaves -- ni una más (ni siquiera PATH real del padre).
    assert set(env.keys()) == {"PATH", "JAVA_TOOL_OPTIONS"}
    assert env["JAVA_TOOL_OPTIONS"] == "-Xmx384m -XX:MaxMetaspaceSize=256m"
    assert env["PATH"] == subprocess_verapdf._MINIMAL_PATH

    for leaked_key in ("DATABASE_URL", "MINIO_SECRET_KEY", "KEYCLOAK_CLIENT_SECRET", "INTERNAL_SERVICE_TOKEN"):
        assert leaked_key not in env

    serialized_env = " ".join(env.values())
    for secret_value in ("leak-me", "super-secreto-minio", "super-secreto-keycloak", "super-secreto-interno"):
        assert secret_value not in serialized_env


# --------------------------------------------------------------------------
# 4. Timeout -> killpg + reap + not_evaluated.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_kills_process_group_and_reaps(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    getpgid_calls: list[int] = []
    killpg_calls: list[tuple[int, int]] = []

    monkeypatch.setattr(subprocess_verapdf.os, "getpgid", lambda pid: getpgid_calls.append(pid) or 9999)
    monkeypatch.setattr(subprocess_verapdf.os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    holder: dict[str, FakeProcess] = {}

    def factory(args, kwargs):
        proc = FakeProcess(FakeStreamReader(hang=True), FakeStreamReader(hang=True), pid=555)
        holder["proc"] = proc
        return proc

    install_fake_subprocess(monkeypatch, factory)

    validator = _validator(timeout_seconds=1)
    result = await validator.validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"
    assert "timeout" in result.detail["motivo"].lower()
    assert getpgid_calls == [555]
    assert killpg_calls == [(9999, signal.SIGKILL)]
    assert holder["proc"].wait_calls >= 1, "el proceso debe cosecharse (wait()) tras el killpg"
    assert holder["proc"].returncode == 0  # wait() nunca lanzó / se completó


@pytest.mark.asyncio
async def test_timeout_survives_process_already_gone(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    """Si el proceso ya no existe cuando se intenta matarlo (murió justo
    entre el timeout y el killpg), `ProcessLookupError` se traga -- no debe
    propagar ni impedir que se reporte `not_evaluated`."""

    def raise_lookup_error(pid: int) -> int:
        raise ProcessLookupError(pid)

    monkeypatch.setattr(subprocess_verapdf.os, "getpgid", raise_lookup_error)
    killpg_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(subprocess_verapdf.os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    install_fake_subprocess(
        monkeypatch, lambda a, k: FakeProcess(FakeStreamReader(hang=True), FakeStreamReader(hang=True))
    )

    validator = _validator(timeout_seconds=1)
    result = await validator.validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"
    assert killpg_calls == []  # nunca se llegó a invocar killpg


# --------------------------------------------------------------------------
# 5. Salida sobredimensionada -> kill + not_evaluated (no OOM, no cuelgue).
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_oversized_stdout_triggers_kill_and_not_evaluated(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    killpg_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(subprocess_verapdf.os, "getpgid", lambda pid: 7777)
    monkeypatch.setattr(subprocess_verapdf.os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    huge = b"x" * (subprocess_verapdf._MAX_OUTPUT_BYTES + 4096)

    install_fake_subprocess(monkeypatch, lambda a, k: FakeProcess(FakeStreamReader(huge), FakeStreamReader(b"")))

    validator = _validator()
    result = await validator.validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"
    assert str(subprocess_verapdf._MAX_OUTPUT_BYTES) in result.detail["motivo"]
    assert killpg_calls == [(7777, signal.SIGKILL)]


@pytest.mark.asyncio
async def test_oversized_stderr_alone_also_triggers_kill(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    """El cap aplica a CUALQUIERA de los dos streams -- un PDF adversarial
    podría inducir stderr desproporcionado en vez de stdout."""
    killpg_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(subprocess_verapdf.os, "getpgid", lambda pid: 8888)
    monkeypatch.setattr(subprocess_verapdf.os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    huge = b"e" * (subprocess_verapdf._MAX_OUTPUT_BYTES + 4096)
    install_fake_subprocess(monkeypatch, lambda a, k: FakeProcess(FakeStreamReader(b""), FakeStreamReader(huge)))

    validator = _validator()
    result = await validator.validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"
    assert killpg_calls == [(8888, signal.SIGKILL)]


# --------------------------------------------------------------------------
# 6. Parseo JSON real de veraPDF -- vía el camino completo `validate()`.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parses_conformant_report_with_version_in_agent(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    install_fake_subprocess(
        monkeypatch, lambda a, k: make_fake_process(_report_bytes(compliant=True, profile="2b", version="1.30.2"))
    )
    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "conformant"
    assert result.profile == "2b"
    assert "1.30.2" in result.validator_agent
    assert "2b" in result.validator_agent


@pytest.mark.asyncio
async def test_parses_non_conformant_report(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    install_fake_subprocess(monkeypatch, lambda a, k: make_fake_process(_report_bytes(compliant=False, profile="2b")))
    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "non_conformant"


@pytest.mark.asyncio
async def test_parses_validation_result_as_direct_object_not_list(
    monkeypatch: pytest.MonkeyPatch, tmp_pdf: str
) -> None:
    """Forma alterna tolerada: `validationResult` como objeto directo (no
    lista) -- el parser la acepta, aunque la forma REAL confirmada de
    veraPDF sea siempre la lista de un elemento."""
    install_fake_subprocess(
        monkeypatch,
        lambda a, k: make_fake_process(_report_bytes(compliant=True, validation_result_as_object=True)),
    )
    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "conformant"


@pytest.mark.asyncio
async def test_task_exception_corrupt_pdf_is_not_evaluated_not_non_conformant(
    monkeypatch: pytest.MonkeyPatch, tmp_pdf: str
) -> None:
    """Un PDF que ni siquiera pudo parsearse (`taskException`) es
    honestamente `not_evaluated` -- NO `non_conformant` (eso implicaría que
    SÍ se evaluó contra el perfil)."""
    install_fake_subprocess(monkeypatch, lambda a, k: make_fake_process(_report_bytes(task_exception=True)))
    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"
    assert "Couldn't parse stream" in result.detail["motivo"] or "no pudo parsear" in result.detail["motivo"]


@pytest.mark.asyncio
async def test_malformed_json_is_not_evaluated(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    install_fake_subprocess(monkeypatch, lambda a, k: make_fake_process(b"esto no es JSON en absoluto {{{"))
    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"
    assert "JSON" in result.detail["motivo"]


@pytest.mark.asyncio
async def test_non_utf8_output_is_not_evaluated(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    install_fake_subprocess(monkeypatch, lambda a, k: make_fake_process(b"\xff\xfe\x00\x01no-utf8"))
    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"
    assert "UTF-8" in result.detail["motivo"]


@pytest.mark.asyncio
async def test_json_not_an_object_is_not_evaluated(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    install_fake_subprocess(monkeypatch, lambda a, k: make_fake_process(b"[1, 2, 3]"))
    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"
    assert "objeto" in result.detail["motivo"]


@pytest.mark.asyncio
async def test_json_missing_report_jobs_is_not_evaluated(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    install_fake_subprocess(monkeypatch, lambda a, k: make_fake_process(b'{"report": {}}'))
    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"
    assert "report.jobs" in result.detail["motivo"]


@pytest.mark.asyncio
async def test_json_missing_compliant_field_is_not_evaluated(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    payload = {
        "report": {
            "buildInformation": {"releaseDetails": []},
            "jobs": [{"itemDetails": {}, "validationResult": [{"profileName": "x"}]}],
        }
    }
    install_fake_subprocess(monkeypatch, lambda a, k: make_fake_process(json.dumps(payload).encode()))
    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"
    assert "compliant" in result.detail["motivo"]


@pytest.mark.asyncio
async def test_json_compliant_field_wrong_type_is_not_evaluated(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    payload = {
        "report": {
            "buildInformation": {"releaseDetails": []},
            "jobs": [{"itemDetails": {}, "validationResult": [{"compliant": "yes"}]}],
        }
    }
    install_fake_subprocess(monkeypatch, lambda a, k: make_fake_process(json.dumps(payload).encode()))
    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"
    assert "booleano" in result.detail["motivo"]


@pytest.mark.asyncio
async def test_empty_output_is_not_evaluated(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    """Binario que termina sin escribir nada (crash silencioso, versión
    incompatible) -- degrada honestamente, no revienta el parser."""
    install_fake_subprocess(monkeypatch, lambda a, k: make_fake_process(b""))
    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"


# --------------------------------------------------------------------------
# 7. Semáforo de concurrencia.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_concurrency_one_serializes_invocations(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    pdf1 = tmp_path / "uno.pdf"
    pdf1.write_bytes(b"%PDF-1.4 uno")
    pdf2 = tmp_path / "dos.pdf"
    pdf2.write_bytes(b"%PDF-1.4 dos")

    invocation_order: list[int] = []
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    release_first = asyncio.Event()

    def factory(args, kwargs):
        idx = len(invocation_order)
        invocation_order.append(idx)
        if idx == 0:
            first_started.set()
            return FakeProcess(FakeStreamReader(_report_bytes(compliant=True), gate=release_first), FakeStreamReader(b""))
        second_started.set()
        return make_fake_process(_report_bytes(compliant=True))

    install_fake_subprocess(monkeypatch, factory)

    validator = _validator(max_concurrency=1)

    task1 = asyncio.ensure_future(validator.validate(str(pdf1), profile="2b"))
    await asyncio.wait_for(first_started.wait(), timeout=2)

    task2 = asyncio.ensure_future(validator.validate(str(pdf2), profile="2b"))
    # El segundo call debe quedar bloqueado en el semáforo -- nunca llega a
    # invocar el subprocess mientras el primero sigue en curso.
    await asyncio.sleep(0.05)
    assert second_started.is_set() is False
    assert invocation_order == [0]
    assert task2.done() is False

    release_first.set()
    result1 = await asyncio.wait_for(task1, timeout=2)
    await asyncio.wait_for(second_started.wait(), timeout=2)
    result2 = await asyncio.wait_for(task2, timeout=2)

    assert result1.outcome == "conformant"
    assert result2.outcome == "conformant"
    assert invocation_order == [0, 1]


@pytest.mark.asyncio
async def test_max_concurrency_two_allows_two_in_flight(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """Contraprueba de la anterior: con `max_concurrency=2`, dos
    invocaciones concurrentes SÍ pueden estar en curso al mismo tiempo (el
    semáforo limita, no serializa incondicionalmente)."""
    pdf1 = tmp_path / "uno.pdf"
    pdf1.write_bytes(b"%PDF-1.4 uno")
    pdf2 = tmp_path / "dos.pdf"
    pdf2.write_bytes(b"%PDF-1.4 dos")

    both_started = asyncio.Event()
    started_count = 0
    release = asyncio.Event()

    def factory(args, kwargs):
        nonlocal started_count
        started_count += 1
        if started_count == 2:
            both_started.set()
        return FakeProcess(FakeStreamReader(_report_bytes(compliant=True), gate=release), FakeStreamReader(b""))

    install_fake_subprocess(monkeypatch, factory)
    validator = _validator(max_concurrency=2)

    task1 = asyncio.ensure_future(validator.validate(str(pdf1), profile="2b"))
    task2 = asyncio.ensure_future(validator.validate(str(pdf2), profile="2b"))

    await asyncio.wait_for(both_started.wait(), timeout=2)
    release.set()

    result1, result2 = await asyncio.wait_for(asyncio.gather(task1, task2), timeout=2)
    assert result1.outcome == "conformant"
    assert result2.outcome == "conformant"


# --------------------------------------------------------------------------
# 8. Tempfile de invocación: 0700 + input 0600 + cleanup en `finally`.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workdir_0700_input_0600_and_removed_after_success(
    monkeypatch: pytest.MonkeyPatch, tmp_pdf: str
) -> None:
    snapshot: dict = {}

    def factory(args, kwargs):
        input_path = args[5]
        work_dir = os.path.dirname(input_path)
        snapshot["work_dir"] = work_dir
        snapshot["input_path"] = input_path
        snapshot["dir_mode"] = stat.S_IMODE(os.stat(work_dir).st_mode)
        snapshot["file_mode"] = stat.S_IMODE(os.stat(input_path).st_mode)
        snapshot["existed_during_invocation"] = os.path.isdir(work_dir)
        return make_fake_process(_report_bytes(compliant=True))

    install_fake_subprocess(monkeypatch, factory)

    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "conformant"
    assert snapshot["existed_during_invocation"] is True
    assert snapshot["dir_mode"] == 0o700
    assert snapshot["file_mode"] == 0o600
    assert not os.path.exists(snapshot["work_dir"]), "el tempdir de la invocación debe borrarse tras terminar"


@pytest.mark.asyncio
async def test_workdir_removed_even_when_subprocess_creation_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_pdf: str
) -> None:
    snapshot: dict = {}

    async def raising_exec(*args, **kwargs):
        snapshot["work_dir"] = os.path.dirname(args[5])
        raise OSError("fallo simulado de fork/exec")

    monkeypatch.setattr(subprocess_verapdf.asyncio, "create_subprocess_exec", raising_exec)

    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"  # nunca lanza (invariante 9)
    assert "work_dir" in snapshot
    assert not os.path.exists(snapshot["work_dir"])


@pytest.mark.asyncio
async def test_workdir_removed_even_on_timeout(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    monkeypatch.setattr(subprocess_verapdf.os, "getpgid", lambda pid: 1)
    monkeypatch.setattr(subprocess_verapdf.os, "killpg", lambda pgid, sig: None)

    snapshot: dict = {}

    def factory(args, kwargs):
        snapshot["work_dir"] = os.path.dirname(args[5])
        return FakeProcess(FakeStreamReader(hang=True), FakeStreamReader(hang=True))

    install_fake_subprocess(monkeypatch, factory)

    result = await _validator(timeout_seconds=1).validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"
    assert not os.path.exists(snapshot["work_dir"])


# --------------------------------------------------------------------------
# 9. Nunca lanza -- contrato del Protocol `PdfaValidator.validate`.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_never_raises_when_source_pdf_missing(tmp_path) -> None:
    validator = _validator()
    missing = str(tmp_path / "no-existe.pdf")

    result = await validator.validate(missing, profile="2b")

    assert result.outcome == "not_evaluated"


@pytest.mark.asyncio
async def test_never_raises_on_unexpected_internal_exception(monkeypatch: pytest.MonkeyPatch, tmp_pdf: str) -> None:
    """Cualquier fallo interno no previsto (aquí, el parser JSON revienta
    con un bug simulado) degrada a `not_evaluated` -- el `except Exception`
    de `validate()` es la última red, no la primera línea de defensa.

    Además (remediación #1, E10 C2b): el subprocess debe cosecharse
    (killpg + wait) en el `finally` de `_invoke`, no solo cuando el fallo
    cae en uno de los caminos ya manejados (timeout/salida excesiva/gracia)."""
    getpgid_calls: list[int] = []
    killpg_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(subprocess_verapdf.os, "getpgid", lambda pid: getpgid_calls.append(pid) or 6161)
    monkeypatch.setattr(subprocess_verapdf.os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    def boom(*args, **kwargs):
        raise RuntimeError("bug inesperado simulado en el parser")

    monkeypatch.setattr(subprocess_verapdf, "_parse_verapdf_json", boom)
    holder: dict[str, FakeProcess] = {}

    def factory(args, kwargs):
        proc = make_fake_process(_report_bytes(compliant=True), pid=6161)
        holder["proc"] = proc
        return proc

    install_fake_subprocess(monkeypatch, factory)

    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"
    assert "excepción inesperada" in result.detail["motivo"]
    # El fake ya reporta `returncode=0` (terminó normalmente) tras el
    # `drain` -- el `finally` no debe re-matar un proceso que ya cosechó su
    # propio `returncode`, así que aquí NO se espera killpg (returncode no
    # es None). Ver el test siguiente para el caso donde SÍ sigue vivo.
    assert killpg_calls == []


@pytest.mark.asyncio
async def test_cancelled_error_during_drain_is_reaped_and_propagates(
    monkeypatch: pytest.MonkeyPatch, tmp_pdf: str
) -> None:
    """Remediación #1 (E10 C2b, Media): si `asyncio.CancelledError` llega
    mientras se espera `self._drain(proc)` (p.ej. el request que disparó la
    validación se cancela), el `finally` de `_invoke` debe cosechar el
    proceso (killpg + wait) igual que en timeout -- Y la `CancelledError`
    debe seguir propagándose intacta: `CancelledError` hereda de
    `BaseException`, así que el `except Exception` de `validate()` (la red
    de "nunca lanza") NO debe (ni puede) tragársela. El fix es sobre la
    cosecha, no sobre suprimir la cancelación."""
    getpgid_calls: list[int] = []
    killpg_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(subprocess_verapdf.os, "getpgid", lambda pid: getpgid_calls.append(pid) or 3131)
    monkeypatch.setattr(subprocess_verapdf.os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    holder: dict[str, FakeProcess] = {}

    def factory(args, kwargs):
        # `returncode=None`: el proceso sigue vivo cuando llega la
        # cancelación -- exactamente el caso que debe cosechar el `finally`.
        proc = FakeProcess(FakeStreamReader(hang=True), FakeStreamReader(hang=True), pid=3131, returncode=None)
        holder["proc"] = proc
        return proc

    install_fake_subprocess(monkeypatch, factory)

    async def cancelling_drain(proc):
        raise asyncio.CancelledError()

    monkeypatch.setattr(SubprocessVeraPdfValidator, "_drain", staticmethod(cancelling_drain))

    validator = _validator()
    with pytest.raises(asyncio.CancelledError):
        await validator.validate(tmp_pdf, profile="2b")

    assert getpgid_calls == [3131]
    assert killpg_calls == [(3131, signal.SIGKILL)]
    assert holder["proc"].wait_calls >= 1, "el proceso debe cosecharse (wait()) pese a la cancelación"


@pytest.mark.asyncio
async def test_unexpected_exception_after_spawn_reaps_still_alive_process(
    monkeypatch: pytest.MonkeyPatch, tmp_pdf: str
) -> None:
    """Remediación #1: una excepción no prevista (no-timeout) entre el spawn
    y el reap normal, con el proceso TODAVÍA vivo (`returncode is None`),
    debe cosecharse en el `finally` -- y el resultado degrada honestamente a
    `not_evaluated` (el `except Exception` de `validate()` sigue siendo la
    última red)."""
    getpgid_calls: list[int] = []
    killpg_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(subprocess_verapdf.os, "getpgid", lambda pid: getpgid_calls.append(pid) or 7373)
    monkeypatch.setattr(subprocess_verapdf.os, "killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    holder: dict[str, FakeProcess] = {}

    def factory(args, kwargs):
        proc = FakeProcess(FakeStreamReader(hang=True), FakeStreamReader(hang=True), pid=7373, returncode=None)
        holder["proc"] = proc
        return proc

    install_fake_subprocess(monkeypatch, factory)

    async def raising_drain(proc):
        raise RuntimeError("bug inesperado simulado durante el drain")

    monkeypatch.setattr(SubprocessVeraPdfValidator, "_drain", staticmethod(raising_drain))

    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"
    assert getpgid_calls == [7373]
    assert killpg_calls == [(7373, signal.SIGKILL)]
    assert holder["proc"].wait_calls >= 1, "el proceso debe cosecharse (wait()) tras la excepción inesperada"


@pytest.mark.asyncio
async def test_never_raises_when_create_subprocess_exec_raises_unexpected_error(
    monkeypatch: pytest.MonkeyPatch, tmp_pdf: str
) -> None:
    async def raising_exec(*args, **kwargs):
        raise PermissionError("simulado: sin permisos para ejecutar el binario")

    monkeypatch.setattr(subprocess_verapdf.asyncio, "create_subprocess_exec", raising_exec)

    result = await _validator().validate(tmp_pdf, profile="2b")

    assert result.outcome == "not_evaluated"


def test_agent_id_uses_only_basename_of_binary_not_full_path() -> None:
    """El `validator_agent` de fallback no debe filtrar la ruta absoluta
    completa del binario (detalle de despliegue) en resultados/PREMIS."""
    validator = _validator(binary="/opt/verapdf-secreto-interno/bin/verapdf")
    agent = validator._agent_id()

    assert agent == "veraPDF (verapdf)"
    assert "/opt/verapdf-secreto-interno" not in agent
