# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Migración de formato contra la obsolescencia — E10, RF-PRE-04.

Lo que se prueba es lo que hace que una copia de preservación merezca ese
nombre:

* **El original nunca se toca.** La migración produce un DERIVADO; un sistema
  que sustituye el original ha perdido la evidencia el día que alguien discuta
  si la conversión alteró el contenido.
* **Fixity ANTES de convertir.** Migrar un archivo ya corrupto propagaría la
  corrupción a la copia de preservación, y le daría además el aspecto de ser la
  buena.
* **El derivado se verifica.** Convertir a PDF/A y no comprobar que el
  resultado ES PDF/A produce una copia que nadie ha validado. No conforme →
  `fallido`. Sin validador → `no_verificado`, y **tampoco se promueve**.
* **No hay migrador stub.** Un validador puede decir «no evaluado»; un migrador
  falso tendría que inventarse el archivo. Sin convertidor: `503`.
* **Los intentos fallidos se registran.** «Se intentó y no se pudo» es lo que
  dice qué documentos siguen esperando una migración que no sale.
"""

import hashlib
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.format_migration_service import (
    FixityMismatchError,
    FormatMigrationService,
    MigradorNoDisponibleError,
)
from app.services.migracion.types import MigracionResult
from app.services.pdfa.types import PdfaResult

ORIGEN_BYTES = b"%PDF-1.4 original"
DERIVADO_BYTES = b"%PDF-1.4 derivado PDF/A"
SHA_ORIGEN = hashlib.sha256(ORIGEN_BYTES).hexdigest()


class _Registro:
    """Doble de la fila devuelta por el repositorio: se comporta como un
    `Record` (dict-like) porque el servicio hace `dict(fila)`."""

    def __init__(self, datos):
        self._d = dict(datos)

    def keys(self):
        return self._d.keys()

    def __getitem__(self, k):
        return self._d[k]

    def __iter__(self):
        return iter(self._d)


class _FileRecord:
    def __init__(self, sha256=SHA_ORIGEN):
        self.id = uuid.uuid4()
        self.bucket = "b"
        self.object_key = "k/origen.pdf"
        self.filename = "origen.pdf"
        self.mime_type = "application/pdf"
        self.sha256 = sha256


@pytest.fixture
def mock_conn():
    """Conexión doble. Estos tests ejercen el SERVICIO directamente: el
    endpoint solo lo envuelve, y montar la app entera escondería el ORDEN de
    los pasos, que es justo lo que aquí se mide."""
    conn = AsyncMock()
    # `audit.append` encadena hashes leyendo el anterior; con un doble que no
    # devuelva nada usable, el registro revienta por una razón ajena al test.
    conn.fetchval = AsyncMock(return_value=None)
    conn.transaction = _tx
    return conn


class _tx:
    """`async with conn.transaction():` sobre un doble."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _servicio(conn=None):
    return FormatMigrationService(conn or AsyncMock(), "demo")


def _migrador(outcome="migrado", contenido=DERIVADO_BYTES, motivo=None):
    resultado = MigracionResult(
        outcome=outcome,
        formato_destino="PDF/A-2b",
        herramienta="ghostscript (gs)",
        contenido=contenido if outcome == "migrado" else None,
        detalle={"motivo": motivo} if motivo else {},
    )
    doble = AsyncMock()
    doble.migrar = AsyncMock(return_value=resultado)
    return patch("app.services.format_migration_service.get_format_migrator", return_value=doble)


def _validador(outcome="conformant"):
    doble = AsyncMock()
    doble.validate = AsyncMock(
        return_value=PdfaResult(outcome=outcome, profile="2b", validator_agent="veraPDF")
    )
    return patch("app.services.format_migration_service.get_pdfa_validator", return_value=doble)


def _entorno(servicio, record):
    # El repositorio de archivos se sustituye ENTERO por un doble para poder
    # afirmar que sobre el original solo se LEE: si alguien añadiera mañana un
    # «marcar como sustituido» sobre la fila de origen, este test se pondría en
    # rojo — que es exactamente lo que debe pasar, porque la norma manda
    # conservar el original, no anotarlo.
    servicio._files = AsyncMock()
    servicio._files.get_by_id_any_state = AsyncMock(return_value=record)
    registros = []

    async def _insert(datos):
        registros.append(datos)
        return _Registro(
            {
                "id": uuid.uuid4(),
                "iniciada_at": "2026-08-29T10:00:00+00:00",
                **datos,
                "iniciada_por": datos.get("iniciada_por"),
            }
        )

    servicio._preservacion.insert_migracion = AsyncMock(side_effect=_insert)
    servicio._preservacion.insert_evento = AsyncMock()
    return registros


async def _descargar_ok(_record):
    return ORIGEN_BYTES


