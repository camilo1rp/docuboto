# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
El circuito de firma: se lee y se prepara, no se ejerce (E06, RF-FIR-04/12/13).

El censo del catálogo contra los 391 endpoints reales encontró el circuito de
firma ENTERO sin exponer — trece endpoints, cero tools—, así que el asistente no
podía contestar «¿qué tengo pendiente de firmar?», que es la pregunta más
frecuente que hay sobre él.

Lo que se añade es la mitad legible: ver los pendientes, el estado de una cadena,
si un documento ya está en circuito, el comprobante de una firma, y abrir un
circuito —que no firma nada ni compromete a nadie—.

La otra mitad NO se ofrece, y el motivo ya estaba decidido en este mismo módulo
para la firma del acta de transferencia: firmar consume un segundo factor de un
solo uso y pedirlo por un chat lo traslada a un canal que no lo custodia. El
visto bueno no consume ninguno y tampoco se ofrece, por otra razón: afirma «he
leído y apruebo», y este canal no puede poner el documento delante de nadie.

Lo que estos tests fijan es que la ausencia sea **declarada** y no un olvido: una
capacidad omitida en silencio es indistinguible de una que se cayó del catálogo,
y el marco de alcance existe justo para que nadie descubra un límite fallando.
"""

from __future__ import annotations

from app.services.scope import NO_OFRECIDAS
from app.tools.catalog import CATALOG

_POR_NOMBRE = {t.name: t for t in CATALOG}
_RUTAS = {(t.method, t.path) for t in CATALOG}


def test_se_puede_preguntar_que_tengo_pendiente_de_firmar() -> None:
    """La pregunta que motivó el incremento."""
    t = _POR_NOMBRE["firmas_pendientes"]
    assert (t.method, t.path) == ("GET", "/api/v1/signature/pending")
    assert t.write is False
    assert t.permission == "PERM_FIRMA"


def test_las_cinco_lecturas_del_circuito_estan() -> None:
    esperadas = {
        ("GET", "/api/v1/signature/pending"),
        ("GET", "/api/v1/signature/requests/{solicitud_id}"),
        ("GET", "/api/v1/signature/requests"),
        ("GET", "/api/v1/signature/firmas/{firma_id}"),
        ("POST", "/api/v1/signature/requests"),
    }
    assert esperadas <= _RUTAS


def test_abrir_un_circuito_no_es_un_acto_irreversible() -> None:
    """Y lo declara, en vez de dejarlo sin decir.

    Abrir no firma: cada turno lo decide su firmante y puede rechazarlo. Lo
    irreversible es firmar, y eso no se ofrece por este canal.
    """
    t = _POR_NOMBRE["abrir_cadena_firma"]
    assert t.write is True
    assert t.destructive is False


def test_el_tipo_de_objeto_va_acotado_y_sin_el_tipo_fantasma() -> None:
    """`documento` estuvo admitido sin productor: una cadena que se creaba y no
    se podía firmar nunca. Se retiró del backend; el catálogo no lo reintroduce.
    """
    for nombre in ("abrir_cadena_firma", "cadena_firma_de_un_objeto"):
        enum = _POR_NOMBRE[nombre].params_by_name["objeto_tipo"].enum
        assert enum == ("radicado", "expediente", "borrador"), nombre
        assert "documento" not in enum, nombre


def test_firmar_aprobar_y_rechazar_NO_estan_en_el_catalogo() -> None:
    """Control negativo, escrito por ruta y no por nombre de tool.

    Por nombre sería fácil de burlar sin querer: basta llamarla de otra forma.
    """
    prohibidas = {
        ("POST", "/api/v1/signature/requests/{solicitud_id}/sign"),
        ("POST", "/api/v1/signature/requests/{solicitud_id}/vobo"),
        ("POST", "/api/v1/signature/requests/{solicitud_id}/reject"),
        ("POST", "/api/v1/signature/requests/{solicitud_id}/challenge"),
        ("POST", "/api/v1/signature/batch"),
        ("POST", "/api/v1/signature/sign-personal"),
        ("POST", "/api/v1/signature/verify"),
    }
    assert prohibidas & _RUTAS == set()


def test_la_ausencia_esta_declarada_y_no_solo_omitida() -> None:
    """Omitir en silencio y declarar no son lo mismo.

    Una capacidad que falta sin explicación es indistinguible de una que se cayó
    del catálogo por descuido — y quien pregunte se queda sin saber por dónde sí.
    """
    operaciones = " ".join(x["operacion"] for x in NO_OFRECIDAS).lower()
    assert "firmar un turno" in operaciones
    assert "integridad de una firma" in operaciones

    for x in NO_OFRECIDAS:
        if "firmar un turno" in x["operacion"].lower():
            # Las dos mitades de una exclusión útil: por qué no, y por dónde sí.
            assert "segundo factor" in x["motivo"]
            assert x["via_alternativa"].strip()
            # Y que diga que el visto bueno también queda fuera, que es la parte
            # que más sorprende: no consume factor y aun así no se ofrece.
            assert "visto bueno" in x["motivo"]
            break
    else:  # pragma: no cover - lo cubre el assert de arriba
        raise AssertionError("no se encontró la exclusión de la firma")
