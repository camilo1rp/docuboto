# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Procedimientos: el trámite entero, y qué pasa cuando se rompe a mitad.

Lo que aquí importa **no** es que la cadena feliz funcione —eso es el control
positivo— sino lo otro: qué se dice cuando el segundo paso falla y el primero ya
ocurrió. Los pasos son llamadas HTTP a servicios distintos, no hay transacción
que los abarque, y radicar consume un número inmutable que no vuelve al
mostrador. Un procedimiento que dijera «falló» sin más llevaría a repetirlo
entero y a duplicar el radicado, que es el daño más caro que puede causar este
sistema.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import procedure_runner
from app.tools.procedures import (
    PROCEDIMIENTOS,
    Paso,
    Procedimiento,
    ProcedureError,
    get_procedimiento,
    resolver_args,
    tool_de,
)

RAD_ID = "22222222-2222-2222-2222-222222222222"


def _cliente(*respuestas: httpx.Response) -> AsyncMock:
    cliente = AsyncMock()
    cliente.request = AsyncMock(side_effect=list(respuestas))
    return cliente


async def _correr(proc: Procedimiento, args: dict, cliente: AsyncMock) -> dict:
    # La identidad entra porque cada paso se REAUTORIZA: el permiso agregado del
    # procedimiento sirve para ofrecerlo, no para dar por bueno todo lo que
    # contiene. Aquí se pasa como raíz para que las pruebas de este archivo sigan
    # midiendo lo suyo —el encadenado de pasos— y no el permiso.
    return await procedure_runner.ejecutar(
        cliente,
        proc,
        args,
        authorization="Bearer x",
        tenant_slug="demo",
        permissions=set(),
        is_root=True,
    )


# ---------------------------------------------------------------------------
# La declaración
# ---------------------------------------------------------------------------


def test_todo_paso_nombra_una_tool_que_EXISTE() -> None:
    """Un paso que apunte a una tool inexistente es un procedimiento que solo
    falla cuando alguien lo invoca, en producción y a mitad del trámite."""
    for proc in PROCEDIMIENTOS:
        for paso in proc.pasos:
            assert tool_de(paso) is not None, f"{proc.name} → {paso.tool}"


def test_ningun_procedimiento_se_llama_como_una_tool() -> None:
    """Los dos catálogos comparten espacio de nombres al invocar: una colisión
    haría que `get_tool(...) or get_procedimiento(...)` resolviera siempre al
    primero, y el acto ejecutado no sería el anunciado."""
    from app.tools.catalog import CATALOG

    nombres_tool = {t.name for t in CATALOG}
    for proc in PROCEDIMIENTOS:
        assert proc.name not in nombres_tool, proc.name


def test_toda_escritura_declara_si_es_irreversible() -> None:
    """`None` significa «nadie lo declaró», y un acto irreversible sin declarar
    se confirmaría con la misma fricción que cambiar un asunto."""
    for proc in PROCEDIMIENTOS:
        if proc.write:
            assert proc.destructive is not None, proc.name


def test_los_pasos_viajan_al_cliente() -> None:
    """Quien confirma un procedimiento aprueba una CADENA de actos: ocultársela
    sería pedirle una firma en blanco."""
    esquema = get_procedimiento("tramitar_peticion_ciudadana").to_schema()
    assert [p["tool"] for p in esquema["pasos"]] == [
        "radicar_documento",
        "asignar_radicado",
        "vincular_radicado",
    ]


# ---------------------------------------------------------------------------
# El enlace entre pasos
# ---------------------------------------------------------------------------


def test_un_enlace_roto_ABORTA_en_vez_de_mandar_nulo() -> None:
    """Si el paso anterior no devolvió el identificador, seguir significaría
    enlazar contra `None` y crear un vínculo huérfano que nadie notaría."""
    paso = Paso(etiqueta="x", tool="asignar_radicado", args={"radicado_id": "#radicar.id"})
    with pytest.raises(ProcedureError):
        resolver_args(paso, {}, {"radicar": {"tracking_number": "2026-DEMO-E-1"}})


def test_un_argumento_OPCIONAL_ausente_simplemente_no_viaja() -> None:
    paso = Paso(etiqueta="x", tool="radicar_documento", args={"pages": "@pages"})
    assert resolver_args(paso, {}, {}) == {}


