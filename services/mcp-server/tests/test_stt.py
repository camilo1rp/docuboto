# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Transcripción de voz (ADR-020): el sistema transcribe, o dice que no puede.

La propiedad que estos tests blindan **no** es que Whisper acierte: es que
**nunca salga de aquí un texto que nadie dijo**. `knowledge-service` puede
permitirse un `StubEmbeddingProvider` porque un vectorizador por hashing es
recuperación léxica de verdad, más pobre pero honesta. Con la voz no existe el
equivalente: cualquier stub tendría que inventarse la frase, y esa frase acabaría
en el compositor del asistente o en el cuerpo de un documento oficial.

Por eso el default es `disabled` y su comportamiento es negarse.
"""

from __future__ import annotations

import pytest

from app.services.stt import (
    SttDeshabilitado,
    SttNoDisponible,
    WhisperLocal,
    build_stt_provider,
)


class TestFabrica:
    def test_el_default_no_transcribe(self) -> None:
        """Sin `STT_PROVIDER=whisper`, nada transcribe."""
        proveedor = build_stt_provider("disabled", whisper_url="http://x", timeout=1.0)
        assert isinstance(proveedor, SttDeshabilitado)

    def test_whisper_se_elige_explicitamente(self) -> None:
        """Control positivo: sin él, una fábrica que devolviera siempre
        `SttDeshabilitado` dejaría los demás tests verdes y el dictado muerto."""
        proveedor = build_stt_provider("whisper", whisper_url="http://x", timeout=1.0)
        assert isinstance(proveedor, WhisperLocal)

    @pytest.mark.parametrize("nombre", ["", "wisper", "openai", "WHISPER_CLOUD", "stub"])
    def test_un_nombre_desconocido_cae_en_deshabilitado(self, nombre: str) -> None:
        """Fail-closed en la fábrica, incluido «stub».

        Una errata en `STT_PROVIDER` tiene que dejar el sistema sin transcribir y
        diciéndolo, nunca activando por descarte un camino que nadie eligió. Y
        `stub` se prueba a propósito: es el nombre que alguien intentará por
        analogía con los embeddings, y aquí **no existe** — debe caer en
        deshabilitado, no en un modo que aproxime.
        """
        assert isinstance(
            build_stt_provider(nombre, whisper_url="http://x", timeout=1.0), SttDeshabilitado
        )


class TestDeshabilitado:
    @pytest.mark.asyncio
    async def test_se_niega_y_dice_que_falta(self) -> None:
        """Negarse NO es un hueco pendiente: es la respuesta correcta mientras
        nadie haya decidido dónde se transcribe la voz de sus funcionarios."""
        with pytest.raises(SttNoDisponible) as ei:
            await SttDeshabilitado().transcribir(
                b"audio", nombre_fichero="d.webm", mime="audio/webm", idioma="es"
            )
        assert ei.value.status == 503
        # El mensaje tiene que decir QUÉ hacer: un 503 seco deja a la persona
        # creyendo que el sistema está roto.
        assert "STT_PROVIDER" in ei.value.detalle
        assert "assistant" in ei.value.detalle

    @pytest.mark.asyncio
    async def test_no_devuelve_NINGUN_texto(self) -> None:
        """La propiedad central: eleva, no devuelve.

        Si algún día alguien «arregla» esto devolviendo `Transcripcion(texto="")`
        —o peor, un texto de ejemplo— el llamante lo tratará como una
        transcripción válida y la meterá en el compositor.
        """
        proveedor = SttDeshabilitado()
        with pytest.raises(SttNoDisponible):
            await proveedor.transcribir(
                b"x", nombre_fichero="d.webm", mime="audio/webm", idioma="es"
            )


class TestWhisperLocal:
    @pytest.mark.asyncio
    async def test_manda_el_audio_al_servicio_de_la_red_interna(self, monkeypatch) -> None:
        """El audio va al `whisper-service` de esta red, y a ningún otro sitio."""
        import app.services.stt as stt

        capturado: dict = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {"text": "  radica esta tutela  ", "language": "es", "model": "small"}

        class _Cliente:
            def __init__(self, **kw):
                capturado["base_url"] = kw.get("base_url")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, ruta, **kw):
                capturado["ruta"] = ruta
                capturado["files"] = kw.get("files")
                capturado["data"] = kw.get("data")
                return _Resp()

        monkeypatch.setattr(stt.httpx, "AsyncClient", _Cliente)

        r = await stt.WhisperLocal("http://whisper-service:8012", 5.0).transcribir(
            b"BINARIO", nombre_fichero="dictado.webm", mime="audio/webm", idioma="es"
        )

        assert capturado["base_url"] == "http://whisper-service:8012"
        assert capturado["ruta"] == "/transcribe"
        # El audio viaja tal cual, sin reempaquetar: cualquier conversión
        # intermedia es una oportunidad de perder o alterar lo que se dijo.
        assert capturado["files"]["file"][1] == b"BINARIO"
        assert capturado["data"]["language"] == "es"
        assert r.texto == "radica esta tutela"
        assert r.proveedor == "whisper"

    @pytest.mark.asyncio
    async def test_un_audio_sin_habla_devuelve_vacio_y_NO_es_un_error(self, monkeypatch) -> None:
        """Silencio es un resultado, no un fallo — y tampoco una frase inventada.

        Es justo donde la tentación de «devolver algo» es mayor: un compositor
        vacío parece un fallo del sistema. No lo es: es lo que se oyó.
        """
        import app.services.stt as stt

        class _Resp:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {"text": "   ", "language": "es"}

        class _Cliente:
            def __init__(self, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                return _Resp()

        monkeypatch.setattr(stt.httpx, "AsyncClient", _Cliente)
        r = await stt.WhisperLocal("http://w:8012", 5.0).transcribir(
            b"x", nombre_fichero="d.webm", mime="audio/webm", idioma="es"
        )
        assert r.texto == ""

    @pytest.mark.asyncio
    async def test_el_servicio_caido_se_distingue_de_no_configurado(self, monkeypatch) -> None:
        """Dos situaciones opuestas no pueden dar el mismo mensaje.

        «No lo has activado» y «lo activaste y no responde» llevan a acciones
        distintas: una es configurar, la otra es levantar un contenedor. El
        mensaje de esta segunda menciona el perfil y el detalle del fallo.
        """
        import httpx

        import app.services.stt as stt

        class _Cliente:
            def __init__(self, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **kw):
                raise httpx.ConnectError("no route to host")

        monkeypatch.setattr(stt.httpx, "AsyncClient", _Cliente)
        with pytest.raises(SttNoDisponible) as ei:
            await stt.WhisperLocal("http://w:8012", 5.0).transcribir(
                b"x", nombre_fichero="d.webm", mime="audio/webm", idioma="es"
            )
        assert "no responde" in ei.value.detalle
        assert "STT_PROVIDER" not in ei.value.detalle, (
            "este mensaje es el de «está caído», no el de «no está configurado»"
        )


# ---------------------------------------------------------------------------
# El endpoint (ADR-020: `POST /api/v1/assistant/transcribe`)
# ---------------------------------------------------------------------------


CABECERAS = {"Authorization": "Bearer tok", "X-Tenant-Slug": "demo"}


class TestEndpoint:
    @pytest.mark.asyncio
    async def test_sin_proveedor_responde_503_y_explica(self, client_with_gateway) -> None:
        """Lo que ve una instalación mínima: un no, con instrucciones."""
        from app.main import app
        from app.services.stt import SttDeshabilitado

        client, _ = client_with_gateway
        app.state.stt_provider = SttDeshabilitado()

        resp = await client.post(
            "/api/v1/assistant/transcribe",
            files={"file": ("d.webm", b"audio", "audio/webm")},
            headers=CABECERAS,
        )

        assert resp.status_code == 503
        cuerpo = resp.json()["detail"]
        assert cuerpo["error"] == "stt_no_disponible"
        assert "STT_PROVIDER" in cuerpo["detail"]
        # Y NO hay ningún campo de texto: un cliente descuidado no puede leer
        # una transcripción de una respuesta de error.
        assert "texto" not in resp.json()

    @pytest.mark.asyncio
    async def test_devuelve_el_texto_y_DICE_por_donde_paso_la_voz(
        self, client_with_gateway
    ) -> None:
        """El contrato incluye proveedor y modelo, a propósito.

        Quien revisa un documento que salió de un dictado tiene derecho a saber
        quién transcribió su voz. Ocultarlo haría indistinguible una
        transcripción local de una que hubiera salido a un tercero.
        """
        from app.main import app
        from app.services.stt import SttProvider, Transcripcion

        class _Falso(SttProvider):
            nombre = "whisper"

            async def transcribir(self, audio, *, nombre_fichero, mime, idioma):
                return Transcripcion(
                    texto="radica esta tutela", idioma=idioma, proveedor="whisper", modelo="small"
                )

        client, _ = client_with_gateway
        app.state.stt_provider = _Falso()

        resp = await client.post(
            "/api/v1/assistant/transcribe",
            files={"file": ("d.webm", b"audio", "audio/webm")},
            data={"idioma": "es"},
            headers=CABECERAS,
        )

        assert resp.status_code == 200
        cuerpo = resp.json()
        assert cuerpo["texto"] == "radica esta tutela"
        assert cuerpo["proveedor"] == "whisper"
        assert cuerpo["modelo"] == "small"

    @pytest.mark.asyncio
    async def test_sin_sesion_no_se_llega_al_proveedor(self, client_with_gateway) -> None:
        """El guard corre ANTES: un audio no se procesa por quien no se identificó."""
        from app.main import app
        from app.services.stt import SttProvider

        llamado = False

        class _Espia(SttProvider):
            nombre = "whisper"

            async def transcribir(self, *a, **kw):
                nonlocal llamado
                llamado = True
                raise AssertionError("no debió llegar aquí")

        client, _ = client_with_gateway
        app.state.stt_provider = _Espia()

        resp = await client.post(
            "/api/v1/assistant/transcribe",
            files={"file": ("d.webm", b"audio", "audio/webm")},
            headers={"X-Tenant-Slug": "demo"},  # sin Authorization
        )

        assert resp.status_code == 401
        assert not llamado

    @pytest.mark.asyncio
    async def test_un_audio_demasiado_grande_se_rechaza_antes_de_transcribir(
        self, client_with_gateway, monkeypatch
    ) -> None:
        """Esto es para dictar una frase, no para subir una reunión de dos horas.

        El tope protege la memoria del servicio, y se comprueba ANTES de llamar
        al proveedor: transcribir cien megas y luego decir que no cabían sería
        pagar todo el coste para nada.
        """
        from app.core.config import settings
        from app.main import app
        from app.services.stt import SttProvider

        llamado = False

        class _Espia(SttProvider):
            nombre = "whisper"

            async def transcribir(self, *a, **kw):
                nonlocal llamado
                llamado = True
                raise AssertionError("no debió llegar aquí")

        client, _ = client_with_gateway
        app.state.stt_provider = _Espia()
        monkeypatch.setattr(settings, "stt_max_bytes", 10)

        resp = await client.post(
            "/api/v1/assistant/transcribe",
            files={"file": ("d.webm", b"x" * 100, "audio/webm")},
            headers=CABECERAS,
        )

        assert resp.status_code == 413
        assert not llamado
