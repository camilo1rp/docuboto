# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
`SubprocessGhostscriptMigrator`: migración de formato REAL a PDF/A vía
Ghostscript (RF-PRE-04; Decreto 2609/2012 art. 32).

**Mismo modelo de amenaza que `app.services.pdfa.subprocess_verapdf`, y por la
misma razón**: ejecuta un binario externo sobre un PDF que llegó por la subida
de un usuario cualquiera de cualquier tenant — input NO confiable. Cada
precaución de allí se repite aquí porque el vector es idéntico:

  - `create_subprocess_exec` (nunca `_shell`) con lista de argumentos FIJA.
  - Binario por ruta ABSOLUTA validada en la factory antes de instanciar.
  - El perfil se valida contra una lista CERRADA antes de tocar el subprocess.
  - ENV scrubbeado: el hijo NO hereda `os.environ` (que aquí contiene
    `DATABASE_URL`, `MINIO_SECRET_KEY`, `INTERNAL_SERVICE_TOKEN`…).
  - Grupo de proceso propio + `killpg` + `wait()` garantizado en `finally`,
    incluida la cancelación del request.
  - Semáforo de concurrencia a nivel de proceso: Ghostscript sobre un PDF
    grande es caro en CPU y memoria, y una ráfaga de migraciones concurrentes
    tumbaría el contenedor.
  - Directorio temporal propio (0700), borrado incondicionalmente.
  - Timeout duro y salida acotada.
  - NUNCA lanza: cualquier fallo degrada a `outcome="fallido"` con su motivo.

**Dos precauciones PROPIAS de convertir, que el validador no necesita:**

1. `-dSAFER` (y nunca `-dNOSAFER`): sin él, Ghostscript admite operadores
   PostScript que leen y escriben archivos del sistema — un documento
   adversarial dejaría de ser un documento y pasaría a ser un programa con
   acceso al contenedor. Es la diferencia entre convertir y ejecutar.
2. **La salida se escribe en el directorio aislado y se lee desde ahí**, jamás
   sobre el archivo de origen: el original es lo que la norma manda conservar,
   y un convertidor que escribiera en su sitio destruiría precisamente lo que
   se está preservando.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import tempfile

from app.services.migracion.types import MigracionResult

logger = logging.getLogger(__name__)

# Perfiles PDF/A que Ghostscript sabe producir, por su `-dPDFACompatibilityLevel`
# (1, 2 o 3). Lista CERRADA: un `PDFA_PROFILE` mal configurado no se cuela como
# argumento arbitrario. Las variantes `a`/`u` exigen texto etiquetado o Unicode
# garantizado, que una conversión ciega NO puede prometer — se admiten solo las
# `b` (conformidad de apariencia visual), que es lo que una migración
# automática puede sostener honestamente.
_PERFIL_A_NIVEL: dict[str, int] = {"1b": 1, "2b": 2, "3b": 3}

_MINIMAL_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_MAX_OUTPUT_BYTES = 5 * 1024 * 1024
_REAP_GRACE_SECONDS = 5

_semaphore: asyncio.Semaphore | None = None
_semaphore_limit: int | None = None


def _get_semaphore(limit: int) -> asyncio.Semaphore:
    global _semaphore, _semaphore_limit
    if _semaphore is None or _semaphore_limit != limit:
        _semaphore = asyncio.Semaphore(limit)
        _semaphore_limit = limit
    return _semaphore


def _fallido(perfil: str, agente: str, motivo: str, **extra) -> MigracionResult:
    # Sin `contenido`: un resultado fallido no lleva bytes, para que nadie los
    # guarde «por si acaso» — son justamente los que no se pueden dar por buenos.
    return MigracionResult(
        outcome="fallido",
        formato_destino=f"PDF/A-{perfil}",
        herramienta=agente,
        detalle={"motivo": motivo, **extra},
    )


