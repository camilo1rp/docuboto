# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El barrido de vencimientos y su alerta (E04, RF-TRD-08 + RF-TRD-06 R.4.8).

Lo que se prueba es lo que falla en silencio:

* **El aviso se compone POR DESTINATARIO**, con el recuento recortado por SU
  clearance. Decirle «tienes 14 vencimientos» a quien solo alcanza 9 le está
  diciendo que existen 5 expedientes clasificados. Es la trampa anotada por
  adelantado en el plan, y la lección de la cola de anulaciones reapareciendo
  en un sitio nuevo: allí el punto delicado era el total de la lista, aquí es
  el número del correo.
* **A quien no le toca nada, no se le escribe.** Que otro con más clearance sí
  tenga trabajo no es asunto suyo, y decírselo sería la fuga.
* **El correo no lleva archivo**: ni números de radicado, ni asuntos, ni series.
  Lo que hay que mover es a la persona hasta la pantalla.
* **El job no ejecuta ninguna disposición.** Cuenta y avisa.
* Los fallos quedan aislados por tenant y por destinatario.
"""

from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.jobs import disposicion_alerta


class _FakeRepo:
    """Repositorio de mentira con recuentos POR CLEARANCE — el detalle que
    hace verificable la trampa: si el job pidiera un recuento global, este
    doble no podría distinguirlo y el test pasaría igual. Por eso guarda con
    qué clearance se le preguntó."""

    def __init__(self, destinatarios, por_clearance):
        self._destinatarios = destinatarios
        self._por_clearance = por_clearance
        self.preguntas = []

    async def list_destinatarios_disposicion(self):
        return self._destinatarios

    async def contar_vencimientos_sin_decidir(self, *, max_clearance, dias):
        self.preguntas.append((max_clearance, dias))
        return self._por_clearance.get(max_clearance, 0)


@contextmanager
def _entorno(repo, *, slugs=("demo",), enviar=None):
    """Parchea el mundo alrededor del job: tenants, conexión y repositorio.
    Devuelve el doble de `enviar_aviso` para inspeccionarlo."""

    class _Conn:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    envios = enviar if enviar is not None else AsyncMock()
    with ExitStack() as stack:
        stack.enter_context(patch.object(disposicion_alerta, "get_pool", return_value=object()))
        stack.enter_context(
            patch.object(
                disposicion_alerta, "list_active_tenants", new=AsyncMock(return_value=list(slugs))
            )
        )
        stack.enter_context(
            patch.object(disposicion_alerta, "acquire_tenant", lambda slug: _Conn())
        )
        stack.enter_context(
            patch.object(disposicion_alerta, "ArchiveRepository", lambda conn, slug: repo)
        )
        stack.enter_context(patch.object(disposicion_alerta, "enviar_aviso", new=envios))
        yield envios


@pytest.mark.asyncio
async def test_cada_destinatario_recibe_su_propio_recuento() -> None:
    """DOS personas, DOS clearances, DOS números — y cada uno cierto para quien
    lo lee. Si el job preguntara una sola vez por tenant, uno de los dos
    correos estaría mintiendo."""
    repo = _FakeRepo(
        destinatarios=[
            {"id": 1, "email": "archivista@e.gov.co", "max_clearance": 1},
            {"id": 2, "email": "jefe@e.gov.co", "max_clearance": 3},
        ],
        por_clearance={1: 9, 3: 14},
    )
    with _entorno(repo) as envios:
        resumen = await disposicion_alerta.run_once()

    assert resumen["avisos"] == 2
    assert {c[0] for c in repo.preguntas} == {1, 3}
    cuerpos = {
        c.kwargs["recipient_email"]: c.kwargs["body"] for c in envios.await_args_list
    }
    assert "9 expedientes" in cuerpos["archivista@e.gov.co"]
    assert "14 expedientes" in cuerpos["jefe@e.gov.co"]
    # Y el de clearance corto NO ve el número del otro.
    assert "14" not in cuerpos["archivista@e.gov.co"]


@pytest.mark.asyncio
async def test_a_quien_no_le_toca_nada_no_se_le_escribe() -> None:
    repo = _FakeRepo(
        destinatarios=[
            {"id": 1, "email": "sinnada@e.gov.co", "max_clearance": 1},
            {"id": 2, "email": "jefe@e.gov.co", "max_clearance": 3},
        ],
        por_clearance={1: 0, 3: 5},
    )
    with _entorno(repo) as envios:
        await disposicion_alerta.run_once()

    destinos = [c.kwargs["recipient_email"] for c in envios.await_args_list]
    assert destinos == ["jefe@e.gov.co"]


@pytest.mark.asyncio
async def test_el_correo_no_lleva_datos_del_archivo() -> None:
    """Un correo sale de la institución y acaba en bandejas que el SGDEA no
    gobierna. Lo que se mueve es la persona hasta la pantalla."""
    repo = _FakeRepo(
        destinatarios=[{"id": 1, "email": "a@e.gov.co", "max_clearance": 3}],
        por_clearance={3: 3},
    )
    with _entorno(repo) as envios:
        await disposicion_alerta.run_once()

    cuerpo = envios.await_args.kwargs["body"]
    asunto = envios.await_args.kwargs["subject"]
    for prohibido in ("EXP-", "DEM-", "-E-0", "radicado"):
        assert prohibido not in cuerpo, (prohibido, cuerpo)
        assert prohibido not in asunto, (prohibido, asunto)
    # Sí dice a dónde ir, y que vencer no es eliminar.
    assert "Disposición vencida" in cuerpo
    assert "no significa eliminar" in cuerpo


@pytest.mark.asyncio
async def test_sin_usuarios_autorizados_no_se_avisa_a_nadie() -> None:
    """Una entidad que aún no repartió el permiso no es un error del job, y
    avisar a otro sería avisar a quien no puede actuar."""
    repo = _FakeRepo(destinatarios=[], por_clearance={})
    with _entorno(repo) as envios:
        resumen = await disposicion_alerta.run_once()

    assert resumen["tenants"] == 0
    envios.assert_not_awaited()


@pytest.mark.asyncio
async def test_un_correo_que_rebota_no_deja_sin_avisar_a_los_demas() -> None:
    repo = _FakeRepo(
        destinatarios=[
            {"id": 1, "email": "rebota@e.gov.co", "max_clearance": 3},
            {"id": 2, "email": "llega@e.gov.co", "max_clearance": 3},
        ],
        por_clearance={3: 4},
    )

    async def _enviar(*, recipient_email, **kw):
        if recipient_email == "rebota@e.gov.co":
            raise disposicion_alerta.NotificationDeliveryError("smtp caído")

    with _entorno(repo, enviar=_enviar):
        resumen = await disposicion_alerta.run_once()

    assert resumen["fallidos"] == 1
    assert resumen["avisos"] == 1


@pytest.mark.asyncio
async def test_un_tenant_roto_no_aborta_la_barrida() -> None:
    repo = _FakeRepo(
        destinatarios=[{"id": 1, "email": "a@e.gov.co", "max_clearance": 3}],
        por_clearance={3: 2},
    )
    llamadas = {"n": 0}

    class _ConnQueFalla:
        async def __aenter__(self):
            llamadas["n"] += 1
            if llamadas["n"] == 1:
                raise RuntimeError("postgres caído para este tenant")
            return object()

        async def __aexit__(self, *a):
            return False

    envios = AsyncMock()
    with (
        patch.object(disposicion_alerta, "get_pool", return_value=object()),
        patch.object(
            disposicion_alerta,
            "list_active_tenants",
            new=AsyncMock(return_value=["roto", "sano"]),
        ),
        patch.object(disposicion_alerta, "acquire_tenant", lambda slug: _ConnQueFalla()),
        patch.object(disposicion_alerta, "ArchiveRepository", lambda conn, slug: repo),
        patch.object(disposicion_alerta, "enviar_aviso", new=envios),
    ):
        resumen = await disposicion_alerta.run_once()

    assert resumen["avisos"] == 1  # el sano sí recibió


@pytest.mark.asyncio
async def test_el_job_no_ejecuta_ninguna_disposicion() -> None:
    """Guardián por FORMA: el job cuenta y avisa. Si algún día alguien le
    añadiera un camino que borra, transfiere o decide, esto se pone en rojo —
    un barrido que actuara solo es exactamente lo que la regla de negocio 4
    del RT de TRD prohíbe."""
    import inspect

    fuente = inspect.getsource(disposicion_alerta)
    codigo = "\n".join(
        linea for linea in fuente.splitlines() if not linea.lstrip().startswith("#")
    )
    # Se inspecciona el CÓDIGO sin comentarios: la primera versión de este
    # guardián casaba con su propio docstring, que era justo el que decía que
    # el job no elimina (2.ª vez que un censo se encuentra a sí mismo).
    cuerpo = codigo.split('"""', 2)[-1]
    for prohibido in ("decidir_disposicion", "registrar_decision", "DELETE", "transition_expediente"):
        assert prohibido not in cuerpo, prohibido
