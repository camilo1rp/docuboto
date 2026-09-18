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


# ---------------------------------------------------------------------------
# Despacho y producción documental (F3: era el flujo con menos cobertura)
# ---------------------------------------------------------------------------


def test_una_vista_previa_no_gasta_friccion_de_escritura() -> None:
    """`combinar_plantilla` es POST y NO es escritura.

    El método lo impone el contrato —el contexto va en el cuerpo—, pero no
    persiste nada. Marcarla como escritura pediría confirmación para mirar, y la
    fricción gastada donde no toca deja de funcionar donde sí: quien confirma
    diez veces al día acaba confirmando sin leer.
    """
    t = _POR_NOMBRE["combinar_plantilla"]
    assert t.method == "POST"
    assert t.write is False
    assert t.destructive is None  # no se declara: no es escritura


def test_devolver_un_borrador_aprobado_avisa_de_lo_que_cuesta() -> None:
    """No destruye el borrador, pero tira las aprobaciones ya dadas.

    Volver a pedirlas a las mismas personas es el coste, y no se deshace. Es
    alto impacto aunque no destruya: quien lo pide tiene que saberlo antes.
    """
    t = _POR_NOMBRE["devolver_borrador_a_edicion"]
    assert t.write is True
    assert t.destructive is True
    assert "no se arrastran" in t.description.lower()


def test_el_despacho_se_puede_seguir() -> None:
    """Las dos preguntas distintas sobre un envío, y son distintas.

    Una lee lo que el sistema tiene guardado; la otra le pregunta al operador.
    Cuando nadie ha registrado todavía la entrega, solo la segunda contesta.
    """
    assert ("GET", "/api/v1/envios/{shipment_id}") in _RUTAS
    assert ("GET", "/api/v1/envios/{shipment_id}/tracking") in _RUTAS


def test_el_pdf_y_la_edicion_de_plantillas_quedan_declarados() -> None:
    """Otra vez: omitir y declarar no son lo mismo.

    Pedir «enséñame el oficio» es lo más natural del mundo, y la respuesta
    —«el PDF no cabe en una conversación, pero sí el texto combinado»— solo
    existe si alguien la escribió.
    """
    operaciones = " ".join(x["operacion"] for x in NO_OFRECIDAS).lower()
    assert "pdf de un borrador" in operaciones
    for x in NO_OFRECIDAS:
        if "pdf de un borrador" in x["operacion"].lower():
            assert "combinada" in x["via_alternativa"]
            break
    else:  # pragma: no cover - lo cubre el assert de arriba
        raise AssertionError("no se encontró la exclusión del PDF")


def test_el_rastreo_no_promete_un_operador_que_no_esta_conectado() -> None:
    """Mi propia descripción prometía más de lo que el sistema entrega.

    `consultar_estado` es un stub que devuelve `None` siempre, y el backend lo
    dice con honestidad (`operador_conectado: false`). Anunciarle al modelo que
    la herramienta «sirve para saber si ya llegó» convertía esa honestidad en
    una promesa falsa un salto más allá: el modelo habría leído la ausencia de
    estado como «sin novedad», que es exactamente lo que el backend se cuida de
    no decir.
    """
    d = _POR_NOMBRE["rastrear_envio"].description.lower()
    assert "stub" in d
    assert "sin \u00absin novedad\u00bb" in d or "no significa" in d
