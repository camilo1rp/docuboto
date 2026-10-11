# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""B6 (ronda de corrección): catálogo de tipos, claves reservadas, procedencia, origen
de la Salida y orden validar-antes-de-producir.

Cada prueba se ejerce por mutación (ver la bitácora de la tanda): quitar la línea que
protege tiene que ponerla en rojo.
"""

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from app.schemas.document import CLAVES_RESERVADAS, DocumentCreate, DocumentUpdate
from tests.conftest import TENANT_HEADERS, FakeTenantClient, make_radicado_record

_USER = str(uuid.uuid4())
_HEADERS = {**TENANT_HEADERS, "X-User-Id": _USER}
_CLEARANCE = {"is_root": False, "max_level": 3}


def _fetchval_catalogo(*, tipos: set[str], plantilla_sql: str = "metadata_templates"):
    """`fetchval` que responde de verdad al catálogo: el código buscado va en `$1`.

    Un `True` a secas daría verde con la validación puesta y con ella quitada.
    """

    async def _fv(sql: str, *args: object):
        if "FROM tipos_documentales" in sql:
            return "Nombre" if args and args[0] in tipos else None
        return 1  # RBAC, consecutivo, hash previo del asiento

    return _fv


# ----------------------------------------------------------------------- ítem 1


@pytest.mark.asyncio
async def test_radicar_con_tipo_inexistente_es_422_y_no_gasta_consecutivo(
    client_with_conn,
) -> None:
    client, conn = client_with_conn
    conn.fetchval.side_effect = _fetchval_catalogo(tipos={"Oficio"})
    conn.fetchrow.return_value = None
    conn.fetch.return_value = []
    resp = await client.post(
        "/api/v1/documents",
        json={"doc_type": "E", "subject": "x", "doc_class": "Inventado", "dest_dept_code": 1},
        headers=_HEADERS,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "tipo_documental_desconocido"
    consultas = [str(c.args[0]) for c in conn.fetchval.await_args_list if c.args]
    assert not any("tracking_sequences" in q for q in consultas)  # sin número gastado


@pytest.mark.asyncio
async def test_radicar_con_tipo_del_catalogo_pasa(client_with_conn) -> None:
    """Control positivo: la validación no cierra el camino legítimo."""
    client, conn = client_with_conn
    conn.fetchval.side_effect = _fetchval_catalogo(tipos={"Oficio"})
    conn.fetchrow.side_effect = [None, {"code": "DEMO"}, make_radicado_record()]
    conn.fetch.return_value = []
    resp = await client.post(
        "/api/v1/documents",
        json={"doc_type": "E", "subject": "x", "doc_class": "Oficio", "dest_dept_code": 1},
        headers=_HEADERS,
    )
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_retipificar_a_tipo_inexistente_es_422_sin_escribir(client_with_conn) -> None:
    client, conn = client_with_conn
    doc_id = uuid.uuid4()
    conn.fetchval.side_effect = _fetchval_catalogo(tipos={"Oficio"})
    conn.fetchrow.side_effect = [_CLEARANCE, make_radicado_record(doc_id=doc_id)]
    conn.fetch.return_value = []
    resp = await client.patch(
        f"/api/v1/documents/{doc_id}", json={"doc_class": "Inventado"}, headers=_HEADERS
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "tipo_documental_desconocido"
    escritos = [c for c in conn.fetchrow.await_args_list if "UPDATE radicados" in str(c.args[0])]
    assert not escritos


@pytest.mark.asyncio
async def test_el_lote_no_radica_un_tipo_inexistente() -> None:
    from fastapi import HTTPException

    from app.core.tipos import exigir_tipo_documental

    conn = AsyncMock()
    conn.fetchval.side_effect = _fetchval_catalogo(tipos=set())
    with pytest.raises(HTTPException) as exc:
        await exigir_tipo_documental(conn, "Inventado")
    assert exc.value.detail["error"] == "tipo_documental_desconocido"
    # Sin tipo no se juzga (lo exige `datos_minimos` según el tipo de radicado).
    conn.fetchval.reset_mock()
    await exigir_tipo_documental(conn, None)
    conn.fetchval.assert_not_called()


def test_el_batch_valida_el_catalogo() -> None:
    """El lote es una puerta más: el cableado, no solo la pieza."""
    from pathlib import Path

    fuente = (Path(__file__).parents[1] / "app/services/batch_service.py").read_text()
    assert "exigir_tipo_documental(self.conn, doc_data.doc_class)" in fuente


def test_la_ventanilla_y_create_comparten_la_consulta_del_catalogo() -> None:
    """Un solo sitio: ni `_validar_tipo` ni `create` escriben ya su propio SELECT."""
    import inspect

    from app.services.document_service import DocumentService
    from app.services.ventanilla_service import VentanillaService

    assert "tipos_documentales" not in inspect.getsource(VentanillaService._validar_tipo)
    assert "tipos_documentales" not in inspect.getsource(DocumentService)
    assert "nombre_tipo_documental" in inspect.getsource(VentanillaService._validar_tipo)


# ----------------------------------------------------------------------- ítem 2


@pytest.mark.parametrize("clave", sorted(CLAVES_RESERVADAS))
def test_documentcreate_rechaza_cada_clave_reservada(clave: str) -> None:
    base = {"doc_type": "I", "subject": "x", "doc_class": "Oficio",
            "origin_dept_code": 1, "dest_dept_code": 1}
    with pytest.raises(ValidationError, match=clave):
        DocumentCreate(**base, metadata={clave: "x"})
    assert DocumentCreate(**base, metadata={"otra": 1}).metadata == {"otra": 1}


def test_la_lista_reservada_es_la_acordada() -> None:
    assert CLAVES_RESERVADAS == {"from_borrador", "canal", "consentimiento"}


@pytest.mark.parametrize("clave", sorted(CLAVES_RESERVADAS))
def test_el_patch_de_metadata_rechaza_las_reservadas(clave: str) -> None:
    with pytest.raises(ValidationError, match=clave):
        DocumentUpdate(metadata={clave: "x"})
    assert DocumentUpdate(metadata={"ok": 1}).metadata == {"ok": 1}
    assert DocumentUpdate().metadata is None


@pytest.mark.parametrize("clave", sorted(CLAVES_RESERVADAS))
def test_borrador_y_respuesta_usan_la_misma_lista(clave: str) -> None:
    from app.schemas.borrador import BorradorRadicar
    from app.schemas.respuesta import RespuestaRapidaCreate

    with pytest.raises(ValidationError):
        BorradorRadicar(metadata={clave: "x"})
    with pytest.raises(ValidationError):
        RespuestaRapidaCreate(metadata={clave: "x"})


@pytest.mark.asyncio
async def test_post_documents_con_canal_del_cliente_es_422(client_with_conn) -> None:
    client, conn = client_with_conn
    resp = await client.post(
        "/api/v1/documents",
        json={"doc_type": "E", "subject": "x", "doc_class": "Oficio", "dest_dept_code": 1,
              "metadata": {"canal": "ventanilla_publica"}},
        headers=_HEADERS,
    )
    assert resp.status_code == 422
    conn.execute.assert_not_called()


def test_el_sistema_pone_canal_y_consentimiento_por_procedencia() -> None:
    """La ventanilla (legítima) las manda por `procedencia`, no en `metadata`."""
    import inspect

    from app.services.ventanilla_service import VentanillaService

    src = inspect.getsource(VentanillaService.radicar)
    assert "procedencia=procedencia" in src
    bloque = src.split("procedencia = {")[1].split("doc = DocumentCreate")[0]
    assert '"canal": CANAL' in bloque and '"consentimiento"' in bloque
    metadata_doc = src.split("doc = DocumentCreate")[1].split("try:")[0]
    assert '"canal"' not in metadata_doc and '"consentimiento"' not in metadata_doc


# ----------------------------------------------------------------------- ítem 3


@pytest.mark.asyncio
async def test_el_asiento_de_creacion_lleva_from_borrador(client_with_conn) -> None:
    from app.services.document_service import DocumentService

    _c, conn = client_with_conn
    conn.fetchrow.side_effect = [None, {"code": "DEMO"}, make_radicado_record()]
    conn.fetchval.side_effect = _fetchval_catalogo(tipos={"Oficio"})
    conn.fetch.return_value = []
    bid = str(uuid.uuid4())
    doc = DocumentCreate(doc_type="E", subject="x", doc_class="Oficio", dest_dept_code=1)
    await DocumentService(conn, "demo", FakeTenantClient()).create(  # type: ignore[arg-type]
        doc, None, procedencia={"from_borrador": bid}
    )
    asientos = [
        c.args for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == "document.radicado_created"
    ]
    assert len(asientos) == 1
    assert json.loads(asientos[0][8])["from_borrador"] == bid


@pytest.mark.asyncio
async def test_sin_borrador_el_asiento_no_inventa_from_borrador(client_with_conn) -> None:
    from app.services.document_service import DocumentService

    _c, conn = client_with_conn
    conn.fetchrow.side_effect = [None, {"code": "DEMO"}, make_radicado_record()]
    conn.fetchval.side_effect = _fetchval_catalogo(tipos={"Oficio"})
    conn.fetch.return_value = []
    doc = DocumentCreate(doc_type="E", subject="x", doc_class="Oficio", dest_dept_code=1)
    await DocumentService(conn, "demo", FakeTenantClient()).create(doc, None)  # type: ignore[arg-type]
    asientos = [
        c.args for c in conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == "document.radicado_created"
    ]
    assert "from_borrador" not in json.loads(asientos[0][8])


@pytest.mark.asyncio
async def test_el_patch_de_metadata_preserva_las_claves_reservadas(client_with_conn) -> None:
    client, conn = client_with_conn
    doc_id = uuid.uuid4()
    bid = str(uuid.uuid4())
    previo = make_radicado_record(
        doc_id=doc_id, metadata={"from_borrador": bid, "canal": "x", "viejo": 1}
    )
    conn.fetchval.side_effect = _fetchval_catalogo(tipos={"Oficio"})
    conn.fetchrow.side_effect = [_CLEARANCE, previo, None, make_radicado_record(doc_id=doc_id)]
    conn.fetch.return_value = []
    resp = await client.patch(
        f"/api/v1/documents/{doc_id}", json={"metadata": {"nuevo": 2}}, headers=_HEADERS
    )
    assert resp.status_code == 200, resp.text
    llamada = next(c for c in conn.fetchrow.await_args_list if "UPDATE radicados" in str(c.args[0]))
    escrito = next(json.loads(a) for a in llamada.args[1:] if isinstance(a, str) and a.startswith("{"))
    assert escrito == {"nuevo": 2, "from_borrador": bid, "canal": "x"}  # `viejo` sí se reemplaza


@pytest.mark.asyncio
async def test_retipificar_valida_los_datos_del_tipo_sin_la_procedencia(client_with_conn) -> None:
    """Una plantilla con `additionalProperties: false` no debe rechazar `from_borrador`."""
    client, conn = client_with_conn
    doc_id = uuid.uuid4()
    previo = make_radicado_record(doc_id=doc_id, metadata={"from_borrador": "b", "n": "1"})
    plantilla = {
        "id": uuid.uuid4(),
        "json_schema": json.dumps({"type": "object", "properties": {"n": {"type": "string"}},
                                   "additionalProperties": False}),
    }
    conn.fetchval.side_effect = _fetchval_catalogo(tipos={"Oficio", "Contratos"})
    conn.fetchrow.side_effect = [_CLEARANCE, previo, plantilla,
                                 make_radicado_record(doc_id=doc_id, doc_class="Contratos")]
    conn.fetch.return_value = []
    resp = await client.patch(
        f"/api/v1/documents/{doc_id}", json={"doc_class": "Contratos"}, headers=_HEADERS
    )
    assert resp.status_code == 200, resp.text


# ----------------------------------------------------------------------- ítem 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("elegido", "propias", "doc_type", "actor", "pasa"),
    [
        (7, [1, 2], "S", True, False),  # dependencia ajena: 422
        (2, [1, 2], "S", True, True),  # propia: pasa
        (7, [], "S", True, False),  # sin URD activa: fail-closed
        (7, [1, 2], "I", True, True),  # solo se limita la Salida
        (7, [1, 2], "E", True, True),  # una Entrada no emite
        (7, [], "S", False, True),  # sin actor humano (sistema): no aplica
    ],
)
async def test_create_limita_el_origen_de_la_salida_a_las_dependencias_del_actor(
    elegido: int, propias: list[int], doc_type: str, actor: bool, pasa: bool
) -> None:
    """UNA fuente (`DocumentService.create`) para todas las puertas."""
    from fastapi import HTTPException

    from app.services.document_service import DocumentService

    conn = AsyncMock()
    conn.fetch.return_value = [{"depe_id": d} for d in propias]
    conn.fetchval.return_value = None
    svc = DocumentService(conn, "demo", FakeTenantClient())
    payload = DocumentCreate(
        doc_type=doc_type, subject="x", doc_class="Oficio",
        origin_dept_code=elegido, dest_dept_code=1,
    )
    tc = FakeTenantClient()
    tc.dependencias[elegido] = {"codigo": elegido, "sigla": "X", "nombre": "X", "activo": True}
    svc = DocumentService(conn, "demo", tc)
    with pytest.raises(Exception) as exc:  # noqa: PT011 - o el 422 o lo que siga aguas abajo
        await svc.create(payload, uuid.uuid4() if actor else None)
    es_origen = (
        isinstance(exc.value, HTTPException)
        and isinstance(exc.value.detail, dict)
        and exc.value.detail.get("error") == "origen_fuera_de_sus_dependencias"
    )
    assert es_origen is (not pasa)
    if not pasa:
        consultas = [str(c.args[0]) for c in conn.fetchval.await_args_list if c.args]
        assert not any("tracking_sequences" in q for q in consultas)  # sin número gastado


def test_las_puertas_de_salida_delegan_en_create_y_el_borrador_no_duplica() -> None:
    from pathlib import Path

    raiz = Path(__file__).parents[1] / "app/services"
    assert "origen_fuera_de_sus_dependencias" not in (raiz / "borrador_service.py").read_text()
    assert "origen_fuera_de_sus_dependencias" in (raiz / "document_service.py").read_text()
    # Respuesta rápida pasa por `create` con el actor.
    assert "await self.create(salida, registered_by)" in (
        raiz / "document_service.py"
    ).read_text()


@pytest.mark.asyncio
async def test_el_lote_limita_el_origen_de_la_salida() -> None:
    from pathlib import Path

    fuente = (Path(__file__).parents[1] / "app/services/batch_service.py").read_text()
    assert 'doc_data.doc_type == "S"' in fuente
    assert "origen_fuera_de_sus_dependencias" in fuente


@pytest.mark.asyncio
async def test_reenviar_el_mismo_tipo_ya_inactivo_en_el_patch_no_da_422(client_with_conn) -> None:
    """Retipificar exige tipo ACTIVO; reenviar el que ya tiene (hoy inactivo) no es retipificar."""
    client, conn = client_with_conn
    doc_id = uuid.uuid4()
    conn.fetchval.side_effect = _fetchval_catalogo(tipos=set())  # ningún tipo activo
    previo = make_radicado_record(doc_id=doc_id, doc_class="Oficio")
    conn.fetchrow.side_effect = [_CLEARANCE, previo, None, previo]
    conn.fetch.return_value = []
    resp = await client.patch(
        f"/api/v1/documents/{doc_id}", json={"doc_class": "Oficio"}, headers=_HEADERS
    )
    assert resp.status_code != 422 or (
        resp.json()["detail"].get("error") != "tipo_documental_desconocido"
    ), resp.text


def test_import_mueve_las_claves_reservadas_a_procedencia_origen() -> None:
    from app.services.import_service import _sin_claves_reservadas

    out = _sin_claves_reservadas(
        {"canal": "ventanilla", "consentimiento": {"x": 1}, "from_borrador": "b", "otra": 2}
    )
    assert out == {
        "otra": 2,
        "procedencia_origen": {
            "canal": "ventanilla", "consentimiento": {"x": 1}, "from_borrador": "b",
        },
    }
    assert not (CLAVES_RESERVADAS & set(out))
    assert _sin_claves_reservadas(None) == {}
    assert _sin_claves_reservadas({"otra": 1}) == {"otra": 1}


@pytest.mark.asyncio
async def test_import_insert_no_guarda_claves_reservadas_de_primer_nivel() -> None:
    from app.services.import_service import ImportService

    svc = ImportService(AsyncMock(), "demo")
    svc._tenant_code = "OTRO"
    svc._repo = AsyncMock()
    svc._repo.insert_radicado.return_value = uuid.uuid4()
    entity = {
        "numero_radicado": "2020-ORIG-E-000001", "tipo": "E", "asunto": "a",
        "nivel_seguridad": 1,
        "metadata_contextual": {"canal": "ventanilla", "consentimiento": {"ok": True}},
    }
    import re

    m = re.match(r"^(\d{4})-([A-Z0-9]+)-([ESI])-(\d{6})$", entity["numero_radicado"])
    await svc._insert(entity, m, [])
    meta = svc._repo.insert_radicado.await_args.args[0]["metadata"]
    assert not (CLAVES_RESERVADAS & set(meta))
    assert meta["procedencia_origen"]["canal"] == "ventanilla"


# ----------------------------------------------------------------------- ítem 5


def _borrador_row(bid, **kw):
    from tests.test_borradores import _borrador_row as base

    return base(id=bid, doc_type="S", estado="aprobado", **kw)


@pytest.mark.asyncio
async def test_metadata_invalida_falla_antes_de_renderizar_y_subir(
    client_with_conn, monkeypatch
) -> None:
    """El 422 llega ANTES de `_producir` (render + subida): ningún binario huérfano."""
    from app.services import borrador_service as svc
    from app.services.metadata_service import MetadataValidationError

    async def _minimos(self, row, registered_by, datos=None):
        return "Oficio", 1, 1

    monkeypatch.setattr(svc.BorradorService, "_datos_minimos_del_borrador", _minimos)
    producir = AsyncMock(side_effect=AssertionError("no debe renderizar"))
    monkeypatch.setattr(svc.BorradorService, "_producir", producir)

    client, conn = client_with_conn
    bid = uuid.uuid4()
    conn.fetchval.return_value = True
    conn.fetch.return_value = []
    conn.fetchrow.side_effect = [_CLEARANCE, _borrador_row(bid, cuerpo="Texto")]
    with patch(
        "app.services.document_service.DocumentService.validar_tipo_y_metadata",
        AsyncMock(side_effect=MetadataValidationError("falta x", field="x")),
    ):
        resp = await client.post(
            f"/api/v1/borradores/{bid}/radicar",
            json={"tipo_documental": "Oficio", "metadata": {}},
            headers=_HEADERS,
        )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "metadata_validation_failed"
    producir.assert_not_awaited()
    assert not any("tracking_sequences" in str(c.args[0]) for c in conn.fetchval.await_args_list)
