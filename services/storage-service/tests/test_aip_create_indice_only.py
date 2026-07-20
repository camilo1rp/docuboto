# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests de la capa Pydantic ``AipCreate`` para el modo AIP índice-only (E10 AIP
wiring follow-up): ``documentos`` ahora admite lista vacía
(``min_length=0``), pero ``_check_at_least_one_payload`` sigue rechazando un
AIP TOTALMENTE vacío (sin documentos NI índice) con 422 -- nunca produce un
Bag empty-payload (defensa en profundidad de
``app.services.aip_builder.EmptyPayloadBagError``, ver
``test_aip_builder_indice_only.py``).

Ejercita el schema DIRECTAMENTE (``AipCreate(**payload)``), sin pasar por
HTTP/router -- aislado y rápido, mismo nivel que un test de
``model_validator`` puro. El nivel HTTP completo (con MinIO/BD mockeados)
vive en ``test_aip_real_indice_only.py``.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from app.schemas.preservacion import AipCreate


def _base_kwargs(**overrides) -> dict:
    kwargs = {
        "expediente_id": "EXP-1",
        "documentos": [],
        "retencion_anios": 5,
        "fecha_inicio": date(2026, 1, 1),
    }
    kwargs.update(overrides)
    return kwargs


def test_documentos_vacio_con_indice_file_id_es_aceptado() -> None:
    """El caso índice-only central: ``documentos=[]`` + ``indice_file_id``
    presente -- un expediente 100% físico sigue siendo empaquetable."""
    aip = AipCreate(**_base_kwargs(indice_file_id=uuid.uuid4()))
    assert aip.documentos == []
    assert aip.indice_file_id is not None


def test_documentos_vacio_sin_indice_file_id_es_rechazado() -> None:
    """AIP totalmente vacío (sin documentos NI índice) -- 422, nunca llega a
    intentar construir un Bag empty-payload."""
    with pytest.raises(ValidationError) as exc_info:
        AipCreate(**_base_kwargs(indice_file_id=None))
    assert "al menos uno" in str(exc_info.value).lower()


def test_documentos_no_vacio_sigue_aceptado_como_antes() -> None:
    """Regresión: el modo normal (con documentos, sin índice) NO se ve
    afectado por el follow-up índice-only."""
    aip = AipCreate(
        **_base_kwargs(
            documentos=[
                {"document_id": "R-1", "nombre": "oficio.pdf", "file_id": str(uuid.uuid4())}
            ]
        )
    )
    assert len(aip.documentos) == 1
    assert aip.indice_file_id is None


def test_documentos_no_vacio_con_indice_tambien_aceptado() -> None:
    """Modo normal CON índice (índice como tag file, no payload) -- también
    intacto."""
    aip = AipCreate(
        **_base_kwargs(
            documentos=[
                {"document_id": "R-1", "nombre": "oficio.pdf", "file_id": str(uuid.uuid4())}
            ],
            indice_file_id=uuid.uuid4(),
        )
    )
    assert len(aip.documentos) == 1
    assert aip.indice_file_id is not None


def test_pdfa_profile_coexiste_con_documentos_vacio() -> None:
    """``pdfa_profile`` (E10 debt "perfil por serie") es ortogonal al modo
    índice-only -- un AIP índice-only puede seguir declarando un perfil (sin
    efecto práctico porque no hay documentos PDF que validar, pero el schema
    no debe rechazarlo)."""
    aip = AipCreate(
        **_base_kwargs(indice_file_id=uuid.uuid4(), pdfa_profile="PDF/A-2b")
    )
    assert aip.pdfa_profile == "PDF/A-2b"
    assert aip.documentos == []


def test_unicidad_de_nombres_trivial_en_lista_vacia() -> None:
    """``_check_unique_nombres`` no debe fallar con ``documentos=[]`` --
    ``len([]) == len(set())`` es trivialmente cierto, sin caso especial
    necesario en el validador."""
    aip = AipCreate(**_base_kwargs(indice_file_id=uuid.uuid4()))
    assert aip.documentos == []


def test_documentos_ausente_por_completo_tambien_rechazado_sin_indice() -> None:
    """``documentos`` es obligatorio en el payload (``Field(..., min_length=0)``
    -- el ``...`` sigue exigiendo la clave, solo la LONGITUD mínima bajó a 0).
    Sin la clave en absoluto -- 422 por campo faltante, no por el
    model_validator del payload vacío."""
    kwargs = _base_kwargs()
    del kwargs["documentos"]
    with pytest.raises(ValidationError):
        AipCreate(**kwargs)
