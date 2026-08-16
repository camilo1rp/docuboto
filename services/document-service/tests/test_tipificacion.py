# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tipificar un radicado ya creado (E03/E04).

`doc_class` solo se podía fijar AL RADICAR: un documento mal tipificado en
ventanilla —o radicado sin tipificar, que el schema permite— se quedaba así para
siempre, y con él la serie TRD que gobierna su retención y su disposición final.
No era una restricción deliberada; no había endpoint que lo hiciera.

Cada test fija una de las tres cosas que este acto necesita y que las demás
mutaciones del PATCH no necesitan: **nivel de escritura**, **revalidación de los
metadatos** y **traza del antes y el después**.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.conftest import TENANT_HEADERS, make_radicado_record

_USER = str(uuid.uuid4())
_HEADERS = {**TENANT_HEADERS, "X-User-Id": _USER}


def _clearance_row(max_level: int = 3) -> dict:
    return {"is_root": False, "max_level": max_level}


def _permiso_por_nivel(nivel_concedido: int):
    """`fetchval` que responde al nivel REALMENTE pedido en la consulta RBAC.

    Un mock que devuelva `True` a secas no discrimina: daría verde con el gate
    puesto y con el gate quitado, que es la clase de test que no sirve para nada.
    `check_permission` pasa `min_crud` como `$3`, así que se lee de ahí.
    """

    async def _fetchval(*args, **kwargs):
        if len(args) >= 4 and isinstance(args[3], int):
            return args[3] <= nivel_concedido
        return True

    return _fetchval


def _plantilla(schema: dict) -> dict:
    return {"id": uuid.uuid4(), "json_schema": json.dumps(schema)}


_SCHEMA_ESTRICTO = {
    "type": "object",
    "properties": {"numero_contrato": {"type": "string"}},
    "required": ["numero_contrato"],
}