@pytest.mark.asyncio
async def test_sin_convertidor_NO_se_inventa_una_copia_de_preservacion() -> None:
    """No hay migrador «stub»: un migrador falso tendría que producir el
    archivo, y el único que puede producir sin la herramienta es uno
    inventado."""
    servicio = _servicio()
    subir = AsyncMock()

    with patch("app.services.format_migration_service.get_format_migrator", return_value=None):
        with pytest.raises(MigradorNoDisponibleError):
            await servicio.migrar_a_pdfa(
                uuid.uuid4(), perfil="2b", actor=None, descargar=_descargar_ok, subir=subir
            )

    subir.assert_not_awaited()


@pytest.mark.asyncio
async def test_la_fixity_se_comprueba_ANTES_de_convertir(mock_conn) -> None:
    """Un original alterado no se migra: la copia saldría corrupta y con mejor
    aspecto que el original."""
    servicio = _servicio(mock_conn)
    registros = _entorno(servicio, _FileRecord(sha256="f" * 64))
    subir = AsyncMock()

    with _migrador() as migrador_factory, pytest.raises(FixityMismatchError):
        await servicio.migrar_a_pdfa(
            uuid.uuid4(), perfil="2b", actor=None, descargar=_descargar_ok, subir=subir
        )
        migrador = migrador_factory.return_value
        migrador.migrar.assert_not_awaited()

    subir.assert_not_awaited()
    # Y queda constancia del intento: un archivo que falla la fixity es justo
    # el que hay que ir a mirar.
    assert registros and registros[0]["motivo"] == "fixity_origen_no_coincide"


@pytest.mark.asyncio
async def test_una_migracion_correcta_deja_el_original_INTACTO(mock_conn) -> None:
    servicio = _servicio(mock_conn)
    _entorno(servicio, _FileRecord())
    destino_id = uuid.uuid4()
    subir = AsyncMock(return_value=destino_id)

    with _migrador(), _validador("conformant"):
        salida = await servicio.migrar_a_pdfa(
            uuid.uuid4(), perfil="2b", actor=None, descargar=_descargar_ok, subir=subir
        )

    assert salida["estado"] == "migrado"
    assert salida["file_id_destino"] == destino_id
    # Los DOS hashes, que es el criterio de aceptación de RF-PRE-04.
    assert salida["sha256_origen"] == SHA_ORIGEN
    assert salida["sha256_destino"] == hashlib.sha256(DERIVADO_BYTES).hexdigest()
    # El original NO se toca por ninguna vía: la única llamada al repositorio
    # de archivos es la lectura.
    llamadas = {n.split(".")[0] for n, _, _ in servicio._files.mock_calls if n}
    assert llamadas == {"get_by_id_any_state"}, llamadas


@pytest.mark.asyncio
async def test_un_derivado_NO_conforme_no_se_guarda(mock_conn) -> None:
    """Convertir a PDF/A y no comprobarlo produce una copia que nadie validó."""
    servicio = _servicio(mock_conn)
    registros = _entorno(servicio, _FileRecord())
    subir = AsyncMock()

    with _migrador(), _validador("non_conformant"):
        salida = await servicio.migrar_a_pdfa(
            uuid.uuid4(), perfil="2b", actor=None, descargar=_descargar_ok, subir=subir
        )

    assert salida["estado"] == "fallido"
    subir.assert_not_awaited()
    assert registros[0]["file_id_destino"] is None


@pytest.mark.asyncio
async def test_sin_validador_queda_NO_VERIFICADO_y_tampoco_se_promueve(mock_conn) -> None:
    """Tercer estado, distinto de los otros dos: se convirtió, pero nadie pudo
    comprobar que el resultado sea lo que dice ser."""
    servicio = _servicio(mock_conn)
    registros = _entorno(servicio, _FileRecord())
    subir = AsyncMock()

    with _migrador(), _validador("not_evaluated"):
        salida = await servicio.migrar_a_pdfa(
            uuid.uuid4(), perfil="2b", actor=None, descargar=_descargar_ok, subir=subir
        )

    assert salida["estado"] == "no_verificado"
    subir.assert_not_awaited()
    assert "promueve a copia de preservación" in registros[0]["motivo"]


