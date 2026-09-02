# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Visto bueno y firma en una sola cadena (E23 Inc.4, RF-PRO-10).

En una entidad varias personas dan visto bueno y UNA firma: la que compromete a
la entidad y produce el documento definitivo. Presentar los dos actos igual
atribuiría a un revisor un compromiso que no adquirió — por eso el rol es una
columna y no una convención, y por eso cada acto tiene su propio endpoint.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.schemas.cadena import SolicitudCreate
from app.services.cadena_service import CadenaFirmaService

HUELLA = "a" * 64
REVISOR = uuid.uuid4()


def _sol(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "objeto_tipo": "borrador",
        "objeto_id": str(uuid.uuid4()),
        "payload_sha256": HUELLA,
        "estado": "pendiente",
        "nivel_seguridad": 1,
    }
    base.update(kw)
    return base


def _turno(rol: str = "visto_bueno", firmante_id: uuid.UUID = REVISOR) -> dict:
    return {
        "id": uuid.uuid4(),
        "orden": 1,
        "firmante_id": firmante_id,
        "rol": rol,
        "estado": "pendiente",
    }


def _conn() -> AsyncMock:
    conn = AsyncMock()
    tx = AsyncMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx)
    return conn


def _servicio(turno: dict, monkeypatch, *, sol: dict | None = None) -> CadenaFirmaService:
    from app.services import cadena_service as mod

    sol = sol or _sol()
    svc = CadenaFirmaService(_conn(), "demo")
    monkeypatch.setattr(svc, "_get_or_404", AsyncMock(return_value=sol), raising=False)
    monkeypatch.setattr(svc, "_assert_can_read", AsyncMock(return_value=None), raising=False)
    monkeypatch.setattr(svc, "get", AsyncMock(return_value="ok"), raising=False)
    svc._repo.es_firmante = AsyncMock(return_value=True)
    svc._repo.turno_activo_for_update = AsyncMock(return_value=turno)
    svc._repo.turno_activo = AsyncMock(return_value=None)
    svc._repo.marcar_turno = AsyncMock(return_value=True)
    svc._repo.actualizar_estado_solicitud = AsyncMock(return_value=None)

    async def _huella(borrador_id: str, tenant_slug: str) -> dict:
        return {"version": 1, "cuerpo_sha256": HUELLA}

    monkeypatch.setattr(mod, "get_huella_borrador", _huella)
    return svc


class TestComposicionDelCircuito:
    def test_roles_y_firmantes_tienen_que_casar(self) -> None:
        """Un desajuste repartiría los roles CORRIDOS.

        El revisor quedaría de firmante, y nadie lo vería hasta que alguien
        firmara algo que solo iba a revisar.
        """
        with pytest.raises(ValidationError, match="misma longitud"):
            SolicitudCreate(
                objeto_tipo="borrador",
                objeto_id="b1",
                payload_sha256=HUELLA,
                firmantes=[uuid.uuid4(), uuid.uuid4()],
                roles=["visto_bueno"],
            )

    def test_un_circuito_de_solo_vistos_buenos_no_se_admite(self) -> None:
        # Sin turno de firma no hay documento definitivo: el circuito no
        # terminaria nunca y el borrador se quedaria sin poder salir.
        with pytest.raises(ValidationError, match="al menos un turno"):
            SolicitudCreate(
                objeto_tipo="borrador",
                objeto_id="b1",
                payload_sha256=HUELLA,
                firmantes=[uuid.uuid4()],
                roles=["visto_bueno"],
            )

    def test_omitir_roles_deja_la_cadena_como_era_antes(self) -> None:
        # Compatibilidad: todas las cadenas anteriores a E23 son de firma. Un
        # default distinto reescribiria su significado en silencio.
        sol = SolicitudCreate(
            objeto_tipo="borrador",
            objeto_id="b1",
            payload_sha256=HUELLA,
            firmantes=[uuid.uuid4()],
        )
        assert sol.roles is None

    def test_el_circuito_tipico_se_admite(self) -> None:
        sol = SolicitudCreate(
            objeto_tipo="borrador",
            objeto_id="b1",
            payload_sha256=HUELLA,
            firmantes=[uuid.uuid4(), uuid.uuid4(), uuid.uuid4()],
            roles=["visto_bueno", "visto_bueno", "firma"],
        )
        assert sol.roles[-1] == "firma"


