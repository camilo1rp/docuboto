# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Ejecutor de procedimientos (E18, ADR-019).

Recorre los pasos declarados en orden, encadenando la salida de uno con la
entrada del siguiente y llamando al gateway **con el token de la persona** en
cada paso.

── Lo que este ejecutor NO hace, y hay que decirlo ─────────────────────────

**No deshace nada.** Los pasos son llamadas HTTP a servicios distintos: no hay
transacción que los abarque, y aunque la hubiera, los actos de este dominio no
son reversibles — radicar consume un número inmutable que no vuelve al
mostrador. Si el tercer paso falla, los dos primeros ocurrieron y siguen siendo
válidos.

Por eso el resultado es un **parte de lo ocurrido**, paso por paso, y no un
booleano: quien pregunta necesita saber que tiene un radicado con número y sin
repartir, no un «falló» que le haría radicarlo otra vez. Un procedimiento que
fingiera atomicidad produciría radicados duplicados, que es el daño más caro y
menos reversible que puede causar este sistema.

**No amplía permisos.** Cada paso lleva el `Authorization` del usuario, así que
cada gate se evalúa igual que si hubiera llamado a la tool a mano. Un
procedimiento no es una credencial: es un orden.
"""

from __future__ import annotations

from typing import Any

import httpx

from app.services.dispatcher import DispatchError, authorize, build_request, execute
from app.tools.catalog import ArgError, coerce_args
from app.tools.procedures import Procedimiento, ProcedureError, resolver_args, tool_de


async def ejecutar(
    client: httpx.AsyncClient,
    proc: Procedimiento,
    args: dict[str, Any],
    *,
    authorization: str | None,
    tenant_slug: str | None,
    permissions: set[str],
    is_root: bool,
    levels: dict[str, int] | None = None,
    asistente_habilitado: bool = False,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Ejecuta el procedimiento y devuelve el parte de lo ocurrido.

    La identidad del solicitante entra aquí porque **cada paso se reautoriza**.
    Antes se comprobaba el permiso declarado por el procedimiento COMO UN TODO, y
    sus pasos se despachaban después sin volver a mirar nada: un procedimiento
    con un permiso llano podía encadenar herramientas que exigen otro más alto, y
    quien lo ejecutara haría por esa vía lo que no puede hacer directamente. El
    permiso agregado es una comodidad para ofrecerlo, no una autorización para
    todo lo que contiene.
    """
    salidas: dict[str, Any] = {}
    parte: list[dict[str, Any]] = []

    for n, paso in enumerate(proc.pasos, start=1):
        if paso.si_hay and args.get(paso.si_hay) in (None, ""):
            parte.append(
                {
                    "paso": paso.etiqueta,
                    "estado": "omitido",
                    "motivo": f"no se aportó «{paso.si_hay}»",
                }
            )
            continue

        herramienta = tool_de(paso)

        # Reautorización POR PASO, con la identidad de quien pidió el
        # procedimiento. Un paso que el solicitante no podría invocar por su
        # cuenta tampoco se ejecuta desde aquí.
        try:
            authorize(
                herramienta,
                permissions=permissions,
                is_root=is_root,
                levels=levels,
                asistente_habilitado=asistente_habilitado,
            )
        except DispatchError:
            # Se devuelve un PARTE, no una excepción en bruto: quien pidió el
            # procedimiento tiene que saber en qué paso se detuvo y por qué, y
            # los pasos ya ejecutados quedan reflejados. El motivo no nombra el
            # permiso que falta, por el mismo criterio de no-oráculo del resto.
            parte.append(
                {
                    "paso": paso.etiqueta,
                    "estado": "denegado",
                    "motivo": "no tiene permiso para este paso del procedimiento",
                }
            )
            return _parte(proc, parte, completado=False)

        try:
            crudos = resolver_args(paso, args, salidas)
            argumentos = coerce_args(herramienta, crudos)
        except (ProcedureError, ArgError) as exc:
            # Enlace roto: el paso anterior no devolvió lo que este necesita.
            # Se corta — seguir significaría enlazar contra un identificador
            # ausente y dejar un vínculo huérfano que nadie va a notar.
            parte.append({"paso": paso.etiqueta, "estado": "fallido", "motivo": str(exc)})
            return _parte(proc, parte, completado=False)

        preparada = build_request(herramienta, argumentos)
        try:
            resp = await execute(
                client,
                preparada,
                authorization=authorization,
                tenant_slug=tenant_slug,
                # Un procedimiento son varios actos: cada paso lleva su propia
                # clave (`<jti>:<n>`) para que, el día que un servicio la honre
                # como idempotencia, dos pasos distintos no se tomen por uno.
                idempotency_key=f"{idempotency_key}:{n}" if idempotency_key else None,
            )
        except httpx.RequestError as exc:
            parte.append(
                {
                    "paso": paso.etiqueta,
                    "estado": "fallido",
                    "motivo": f"no se pudo contactar el servicio: {exc}",
                }
            )
            if paso.opcional:
                continue
            return _parte(proc, parte, completado=False)

        cuerpo: Any = None
        if resp.content:
            try:
                cuerpo = resp.json()
            except ValueError:
                cuerpo = None

        if resp.status_code >= 400:
            registro = {
                "paso": paso.etiqueta,
                "estado": "fallido",
                "http": resp.status_code,
                "motivo": _motivo(cuerpo, resp),
            }
            parte.append(registro)
            if paso.opcional:
                continue
            return _parte(proc, parte, completado=False)

        if isinstance(cuerpo, dict):
            salidas[paso.etiqueta] = cuerpo
        parte.append(
            {
                "paso": paso.etiqueta,
                "estado": "hecho",
                "http": resp.status_code,
                "resultado": cuerpo,
            }
        )

    return _parte(proc, parte, completado=True)


def _motivo(cuerpo: Any, resp: httpx.Response) -> str:
    """El motivo que devolvió el servicio, sin inventarlo.

    Se prefiere el `detail` del contrato de error del sistema; si no lo hay, el
    texto crudo acotado. Sustituirlo por una frase propia («no se pudo
    completar») borraría justo lo que quien lee necesita para decidir qué hacer.
    """
    if isinstance(cuerpo, dict):
        detalle = cuerpo.get("detail")
        if isinstance(detalle, dict):
            return str(detalle.get("detail") or detalle.get("error") or detalle)
        if isinstance(detalle, str):
            return detalle
        if cuerpo.get("error"):
            return str(cuerpo["error"])
    return resp.text[:300]


def _parte(proc: Procedimiento, pasos: list[dict[str, Any]], *, completado: bool) -> dict[str, Any]:
    hechos = [p["paso"] for p in pasos if p["estado"] == "hecho"]
    return {
        "procedimiento": proc.name,
        "completado": completado,
        "pasos": pasos,
        # Se repite en claro lo que YA ocurrió, aunque esté en `pasos`: cuando
        # un procedimiento se corta a mitad, esta lista es lo que impide que
        # alguien lo vuelva a lanzar entero y duplique un radicado.
        "pasos_ejecutados": hechos,
        "advertencia": (
            None
            if completado
            else (
                "El procedimiento se detuvo. Los pasos ya ejecutados NO se deshacen "
                "—un número de radicado no se devuelve—: revise lo hecho y continúe "
                "desde donde quedó, no lo repita entero."
            )
        ),
    }
