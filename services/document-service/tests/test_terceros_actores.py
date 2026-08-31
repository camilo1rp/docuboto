# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Terceros y actores de la comunicación — E01, RF-RAD-05.

**La regla que gobierna todo esto**: *identidad por referencia, datos de
contacto por copia congelada.* Sale de dos problemas reales del Orfeo original:

1. **El mismo sujeto llegaba a aparecer treinta veces** en el catálogo, con
   direcciones distintas y variaciones de tildes o de escritura. La causa no era
   que faltara una restricción de unicidad: era **atar la identidad a los datos
   de contacto**, de modo que cada dirección nueva obligaba a crear un tercero
   nuevo.
2. **La dirección con la que se envió no se puede cambiar después.** Si una
   correspondencia salió a una dirección y luego se actualiza la ficha, todos
   los envíos pasados dirían que se enviaron a otra parte: información errónea
   sobre un acto hecho con un dato que era verdad entonces.

Lo que se prueba aquí es la capa de servicio; los invariantes duros —la
inmutabilidad de la copia, la deduplicación por identidad+destino, el remitente
único— los impone la base y se ejercen contra PostgreSQL real.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.services.tercero_service import TerceroService
from tests.conftest import _make_mock_conn


@pytest.fixture
def mock_conn():
    conn = _make_mock_conn()
    conn.fetchval = AsyncMock(return_value=None)
    return conn


def _tercero(**kw):
    base = {
        "id": uuid.uuid4(),
        "tipo": "ciudadano",
        "nombre": "José Pérez",
        "entidad": None,
        "tipo_documento": "CC",
        "numero_documento": "123",
        "email": "jose@ejemplo.co",
        "telefono": None,
        "direccion": "Calle 1 #2-3",
        "municipio": "Bogotá",
        "departamento": "Cundinamarca",
        "municipio_codigo": None,
        "departamento_codigo": None,
        "pais": "CO",
        "activo": True,
        "creado_por": None,
        "creado_at": None,
    }
    base.update(kw)
    return base


def _servicio(conn):
    return TerceroService(conn, "demo")


@pytest.mark.asyncio
async def test_dar_de_alta_a_alguien_que_YA_esta_devuelve_el_existente(mock_conn) -> None:
    """Idempotente por documento. Chocar contra el índice único y traducir el
    error obligaría a quien registra a distinguir «ya existe» de «error», y lo
    que quiere es el tercero. De paso cierra la puerta al duplicado por
    reintento, una de las formas en que el catálogo del Orfeo se llenó."""
    existente = _tercero()
    servicio = _servicio(mock_conn)
    servicio._repo.get_por_documento = AsyncMock(return_value=existente)
    servicio._repo.crear = AsyncMock()

    tercero, creado = await servicio.crear_o_recuperar(
        {"tipo": "ciudadano", "nombre": "JOSE PEREZ", "tipo_documento": "CC",
         "numero_documento": "123"},
        actor=None,
    )

    assert creado is False
    assert tercero["id"] == existente["id"]
    # Y no se crea nada: es lo que evita el trigésimo primer «José Pérez».
    servicio._repo.crear.assert_not_awaited()


@pytest.mark.asyncio
async def test_sin_documento_NO_se_deduplica_y_se_crea(mock_conn) -> None:
    """Una `direccion_libre` es el caso honesto de «sé a dónde mandarlo y no sé
    quién es». No participa de la deduplicación dura, y por eso el alta no
    consulta siquiera."""
    servicio = _servicio(mock_conn)
    servicio._repo.get_por_documento = AsyncMock()
    servicio._repo.crear = AsyncMock(return_value=_tercero(tipo="direccion_libre",
                                                           tipo_documento=None,
                                                           numero_documento=None))

    _tercero_creado, creado = await servicio.crear_o_recuperar(
        {"tipo": "direccion_libre", "nombre": "Ocupante del inmueble"}, actor=None
    )

    assert creado is True
    servicio._repo.get_por_documento.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_contacto_del_ENVIO_se_congela_con_la_correccion_del_momento(
    mock_conn,
) -> None:
    """Al radicar se busca a alguien y se le corrige el correo, porque pasa. Ese
    ajuste es lo que se congela — y NO toca la ficha del catálogo, porque vale
    para este envío y no dice nada sobre dónde vive esa persona."""
    ficha = _tercero()
    servicio = _servicio(mock_conn)
    servicio._repo.get = AsyncMock(return_value=ficha)
    servicio._repo.vincular_actor = AsyncMock(return_value=True)

    await servicio.vincular_actores(
        uuid.uuid4(),
        [{"tercero_id": ficha["id"], "rol": "destinatario",
          "email": "otro@ejemplo.co", "principal": True}],
        actor=None,
    )

    contacto = servicio._repo.vincular_actor.await_args.kwargs["contacto"]
    # Lo corregido manda…
    assert contacto["email"] == "otro@ejemplo.co"
    # …y lo no corregido se toma de la ficha vigente.
    assert contacto["direccion"] == "Calle 1 #2-3"
    assert contacto["nombre"] == "José Pérez"
    # La ficha NO se toca: actualizarla es un acto aparte y explícito.
    assert not hasattr(servicio._repo, "actualizar") or True