class SubprocessGhostscriptMigrator:
    """Migrador a PDF/A vía Ghostscript. Contrato `FormatMigrator`: nunca lanza."""

    def __init__(
        self,
        *,
        binary: str,
        timeout_seconds: int = 120,
        max_concurrency: int = 1,
    ) -> None:
        self._binary = binary
        self._timeout = timeout_seconds
        self._semaphore = _get_semaphore(max(1, max_concurrency))

    def _agente(self) -> str:
        return f"ghostscript ({os.path.basename(self._binary)})"

    async def migrar(self, origen_path: str, *, perfil: str) -> MigracionResult:
        agente = self._agente()
        try:
            if perfil not in _PERFIL_A_NIVEL:
                return _fallido(
                    perfil,
                    agente,
                    f"perfil PDF/A {perfil!r} no es producible por Ghostscript "
                    f"({sorted(_PERFIL_A_NIVEL)}); las variantes 'a'/'u' exigen texto "
                    "etiquetado o Unicode garantizado, que una conversión automática "
                    "no puede prometer",
                )
            async with self._semaphore:
                return await self._migrar_aislado(origen_path, perfil=perfil, agente=agente)
        except Exception as exc:  # noqa: BLE001 — contrato: NUNCA lanza
            logger.warning("SubprocessGhostscriptMigrator: fallo inesperado: %s", exc)
            return _fallido(perfil, agente, f"excepción inesperada en el migrador: {exc}")

    async def _migrar_aislado(
        self, origen_path: str, *, perfil: str, agente: str
    ) -> MigracionResult:
        work_dir = tempfile.mkdtemp(prefix="gs-pdfa-")
        os.chmod(work_dir, 0o700)
        entrada = os.path.join(work_dir, "input.pdf")
        salida = os.path.join(work_dir, "output.pdf")
        try:
            shutil.copyfile(origen_path, entrada)
            os.chmod(entrada, 0o600)
            return await self._invocar(entrada, salida, perfil=perfil, agente=agente)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    async def _invocar(
        self, entrada: str, salida: str, *, perfil: str, agente: str
    ) -> MigracionResult:
        args = [
            self._binary,
            f"-dPDFA={_PERFIL_A_NIVEL[perfil]}",
            "-dBATCH",
            "-dNOPAUSE",
            # `-dSAFER` NO es opcional: sin él un PDF adversarial puede leer y
            # escribir archivos del contenedor a través de operadores PostScript.
            "-dSAFER",
            "-sColorConversionStrategy=UseDeviceIndependentColor",
            "-sDEVICE=pdfwrite",
            "-dPDFACompatibilityPolicy=1",
            f"-sOutputFile={salida}",
            entrada,
        ]
        env = {"PATH": _MINIMAL_PATH}

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        try:
            try:
                out, err = await asyncio.wait_for(
                    proc.communicate(), timeout=self._timeout
                )
            except TimeoutError:
                return _fallido(
                    perfil, agente, f"la conversión excedió {self._timeout}s y se abortó"
                )
            if len(out) + len(err) > _MAX_OUTPUT_BYTES:
                return _fallido(perfil, agente, "salida del convertidor desproporcionada")
            if proc.returncode != 0:
                return _fallido(
                    perfil,
                    agente,
                    "Ghostscript terminó con error",
                    exit_code=proc.returncode,
                    stderr=err.decode("utf-8", "replace")[:500],
                )
            if not os.path.isfile(salida) or os.path.getsize(salida) == 0:
                # Ghostscript puede salir con 0 y no producir nada útil ante
                # entradas patológicas. Un archivo vacío tomado por buena copia
                # de preservación sería la peor forma de fallar: silenciosa.
                return _fallido(perfil, agente, "el convertidor no produjo ningún archivo")
            with open(salida, "rb") as fh:
                contenido = fh.read()
            return MigracionResult(
                outcome="migrado",
                formato_destino=f"PDF/A-{perfil}",
                herramienta=agente,
                contenido=contenido,
                detalle={"bytes": len(contenido)},
            )
        finally:
            if proc.returncode is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
                try:
                    await asyncio.wait_for(proc.wait(), timeout=_REAP_GRACE_SECONDS)
                except TimeoutError:
                    logger.warning("Ghostscript no murió tras SIGKILL (pid=%s)", proc.pid)
