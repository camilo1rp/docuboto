# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
`SubprocessVeraPdfValidator`: validador PDF/A REAL vía veraPDF CLI (E10
Increment C2b, RF-PRE-02, ISO 19005).

Este módulo ejecuta un binario Java externo sobre un PDF que llegó por
`POST /files/upload` de un usuario cualquiera de cualquier tenant —
**input NO confiable**. El modelo de seguridad de abajo NO es opcional; cada
punto responde a un vector concreto de un PDF/entrada adversarial:

  - `asyncio.create_subprocess_exec` (NUNCA `create_subprocess_shell`) con
    una lista de argumentos FIJA — cero interpolación de shell, cero
    inyección de argumentos.
  - El binario es la ruta ABSOLUTA de `settings.verapdf_binary`, validada
    por la factory (`app.services.pdfa.factory.get_pdfa_validator`) antes de
    construir esta clase — si no es absoluta/ejecutable, ni se instancia.
  - `--flavour` (perfil PDF/A) se valida contra la lista CERRADA de
    "flavours" reales de veraPDF antes de tocar el subprocess: un
    `PDFA_PROFILE` mal configurado nunca se cuela como argumento arbitrario.
  - ENV SCRUBBEADO: el hijo NUNCA hereda `os.environ` (que en este proceso
    incluye `DATABASE_URL`, `MINIO_SECRET_KEY`, `KEYCLOAK_CLIENT_SECRET`,
    `INTERNAL_SERVICE_TOKEN`...). Solo se le da un `PATH` mínimo (para que
    el wrapper `verapdf` encuentre `java`) y el cap de heap de la JVM.
  - Grupo de proceso propio (`start_new_session=True`) + `killpg` en
    timeout/salida-excesiva, seguido SIEMPRE de `proc.wait()` para cosechar
    — la JVM de veraPDF puede spawnear hijos; matar solo el pid raíz deja
    procesos huérfanos vivos.
  - Semáforo de concurrencia a nivel de proceso (`settings.verapdf_max_concurrency`):
    cada invocación es una JVM de ~150-500MB; sin límite, ráfagas de AIPs
    concurrentes de tenants distintos harían OOM al contenedor entero.
  - Heap acotado vía `JAVA_TOOL_OPTIONS=-Xmx<N>m -XX:MaxMetaspaceSize=<N>m` --
    el heap NO es toda la memoria nativa de la JVM (metaspace/hilos/etc
    quedan fuera de `-Xmx`), así que también se acota el metaspace. El techo
    DURO real de memoria lo sigue imponiendo el límite de memoria del
    contenedor en el deploy -- esto es un segundo cinturón barato, no la
    garantía.
  - Cosecha del subprocess GARANTIZADA: el ciclo de vida tras el spawn vive
    en un `try/finally` que hace `killpg`+`wait()` si el proceso sigue vivo
    al salir por CUALQUIER camino -- incluida `asyncio.CancelledError`
    (cancelación del request) o una excepción inesperada entre el spawn y el
    parseo, no solo los caminos ya manejados de timeout/salida-excesiva/
    gracia post-EOF. Sin esto, cancelar una validación en curso (p.ej.
    shutdown, timeout de un caller aguas arriba) deja la JVM huérfana viva.
  - El PDF se copia a un directorio temporal PROPIO de la invocación, creado
    con `tempfile.mkdtemp` y `chmod 0700`, y se borra (`shutil.rmtree`) en
    `finally` — no confiamos en los permisos del tempfile que entregó
    `runner.py` (compartido con el resto del proceso de empaquetado), esta
    clase construye su propio directorio aislado de principio a fin.
  - Timeout duro (`settings.verapdf_timeout_seconds`) sobre la lectura
    completa de stdout+stderr, no solo sobre el arranque del proceso.
  - Salida ACOTADA (`_MAX_OUTPUT_BYTES`): un PDF adversarial podría inducir
    a veraPDF a emitir un reporte JSON desproporcionado; se lee con límite
    duro y, si se excede, se aborta (kill+reap) en vez de agotar memoria.
  - Solo se pide/parsea `--format json` — JAMÁS XML (evita XXE en el parser
    del reporte). El parseo es defensivo: cualquier forma inesperada del
    JSON degrada a `not_evaluated`, nunca lanza ni asume conformidad.
  - Esta clase NUNCA lanza (contrato `PdfaValidator.validate`): cualquier
    fallo no previsto (I/O, parseo, subprocess) degrada a
    `PdfaResult(outcome="not_evaluated", ...)`.
  - RF-FIR-15 (honestidad): `not_evaluated` no es "passed". Si el binario
    falla, cuelga, o el JSON no se entiende, jamás se informa conformidad.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import tempfile
