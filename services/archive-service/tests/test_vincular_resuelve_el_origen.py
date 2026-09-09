# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La dependencia productora y el formato se resuelven EN EL SERVIDOR al
vincular (E15 perfil v4 R1; Ac. AGN 001/2024 art. 4.3.2.3).

Gemelo de `test_vincular_resuelve_huella.py` y por el mismo motivo. El contrato
tenía `dependencia_productora_codigo`/`_nombre` desde el perfil v4 y NINGUNA
pantalla los mandaba: las dos columnas viajaban vacías al manifiesto de
exportación y el índice electrónico no emitía `<dependenciaProductora>` para
ningún documento. Y no bastaba con que la pantalla los mandara: quien vincula
suele ser un archivista central, no la oficina que produjo el documento — el
dato que aportaría sería el equivocado por construcción, en un artefacto que se
firma.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.archive_service import ArchiveService

_RAD = uuid.uuid4()
_META = {
    str(_RAD): {
        "subject": "Contrato de suministro",
        "doc_type": "E",
        "origin_dept": "Oficina de Contratacion",
        "origin_dept_code": 120,
        "formato": "application/pdf",
        "tamano_bytes": 2048,
    }
}


def _servicio() -> ArchiveService:
    return ArchiveService(AsyncMock(), "demo")


def _metadata(**kwargs):
    return patch(
        "app.clients.document_client.fetch_radicado_metadata_internal",
        AsyncMock(**kwargs),
    )


@pytest.mark.asyncio
async def test_el_origen_sale_del_servicio_dueno_del_radicado() -> None:
    with _metadata(return_value=_META):
        meta = await _servicio()._origen_por_radicado([_RAD])
    assert meta[str(_RAD)]["origin_dept"] == "Oficina de Contratacion"
    assert meta[str(_RAD)]["formato"] == "application/pdf"


@pytest.mark.asyncio
async def test_se_pide_SIN_recorte_de_clearance() -> None:
    """Mismo criterio que la huella: es un acto de sistema componiendo el
    índice. Recortarlo haría que dos archivistas produjeran índices distintos
    del mismo expediente."""
    espia = AsyncMock(return_value={})
    with patch("app.clients.document_client.fetch_radicado_metadata_internal", espia):
        await _servicio()._origen_por_radicado([_RAD])
    assert espia.await_args.kwargs["max_clearance"] == 3


@pytest.mark.asyncio
async def test_si_document_service_no_responde_el_VINCULO_no_se_pierde() -> None:
    """Best-effort: vincular no puede quedar preso de que otro servicio esté en
    pie, y tanto el índice como el manifiesto saben declarar el hueco."""
    from app.clients.document_client import DocumentClientError

    with _metadata(side_effect=DocumentClientError("caído")):
        assert await _servicio()._origen_por_radicado([_RAD]) == {}


@pytest.mark.asyncio
async def test_no_se_pregunta_por_nada_si_la_lista_esta_vacia() -> None:
    espia = AsyncMock(return_value={})
    with patch("app.clients.document_client.fetch_radicado_metadata_internal", espia):
        assert await _servicio()._origen_por_radicado([]) == {}
    espia.assert_not_awaited()


# ---------------------------------------------------------------------------
# `_productora`: la pareja código+nombre viaja entera o no viaja.
# ---------------------------------------------------------------------------


def test_la_pareja_viaja_entera() -> None:
    assert ArchiveService._productora(_META[str(_RAD)]) == (120, "Oficina de Contratacion")


def test_media_pareja_no_es_media_verdad() -> None:
    """El índice solo emite `<dependenciaProductora>` cuando tiene los dos, y la
    BD lo exige con un CHECK de pareja (migración 019). Media pareja es una fila
    que la base rechaza en el mejor caso y un elemento a medias en el peor."""
    assert ArchiveService._productora({"origin_dept_code": 120, "origin_dept": None}) == (
        None,
        None,
    )
    assert ArchiveService._productora({"origin_dept_code": None, "origin_dept": "Oficina"}) == (
        None,
        None,
    )


def test_sin_metadata_no_se_inventa_una_dependencia() -> None:
    """Control negativo, el mismo que exigió la tipología: el vacío honesto es
    una respuesta válida; un valor inventado en un índice que se firma es
    indistinguible de un dato real."""
    assert ArchiveService._productora(None) == (None, None)
    assert ArchiveService._productora({}) == (None, None)
