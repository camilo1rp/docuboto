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

from app.schemas.archive import RadicadoLink
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


_META_COMPLETA = {
    str(_RAD): {**_META[str(_RAD)], "tracking_number": "2026-X-E-000001", "doc_class": "COR-PET"}
}


def _enlace(**kwargs) -> RadicadoLink:
    return RadicadoLink(radicado_id=_RAD, tracking_number="2026-X-E-000001", **kwargs)


async def _resolver(clearance: int = 3, **kwargs):
    with patch("app.services.archive_service.audit.append", AsyncMock()):
        return await _servicio()._resolver_metadatos_vinculo(
            [_enlace(**kwargs)], expediente_code="EXP-1", actor=None, clearance=clearance
        )


@pytest.mark.asyncio
async def test_el_origen_sale_del_servicio_dueno_del_radicado() -> None:
    with _metadata(return_value=_META_COMPLETA), patch(
        "app.services.archive_service.ArchiveService._huellas_por_radicado",
        AsyncMock(return_value={}),
    ):
        r = (await _resolver())[str(_RAD)]
    assert (r["dep_codigo"], r["dep_nombre"]) == (120, "Oficina de Contratacion")
    assert r["formato"] == "application/pdf"
    assert r["tamano_bytes"] == 2048


@pytest.mark.asyncio
async def test_se_pide_con_el_clearance_del_LLAMANTE() -> None:
    """Hallazgo medio de T6: sin recorte, el 422 `desacuerdos` enumeraba la
    tipologia/MIME/productora de un radicado reservado a quien no podia leerlo.
    Mutacion: volver a `max_clearance=CLEARANCE_SIN_RESTRICCION` lo tumba."""
    espia = AsyncMock(return_value=_META_COMPLETA)
    with patch("app.clients.document_client.fetch_radicado_metadata_internal", espia), patch(
        "app.services.archive_service.ArchiveService._huellas_por_radicado",
        AsyncMock(return_value={}),
    ):
        await _resolver(clearance=1)
    assert espia.await_args.kwargs["max_clearance"] == 1


@pytest.mark.asyncio
async def test_si_document_service_no_responde_el_vinculo_FALLA_CERRADO() -> None:
    """Invierte lo que decía este test hasta AREA-07-02: vincular era
    best-effort y sellaba en el índice el número de radicado que escribiera
    quien vinculaba. Sin poder cotejarlo con quien lo guarda, no se vincula:
    503, no un vínculo con datos sin contrastar."""
    from fastapi import HTTPException

    from app.clients.document_client import DocumentClientError

    with _metadata(side_effect=DocumentClientError("caído")), pytest.raises(
        HTTPException
    ) as exc:
        await _resolver()
    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "document_service_no_disponible"


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