from typing import Any

from app.services.pdfa.types import PdfaOutcome, PdfaResult

logger = logging.getLogger(__name__)

# "Flavours" reales de veraPDF para perfiles PDF/A (ISO 19005 partes 1-3) --
# lista CERRADA: cualquier `PDFA_PROFILE` que no calce con esto se rechaza
# ANTES de construir los argumentos del subprocess (defensa en profundidad:
# `pdfa_profile` viene de config, no de contenido del PDF, pero un valor mal
# configurado tampoco debe colarse sin validar).
_VALID_FLAVOURS = frozenset({"1a", "1b", "2a", "2b", "2u", "3a", "3b", "3u"})

# PATH mínimo para que el wrapper `verapdf` (script shell) encuentre el
# binario `java` en la imagen de deploy (Debian slim + JRE headless, ver
# services/storage-service/Dockerfile target=production) -- NUNCA se usa el
# PATH heredado del proceso padre (podría apuntar a herramientas que no
# deberían estar al alcance del subprocess).
_MINIMAL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

# Techo duro sobre stdout/stderr leídos del subprocess. El reporte JSON de
# veraPDF para un PDF normal pesa unos pocos KB; unos MB ya es generoso. Un
# PDF adversarial que induzca un reporte desproporcionado se corta acá en
# vez de agotar memoria del contenedor.
_MAX_OUTPUT_BYTES = 5 * 1024 * 1024  # 5 MiB

# Cuánto esperar a que el proceso termine de morir tras SIGKILL a su grupo
# antes de darlo por zombie-irrecuperable (solo logueamos, no bloqueamos
# indefinidamente el request).
_REAP_GRACE_SECONDS = 5

# Semáforo de concurrencia GLOBAL AL PROCESO (no por-instancia): la factory
# construye un `SubprocessVeraPdfValidator` nuevo por invocación de
# `empaquetar_aip` (ver `app/services/pdfa/factory.py`), así que un semáforo
# de instancia no serializaría nada entre requests concurrentes. Se cachea
# por valor de `max_concurrency` (fijo durante la vida del proceso, viene de
# `Settings` cargada una sola vez al arrancar) -- sin lock explícito: no hay
# ningún `await` entre el check y la creación, así que no hay ventana de
# carrera dentro del event loop único de asyncio.
_semaphore: asyncio.Semaphore | None = None
_semaphore_limit: int | None = None


def _get_semaphore(limit: int) -> asyncio.Semaphore:
    global _semaphore, _semaphore_limit
    if _semaphore is None or _semaphore_limit != limit:
        _semaphore = asyncio.Semaphore(limit)
        _semaphore_limit = limit
    return _semaphore


def _not_evaluated(profile: str, agent: str, motivo: str, **extra: Any) -> PdfaResult:
    return PdfaResult(
        outcome="not_evaluated",
        profile=profile,
        validator_agent=agent,
        detail={"motivo": motivo, **extra},
    )