# ---------------------------------------------------------------------------
# La ejecución
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_la_cadena_feliz_encadena_el_numero_recien_creado() -> None:
    """El control positivo, y lo que un procedimiento aporta: el identificador
    del radicado que acaba de nacer llega al paso que lo reparte sin que nadie
    lo copie a mano."""
    proc = get_procedimiento("tramitar_peticion_ciudadana")
    cliente = _cliente(
        httpx.Response(201, json={"id": RAD_ID, "tracking_number": "2026-DEMO-E-000001"}),
        httpx.Response(201, json={"step_id": "s-1"}),
    )
    parte = await _correr(
        proc,
        {
            "subject": "Petición",
            "sender_name": "Ana",
            "dest_dept_code": 902,
            "to_dept_code": 902,
        },
        cliente,
    )

    assert parte["completado"] is True
    assert [p["paso"] for p in parte["pasos"]] == ["radicar", "repartir", "expedientar"]
    cuerpo_asignacion = cliente.request.await_args_list[1].kwargs["json"]
    assert cuerpo_asignacion["radicado_id"] == RAD_ID
    assert cuerpo_asignacion["tracking_number"] == "2026-DEMO-E-000001"


@pytest.mark.asyncio
async def test_el_paso_condicionado_se_OMITE_y_se_dice() -> None:
    """Sin expediente no se expedienta, y eso no es un fallo: se informa como
    omitido para que quien lea sepa que ese radicado quedó sin expedientar."""
    proc = get_procedimiento("tramitar_peticion_ciudadana")
    cliente = _cliente(
        httpx.Response(201, json={"id": RAD_ID, "tracking_number": "2026-DEMO-E-000001"}),
        httpx.Response(201, json={}),
    )
    parte = await _correr(
        proc,
        {"subject": "P", "sender_name": "Ana", "dest_dept_code": 902, "to_dept_code": 902},
        cliente,
    )
    omitido = [p for p in parte["pasos"] if p["paso"] == "expedientar"][0]
    assert omitido["estado"] == "omitido"
    assert cliente.request.await_count == 2


@pytest.mark.asyncio
async def test_si_falla_el_SEGUNDO_paso_se_dice_QUE_quedo_hecho() -> None:
    """**El caso que justifica todo este diseño.** El radicado ya tiene número y
    no se puede deshacer. Un «falló» a secas llevaría a repetir el procedimiento
    entero y a radicar dos veces la misma petición."""
    proc = get_procedimiento("tramitar_peticion_ciudadana")
    cliente = _cliente(
        httpx.Response(201, json={"id": RAD_ID, "tracking_number": "2026-DEMO-E-000007"}),
        httpx.Response(403, json={"error": "forbidden", "detail": "sin permiso de trámite"}),
    )
    parte = await _correr(
        proc,
        {"subject": "P", "sender_name": "Ana", "dest_dept_code": 902, "to_dept_code": 902},
        cliente,
    )

    assert parte["completado"] is False
    assert parte["pasos_ejecutados"] == ["radicar"]
    # El número YA existe y viaja en el parte: es lo único que impide repetirlo.
    assert parte["pasos"][0]["resultado"]["tracking_number"] == "2026-DEMO-E-000007"
    # El motivo es el que dio el servicio, no una frase propia que lo tape.
    assert "sin permiso de trámite" in parte["pasos"][1]["motivo"]
    assert "NO se deshacen" in parte["advertencia"]


@pytest.mark.asyncio
async def test_un_paso_OPCIONAL_que_falla_no_tumba_el_procedimiento() -> None:
    """Un radicado recién creado no tiene hoja de ruta ni respuestas: que falten
    no puede convertir una consulta en un error que obligue a preguntar pieza
    por pieza — que es justo lo que este procedimiento evita."""
    proc = get_procedimiento("estado_del_tramite")
    cliente = _cliente(
        httpx.Response(200, json={"id": RAD_ID, "subject": "Petición"}),
        httpx.Response(404, json={"error": "not_found"}),
        httpx.Response(200, json={"items": []}),
        httpx.Response(200, json={"items": []}),
        httpx.Response(200, json={"items": []}),
    )
    parte = await _correr(proc, {"document_id": RAD_ID}, cliente)

    assert parte["completado"] is True
    fallido = [p for p in parte["pasos"] if p["estado"] == "fallido"]
    assert [p["paso"] for p in fallido] == ["paso_actual"]


@pytest.mark.asyncio
async def test_cada_paso_lleva_el_token_de_la_PERSONA() -> None:
    """Un procedimiento es un orden, no una credencial: si no fuera el token del
    usuario, encadenar tools se habría convertido en una escalada de permisos."""
    proc = get_procedimiento("estado_del_tramite")
    cliente = _cliente(*[httpx.Response(200, json={}) for _ in range(5)])
    await _correr(proc, {"document_id": RAD_ID}, cliente)

    for llamada in cliente.request.await_args_list:
        assert llamada.kwargs["headers"]["Authorization"] == "Bearer x"
        assert llamada.kwargs["headers"]["X-Tenant-Slug"] == "demo"


