# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El aval del Comité y la anulación — RF-TRD-07, fase 3 de 5.

El aval es la **decisión archivística** que autoriza destruir (Ac. AGN 001/2024
art. 4.3.2.5). El sistema no decide: registra que un Comité decidió, con qué
acta y con qué acto administrativo.

Lo que se prueba, y por qué cada cosa:

* **No se avala con el plazo de publicidad abierto.** Es la regla de fondo de
  esta fase: autorizar la destrucción antes de que venza el tiempo que la
  entidad dio para oponerse convierte la publicidad previa en un adorno. La
  guarda está en el `WHERE` de la transición —se afirma sobre el SQL emitido—
  porque con un mock el «0 filas» lo da el mock, no la condición.
* **El 409 dice CUÁL de los tres motivos fue.** «No se pudo avalar» manda a
  buscar un permiso cuando lo que pasa es que faltan cuatro días de plazo — el
  mismo defecto que ya se pagó aquí con un 403 que acusaba de faltar un permiso
  que el usuario tenía.
* **Un lote ejecutado no se anula.** Lo destruido no se des-destruye, y marcar
  «anulado» un lote ejecutado afirmaría lo contrario de lo que pasó.
"""

import uuid
from datetime import UTC, date, datetime

import pytest

from tests.conftest import TENANT_HEADERS, USER_ID

LOTE = uuid.uuid4()
BASE = f"/api/v1/eliminacion/lotes/{LOTE}"
AVAL = {
    "acta_comite": "Acta 07 del Comité Institucional de Gestión y Desempeño",
    "fecha_aval": "2026-08-20",
    "acto_administrativo": "Resolución 512 de 2026",
}


def _lote_row(estado="avalado") -> dict:
    return {
        "id": LOTE,
        "codigo": "LOTE-2026-001",
        "estado": estado,
        "observaciones": None,
        "creado_por": uuid.uuid4(),
        "creado_at": datetime(2026, 8, 1, tzinfo=UTC),
        "publicado_at": datetime(2026, 8, 2, tzinfo=UTC),
        "publicado_hasta": date(2026, 8, 15),
        "acta_comite": AVAL["acta_comite"],
        "fecha_aval": date(2026, 8, 20),
        "acto_administrativo": AVAL["acto_administrativo"],
        "ejecutado_at": None,
        "anulado_motivo": None,
    }


def _diagnostico(
    estado: str, *, abierta: bool, hasta=date(2026, 9, 30), creado_por=None
) -> dict:
    """Lo que devuelve la lectura de DIAGNÓSTICO tras una transición fallida.
    No es una guarda: solo compone la frase."""
    return {
        "estado": estado,
        "publicado_hasta": hasta,
        "publicidad_abierta": abierta,
        "creado_por": creado_por or uuid.uuid4(),
        "avalado_por": None,
    }


# ── El aval ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_avalar_registra_las_tres_constancias_y_deja_asiento(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _lote_row()

    resp = await client.post(f"{BASE}/avalar", json=AVAL, headers=TENANT_HEADERS)

    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["estado"] == "avalado"
    assert cuerpo["acta_comite"] == AVAL["acta_comite"]
    assert cuerpo["acto_administrativo"] == AVAL["acto_administrativo"]
    asientos = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == "archive.eliminacion_lote_avalado"
    ]
    assert asientos


@pytest.mark.asyncio
async def test_el_plazo_de_publicidad_se_exige_EN_EL_SQL_de_la_transicion(
    client_with_conn,
) -> None:
    """Comprobarlo antes en Python y actualizar después deja una ventana en la
    que el plazo se lee abierto y se escribe cerrado. Y `CURRENT_DATE`, no una
    fecha del cliente: el plazo lo mide el servidor."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _lote_row()

    await client.post(f"{BASE}/avalar", json=AVAL, headers=TENANT_HEADERS)

    sql = mock_conn.fetchrow.await_args_list[0].args[0]
    assert "publicado_hasta < CURRENT_DATE" in sql, sql
    assert "estado = 'publicado'" in sql, sql


