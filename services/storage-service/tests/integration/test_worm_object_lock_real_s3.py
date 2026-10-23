# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Conformidad WORM contra un almacén S3 REAL — condición de adopción de un motor.

Por qué existe este fichero, en una frase: hasta ahora el sistema **ordenaba** la
retención y **nunca volvía a mirarla**. `set_object_retention` aparece en tres
ficheros y `get_object_retention` en **ninguno**; los 76 usos de COMPLIANCE y los
122 de legal hold se prueban contra un mock, es decir, contra una afirmación
nuestra sobre MinIO — no contra MinIO.

Eso era tolerable mientras el motor fuera uno solo y de reputación conocida. Dejó
de serlo: MinIO archivó su proyecto libre y retiró sus imágenes de todos los
registros, así que hay que cambiar de motor, y los candidatos fallan de maneras
que un mock no puede ver. Los tres modos, por gravedad:

1. **Rechaza la orden** — inocuo: el sistema falla ruidosamente y nadie firma nada.
2. **Acepta y no cumple** — catastrófico. Le pasó a SeaweedFS: `PutObjectRetention`
   con `Mode=COMPLIANCE` devolvía 200 y el `DeleteObject` seguía funcionando. El
   acta de transferencia, con su huella y su firma XAdES, **afirma** entonces una
   protección que no existe: un documento criptográficamente impecable y falso en
   su contenido. Es peor que no tener WORM, porque convierte la ausencia de
   garantía en una afirmación firmada.
3. **Cumple pero reporta que no cumple** — no destruye nada, rompe la auditoría:
   quien no haga la prueba de borrado concluirá no conformidad, o «repar rá» lo que
   no estaba roto.

Marco normativo de lo que se mide: Acuerdo AGN 001/2024, art. 4.3.2.4 P1 — los
expedientes electrónicos no pueden ser «modificados, eliminados o reemplazados».
La firma y la huella cubren *modificados* y *reemplazados*; **ninguna propiedad
criptográfica impide un borrado**, así que el verbo *eliminados* solo lo cierra el
almacén. Esto es lo que aquí se comprueba.

CÓMO SE USA
-----------
Doble gate, y el segundo es propio: `RUN_INTEGRATION=1` (el del tier) **y**
`RUN_S3_WORM=1`. El tier de integración solo exige Postgres; esto exige además un
almacén S3 en pie, y **borra objetos a propósito**.

    docker compose run --rm \\
      -e RUN_INTEGRATION=1 -e RUN_S3_WORM=1 \\
      -e MINIO_ENDPOINT=minio:9000 \\
      storage-service pytest tests/integration/test_worm_object_lock_real_s3.py -q

