# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Consulta de solo-lectura de `public.audit_log` acotada a un expediente (E15
perfil v4, R3-b — historial de acceso).

Espejo de `auth-service/app/repositories/audit_query_repository.py`: el ÚNICO
punto de ESCRITURA de `audit_log` es `orpycamcp_common.audit`; este repositorio
nunca inserta. El filtro `tenant_slug` es defensa en profundidad (aunque
`audit_log` vive en `public`, no en el schema del tenant) — sin él, un llamante
de un tenant podría, en teoría, leer el historial de acceso de un expediente de
OTRO tenant si por azar comparte `object_ref` (el `code` de expediente,
p. ej. "EXP-2024-0001", NO es globalmente único entre tenants).
"""

from __future__ import annotations

import asyncpg

_COLS = (
    "a.tenant_slug, a.ts, a.service, a.actor, a.canal, a.action, "
    "a.object_type, a.object_ref, a.payload, u.username AS actor_nombre"
)

#: **El actor se resuelve a un nombre aquí, no en la pantalla.** Gemelo exacto
#: de `document-service/app/repositories/audit_read.py`: `audit_log` guarda el
#: identificador interno —correcto, porque un `username` cambia y un asiento no
#: se toca nunca— pero un historial de acceso que contesta con un UUID no
#: contesta quién. `LEFT JOIN` y por texto a propósito: `actor` no siempre es un
#: usuario (hay asientos del propio sistema), y castear a `uuid` haría fallar la
#: consulta entera por una fila que ni se estaba preguntando.
_JOIN_ACTOR = "LEFT JOIN auth_users u ON u.id::text = a.actor"
_OBJECT_TYPES = ("expediente", "expediente_indice")

# Acciones de ACCESO sobre los radicados del expediente (2026-08-29). La traza
# existía y era buena —`document.radicado_consultado` y
# `document.anexo_descargado`, con actor, canal y nivel de seguridad— pero se
# escribe con `object_type='radicado'` y `object_ref=<número de radicado>`,
# mientras esta consulta filtraba por el CÓDIGO DEL EXPEDIENTE. Resultado: la
# pregunta «¿quién descargó los documentos de este expediente?» no tenía
# respuesta en la pantalla hecha para responderla, aunque el dato estuviera
# guardado. Otra vez el dato cargado que nadie lee.
#
# Lista de ADMISIÓN y no de exclusión, y acotada a LECTURAS: un radicado
# acumula además creación, anulación, reclasificación… y volcarlas aquí
# convertiría el «historial de acceso» en un historial de todo, que es otra
# cosa. Una acción nueva no entra sola: hay que decidir si es un acceso.
_ACCIONES_ACCESO_RADICADO = (
    "document.radicado_consultado",
    "document.anexo_descargado",
)


class AuditReadRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # La condición es UNA sola y compartida por el recuento y la página. Si
    # vivieran separadas, el total podría contar entradas que la página no
    # muestra —o al revés— y nadie se enteraría: es el mismo cuidado que ya se
    # tuvo con la cola de vencimientos.
    _WHERE = (
        "a.tenant_slug = $1 AND ("
        "  (a.object_ref = $2 AND a.object_type = ANY($3::text[]))"
        "  OR (a.object_type = 'radicado' AND a.object_ref = ANY($4::text[])"
        "      AND a.action = ANY($5::text[]))"
        ")"
    )

    def _params(self, tenant_slug: str, code: str, trackings: list[str]) -> list:
        return [
            tenant_slug,
            code,
            list(_OBJECT_TYPES),
            trackings,
            list(_ACCIONES_ACCESO_RADICADO),
        ]

    async def count_for_expediente(
        self, tenant_slug: str, code: str, trackings: list[str] | None = None
    ) -> int:
        total: int = await self._conn.fetchval(
            f"SELECT COUNT(*) FROM public.audit_log a WHERE {self._WHERE}",
            *self._params(tenant_slug, code, trackings or []),
        )
        return total or 0

    async def list_for_expediente(
        self,
        tenant_slug: str,
        code: str,
        trackings: list[str] | None = None,
        *,
        offset: int,
        limit: int,
    ) -> list[asyncpg.Record]:
        params = self._params(tenant_slug, code, trackings or [])
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM public.audit_log a {_JOIN_ACTOR} "
                f"WHERE {self._WHERE} "
                "ORDER BY a.ts DESC, a.id DESC LIMIT $6 OFFSET $7",
                *params,
                limit,
                offset,
            )
        )
