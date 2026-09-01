# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Plantillas y borradores de Salida (paridad legado).

Ciclo del borrador: crear (opc. desde plantilla) → editar → aprobar → RADICAR
(genera el radicado real reutilizando la radicación). Hasta radicar no hay número.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg
from fastapi import HTTPException

from app.clients.storage_client import (
    RenderFallidoError,
    RenderNoDisponibleError,
    render_documento,
    upload_artefacto,
)
from app.core.combinacion import SEGUNDA_PASADA, combinar, desconocidos, marcadores
from app.repositories.borrador_repository import BorradorRepository
from app.schemas.borrador import (
    BorradorCreate,
    BorradorResponse,
    BorradorUpdate,
    CombinarRequest,
    CombinarResponse,
    PlantillaCreate,
    PlantillaResponse,
)
from app.schemas.document import DocumentCreate, DocumentResponse
from app.services.contexto_combinacion import ContextoCombinacion

logger = logging.getLogger(__name__)


def _json_obj(raw: object) -> dict:
    """asyncpg devuelve JSONB como `str`; se tolera `dict` por si hay codec."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            cargado = json.loads(raw)
        except ValueError:
            return {}
        return cargado if isinstance(cargado, dict) else {}
    return {}


@dataclass(frozen=True)
class _DocumentoProducido:
    """El PDF/A ya generado y ya subido, con lo que hace falta para anexarlo."""

    file_id: uuid.UUID
    sha256: str | None
    file_size: int | None
    formato: str


def _bo(row, faltantes: list[str] | None = None) -> BorradorResponse:
    return BorradorResponse(**dict(row), faltantes=faltantes or [])


def _marcadores_desconocidos(cuerpo: str) -> HTTPException:
    """422 con la lista, no un 'no válido' a secas.

    Quien escribe la plantilla necesita saber QUÉ marcador sobra: un rechazo sin
    la lista obliga a buscarlo a ojo en un cuerpo de varias páginas, y el camino
    corto entonces es borrar marcadores buenos hasta que pase.
    """
    faltan = desconocidos(cuerpo)
    return HTTPException(
        status_code=422,
        detail={
            "error": "marcador_desconocido",
            "detail": (
                "La plantilla usa marcadores que no existen en el vocabulario: "
                + ", ".join(f"*{m}*" for m in faltan)
            ),
            "marcadores": faltan,
            "status": 422,
        },
    )


def _not_found(detail: str) -> HTTPException:
    return HTTPException(
        status_code=404, detail={"error": "not_found", "detail": detail, "status": 404}
    )


class BorradorService:
    def __init__(
        self,
        conn: asyncpg.Connection,
        tenant_slug: str = "",
        tenant_client: Any = None,
    ) -> None:
        self._conn = conn
        self._repo = BorradorRepository(conn)
        self._slug = tenant_slug
        self._tenant_client = tenant_client

    # -- plantillas ----------------------------------------------------

    async def crear_plantilla(self, payload: PlantillaCreate) -> PlantillaResponse:
        # RF-PRO-01: el marcador desconocido se rechaza AL GUARDAR, no al usar.
        # En el legado no fallaba nunca: se quedaba impreso en el oficio que
        # recibía el ciudadano, y para entonces ya no había a quién preguntarle.
        if desconocidos(payload.cuerpo):
            raise _marcadores_desconocidos(payload.cuerpo)
        row = await self._repo.insert_plantilla(payload.model_dump())
        return PlantillaResponse(**dict(row))

    async def listar_plantillas(self, activo: bool | None) -> list[PlantillaResponse]:
        return [PlantillaResponse(**dict(r)) for r in await self._repo.list_plantillas(activo)]

    async def combinar_plantilla(
        self,
        plantilla_id: uuid.UUID,
        payload: CombinarRequest,
        *,
        max_clearance: int,
        funcionario: dict[str, Any] | None = None,
    ) -> CombinarResponse:
        """Devuelve el cuerpo de la plantilla YA combinado, con sus faltantes.

        Existe porque la pantalla necesita enseñar el texto resuelto en el
        editor antes de guardar nada, y la combinación no puede bajar al
        navegador: un marcador resuelto en el cliente es un dato que el cliente
        puede cambiar, y el documento que da fe no puede depender de eso (D-05).
        """
        pl = await self._repo.get_plantilla(plantilla_id)
        if pl is None:
            raise _not_found("Plantilla no encontrada")
        contexto = await ContextoCombinacion(self._conn, self._slug).construir(
            radicado_padre_id=payload.radicado_padre_id,
            max_clearance=max_clearance,
            dependencia_codigo=payload.dest_dept_code,
            funcionario=funcionario,
            tenant_client=self._tenant_client,
        )
        resultado = combinar(pl["cuerpo"], contexto)
        return CombinarResponse(cuerpo=resultado.texto, faltantes=resultado.faltantes)

    async def borrar_plantilla(self, plantilla_id: uuid.UUID) -> None:
        if not await self._repo.delete_plantilla(plantilla_id):
            raise _not_found(f"Plantilla {plantilla_id} no encontrada")

    # -- borradores ----------------------------------------------------

    async def crear(
        self,
        payload: BorradorCreate,
        creado_por: uuid.UUID | None,
        *,
        max_clearance: int,
        funcionario: dict[str, Any] | None = None,
    ) -> BorradorResponse:
        """Crea el borrador y, si viene de plantilla, COMBINA los campos.

        `max_clearance` es obligatorio y sin default a propósito: el contexto de
        combinación lee el radicado padre, y un default que significara «sin
        restricción» sería fail-OPEN — el mismo modo de fallo que dejó
        `get/set_disposition` sin gate.
        """
        cuerpo = payload.cuerpo
        faltantes: list[str] = []

        if cuerpo is None and payload.plantilla_id is not None:
            pl = await self._repo.get_plantilla(payload.plantilla_id)
            if pl is None:
                raise _not_found("Plantilla no encontrada")
            # Aquí estaba la brecha: `cuerpo = pl["cuerpo"]` copiaba la plantilla
            # LITERAL, marcadores incluidos. La plantilla era texto muerto.
            contexto = await ContextoCombinacion(self._conn, self._slug).construir(
                radicado_padre_id=payload.radicado_padre_id,
                max_clearance=max_clearance,
                dependencia_codigo=payload.dest_dept_code,
                funcionario=funcionario,
                tenant_client=self._tenant_client,
            )
            resultado = combinar(pl["cuerpo"], contexto)
            cuerpo, faltantes = resultado.texto, resultado.faltantes

        row = await self._repo.insert_borrador(
            {
                "doc_type": payload.doc_type,
                "subject": payload.subject,
                "dest_dept": payload.dest_dept,
                "cuerpo": cuerpo,
                "plantilla_id": payload.plantilla_id,
                "radicado_padre_id": payload.radicado_padre_id,
                "creado_por": creado_por,
            }
        )
        return _bo(row, faltantes)

    async def get(self, borrador_id: uuid.UUID) -> BorradorResponse:
        row = await self._repo.get_borrador(borrador_id)
        if row is None:
            raise _not_found(f"Borrador {borrador_id} no encontrado")
        return _bo(row)

    async def listar(self, estado: str | None) -> list[BorradorResponse]:
        return [_bo(r) for r in await self._repo.list_borradores(estado)]

    async def _require_editable(self, borrador_id: uuid.UUID):
        row = await self._repo.get_borrador(borrador_id)
        if row is None:
            raise _not_found(f"Borrador {borrador_id} no encontrado")
        if row["estado"] == "radicado":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "borrador_radicado",
                    "detail": "El borrador ya fue radicado",
                    "status": 409,
                },
            )
        return row

    async def editar(self, borrador_id: uuid.UUID, payload: BorradorUpdate) -> BorradorResponse:
        await self._require_editable(borrador_id)
        row = await self._repo.update_borrador(borrador_id, payload.model_dump(exclude_unset=True))
        return _bo(row)

    async def aprobar(self, borrador_id: uuid.UUID) -> BorradorResponse:
        row = await self._require_editable(borrador_id)
        if row["estado"] != "borrador":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "estado_invalido",
                    "detail": f"No se puede aprobar desde '{row['estado']}'",
                    "status": 409,
                },
            )
        return _bo(await self._repo.set_estado(borrador_id, "aprobado"))

    async def previsualizar(
        self, borrador_id: uuid.UUID, *, max_clearance: int
    ) -> tuple[bytes, list[str]]:
        """El documento tal como saldrá, SIN persistir nada (RF-PRO-05).

        Es lo que permite ver antes de gastar el número. `*RAD_S*` se queda a la
        vista en la previsualización a propósito: todavía no existe, y sustituirlo
        por un número de ejemplo enseñaría un documento que no es el que se va a
        producir — justo lo contrario de para qué sirve previsualizar.
        """
        row = await self._repo.get_borrador(borrador_id)
        if row is None:
            raise _not_found(f"Borrador {borrador_id} no encontrado")

        contexto = _json_obj(row["combinacion"])
        if not contexto:
            contexto = await ContextoCombinacion(self._conn, self._slug).construir(
                radicado_padre_id=row["radicado_padre_id"],
                max_clearance=max_clearance,
                dependencia_codigo=None,
                tenant_client=self._tenant_client,
            )
        resultado = combinar(row["cuerpo"] or "", contexto)
        rendido = await render_documento(
            tenant_slug=self._slug,
            cuerpo_html=resultado.texto,
            pagina=self._pagina(contexto, row, numero=None),
        )
        return rendido.contenido, resultado.faltantes

    async def radicar(
        self,
        borrador_id: uuid.UUID,
        document_service,
        registered_by: uuid.UUID | None,
        *,
        max_clearance: int,
    ) -> DocumentResponse:
        """Convierte el borrador en radicado real (reutiliza la radicación)."""
        row = await self._repo.get_borrador(borrador_id)
        if row is None:
            raise _not_found(f"Borrador {borrador_id} no encontrado")
        if row["estado"] == "radicado":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "borrador_radicado",
                    "detail": "El borrador ya fue radicado",
                    "status": 409,
                },
            )
        # La aprobación es REQUISITO, y hasta ahora solo la exigía el navegador.
        #
        # El ciclo documentado en la cabecera de este módulo es crear → editar →
        # aprobar → RADICAR, y la pantalla de borradores lo respeta
        # (`canRadicar = estado === "aprobado"`). Pero el servicio solo
        # comprobaba que no estuviera ya radicado, así que un `POST
        # /borradores/{id}/radicar` directo saltaba la aprobación y consumía el
        # número igualmente. Un control que vive solo en el cliente no es un
        # control: el cliente es la parte hostil (D-05).
        #
        # No rompe nada de lo que hoy funciona — la única interfaz que llama a
        # esta ruta no la ofrece sin aprobar — y convierte la marca de
        # aprobación en lo que su nombre dice que es.
        if row["estado"] != "aprobado":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "borrador_sin_aprobar",
                    "detail": (
                        "El borrador debe aprobarse antes de radicarlo: radicar "
                        "consume un número inmutable."
                    ),
                    "status": 409,
                },
            )
        # ── EL ORDEN, y es la mitad del diseño ─────────────────────────────
        #
        #   1. combinar (1.ª pasada)
        #   2. RENDERIZAR                 ← nada irreversible consumido aún
        #   3. subir a storage
        #   4. crear el radicado          ← NÚMERO, irreversible
        #   5. 2.ª pasada *RAD_S* + re-render + subir   (solo si hace falta)
        #   6. anexar
        #
        # El paso 2 va antes del 4 justamente para que un fallo de render NO
        # gaste un consecutivo. Un número no se devuelve: la ley lo hace
        # inmutable, así que un hueco en la secuencia es permanente y hay que
        # explicarlo a un auditor. Mismo orden que usa la radicación de correo:
        # binarios primero, número después.
        contexto = _json_obj(row["combinacion"])
        if not contexto:
            contexto = await ContextoCombinacion(self._conn, self._slug).construir(
                radicado_padre_id=row["radicado_padre_id"],
                max_clearance=max_clearance,
                dependencia_codigo=None,
                tenant_client=self._tenant_client,
            )

        documento: _DocumentoProducido | None = None
        cuerpo = row["cuerpo"] or ""
        usa_diferidos = bool(SEGUNDA_PASADA & set(marcadores(cuerpo)))

        if cuerpo.strip():
            documento = await self._producir(cuerpo, contexto, row, numero=None)

        payload = DocumentCreate(
            doc_type=row["doc_type"],
            subject=row["subject"],
            dest_dept=row["dest_dept"],
            observations=row["cuerpo"],
            metadata={"from_borrador": str(borrador_id)},
        )
        result = await document_service.create(payload, registered_by)

        if documento is not None:
            if usa_diferidos:
                # Segunda pasada: el número no existía cuando se combinó. Se
                # vuelve a renderizar porque el documento debe llevar su propio
                # número impreso (Ac. AGN 060/2001 art. 10).
                #
                # **Riesgo residual, declarado**: este render ocurre YA con el
                # número consumido. La primera pasada probó que el motor
                # funciona y que este cuerpo se deja pintar, así que lo único
                # que cambia es una cadena sustituida; pero si aun así fallara,
                # el radicado queda sin documento y hay que reintentar el
                # anexado, no la radicación. Se prefiere eso a pedir el número
                # antes, que dejaría el consecutivo huérfano en el caso mucho
                # más probable de que falle el render inicial.
                contexto = {
                    **contexto,
                    "RAD_S": result.tracking_number,
                    "F_RAD_S": result.registered_at.date().isoformat(),
                }
                try:
                    documento = await self._producir(
                        cuerpo, contexto, row, numero=result.tracking_number
                    )
                except (RenderNoDisponibleError, RenderFallidoError):
                    logger.exception(
                        "El radicado %s se creo pero su documento no se pudo regenerar "
                        "con el numero; queda sin anexo.",
                        result.tracking_number,
                    )
                    documento = None

        if documento is not None:
            await document_service.adjuntar_documento_producido(
                result.id,
                file_id=documento.file_id,
                filename=f"{result.tracking_number}.pdf",
                file_size=documento.file_size,
            )
            await self._repo.set_documento(
                borrador_id,
                file_id=documento.file_id,
                sha256=documento.sha256,
                formato=documento.formato,
                combinacion=contexto,
            )

        await self._repo.set_estado(borrador_id, "radicado", radicado_id=result.id)
        return result

    @staticmethod
    def _pagina(contexto: dict, row, *, numero: str | None) -> dict[str, Any]:
        """Membrete, pie y número (RF-PRO-04; Ac. AGN 060/2001 art. 10).

        Un solo sitio para los dos caminos —previsualizar y radicar—: si la
        previsualización compusiera la página por su cuenta, enseñaría un
        documento distinto del que se produce, que es peor que no previsualizar.
        """
        return {
            "entidad": contexto.get("NOM_E"),
            "dependencia": contexto.get("DEPENDENCIA") or row["dest_dept"],
            "radicado": numero or "",
            "fecha": contexto.get("F_HOY", ""),
            "pie": " · ".join(
                str(contexto[c])
                for c in ("DIR_E", "TEL_E", "MAIL_E", "WEB_E")
                if contexto.get(c)
            ),
        }

    async def _producir(
        self, cuerpo: str, contexto: dict, row, *, numero: str | None
    ) -> _DocumentoProducido:
        """Combina lo que falte, renderiza y sube. No toca la secuencia."""
        resultado = combinar(cuerpo, contexto, diferidos=numero is not None)
        rendido = await render_documento(
            tenant_slug=self._slug,
            cuerpo_html=resultado.texto,
            pagina=self._pagina(contexto, row, numero=numero),
        )
        subido = await upload_artefacto(
            tenant_slug=self._slug,
            filename=f"{numero or 'borrador'}.pdf",
            mime_type="application/pdf",
            data=rendido.contenido,
        )
        return _DocumentoProducido(
            file_id=subido.file_id,
            sha256=subido.sha256,
            file_size=subido.file_size,
            formato=rendido.formato,
        )
