# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Migración de formato contra la obsolescencia — E10, RF-PRE-04 (Decreto
2609/2012 art. 32; OAIS).

La norma pide tres cosas y las tres son invariantes de este módulo:

1. **Conservar el original.** El archivo de origen no se toca, no se marca, no
   se reemplaza: la migración produce un artefacto DERIVADO que convive con él.
   Un sistema que sustituye el original ha perdido la evidencia el día que
   alguien discuta si la conversión alteró el contenido.
2. **Registrar versión origen y destino** con su cadena de procedencia
   (`preservacion_migracion`, migración 015) y su evento PREMIS `MIGRACION` —
   un tipo que llevaba en el vocabulario desde el principio y que hasta hoy
   **nadie escribía**.
3. **Verificar la integridad antes y después.** El SHA-256 del origen se
   **recalcula** sobre los bytes descargados y se compara con el almacenado
   ANTES de convertir: migrar un archivo ya corrupto propagaría la corrupción a
   la copia de preservación, y le daría además el aspecto de ser la buena.

**Y una cuarta que la norma no dice y el oficio sí**: convertir a PDF/A y no
comprobar que el resultado ES PDF/A produce una «copia de preservación» que
nadie ha verificado. Aquí el derivado pasa por el validador; si no es conforme,
la migración es `fallido` y los bytes se descartan. Si el validador no está
disponible, el resultado es `no_verificado` y **tampoco se promueve**: existe el
registro del intento, no una copia con etiqueta de buena.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import uuid

import asyncpg
from orpycamcp_common import audit

from app.core.config import settings
from app.repositories.file_repository import FileRepository
from app.repositories.preservacion_repository import PreservacionRepository
from app.services.migracion.factory import get_format_migrator
from app.services.pdfa.factory import get_pdfa_validator

logger = logging.getLogger(__name__)

_SERVICE = "storage-service"


class MigradorNoDisponibleError(Exception):
    """No hay convertidor en este despliegue.

    Clase propia y no un resultado más: es lo que separa «este documento no se
    deja convertir» de «esta instalación no puede convertir nada», y son dos
    problemas con dos soluciones distintas — depurar el archivo, o instalar la
    herramienta. Colapsarlos manda a mirar el archivo equivocado."""


class FileNotFoundForMigrationError(Exception):
    pass


class FixityMismatchError(Exception):
    """El archivo de origen ya no coincide con su hash almacenado.

    Aborta la migración: convertir un archivo corrupto produce una copia de
    preservación corrupta, y con mejor aspecto que el original."""