class TestResolucionDelVistoBueno:
    @pytest.mark.asyncio
    async def test_un_revisor_aprueba_su_turno(self, monkeypatch) -> None:
        svc = _servicio(_turno("visto_bueno"), monkeypatch)
        assert await svc.dar_visto_bueno(uuid.uuid4(), REVISOR, "de acuerdo") == "ok"
        svc._repo.marcar_turno.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_un_turno_de_firma_NO_se_resuelve_por_aqui(self, monkeypatch) -> None:
        """Este es el punto del endpoint separado.

        Si un turno de firma se pudiera cerrar con un visto bueno, existiría la
        vía para comprometer a la entidad sin firmar: el documento definitivo
        saldría sin la firma de quien responde por él.
        """
        svc = _servicio(_turno("firma"), monkeypatch)
        with pytest.raises(Exception) as exc:
            await svc.dar_visto_bueno(uuid.uuid4(), REVISOR)
        assert exc.value.detail["error"] == "turno_es_de_firma"
        svc._repo.marcar_turno.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_el_visto_bueno_pasa_por_el_MISMO_gate_del_documento(
        self, monkeypatch
    ) -> None:
        """Un visto bueno sobre un texto ya modificado no dice nada del actual.

        Sin este gate, el circuito recogería aprobaciones de un texto y firmaría
        otro — que es exactamente el fraude que existe para impedir.
        """
        from app.services import cadena_service as mod

        svc = _servicio(_turno("visto_bueno"), monkeypatch)

        async def _huella_distinta(borrador_id: str, tenant_slug: str) -> dict:
            return {"version": 3, "cuerpo_sha256": "b" * 64}

        monkeypatch.setattr(mod, "get_huella_borrador", _huella_distinta)
        with pytest.raises(Exception) as exc:
            await svc.dar_visto_bueno(uuid.uuid4(), REVISOR)
        assert exc.value.detail["error"] == "documento_modificado"
        svc._repo.marcar_turno.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_quien_no_esta_en_la_cadena_no_aprueba(self, monkeypatch) -> None:
        svc = _servicio(_turno("visto_bueno"), monkeypatch)
        svc._repo.es_firmante = AsyncMock(return_value=False)
        with pytest.raises(Exception) as exc:
            await svc.dar_visto_bueno(uuid.uuid4(), uuid.uuid4())
        assert exc.value.detail["error"] == "no_es_firmante"

    @pytest.mark.asyncio
    async def test_no_es_su_turno(self, monkeypatch) -> None:
        # El orden de la cadena es la garantía de que cada uno opina sobre lo
        # que el anterior ya vio.
        svc = _servicio(_turno("visto_bueno", firmante_id=uuid.uuid4()), monkeypatch)
        with pytest.raises(Exception) as exc:
            await svc.dar_visto_bueno(uuid.uuid4(), REVISOR)
        assert exc.value.detail["error"] == "solicitud_no_pendiente"

    @pytest.mark.asyncio
    async def test_una_cadena_ya_resuelta_no_admite_mas(self, monkeypatch) -> None:
        svc = _servicio(_turno("visto_bueno"), monkeypatch, sol=_sol(estado="firmado"))
        with pytest.raises(Exception) as exc:
            await svc.dar_visto_bueno(uuid.uuid4(), REVISOR)
        assert exc.value.detail["error"] == "solicitud_no_pendiente"


