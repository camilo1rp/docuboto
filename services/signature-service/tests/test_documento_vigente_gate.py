# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Lo que se firma tiene que seguir siendo lo que se aprobó (E23 Inc.4, RF-PRO-11).

**La defensa que se prueba aquí es la SÍNCRONA, y corre con el consumidor de
eventos parado.** La invalidación por evento es *at-least-once* y puede
retrasarse; si la garantía viviera solo en la cola, existiría una ventana en la
que un firmante resuelve su turno sobre un texto ya modificado, y un test que
dependiera de la cola pasaría en verde por casualidad de tiempos. Aquí no hay
cola: se relee la huella y se compara, o se deniega.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.core.http_clients import BorradorDesaparecidoError, HuellaNoVerificableError
from app.services.cadena_service import CadenaFirmaService

HUELLA_APROBADA = "a" * 64
HUELLA_OTRA = "b" * 64


def _sol(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "objeto_tipo": "borrador",
        "objeto_id": str(uuid.uuid4()),
        "payload_sha256": HUELLA_APROBADA,
        "estado": "pendiente",
        "nivel_seguridad": 1,
    }
    base.update(kw)
    return base


def _servicio() -> CadenaFirmaService:
    return CadenaFirmaService(AsyncMock(), "demo")


async def _gate(sol: dict, monkeypatch, respuesta=None, error=None) -> None:
    from app.services import cadena_service as mod

    async def _huella(borrador_id: str, tenant_slug: str) -> dict:
        if error is not None:
            raise error
        return respuesta or {}

    monkeypatch.setattr(mod, "get_huella_borrador", _huella)
    await _servicio()._gate_documento_vigente(sol)


class TestGateSincrono:
    @pytest.mark.asyncio
    async def test_el_documento_intacto_pasa(self, monkeypatch) -> None:
        await _gate(
            _sol(),
            monkeypatch,
            respuesta={"version": 1, "cuerpo_sha256": HUELLA_APROBADA},
        )

    @pytest.mark.asyncio
    async def test_el_documento_cambiado_no_se_firma(self, monkeypatch) -> None:
        # El caso central: alguien editó el texto después de que dos personas
        # dieran su visto bueno. La firma NO puede recaer sobre lo nuevo.
        with pytest.raises(Exception) as exc:
            await _gate(
                _sol(),
                monkeypatch,
                respuesta={"version": 4, "cuerpo_sha256": HUELLA_OTRA},
            )
        detalle = exc.value.detail
        assert detalle["error"] == "documento_modificado"
        assert detalle["status"] == 409
        # El mensaje dice qué hacer: las aprobaciones no se arrastran.
        assert "no se arrastran" in detalle["detail"]

    @pytest.mark.asyncio
    async def test_sin_documento_producido_tampoco_se_firma(self, monkeypatch) -> None:
        # El trigger de la base retira el documento al cambiar el cuerpo, así
        # que `None` es exactamente el estado «el texto cambió y aún no se ha
        # vuelto a generar». Tratarlo como «igual» sería el peor de los fallos.
        with pytest.raises(Exception) as exc:
            await _gate(
                _sol(), monkeypatch, respuesta={"version": 2, "cuerpo_sha256": None}
            )
        assert exc.value.detail["error"] == "documento_modificado"

    @pytest.mark.asyncio
    async def test_si_no_se_puede_comprobar_se_DENIEGA(self, monkeypatch) -> None:
        """No poder comprobar NO es «no ha cambiado».

        Si un fallo de red se interpretara como «sigue igual», bastaría con que
        document-service estuviera caído para poder firmar sobre un texto
        modificado — el momento en que menos se querría poder.
        """
        with pytest.raises(Exception) as exc:
            await _gate(
                _sol(), monkeypatch, error=HuellaNoVerificableError("red caida")
            )
        assert exc.value.detail["error"] == "verificacion_documento_no_disponible"
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_el_objeto_desaparecido_se_distingue(self, monkeypatch) -> None:
        # Mismo efecto (denegar) y mensaje distinto: «ya no existe» y «no se
        # pudo comprobar» mandan a mirar sitios diferentes.
        with pytest.raises(Exception) as exc:
            await _gate(_sol(), monkeypatch, error=BorradorDesaparecidoError("no está"))
        assert exc.value.detail["error"] == "documento_desaparecido"

    @pytest.mark.asyncio
    async def test_una_cadena_vieja_sin_huella_no_se_firma_a_ciegas(
        self, monkeypatch
    ) -> None:
        # Cadenas creadas antes de que la huella fuera obligatoria. Firmarlas
        # sería volver a firmar el NOMBRE del objeto.
        with pytest.raises(Exception) as exc:
            await _gate(_sol(payload_sha256=None), monkeypatch, respuesta={})
        assert exc.value.detail["error"] == "cadena_sin_huella"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("tipo", ["radicado", "expediente"])
    async def test_radicado_y_expediente_pasan_por_el_gate(
        self, monkeypatch, tipo: str
    ) -> None:
        """AREA-04-09 Media-2: ya no se firma el identificador, se firma el contenido.

        Antes el gate no decía nada de estos tipos (no había huella contra la que
        comparar). Ahora el productor (PDF vigente / índice vigente) la resuelve:
        si coincide pasa; si el contenido cambió, `409 documento_modificado`.
        """
        from unittest.mock import AsyncMock

        from app.services import cadena_service as mod

        productor = AsyncMock(return_value={"huella_sha256": HUELLA_APROBADA, "version": 3})
        monkeypatch.setattr(
            mod, "get_huella_radicado" if tipo == "radicado" else "get_huella_indice_expediente",
            productor,
        )
        await _servicio()._gate_documento_vigente(_sol(objeto_tipo=tipo))
        productor.assert_awaited_once()

        productor.return_value = {"huella_sha256": HUELLA_OTRA, "version": 4}
        with pytest.raises(Exception) as exc:
            await _servicio()._gate_documento_vigente(_sol(objeto_tipo=tipo))
        assert exc.value.detail["error"] == "documento_modificado"
        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_un_tipo_sin_resolutor_no_pasa_por_aqui(self, monkeypatch) -> None:
        """Control: lo que no está en `RESOLUTOR_DE_HUELLA` no se consulta ni se niega."""
        llamadas = []
        from app.services import cadena_service as mod

        async def _huella(borrador_id: str, tenant_slug: str) -> dict:
            llamadas.append(borrador_id)
            return {}

        monkeypatch.setattr(mod, "get_huella_borrador", _huella)
        await _servicio()._gate_documento_vigente(
            _sol(objeto_tipo="documento", payload_sha256=None)
        )
        assert llamadas == []


