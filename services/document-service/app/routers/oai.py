# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Endpoint OAI-PMH 2.0 (E11, RF-INT-02) — cosecha de metadatos PÚBLICOS por tenant.

`GET /api/v1/public/oai/{tenant}` — bajo `/api/v1/public/` el gateway NO exige JWT
(cosecha abierta de metadatos públicos, sin tocar el gateway, como el webhook postal
E20). El tenant va en el path (el cosechador no manda JWT); `search_path` se fija a
ESE tenant. El recorte de seguridad (solo `nivel_seguridad=1`, no anulado) vive en el
repositorio → la omisión de reservados es indistinguible (spec §5 inv. 11; Ley
1712/2014 arts. 18-19; RF-SEG-08).
"""

import asyncpg
from fastapi import APIRouter, Depends, Request, Response
from orpycamcp_common.tenant import AtencionPublica, atencion_publica

from app.core.database import tenant_schema
from app.core.public_audit import audit_public_access
from app.core.public_interop import require_public_interop
from app.repositories.oai_repository import OaiRepository
from app.services.oai_service import OaiError, OaiService

# Guard de interoperabilidad pública ANÓNIMA (E11): con el interruptor apagado —el
# default— estas rutas responden 404, indistinguible de una ruta inexistente. Ver
# `app/core/public_interop.py` para el razonamiento completo.
router = APIRouter(tags=["oai"], dependencies=[Depends(require_public_interop)])

_VERBS = {
    "Identify": (set(), set()),
    "ListMetadataFormats": ({"identifier"}, set()),
    "ListSets": ({"resumptionToken"}, set()),
    "ListIdentifiers": ({"from", "until", "set", "resumptionToken", "metadataPrefix"}, set()),
    "ListRecords": ({"from", "until", "set", "resumptionToken", "metadataPrefix"}, set()),
    "GetRecord": ({"identifier", "metadataPrefix"}, {"identifier", "metadataPrefix"}),
}
_LISTS = {"ListIdentifiers", "ListRecords"}


def _validate(verb: str, params: dict[str, str]) -> None:
    """Reglas de argumentos OAI-PMH → badVerb / badArgument (sin tocar la BD)."""
    if verb not in _VERBS:
        raise OaiError("badVerb", f"Verbo desconocido o ausente: {verb!r}")
    allowed, required = _VERBS[verb]
    given = set(params) - {"verb"}
    unknown = given - allowed
    if unknown:
        raise OaiError("badArgument", f"Argumentos no permitidos: {sorted(unknown)}")
    if "resumptionToken" in given:
        # Es EXCLUSIVO: no combina con from/until/set/metadataPrefix.
        if given - {"resumptionToken"}:
            raise OaiError("badArgument", "resumptionToken es exclusivo")
        return
    missing = required - given
    # Las listas exigen metadataPrefix salvo que reanuden con token.
    if verb in _LISTS and "metadataPrefix" not in given:
        missing = missing | {"metadataPrefix"}
    if missing:
        raise OaiError("badArgument", f"Faltan argumentos: {sorted(missing)}")


@router.get("/api/v1/public/oai/{tenant}", summary="OAI-PMH 2.0 — cosecha de metadatos (E11)")
async def oai_pmh(tenant: str, request: Request) -> Response:
    base_url = str(request.url).split("?", 1)[0]
    params = dict(request.query_params)
    verb = params.get("verb", "")

    # El schema se valida ANTES de tocar la BD (error_xml no usa el repo).
    try:
        schema = tenant_schema(tenant)
    except Exception:  # tenant inválido
        svc0 = OaiService(None, base_url=base_url, tenant=tenant)  # type: ignore[arg-type]
        body = svc0.error_xml({"verb": verb} if verb else {}, "badArgument", "Tenant inválido")
        return Response(body, media_type="application/xml", status_code=400)

    # request_attrs reflejados en <request> de las respuestas de error (error_xml los
    # descarta para badVerb/badArgument, regla OAI).
    req_attrs = {k: v for k, v in params.items() if v != ""}
    pool: asyncpg.Pool = request.app.state.db_pool
    async with pool.acquire() as conn:
        # ADR-035: inexistente y sin aprovisionar responden el MISMO 400 badArgument
        # que un slug malformado (la suspendida SÍ se sirve: es solo lectura), y el estado se lee ANTES de fijar el search_path.
        if await atencion_publica(conn, tenant) is AtencionPublica.NINGUNA:
            svc0 = OaiService(None, base_url=base_url, tenant=tenant)  # type: ignore[arg-type]
            body = svc0.error_xml({}, "badArgument", "Tenant inválido")
            return Response(body, media_type="application/xml", status_code=400)
        await conn.execute(f'SET search_path = "{schema}", public')
        try:
            svc = OaiService(OaiRepository(conn), base_url=base_url, tenant=tenant)
            try:
                _validate(verb, params)
                body = await _dispatch(svc, verb, params)
                # Traza de la cosecha ANÓNIMA (best-effort, ver core/public_audit.py).
                # Se asienta el verbo y los argumentos, no el resultado: el payload
                # de auditoría no debe convertirse en una segunda copia de lo
                # cosechado, y el `bytes` sí permite dimensionar cuánto se llevó.
                await audit_public_access(
                    conn,
                    tenant_slug=tenant,
                    action="document.oai_cosecha",
                    object_type="oai_request",
                    object_ref=verb,
                    payload={
                        "verbo": verb,
                        "argumentos": req_attrs,
                        "bytes": len(body),
                    },
                )
                return Response(body, media_type="application/xml")
            except OaiError as exc:
                body = svc.error_xml(req_attrs, exc.code, exc.message)
                return Response(body, media_type="application/xml")
        finally:
            await conn.execute("SET search_path = public")


async def _dispatch(svc: OaiService, verb: str, params: dict[str, str]) -> bytes:
    req_attrs = {k: v for k, v in params.items() if v != ""}
    if verb == "Identify":
        return await svc.identify(req_attrs)
    if verb == "ListMetadataFormats":
        return await svc.list_metadata_formats(req_attrs, params.get("identifier"))
    if verb == "ListSets":
        return await svc.list_sets(req_attrs)
    if verb == "GetRecord":
        return await svc.get_record(req_attrs, params["identifier"], params["metadataPrefix"])
    # ListIdentifiers / ListRecords
    return await svc.list_records(req_attrs, params, headers_only=(verb == "ListIdentifiers"))