@pytest.mark.asyncio
async def test_una_conversion_fallida_TAMBIEN_se_registra(mock_conn) -> None:
    servicio = _servicio(mock_conn)
    registros = _entorno(servicio, _FileRecord())
    subir = AsyncMock()

    with _migrador(outcome="fallido", motivo="Ghostscript terminó con error"), _validador():
        salida = await servicio.migrar_a_pdfa(
            uuid.uuid4(), perfil="2b", actor=None, descargar=_descargar_ok, subir=subir
        )

    assert salida["estado"] == "fallido"
    assert registros[0]["motivo"] == "Ghostscript terminó con error"
    subir.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_evento_PREMIS_lleva_los_dos_hashes(mock_conn) -> None:
    """`MIGRACION` llevaba en el vocabulario desde el principio y nadie lo
    escribía. Un evento de migración sin el hash de antes y el de después no
    demuestra que el contenido sobrevivió al viaje."""
    servicio = _servicio(mock_conn)
    _entorno(servicio, _FileRecord())

    with _migrador(), _validador("conformant"):
        await servicio.migrar_a_pdfa(
            uuid.uuid4(),
            perfil="2b",
            actor=None,
            descargar=_descargar_ok,
            subir=AsyncMock(return_value=uuid.uuid4()),
        )

    evento = servicio._preservacion.insert_evento.await_args.args[0]
    assert evento["tipo"] == "MIGRACION"
    assert evento["resultado"] == "ok"
    assert evento["hash_before"] == SHA_ORIGEN
    assert evento["hash_after"] == hashlib.sha256(DERIVADO_BYTES).hexdigest()


# ── La fábrica: la asimetría con el validador es deliberada ─────────────────


class _Settings:
    def __init__(self, **kw):
        self.format_migrator = kw.get("format_migrator", "none")
        for k, v in kw.items():
            setattr(self, k, v)


def test_sin_binario_la_fabrica_devuelve_None_y_no_un_stub() -> None:
    """El punto entero del módulo. La factory gemela del validador degrada a un
    stub honesto porque «no evaluado» es un resultado real; aquí no existe el
    equivalente, así que la ausencia se propaga como `None` hasta el 503."""
    from app.services.migracion.factory import get_format_migrator

    assert get_format_migrator(_Settings(format_migrator="none")) is None
    assert get_format_migrator(_Settings(format_migrator="ghostscript")) is None
    assert (
        get_format_migrator(
            _Settings(format_migrator="ghostscript", ghostscript_binary="gs")
        )
        is None
    ), "una ruta relativa no debe activar el convertidor"
    assert (
        get_format_migrator(
            _Settings(format_migrator="ghostscript", ghostscript_binary="/no/existe/gs")
        )
        is None
    )


def test_un_migrador_desconocido_no_migra_en_vez_de_reventar() -> None:
    from app.services.migracion.factory import get_format_migrator

    assert get_format_migrator(_Settings(format_migrator="inventado")) is None


@pytest.mark.asyncio
async def test_el_perfil_se_valida_contra_una_lista_CERRADA() -> None:
    """`-dPDFA=<n>` se construye a partir del perfil: un valor no reconocido no
    puede llegar a los argumentos del subprocess. Y las variantes `a`/`u` se
    rechazan a propósito — exigen texto etiquetado o Unicode garantizado, que
    una conversión automática no puede prometer."""
    from app.services.migracion.subprocess_ghostscript import SubprocessGhostscriptMigrator

    migrador = SubprocessGhostscriptMigrator(binary="/bin/true")
    for perfil in ("2a", "3u", "../etc/passwd", ""):
        resultado = await migrador.migrar("/tmp/x.pdf", perfil=perfil)
        assert resultado.outcome == "fallido"
        assert resultado.contenido is None
        # Se afirma sobre el MOTIVO y no solo sobre el desenlace: con un
        # binario que no convierte nada, el fallo llegaría igual por otro
        # camino («no produjo ningún archivo») y el test seguiría verde aunque
        # la lista de perfiles dejara de estar cerrada.
        assert "no es producible por Ghostscript" in resultado.detalle["motivo"], perfil


@pytest.mark.asyncio
async def test_el_convertidor_corre_con_dSAFER() -> None:
    """Sin `-dSAFER`, Ghostscript admite operadores PostScript que leen y
    escriben archivos del sistema: un documento adversarial dejaría de ser un
    documento y pasaría a ser un programa con acceso al contenedor."""
    from unittest.mock import MagicMock

    from app.services.migracion.subprocess_ghostscript import SubprocessGhostscriptMigrator

    capturado = {}

    async def _spawn(*args, **kwargs):
        capturado["args"] = args
        capturado["env"] = kwargs.get("env")
        proc = MagicMock()
        proc.communicate = AsyncMock(return_value=(b"", b""))
        proc.returncode = 1  # corta antes de leer la salida
        return proc

    migrador = SubprocessGhostscriptMigrator(binary="/usr/bin/gs")
    with patch("asyncio.create_subprocess_exec", _spawn):
        await migrador.migrar(__file__, perfil="2b")

    assert "-dSAFER" in capturado["args"]
    assert "-dPDFA=2" in capturado["args"]
    # El hijo NO hereda el entorno del padre, que aquí trae DATABASE_URL,
    # MINIO_SECRET_KEY e INTERNAL_SERVICE_TOKEN.
    assert set(capturado["env"]) == {"PATH"}
