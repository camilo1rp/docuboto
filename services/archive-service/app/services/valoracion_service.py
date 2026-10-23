# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Retención legal reversible de un fondo acumulado hasta la valoración de la TVD (C-5 / C7,
ADR-043, enmienda de ADR-026 D4.4).

Flujo:

1. Al proteger el contenido (índice, AIP) de un fondo cuyo calendario ya venció,
   `retention_math.base_worm` devuelve `RetencionDiferida`. `resolver_diferida` ASEGURA PRIMERO el
   hold `valoracion_pendiente` en storage y lo amarra a la versión de TVD
   (`expediente_valoracion_hold`); solo DESPUÉS el llamador protege con `retencion_diferida`.
   Orden conservador: si el hold falla no hay objeto sin protección, hay objeto sin crear.
2. El Comité registra la valoración (`registrar_valoracion`): `tvd_valoracion` + amarre de los
   expedientes de esa versión + asiento, en UNA transacción. Se aplican de forma síncrona los
   primeros N; el resto lo toma la fase `run_once_valoracion_holds`.
3. Aplicación por expediente (`aplicar_expediente`), orden conservador: CT -> primero se FIJA la
   retención COMPLIANCE (ventana rodante desde hoy; la renueva después la fase 5) y DESPUÉS se
   levanta el hold; E -> solo se levanta. Un hold judicial concurrente sobrevive: storage no
   libera S3 mientras quede otro.
   - **M (medio técnico)**: el contenido de estos expedientes YA está en soporte electrónico
     (es lo que protege storage), así que M se trata como CT: se conserva.
   - **S (selección)**: la valoración NO levanta el hold. Cada expediente queda en
     `seleccion_pendiente` hasta el acto de selección (`registrar_seleccion`, TVD crud >= 3, con
     asiento): `conservar` sigue el camino de CT; `descartar` levanta el hold sin fijar retención
     y el expediente queda elegible para el lote. El método de muestreo se declara en la
     valoración."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Literal

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.clients import storage_client
from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.core.disposition import CT_DISPOSITION_CODES
from app.repositories.archive_repository import ArchiveRepository
from app.repositories.valoracion_repository import ValoracionRepository
from app.schemas.retencion_legal import (
    SeleccionPendienteItem,
    SeleccionValoracionRequest,
    SeleccionValoracionResponse,
    ValoracionTvd,
    ValoracionTvdEstado,
    ValoracionTvdRequest,
    ValoracionTvdResponse,
)
from app.services.backoff import next_backoff_seconds
from app.services.retention_math import RetencionDiferida, total_trd_years

logger = logging.getLogger(__name__)

_SERVICE = "archive-service"
MOTIVO_VALORACION = "valoracion_pendiente"
_ESTADOS_VALORABLES = ("aprobada", "convalidada")

AplicacionResultado = Literal["aplicada", "esperando", "pendiente", "agotada", "omitida"]


class ValoracionHoldError(Exception):
    """No se pudo asegurar el hold `valoracion_pendiente` (storage caído, preservación apagada).
    El llamador pospone con backoff: NO se protege el contenido sin el hold."""


def _disposicion_agn(valor: str) -> str:
    """Código AGN (CT/E/S/M) de una disposición, con los alias heredados."""
    if valor in CT_DISPOSITION_CODES:
        return "CT"
    return {"E": "E", "eliminate": "E", "S": "S", "select": "S", "M": "M", "digitize": "M"}.get(
        valor, valor
    )


def _tvd_no_encontrada() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "error": "tvd_not_found",
            "detail": "No existe una TVD con ese identificador",
            "status": 404,
        },
    )


