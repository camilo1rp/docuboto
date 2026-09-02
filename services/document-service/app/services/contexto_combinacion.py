# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Resolución del contexto de combinación (E23, RF-PRO-02).

Reúne, de las fuentes autoritativas del tenant, los valores que `combinacion.py`
sustituye en la plantilla: el radicado que se responde, su remitente, el
destinatario de la salida, la entidad, la dependencia productora, el funcionario
y las fechas.

**El no-read-up no es opcional aquí** (RF-SEG-08). El asunto y el número del
radicado padre acaban ESCRITOS dentro del cuerpo del documento que se produce.
Leer el padre sin el clearance del llamante convertiría la combinación en una vía
de exfiltración: bastaría crear un borrador contra un radicado reservado para
sacar su asunto a un documento legible por cualquiera. Es el mismo razonamiento
—y la misma corrección— que ya lleva `DocumentService.crear_respuesta`.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import asyncpg

_MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)

#: Claves de `sgd_param_admin` (tenant-service) que alimentan el grupo «Entidad».
#: Se declaran aquí, junto a los marcadores que llenan, porque un parámetro cuyo
#: nombre solo vive en la cabeza de quien lo configuró no se puede configurar.
PARAMS_ENTIDAD: dict[str, str] = {
    "NOM_E": "entidad_nombre",
    "SIGLA_E": "entidad_sigla",
    "NIT_E": "entidad_nit",
    "DIR_E": "entidad_direccion",
    "TEL_E": "entidad_telefono",
    "MAIL_E": "entidad_email",
    "WEB_E": "entidad_web",
}


def fecha_en_letras(dia: date) -> str:
    """`2026-09-01` → `1 de septiembre de 2026`. Sin locale del sistema: el
    formato de un oficio no puede depender de cómo esté configurado el contenedor."""
    return f"{dia.day} de {_MESES[dia.month - 1]} de {dia.year}"


