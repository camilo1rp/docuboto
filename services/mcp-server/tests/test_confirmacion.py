# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Qué se confirma, y qué cuesta confirmarlo (E18, F7).

El gate tenía un solo escalón: la frase fija `CONFIRMO` para todo lo
irreversible. Con la eliminación de documentos en el catálogo eso deja de
bastar, y no por severidad sino por **objeto**: una frase fija se teclea de
memoria antes de leer nada, así que confirma «un acto», no «este acto».

Aquí se prueban las tres cosas que se decidieron:

1. la escalera —nada · frase fija · identificador de la tarjeta—;
2. que la exigencia la fije el CATÁLOGO sobre los argumentos del token firmado,
   nunca el cliente;
3. que la fricción sea **asimétrica**: retirar un lote (que PARA la destrucción)
   es más barato que avalarlo (que la habilita). Si parar costara lo mismo que
   seguir, la fricción empujaría hacia adelante.
"""

from __future__ import annotations

import pytest

from app.services.write_gate import FRASE_IRREVERSIBLE, frase_requerida, frase_valida
from app.tools.catalog import card_fields, get_tool

LOTE = "44444444-4444-4444-4444-444444444444"


def test_una_escritura_reversible_no_pide_teclear_nada() -> None:
    """Vincular, crear una caja o proponer un lote se aprueban con un clic: la
    fricción gastada donde no hace falta la devalúa donde sí."""
    assert frase_requerida(get_tool("proponer_lote_eliminacion"), {"codigo": "L-1"}) is None
    assert frase_requerida(get_tool("crear_unidad"), {}) is None


def test_lo_irreversible_pide_la_frase_fija() -> None:
    assert frase_requerida(get_tool("radicar_documento"), {}) == FRASE_IRREVERSIBLE
    assert frase_requerida(get_tool("publicar_inventario_lote"), {"lote_id": LOTE}) == FRASE_IRREVERSIBLE


def test_el_acto_que_HABILITA_la_destruccion_pide_el_identificador() -> None:
    """El salto que importa. Avalar un lote es lo que abre la puerta a borrar
    documentos de archivo: para confirmarlo hay que reproducir el identificador
    del lote, y ese valor solo se conoce mirando la tarjeta."""
    exigida = frase_requerida(get_tool("avalar_lote_eliminacion"), {"lote_id": LOTE})
    assert exigida == LOTE
    assert exigida != FRASE_IRREVERSIBLE


def test_PARAR_la_destruccion_cuesta_menos_que_autorizarla() -> None:
    """Fricción asimétrica, y es una decisión de diseño, no un descuido: dos
    actos opuestos con el mismo coste inclinan la balanza hacia el que destruye."""
    avalar = frase_requerida(get_tool("avalar_lote_eliminacion"), {"lote_id": LOTE})
    anular = frase_requerida(get_tool("anular_lote_eliminacion"), {"lote_id": LOTE})
    assert avalar is not None
    assert anular is None


def test_la_frase_del_acto_ANTERIOR_no_sirve_para_este() -> None:
    """Lo que convierte el identificador en un control y no en un trámite: la
    respuesta correcta cambia con el objeto."""
    otro = "55555555-5555-5555-5555-555555555555"
    assert frase_valida(otro, esperada=LOTE) is False
    assert frase_valida(FRASE_IRREVERSIBLE, esperada=LOTE) is False
    assert frase_valida(LOTE, esperada=LOTE) is True


def test_se_admite_el_tecleo_humano_pero_no_el_valor_equivocado() -> None:
    """Sin distinguir mayúsculas ni espacios de sobra: exigir capitalización
    exacta convierte el control en un acertijo, y quien pelea tres veces con un
    identificador acaba copiando sin leer — justo lo que esto evita."""
    assert frase_valida(f"  {LOTE.upper()}  ", esperada=LOTE) is True
    assert frase_valida("", esperada=LOTE) is False
    assert frase_valida(None, esperada=LOTE) is False


def test_sin_el_parametro_se_cae_a_la_frase_fija_y_no_a_ninguna() -> None:
    """Degradar hacia MÁS control. Si el identificador no viaja, la alternativa
    no puede ser dejar de exigir nada."""
    assert frase_requerida(get_tool("avalar_lote_eliminacion"), {}) == FRASE_IRREVERSIBLE


def test_la_tarjeta_resume_las_listas_en_vez_de_volcarlas() -> None:
    """Un lote lleva los identificadores de lo que va a destruir. Volcar cuarenta
    UUID no informa: TAPA, y quien decide acaba mirando un muro de texto en vez
    de la única cifra que importa."""
    campos = card_fields(
        get_tool("proponer_lote_eliminacion"),
        {"codigo": "LOTE-2026-0007", "expediente_ids": [LOTE, LOTE, LOTE]},
    )
    por_arg = {c["arg"]: c["valor"] for c in campos}
    assert por_arg["expediente_ids"] == "3 elementos"
    assert por_arg["codigo"] == "LOTE-2026-0007"


def test_el_acto_que_DESTRUYE_no_esta_en_el_catalogo() -> None:
    """La decisión de fondo de F7: se ofrecen las cuatro fases previas y no la
    quinta. La tarjeta encarece el descuido; no convierte a un modelo en una
    persona, y cuando lo que está en juego es destruir documentos de archivo esa
    diferencia deja de ser teórica."""
    from app.tools.catalog import CATALOG

    rutas = {t.path for t in CATALOG}
    assert "/api/v1/eliminacion/lotes/{lote_id}/ejecutar" not in rutas


def test_y_la_ausencia_se_DECLARA_con_su_motivo_y_su_via() -> None:
    """Un catálogo recortado en silencio hace que el límite se descubra pidiendo
    algo que nunca va a ocurrir."""
    from app.services.scope import NO_OFRECIDAS

    ejecutar = [x for x in NO_OFRECIDAS if "borrado de un lote" in x["operacion"]]
    assert ejecutar, "el acto no ofrecido tiene que aparecer en el marco de alcance"
    assert ejecutar[0]["motivo"]
    assert ejecutar[0]["via_alternativa"]


@pytest.mark.parametrize("nombre", ["avalar_lote_eliminacion", "generar_acta_eliminacion"])
def test_los_actos_de_eliminacion_declaran_su_irreversibilidad(nombre: str) -> None:
    assert get_tool(nombre).destructive is True


# ---------------------------------------------------------------------------
# F8 · Sellar en WORM: el escalón es «no se ofrece», y por una razón distinta
# de la de `ejecutar`.
#
# En F7 el motivo era la garantía humana. Aquí es anterior: el acto exige un
# identificador que el llamante NO PUEDE conocer legítimamente, así que la
# confirmación sería sobre un valor inventado. Una tarjeta que enseña un UUID
# que la persona no tiene forma de contrastar no es un control, es un trámite.
# ---------------------------------------------------------------------------


def test_sellar_en_WORM_no_esta_en_el_catalogo() -> None:
    from app.tools.catalog import CATALOG

    rutas = {t.path for t in CATALOG}
    assert "/api/v1/preservacion/proteger-indice" not in rutas
    assert "/api/v1/preservacion/proteger-artefacto" not in rutas
    assert "/api/v1/preservacion/aip" not in rutas


def test_pero_SÍ_se_puede_ver_lo_que_quedo_pendiente() -> None:
    """No ofrecer el acto no puede significar dejar a ciegas: el sellado ocurre
    solo al cerrar, y lo que hace falta es enterarse de lo que quedó a medias."""
    assert get_tool("indices_pendientes_firma") is not None
    assert get_tool("artefactos_pendientes_renovacion") is not None
    assert get_tool("firmar_indice") is not None


def test_el_sellado_no_ofrecido_se_declara_con_su_motivo() -> None:
    from app.services.scope import NO_OFRECIDAS

    sellado = [x for x in NO_OFRECIDAS if "WORM" in x["operacion"]]
    assert sellado, "el sellado tiene que aparecer en el marco como no ofrecido"
    assert "no se deshace" in sellado[0]["motivo"]
    assert sellado[0]["via_alternativa"]


def test_migrar_formato_NO_es_irreversible_porque_el_original_se_conserva() -> None:
    """La distinción que evita gastar fricción donde no toca: migrar crea un
    derivado y deja el original intacto (Decreto 2609/2012 art. 32)."""
    assert get_tool("migrar_formato").destructive is False


def test_anotar_en_el_registro_PREMIS_sí_lo_es() -> None:
    """No destruye, pero el registro es append-only: lo irreversible aquí es la
    AFIRMACIÓN, no la pérdida. Una intervención mal descrita queda dicha."""
    assert get_tool("registrar_evento_preservacion").destructive is True


def test_declarar_el_plan_de_preservacion_no_pide_teclear_nada() -> None:
    """Versionado append-only: la corrección es declarar otra versión, y la
    anterior sigue ahí."""
    assert frase_requerida(get_tool("declarar_plan_preservacion"), {}) is None


# ---------------------------------------------------------------------------
# F9 · Instrumentos: la fricción va en el NÚMERO DE VERSIÓN
#
# Convalidar y derogar cambian qué tabla gobierna la clasificación de aquí en
# adelante. El error irreparable no es «convalidar» —eso es lo que toca— sino
# convalidar la versión EQUIVOCADA: no se corrige con otro acto, hay que abrir
# una versión nueva y recorrer el circuito entero, con la tabla equivocada
# rigiendo mientras tanto.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "nombre", ["convalidar_version_trd", "derogar_version_trd", "convalidar_version_tvd", "derogar_version_tvd"]
)
def test_los_actos_que_cambian_QUE_TABLA_RIGE_piden_el_numero_de_version(nombre: str) -> None:
    assert frase_requerida(get_tool(nombre), {"code": "DEM-100", "version": 4}) == "4"


@pytest.mark.parametrize("nombre", ["aprobar_version_trd", "devolver_version_trd", "nueva_version_serie_trd"])
def test_los_pasos_CON_vuelta_atras_no_piden_nada(nombre: str) -> None:
    """El circuito ofrece `devolver` para regresar de aprobada a borrador: poner
    ahí la fricción del acto irreversible sería gastarla donde hay remedio."""
    assert frase_requerida(get_tool(nombre), {"code": "DEM-100", "version": 4}) is None


def test_las_dos_consultas_de_preservacion_van_por_la_ruta_MEDIADA() -> None:
    """Las de storage están selladas con `X-Internal-Token` y solo las alcanza el
    propio servicio: es el mediador de archive quien resuelve el clearance del
    llamante contra el nivel vigente del expediente. Apuntar a la sellada habría
    dado 401 a todo el mundo."""
    assert get_tool("consultar_aip").path == "/api/v1/expedientes/{expediente_id}/aip"
    assert (
        get_tool("eventos_preservacion").path
        == "/api/v1/expedientes/{expediente_id}/preservacion/eventos"
    )


def test_la_estructura_del_PINAR_no_se_arma_desde_aqui_y_se_declara() -> None:
    from app.services.scope import NO_OFRECIDAS
    from app.tools.catalog import CATALOG

    rutas = {t.path for t in CATALOG}
    assert "/api/v1/pinar/planes/{plan_id}/objetivos" not in rutas or not any(
        t.write for t in CATALOG if t.path == "/api/v1/pinar/planes/{plan_id}/objetivos"
    )
    pinar = [x for x in NO_OFRECIDAS if "PINAR" in x["operacion"]]
    assert pinar and pinar[0]["via_alternativa"]