@pytest.mark.asyncio
async def test_tipificar_exige_nivel_de_creacion(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Con `PERM_RADI` de solo lectura se puede corregir un asunto, no retipificar.

    Retipificar fija la serie TRD, y con ella cuánto se conserva el documento y
    si acaba destruido (Ley 594/2000 art. 24). Que eso pasara con permiso de
    LECTURA es el mismo defecto que ya se corrigió en `disposition`.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = _permiso_por_nivel(1)

    resp = await client.patch(
        f"/api/v1/documents/{uuid.uuid4()}",
        json={"doc_class": "Contratos"},
        headers=_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    # Y no llegó a tocarse la base: la negativa va ANTES de la mutación.
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_el_resto_del_patch_sigue_bastando_con_nivel_de_lectura(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Control positivo, y no es una formalidad.

    Subir el gate del endpoint entero a `crud>=3` habría sido lo cómodo, y habría
    roto a todo el que hoy corrige un asunto o un número de folios con el nivel
    que siempre le bastó. El acto nuevo entra con su umbral; los que ya existían
    conservan el suyo.
    """
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchval.side_effect = _permiso_por_nivel(1)
    mock_conn.fetchrow.return_value = make_radicado_record(doc_id=doc_id, subject="Corregido")
    mock_conn.fetch.return_value = []

    resp = await client.patch(
        f"/api/v1/documents/{doc_id}",
        json={"subject": "Corregido"},
        headers=_HEADERS,
    )

    assert resp.status_code == 200
    assert resp.json()["subject"] == "Corregido"


@pytest.mark.asyncio
async def test_retipificar_revalida_los_metadatos_contra_el_tipo_nuevo(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Un radicado no puede quedar tipificado como X con los campos de Y.

    Es el corazón del arreglo. Sin esta revalidación, retipificar habría sido un
    `UPDATE doc_class` que dejaba metadatos huérfanos del tipo anterior — y el
    radicado habría pasado a declarar una conformidad con la plantilla que ya no
    tenía.
    """
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchval.side_effect = _permiso_por_nivel(3)
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),  # resolve_caller_clearance
        make_radicado_record(doc_id=doc_id, doc_class="Derechos de Petición", metadata={}),
        _plantilla(_SCHEMA_ESTRICTO),  # plantilla activa del tipo NUEVO
    ]
    mock_conn.fetch.return_value = []

    resp = await client.patch(
        f"/api/v1/documents/{doc_id}",
        json={"doc_class": "Contratos"},  # sin `numero_contrato`
        headers=_HEADERS,
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "metadata_validation_failed"


@pytest.mark.asyncio
async def test_retipificar_con_los_metadatos_del_tipo_nuevo_pasa(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El camino bueno: tipo nuevo y sus campos, en el mismo acto.

    Un radicado retipificado tiene que quedar indistinguible de uno radicado así
    desde el principio; por eso la comprobación es la MISMA que hace `create`.
    """
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchval.side_effect = _permiso_por_nivel(3)
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        make_radicado_record(doc_id=doc_id, doc_class="Derechos de Petición", metadata={}),
        _plantilla(_SCHEMA_ESTRICTO),
        make_radicado_record(
            doc_id=doc_id, doc_class="Contratos", metadata={"numero_contrato": "C-2026-1"}
        ),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.patch(
        f"/api/v1/documents/{doc_id}",
        json={"doc_class": "Contratos", "metadata": {"numero_contrato": "C-2026-1"}},
        headers=_HEADERS,
    )

    assert resp.status_code == 200
    assert resp.json()["doc_class"] == "Contratos"


@pytest.mark.asyncio
async def test_cambiar_solo_los_metadatos_tambien_se_valida(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Si no, el gate de la radicación sería puenteable con un PATCH posterior.

    Radicar valida los metadatos contra la plantilla activa; permitir luego un
    PATCH que los reemplace sin validar habría dejado esa validación como un
    trámite de la primera pantalla, no como una regla del documento.
    """
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchval.side_effect = _permiso_por_nivel(3)
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        make_radicado_record(
            doc_id=doc_id, doc_class="Contratos", metadata={"numero_contrato": "C-1"}
        ),
        _plantilla(_SCHEMA_ESTRICTO),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.patch(
        f"/api/v1/documents/{doc_id}",
        json={"metadata": {"otra_cosa": "x"}},  # falta `numero_contrato`
        headers=_HEADERS,
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_retipificar_a_un_tipo_sin_plantilla_suelta_la_version_anterior(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """El `NULL` explícito, que es el detalle que se olvida.

    Si `metadata_template_id` no se reescribiera, el radicado quedaría apuntando
    a la plantilla del tipo ANTERIOR: una traza que afirma que sus metadatos se
    validaron contra un esquema que ya no le corresponde. Se comprueba sobre el
    SQL realmente enviado, no sobre el código fuente.
    """
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchval.side_effect = _permiso_por_nivel(3)
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        make_radicado_record(doc_id=doc_id, doc_class="Derechos de Petición"),
        None,  # el tipo nuevo NO tiene plantilla activa
        make_radicado_record(doc_id=doc_id, doc_class="Memorandos"),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.patch(
        f"/api/v1/documents/{doc_id}",
        json={"doc_class": "Memorandos"},
        headers=_HEADERS,
    )

    assert resp.status_code == 200
    llamada = next(
        c for c in mock_conn.fetchrow.call_args_list if "UPDATE radicados" in str(c.args[0])
    )
    sql = llamada.args[0]
    # `metadata_template_id = $N`, no solo el nombre de la columna: aparece
    # también en el `RETURNING`, así que buscar el nombre a secas daba verde con
    # la escritura puesta Y quitada. Lo descubrió la mutación, no la lectura.
    asignacion = "metadata_template_id = $"
    assert asignacion in sql, "la versión de plantilla no se reescribe al retipificar"

    # Y el valor escrito es NULL: el tipo nuevo no tiene plantilla activa.
    posicion = int(sql.split(asignacion)[1].split()[0].rstrip(","))
    assert llamada.args[posicion] is None


@pytest.mark.asyncio
async def test_tipificar_deja_el_antes_y_el_despues_en_la_traza(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """«Se cambió el tipo» sin decir de cuál a cuál no permite reconstruir nada.

    Y es el campo más caro de reconstruir a posteriori: de él cuelga la serie, la
    retención y la disposición final.
    """
    client, mock_conn = client_with_conn
    doc_id = uuid.uuid4()
    mock_conn.fetchval.side_effect = _permiso_por_nivel(3)
    mock_conn.fetchrow.side_effect = [
        _clearance_row(),
        make_radicado_record(doc_id=doc_id, doc_class="Derechos de Petición"),
        None,
        make_radicado_record(doc_id=doc_id, doc_class="Memorandos"),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.patch(
        f"/api/v1/documents/{doc_id}",
        json={"doc_class": "Memorandos"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200

    # Se lee el `payload` del asiento ya parseado, no el `repr` de la llamada:
    # `audit.append` lo serializa con `json.dumps`, que escapa los no-ASCII, así
    # que buscar «Derechos de Petición» como texto crudo NO lo encuentra aunque
    # esté. Ese primer intento pasaba por un fallo del código y era un fallo del
    # test — buscar la cadena equivocada en el sitio correcto.
    cambios = None
    for llamada in mock_conn.execute.call_args_list + mock_conn.fetchval.call_args_list:
        if not llamada.args or "audit_log" not in str(llamada.args[0]):
            continue
        for arg in llamada.args[1:]:
            if not isinstance(arg, str):
                continue
            try:
                datos = json.loads(arg)
            except ValueError:
                continue
            if isinstance(datos, dict) and "cambios" in datos:
                cambios = datos["cambios"]

    assert cambios is not None, "la retipificación no dejó asiento en audit_log"
    assert cambios["doc_class"] == {
        "antes": "Derechos de Petición",
        "despues": "Memorandos",
    }