class ContextoCombinacion:
    """Construye el diccionario `marcador → valor` para `combinar`."""

    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._slug = tenant_slug

    async def construir(
        self,
        *,
        radicado_padre_id: uuid.UUID | None,
        max_clearance: int,
        productor_id: uuid.UUID | None = None,
        funcionario: dict[str, Any] | None = None,
        tenant_client: Any = None,
        hoy: date | None = None,
    ) -> dict[str, object]:
        hoy = hoy or date.today()
        ctx: dict[str, object] = {
            "F_HOY": fecha_en_letras(hoy),
            "F_HOY_CORTO": hoy.isoformat(),
        }

        if radicado_padre_id is not None:
            ctx.update(await self._del_padre(radicado_padre_id, max_clearance))

        params = {}
        if tenant_client is not None:
            params = await tenant_client.get_params(tenant_slug=self._slug)
        for marcador, clave in PARAMS_ENTIDAD.items():
            ctx[marcador] = params.get(clave)

        if funcionario:
            ctx["FUNCIONARIO"] = funcionario.get("nombre")
            ctx["CARGO"] = funcionario.get("cargo")
            ctx["LOGIN"] = funcionario.get("login")

        # Dependencia PRODUCTORA — la de quien firma, no la de quien recibe.
        #
        # Estos tres marcadores se resolvían desde `dest_dept_code`, la
        # dependencia DESTINO del documento. En una Salida eso es la unidad
        # receptora, así que el bloque de firma del oficio salía con el nombre
        # de la dependencia equivocada. Un campo vacío se ve; uno relleno con el
        # valor de otro, no: el oficio se firma y se envía con él.
        codigo = await self._dependencia_productora(productor_id)
        if codigo is not None and tenant_client is not None:
            dep = await tenant_client.get_dependencia(tenant_slug=self._slug, codigo=codigo)
            if dep:
                ctx["DEPENDENCIA"] = dep.get("nombre")
                ctx["DEP_SIGLA"] = dep.get("sigla")
                ctx["DEP_CODIGO"] = dep.get("codigo")

        return ctx

    async def _dependencia_productora(self, productor_id: uuid.UUID | None) -> int | None:
        """Dependencia desde la que el funcionario produce el documento.

        La activa del conmutador si la hay y, si no, su dependencia principal.
        Devuelve `None` cuando no se puede establecer: los marcadores quedan sin
        resolver y `combinar` los declara faltantes, que es lo correcto —
        inventar una dependencia en el pie de un oficio es peor que dejar el
        hueco a la vista de quien lo redacta.
        """
        if productor_id is None:
            return None
        try:
            row = await self._conn.fetchrow(
                "SELECT COALESCE(u.active_depe_id, urd.depe_id) AS codigo "
                "FROM auth_users u "
                "LEFT JOIN urd_usuaroldep urd "
                "       ON urd.user_id = u.id AND urd.es_principal AND urd.is_active "
                "WHERE u.id = $1",
                productor_id,
            )
        except asyncpg.UndefinedTableError:
            # Tablas de identidad del tenant ausentes (entorno no provisionado).
            # Mismo criterio que `resolve_caller_clearance`: no se puede
            # establecer la dependencia, así que no se pone ninguna.
            return None
        return int(row["codigo"]) if row and row["codigo"] is not None else None

    async def _del_padre(
        self, radicado_padre_id: uuid.UUID, max_clearance: int
    ) -> dict[str, object]:
        """Radicado padre + su remitente, recortado por clearance.

        El `WHERE nivel_seguridad <= $2` es el gate. Si el padre está por encima
        del clearance del llamante, la consulta no devuelve fila y el contexto
        queda sin esos campos: `combinar` los reporta como faltantes, que es
        indistinguible de «el radicado no existe» — y esa indistinguibilidad es
        lo que se quiere (no confirmar la existencia de lo reservado).
        """
        row = await self._conn.fetchrow(
            "SELECT id, tracking_number, subject, pages, registered_at, "
            "       sender_name, sender_entity "
            "FROM radicados WHERE id = $1 AND nivel_seguridad <= $2",
            radicado_padre_id,
            max_clearance,
        )
        if row is None:
            return {}

        ctx: dict[str, object] = {
            "RAD_E": row["tracking_number"],
            "F_RAD_E": row["registered_at"].date().isoformat(),
            "ASUNTO": row["subject"],
            "NRO_PAGS": row["pages"],
        }

        anexos = await self._conn.fetch(
            "SELECT filename FROM anexos WHERE radicado_id = $1 ORDER BY created_at", row["id"]
        )
        ctx["DESC_ANEXOS"] = ", ".join(a["filename"] for a in anexos)

        # El remitente del padre, por su copia CONGELADA en `radicado_actor`: es
        # el dato que era verdad cuando llegó la comunicación. Tomar la ficha
        # actual del tercero contestaría a una dirección que quizá ya no es la
        # que él dio. La dirección pertenece al envío, no al sujeto.
        # El número de documento se toma del TERCERO y no de la copia congelada,
        # y la asimetría es deliberada: la identidad es por referencia y no
        # cambia con la mudanza; el contacto es copia y sí. Son los dos ejes que
        # el modelo de terceros separó a propósito.
        actor = await self._conn.fetchrow(
            "SELECT a.nombre, a.entidad, a.email, a.telefono, a.direccion, "
            "       a.municipio, a.departamento, t.numero_documento "
            "FROM radicado_actor a LEFT JOIN tercero t ON t.id = a.tercero_id "
            "WHERE a.radicado_id = $1 AND a.rol = 'remitente' "
            "ORDER BY a.principal DESC, a.orden ASC LIMIT 1",
            row["id"],
        )
        if actor is not None:
            remitente = {
                "NOM_R": actor["nombre"],
                "ENT_R": actor["entidad"],
                "DIR_R": actor["direccion"],
                "MPIO_R": actor["municipio"],
                "DEPTO_R": actor["departamento"],
                "TEL_R": actor["telefono"],
                "MAIL_R": actor["email"],
                "DOC_R": actor["numero_documento"],
            }
        else:
            # Radicados anteriores al modelo de actores: `sender_name` y
            # `sender_entity` siguen siendo la única fuente. Se usan en vez de
            # dejar el oficio sin destinatario.
            remitente = {"NOM_R": row["sender_name"], "ENT_R": row["sender_entity"]}
        ctx.update(remitente)

        # Responder es dirigirse a quien escribió: el destinatario de la salida
        # es, por defecto, el remitente del padre. Se PRERRELLENA, no se impone
        # — cuando el borrador tenga sus propios destinatarios, mandan ellos.
        for clave, valor in remitente.items():
            ctx.setdefault(clave.replace("_R", "_P"), valor)

        return ctx