class TestHuellaAutoritativa:
    """La huella la fija el SERVIDOR, no el cliente (E23, RF-PRO-09).

    Es el gemelo de la resolución del nivel de seguridad, y por el mismo motivo:
    una huella que el llamante elige no vincula a nadie. Si el cliente pudiera
    declararla, bastaría con enviar la del documento anterior para que la cadena
    siguiera pareciendo válida después de editar el texto — el fraude que todo
    esto existe para impedir, entrando por la puerta principal.
    """

    def _svc(self, monkeypatch, respuesta=None, error=None) -> CadenaFirmaService:
        from app.services import cadena_service as mod

        async def _huella(borrador_id: str, tenant_slug: str) -> dict:
            if error is not None:
                raise error
            return respuesta or {}

        monkeypatch.setattr(mod, "get_huella_borrador", _huella)
        return CadenaFirmaService(_conn(), "demo")

    @pytest.mark.asyncio
    async def test_la_huella_del_cliente_se_ignora(self, monkeypatch) -> None:
        svc = self._svc(monkeypatch, respuesta={"cuerpo_sha256": "c" * 64})
        assert await svc._resolver_huella("b1") == "c" * 64

    @pytest.mark.asyncio
    async def test_la_cadena_ancla_al_TEXTO_y_no_al_pdf(self, monkeypatch) -> None:
        """El PDF/A de un borrador no existe hasta que se radica.

        Anclar al documento producido hacía el circuito inalcanzable: solo podía
        abrirse DESPUÉS de radicar, cuando recoger aprobaciones ya no sirve de
        nada. El trámite es el contrario — varios aprueban el TEXTO y el turno
        de firma produce el documento definitivo—, así que la cadena se ata a
        `cuerpo_sha256`. Si por lo que fuera llegara también la del PDF, se
        ignora: la que vincula es la del texto.
        """
        svc = self._svc(
            monkeypatch,
            respuesta={"cuerpo_sha256": "c" * 64, "documento_sha256": None},
        )
        assert await svc._resolver_huella("b1") == "c" * 64

    @pytest.mark.asyncio
    async def test_un_borrador_sin_texto_no_abre_circuito(self, monkeypatch) -> None:
        """Aprobar la nada no vincula a nadie."""
        svc = self._svc(monkeypatch, respuesta={"cuerpo_sha256": None})
        with pytest.raises(Exception) as exc:
            await svc._resolver_huella("b1")
        assert exc.value.detail["error"] == "borrador_sin_texto"

    @pytest.mark.asyncio
    async def test_si_no_se_puede_resolver_no_se_abre(self, monkeypatch) -> None:
        # Una cadena con la huella equivocada es PEOR que ninguna: parece que
        # protege.
        from app.core.http_clients import HuellaNoVerificableError

        svc = self._svc(monkeypatch, error=HuellaNoVerificableError("caido"))
        with pytest.raises(Exception) as exc:
            await svc._resolver_huella("b1")
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_un_borrador_inexistente_da_404_neutro(self, monkeypatch) -> None:
        # Mismo 404 que el no-read-up: abrir un circuito no puede servir para
        # descubrir si un objeto existe.
        from app.core.http_clients import BorradorDesaparecidoError

        svc = self._svc(monkeypatch, error=BorradorDesaparecidoError("no está"))
        with pytest.raises(Exception) as exc:
            await svc._resolver_huella("b1")
        assert exc.value.status_code == 404
        assert exc.value.detail["error"] == "solicitud_not_found"

    @pytest.mark.asyncio
    async def test_crear_GUARDA_la_huella_del_servidor_no_la_del_cliente(
        self, monkeypatch
    ) -> None:
        """El test de cableado, y el que de verdad cierra el fraude.

        Los de arriba llaman a `_resolver_huella` directamente: prueban su
        lógica, no que `crear` la use. Dejar que `crear` guarde
        `payload.payload_sha256` los deja a todos en verde — es la tercera vez
        en esta épica que un test mide la pieza en vez del cableado.

        Aquí se envía una huella FALSA en el body y se comprueba qué acabó
        escrito en la fila.
        """
        from app.services import cadena_service as mod

        HUELLA_FALSA = "f" * 64
        HUELLA_REAL = "9" * 64

        async def _huella(borrador_id: str, tenant_slug: str) -> dict:
            return {"version": 1, "cuerpo_sha256": HUELLA_REAL}

        monkeypatch.setattr(mod, "get_huella_borrador", _huella)
        async def _clearance(conn, actor) -> int:
            return 3

        monkeypatch.setattr(mod, "resolve_caller_clearance", _clearance)

        svc = CadenaFirmaService(_conn(), "demo")
        guardado: dict = {}

        from datetime import UTC, datetime

        async def _crear_solicitud(datos: dict) -> dict:
            guardado.update(datos)
            return {
                "id": uuid.uuid4(),
                "estado": "pendiente",
                "created_at": datetime.now(UTC),
                **datos,
            }

        # `resolver_nivel_por_objeto` devuelve None: un borrador no resuelve
        # contra `radicados`/`expedientes`, que es justo el caso de este test.
        svc._repo.resolver_nivel_por_objeto = AsyncMock(return_value=None)
        svc._repo.crear_solicitud = _crear_solicitud
        svc._repo.existe_abierta = AsyncMock(return_value=False)
        svc._repo.crear_turnos = AsyncMock(return_value=None)
        svc._repo.list_turnos = AsyncMock(return_value=[])
        monkeypatch.setattr(svc, "get", AsyncMock(return_value="ok"), raising=False)

        await svc.crear(
            SolicitudCreate(
                objeto_tipo="borrador",
                objeto_id="b1",
                payload_sha256=HUELLA_FALSA,
                firmantes=[uuid.uuid4()],
            ),
            uuid.uuid4(),
        )

        assert guardado["payload_sha256"] == HUELLA_REAL
        assert guardado["payload_sha256"] != HUELLA_FALSA, (
            "la huella del cliente NO puede acabar en la fila: bastaria con "
            "enviar la del documento anterior para que la cadena siguiera "
            "pareciendo valida despues de editar el texto"
        )


