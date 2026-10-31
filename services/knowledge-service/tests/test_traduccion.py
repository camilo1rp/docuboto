# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La fila oficial se vuelve un record PPI, y el anexo un localizador con huella."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest

from app.core.traduccion import lote, trozos

RADICADO = uuid.UUID("00000000-0000-4000-8000-0000000000b2")
ANEXO = uuid.UUID("00000000-0000-4000-8000-0000000000c3")
SHA = "ab" * 32
CUANDO = datetime(2026, 10, 31, 15, 1, tzinfo=UTC)


def _fila(**cambios: object) -> dict[str, object]:
    fila: dict[str, object] = {
        "id": RADICADO,
        "tracking_number": "2026-DEMO-E-000001",
        "doc_type": "E",
        "subject": "Solicitud de copia",
        "doc_class": "peticion",
        "dest_dept_code": 12,
        "status": "registered",
        "registered_at": datetime(2026, 10, 31, 15, 0, tzinfo=UTC),
        "due_date": date(2026, 11, 15),
        "sender_name": "Ana",
        "sender_entity": "Ciudadanía",
        "metadata": {
            "ciudad": "Bogotá",
            "activo": True,
            "nido": {"a": 1},
            "1mala": "no",
        },
        "updated_at": CUANDO,
    }
    fila.update(cambios)
    return fila


def _anexo(**cambios: object) -> dict[str, object]:
    anexo: dict[str, object] = {
        "id": ANEXO,
        "filename": "solicitud.pdf",
        "mime_type": "application/pdf",
        "file_size": 120,
        "es_principal": True,
        "folios": 2,
        "sha256": SHA,
    }
    anexo.update(cambios)
    return anexo


def test_el_radicado_entra_como_record_y_el_anexo_por_localizador() -> None:
    cuerpo = lote(_fila(), [_anexo(), _anexo(sha256="no-es-huella", id=uuid.uuid4())])
    record = cuerpo["items"][0]["record"]
    assert record["direction"] == "incoming"
    assert record["registration_number"] == "2026-DEMO-E-000001"
    assert record["received_at"] == "2026-10-31T15:00:00+00:00"
    assert record["version"] == CUANDO.isoformat()
    assert record["document_type"] == "peticion"
    assert record["unit"] == "12"
    assert record["due_date"] == "2026-11-15"
    assert record["closed"] is False
    assert record["parties"] == [
        {"role": "sender", "name": "Ana", "organization": "Ciudadanía"}
    ]
    assert record["metadata"] == {"activo": True, "ciudad": "Bogotá"}
    assert len(cuerpo["items"]) == 2
    pieza = cuerpo["items"][1]["attachment"]
    assert pieza["sha256"] == SHA
    assert pieza["content"] == {"mode": "locator", "locator": f"att:{ANEXO}:{SHA}"}
    assert pieza["parent_ref"] == str(RADICADO)
    assert pieza["role"] == "main"
    assert pieza["page_count"] == 2


@pytest.mark.parametrize(
    ("tipo", "direction"),
    [("S", "outgoing"), ("I", "internal")],
)
def test_el_tipo_documental_define_la_direccion(tipo: str, direction: str) -> None:
    record = lote(_fila(doc_type=tipo), [])["items"][0]["record"]
    assert record["direction"] == direction


def test_sin_asunto_no_hay_lote() -> None:
    with pytest.raises(ValueError):
        lote(_fila(subject="  "), [])


def test_un_lote_largo_se_parte_en_tandas_de_cien() -> None:
    items = [{"type": "record", "record": {"n": n}} for n in range(101)]
    partes = trozos({"items": items}, tope=100)
    assert [len(parte["items"]) for parte in partes] == [100, 1]
