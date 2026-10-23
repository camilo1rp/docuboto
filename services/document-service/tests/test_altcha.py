# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Reto ALTCHA v1 — núcleo puro (C16, ADR-038).

Lo importante, en orden:

1. **El vector dorado** está generado con el algoritmo del widget `altcha@2.3.0`
   (SHA-256 de `salt + number` con `crypto.subtle`, HMAC-SHA256 de la clave sobre
   el `challenge`, `btoa(JSON.stringify(...))`), NO con este código. Si alguien sube
   el widget a 3.x (PBKDF2/Argon2) y olvida el verificador, este test cae.
2. Ida y vuelta emitir → resolver → verificar (control positivo: sin él, todos los
   rechazos de abajo podrían ser rechazos por cualquier otra cosa).
3. Cada rechazo se prueba contra ese control positivo cambiando UNA sola cosa.
"""

from __future__ import annotations

import base64
import json

import pytest

from app.core import altcha

CLAVE = b"orpycamcp-altcha-vector-key-0123456789"
AHORA = 1_800_000_000
MAXN = 100_000

# Generado con el algoritmo del widget 2.3.0 (ver encabezado). expires=2030-01-01, t=demo.
VECTOR_PAYLOAD = (
    "eyJhbGdvcml0aG0iOiJTSEEtMjU2IiwiY2hhbGxlbmdlIjoiYWJmZmRlNTM5YjA5MzRmOTJmYmNlODVjNTc2OTM2ZmYw"
    "MzFjZmIxYjA5ZjFiZTI5YTQyY2FiYTdkNTM4ZGJmMyIsIm51bWJlciI6NDI0Miwic2FsdCI6IjAxMjM0NTY3ODlhYmNk"
    "ZWYwMTIzNDU2Nzg5YWJjZGVmP2V4cGlyZXM9MTg5MzQ1NjAwMCZ0PWRlbW8mdj0xIiwic2lnbmF0dXJlIjoiNjRlZjI1"
    "ZWQwMzU5OWJjODY3NDU5ZGNiNTYwODFkZjZjODczMmQyMGI5OThjZDk2MjdiZjEwYTE3ZDMwZGU1MSIsInRvb2siOjE3fQ=="
)
VECTOR_CHALLENGE = "abffde539b0934f92fbce85c576936ff031cfb1b09f1be29a42caba7d538dbf3"


def _ver(payload: str, **kw):
    args = {"clave": CLAVE, "tenant": "demo", "maxnumber": MAXN, "ahora": AHORA} | kw
    return altcha.verificar(payload, **args)


def _reto(**kw):
    args = {"clave": CLAVE, "tenant": "demo", "ahora": AHORA, "ttl": 300, "maxnumber": 2_000} | kw
    return altcha.emitir_reto(**args)


def _payload_de(reto: dict, number: int | None = None, **cambios) -> str:
    n = altcha.resolver_reto(reto) if number is None else number
    datos = json.loads(base64.b64decode(altcha.codificar_payload(reto, n)))
    datos.update(cambios)
    return base64.b64encode(json.dumps(datos).encode()).decode()


def _rechaza(payload: str, codigo: str = "antirrobots_invalido", **kw) -> None:
    with pytest.raises(altcha.AltchaRechazado) as exc:
        _ver(payload, **kw)
    assert exc.value.codigo == codigo


# ----------------------------------------------------------------- el contrato


def test_vector_dorado_del_widget_2_3_0() -> None:
    challenge, expires = _ver(VECTOR_PAYLOAD)
    assert challenge == VECTOR_CHALLENGE
    assert expires == 1_893_456_000


def test_el_vector_dorado_deja_de_valer_si_cambia_el_protocolo() -> None:
    """Un payload de otra versión (`v=2`) NO se acepta con el verificador v1."""
    datos = json.loads(base64.b64decode(VECTOR_PAYLOAD))
    datos["salt"] = datos["salt"].replace("v=1", "v=2")
    _rechaza(base64.b64encode(json.dumps(datos).encode()).decode())


def test_ida_y_vuelta_emitir_resolver_verificar() -> None:
    reto = _reto()
    assert set(reto) == {"algorithm", "challenge", "maxnumber", "salt", "signature"}
    assert reto["algorithm"] == "SHA-256"
    challenge, expires = _ver(_payload_de(reto), maxnumber=2_000)
    assert challenge == reto["challenge"]
    assert expires == AHORA + 300


def test_el_salt_lleva_vencimiento_entidad_y_version() -> None:
    reto = _reto()
    assert reto["salt"].split("?", 1)[1] == f"expires={AHORA + 300}&t=demo&v=1"


# --------------------------------------------------------------------- rechazos


def test_firma_alterada() -> None:
    reto = _reto()
    reto["signature"] = ("0" if reto["signature"][0] != "0" else "1") + reto["signature"][1:]
    _rechaza(_payload_de(reto), maxnumber=2_000)


def test_firma_de_otra_clave() -> None:
    _rechaza(_payload_de(_reto(clave=b"otra-clave-distinta-0123456789abcdef")), maxnumber=2_000)


def test_alargar_el_vencimiento_invalida_el_reto() -> None:
    """`expires` está dentro del `salt`: cambiarlo cambia el hash esperado."""
    reto = _reto()
    n = altcha.resolver_reto(reto)
    salt = reto["salt"].replace(f"expires={AHORA + 300}", f"expires={AHORA + 99999}")
    _rechaza(_payload_de(reto, n, salt=salt), maxnumber=2_000)


def test_vencido_se_dice_solo_despues_de_la_firma() -> None:
    reto = _reto(ttl=60)
    _rechaza(_payload_de(reto), "antirrobots_vencido", maxnumber=2_000, ahora=AHORA + 61)
    # …y un vencimiento FALSO no merece un mensaje distinto: firma mala => inválido.
    reto["signature"] = "0" * 64
    _rechaza(_payload_de(reto), "antirrobots_invalido", maxnumber=2_000, ahora=AHORA + 61)


def test_justo_en_el_vencimiento_ya_no_vale() -> None:
    _rechaza(_payload_de(_reto(ttl=60)), "antirrobots_vencido", maxnumber=2_000, ahora=AHORA + 60)


def test_reto_de_otra_entidad() -> None:
    _rechaza(_payload_de(_reto(tenant="otra")), maxnumber=2_000)


def test_algoritmo_no_negociable() -> None:
    for algoritmo in ("SHA-1", "SHA-512", "sha-256", ""):
        _rechaza(_payload_de(_reto(), algorithm=algoritmo), maxnumber=2_000)


def test_numero_fuera_de_rango_o_de_otro_tipo() -> None:
    reto = _reto(maxnumber=2_000)
    n = altcha.resolver_reto(reto)
    # Control positivo: con el número correcto el mismo reto pasa.
    _ver(_payload_de(reto, number=n), maxnumber=2_000)
    for malo in (-1, 10**9, True, 1.5, str(n)):
        _rechaza(_payload_de(reto, number=malo), maxnumber=2_000)
    # Solución correcta pero MAYOR que el tope configurado ahora: fuera de rango.
    _rechaza(_payload_de(reto, number=n), maxnumber=max(0, n - 1))


@pytest.mark.parametrize(
    "crudo",
    [
        "no-es-base64!!!",
        base64.b64encode(b"no es json").decode(),
        base64.b64encode(b"[1,2,3]").decode(),
        base64.b64encode(b'"texto"').decode(),
        base64.b64encode(b"{}").decode(),
        "",
    ],
)
def test_payload_malformado(crudo: str) -> None:
    _rechaza(crudo)


def test_tipos_incorrectos_en_los_campos() -> None:
    reto = _reto()
    for campo in ("algorithm", "challenge", "salt", "signature"):
        _rechaza(_payload_de(reto, **{campo: 123}), maxnumber=2_000)


def test_parametros_del_salt_duplicados_ajenos_o_ausentes() -> None:
    reto = _reto()
    n = altcha.resolver_reto(reto)
    base = reto["salt"].split("?", 1)[0]
    for consulta in (
        f"expires={AHORA + 300}&expires={AHORA + 900}&t=demo&v=1",  # duplicado
        f"expires={AHORA + 300}&t=demo&v=1&x=1",  # ajeno
        f"expires={AHORA + 300}&t=demo",  # falta v
        "t=demo&v=1",  # falta expires
        "expires=abc&t=demo&v=1",  # no numérico
        "expires=-5&t=demo&v=1",
    ):
        _rechaza(_payload_de(reto, n, salt=f"{base}?{consulta}"), maxnumber=2_000)


def test_salt_sin_el_prefijo_aleatorio() -> None:
    reto = _reto()
    n = altcha.resolver_reto(reto)
    _rechaza(_payload_de(reto, n, salt="corto?expires=1&t=demo&v=1"), maxnumber=2_000)


def test_challenge_que_no_corresponde_a_la_solucion() -> None:
    reto = _reto()
    _rechaza(_payload_de(reto, challenge="a" * 64), maxnumber=2_000)