class TestElRolLlegaALasPantallas:
    """El `rol` estaba en la consulta, en el esquema, y NO se pasaba al construir.

    `_build_response` creaba cada `TurnoItem` sin `rol`, así que todos caían al
    valor por defecto —«firma»— hacia todas las pantallas. Es la peor dirección
    posible: el propio esquema advierte que presentarlos igual «atribuiría a un
    revisor un compromiso que no adquirió».

    Y de rebote dejaba media función inalcanzable: `/firmas` decide con este
    campo si ofrece «Dar visto bueno» en vez de «Firmar». Con el defecto puesto,
    esa acción no aparecía nunca — por mucho que el endpoint, el proxy y la
    pantalla estuvieran escritos y probados.
    """

    @staticmethod
    def _sol():
        return {
            "id": uuid.uuid4(),
            "objeto_tipo": "borrador",
            "objeto_id": str(uuid.uuid4()),
            "titulo": "Oficio",
            "estado": "pendiente",
            "nivel_seguridad": 1,
            "created_at": datetime.now(tz=UTC),
        }

    @staticmethod
    def _turno(orden: int, rol: str):
        return {
            "orden": orden,
            "firmante_id": uuid.uuid4(),
            "rol": rol,
            "estado": "pendiente",
            "firma_id": None,
            "comentario": None,
            "decidido_at": None,
        }

    def test_cada_turno_conserva_su_rol(self) -> None:
        from app.services.cadena_service import _build_response

        resp = _build_response(
            self._sol(),
            [self._turno(1, "visto_bueno"), self._turno(2, "visto_bueno"), self._turno(3, "firma")],
        )

        assert [t.rol for t in resp.items] == ["visto_bueno", "visto_bueno", "firma"]

    def test_un_revisor_nunca_se_presenta_como_firmante(self) -> None:
        # Anti-vacuidad por el lado que importa: si el defecto volviera, TODOS
        # los turnos dirían «firma» y este caso lo cazaría aunque el de arriba
        # cambiara de forma.
        from app.services.cadena_service import _build_response

        resp = _build_response(self._sol(), [self._turno(1, "visto_bueno")])

        assert resp.items[0].rol != "firma"
