# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
El aviso de radicación a la dependencia competente.

`build_document_notifications` **no tenía ningún test propio** —solo se
mencionaba en el docstring de otro— y por eso su defecto sobrevivió: leía
únicamente `dest_dept_code`, así que una radicación hecha por NOMBRE de
dependencia producía dos fallos a la vez, y ninguno se veía desde el código:

  · el aviso decía «Dependencia destino: sin asignar» **teniendo el nombre en el
    mismo payload** — el patrón de esta revisión: el dato bueno está y el mensaje
    muestra otro;
  · y se enviaba a la ventanilla genérica en vez de a la dependencia, así que la
    oficina competente no se enteraba de su documento.

Es el mismo defecto que ya se había corregido en el stream de flujos. El de
documentos se quedó fuera, y apareció al radicar un correo desde la bandeja —que
compone la dependencia por nombre, no por código—.
"""

import pytest

from app.services.event_consumer import build_document_notifications

BUZON = "dependencia-100@orpycamcp.local"


def _sobre(**payload):
    base = {"tracking_number": "2026-DEMO-E-000025"}
    base.update(payload)
    return {
        "event_type": "document.radicado.created",
        "tenant_slug": "demo",
        "payload": base,
    }


def test_el_aviso_nombra_la_dependencia_cuando_viene_por_NOMBRE() -> None:
    """El caso que estaba roto."""
    envios = build_document_notifications(
        _sobre(dest_dept="Oficina Jurídica", dest_dept_code=None), buzon_destino=BUZON
    )
    assert len(envios) == 1
    assert "Oficina Jurídica" in envios[0].body
    assert "sin asignar" not in envios[0].body
    assert envios[0].recipient_email == BUZON


def test_el_nombre_gana_al_codigo_porque_es_lo_que_un_humano_reconoce() -> None:
    envios = build_document_notifications(
        _sobre(dest_dept="Oficina Jurídica", dest_dept_code=100), buzon_destino=BUZON
    )
    assert "Oficina Jurídica" in envios[0].body
    assert "100" not in envios[0].body


def test_con_solo_el_codigo_se_muestra_el_codigo() -> None:
    """Retrocompatible: un cliente que solo maneja códigos sigue funcionando."""
    envios = build_document_notifications(
        _sobre(dest_dept=None, dest_dept_code=100), buzon_destino=BUZON
    )
    assert "100" in envios[0].body


def test_sin_dependencia_alguna_se_dice_sin_asignar() -> None:
    """Y solo entonces: «sin asignar» debe significar que de verdad no hay."""
    envios = build_document_notifications(
        _sobre(dest_dept=None, dest_dept_code=None), buzon_destino=BUZON
    )
    assert "sin asignar" in envios[0].body


def test_el_buzon_destino_es_obligatorio() -> None:
    """Sin valor por defecto, igual que en el constructor de flujos.

    Un default convertiría «no supe a quién avisar» en «avisé a la ventanilla»
    sin que nadie se entere; sin él, un llamante que lo olvide **falla al
    escribirse**, no en producción.
    """
    with pytest.raises(TypeError):
        build_document_notifications(_sobre(dest_dept="X"))  # type: ignore[call-arg]


def test_otros_eventos_no_producen_correo() -> None:
    sobre = _sobre(dest_dept="X")
    sobre["event_type"] = "document.radicado.annulled"
    assert build_document_notifications(sobre, buzon_destino=BUZON) == []