# ---------------------------------------------------------------------------
# El invariante que faltaba: los argumentos de cada paso EXISTEN en su tool.
#
# Escribí `dest_dept` en un paso porque así se llama en el ENDPOINT; la tool que
# el paso encadena declara `dest_dept_code`. El procedimiento entero fallaba en
# su primer paso, y —lo peor— el modelo le habría anunciado a la persona un
# argumento inexistente. Lo cazó un test de ejecución; esto lo caza al arrancar.
# ---------------------------------------------------------------------------


def test_cada_paso_mapea_solo_argumentos_QUE_SU_TOOL_DECLARA() -> None:
    for proc in PROCEDIMIENTOS:
        for paso in proc.pasos:
            declarados = set(tool_de(paso).params_by_name)
            sobran = set(paso.args) - declarados
            assert not sobran, (
                f"{proc.name} → paso «{paso.etiqueta}»: {sorted(sobran)} no son "
                f"argumentos de la tool «{paso.tool}». Los nombres son los de la "
                "TOOL, no los del endpoint."
            )


def test_cada_paso_aporta_los_argumentos_OBLIGATORIOS_de_su_tool() -> None:
    """Un obligatorio sin mapear no falla al declarar: falla al ejecutar, y a
    mitad del trámite — con los pasos anteriores ya hechos y sin deshacer."""
    for proc in PROCEDIMIENTOS:
        for paso in proc.pasos:
            herramienta = tool_de(paso)
            obligatorios = {p.name for p in herramienta.params if p.required}
            faltan = obligatorios - set(paso.args)
            assert not faltan, (
                f"{proc.name} → paso «{paso.etiqueta}»: la tool «{paso.tool}» exige "
                f"{sorted(faltan)} y el paso no los mapea."
            )


def test_todo_argumento_del_procedimiento_SE_USA_en_algun_paso() -> None:
    """Un parámetro que se pide a la persona y no viaja a ningún sitio es una
    pregunta cuya respuesta se tira: el modelo la pedirá y no servirá para nada."""
    for proc in PROCEDIMIENTOS:
        usados = {
            origen[1:]
            for paso in proc.pasos
            for origen in paso.args.values()
            if isinstance(origen, str) and origen.startswith("@")
        }
        usados |= {paso.si_hay for paso in proc.pasos if paso.si_hay}
        declarados = {p.name for p in proc.params}
        huerfanos = declarados - usados
        assert not huerfanos, f"{proc.name}: {sorted(huerfanos)} se piden y no se usan."


@pytest.mark.asyncio
async def test_un_paso_que_no_puede_invocar_NO_se_ejecuta_desde_el_procedimiento():
    """El permiso agregado sirve para OFRECER el procedimiento, no para dar por
    bueno todo lo que contiene.

    Antes se comprobaba el permiso declarado por el procedimiento como un todo y
    sus pasos se despachaban después sin volver a mirar nada: un procedimiento
    con un permiso llano podía encadenar herramientas que exigen otro más alto, y
    quien lo ejecutara haría por esa vía lo que no puede hacer directamente.
    """
    from app.services import procedure_runner

    proc = PROCEDIMIENTOS[0]
    llamadas = []

    class _Cliente:
        async def request(self, *a, **k):
            llamadas.append(a)
            raise AssertionError("no debería haberse despachado ningún paso")

    parte = await procedure_runner.ejecutar(
        _Cliente(),
        proc,
        {},
        authorization="Bearer x",
        tenant_slug="demo",
        permissions=set(),   # sin ningún permiso
        is_root=False,
    )

    assert not llamadas, "se despachó un paso sin permiso"
    assert parte["completado"] is False
    estados = [p.get("estado") for p in parte.get("pasos", [])]
    assert "denegado" in estados or "omitido" in estados, parte


# ---------------------------------------------------------------------------
# AREA-11-04: cada paso lleva su propia Idempotency-Key (`<jti>:<n>`)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cada_paso_de_un_procedimiento_envia_su_propia_idempotency_key() -> None:
    """El `jti` de la propuesta correlaciona el asiento con lo aprobado; en un
    procedimiento hay varios actos, y la misma clave en dos pasos los haría pasar
    por uno el día que un servicio la honre."""
    proc = get_procedimiento("tramitar_peticion_ciudadana")
    cliente = _cliente(
        httpx.Response(201, json={"id": RAD_ID, "tracking_number": "2026-DEMO-E-000001"}),
        httpx.Response(201, json={"step_id": "s-1"}),
    )

    parte = await procedure_runner.ejecutar(
        cliente,
        proc,
        {"subject": "P", "sender_name": "Ana", "dest_dept_code": 902, "to_dept_code": 902},
        authorization="Bearer x",
        tenant_slug="demo",
        permissions=set(),
        is_root=True,
        idempotency_key="jti-1",
    )

    assert parte["completado"] is True
    claves = [
        c.kwargs["headers"].get("Idempotency-Key") for c in cliente.request.await_args_list
    ]
    assert claves == ["jti-1:1", "jti-1:2"], claves