class ValoracionService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._repo = ValoracionRepository(conn, tenant_slug)
        self._archive = ArchiveRepository(conn, tenant_slug)

    # -- 1. asegurar el hold antes de proteger ---------------------------------------------

    async def resolver_diferida(
        self, exp, serie
    ) -> RetencionDiferida | tuple[int, date] | None:
        """Qué retención lleva el contenido de un fondo VENCIDO, asegurando el hold si hace falta.

        - Sin amarre previo: fija el hold `valoracion_pendiente` (storage, idempotente), lo amarra
          a la versión de TVD y devuelve `RetencionDiferida`.
        - Amarre vigente (sin aplicar): `RetencionDiferida`.
        - Valoración YA aplicada con CT: el objeto nace con ventana rodante `(total, hoy)`.
        - Valoración YA aplicada con E/S/M: `None` (no hay retención que fijar; el llamador
          pospone y la alarma de agotamiento la hace visible).

        Lanza `ValoracionHoldError` si no se pudo fijar el hold (fail-closed)."""
        hold = await self._repo.get_hold(exp.id)
        if hold is not None:
            if hold["aplicada_at"] is None:
                return RetencionDiferida()
            if hold["resultado"] == "retencion_ct":
                total = total_trd_years(serie.archivo_gestion_years, serie.archivo_central_years)
                return (total, datetime.now(tz=UTC).date()) if total is not None else None
            return None

        try:
            res = await storage_client.fijar_legal_hold(
                tenant_slug=self._tenant_slug,
                actor_id=None,
                expediente_id=str(exp.id),
                motivo_tipo=MOTIVO_VALORACION,
                fundamento=(
                    "Fondo acumulado con calendario de retencion vencido: el contenido queda "
                    "bajo retencion legal hasta la valoracion de la TVD por el Comite (ADR-043)"
                ),
                acta_ref=f"TVD {serie.code} v{serie.version}",
            )
        except storage_client.StorageLegalHoldError as exc:
            raise ValoracionHoldError(f"hold no fijado: {exc} ({exc.code})") from exc
        async with self._conn.transaction():
            row = await self._repo.insert_hold(
                expediente_id=exp.id,
                hold_id=uuid.UUID(str(res["hold_id"])),
                trd_serie_id=exp.trd_serie_id,
            )
            if row is not None:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.expediente_valoracion_hold_fijado",
                    object_type="expediente",
                    object_ref=exp.code if getattr(exp, "code", None) else str(exp.id),
                    actor=str(SYSTEM_RECONCILER_ID),
                    canal="system",
                    payload={
                        "expediente_id": str(exp.id),
                        "hold_id": str(res["hold_id"]),
                        "trd_serie_id": str(exp.trd_serie_id),
                        "serie": f"{serie.code} v{serie.version}",
                        "creado_en_storage": bool(res.get("creado")),
                        "valoracion_ya_registrada": row["valoracion_id"] is not None,
                    },
                )
        return RetencionDiferida()

    # -- 2. el acto del Comité ---------------------------------------------------------------

    async def registrar_valoracion(
        self, serie_id: uuid.UUID, payload: ValoracionTvdRequest, *, actor: uuid.UUID
    ) -> ValoracionTvdResponse:
        serie = await self._archive.get_tvd_agrupacion_by_id(serie_id)
        if serie is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "tvd_not_found",
                    "detail": "No existe una TVD con ese identificador",
                    "status": 404,
                },
            )
        if serie.estado not in _ESTADOS_VALORABLES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "tvd_no_aprobada",
                    "detail": (
                        f"La TVD está en estado '{serie.estado}': la valoración solo se registra "
                        "sobre una versión aprobada o convalidada"
                    ),
                    "status": 409,
                },
            )
        disposicion = _disposicion_agn(serie.disposition)
        if disposicion == "S" and not (payload.metodo_muestreo and payload.metodo_muestreo.strip()):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "metodo_muestreo_requerido",
                    "detail": (
                        "La disposición de la TVD es Selección: declare el método de muestreo "
                        "con que se elige lo que se conserva"
                    ),
                    "status": 422,
                },
            )
        if disposicion not in ("CT", "E", "S", "M"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "tvd_sin_disposicion",
                    "detail": "La TVD no declara una disposición final válida (CT/E/S/M)",
                    "status": 409,
                },
            )

        metodo = (payload.metodo_muestreo or "").strip() if disposicion == "S" else None
        async with self._conn.transaction():
            fila = await self._repo.insert_valoracion(
                trd_serie_id=serie_id,
                disposicion=disposicion,
                acta_comite=payload.acta_comite,
                fecha_acta=payload.fecha_acta,
                fundamento=payload.fundamento,
                decidida_por=actor,
                metodo_muestreo=metodo,
            )
            if fila is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "valoracion_ya_registrada",
                        "detail": "Esta versión de la TVD ya tiene su valoración registrada",
                        "status": 409,
                    },
                )
            afectados = await self._repo.amarrar_valoracion(serie_id, fila["id"])
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.tvd_valoracion_registrada",
                object_type="trd_serie",
                object_ref=f"{serie.code} v{serie.version}",
                actor=str(actor),
                canal=audit.CANAL_PETICION,
                payload={
                    "valoracion_id": str(fila["id"]),
                    "trd_serie_id": str(serie_id),
                    "disposicion": disposicion,
                    "acta_comite": payload.acta_comite,
                    "fecha_acta": payload.fecha_acta.isoformat(),
                    "expedientes_afectados": len(afectados),
                    "metodo_muestreo": metodo,
                    "seleccion_pendiente": len(afectados) if disposicion == "S" else 0,
                },
            )

        # Aplicación síncrona ACOTADA y best-effort: el acto ya es un hecho; lo que no alcance lo
        # aplica la fase de reconciliación.
        # Con S nada se aplica: cada expediente espera su acto de selección.
        para_aplicar = [] if disposicion == "S" else afectados[: settings.valoracion_sync_apply_max]
        for expediente_id in para_aplicar:
            try:
                await self.aplicar_expediente(expediente_id)
            except Exception:
                logger.warning(
                    "valoración: aplicación síncrona falló expediente_id=%s (la fase la reintenta)",
                    expediente_id,
                    exc_info=True,
                )
        return ValoracionTvdResponse(
            valoracion_id=fila["id"],
            disposicion=disposicion,
            expedientes_afectados=len(afectados),
            seleccion_pendiente=len(afectados) if disposicion == "S" else 0,
        )

    async def get_estado(self, serie_id: uuid.UUID) -> ValoracionTvdEstado:
        serie = await self._archive.get_tvd_agrupacion_by_id(serie_id)
        if serie is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "tvd_not_found",
                    "detail": "No existe una TVD con ese identificador",
                    "status": 404,
                },
            )
        fila = await self._repo.get_valoracion(serie_id)
        conteos = await self._repo.conteos(serie_id)
        return ValoracionTvdEstado(
            trd_serie_id=serie_id,
            valoracion=(
                ValoracionTvd(
                    valoracion_id=fila["id"],
                    trd_serie_id=fila["trd_serie_id"],
                    disposicion=fila["disposicion"],
                    acta_comite=fila["acta_comite"],
                    fecha_acta=fila["fecha_acta"],
                    fundamento=fila["fundamento"],
                    decidida_por=fila["decidida_por"],
                    decidida_at=fila["decidida_at"],
                    metodo_muestreo=fila["metodo_muestreo"],
                )
                if fila is not None
                else None
            ),
            **conteos,
        )

    # -- 3. aplicación por expediente ------------------------------------------------------------

    async def aplicar_expediente(self, expediente_id: uuid.UUID) -> AplicacionResultado:
        """Aplica la valoración a UN expediente. Nunca lanza por fallos de storage: los registra
        con backoff y, agotados, la fila queda `agotada` (intervención manual)."""
        fila = await self._repo.get_para_aplicar(expediente_id)
        if fila is None:
            return "omitida"
        if await self._repo.artefactos_pendientes(expediente_id):
            # No consume intento: aún no hay nada (o no todo) que valorar en storage.
            return "esperando"

        disposicion = fila["disposicion"]
        # Qué se hace con el contenido: se CONSERVA (retención rolante, camino de CT) o solo se
        # levanta el hold. M se conserva (ya es soporte electrónico); S, según la selección.
        conserva = disposicion in ("CT", "M") or (
            disposicion == "S" and fila["seleccion"] == "conservar"
        )
        try:
            if conserva:
                total = total_trd_years(fila["archivo_gestion_years"], fila["archivo_central_years"])
                if total is None:
                    return await self._fallo(fila, "trd_sin_retencion")
                # PRIMERO la retención, DESPUÉS el levantamiento: invertir el orden y fallar el
                # segundo paso dejaría el contenido sin ninguna protección.
                fijada = await storage_client.fijar_retencion_internal(
                    tenant_slug=self._tenant_slug,
                    expediente_id=str(expediente_id),
                    retencion_anios=total,
                    fecha_inicio=datetime.now(tz=UTC).date(),
                )
                retain_until = fijada.get("retain_until")
                if retain_until:
                    await self._repo.propagar_espejo_retencion(
                        expediente_id, datetime.fromisoformat(str(retain_until))
                    )
            try:
                levantado = await storage_client.levantar_legal_hold(
                    tenant_slug=self._tenant_slug,
                    actor_id=None,
                    hold_id=fila["hold_id"],
                    motivo_tipo_esperado=MOTIVO_VALORACION,
                    fundamento=(
                        f"Valoracion de la TVD {fila['serie_code']} v{fila['serie_version']} "
                        f"por el Comite (disposicion {disposicion})"
                    ),
                    acta_ref=fila["acta_comite"],
                )
            except storage_client.StorageLegalHoldError as exc:
                if exc.status == 409 and exc.code == "hold_ya_levantado":
                    levantado = {"objetos_liberados_s3": 0, "holds_restantes": -1}
                else:
                    raise
        except storage_client.StorageLegalHoldError as exc:
            return await self._fallo(fila, f"storage:{exc.status}:{exc.code or 'sin_codigo'}")

        resultado = "retencion_ct" if conserva else "sin_retencion"
        async with self._conn.transaction():
            marcada = await self._repo.marcar_aplicada(expediente_id, resultado)
            if marcada is not None:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.expediente_valoracion_aplicada",
                    object_type="expediente",
                    object_ref=fila["expediente_code"],
                    actor=str(SYSTEM_RECONCILER_ID),
                    canal="system",
                    payload={
                        "expediente_id": str(expediente_id),
                        "hold_id": str(fila["hold_id"]),
                        "valoracion_id": str(fila["valoracion_id"]),
                        "disposicion": disposicion,
                        "seleccion": fila["seleccion"],
                        "resultado": resultado,
                        "acta_comite": fila["acta_comite"],
                        "objetos_liberados_s3": int(levantado["objetos_liberados_s3"]),
                        "holds_restantes": int(levantado["holds_restantes"]),
                    },
                )
        return "aplicada"

    # -- 4. el acto de selección (disposición S) ----------------------------------------------

    async def listar_seleccion_pendiente(
        self, serie_id: uuid.UUID, *, page: int, size: int
    ) -> tuple[list[SeleccionPendienteItem], int]:
        if await self._archive.get_tvd_agrupacion_by_id(serie_id) is None:
            raise _tvd_no_encontrada()
        rows, total = await self._repo.listar_seleccion_pendiente(
            serie_id, size, (page - 1) * size
        )
        return [SeleccionPendienteItem(**dict(r)) for r in rows], total

    async def registrar_seleccion(
        self,
        serie_id: uuid.UUID,
        expediente_id: uuid.UUID,
        payload: SeleccionValoracionRequest,
        *,
        actor: uuid.UUID,
    ) -> SeleccionValoracionResponse:
        """Acto de selección sobre UN expediente de una valoración S. Atómico con su asiento; la
        aplicación (retención + levantamiento) va después, best-effort, y la completa la fase de
        reconciliación si falla."""
        async with self._conn.transaction():
            hold = await self._repo.bloquear_hold_de_serie(serie_id, expediente_id)
            if hold is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": "valoracion_expediente_not_found",
                        "detail": "El expediente no está en la valoración de esa TVD",
                        "status": 404,
                    },
                )
            if hold["valoracion_id"] is None or hold["disposicion"] != "S":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "valoracion_no_es_seleccion",
                        "detail": (
                            "La valoración de esta TVD no es de Selección o aún no se registró: "
                            "no hay acto de selección que registrar"
                        ),
                        "status": 409,
                    },
                )
            if hold["seleccion"] is not None or hold["aplicada_at"] is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "seleccion_ya_registrada",
                        "detail": "Este expediente ya tiene su selección: no se rehace",
                        "status": 409,
                    },
                )
            fila = await self._repo.registrar_seleccion(
                expediente_id,
                accion=payload.accion,
                fundamento=payload.fundamento.strip(),
                actor=actor,
            )
            if fila is None:  # carrera residual: el FOR UPDATE ya debería impedirla
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "seleccion_ya_registrada",
                        "detail": "Este expediente ya tiene su selección: no se rehace",
                        "status": 409,
                    },
                )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="archive.expediente_seleccion_registrada",
                object_type="expediente",
                object_ref=hold["expediente_code"],
                actor=str(actor),
                canal=audit.CANAL_PETICION,
                payload={
                    "expediente_id": str(expediente_id),
                    "trd_serie_id": str(serie_id),
                    "valoracion_id": str(hold["valoracion_id"]),
                    "accion": payload.accion,
                    "fundamento": payload.fundamento.strip(),
                },
            )
        try:
            aplicacion: str = await self.aplicar_expediente(expediente_id)
        except Exception:  # noqa: BLE001 — el acto ya es un hecho; la fase lo completa
            logger.warning(
                "selección: aplicación síncrona falló expediente_id=%s (la fase la reintenta)",
                expediente_id,
                exc_info=True,
            )
            aplicacion = "pendiente"
        return SeleccionValoracionResponse(
            expediente_id=expediente_id, accion=payload.accion, aplicacion=aplicacion
        )

    async def _fallo(self, fila, motivo: str) -> AplicacionResultado:
        intentos = int(fila["intentos"]) + 1
        agotada = intentos >= settings.indice_reconciliation_max_intentos
        proximo = (
            None
            if agotada
            else datetime.now(tz=UTC)
            + timedelta(
                seconds=next_backoff_seconds(
                    intentos,
                    base=settings.indice_reconciliation_backoff_base_seconds,
                    cap=settings.indice_reconciliation_backoff_cap_seconds,
                )
            )
        )
        await self._repo.bump_intento(
            fila["expediente_id"], proximo_at=proximo, ultimo_error=motivo, agotada=agotada
        )
        if agotada:
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="archive.expediente_valoracion_agotada",
                    object_type="expediente",
                    object_ref=fila["expediente_code"],
                    actor=str(SYSTEM_RECONCILER_ID),
                    canal="system",
                    payload={
                        "expediente_id": str(fila["expediente_id"]),
                        "intentos": intentos,
                        "motivo": motivo,
                    },
                )
            except Exception:  # noqa: BLE001 — best-effort: nunca degrada el resultado.
                logger.warning("audit.append(valoracion_agotada) failed", exc_info=True)
            return "agotada"
        return "pendiente"

    async def barrer(self, limit: int) -> dict[str, int]:
        """Una barrida del tenant (fase `run_once_valoracion_holds`)."""
        resumen = {"vistos": 0, "aplicadas": 0, "esperando": 0, "pendientes": 0, "agotadas": 0}
        for row in await self._repo.list_pendientes_aplicar(limit):
            resumen["vistos"] += 1
            try:
                r = await self.aplicar_expediente(row["expediente_id"])
            except Exception:
                logger.warning(
                    "valoración: fallo aplicando expediente_id=%s", row["expediente_id"],
                    exc_info=True,
                )
                resumen["pendientes"] += 1
                continue
            clave = {"aplicada": "aplicadas", "esperando": "esperando", "agotada": "agotadas"}.get(
                r, "pendientes"
            )
            resumen[clave] += 1
        return resumen
