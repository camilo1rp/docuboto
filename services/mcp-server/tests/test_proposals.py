# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Propuestas firmadas: qué se garantiza y qué no.

Cada test fija una de las cuatro defensas —integridad, vigencia, titularidad y
consumo— por separado, para que una mutación caiga en el test de la defensa que
rompe y no en un test genérico que no diga cuál.

El último test es tan importante como los otros: declara el límite real del
consumo, para que nadie lea este módulo como una garantía de unicidad global que
no da.
"""

from __future__ import annotations

import pytest

from app.services.proposals import (
    Proposal,
    ProposalError,
    consume,
    huella,
    issue,
    limpiar_consumidas,
    verify,
)

_SECRETO = "clave-de-pruebas"
_SUB = "11111111-1111-1111-1111-111111111111"
_TEN = "demo"
_ARGS = {"doc_type": "E", "subject": "Tutela"}


@pytest.fixture(autouse=True)
def _limpiar() -> None:
    limpiar_consumidas()


def _emitir(**kw) -> tuple[str, Proposal]:
    base = {
        "sub": _SUB,
        "ten": _TEN,
        "secreto": _SECRETO,
        "ttl_seconds": 120,
        "ahora": 1_000_000,
    }
    return issue("radicar_documento", _ARGS, **{**base, **kw})


def test_el_camino_valido_devuelve_el_acto_intacto() -> None:
    token, emitida = _emitir()
    leida = verify(token, secretos=(_SECRETO,), sub=_SUB, ten=_TEN, ahora=1_000_010)

    assert leida.tool == "radicar_documento"
    assert leida.args == _ARGS
    assert leida.jti == emitida.jti


def test_una_firma_alterada_se_rechaza() -> None:
    """Integridad: si se toca el payload, la firma deja de casar.

    Es lo que hace que «lo que se firmó» sea «lo que se ejecuta»: cambiar el
    asunto o el destinatario después de que la persona lo viera invalida el
    token en vez de ejecutarse en silencio.
    """
    token, _ = _emitir()
    prefijo, payload, firma = token.split(".")

    # Payload manipulado, firma original.
    otro, _ = issue(
        "radicar_documento",
        {"doc_type": "E", "subject": "OTRA COSA"},
        sub=_SUB,
        ten=_TEN,
        secreto=_SECRETO,
        ttl_seconds=120,
        ahora=1_000_000,
    )
    falsificado = f"{prefijo}.{otro.split('.')[1]}.{firma}"

    with pytest.raises(ProposalError) as exc:
        verify(falsificado, secretos=(_SECRETO,), sub=_SUB, ten=_TEN, ahora=1_000_010)
    assert exc.value.code == "propuesta_invalida"


def test_una_firma_de_otra_clave_se_rechaza() -> None:
    token, _ = _emitir(secreto="otra-clave")
    with pytest.raises(ProposalError) as exc:
        verify(token, secretos=(_SECRETO,), sub=_SUB, ten=_TEN, ahora=1_000_010)
    assert exc.value.code == "propuesta_invalida"


def test_la_clave_anterior_sigue_valiendo_durante_la_rotacion() -> None:
    """Rotar la clave no puede invalidar las propuestas ya emitidas.

    Si lo hiciera, cada rotación rompería en la cara de quien tuviera una tarjeta
    abierta — y el equipo aprendería a no rotar.
    """
    token, _ = _emitir(secreto="clave-anterior")
    leida = verify(
        token, secretos=("clave-nueva", "clave-anterior"), sub=_SUB, ten=_TEN, ahora=1_000_010
    )
    assert leida.tool == "radicar_documento"


def test_una_propuesta_caducada_se_rechaza() -> None:
    token, _ = _emitir(ttl_seconds=120)
    with pytest.raises(ProposalError) as exc:
        verify(token, secretos=(_SECRETO,), sub=_SUB, ten=_TEN, ahora=1_000_121)
    assert exc.value.code == "propuesta_expirada"
    assert exc.value.status == 410


def test_una_propuesta_de_otro_usuario_se_rechaza() -> None:
    """Titularidad: un token filtrado no sirve a otra persona."""
    token, _ = _emitir()
    with pytest.raises(ProposalError) as exc:
        verify(
            token,
            secretos=(_SECRETO,),
            sub="22222222-2222-2222-2222-222222222222",
            ten=_TEN,
            ahora=1_000_010,
        )
    assert exc.value.code == "propuesta_ajena"


def test_una_propuesta_de_otra_entidad_se_rechaza() -> None:
    """Ni a otra entidad: el aislamiento multi-tenant también aplica aquí."""
    token, _ = _emitir()
    with pytest.raises(ProposalError) as exc:
        verify(token, secretos=(_SECRETO,), sub=_SUB, ten="otra", ahora=1_000_010)
    assert exc.value.code == "propuesta_ajena"


def test_una_propuesta_no_se_ejecuta_dos_veces() -> None:
    """El doble clic y el reintento, que es el caso real."""
    _, propuesta = _emitir()
    consume(propuesta, ahora=1_000_010)

    with pytest.raises(ProposalError) as exc:
        consume(propuesta, ahora=1_000_011)
    assert exc.value.code == "propuesta_consumida"
    assert exc.value.status == 409


def test_el_registro_de_consumo_no_crece_sin_fin() -> None:
    """Las caducadas se barren: si no, el diccionario crece hasta el reinicio."""
    _, vieja = _emitir(ttl_seconds=10)
    consume(vieja, ahora=1_000_001)
    # Pasado su vencimiento, otra propuesta cualquiera dispara el barrido y la
    # vieja deja de ocupar sitio; se comprueba por su efecto observable: el mismo
    # jti vuelve a ser aceptable (ya no puede hacer daño: está caducado).
    consume(vieja, ahora=1_000_100)


def test_formato_invalido_no_revienta() -> None:
    for basura in ("", "sin-puntos", "op9.a.b", "op1.no-base64!.x"):
        with pytest.raises(ProposalError) as exc:
            verify(basura, secretos=(_SECRETO,), sub=_SUB, ten=_TEN, ahora=1_000_010)
        assert exc.value.code == "propuesta_invalida"


def test_sin_secreto_no_se_emite() -> None:
    """Una firma con clave vacía la reproduce cualquiera: el gate sería decorativo."""
    with pytest.raises(ProposalError) as exc:
        issue("t", {}, sub=_SUB, ten=_TEN, secreto="", ttl_seconds=120)
    assert exc.value.code == "propuesta_sin_secreto"


def test_la_huella_detecta_que_el_mundo_cambio() -> None:
    """Sirve para no mostrar una tarjeta mentirosa, no para serializar escrituras."""
    antes = huella({"status": "open", "assigned_to": "u1"})
    igual = huella({"assigned_to": "u1", "status": "open"})  # orden irrelevante
    despues = huella({"status": "open", "assigned_to": "u2"})

    assert antes == igual
    assert antes != despues


def test_el_consumo_es_por_proceso_y_asi_se_declara() -> None:
    """LÍMITE DECLARADO, no defecto oculto.

    El registro de consumo vive en memoria del proceso: con varias réplicas, un
    token robado dentro de su ventana puede ejecutarse dos veces. La defensa
    definitiva es la idempotencia aguas abajo, que hoy no implementa ningún
    servicio; se emite `Idempotency-Key` como gancho, no como garantía.

    Este test existe para que ese límite esté escrito en el código y no solo en
    un ADR que nadie relee: si alguien migra el registro a un almacén compartido,
    tendrá que venir aquí a borrarlo, y en ese momento se dará cuenta de que la
    garantía cambió.
    """
    import app.services.proposals as mod

    assert isinstance(mod._CONSUMIDAS, dict), (
        "el consumo sigue siendo un diccionario en proceso: si esto cambia, "
        "revisa la garantía que promete el docstring del módulo"
    )


# ---------------------------------------------------------------------------
# El arranque
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sin_clave_de_firma_el_servicio_no_arranca(monkeypatch) -> None:
    """Las dos mitades: default vacío Y comprobación al arrancar.

    Con solo la primera, el servicio levantaría con el gate apagado y nadie lo
    notaría hasta que alguien buscara por qué una escritura pasó sin confirmar.
    Es el mismo criterio de los secretos del bloque 11.

    El lifespan no corre en el resto de la suite, así que sin este test la
    comprobación estaría escrita y sin verificar.
    """
    from orpycamcp_common.secrets import SecretoNoConfigurado

    from app.core.config import settings
    from app.main import app, lifespan

    monkeypatch.setattr(settings, "mcp_proposal_secret", "")
    with pytest.raises(SecretoNoConfigurado) as exc:
        async with lifespan(app):
            pass
    assert "MCP_PROPOSAL_SECRET" in str(exc.value)


def test_con_clave_de_firma_el_guard_no_bloquea(monkeypatch) -> None:
    """Control positivo del guard, no del lifespan completo.

    No se arranca el lifespan entero a propósito: el gestor de sesiones MCP no
    admite dos arranques en el mismo proceso, y el test caía por contaminación
    con el negativo —verde en aislamiento, rojo en conjunto—, que es la peor
    clase de test. Lo que aquí se comprueba es lo que este bloque añadió: que con
    la clave puesta el guard deja pasar. El arranque completo ya lo cubre
    `test_streamable_http_initialize`.
    """
    from orpycamcp_common.secrets import require_secrets

    from app.core.config import settings

    monkeypatch.setattr(settings, "mcp_proposal_secret", "clave-de-pruebas")
    require_secrets(
        {"MCP_PROPOSAL_SECRET": settings.mcp_proposal_secret}, servicio="mcp-server"
    )