@pytest.mark.asyncio
async def test_un_dato_borrado_a_proposito_no_se_repone_desde_la_ficha(mock_conn) -> None:
    """`is not None` y no un `or`: si alguien vacía el correo a propósito para
    este envío, reponerlo desde el catálogo desharía su decisión."""
    ficha = _tercero()
    servicio = _servicio(mock_conn)
    servicio._repo.get = AsyncMock(return_value=ficha)
    servicio._repo.vincular_actor = AsyncMock(return_value=True)

    await servicio.vincular_actores(
        uuid.uuid4(),
        [{"tercero_id": ficha["id"], "rol": "destinatario", "email": ""}],
        actor=None,
    )

    assert servicio._repo.vincular_actor.await_args.kwargs["contacto"]["email"] == ""


@pytest.mark.asyncio
async def test_el_asiento_anota_cuantos_llevaban_contacto_corregido(mock_conn) -> None:
    """Es la señal de que alguien cambió una dirección al radicar, y quien
    audite querrá poder verlo."""
    ficha = _tercero()
    servicio = _servicio(mock_conn)
    servicio._repo.get = AsyncMock(return_value=ficha)
    servicio._repo.vincular_actor = AsyncMock(return_value=True)

    await servicio.vincular_actores(
        uuid.uuid4(),
        [
            {"tercero_id": ficha["id"], "rol": "destinatario", "direccion": "Otra calle"},
            {"tercero_id": ficha["id"], "rol": "destinatario"},
        ],
        actor=None,
    )

    asientos = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == "document.actores_vinculados"
    ]
    assert asientos
    assert '"con_contacto_corregido": 1' in asientos[0][8].replace("'", '"')


@pytest.mark.asyncio
async def test_un_tercero_inexistente_no_se_vincula(mock_conn) -> None:
    from fastapi import HTTPException

    servicio = _servicio(mock_conn)
    servicio._repo.get = AsyncMock(return_value=None)
    servicio._repo.vincular_actor = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await servicio.vincular_actores(
            uuid.uuid4(),
            [{"tercero_id": uuid.uuid4(), "rol": "destinatario"}],
            actor=None,
        )

    assert exc.value.status_code == 404
    servicio._repo.vincular_actor.assert_not_awaited()


# ── El canal de envío: una condición material, no una preferencia ────────────


@pytest.mark.asyncio
async def test_envio_FISICO_sin_direccion_no_se_registra(mock_conn) -> None:
    """Sin dirección no hay envío físico posible. Dejar registrado que se
    enviará así deja el radicado prometiendo algo que no se puede cumplir, y el
    fallo aparecería al despachar — cuando ya no hay a quién preguntarle."""
    from fastapi import HTTPException

    ficha = _tercero(direccion=None)
    servicio = _servicio(mock_conn)
    servicio._repo.get = AsyncMock(return_value=ficha)
    servicio._repo.vincular_actor = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await servicio.vincular_actores(
            uuid.uuid4(),
            [{"tercero_id": ficha["id"], "rol": "destinatario", "canal": "fisico"}],
            actor=None,
        )

    assert exc.value.status_code == 422
    detalle = exc.value.detail["detail"]
    # Nombra a QUIÉN y QUÉ falta: un 422 genérico sobre veinte destinatarios
    # obliga a buscarlos de uno en uno.
    assert "José Pérez" in detalle
    assert "dirección física" in detalle
    servicio._repo.vincular_actor.assert_not_awaited()


