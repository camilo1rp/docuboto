# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Validación de argumentos contra el catálogo (`coerce_args`).

Es la barrera real: `additionalProperties: False` del JSON Schema es advisory
para el modelo y no lo aplica nadie del lado del servidor.
"""

from __future__ import annotations

import pytest

from app.tools.catalog import ArgError, Param, Tool, card_fields, coerce_args, get_tool, json_schema

_TOOL = Tool(
    name="t",
    description="",
    method="POST",
    path="/api/v1/x/{id}",
    path_params=("id",),
    params=(
        Param(name="id", required=True, location="path", format="uuid", label="Objeto"),
        Param(name="texto", min_length=2, max_length=5, label="Texto"),
        Param(name="n", type="integer", minimum=1, maximum=10, label="Número"),
        Param(name="tipo", enum=("E", "S"), label="Tipo"),
        Param(name="flag", type="boolean", label="Bandera"),
        Param(name="pag", type="integer", show_in_card=False),
    ),
)

_UUID = "11111111-1111-1111-1111-111111111111"


def test_una_clave_no_declarada_se_rechaza_no_se_descarta() -> None:
    """El silencio es el problema: una inyección debe dejar rastro.

    La versión anterior filtraba las claves desconocidas sin decir nada. Es
    cómodo hasta el día en que quien cuela una clave es el contenido de un
    radicado de Entrada, que lo redacta un tercero externo.
    """
    with pytest.raises(ArgError) as exc:
        coerce_args(_TOOL, {"id": _UUID, "acl": {"max": 3}})
    assert exc.value.code == "parametro_no_declarado"
    assert "acl" in exc.value.detail


def test_falta_un_obligatorio() -> None:
    with pytest.raises(ArgError) as exc:
        coerce_args(_TOOL, {"texto": "hola"})
    assert exc.value.code == "falta_parametro"


def test_un_uuid_mal_formado_no_llega_al_backend() -> None:
    """Vale por el mensaje, y además es la primera capa contra el traversal."""
    with pytest.raises(ArgError) as exc:
        coerce_args(_TOOL, {"id": "../../auth/me"})
    assert exc.value.code == "valor_invalido"


def test_un_booleano_no_cuela_como_entero() -> None:
    """`bool` es subclase de `int` en Python: sin la guarda, `True` pasaría."""
    with pytest.raises(ArgError) as exc:
        coerce_args(_TOOL, {"id": _UUID, "n": True})
    assert exc.value.code == "tipo_invalido"


def test_limites_y_enumeraciones() -> None:
    for malo, code in (
        ({"texto": "a"}, "valor_invalido"),  # por debajo del mínimo
        ({"texto": "abcdef"}, "valor_invalido"),  # por encima del máximo
        ({"n": 0}, "valor_invalido"),
        ({"n": 11}, "valor_invalido"),
        ({"tipo": "X"}, "valor_invalido"),
    ):
        with pytest.raises(ArgError) as exc:
            coerce_args(_TOOL, {"id": _UUID, **malo})
        assert exc.value.code == code, malo


def test_los_opcionales_ausentes_no_viajan() -> None:
    """Un `None` explícito tampoco: enviar `null` a un campo opcional no es
    lo mismo que omitirlo, y algunos backends lo tratan como borrado."""
    salida = coerce_args(_TOOL, {"id": _UUID, "texto": None})
    assert salida == {"id": _UUID}


def test_el_camino_valido_pasa_entero() -> None:
    salida = coerce_args(_TOOL, {"id": _UUID, "texto": "hola", "n": 5, "tipo": "E", "flag": True})
    assert salida == {"id": _UUID, "texto": "hola", "n": 5, "tipo": "E", "flag": True}


def test_la_tarjeta_omite_el_ruido_y_usa_etiquetas() -> None:
    campos = card_fields(_TOOL, {"id": _UUID, "texto": "hola", "pag": 2})
    etiquetas = [c["etiqueta"] for c in campos]
    assert etiquetas == ["Objeto", "Texto"]  # `pag` no se pinta
    assert campos[0]["valor"] == _UUID


def test_el_schema_declara_obligatorios_y_cierra_la_puerta() -> None:
    esquema = json_schema(_TOOL)
    assert esquema["required"] == ["id"]
    assert esquema["additionalProperties"] is False
    assert esquema["properties"]["tipo"]["enum"] == ["E", "S"]
    assert esquema["properties"]["texto"]["minLength"] == 2


# ---------------------------------------------------------------------------
# Tipificar (E03) — el acto que el backend no tenía
# ---------------------------------------------------------------------------


def test_tipificar_solo_se_ofrece_con_nivel_de_creacion() -> None:
    """`min_crud=3`, y en los DOS sentidos.

    El backend exige nivel de creación para tipificar (fija la serie TRD, y con
    ella la retención), así que ofrecer la tool a quien solo tiene lectura
    produciría un 403 al confirmar — el defecto que el marco de alcance existe
    para evitar. Y exigir de más la escondería a quien sí puede, que falla en
    silencio y es peor.
    """
    from app.tools.catalog import get_tool, puede_usar

    tool = get_tool("tipificar_radicado")
    assert tool is not None
    perms = {"PERM_RADI"}
    assert not puede_usar(tool, perms, levels={"PERM_RADI": 1})
    assert not puede_usar(tool, perms, levels={"PERM_RADI": 2})
    assert puede_usar(tool, perms, levels={"PERM_RADI": 3})


def test_tipificar_viaja_como_PATCH_con_el_tipo_en_el_cuerpo() -> None:
    """El id va en la ruta y el tipo en el cuerpo; el método es PATCH, no POST.

    Antes de que el dispatcher supiera de PATCH, todo lo que no era GET caía en
    un POST incondicional — y `POST /documents/{id}` no existe: habría sido un
    404 presentado como un fallo del sistema.
    """
    from app.services.dispatcher import build_request
    from app.tools.catalog import coerce_args, get_tool

    tool = get_tool("tipificar_radicado")
    assert tool is not None
    args = coerce_args(
        tool,
        {
            "document_id": "11111111-1111-1111-1111-111111111111",
            "doc_class": "Contratos",
            "metadata": {"numero_contrato": "C-1"},
        },
    )
    preparada = build_request(tool, args)

    assert preparada.method == "PATCH"
    assert preparada.path == "/api/v1/documents/11111111-1111-1111-1111-111111111111"
    assert preparada.body == {"doc_class": "Contratos", "metadata": {"numero_contrato": "C-1"}}
    assert preparada.query == {}


def test_los_dos_lados_del_xor_de_antecedentes_se_rechazan_antes_de_salir() -> None:
    """Ni ambos ni ninguno — y el error se da aquí, no lo devuelve el backend.

    `buscar_antecedentes` acepta el asunto o el radicado de referencia, nunca
    los dos: lo impone `AntecedentesRequest` con un `model_validator`. Con los
    dos parámetros anunciados como simplemente opcionales, el modelo manda ambos
    —que es lo natural cuando tiene los dos datos a mano— y recibe un 422 del
    backend después de haber hecho esperar a la persona. Ese es el límite
    descubierto fallando, en pequeño.

    Se comprueban los DOS lados porque son dos errores distintos del modelo:
    mandar de más (tiene ambos datos) y mandar de menos (no tiene ninguno).
    """
    from app.tools.catalog import coerce_args, get_tool

    tool = get_tool("buscar_antecedentes")
    assert tool is not None

    with pytest.raises(ArgError) as ambos:
        coerce_args(tool, {"query": "derecho de petición", "radicado_ref": "2026-X-E-000001"})
    assert ambos.value.code == "parametros_excluyentes"

    with pytest.raises(ArgError) as ninguno:
        coerce_args(tool, {"top_k": 3})
    assert ninguno.value.code == "parametros_excluyentes"

    # Control positivo: cada lado por separado pasa entero. Sin esto, un
    # `exactly_one_of` que rechazara SIEMPRE dejaría los dos asertos de arriba
    # verdes y la tool muerta.
    assert coerce_args(tool, {"query": "derecho de petición"}) == {"query": "derecho de petición"}
    assert coerce_args(tool, {"radicado_ref": "2026-X-E-000001"}) == {
        "radicado_ref": "2026-X-E-000001"
    }


def test_un_grupo_excluyente_no_cuenta_los_nulos_como_presentes() -> None:
    """`{"query": None}` es «no lo mando», no «lo mando vacío».

    El modelo rellena huecos con `null` con toda naturalidad. Si el grupo
    contara la CLAVE en vez del valor efectivo, mandar `radicado_ref` con
    `query: null` —perfectamente válido— sería rechazado, y el mensaje diría
    que se enviaron los dos cuando el usuario solo pidió uno.
    """
    from app.tools.catalog import coerce_args, get_tool

    tool = get_tool("buscar_antecedentes")
    assert tool is not None
    assert coerce_args(tool, {"query": None, "radicado_ref": "2026-X-E-000001"}) == {
        "radicado_ref": "2026-X-E-000001"
    }


def test_la_retencion_lleva_la_serie_en_la_ruta_y_la_fecha_en_la_query() -> None:
    """Primera tool con un argumento OBLIGATORIO que no es de ruta ni de cuerpo.

    `GET /trd/{serie_id}/retention` exige `closed_at` como query. El reparto por
    `location="auto"` lo resuelve —ruta si está en la plantilla, query en un
    GET—, pero hasta ahora ningún GET tenía un obligatorio fuera de la ruta, así
    que la regla no estaba ejercida en ese caso. Si la fecha acabara en el
    cuerpo, el backend devolvería 422 por un argumento que el usuario sí dio.

    Un GET no lleva cuerpo: se comprueba explícitamente, porque un cuerpo vacío
    y un cuerpo con la fecha dentro se distinguen solo aquí.
    """
    from app.services.dispatcher import build_request
    from app.tools.catalog import coerce_args, get_tool

    tool = get_tool("consultar_retencion")
    assert tool is not None
    args = coerce_args(
        tool,
        {"serie_id": "22222222-2222-2222-2222-222222222222", "closed_at": "2026-03-01"},
    )
    preparada = build_request(tool, args)

    assert preparada.method == "GET"
    assert preparada.path == "/api/v1/trd/22222222-2222-2222-2222-222222222222/retention"
    assert preparada.query == {"closed_at": "2026-03-01"}
    assert not preparada.body


def test_dependencia_se_indica_por_codigo_no_por_nombre():
    """B5 (ADR-032): asignar/trasladar/devolver piden `to_dept_code`; el origen lo deriva el servicio."""
    from app.tools.catalog import get_tool

    for nombre in ("asignar_radicado", "trasladar_radicado", "devolver_radicado"):
        params = {p.name: p for p in get_tool(nombre).params}
        assert "to_dept" not in params, nombre
        assert "from_dept" not in params, nombre
        assert params["to_dept_code"].required and params["to_dept_code"].type == "integer", nombre
    for nombre in ("bandeja_tramite", "pendientes_equipo"):
        names = {p.name for p in get_tool(nombre).params}
        assert "dept_code" in names and "dept" not in names, nombre


def test_destino_solo_por_codigo() -> None:
    """B5 (ADR-032): ninguna tool expone `dest_dept` por nombre (homónimas dan 422)."""
    for nombre in ("actualizar_radicado", "crear_respuesta_vinculada", "crear_borrador", "editar_borrador"):
        tool = get_tool(nombre)
        assert tool is not None
        nombres = {p.name for p in tool.params}
        assert "dest_dept" not in nombres, nombre
        assert "dest_dept_code" in nombres, nombre