class TestGateConectado:
    """El gate existe **y se invoca** al firmar.

    Los tests de arriba llaman a `_gate_documento_vigente` directamente: prueban
    su lógica, no que esté cableada. Desconectar la llamada de `firmar()` los
    dejaría a todos en verde — el mismo defecto que un test que mide el DOM en
    vez de la variable que se emite. Este es el que cae con esa mutación.

    **Sobre el orden (B6 §14.4).** Estos tests exigían antes que el gate corriera
    ANTES de `es_firmante`, por tres razones escritas en su docstring. Dos eran
    equivocadas: que un ajeno a la cadena recibiera `no_es_firmante` se
    presentaba como daño, y es lo correcto —el orden anterior le contestaba
    `409 documento_modificado` con el número de versión, confirmándole que el
    circuito existe y por dónde va, con el nivel de acceso como único
    requisito—. La tercera sí era de fondo, y **sigue garantizada**: el gate
    corre antes de la transacción y antes de `_gate_2fa`, así que ningún segundo
    factor se consume en un acto que se va a rechazar igual. Eso es lo que mide
    ahora el segundo test, en vez del orden que producía la incoherencia.
    Ver `test_la_autorizacion_va_antes_que_el_estado_del_documento.py`.
    """

    @staticmethod
    def _servicio_con_turno_propio(actor, sol, monkeypatch):
        """Servicio con las guardas de autorización ya satisfechas."""
        servicio = _servicio()
        servicio._repo.es_firmante = AsyncMock(return_value=True)
        servicio._repo.turno_activo = AsyncMock(
            return_value={"id": uuid.uuid4(), "firmante_id": actor, "rol": "firma"}
        )
        monkeypatch.setattr(
            servicio, "_get_or_404", AsyncMock(return_value=sol), raising=False
        )
        monkeypatch.setattr(
            servicio, "_assert_can_read", AsyncMock(return_value=None), raising=False
        )
        return servicio

    @pytest.mark.asyncio
    async def test_firmar_un_borrador_modificado_da_409(self, monkeypatch) -> None:
        from app.services import cadena_service as mod

        sol = _sol()
        actor = uuid.uuid4()
        servicio = self._servicio_con_turno_propio(actor, sol, monkeypatch)

        async def _huella(borrador_id: str, tenant_slug: str) -> dict:
            return {"version": 7, "cuerpo_sha256": HUELLA_OTRA}

        monkeypatch.setattr(mod, "get_huella_borrador", _huella)

        with pytest.raises(Exception) as exc:
            await servicio.firmar(sol["id"], actor)
        assert exc.value.detail["error"] == "documento_modificado"

    @pytest.mark.asyncio
    async def test_el_gate_corre_antes_de_consumir_ningun_segundo_factor(
        self, monkeypatch
    ) -> None:
        """Lo que de verdad no puede pasar: quemar un factor para nada.

        Un OTP-correo consumido, o un TOTP quemado, en un acto que el gate del
        documento va a rechazar de todos modos, es un coste irreversible para el
        firmante —y en el caso del correo, un desafío gastado y un correo
        enviado por vuelta—. El gate va antes de la transacción, así que ni
        `_gate_2fa` llega a llamarse ni se abre transacción alguna.
        """
        from app.services import cadena_service as mod

        sol = _sol()
        actor = uuid.uuid4()
        servicio = self._servicio_con_turno_propio(actor, sol, monkeypatch)
        monkeypatch.setattr(
            servicio,
            "_gate_2fa",
            AsyncMock(side_effect=AssertionError("el 2FA no debio consultarse")),
            raising=False,
        )

        async def _huella(borrador_id: str, tenant_slug: str) -> dict:
            return {"version": 7, "cuerpo_sha256": HUELLA_OTRA}

        monkeypatch.setattr(mod, "get_huella_borrador", _huella)

        with pytest.raises(Exception) as exc:
            await servicio.firmar(sol["id"], actor)
        assert exc.value.detail["error"] == "documento_modificado"
        servicio._conn.transaction.assert_not_called()