class FormatMigrationService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._files = FileRepository(conn, tenant_slug)
        self._preservacion = PreservacionRepository(conn, tenant_slug)

    async def migrar_a_pdfa(
        self,
        file_id: uuid.UUID,
        *,
        perfil: str,
        actor: uuid.UUID | None,
        descargar,
        subir,
    ) -> dict:
        """Migra un archivo a PDF/A y devuelve el registro de la migración.

        `descargar` y `subir` se **inyectan** en vez de resolverse aquí: este
        módulo decide la política de preservación (fixity, verificación,
        procedencia) y no debe además saber cómo se hablan MinIO y la tabla de
        archivos. Así el orden de los pasos —que es donde está la corrección—
        se puede probar sin levantar un almacén.
        """
        migrador = get_format_migrator(settings)
        if migrador is None:
            raise MigradorNoDisponibleError(
                "Este despliegue no tiene convertidor de formato configurado "
                "(FORMAT_MIGRATOR/GHOSTSCRIPT_BINARY). No se migra nada: un migrador "
                "de mentira tendría que inventarse el archivo."
            )

        origen = await self._files.get_by_id_any_state(file_id)
        if origen is None:
            raise FileNotFoundForMigrationError(str(file_id))

        # ── 1. Fixity ANTES ────────────────────────────────────────────────
        contenido = await descargar(origen)
        sha_origen = hashlib.sha256(contenido).hexdigest()
        if sha_origen != origen.sha256:
            # Se deja constancia del intento: un archivo que falla la fixity es
            # justo el que hay que ir a mirar, y sin registro nadie sabría que
            # se detectó.
            await self._registrar(
                file_id_origen=file_id,
                file_id_destino=None,
                formato_origen=origen.mime_type or "desconocido",
                formato_destino=f"PDF/A-{perfil}",
                sha256_origen=origen.sha256,
                sha256_destino=None,
                herramienta="—",
                estado="fallido",
                motivo="fixity_origen_no_coincide",
                actor=actor,
            )
            raise FixityMismatchError(
                f"El SHA-256 del archivo {file_id} no coincide con el almacenado: "
                "no se migra un original que ya está alterado."
            )

        # ── 2. Convertir, sobre un temporal propio ─────────────────────────
        work_dir = tempfile.mkdtemp(prefix="migracion-")
        origen_path = os.path.join(work_dir, "origen.pdf")
        try:
            with open(origen_path, "wb") as fh:
                fh.write(contenido)
            resultado = await migrador.migrar(origen_path, perfil=perfil)
        finally:
            try:
                os.remove(origen_path)
                os.rmdir(work_dir)
            except OSError:
                pass

        if resultado.outcome != "migrado" or not resultado.contenido:
            return await self._registrar(
                file_id_origen=file_id,
                file_id_destino=None,
                formato_origen=origen.mime_type or "desconocido",
                formato_destino=resultado.formato_destino,
                sha256_origen=sha_origen,
                sha256_destino=None,
                herramienta=resultado.herramienta,
                estado="fallido",
                motivo=str(resultado.detalle.get("motivo", "conversión fallida"))[:500],
                actor=actor,
            )

        # ── 3. Verificar que el derivado ES lo que dice ser ────────────────
        estado, motivo = await self._verificar(resultado.contenido, perfil=perfil)
        if estado != "migrado":
            # Los bytes se descartan: una copia de preservación no verificada,
            # guardada, es una copia con etiqueta de buena.
            return await self._registrar(
                file_id_origen=file_id,
                file_id_destino=None,
                formato_origen=origen.mime_type or "desconocido",
                formato_destino=resultado.formato_destino,
                sha256_origen=sha_origen,
                sha256_destino=None,
                herramienta=resultado.herramienta,
                estado=estado,
                motivo=motivo,
                actor=actor,
            )

        # ── 4. Guardar el DERIVADO. El original sigue intacto ──────────────
        sha_destino = hashlib.sha256(resultado.contenido).hexdigest()
        destino_id = await subir(origen, resultado.contenido, resultado.formato_destino)

        return await self._registrar(
            file_id_origen=file_id,
            file_id_destino=destino_id,
            formato_origen=origen.mime_type or "desconocido",
            formato_destino=resultado.formato_destino,
            sha256_origen=sha_origen,
            sha256_destino=sha_destino,
            herramienta=resultado.herramienta,
            estado="migrado",
            motivo=None,
            actor=actor,
        )

    async def _verificar(self, contenido: bytes, *, perfil: str) -> tuple[str, str | None]:
        """Comprueba que el derivado es PDF/A conforme.

        Tres desenlaces y ninguno se confunde: conforme (`migrado`), no
        conforme (`fallido`, la conversión no sirvió) y no evaluado
        (`no_verificado`, no había con qué comprobarlo). El tercero **no** se
        promueve a copia de preservación."""
        validador = get_pdfa_validator(settings)
        work_dir = tempfile.mkdtemp(prefix="migracion-verif-")
        destino_path = os.path.join(work_dir, "destino.pdf")
        try:
            with open(destino_path, "wb") as fh:
                fh.write(contenido)
            veredicto = await validador.validate(destino_path, profile=perfil)
        finally:
            try:
                os.remove(destino_path)
                os.rmdir(work_dir)
            except OSError:
                pass

        if veredicto.outcome == "conformant":
            return "migrado", None
        if veredicto.outcome == "non_conformant":
            return "fallido", "el derivado no resultó PDF/A conforme"
        return (
            "no_verificado",
            "se convirtió, pero no hay validador PDF/A disponible para comprobarlo: "
            "el derivado NO se promueve a copia de preservación",
        )

    async def _registrar(self, *, actor: uuid.UUID | None, **datos) -> dict:
        """Persiste la migración, su evento PREMIS y su asiento **en una sola
        transacción**: el registro de procedencia y el rastro de preservación
        son el mismo acto, y un fallo entre ambos dejaría un derivado del que
        nadie sabría de dónde salió."""
        async with self._conn.transaction():
            fila = await self._preservacion.insert_migracion({**datos, "iniciada_por": actor})
            await self._preservacion.insert_evento(
                {
                    "object_ref": str(datos["file_id_origen"]),
                    "tipo": "MIGRACION",
                    # El vocabulario de `preservacion_evento` ya distinguía los
                    # tres estados; se reutiliza en vez de inventar otro.
                    "resultado": {
                        "migrado": "ok",
                        "fallido": "fallo",
                        "no_verificado": "no_evaluado",
                    }[datos["estado"]],
                    "hash_before": datos["sha256_origen"],
                    "hash_after": datos["sha256_destino"],
                    "detalle": {
                        "formato_origen": datos["formato_origen"],
                        "formato_destino": datos["formato_destino"],
                        "file_id_destino": str(datos["file_id_destino"] or ""),
                        "motivo": datos["motivo"],
                    },
                },
                agente=datos["herramienta"],
            )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="file.migracion_formato",
                object_type="file",
                object_ref=str(datos["file_id_origen"]),
                actor=str(actor) if actor else None,
                payload={
                    "estado": datos["estado"],
                    "formato_destino": datos["formato_destino"],
                    "sha256_origen": datos["sha256_origen"],
                    "sha256_destino": datos["sha256_destino"],
                    "herramienta": datos["herramienta"],
                },
            )
        return dict(fila)
