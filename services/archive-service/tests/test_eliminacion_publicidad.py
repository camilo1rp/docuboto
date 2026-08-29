# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La publicidad previa — RF-TRD-07, fase 2 de 5.

El inventario de lo que se va a eliminar se pone a la vista **de cualquiera, sin
autenticar**, para que quien tenga algo que oponer pueda hacerlo ANTES.

Lo que se prueba:

* **El chokepoint es el ESTADO del lote**, no un interruptor de despliegue. Un
  lote en `borrador` o `anulado` **no existe** para el público: 404, no 403.
* **La descripción de un expediente clasificado NO se publica**, pero la fila
  SÍ. Omitirla dejaría de cumplir la publicidad previa justo sobre lo que más
  importa que alguien pueda objetar; lo que se retiene es dónde vive el
  contenido («Historia laboral de …»). Y **el recorte va en el SELECT**, no en
  quien pinta.
* Publicar **congela el inventario** y es una transición cerrada contra
  carreras.
"""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from tests.conftest import TENANT_HEADERS

LOTE = uuid.uuid4()
PUBLICO = "/api/v1/public/archive/eliminacion/demo/lotes"
MANANA = (date.today() + timedelta(days=30)).isoformat()


def _lote_row(estado="publicado") -> dict:
    return {
        "id": LOTE,
        "codigo": "LOTE-2026-001",
        "estado": estado,
        "observaciones": None,
        "creado_por": uuid.uuid4(),
        "creado_at": datetime(2026, 8, 1, tzinfo=UTC),
        "publicado_at": datetime(2026, 8, 28, tzinfo=UTC),
        "publicado_hasta": date(2026, 9, 30),
        "acta_comite": None,
        "fecha_aval": None,
        "acto_administrativo": None,
        "ejecutado_at": None,
        "anulado_motivo": None,
    }


def _item_publico(*, code="EXP-2020-0001", nombre="Contratos 2020", reservado=False) -> dict:
    return {
        "expediente_code": code,
        # El SELECT ya devuelve NULL cuando el nivel supera PÚBLICA: aquí se
        # reproduce lo que la BD entrega, no lo que el servicio decidiría.
        "expediente_nombre": None if reservado else nombre,
        "nombre_reservado": reservado,
        "trd_serie_code": "DEM-100",
        "disposition_code": "E",
        "fecha_vencimiento": date(2026, 1, 1),
        "fecha_extrema_inicial": date(2020, 1, 1),
        "fecha_extrema_final": date(2020, 12, 31),
        "radicados_incluidos": 12,
    }


# ── Publicar ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_publicar_fija_el_plazo_y_deja_asiento(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _lote_row()

    resp = await client.post(
        f"/api/v1/eliminacion/lotes/{LOTE}/publicar",
        json={"publicado_hasta": MANANA},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    assert resp.json()["estado"] == "publicado"
    asientos = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == "archive.eliminacion_lote_publicado"
    ]
    assert asientos


@pytest.mark.asyncio
async def test_publicar_con_el_plazo_ya_vencido_no_da_ocasion_de_oponerse(
    client_with_conn,
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(
        f"/api/v1/eliminacion/lotes/{LOTE}/publicar",
        json={"publicado_hasta": "2020-01-01"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_un_lote_que_ya_no_esta_en_borrador_no_se_republica(client_with_conn) -> None:
    """La transición cierra su `WHERE ... AND estado = 'borrador'`: dos clics o
    dos réplicas no publican dos veces."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = None  # el UPDATE no tocó ninguna fila

    resp = await client.post(
        f"/api/v1/eliminacion/lotes/{LOTE}/publicar",
        json={"publicado_hasta": MANANA},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "lote_no_publicable"
    # Y la condición está EN EL SQL, no solo en el doble: con un mock, el
    # «0 filas» lo da el mock — mutar el `WHERE` dejaba este test en VERDE.
    # La prueba de verdad de la carrera vive en el carril de integración
    # (`tests/integration/test_eliminacion_lote_estados.py`), donde el conteo
    # de filas lo produce Postgres.
    sql = mock_conn.fetchrow.await_args.args[0]
    assert "estado = 'borrador'" in sql, sql


@pytest.mark.asyncio
async def test_publicar_exige_nivel_3(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    resp = await client.post(
        f"/api/v1/eliminacion/lotes/{LOTE}/publicar",
        json={"publicado_hasta": MANANA},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403


# ── La vista pública ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_el_inventario_publicado_se_ve_SIN_autenticar(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = _lote_row()
    mock_conn.fetch.return_value = [_item_publico()]

    # Ni cabecera de tenant, ni identidad, ni permiso: el tenant va en la ruta.
    resp = await client.get(f"{PUBLICO}/{LOTE}")

    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["codigo"] == "LOTE-2026-001"
    assert cuerpo["items"][0]["expediente_nombre"] == "Contratos 2020"


@pytest.mark.asyncio
async def test_la_descripcion_de_un_clasificado_NO_se_publica_pero_la_fila_SI(
    client_with_conn,
) -> None:
    """Omitir la fila dejaría de cumplir la publicidad previa justo sobre los
    expedientes que más importa que alguien pueda objetar. Lo que se retiene es
    la descripción, que es donde vive el contenido — y se DICE, con
    `nombre_reservado`, en vez de dejar un hueco mudo."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = _lote_row()
    mock_conn.fetch.return_value = [
        _item_publico(code="EXP-PUBLICO"),
        _item_publico(code="EXP-RESERVADO", reservado=True),
    ]

    cuerpo = (await client.get(f"{PUBLICO}/{LOTE}")).json()

    assert cuerpo["total_items"] == 2
    por_code = {i["expediente_code"]: i for i in cuerpo["items"]}
    assert por_code["EXP-RESERVADO"]["expediente_nombre"] is None
    assert por_code["EXP-RESERVADO"]["nombre_reservado"] is True
    # Y sigue trayendo lo que permite objetar: serie, fechas extremas, volumen.
    assert por_code["EXP-RESERVADO"]["trd_serie_code"] == "DEM-100"
    assert por_code["EXP-RESERVADO"]["radicados_incluidos"] == 12


@pytest.mark.asyncio
async def test_el_recorte_va_en_el_SELECT_no_en_quien_pinta(client_with_conn) -> None:
    """Se inspecciona el SQL EMITIDO: si el nombre saliera de la base y se
    filtrara después, cualquier consumidor futuro del repositorio lo
    reintroduciría sin enterarse."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = _lote_row()
    mock_conn.fetch.return_value = []

    await client.get(f"{PUBLICO}/{LOTE}")

    sql = mock_conn.fetch.await_args.args[0]
    assert "nivel_seguridad > 1" in sql
    assert "THEN NULL" in sql


@pytest.mark.asyncio
async def test_un_lote_en_borrador_no_existe_para_el_publico(client_with_conn) -> None:
    """404, no 403: la respuesta es indistinguible de la de un identificador
    inventado. Un 403 confirmaría que el lote existe."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None  # el WHERE por estado no lo devolvió

    resp = await client.get(f"{PUBLICO}/{LOTE}")

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "lote_no_encontrado"


@pytest.mark.asyncio
async def test_el_estado_es_el_chokepoint_y_esta_en_el_SQL(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None
    await client.get(f"{PUBLICO}/{LOTE}")

    sql = mock_conn.fetchrow.await_args.args[0]
    estados = mock_conn.fetchrow.await_args.args[2]
    assert "estado = ANY" in sql
    # Los ya avalados y ejecutados siguen siendo consultables: la constancia de
    # qué se eliminó no puede desaparecer al eliminarlo.
    assert set(estados) == {"publicado", "avalado", "ejecutado"}
    assert "borrador" not in estados
    assert "anulado" not in estados