async def _read_capped(stream: asyncio.StreamReader) -> tuple[bytes, bool]:
    """Lee `stream` hasta EOF con un techo duro de `_MAX_OUTPUT_BYTES`.

    Devuelve `(bytes_leidos, truncado)`. Nunca acumula más allá del límite
    en memoria -- corta la lectura en cuanto lo cruza."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return b"".join(chunks), False
        total += len(chunk)
        if total > _MAX_OUTPUT_BYTES:
            return b"".join(chunks), True
        chunks.append(chunk)


def _parse_verapdf_json(raw: bytes, *, profile: str, fallback_agent: str) -> PdfaResult:
    """Parsea el reporte `--format json` de veraPDF -- SOLO JSON, jamás XML
    (evita XXE). Cualquier forma inesperada degrada a `not_evaluated`."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return _not_evaluated(profile, fallback_agent, f"salida de veraPDF no es UTF-8 válido: {exc}")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return _not_evaluated(profile, fallback_agent, f"salida de veraPDF no es JSON válido: {exc}")

    if not isinstance(data, dict):
        return _not_evaluated(
            profile, fallback_agent, "salida JSON de veraPDF con forma inesperada (no es un objeto)"
        )

    # `report.buildInformation.releaseDetails` es una LISTA de componentes
    # (core/validation-model/apps), no un único objeto con "version" -- se
    # toma la entrada "core" como identificador de versión (confirmado
    # contra la salida REAL de veraPDF 1.30.2, no solo su documentación).
    version: str | None = None
    report = data.get("report")
    if isinstance(report, dict):
        build_info = report.get("buildInformation")
        if isinstance(build_info, dict):
            release_details = build_info.get("releaseDetails")
            if isinstance(release_details, list):
                for entry in release_details:
                    if isinstance(entry, dict) and entry.get("id") == "core":
                        raw_version = entry.get("version")
                        if isinstance(raw_version, str):
                            version = raw_version
                        break

    agent = f"veraPDF {version} (perfil {profile})" if version else f"{fallback_agent} (perfil {profile})"

    # Un PDF adversarial/corrupto (no solo "no conforme PDF/A") hace que
    # veraPDF ni siquiera pueda PARSEARLO -- el job trae `taskException` en
    # vez de `validationResult` (confirmado contra la salida real de
    # veraPDF con un PDF malformado). No es "non_conformant" (eso implica
    # que SÍ se evaluó contra el perfil): es honestamente `not_evaluated`,
    # con el motivo real del fallo de parseo en vez del genérico de abajo.
    try:
        job = data["report"]["jobs"][0]
    except (KeyError, IndexError, TypeError):
        return _not_evaluated(profile, agent, "estructura JSON de veraPDF inesperada (sin report.jobs[0])")

    if isinstance(job, dict) and "taskException" in job and "validationResult" not in job:
        task_exc = job.get("taskException")
        motivo = (
            task_exc.get("exceptionMessage")
            if isinstance(task_exc, dict)
            else "veraPDF no pudo procesar el archivo"
        )
        return _not_evaluated(profile, agent, f"veraPDF no pudo parsear/procesar el PDF: {motivo}")

    try:
        # `validationResult` es una LISTA de un elemento en veraPDF real
        # (confirmado contra su salida real, no solo su documentación) --
        # pero se acepta también la forma "objeto directo" por si una
        # versión futura/distinta la simplifica; cualquier otra forma cae
        # al `except` de abajo (not_evaluated, nunca asume nada).
        raw_result = job["validationResult"]
        validation_result = raw_result[0] if isinstance(raw_result, list) else raw_result
        compliant = validation_result["compliant"]
    except (KeyError, IndexError, TypeError):
        return _not_evaluated(
            profile,
            agent,
            "estructura JSON de veraPDF inesperada "
            "(se esperaba report.jobs[0].validationResult[0].compliant)",
        )

    if not isinstance(compliant, bool):
        return _not_evaluated(profile, agent, "campo 'compliant' de veraPDF no es booleano")

    outcome: PdfaOutcome = "conformant" if compliant else "non_conformant"
    return PdfaResult(
        outcome=outcome,
        profile=profile,
        validator_agent=agent,
        detail={
            "statement": validation_result.get("statement"),
            "profileName": validation_result.get("profileName"),
        },
    )


