# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El vínculo anexo↔radicado no lo puede afirmar quien radica.

La descarga mediada comprueba que el `file_id` sea anexo DE ESE radicado y que el
radicado esté dentro del clearance. Las dos eran ciertas cuando el propio
atacante acababa de escribir la primera: adosaba el `file_id` de un anexo
clasificado a un radicado propio de nivel PUBLICA y se llevaba los bytes, con la
descarga auditada como legítima.
"""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.services.document_service import AnexoNoDisponibleError, DocumentService
from tests.test_documents import _CREATE_PAYLOAD, TENANT_HEADERS  # noqa: F401


class _Anexo:
    def __init__(self, file_id):
        self.file_id = file_id


def _servicio(fetchval_result):
    conn = AsyncMock()
    conn.fetchval = AsyncMock(return_value=fetchval_result)
    svc = DocumentService.__new__(DocumentService)
    svc._conn = conn
    return svc, conn


@pytest.mark.asyncio
async def test_no_se_adjunta_un_fichero_de_un_radicado_fuera_de_alcance():
    """El caso del ataque: el fichero cuelga de algo que el llamante no puede leer."""
    svc, _ = _servicio(fetchval_result=1)  # la consulta encuentra uno fuera de alcance

    with pytest.raises(AnexoNoDisponibleError) as exc:
        await svc._rechazar_anexos_fuera_de_alcance([_Anexo(uuid.uuid4())], clearance=1)

    # El mensaje NO puede distinguir «no existe» de «no puedes verlo»: sería un
    # oráculo para averiguar qué ficheros cuelgan de un radicado reservado.
    assert "no está disponible" in str(exc.value)
    assert "clasificad" not in str(exc.value).lower()
    assert "nivel" not in str(exc.value).lower()


@pytest.mark.asyncio
async def test_el_fichero_deduplicado_que_si_puede_leer_sigue_adjuntandose():
    """La deduplicación por SHA-256 es legítima y no se puede romper.

    Dos radicados que llevan el mismo documento comparten fila de fichero. Una
    unicidad sobre `file_id` habría atajado el abuso rompiendo el caso normal:
    un gate que nadie puede satisfacer no protege, suprime.
    """
    svc, _ = _servicio(fetchval_result=None)  # nada fuera de alcance

    await svc._rechazar_anexos_fuera_de_alcance([_Anexo(uuid.uuid4())], clearance=1)


@pytest.mark.asyncio
async def test_sin_anexos_no_se_consulta_nada():
    """Radicar sin anexos no paga la consulta."""
    svc, conn = _servicio(fetchval_result=None)

    await svc._rechazar_anexos_fuera_de_alcance([], clearance=1)

    conn.fetchval.assert_not_awaited()


@pytest.mark.asyncio
async def test_la_consulta_compara_contra_el_clearance_del_llamante():
    """El gate es por NIVEL, no una lista de radicados: se pasa el clearance real."""
    svc, conn = _servicio(fetchval_result=None)
    fid = uuid.uuid4()

    await svc._rechazar_anexos_fuera_de_alcance([_Anexo(fid)], clearance=2)

    sql, ids, clearance = conn.fetchval.await_args.args
    assert "nivel_seguridad > $2" in sql
    assert ids == [fid]
    assert clearance == 2


@pytest.mark.asyncio
async def test_el_gate_ESTA_CABLEADO_y_no_gasta_consecutivo(client_with_conn):
    """Probar el método aislado no prueba que se invoque, ni cuándo.

    Esta es la aserción que importa: al rechazar, la secuencia NO se tocó. Un
    número de radicado gastado no se devuelve nunca, así que el gate tiene que
    ir antes de pedirlo — el mismo orden por el que un fallo de render tampoco
    consume el consecutivo.
    """
    client, mock_conn = client_with_conn

    # fetchrow, en orden: plantilla de metadatos (ninguna) y luego
    # resolve_caller_clearance → el llamante es PUBLICA (1).
    mock_conn.fetchrow.side_effect = [None, {"is_root": False, "max_level": 1}]
    # La consulta del gate encuentra el fichero colgando de algo fuera de alcance.
    mock_conn.fetchval.return_value = 1

    payload = {
        **_CREATE_PAYLOAD,
        "anexos": [{"file_id": str(uuid.uuid4()), "filename": "ajeno.pdf"}],
    }
    respuesta = await client.post("/api/v1/documents", json=payload, headers=TENANT_HEADERS)

    assert respuesta.status_code == 422
    assert respuesta.json()["detail"]["error"] == "anexo_no_disponible"

    # Lo que importa no es cuántas consultas hubo, sino QUÉ consulta no se hizo:
    # la secuencia se pide con `fetchval` sobre `tracking_sequences`, y el gate
    # dispara antes. Ninguna de las llamadas puede haberla tocado.
    consultas = [c.args[0] for c in mock_conn.fetchval.await_args_list if c.args]
    assert not any("tracking_sequences" in q for q in consultas), (
        f"el rechazo gastó un consecutivo, que no se devuelve nunca: {consultas}"
    )
    assert any("nivel_seguridad >" in q for q in consultas), (
        "el gate no llegó a consultar: la prueba mediría la pieza y no el cableado"
    )
