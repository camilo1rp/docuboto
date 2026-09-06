# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un sobre sin firma es una orden anónima.

Los consumidores toman la entidad destino del propio sobre y escriben en su
schema. Sin prueba de origen, quien pueda escribir en Redis actúa como el
productor legítimo sobre cualquier entidad alojada: publicar una reclasificación
a la baja bastaba para bajar de nivel un radicado reservado ajeno.
"""

import pytest

from orpycamcp_common.events import (
    EventoNoAutentico,
    build_event,
    parse_fields,
    to_fields,
)

_SECRETO = "secreto-de-pruebas"


@pytest.fixture
def con_secreto(monkeypatch):
    monkeypatch.setenv("EVENT_SIGNING_SECRET", _SECRETO)


def _sobre(**kw):
    return build_event(
        event_type=kw.get("event_type", "document.radicado.reclassified"),
        tenant_slug=kw.get("tenant_slug", "entidad-a"),
        payload=kw.get("payload", {"tracking_number": "2026-X-E-000001", "nivel_nuevo": 1}),
    )


def test_el_sobre_legitimo_va_y_vuelve(con_secreto):
    campos = to_fields(_sobre())
    assert campos.get("firma"), "el productor no firmó el sobre"
    assert parse_fields(campos)["tenant_slug"] == "entidad-a"


def test_un_sobre_fabricado_no_se_procesa(con_secreto):
    """El ataque: XADD directo, sin pasar por ningún productor."""
    campos = to_fields(_sobre())
    del campos["firma"]

    with pytest.raises(EventoNoAutentico):
        parse_fields(campos)


def test_cambiar_la_entidad_destino_invalida_la_firma(con_secreto):
    """`tenant_slug` está DENTRO de lo firmado: reapuntar el efecto rompe el sobre."""
    campos = to_fields(_sobre(tenant_slug="entidad-a"))
    campos["tenant_slug"] = "entidad-victima"

    with pytest.raises(EventoNoAutentico):
        parse_fields(campos)


def test_cambiar_el_contenido_invalida_la_firma(con_secreto):
    """El nivel de seguridad viaja en el payload, y el payload está firmado."""
    campos = to_fields(_sobre())
    # El sobre original lleva nivel_nuevo=1; aquí se reescribe a 3 para que el
    # cambio sea REAL. Sustituirlo por el mismo texto no probaría nada.
    campos["payload"] = '{"tracking_number": "2026-X-E-000001", "nivel_nuevo": 3}'

    with pytest.raises(EventoNoAutentico):
        parse_fields(campos)


def test_una_firma_de_otro_secreto_no_vale(con_secreto, monkeypatch):
    campos = to_fields(_sobre())
    monkeypatch.setenv("EVENT_SIGNING_SECRET", "otro-secreto")

    with pytest.raises(EventoNoAutentico):
        parse_fields(campos)


def test_sin_secreto_configurado_no_se_rompe_lo_que_ya_estaba_encolado(monkeypatch):
    """Decisión explícita, no olvido.

    Exigir firma de golpe en un despliegue en marcha dejaría sin procesar todo
    lo ya encolado, incluidos avisos a ciudadanos con plazo legal corriendo. El
    interruptor es la propia variable: quien la pone, la exige.
    """
    monkeypatch.delenv("EVENT_SIGNING_SECRET", raising=False)
    campos = to_fields(_sobre())
    assert "firma" not in campos
    assert parse_fields(campos)["tenant_slug"] == "entidad-a"