class SubprocessVeraPdfValidator:
    """Validador PDF/A real: invoca el CLI `veraPDF` vía subprocess.

    Solo la factory (`app.services.pdfa.factory.get_pdfa_validator`)
    construye esta clase, y solo tras confirmar que `binary` es una ruta
    absoluta a un ejecutable existente -- ver el docstring del módulo para
    el modelo de seguridad completo."""

    def __init__(
        self,
        *,
        binary: str,
        timeout_seconds: int,
        max_concurrency: int,
        jvm_max_heap_mb: int,
        jvm_max_metaspace_mb: int = 256,
    ) -> None:
        self._binary = binary
        self._timeout_seconds = timeout_seconds
        self._jvm_max_heap_mb = jvm_max_heap_mb
        self._jvm_max_metaspace_mb = jvm_max_metaspace_mb
        self._semaphore = _get_semaphore(max_concurrency)

    def _agent_id(self) -> str:
        return f"veraPDF ({os.path.basename(self._binary)})"

    async def validate(self, pdf_path: str, *, profile: str) -> PdfaResult:
        agent = self._agent_id()
        try:
            if profile not in _VALID_FLAVOURS:
                return _not_evaluated(
                    profile,
                    agent,
                    f"perfil PDF/A {profile!r} no es un flavour veraPDF reconocido "
                    f"({sorted(_VALID_FLAVOURS)})",
                )
            async with self._semaphore:
                return await self._validate_isolated(pdf_path, profile=profile, agent=agent)
        except Exception as exc:  # noqa: BLE001 — contrato: NUNCA lanza
            logger.warning("SubprocessVeraPdfValidator: fallo inesperado: %s", exc)
            return _not_evaluated(profile, agent, f"excepción inesperada en el validador subprocess: {exc}")

    async def _validate_isolated(self, pdf_path: str, *, profile: str, agent: str) -> PdfaResult:
        """Copia `pdf_path` a un directorio temporal PROPIO (0700) de esta
        invocación antes de invocar veraPDF -- aislado del tempfile
        compartido de `runner.py`, borrado incondicionalmente en `finally`."""
        work_dir = tempfile.mkdtemp(prefix="verapdf-")
        os.chmod(work_dir, 0o700)
        input_path = os.path.join(work_dir, "input.pdf")
        try:
            shutil.copyfile(pdf_path, input_path)
            os.chmod(input_path, 0o600)
            return await self._invoke(input_path, profile=profile, agent=agent)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    async def _invoke(self, input_path: str, *, profile: str, agent: str) -> PdfaResult:
        # Args ACOTADOS: lista fija, sin --update, sin banderas de red, nada
        # derivado del CONTENIDO del PDF (input_path es nuestro propio
        # tempfile, profile ya se validó contra la whitelist arriba).
        args = [self._binary, "--format", "json", "--flavour", profile, input_path]
        env = {
            "PATH": _MINIMAL_PATH,
            "JAVA_TOOL_OPTIONS": (
                f"-Xmx{self._jvm_max_heap_mb}m -XX:MaxMetaspaceSize={self._jvm_max_metaspace_mb}m"
            ),
        }

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,  # grupo de proceso propio -> killpg limpio
        )

        # Desde acá el proceso está vivo -- el `finally` es la red que
        # GARANTIZA la cosecha (killpg+wait) sin importar por qué camino se
        # sale del bloque: los `return` explícitos de abajo (timeout,
        # salida excesiva, gracia post-EOF) ya dejan `proc.returncode`
        # asignado por su propio `wait()`, así que la guarda
        # `returncode is None` hace este `finally` IDEMPOTENTE (no
        # doble-kill) -- pero también cubre los dos caminos que antes NO
        # cosechaban: `asyncio.CancelledError` (hereda de `BaseException`,
        # no la captura el `except Exception` de `validate()`; se deja
        # propagar tal cual, este `finally` solo cosecha) y cualquier otra
        # excepción inesperada entre el spawn y el parseo.
        try:
            try:
                stdout_data, stdout_truncated, _stderr_data, stderr_truncated = await asyncio.wait_for(
                    self._drain(proc), timeout=self._timeout_seconds
                )
            except TimeoutError:
                await self._kill_and_reap(proc)
                return _not_evaluated(profile, agent, f"timeout de veraPDF tras {self._timeout_seconds}s")

            # Chequear la salida excesiva ANTES de esperar a que el proceso
            # termine por sí mismo: si se truncó, dejamos de leer el pipe a
            # propósito, así que el hijo puede quedar bloqueado escribiendo
            # en él para siempre -- esperar su salida natural aquí lo
            # confundiría con "no terminó tras cerrar su salida" en vez de
            # reportar la causa real (salida desproporcionada, probable
            # entrada adversarial).
            if stdout_truncated or stderr_truncated:
                await self._kill_and_reap(proc)
                return _not_evaluated(
                    profile, agent, f"salida de veraPDF excede el límite de {_MAX_OUTPUT_BYTES} bytes"
                )

            if proc.returncode is None:
                # Ambos streams llegaron a EOF pero el proceso aún no
                # reportó salida (ventana rara) -- se le da un margen corto
                # de gracia antes de tratarlo como colgado.
                try:
                    await asyncio.wait_for(proc.wait(), timeout=_REAP_GRACE_SECONDS)
                except TimeoutError:
                    await self._kill_and_reap(proc)
                    return _not_evaluated(profile, agent, "veraPDF no terminó tras cerrar su salida")

            return _parse_verapdf_json(stdout_data, profile=profile, fallback_agent=agent)
        finally:
            if proc.returncode is None:  # huérfano por cancelación o excepción inesperada
                await self._kill_and_reap(proc)

    @staticmethod
    async def _drain(proc: asyncio.subprocess.Process) -> tuple[bytes, bool, bytes, bool]:
        """Drena stdout y stderr EN PARALELO -- leerlos en secuencia puede
        interbloquear si el hijo llena el buffer del pipe que no se está
        leyendo mientras espera que se vacíe el otro.

        En cuanto CUALQUIERA de los dos cruza `_MAX_OUTPUT_BYTES` se corta de
        inmediato (se cancela la lectura pendiente del otro stream) en vez de
        esperar a que ambos lleguen a EOF -- si no, un hijo que deja de leer
        la respuesta y se bloquea escribiendo en el pipe ya lleno (porque
        dejamos de leerlo tras cruzar el límite) mantendría la otra lectura
        colgada hasta el timeout duro, disfrazando un "salida excesiva" como
        un simple "timeout"."""
        assert proc.stdout is not None
        assert proc.stderr is not None
        stdout_task: asyncio.Task[tuple[bytes, bool]] = asyncio.ensure_future(_read_capped(proc.stdout))
        stderr_task: asyncio.Task[tuple[bytes, bool]] = asyncio.ensure_future(_read_capped(proc.stderr))
        pending: set[asyncio.Task[tuple[bytes, bool]]] = {stdout_task, stderr_task}
        stdout_data, stdout_trunc = b"", False
        stderr_data, stderr_trunc = b"", False

        while pending:
            done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                data, truncated = task.result()
                if task is stdout_task:
                    stdout_data, stdout_trunc = data, truncated
                else:
                    stderr_data, stderr_trunc = data, truncated
            if stdout_trunc or stderr_trunc:
                for task in pending:
                    task.cancel()
                break

        return stdout_data, stdout_trunc, stderr_data, stderr_trunc

    @staticmethod
    async def _kill_and_reap(proc: asyncio.subprocess.Process) -> None:
        """SIGKILL a todo el GRUPO de proceso (la JVM de veraPDF puede
        spawnear hijos) y cosecha (`wait()`) para no dejar zombies."""
        try:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=_REAP_GRACE_SECONDS)
        except TimeoutError:
            logger.error(
                "veraPDF pid=%s no terminó tras SIGKILL a su grupo de proceso -- posible zombie",
                proc.pid,
            )