@pytest.mark.asyncio
async def test_no_se_avala_con_el_plazo_todavia_abierto_y_el_409_DICE_hasta_cuando(
    client_with_conn,
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    # 1.º el UPDATE no tocó ninguna fila; 2.º la lectura de diagnóstico.
    mock_conn.fetchrow.side_effect = [
        None,
        _diagnostico("publicado", abierta=True, hasta=date(2026, 9, 30)),
    ]

    resp = await client.post(f"{BASE}/avalar", json=AVAL, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    detalle = resp.json()["detail"]
    assert detalle["error"] == "publicidad_en_curso"
    # La fecha va en el mensaje: sin ella, «vuelva más tarde» no dice cuándo.
    assert "2026-09-30" in detalle["detail"]


@pytest.mark.asyncio
async def test_quien_propuso_el_lote_no_lo_avala_y_la_guarda_va_EN_EL_SQL(
    client_with_conn,
) -> None:
    """AREA-06-01: separación de funciones. La guarda es `creado_por <> $5` en
    el `WHERE` de la transición —no una comprobación previa en Python, que deja
    una ventana—, y el 409 lo explica con su propio código: el remedio es OTRA
    persona, no esperar. La prueba contra Postgres real (que el UPDATE no toca
    fila) vive en `integration/test_eliminacion_lote_estados.py`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [
        None,
        _diagnostico("publicado", abierta=False, creado_por=USER_ID),
    ]

    resp = await client.post(f"{BASE}/avalar", json=AVAL, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "separacion_de_funciones"
    sql = mock_conn.fetchrow.await_args_list[0].args[0]
    assert "creado_por <> $5" in sql, sql
    # Y el intento deja constancia.
    asientos = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and "archive.eliminacion_separacion_denegada" in c.args
    ]
    assert asientos


@pytest.mark.asyncio
async def test_avalar_exige_nivel_4_y_no_basta_el_3(client_with_conn) -> None:
    """El aval habilita la destrucción: pide el nivel que el catálogo reserva a
    los actos irreversibles. El doble RBAC responde según el `min_crud` que se
    le pasa de verdad, así que bajar el gate a 3 hace fallar este test."""
    client, mock_conn = client_with_conn

    async def _rbac(sql, *args):
        if "gp.crud >= $3" in sql:
            return args[2] <= 3
        return 1

    mock_conn.fetchval.side_effect = _rbac

    resp = await client.post(f"{BASE}/avalar", json=AVAL, headers=TENANT_HEADERS)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_un_lote_en_borrador_no_se_avala_saltandose_la_publicidad(
    client_with_conn,
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [None, _diagnostico("borrador", abierta=False, hasta=None)]

    resp = await client.post(f"{BASE}/avalar", json=AVAL, headers=TENANT_HEADERS)

    assert resp.status_code == 409
    detalle = resp.json()["detail"]
    assert detalle["error"] == "lote_no_avalable"
    # Y distingue el motivo: no es «el plazo sigue abierto».
    assert "borrador" in detalle["detail"]
    assert "publicado" in detalle["detail"]


@pytest.mark.asyncio
async def test_un_lote_que_no_existe_da_404_y_no_409(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [None, None]

    resp = await client.post(f"{BASE}/avalar", json=AVAL, headers=TENANT_HEADERS)

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "lote_no_encontrado"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "campo", ["acta_comite", "fecha_aval", "acto_administrativo"]
)
async def test_el_aval_no_se_afirma_sin_sus_tres_constancias(client_with_conn, campo) -> None:
    """Una aprobación sin acta, sin fecha o sin acto administrativo es una
    afirmación no verificada — el mismo criterio que la convalidación de una
    versión de TRD. Se prueba campo a campo: un test que quitara los tres a la
    vez no diría cuál se dejó de exigir."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    cuerpo = {k: v for k, v in AVAL.items() if k != campo}

    resp = await client.post(f"{BASE}/avalar", json=cuerpo, headers=TENANT_HEADERS)

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_la_fecha_del_aval_no_puede_ser_futura(client_with_conn) -> None:
    """Se registra un acta que ya existe, no una reunión que aún no ha
    ocurrido."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(
        f"{BASE}/avalar",
        json={**AVAL, "fecha_aval": "2099-01-01"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_avalar_exige_nivel_3(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    resp = await client.post(f"{BASE}/avalar", json=AVAL, headers=TENANT_HEADERS)
    assert resp.status_code == 403


# ── La anulación ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_anular_deja_el_motivo_y_su_asiento(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    fila = _lote_row(estado="anulado")
    fila["anulado_motivo"] = "Oposición de un tercero admitida por el Comité"
    mock_conn.fetchrow.return_value = fila

    resp = await client.post(
        f"{BASE}/anular",
        json={"motivo": "Oposición de un tercero admitida por el Comité"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200
    assert resp.json()["anulado_motivo"] == "Oposición de un tercero admitida por el Comité"
    asientos = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == "archive.eliminacion_lote_anulado"
    ]
    assert asientos


@pytest.mark.asyncio
async def test_se_anula_hasta_ya_avalado_pero_por_ADMISION_de_los_tres_vivos(
    client_with_conn,
) -> None:
    """Escrito por admisión y no negando `ejecutado`: un estado nuevo entra en
    el CHECK antes de que nadie se acuerde de esta línea, y con la lista
    negativa entraría además, callando."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _lote_row(estado="anulado")

    await client.post(
        f"{BASE}/anular", json={"motivo": "Se retira por error material"}, headers=TENANT_HEADERS
    )

    sql = mock_conn.fetchrow.await_args_list[0].args[0]
    assert "estado IN ('borrador', 'publicado', 'avalado')" in sql, sql
    assert "ejecutado" not in sql, sql


@pytest.mark.asyncio
async def test_un_lote_EJECUTADO_no_se_anula(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [None, _diagnostico("ejecutado", abierta=False)]

    resp = await client.post(
        f"{BASE}/anular", json={"motivo": "Se retira por error material"}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "lote_no_anulable"
    assert "ejecutado" in resp.json()["detail"]["detail"]


@pytest.mark.asyncio
async def test_anular_sin_motivo_util_no_es_una_constancia(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(f"{BASE}/anular", json={"motivo": "no"}, headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_anular_exige_nivel_3(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    resp = await client.post(
        f"{BASE}/anular", json={"motivo": "Se retira por error material"}, headers=TENANT_HEADERS
    )
    assert resp.status_code == 403
