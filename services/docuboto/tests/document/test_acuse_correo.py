# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Constancia de radicación al remitente (E19 Increment 3).

Lo que se prueba son los dos casos en que **no** hay que enviar —y que no son
errores sino decisiones—, más lo que la constancia dice y lo que calla.
"""

from unittest.mock import AsyncMock

import pytest

from document.services import acuse_correo
from notification import api as notification_api

CUENTA = "pqrs@e.gov.co"


def test_no_se_acusa_al_propio_buzon() -> None:
    """El bucle que esto corta, y que no es teórico.

    Si el remitente es la propia cuenta —alguien reenvía desde el buzón, o un
    reenvío automático mal configurado— la constancia volvería a la carpeta de
    trabajo, se radicaría y generaría otra constancia. Cada vuelta consume un
    número de la secuencia oficial, que es inmutable y no se libera.
    """
    v = acuse_correo.destinatario_valido(CUENTA, direccion_cuenta=CUENTA)
    assert v.enviado is False
    assert "bucle" in (v.motivo or "")

    # Y con otra caja o espacios alrededor: un bucle no se evita por accidente de
    # escritura.
    for variante in ("  PQRS@E.GOV.CO  ", "Pqrs@e.gov.co"):
        assert acuse_correo.destinatario_valido(variante, direccion_cuenta=CUENTA).enviado is False


def test_sin_remitente_no_se_acusa_y_se_dice_por_que() -> None:
    """«No se envió» y «no se pudo enviar» son cosas distintas.

    Sin el motivo, un ciudadano que nunca recibió su número sería indistinguible
    de uno al que no había que acusarle nada.
    """
    for vacio in (None, "", "   "):
        v = acuse_correo.destinatario_valido(vacio, direccion_cuenta=CUENTA)
        assert v.enviado is False and v.motivo


def test_una_direccion_malformada_no_llega_al_servicio_de_correo() -> None:
    for mala in ("no-es-una-direccion", "a@b", "@e.gov.co", "juan@", "a b@c.co"):
        v = acuse_correo.destinatario_valido(mala, direccion_cuenta=CUENTA)
        assert v.enviado is False, f"aceptó {mala!r}"


def test_un_remitente_normal_si_recibe_constancia() -> None:
    """Control positivo: sin esto, un filtro roto rechazaría todo y el test de
    arriba seguiría en verde."""
    assert acuse_correo.destinatario_valido(
        "maria@ciudadana.co", direccion_cuenta=CUENTA
    ).enviado is True


def test_la_constancia_dice_lo_justo() -> None:
    cuerpo = acuse_correo._cuerpo("2026-DEMO-E-000045", "Derecho de petición", "20/08/2026 09:15")
    assert "2026-DEMO-E-000045" in cuerpo
    assert "20/08/2026 09:15" in cuerpo
    assert "no responda" in cuerpo.lower()

    # NO promete plazos: los fija la ley según el tipo de trámite, y esta
    # constancia no sabe cuál es. Prometerlo comprometería a la entidad.
    for promesa in ("días hábiles", "15 días", "responderemos", "plazo de"):
        assert promesa not in cuerpo.lower(), f"promete un plazo: {promesa!r}"


@pytest.mark.asyncio
async def test_un_fallo_del_servicio_de_correo_no_lanza(monkeypatch) -> None:
    """El radicado ya existe y su número es inmutable: deshacerlo por un fallo de
    correo sería mucho peor que un ciudadano sin acuse."""
    monkeypatch.setattr(
        notification_api,
        "send",
        AsyncMock(side_effect=notification_api.NotificationDeliveryError("smtp caído")),
    )
    r = await acuse_correo.enviar(
        tenant_slug="demo", destinatario="a@b.co", tracking_number="2026-DEMO-E-1",
        asunto="x", recibido="20/08/2026 09:15",
    )
    assert r.enviado is False and r.motivo


@pytest.mark.asyncio
async def test_el_error_del_servicio_no_refleja_la_direccion(monkeypatch) -> None:
    """El motivo acaba en una columna que se lee desde la interfaz: propagar el
    detalle del error podría llevar allí la dirección del destinatario."""
    monkeypatch.setattr(
        notification_api,
        "send",
        AsyncMock(
            side_effect=notification_api.NotificationDeliveryError(
                "destinatario maria@ciudadana.co inválido"
            )
        ),
    )
    r = await acuse_correo.enviar(
        tenant_slug="demo", destinatario="maria@ciudadana.co",
        tracking_number="2026-DEMO-E-1", asunto="x", recibido="20/08/2026 09:15",
    )
    assert r.enviado is False
    assert "maria@ciudadana.co" not in (r.motivo or "")


@pytest.mark.asyncio
async def test_va_por_notification_en_proceso_y_no_es_sensible(monkeypatch) -> None:
    """Sin ruta HTTP que mande correo: la constancia sale por `notification.api`."""
    send = AsyncMock(return_value="n1")
    monkeypatch.setattr(notification_api, "send", send)
    r = await acuse_correo.enviar(
        tenant_slug="demo", destinatario="a@b.co", tracking_number="2026-DEMO-E-9",
        asunto="Petición", recibido="20/08/2026 09:15",
    )
    assert r.enviado is True
    kw = send.await_args.kwargs
    assert kw["tenant_slug"] == "demo" and kw["recipient_email"] == "a@b.co"
    assert "2026-DEMO-E-9" in kw["subject"]
    assert kw.get("sensible", False) is False
