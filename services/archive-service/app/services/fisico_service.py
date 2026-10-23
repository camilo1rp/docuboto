# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Servicio del archivo físico (E17, ADR-017).

Topología de ubicaciones (con ruta derivada), unidades de conservación con
signatura topográfica derivada de la ruta + código, y vínculo N:M expediente↔unidad.
"""

from __future__ import annotations

import logging
import uuid
import xml.etree.ElementTree as ET
from datetime import UTC, datetime

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.repositories.archive_repository import ArchiveRepository
from app.repositories.fisico_repository import FisicoRepository
from app.schemas.fisico import (
    ExpedienteEnUnidad,
    ExpedienteUnidadLink,
    FuidCabecera,
    FuidResponse,
    FuidRow,
    MovimientoResponse,
    PrestamoCreate,
    PrestamoResponse,
    PrestatarioElegible,
    PrestatarioExterno,
    PrestatarioInterno,
    UbicacionCreate,
    UbicacionResponse,
    UbicacionUpdate,
    UnidadCreate,
    UnidadDeExpediente,
    UnidadResponse,
)

_FUID_NS = "urn:orpycamcp:fuid:v1"
_SERVICE = "archive-service"


def _fuid_to_xml(
    fuid: FuidResponse,
    *,
    indice_version: int | None = None,
    indice_firma_id: uuid.UUID | None = None,
    indice_sha256: str | None = None,
) -> str:
    """XML canónico (C14N) del FUID. Los tres `indice_*` (AREA-07-04) solo los pasa
    `recibir()`: el acta de entrega ata así la firma del índice que entregó DENTRO de
    lo que se hashea y sella. Sin ellos el XML es idéntico al de siempre (la vista
    previa `GET /fuid` y las actas anteriores no cambian)."""
    root = ET.Element(f"{{{_FUID_NS}}}fuid", attrib={"generado": fuid.generado_ts.isoformat()})
    # Cabecera del formato (Ac. AGN 042/2002, Anexo). El XML salía sin ella: una
    # lista de filas sin decir de qué entidad ni de qué oficina es el inventario
    # no es un FUID, y el receptor de una transferencia no tiene cómo saber qué
    # está recibiendo.
    cab = ET.SubElement(root, f"{{{_FUID_NS}}}cabecera")
    ET.SubElement(cab, f"{{{_FUID_NS}}}entidadProductora").text = (
        fuid.cabecera.entidad_productora
    )
    ET.SubElement(cab, f"{{{_FUID_NS}}}unidadAdministrativa").text = (
        fuid.cabecera.unidad_administrativa or ""
    )
    ET.SubElement(cab, f"{{{_FUID_NS}}}oficinaProductora").text = (
        fuid.cabecera.oficina_productora or ""
    )
    ET.SubElement(cab, f"{{{_FUID_NS}}}objeto").text = fuid.cabecera.objeto or ""
    if indice_firma_id is not None:
        ind = ET.SubElement(
            cab,
            f"{{{_FUID_NS}}}indiceElectronico",
            attrib={
                "version": str(indice_version) if indice_version is not None else "",
                "firmaId": str(indice_firma_id),
                "sha256": indice_sha256 or "",
            },
        )
        ind.text = None
    for r in fuid.rows:
        e = ET.SubElement(root, f"{{{_FUID_NS}}}registro", attrib={"orden": str(r.orden)})
        ET.SubElement(e, f"{{{_FUID_NS}}}expediente").text = r.expediente_code
        ET.SubElement(e, f"{{{_FUID_NS}}}asunto").text = r.asunto
        ET.SubElement(e, f"{{{_FUID_NS}}}serie").text = r.serie_code or ""
        ET.SubElement(e, f"{{{_FUID_NS}}}signatura").text = r.signatura or ""
        ET.SubElement(e, f"{{{_FUID_NS}}}unidad").text = r.unidad_tipo or ""
        ET.SubElement(e, f"{{{_FUID_NS}}}folios").text = str(r.num_folios or "")
        ET.SubElement(e, f"{{{_FUID_NS}}}soporte").text = r.soporte or ""
        ET.SubElement(e, f"{{{_FUID_NS}}}fechaInicial").text = r.fecha_inicial.isoformat()
        ET.SubElement(e, f"{{{_FUID_NS}}}fechaFinal").text = (
            r.fecha_final.isoformat() if r.fecha_final else ""
        )
        ET.SubElement(e, f"{{{_FUID_NS}}}frecuenciaConsulta").text = r.frecuencia_consulta
    return ET.canonicalize(ET.tostring(root, encoding="unicode"))


def _jsonable(valor):
    """Valor apto para el `payload` JSON del asiento.

    `capacidad_ml` llega como `Decimal` desde asyncpg y `json.dumps` no sabe
    serializarlo; un asiento que revienta al escribirse tumbaría la corrección
    entera, que es lo contrario de lo que debe hacer una traza.
    """
    if valor is None or isinstance(valor, (str, int, float, bool)):
        return valor
    return str(valor)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error": "conflict", "detail": detail, "status": 409},
    )


def _not_found(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "not_found", "detail": detail, "status": 404},
    )


def _error(code: int, error: str, detail: str) -> HTTPException:
    """Error estructurado `{error, detail, status}` con código propio (C11)."""
    return HTTPException(
        status_code=code, detail={"error": error, "detail": detail, "status": code}
    )


def _mask_documento(numero: str) -> str:
    """`****1234`: el asiento de auditoría NO guarda el documento completo (la fila
    sí, con finalidad de custodia: Ley 1581/2012)."""
    n = numero.strip()
    return "****" + n[-4:] if len(n) > 4 else "****"


logger = logging.getLogger(__name__)

#: Tope de filas del inventario documental.
#:
#: El FUID no paginaba ni acotaba, y sobre un archivo real son decenas de miles
#: de expedientes, cada uno con su recuento de consultas resuelto por una
#: subconsulta sobre el registro de auditoría; la variante XML además construye
#: el documento entero en memoria.
#:
#: Alto a propósito: un inventario legítimo es grande, y recortarlo de más
#: produciría un FUID INCOMPLETO con aspecto de correcto, que en un instrumento
#: archivístico es peor que negarse.
MAX_FILAS_FUID = 50_000


class FisicoService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._repo = FisicoRepository(conn, tenant_slug)
        self._archive = ArchiveRepository(conn, tenant_slug)

    # -- ubicaciones ---------------------------------------------------

    async def create_ubicacion(
        self, payload: UbicacionCreate, *, actor: uuid.UUID | None = None
    ) -> UbicacionResponse:
        # Atomicidad mutación<->asiento (mismo patrón que TRD/`update_expediente`):
        # el alta y `audit.append` son un único acto dentro de una sola
        # `conn.transaction()` — antes `fisico_service.py` no dejaba NINGÚN
        # rastro (0 `audit.append`), pese a que la topología del depósito es
        # el soporte de la cadena de custodia física (Ley 594/2000 art. 26).
        try:
            async with self._conn.transaction():
                row = await self._repo.insert_ubicacion(payload.model_dump())
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.ubicacion_created",
                    object_type="ubicacion",
                    object_ref=row["codigo"],
                    actor=str(actor) if actor else None,
                    payload={"id": str(row["id"]), "nivel": row["nivel"], "codigo": row["codigo"]},
                    canal=audit.CANAL_PETICION,
                )
        except asyncpg.UniqueViolationError:
            raise _conflict(f"Ya existe una ubicación con código '{payload.codigo}'")
        except asyncpg.ForeignKeyViolationError:
            raise _not_found("Ubicación padre no encontrada")
        return await self._ubicacion_response(row)

    async def update_ubicacion(
        self, ubicacion_id: uuid.UUID, payload: UbicacionUpdate, *, actor: uuid.UUID | None = None
    ) -> UbicacionResponse:
        """Corrige una ubicación existente, con su asiento (Ley 594/2000 art. 26).

        La topografía del depósito es el soporte de la cadena de custodia
        física: cambiarla es un acto y deja rastro, igual que crearla. El
        asiento guarda el ANTES y el DESPUÉS de cada campo tocado — sin el
        valor anterior, un asiento de corrección no permite reconstruir nada.
        """
        cambios = payload.model_dump(exclude_unset=True)
        if not cambios:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "sin_cambios",
                    "detail": "No se indicó ningún campo que corregir.",
                    "status": 422,
                },
            )

        anterior = await self._repo.get_ubicacion(ubicacion_id)
        if anterior is None:
            raise _not_found(f"Ubicación {ubicacion_id} no encontrada")

        async with self._conn.transaction():
            row = await self._repo.update_ubicacion(ubicacion_id, cambios)
            if row is None:
                raise _not_found(f"Ubicación {ubicacion_id} no encontrada")
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.ubicacion_updated",
                object_type="ubicacion",
                object_ref=row["codigo"],
                actor=str(actor) if actor else None,
                payload={
                    "id": str(row["id"]),
                    "cambios": {
                        campo: {"antes": _jsonable(anterior[campo]), "despues": _jsonable(valor)}
                        for campo, valor in cambios.items()
                    },
                },
                canal=audit.CANAL_PETICION,
            )
        return await self._ubicacion_response(row)

    async def get_ubicacion(self, ubicacion_id: uuid.UUID) -> UbicacionResponse:
        row = await self._repo.get_ubicacion(ubicacion_id)
        if row is None:
            raise _not_found(f"Ubicación {ubicacion_id} no encontrada")
        return await self._ubicacion_response(row)

    async def list_ubicaciones(
        self, *, parent_id: uuid.UUID | None, activo: bool | None, solo_raiz: bool = False
    ) -> list[UbicacionResponse]:
        rows = await self._repo.list_ubicaciones(
            parent_id=parent_id, activo=activo, solo_raiz=solo_raiz
        )
        return [UbicacionResponse(**dict(r)) for r in rows]

    async def _ubicacion_response(self, row) -> UbicacionResponse:
        path = await self._repo.ubicacion_path(row["id"])
        return UbicacionResponse(**dict(row), ruta=" / ".join(path))

    # -- unidades ------------------------------------------------------

    async def create_unidad(
        self, payload: UnidadCreate, *, actor: uuid.UUID | None = None
    ) -> UnidadResponse:
        data = payload.model_dump()
        signatura = None
        if payload.ubicacion_id is not None:
            ub = await self._repo.get_ubicacion(payload.ubicacion_id)
            if ub is None:
                raise _not_found("Ubicación no encontrada")
            path = await self._repo.ubicacion_path(payload.ubicacion_id)
            signatura = "-".join([*path, payload.codigo])
        data["signatura"] = signatura
        try:
            async with self._conn.transaction():
                row = await self._repo.insert_unidad(data)
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.unidad_created",
                    object_type="unidad",
                    object_ref=row["signatura"] or row["codigo"],
                    actor=str(actor) if actor else None,
                    payload={
                        "id": str(row["id"]),
                        "tipo": row["tipo"],
                        "codigo": row["codigo"],
                        "signatura": row["signatura"],
                    },
                    canal=audit.CANAL_PETICION,
                )
        except asyncpg.UniqueViolationError:
            raise _conflict(f"Signatura topográfica duplicada: '{signatura}'")
        except asyncpg.ForeignKeyViolationError:
            raise _not_found("Ubicación o unidad padre no encontrada")
        return UnidadResponse(**dict(row))

    async def get_unidad(self, unidad_id: uuid.UUID) -> UnidadResponse:
        row = await self._repo.get_unidad(unidad_id)
        if row is None:
            raise _not_found(f"Unidad {unidad_id} no encontrada")
        return UnidadResponse(**dict(row))

    async def list_unidades(
        self, *, ubicacion_id: uuid.UUID | None, tipo: str | None
    ) -> list[UnidadResponse]:
        rows = await self._repo.list_unidades(ubicacion_id=ubicacion_id, tipo=tipo)
        return [UnidadResponse(**dict(r)) for r in rows]

    # -- vínculo N:M ---------------------------------------------------

    async def link_expediente(
        self,
        unidad_id: uuid.UUID,
        payload: ExpedienteUnidadLink,
        *,
        actor: uuid.UUID | None = None,
    ) -> None:
        unidad = await self._repo.get_unidad(unidad_id)
        if unidad is None:
            raise _not_found(f"Unidad {unidad_id} no encontrada")
        try:
            async with self._conn.transaction():
                # Meter un expediente en una unidad que está FUERA (prestada) lo
                # entregaría a quien no se validó contra su nivel: se serializa
                # con el préstamo por la raíz del árbol y se rechaza en el INSERT.
                await self._repo.lock_arbol(unidad_id)
                vinculado = await self._repo.link_expediente(
                    expediente_id=payload.expediente_id,
                    unidad_id=unidad_id,
                    folio_inicio=payload.folio_inicio,
                    folio_fin=payload.folio_fin,
                )
                if not vinculado:
                    raise _error(
                        status.HTTP_409_CONFLICT,
                        "unidad_prestada",
                        f"La unidad {unidad['codigo']} (o una contigua de su árbol) está "
                        "prestada: no se pueden vincular expedientes mientras esté fuera.",
                    )
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.unidad_expediente_linked",
                    object_type="unidad",
                    object_ref=str(unidad_id),
                    actor=str(actor) if actor else None,
                    payload={
                        "expediente_id": str(payload.expediente_id),
                        "folio_inicio": payload.folio_inicio,
                        "folio_fin": payload.folio_fin,
                    },
                    canal=audit.CANAL_PETICION,
                )
        except asyncpg.UniqueViolationError:
            raise _conflict("El expediente ya está vinculado a esta unidad")
        except asyncpg.ForeignKeyViolationError:
            raise _not_found("Expediente no encontrado")

    async def units_of_expediente(self, expediente_id: uuid.UUID) -> list[UnidadDeExpediente]:
        rows = await self._repo.units_of_expediente(expediente_id)
        return [UnidadDeExpediente(**dict(r)) for r in rows]

    async def expedientes_in_unit(
        self, unidad_id: uuid.UUID, *, user_id: uuid.UUID | None = None
    ) -> list[ExpedienteEnUnidad]:
        # No-read-up (RF-SEG-08): listar los expedientes de una unidad revela
        # la EXISTENCIA de los clasificados/reservados a un no-lector (Ley
        # 1712/2014 art. 19), igual que cualquier otro listado — filtro por
        # fila, mismo patrón que `generate_fuid` más abajo. La ruta gemela
        # `units_of_expediente` (dónde está UN expediente dado su id) es
        # custodia ortogonal y no se toca aquí.
        max_clearance = await self._archive.caller_clearance(user_id)
        rows = await self._repo.expedientes_in_unit(unidad_id, max_clearance=max_clearance)
        return [ExpedienteEnUnidad(**dict(r)) for r in rows]

    # -- préstamos -----------------------------------------------------

    def _prestamo_response(self, row) -> PrestamoResponse:
        hoy = datetime.now(tz=UTC).date()
        vencido = row["estado"] == "prestado" and row["fecha_devolucion_esperada"] < hoy
        return PrestamoResponse(
            id=row["id"],
            unidad_id=row["unidad_id"],
            unidad_codigo=row["unidad_codigo"],
            unidad_signatura=row["unidad_signatura"],
            prestatario_tipo=row["prestatario_tipo"],
            prestatario_user_id=row["prestatario_user_id"],
            prestatario_nombre=row["prestatario_nombre"],
            tercero_documento_tipo=row["tercero_documento_tipo"],
            tercero_documento_numero=row["tercero_documento_numero"],
            tercero_entidad=row["tercero_entidad"],
            tercero_cargo=row["tercero_cargo"],
            tercero_correo=row["tercero_correo"],
            tercero_telefono=row["tercero_telefono"],
            prestado_por=row["prestado_por"],
            devuelto_recibido_por=row["devuelto_recibido_por"],
            observaciones=row["observaciones"],
            fecha_prestamo=row["fecha_prestamo"],
            fecha_devolucion_esperada=row["fecha_devolucion_esperada"],
            fecha_devolucion_real=row["fecha_devolucion_real"],
            estado=row["estado"],
            vencido=vencido,
        )

    async def create_prestamo(
        self,
        unidad_id: uuid.UUID,
        payload: PrestamoCreate,
        performed_by: str | None,
        *,
        actor: uuid.UUID | None = None,
    ) -> PrestamoResponse:
        unidad = await self._repo.get_unidad(unidad_id)
        if unidad is None:
            raise _not_found(f"Unidad {unidad_id} no encontrada")
        if actor is None:
            # `require_permission` ya lo impide; sin quien entrega no hay custodia.
            raise _error(
                status.HTTP_403_FORBIDDEN,
                "forbidden",
                "No se puede prestar sin identificar a quien entrega.",
            )
        # Cadena de custodia física (art. 26 Ley 594/2000): el préstamo saca la
        # unidad de su ubicación de control. QUIÉN entrega lo fija el servidor
        # (`X-User-Id`); QUIÉN recibe es un usuario validado en el INSERT o un
        # tercero con identificador mínimo.
        #
        # Orden: (1) bloqueo por RAÍZ del árbol en sentencia aparte; (2) el
        # `INSERT ... SELECT` con TODAS las reglas en el `WHERE` (no-read-up de
        # quien presta Y de quien recibe, no prestar lo ya prestado ni lo que
        # está saliendo por transferencia o lote de eliminación); (3) solo si
        # dio 0 filas, un diagnóstico que ELIGE el mensaje (no decide).
        pres = payload.prestatario
        tercero: dict[str, str | None] = {}
        if isinstance(pres, PrestatarioExterno):
            tercero = {
                "nombre": pres.nombre,
                "documento_tipo": pres.documento_tipo,
                "documento_numero": pres.documento_numero,
                "entidad": pres.entidad,
                "cargo": pres.cargo,
                "correo": pres.correo,
                "telefono": pres.telefono,
            }
        max_clearance = await self._archive.caller_clearance(actor)
        try:
            async with self._conn.transaction():
                await self._repo.lock_arbol(unidad_id)
                row = await self._repo.insert_prestamo(
                    unidad_id=unidad_id,
                    prestatario_tipo=pres.tipo,
                    prestatario_user_id=pres.user_id if isinstance(pres, PrestatarioInterno) else None,
                    tercero=tercero,
                    prestado_por=actor,
                    fecha_devolucion_esperada=payload.fecha_devolucion_esperada,
                    observaciones=payload.observaciones,
                    max_clearance=max_clearance,
                )
                if row is None:
                    await self._rechazar_prestamo(unidad, pres.tipo, pres, max_clearance)
                    raise AssertionError("inalcanzable")  # pragma: no cover
                await self._repo.insert_movimiento(
                    unidad_id=unidad_id,
                    accion="RETIRADO",
                    ubicacion_before=None,
                    ubicacion_after=None,
                    performed_by=performed_by,
                )
                audit_payload: dict[str, object] = {
                    "unidad_id": str(unidad_id),
                    "prestatario_tipo": pres.tipo,
                    "prestado_por": str(actor),
                    "fecha_devolucion_esperada": payload.fecha_devolucion_esperada.isoformat(),
                }
                if isinstance(pres, PrestatarioInterno):
                    audit_payload["prestatario_user_id"] = str(pres.user_id)
                else:
                    audit_payload["prestatario_nombre"] = tercero["nombre"]
                    audit_payload["documento_tipo"] = tercero["documento_tipo"]
                    audit_payload["documento_numero"] = _mask_documento(
                        str(tercero["documento_numero"])
                    )
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.prestamo_created",
                    object_type="prestamo",
                    object_ref=str(row["id"]),
                    actor=str(actor),
                    payload=audit_payload,
                    canal=audit.CANAL_PETICION,
                )
        except asyncpg.UniqueViolationError:
            # Defensa del motor (índice parcial / trigger de árbol).
            raise _error(
                status.HTTP_409_CONFLICT,
                "unidad_ya_prestada",
                f"La unidad {unidad['codigo']} (o una contigua de su árbol) ya está prestada.",
            )
        except asyncpg.CheckViolationError as exc:
            if exc.constraint_name == "ck_prestamo_unidad_en_transferencia":
                raise _error(
                    status.HTTP_409_CONFLICT,
                    "unidad_en_transferencia",
                    f"La unidad {unidad['codigo']} tiene una transferencia en curso.",
                )
            if exc.constraint_name == "ck_prestamo_unidad_en_lote":
                raise _error(
                    status.HTTP_409_CONFLICT,
                    "unidad_en_lote_eliminacion",
                    f"La unidad {unidad['codigo']} contiene expedientes de un lote de "
                    "eliminación vivo.",
                )
            raise
        return self._prestamo_response(row)

    async def _rechazar_prestamo(self, unidad, tipo: str, pres, max_clearance: int) -> None:
        """Traduce el 0-filas del INSERT al error que corresponde. Orden pensado
        para no ser un oráculo: primero lo de QUIEN PRESTA (si no alcanza el
        contenido, nada más se le dice); luego quién recibe; luego el estado
        físico de la unidad."""
        d = await self._repo.diagnosticar_prestamo(
            unidad_id=unidad["id"],
            prestatario_tipo=tipo,
            prestatario_user_id=getattr(pres, "user_id", None),
            max_clearance=max_clearance,
        )
        codigo = unidad["codigo"]
        if d["presta_sin_acceso"]:
            raise _error(
                status.HTTP_403_FORBIDDEN,
                "clearance_insuficiente",
                "La unidad contiene expedientes por encima de su nivel de acceso: "
                "no puede prestarla.",
            )
        if d["prestatario_inexistente"]:
            raise _error(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "prestatario_inexistente",
                "El usuario indicado no existe o está inactivo. Use el `id` de "
                "usuario del sistema (GET .../prestatarios-elegibles), no el de Keycloak.",
            )
        if d["prestatario_sin_acceso"]:
            raise _error(
                status.HTTP_403_FORBIDDEN,
                "prestatario_sin_acceso",
                (
                    "Quien recibiría la unidad no tiene acceso al contenido de "
                    f"{codigo}."
                    if tipo == "interno"
                    else f"Un tercero externo solo puede recibir unidades con contenido "
                    f"público; {codigo} contiene expedientes clasificados o reservados."
                ),
            )
        if d["ya_prestada"]:
            raise _error(
                status.HTTP_409_CONFLICT,
                "unidad_ya_prestada",
                f"La unidad {codigo} (o una contigua de su árbol) ya está prestada.",
            )
        if d["en_transferencia"]:
            raise _error(
                status.HTTP_409_CONFLICT,
                "unidad_en_transferencia",
                f"La unidad {codigo} contiene expedientes con una transferencia "
                "preparada o enviada.",
            )
        if d["en_lote"]:
            raise _error(
                status.HTTP_409_CONFLICT,
                "unidad_en_lote_eliminacion",
                f"La unidad {codigo} contiene expedientes de un lote de eliminación vivo.",
            )
        # El WHERE rechazó y ninguna causa conocida lo explica: fail-closed.
        raise _error(
            status.HTTP_409_CONFLICT,
            "prestamo_rechazado",
            f"No se pudo prestar la unidad {codigo}; reintente.",
        )

    async def devolver_prestamo(
        self,
        prestamo_id: uuid.UUID,
        performed_by: str | None,
        *,
        actor: uuid.UUID | None = None,
    ) -> PrestamoResponse:
        existing = await self._repo.get_prestamo(prestamo_id)
        if existing is None:
            raise _not_found(f"Préstamo {prestamo_id} no encontrado")
        if actor is None:
            raise _error(
                status.HTTP_403_FORBIDDEN,
                "forbidden",
                "No se puede registrar la devolución sin identificar a quien la recibe.",
            )
        # Ídem `create_prestamo`: cierra la cadena de custodia con la fecha
        # REAL de devolución y QUIÉN la recibió (art. 26 Ley 594/2000).
        async with self._conn.transaction():
            row = await self._repo.devolver_prestamo(prestamo_id, actor)
            if row is None:
                raise _conflict("El préstamo ya estaba devuelto")
            await self._repo.insert_movimiento(
                unidad_id=row["unidad_id"],
                accion="DEVUELTO",
                ubicacion_before=None,
                ubicacion_after=None,
                performed_by=performed_by,
            )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.prestamo_devuelto",
                object_type="prestamo",
                object_ref=str(prestamo_id),
                actor=str(actor),
                payload={
                    "unidad_id": str(row["unidad_id"]),
                    "prestatario_tipo": row["prestatario_tipo"],
                    "prestatario_user_id": (
                        str(row["prestatario_user_id"]) if row["prestatario_user_id"] else None
                    ),
                    "devuelto_recibido_por": str(actor),
                    "fecha_devolucion_esperada": row["fecha_devolucion_esperada"].isoformat(),
                    "fecha_devolucion_real": (
                        row["fecha_devolucion_real"].isoformat()
                        if row["fecha_devolucion_real"]
                        else None
                    ),
                },
                canal=audit.CANAL_PETICION,
            )
        return self._prestamo_response(row)

    async def list_prestatarios_elegibles(
        self,
        unidad_id: uuid.UUID,
        *,
        q: str | None,
        page: int,
        size: int,
        actor: uuid.UUID | None,
    ) -> tuple[list[PrestatarioElegible], int]:
        """Usuarios a quienes SE PUEDE prestar la unidad (clearance cubre el árbol).
        Vacío si quien consulta no alcanza el contenido: no es un oráculo."""
        if await self._repo.get_unidad(unidad_id) is None:
            raise _not_found(f"Unidad {unidad_id} no encontrada")
        max_clearance = await self._archive.caller_clearance(actor)
        rows, total = await self._repo.list_prestatarios_elegibles(
            unidad_id=unidad_id, max_clearance=max_clearance, q=q, page=page, size=size
        )
        return [PrestatarioElegible(user_id=r["user_id"], username=r["username"]) for r in rows], total

    async def list_prestamos(
        self,
        *,
        estado: str | None,
        unidad_id: uuid.UUID | None,
        page: int | None = None,
        size: int | None = None,
    ) -> tuple[list[PrestamoResponse], int]:
        rows, total = await self._repo.list_prestamos(
            estado=estado, unidad_id=unidad_id, page=page, size=size
        )
        return [self._prestamo_response(r) for r in rows], total

    async def list_movimientos(self, unidad_id: uuid.UUID) -> list[MovimientoResponse]:
        rows = await self._repo.list_movimientos(unidad_id)
        return [MovimientoResponse(**dict(r)) for r in rows]

    # -- FUID (RF-ARF-10) ----------------------------------------------

    async def generate_fuid(
        self,
        *,
        ubicacion_id: uuid.UUID | None,
        trd_serie_id: uuid.UUID | None,
        expediente_id: uuid.UUID | None = None,
        user_id: uuid.UUID | None = None,
        max_clearance: int | None = None,
        unidad_administrativa: str | None = None,
        oficina_productora: str | None = None,
        objeto: str | None = None,
    ) -> FuidResponse:
        # No-read-up (RF-SEG-08): el FUID expone asunto/serie/signatura del
        # expediente — es contenido, no metadato de custodia (defensa en
        # profundidad; `TransferenciaService.fuid` ya aplica un 404 total
        # aguas arriba para la puerta single-expediente, pero las puertas
        # multi-expediente de `fisico.py` solo tienen este filtro por fila).
        #
        # `max_clearance` (E12 H-I): por defecto `None` -- la ruta VIVA
        # (llamante humano vía `fuid()`/`generate_fuid_xml`) resuelve el
        # clearance REAL de `user_id`, sin cambios de comportamiento. Un
        # llamante interno que congela el acta de entrega en un acto de
        # CUSTODIA (`TransferenciaService.recibir`, ortogonal a lectura --
        # mismo criterio que `fixity`, `core.clearance` bloque (c)) pasa
        # `CLEARANCE_SIN_RESTRICCION` explícito, sin resolver `caller_clearance`.
        if max_clearance is None:
            max_clearance = await self._archive.caller_clearance(user_id)
        # Se pide UNA fila de más para poder distinguir «cabe justo» de «se
        # pasa», y decirlo en la respuesta en vez de entregar un inventario
        # recortado con aspecto de completo.
        rows = await self._repo.fuid_inventory(
            ubicacion_id=ubicacion_id,
            trd_serie_id=trd_serie_id,
            expediente_id=expediente_id,
            max_clearance=max_clearance,
            limite=MAX_FILAS_FUID,
        )
        truncado = len(rows) > MAX_FILAS_FUID
        if truncado:
            rows = rows[:MAX_FILAS_FUID]
            logger.warning(
                "FUID truncado a %d filas: el inventario completo es mayor.",
                MAX_FILAS_FUID,
            )
        fuid_rows = [FuidRow(orden=i, **dict(r)) for i, r in enumerate(rows, start=1)]
        return FuidResponse(
            cabecera=FuidCabecera(
                entidad_productora=await self._repo.entidad_productora(),
                unidad_administrativa=unidad_administrativa,
                oficina_productora=oficina_productora,
                objeto=objeto,
            ),
            generado_ts=datetime.now(tz=UTC),
            total=len(fuid_rows),
            rows=fuid_rows,
        )

    async def generate_fuid_xml(
        self,
        *,
        ubicacion_id: uuid.UUID | None,
        trd_serie_id: uuid.UUID | None,
        user_id: uuid.UUID | None = None,
        unidad_administrativa: str | None = None,
        oficina_productora: str | None = None,
        objeto: str | None = None,
    ) -> str:
        fuid = await self.generate_fuid(
            ubicacion_id=ubicacion_id,
            trd_serie_id=trd_serie_id,
            user_id=user_id,
            unidad_administrativa=unidad_administrativa,
            oficina_productora=oficina_productora,
            objeto=objeto,
        )
        return _fuid_to_xml(fuid)