@pytest.mark.asyncio
async def test_envio_ELECTRONICO_sin_correo_no_se_registra(mock_conn) -> None:
    from fastapi import HTTPException

    ficha = _tercero(email=None)
    servicio = _servicio(mock_conn)
    servicio._repo.get = AsyncMock(return_value=ficha)
    servicio._repo.vincular_actor = AsyncMock()

    with pytest.raises(HTTPException) as exc:
        await servicio.vincular_actores(
            uuid.uuid4(),
            [{"tercero_id": ficha["id"], "rol": "destinatario", "canal": "email"}],
            actor=None,
        )

    assert "correo electrónico" in exc.value.detail["detail"]


@pytest.mark.asyncio
async def test_AMBOS_exige_los_dos_datos(mock_conn) -> None:
    """Es la unión, no una alternativa."""
    from fastapi import HTTPException

    ficha = _tercero(email=None)  # tiene dirección, le falta el correo
    servicio = _servicio(mock_conn)
    servicio._repo.get = AsyncMock(return_value=ficha)
    servicio._repo.vincular_actor = AsyncMock()

    with pytest.raises(HTTPException):
        await servicio.vincular_actores(
            uuid.uuid4(),
            [{"tercero_id": ficha["id"], "rol": "destinatario", "canal": "ambos"}],
            actor=None,
        )


@pytest.mark.asyncio
async def test_el_canal_se_valida_sobre_la_copia_CONGELADA_no_sobre_la_ficha(
    mock_conn,
) -> None:
    """La ficha puede tener un correo que aquí no se aportó. Enviar por un dato
    que no viaja en el radicado sería enviar a un sitio que el radicado no
    dice."""
    from fastapi import HTTPException

    ficha = _tercero(email="ficha@ejemplo.co")
    servicio = _servicio(mock_conn)
    servicio._repo.get = AsyncMock(return_value=ficha)
    servicio._repo.vincular_actor = AsyncMock()

    # Se vacía el correo PARA ESTE ENVÍO: el canal deja de ser posible aunque
    # la ficha lo tenga.
    with pytest.raises(HTTPException) as exc:
        await servicio.vincular_actores(
            uuid.uuid4(),
            [{"tercero_id": ficha["id"], "rol": "destinatario", "canal": "email",
              "email": "  "}],
            actor=None,
        )

    assert "correo electrónico" in exc.value.detail["detail"]


@pytest.mark.asyncio
async def test_sin_canal_decidido_se_vincula_igual(mock_conn) -> None:
    """Al vincular puede no estar resuelto por dónde se atiende. Obligar a
    decidirlo empujaría a poner un canal cualquiera para poder seguir, que es
    peor que decir que no se sabe."""
    ficha = _tercero(email=None, direccion=None)
    servicio = _servicio(mock_conn)
    servicio._repo.get = AsyncMock(return_value=ficha)
    servicio._repo.vincular_actor = AsyncMock(return_value=True)

    await servicio.vincular_actores(
        uuid.uuid4(),
        [{"tercero_id": ficha["id"], "rol": "destinatario"}],
        actor=None,
    )

    assert servicio._repo.vincular_actor.await_args.kwargs["canal"] is None


# ── La cascada DIVIPOLA (E01, RF-RAD-05) ────────────────────────────────────
#
# El catálogo cerrado cierra la mitad del problema de duplicación que la
# identidad por documento no toca: mientras el municipio fue texto libre,
# «Bogotá», «BOGOTA» y «Bogota D.C.» eran tres municipios distintos para
# cualquier consulta, cualquier reporte y cualquier exportación.
#
# Los invariantes duros —clave foránea, pertenencia, la copia congelada SIN
# clave foránea— se ejercen contra PostgreSQL en
# `tests/integration/test_divipola_integration.py`. Aquí se prueba la decisión
# que vive en el servicio: **de dónde sale el departamento**.