**Se corre primero contra MinIO para fijar línea base.** Si no pasa contra MinIO,
el test está mal, no el motor.
"""

import hashlib
import io
import os
import urllib.parse
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from miniopy_async import Minio, S3Error  # type: ignore[attr-defined]
from miniopy_async.objectlockconfig import COMPLIANCE  # type: ignore[attr-defined]
from miniopy_async.retention import Retention  # type: ignore[attr-defined]

pytestmark = pytest.mark.asyncio

# Allowlist fail-closed del HOST del endpoint, espejo exacto de la del DSN en
# `conftest.py`: este fichero BORRA objetos a propósito, así que un
# `MINIO_ENDPOINT` heredado de staging apuntaría la batería contra el almacén de
# una entidad real. Se abre con `INTEGRATION_S3_HOSTS` solo si el host es de
# verdad desechable.
_DEFAULT_S3_HOSTS = frozenset({"minio", "localhost", "127.0.0.1", "rustfs", "seaweedfs"})


def _allowed_s3_hosts() -> frozenset[str]:
    override = os.environ.get("INTEGRATION_S3_HOSTS")
    if override:
        return frozenset(h.strip() for h in override.split(",") if h.strip())
    return _DEFAULT_S3_HOSTS


def _endpoint() -> str:
    endpoint = os.environ.get("MINIO_ENDPOINT", "")
    if not endpoint:
        raise RuntimeError(
            "MINIO_ENDPOINT no está definida: esta batería exige un almacén S3 real. "
            "No tiene valor por defecto a propósito."
        )
    host = urllib.parse.urlparse(f"//{endpoint}").hostname
    permitidos = _allowed_s3_hosts()
    if host not in permitidos:
        raise RuntimeError(
            f"MINIO_ENDPOINT apunta al host {host!r}, fuera de la allowlist "
            f"{sorted(permitidos)}: esta batería borra objetos a propósito y jamás "
            "debe correr contra un almacén que no sea desechable. Si lo es, exporta "
            "INTEGRATION_S3_HOSTS."
        )
    return endpoint


@pytest.fixture(scope="module")
def gate():
    if os.environ.get("RUN_S3_WORM") != "1":
        if os.environ.get("RUN_INTEGRATION") == "1" and os.environ.get("CI"):
            # AREA-13-01: en CI, un tier de integración que llega aquí sin
            # `RUN_S3_WORM` NO puede saltarse en silencio: la batería WORM contra
            # S3 real es la condición de adopción del motor y salía `skipped` en
            # verde. El job `storage-worm-real` es quien la corre (con RustFS fijado
            # por digest); `test-integration` la excluye con `--ignore`.
            pytest.fail(
                "CI con RUN_INTEGRATION=1 pero sin RUN_S3_WORM=1: la batería WORM "
                "real no puede saltarse en CI (job `storage-worm-real`)."
            )
        pytest.skip("exige RUN_S3_WORM=1 y un almacén S3 real (ver el docstring)")


@pytest.fixture
async def cliente(gate):
    """Cliente con la credencial RAÍZ del almacén, y eso es deliberado.

    La diferencia entre GOVERNANCE y COMPLIANCE es precisamente quién puede
    levantar la retención: en GOVERNANCE, quien tenga privilegios; en COMPLIANCE,
    nadie — ni el root. Si estas pruebas usaran una credencial limitada, un motor
    con solo GOVERNANCE las pasaría todas y la batería no distinguiría los dos
    modos, que es justo lo que la norma obliga a distinguir (ver el dictamen del
    Ac. AGN 001/2024 art. 4.3.2.4 P1 en `docs/es/`).
    """
    c = Minio(
        _endpoint(),
        access_key=os.environ.get("MINIO_ACCESS_KEY", "orpycamcp"),
        secret_key=os.environ.get("MINIO_SECRET_KEY", "orpycamcp_dev"),
        secure=os.environ.get("MINIO_SECURE", "false").lower() == "true",
        # La región es configurable porque no todos los motores usan la de AWS:
        # sin esto, un motor con región propia rechaza la firma con
        # `AuthorizationHeaderMalformed` y la batería parecería reprobarlo por su
        # WORM cuando en realidad no llegó ni a probarlo.
        region=os.environ.get("MINIO_REGION") or None,
    )
    try:
        yield c
    finally:
        # Cerrar la sesión aiohttp al terminar. Sin esto la corrida denuncia
        # sesiones y conectores abiertos al recolectar basura, y ese ruido en una
        # batería de conformidad es caro: es justo donde hay que poder leer un
        # fallo real de un motor sin buscarlo entre trazas propias.
        #
        # MEDIDO contra RustFS 1.0.0 en pie: la corrida pasa de 6 avisos a **0**.
        #
        # La primera medición dijo «baja de 6 a 3» y era falsa: se tomó sobre una
        # imagen construida ANTES de este `finally`, así que midió el código
        # anterior y le atribuyó el residuo a `miniopy_async`. Es la misma trampa
        # del contenedor más viejo que su imagen, esta vez estropeando la medida
        # en lugar del resultado. Tras cada cambio en tests, `docker compose
        # build` antes de creer un número.
        await c.close_session()


def _nombre(prefijo: str) -> str:
    return f"worm-{prefijo}-{uuid.uuid4().hex[:10]}"


async def _bucket_worm(cliente) -> str:
    bucket = _nombre("lock")
    await cliente.make_bucket(bucket, object_lock=True)
    return bucket


async def _poner(cliente, bucket: str, datos: bytes, **kwargs) -> tuple[str, str]:
    clave = "expediente/indice.xml"
    res = await cliente.put_object(
        bucket, clave, io.BytesIO(datos), length=len(datos), **kwargs
    )
    return clave, res.version_id


# ---------------------------------------------------------------------------
# 1. El bucket nace bloqueado — y el control NEGATIVO, que es el que importa
# ---------------------------------------------------------------------------


async def test_el_bucket_nace_bloqueado_y_el_guard_distingue_el_que_no(cliente):
    """`ensure_object_lock_bucket` decide por EXCEPCIÓN, así que hay que probarlo.

    Su caso (c) —«existe SIN Object Lock» → abortar— depende por completo de que
    `get_object_lock_config` LANCE `S3Error` sobre un bucket sin lock. Si el motor
    responde 200 con una configuración vacía, esa función aprueba como WORM un
    bucket que no lo es, **en silencio**: es el fail-open exacto que su docstring
    dice estar evitando.
    """
    con_lock = await _bucket_worm(cliente)
    config = await cliente.get_object_lock_config(con_lock)
    assert config is not None, "el motor no informa la configuración de object lock"

    # CONTROL NEGATIVO: sin esto, la prueba de arriba pasa en un motor que
    # responde lo mismo para todos los buckets y el guard queda ciego.
    sin_lock = _nombre("plano")
    await cliente.make_bucket(sin_lock)
    with pytest.raises(S3Error):
        await cliente.get_object_lock_config(sin_lock)


# ---------------------------------------------------------------------------
# 2. La mutación central: borrar lo retenido DEBE fallar
# ---------------------------------------------------------------------------


async def test_la_version_retenida_no_se_puede_borrar_ni_por_root(cliente):
    """El verbo «eliminados» del art. 4.3.2.4 P1, comprobado por mutación.

    Se verifica leyendo los BYTES después del intento, no el código de error: un
    motor podría responder 403 y haber borrado igual, y entonces la garantía se
    daría por buena sobre un objeto que ya no está.
    """
    bucket = await _bucket_worm(cliente)
    datos = b"indice electronico firmado"
    huella = hashlib.sha256(datos).hexdigest()
    retencion = Retention(COMPLIANCE, datetime.now(tz=UTC) + timedelta(days=2))
    clave, version = await _poner(cliente, bucket, datos, retention=retencion)
    assert version, "sin version_id no hay WORM: la protección se ancla a una versión"

    # Borrar la CLAVE deja marca de borrado; la versión sigue recuperable.
    await cliente.remove_object(bucket, clave)

    # Borrar la VERSIÓN es lo que el almacén tiene que impedir. Es la variante que
    # SeaweedFS dejaba pasar devolviendo 200. Y se intenta con la credencial RAÍZ
    # (ver la fixture): que falle ante root es la definición de COMPLIANCE.
    with pytest.raises(S3Error):
        await cliente.remove_object(bucket, clave, version_id=version)

    respuesta = await cliente.get_object(bucket, clave, version_id=version)
    leido = await respuesta.read()
    assert hashlib.sha256(leido).hexdigest() == huella, (
        "el acta de transferencia afirmaría una protección que el almacén no aplica: "
        "la versión retenida cambió o desapareció"
    )


# ---------------------------------------------------------------------------
# 3. No acortar, sí extender
# ---------------------------------------------------------------------------


async def test_la_retencion_se_extiende_pero_no_se_acorta(cliente):
    """Lo que el código AFIRMA en un comentario y nada comprobaba.

    `preservation_worm_service.py` dice que en COMPLIANCE la retención «solo puede
    extenderse». Si el motor admitiera acortarla, la renovación de series de
    conservación total sería una puerta para liberar lo retenido.
    """
    bucket = await _bucket_worm(cliente)
    hasta = datetime.now(tz=UTC) + timedelta(days=5)
    clave, version = await _poner(
        cliente, bucket, b"aip", retention=Retention(COMPLIANCE, hasta)
    )

    with pytest.raises(S3Error):
        await cliente.set_object_retention(
            bucket, clave, Retention(COMPLIANCE, hasta - timedelta(days=2)),
            version_id=version,
        )

    nuevo = hasta + timedelta(days=30)
    await cliente.set_object_retention(
        bucket, clave, Retention(COMPLIANCE, nuevo), version_id=version
    )
    leida = await cliente.get_object_retention(bucket, clave, version_id=version)
    assert leida.retain_until_date.date() == nuevo.date(), (
        "el motor aceptó extender la retención y no la guardó: el sistema creería "
        "protegido hasta una fecha que el almacén no conoce"
    )


# ---------------------------------------------------------------------------
# 4. Legal hold: 122 usos en el código y ninguna evidencia de que congele algo
# ---------------------------------------------------------------------------


async def test_el_legal_hold_congela_aunque_la_retencion_haya_vencido(cliente):
    """El caso que el legal hold existe para cubrir.

    Una retención TRD vencida deja de proteger; el legal hold es lo que mantiene
    el documento intocable mientras haya un litigio o una investigación. Si no
    congela, el sistema ofrece una figura jurídica que no aplica nada.
    """
    bucket = await _bucket_worm(cliente)
    clave, version = await _poner(cliente, bucket, b"litigio", legal_hold=True)

    activo = await cliente.is_object_legal_hold_enabled(bucket, clave, version_id=version)
    assert activo, "el motor aceptó el legal hold y lo reporta como inactivo"

    with pytest.raises(S3Error):
        await cliente.remove_object(bucket, clave, version_id=version)

    # CONTROL POSITIVO de la otra dirección: liberado el hold, SÍ se puede borrar.
    # Sin esto, un motor que rechaza todos los borrados pasaría la prueba sin
    # tener legal hold alguno.
    await cliente.disable_object_legal_hold(bucket, clave, version_id=version)
    await cliente.remove_object(bucket, clave, version_id=version)


# ---------------------------------------------------------------------------
# 5. El asiento contra la realidad (modo de fallo 3)
# ---------------------------------------------------------------------------


async def test_lo_que_el_almacen_reporta_coincide_con_lo_que_se_asienta(cliente):
    """El motor que protege y dice que no protege rompe la auditoría.

    `preservacion_worm_objeto` guarda `retention_mode` y `retain_until`. Si el
    almacén reporta otra cosa, el asiento de preservación describe un estado que no
    es el real — y PREMIS exige que el evento describa lo que de verdad ocurrió.
    """
    bucket = await _bucket_worm(cliente)
    hasta = datetime.now(tz=UTC) + timedelta(days=3)
    clave, version = await _poner(
        cliente, bucket, b"acta", retention=Retention(COMPLIANCE, hasta)
    )

    leida = await cliente.get_object_retention(bucket, clave, version_id=version)
    assert leida is not None, (
        "el almacén no devuelve la retención que acaba de aceptar: el asiento de "
        "preservación no se puede verificar contra la realidad"
    )
    assert leida.mode == COMPLIANCE, f"modo reportado {leida.mode!r}, se fijó COMPLIANCE"
    assert leida.retain_until_date.date() == hasta.date()


# ---------------------------------------------------------------------------
# 6. La réplica hereda el candado — o no es una segunda copia de preservación
# ---------------------------------------------------------------------------


async def test_la_copia_de_respaldo_hereda_la_proteccion(cliente):
    """Una réplica borrable no es una copia de preservación.

    `aip_replica_service.py` copia con `retention` y `legal_hold`, y hoy nadie
    comprueba que el destino quede protegido. Si no hereda, la mitigación «tenemos
    N copias» es una copia protegida y N-1 borrables.
    """
    from miniopy_async.commonconfig import CopySource  # type: ignore[attr-defined]

    origen = await _bucket_worm(cliente)
    destino = await _bucket_worm(cliente)
    hasta = datetime.now(tz=UTC) + timedelta(days=2)
    clave, _ = await _poner(cliente, origen, b"replica", retention=Retention(COMPLIANCE, hasta))

    resultado = await cliente.copy_object(
        destino, clave, CopySource(origen, clave),
        retention=Retention(COMPLIANCE, hasta),
    )
    version_destino = resultado.version_id
    assert version_destino, "la copia no quedó versionada: no puede estar bajo WORM"

    with pytest.raises(S3Error):
        await cliente.remove_object(destino, clave, version_id=version_destino)


# ---------------------------------------------------------------------------
# 7. Re-proteger una clave ya bloqueada (el defecto que tuvo RustFS)
# ---------------------------------------------------------------------------


async def test_se_puede_escribir_una_version_nueva_sobre_una_clave_bloqueada(cliente):
    """El índice y el AIP se re-protegen: la clave se reescribe con versión nueva.

    RustFS tuvo justo este defecto (#3174): con una versión bajo COMPLIANCE, el
    `put_object` sobre la misma clave fallaba. Eso no es «más seguro»: impide
    sellar la versión siguiente del índice, que es una operación legítima y
    frecuente.
    """
    bucket = await _bucket_worm(cliente)
    hasta = datetime.now(tz=UTC) + timedelta(days=2)
    clave, primera = await _poner(
        cliente, bucket, b"indice v1", retention=Retention(COMPLIANCE, hasta)
    )

    _, segunda = await _poner(
        cliente, bucket, b"indice v2", retention=Retention(COMPLIANCE, hasta)
    )
    assert segunda and segunda != primera, (
        "no se pudo sellar una versión nueva sobre una clave ya retenida: el índice "
        "no se podría re-proteger al cerrar el expediente"
    )

    # Y la primera sigue intacta, que es el punto del versionado.
    respuesta = await cliente.get_object(bucket, clave, version_id=primera)
    assert await respuesta.read() == b"indice v1"


# ---------------------------------------------------------------------------
# 8. C-5/C7 (ADR-043): hold primero, retención DESPUÉS — el régimen (c) del fondo vencido
# ---------------------------------------------------------------------------


async def test_un_objeto_solo_con_hold_acepta_una_retencion_posterior_y_sigue_retenido(cliente):
    """El supuesto del que depende `fijar-retencion-internal`: un objeto escrito SOLO con legal
    hold (sin retención) admite que se le fije COMPLIANCE por primera vez más tarde, y al quitar el
    hold sigue siendo imborrable por la retención.

    Si el motor rechazara fijar retención sobre una versión que ya tiene hold, la valoración CT de
    un fondo acumulado vencido no podría proteger nada: el hold se levantaría y el objeto quedaría
    sin ninguna garantía (el caso catastrófico «acepta y no cumple» en su variante más sutil)."""
    bucket = await _bucket_worm(cliente)
    clave, version = await _poner(cliente, bucket, b"fondo acumulado vencido", legal_hold=True)
    try:
        previa = await cliente.get_object_retention(bucket, clave, version_id=version)
    except (S3Error, ValueError):
        # algunos motores responden NoSuchObjectLockConfiguration en vez de vacío; RustFS
        # contesta 200 sin <Mode> y el cliente lo levanta como ValueError
        previa = None
    assert previa is None, "el objeto no debería tener retención: solo se escribió con hold"

    hasta = datetime.now(tz=UTC) + timedelta(days=3)
    await cliente.set_object_retention(
        bucket, clave, Retention(COMPLIANCE, hasta), version_id=version
    )
    leida = await cliente.get_object_retention(bucket, clave, version_id=version)
    assert leida is not None and leida.mode == COMPLIANCE
    assert leida.retain_until_date.date() == hasta.date()

    # Se quita el hold (levantar la valoración): la retención sola tiene que bastar.
    await cliente.disable_object_legal_hold(bucket, clave, version_id=version)
    with pytest.raises(S3Error):
        await cliente.remove_object(bucket, clave, version_id=version)


async def test_sin_retencion_y_sin_hold_el_objeto_se_borra_control_negativo(cliente):
    """CONTROL NEGATIVO del anterior (régimen (c), valoración E/S/M): un objeto escrito solo con
    hold y al que NUNCA se le fijó retención queda borrable en cuanto se levanta el hold. Sin esta
    prueba, un motor que no borra nada pasaría la anterior sin tener ninguna garantía; con ella se
    ve que lo que protege al objeto entre medias es SOLO el hold."""
    bucket = await _bucket_worm(cliente)
    clave, version = await _poner(cliente, bucket, b"valoracion E", legal_hold=True)

    with pytest.raises(S3Error):
        await cliente.remove_object(bucket, clave, version_id=version)  # el hold lo protege

    await cliente.disable_object_legal_hold(bucket, clave, version_id=version)
    await cliente.remove_object(bucket, clave, version_id=version)  # ya sin nada: se borra