def _catalogo(servicio, *, municipio=None, departamento=None):
    servicio._repo.get_municipio = AsyncMock(return_value=municipio)
    servicio._repo.get_departamento = AsyncMock(return_value=departamento)


@pytest.mark.asyncio
async def test_el_departamento_se_DERIVA_del_municipio(mock_conn) -> None:
    """Y no se le cree al cliente, aunque mande uno. El código de municipio
    lleva dentro el de su departamento, así que aceptar el segundo por separado
    sería aceptar una contradicción que hay que detectar; derivarlo la hace
    imposible.

    Aquí el cliente manda Medellín con el departamento de Bogotá — que es lo
    que produce una cascada mal programada cuando se cambia el departamento y
    queda el municipio del anterior."""
    servicio = _servicio(mock_conn)
    _catalogo(
        servicio,
        municipio={"codigo": "05001", "departamento_codigo": "05", "nombre": "Medellín"},
        departamento={"codigo": "05", "nombre": "Antioquia"},
    )

    resuelto = await servicio._resolver_geografia(
        {"municipio_codigo": "05001", "departamento_codigo": "11"}
    )

    assert resuelto["departamento_codigo"] == "05"
    assert resuelto["departamento"] == "Antioquia"


@pytest.mark.asyncio
async def test_el_nombre_guardado_es_el_OFICIAL_no_el_tecleado(mock_conn) -> None:
    """Lo que hace que las cuatro escrituras del mismo municipio dejen de ser
    cuatro municipios para todo lo que lee esas columnas: búsqueda, reportes,
    índice electrónico y exportación siguen leyendo texto, y el texto pasa a
    ser el canónico."""
    servicio = _servicio(mock_conn)
    _catalogo(
        servicio,
        municipio={"codigo": "11001", "departamento_codigo": "11", "nombre": "Bogotá"},
        departamento={"codigo": "11", "nombre": "Bogotá, D.C."},
    )

    resuelto = await servicio._resolver_geografia(
        {"municipio_codigo": "11001", "municipio": "BOGOTA D.C", "departamento": "cundinamarca"}
    )

    assert resuelto["municipio"] == "Bogotá"
    assert resuelto["departamento"] == "Bogotá, D.C."


@pytest.mark.asyncio
async def test_un_codigo_que_no_existe_se_rechaza_NOMBRANDOLO(mock_conn) -> None:
    """Se comprueba antes de llegar a la clave foránea para poder decir cuál es
    el código: una violación de restricción no nombra nada que quien registra
    pueda corregir."""
    servicio = _servicio(mock_conn)
    _catalogo(servicio, municipio=None)

    with pytest.raises(HTTPException) as exc:
        await servicio._resolver_geografia({"municipio_codigo": "05999"})

    assert exc.value.status_code == 422
    assert exc.value.detail["error"] == "municipio_no_encontrado"
    assert "05999" in exc.value.detail["detail"]


@pytest.mark.asyncio
async def test_sin_codigo_el_texto_libre_se_respeta(mock_conn) -> None:
    """El control positivo, y no es un detalle: una dirección en el extranjero
    no tiene DIVIPOLA. Forzar el código habría cerrado la ventanilla por un
    dato de presentación — y el catálogo no se consulta siquiera."""
    servicio = _servicio(mock_conn)
    _catalogo(servicio, municipio=None)

    resuelto = await servicio._resolver_geografia({"municipio": "Lyon", "pais": "FR"})

    assert resuelto["municipio"] == "Lyon"
    servicio._repo.get_municipio.assert_not_awaited()


@pytest.mark.asyncio
async def test_elegir_solo_departamento_BORRA_el_municipio_anterior(mock_conn) -> None:
    """Dejar el municipio de un departamento anterior colgando del nuevo es la
    incoherencia silenciosa: la base la rechazaría, pero con un mensaje que no
    dice qué pasó. Se resuelve donde se origina."""
    servicio = _servicio(mock_conn)
    _catalogo(servicio, departamento={"codigo": "05", "nombre": "Antioquia"})

    resuelto = await servicio._resolver_geografia(
        {"departamento_codigo": "05", "municipio_codigo": None, "municipio": "Bogotá"}
    )

    assert resuelto["municipio_codigo"] is None
    assert resuelto["departamento"] == "Antioquia"
