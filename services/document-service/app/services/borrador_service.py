# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Plantillas y borradores de Salida (paridad legado).

Ciclo del borrador: crear (opc. desde plantilla) → editar → aprobar → RADICAR
(genera el radicado real reutilizando la radicación). Hasta radicar no hay número.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg
from fastapi import HTTPException

from app.clients import signature_client as signature_client_mod
from app.clients.signature_client import CircuitoNoVerificableError
from app.clients.storage_client import (
    RenderFallidoError,
    RenderNoDisponibleError,
    render_documento,
    upload_artefacto,
)
from app.core.combinacion import SEGUNDA_PASADA, combinar, desconocidos, marcadores
from app.repositories.borrador_repository import BorradorRepository
from app.schemas.borrador import (
    BorradorCreate,
    BorradorResponse,
    BorradorUpdate,
    CombinarRequest,
    CombinarResponse,
    PlantillaAlcanceUpdate,
    PlantillaCreate,
    PlantillaResponse,
)
from app.schemas.document import DocumentCreate, DocumentResponse
from app.services.contexto_combinacion import ContextoCombinacion

logger = logging.getLogger(__name__)


def _json_obj(raw: object) -> dict:
    """asyncpg devuelve JSONB como `str`; se tolera `dict` por si hay codec."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes, bytearray)):
        try:
            cargado = json.loads(raw)
        except ValueError:
            return {}
        return cargado if isinstance(cargado, dict) else {}
    return {}


@dataclass(frozen=True)
class _DocumentoProducido:
    """El PDF/A ya generado y ya subido, con lo que hace falta para anexarlo."""

    file_id: uuid.UUID
    sha256: str | None
    file_size: int | None
    formato: str


def _bo(row, faltantes: list[str] | None = None) -> BorradorResponse:
    """Fila → respuesta, con los faltantes que YA guarda la fila.

    El argumento sigue existiendo para el camino de creación, que los acaba de
    calcular en memoria; en todo lo demás salen de la columna. Antes se pasaba
    `[]` por defecto en cada lectura, así que la lista se perdía en cuanto
    terminaba la petición que la había producido.
    """
    datos = dict(row)
    guardados = list(datos.pop("faltantes", None) or [])
    return BorradorResponse(**datos, faltantes=faltantes if faltantes is not None else guardados)


def _marcadores_desconocidos(cuerpo: str) -> HTTPException:
    """422 con la lista, no un 'no válido' a secas.

    Quien escribe la plantilla necesita saber QUÉ marcador sobra: un rechazo sin
    la lista obliga a buscarlo a ojo en un cuerpo de varias páginas, y el camino
    corto entonces es borrar marcadores buenos hasta que pase.
    """
    faltan = desconocidos(cuerpo)
    return HTTPException(
        status_code=422,
        detail={
            "error": "marcador_desconocido",
            "detail": (
                "La plantilla usa marcadores que no existen en el vocabulario: "
                + ", ".join(f"*{m}*" for m in faltan)
            ),
            "marcadores": faltan,
            "status": 422,
        },
    )


def _not_found(detail: str) -> HTTPException:
    return HTTPException(
        status_code=404, detail={"error": "not_found", "detail": detail, "status": 404}
    )


class BorradorService:
    def __init__(
        self,
        conn: asyncpg.Connection,
        tenant_slug: str = "",
        tenant_client: Any = None,
        signature_client: Any = None,
    ) -> None:
        self._conn = conn
        self._repo = BorradorRepository(conn)
        self._slug = tenant_slug
        self._tenant_client = tenant_client
        # El módulo real por defecto; los tests inyectan un doble. No es
        # opcional en el sentido de «si no está, no se consulta»: sin él la
        # aprobación no puede comprobar el circuito y el gate desaparecería.
        self._signature_client = signature_client or signature_client_mod

    # -- plantillas ----------------------------------------------------

    async def crear_plantilla(
        self, payload: PlantillaCreate, creado_por: uuid.UUID | None = None
    ) -> PlantillaResponse:
        # RF-PRO-01: el marcador desconocido se rechaza AL GUARDAR, no al usar.
        # En el legado no fallaba nunca: se quedaba impreso en el oficio que
        # recibía el ciudadano, y para entonces ya no había a quién preguntarle.
        if desconocidos(payload.cuerpo):
            raise _marcadores_desconocidos(payload.cuerpo)
        datos = payload.model_dump()
        dependencias = datos.pop("dependencias", [])
        datos["creado_por"] = creado_por
        row = await self._repo.insert_plantilla(datos)
        if not payload.es_global and dependencias:
            await self._repo.set_dependencias_plantilla(row["id"], dependencias)
        return PlantillaResponse(**dict(row), dependencias=dependencias)

    async def listar_plantillas(
        self,
        activo: bool | None,
        *,
        usuario: uuid.UUID | None = None,
        sin_recorte: bool = False,
    ) -> list[PlantillaResponse]:
        """Plantillas que alcanza el llamante (RF-PRO-06).

        `sin_recorte=True` es para la pantalla de administración: hay que poder
        gestionar una plantilla que no se usa desde la propia dependencia. Lo
        pide el router tras comprobar el permiso de administración, no el
        cliente — un recorte que el llamante puede apagar no recorta.

        Un usuario sin URD activa ve **solo las globales**, no todas: el vacío
        no es un atajo a «sin restricción». Es la misma regla fail-closed que el
        clearance, donde lo dudoso cae a Pública.
        """
        depe_codigos: list[int] | None = None
        if not sin_recorte:
            depe_codigos = (
                await self._repo.dependencias_del_usuario(usuario) if usuario else []
            )
        filas = await self._repo.list_plantillas(activo, depe_codigos=depe_codigos)
        return [
            PlantillaResponse(
                **dict(r),
                dependencias=(
                    [] if r["es_global"] else await self._repo.dependencias_de_plantilla(r["id"])
                ),
            )
            for r in filas
        ]

    async def fijar_alcance(
        self, plantilla_id: uuid.UUID, payload: PlantillaAlcanceUpdate
    ) -> PlantillaResponse:
        fila = await self._repo.get_plantilla(plantilla_id)
        if fila is None:
            raise _not_found(f"Plantilla {plantilla_id} no encontrada")
        await self._repo.set_alcance_plantilla(plantilla_id, payload.es_global)
        await self._repo.set_dependencias_plantilla(
            plantilla_id, [] if payload.es_global else payload.dependencias
        )
        fila = await self._repo.get_plantilla(plantilla_id)
        assert fila is not None
        return PlantillaResponse(
            **dict(fila),
            dependencias=await self._repo.dependencias_de_plantilla(plantilla_id),
        )

    async def combinar_plantilla(
        self,
        plantilla_id: uuid.UUID,
        payload: CombinarRequest,
        *,
        max_clearance: int,
        productor_id: uuid.UUID | None = None,
        funcionario: dict[str, Any] | None = None,
    ) -> CombinarResponse:
        """Devuelve el cuerpo de la plantilla YA combinado, con sus faltantes.

        Existe porque la pantalla necesita enseñar el texto resuelto en el
        editor antes de guardar nada, y la combinación no puede bajar al
        navegador: un marcador resuelto en el cliente es un dato que el cliente
        puede cambiar, y el documento que da fe no puede depender de eso (D-05).
        """
        pl = await self._repo.get_plantilla(plantilla_id)
        if pl is None:
            raise _not_found("Plantilla no encontrada")
        contexto = await ContextoCombinacion(self._conn, self._slug).construir(
            radicado_padre_id=payload.radicado_padre_id,
            max_clearance=max_clearance,
            productor_id=productor_id,
            funcionario=funcionario,
            tenant_client=self._tenant_client,
        )
        resultado = combinar(pl["cuerpo"], contexto)
        return CombinarResponse(cuerpo=resultado.texto, faltantes=resultado.faltantes)

    async def borrar_plantilla(self, plantilla_id: uuid.UUID) -> None:
        if not await self._repo.delete_plantilla(plantilla_id):
            raise _not_found(f"Plantilla {plantilla_id} no encontrada")

    # -- borradores ----------------------------------------------------

    async def crear(
        self,
        payload: BorradorCreate,
        creado_por: uuid.UUID | None,
        *,
        max_clearance: int,
        funcionario: dict[str, Any] | None = None,
    ) -> BorradorResponse:
        """Crea el borrador y, si viene de plantilla, COMBINA los campos.

        `max_clearance` es obligatorio y sin default a propósito: el contexto de
        combinación lee el radicado padre, y un default que significara «sin
        restricción» sería fail-OPEN — el mismo modo de fallo que dejó
        `get/set_disposition` sin gate.
        """
        cuerpo = payload.cuerpo
        faltantes: list[str] = []

        # La combinación se hace SIEMPRE que haya plantilla, aunque el cuerpo
        # venga puesto.
        #
        # Antes solo corría con `cuerpo is None`, y la pantalla manda el cuerpo
        # ya combinado —lo pide con `POST /plantillas/{id}/combinar` para
        # enseñarlo en el editor—, así que la rama no se ejecutaba nunca y la
        # lista de huecos se guardaba vacía. Quedarían dos salidas malas:
        # aceptar la lista que mande el cliente (un dato que el cliente elige, y
        # este acaba impreso en un documento que da fe) o dejar de enseñar el
        # texto resuelto antes de guardar. La tercera es esta: el servidor
        # rehace la MISMA combinación que ya hizo para `/combinar` y saca de ahí
        # los faltantes, mientras que el cuerpo que se guarda sigue siendo el
        # del cliente — que ha podido editarlo, y con razón.
        if payload.plantilla_id is not None:
            pl = await self._repo.get_plantilla(payload.plantilla_id)
            if pl is None:
                raise _not_found("Plantilla no encontrada")
            # Aquí estaba la brecha: `cuerpo = pl["cuerpo"]` copiaba la plantilla
            # LITERAL, marcadores incluidos. La plantilla era texto muerto.
            contexto = await ContextoCombinacion(self._conn, self._slug).construir(
                radicado_padre_id=payload.radicado_padre_id,
                max_clearance=max_clearance,
                productor_id=creado_por,
                funcionario=funcionario,
                tenant_client=self._tenant_client,
            )
            resultado = combinar(pl["cuerpo"], contexto)
            faltantes = resultado.faltantes
            if cuerpo is None:
                cuerpo = resultado.texto

        row = await self._repo.insert_borrador(
            {
                "doc_type": payload.doc_type,
                "subject": payload.subject,
                "dest_dept": payload.dest_dept,
                "cuerpo": cuerpo,
                "plantilla_id": payload.plantilla_id,
                "radicado_padre_id": payload.radicado_padre_id,
                "creado_por": creado_por,
                "faltantes": faltantes,
            }
        )
        return _bo(row, faltantes)

    async def get(self, borrador_id: uuid.UUID) -> BorradorResponse:
        row = await self._repo.get_borrador(borrador_id)
        if row is None:
            raise _not_found(f"Borrador {borrador_id} no encontrado")
        return _bo(row)

    async def listar(self, estado: str | None) -> list[BorradorResponse]:
        return [_bo(r) for r in await self._repo.list_borradores(estado)]

    async def _require_editable(self, borrador_id: uuid.UUID):
        row = await self._repo.get_borrador(borrador_id)
        if row is None:
            raise _not_found(f"Borrador {borrador_id} no encontrado")
        if row["estado"] == "radicado":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "borrador_radicado",
                    "detail": "El borrador ya fue radicado",
                    "status": 409,
                },
            )
        return row

    async def editar(self, borrador_id: uuid.UUID, payload: BorradorUpdate) -> BorradorResponse:
        await self._require_editable(borrador_id)
        row = await self._repo.update_borrador(borrador_id, payload.model_dump(exclude_unset=True))
        return _bo(row)

    async def aprobar(self, borrador_id: uuid.UUID) -> BorradorResponse:
        """Marca el borrador como aprobado — si nadie tiene un turno pendiente.

        **El circuito de visto bueno tiene que atar** (E23, RF-PRO-09). Antes
        de este gate no ataba: se abría una cadena de tres turnos sobre el
        borrador, nadie resolvía ninguno, y quien tuviera `PERM_RADI` pulsaba
        «Aprobar» y radicaba. El visto bueno de los funcionarios era
        decorativo, y de paso el gate `documento_modificado` que protege cada
        turno no protegía nada: nadie estaba obligado a pasar por él.

        El gate es **vinculante si existe**, no obligatorio: sin circuito
        abierto, aprobar directo sigue funcionando igual que siempre — hay
        entidades donde una salida no pasa por visto bueno, y exigirlo a todas
        convertiría en requisito un paso que nadie pidió. Lo que no puede pasar
        es abrir un circuito deliberadamente y luego ignorarlo.

        Al completarse la cadena el turno final aprueba solo (signature-service
        lo pide al cerrar); si esa llamada se pierde, este botón vuelve a estar
        disponible porque la consulta ve el circuito ya cerrado. Por eso el
        aviso automático puede ser best-effort sin abrir ninguna ventana: la
        autoridad es la consulta, no el aviso.
        """
        row = await self._require_editable(borrador_id)
        if row["estado"] != "borrador":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "estado_invalido",
                    "detail": f"No se puede aprobar desde '{row['estado']}'",
                    "status": 409,
                },
            )
        await self._gate_circuito_pendiente(borrador_id)
        return _bo(await self._repo.set_estado(borrador_id, "aprobado"))

    async def _gate_circuito_pendiente(
        self, borrador_id: uuid.UUID, *, acto: str = "aprobar"
    ) -> dict:
        """409 si hay un circuito de aprobación abierto; 503 si no se puede saber.

        El 503 es deliberado y es el lado seguro: la única consecuencia de no
        poder preguntar es no poder aprobar. Suponer «no hay circuito» cuando
        signature-service no responde reabriría el mismo agujero por la puerta
        de atrás, y lo haría justo cuando el sistema está degradado.
        """
        try:
            estado = await self._signature_client.get_estado_circuito(
                str(borrador_id), self._slug
            )
        except CircuitoNoVerificableError as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "circuito_no_verificable",
                    "detail": (
                        "No se pudo comprobar si este borrador tiene un circuito "
                        f"de aprobación abierto, así que no se {acto}. Reintenta "
                        "en unos segundos."
                    ),
                    "status": 503,
                },
            ) from exc
        if estado.get("pendiente"):
            # El mensaje nombra el acto que se intentaba: «no se aprueba desde
            # aqui» delante de un boton de radicar manda a buscar un problema
            # que no existe.
            cola = (
                "se radica cuando lo firme quien responde por el documento, no antes"
                if acto == "radicar"
                else "se aprueba cuando lo resuelvan todos los turnos, no desde aquí"
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "circuito_pendiente",
                    "detail": (
                        f"Este borrador tiene un circuito de aprobación abierto: {cola}."
                    ),
                    "status": 409,
                },
            )
        return estado

    def _gate_texto_firmado(self, row, estado_circuito: dict) -> None:
        """409 si el texto cambió DESPUÉS de que el circuito lo firmara.

        Editar un documento ya firmado es parte del trámite, no un abuso: se
        recogen los vistos buenos, alguien recuerda un anexo o corrige una cifra
        a última hora, y hay que rehacerlo. El sistema no puede impedirlo —para
        eso existe `devolver_a_borrador` (RF-PRO-10)—. Lo que no puede es dar
        por buenas las firmas anteriores: el firmado deja de valer y las firmas
        se recogen otra vez sobre el texto nuevo.

        Y ahí estaba el hueco. `radicar` comprobaba dos cosas —estado
        `aprobado` y ningún circuito ABIERTO— y ninguna de las dos se entera de
        una edición posterior al cierre: la cadena queda en `firmado`, así que
        no está pendiente, y el estado sigue en `aprobado` porque el disparador
        de la migración 030 sube `version` y recalcula la huella pero no toca
        `estado`. El gate `documento_modificado` de signature-service protege
        cada TURNO mientras la cadena vive y desaparece justo cuando se cierra,
        de modo que el acto irreversible —consumir el consecutivo y emitir el
        oficio— era el único que se quedaba sin vigilancia. Salía un documento
        oficial con número inmutable cuyo texto nadie aprobó, mientras los
        asientos seguían atestiguando que tres funcionarios sí.

        La comparación es la autoridad y se hace PREGUNTANDO, igual que el gate
        de arriba: una marca copiada en `borrador` se queda rancia por caminos
        que no pasan por aquí, y rancia del lado permisivo es una puerta
        abierta. La migración 018 ya había previsto este caso al crear el estado
        `invalidada` —«o peor, "firmado" sobre uno que cambió después»— y dejó
        escrito que una solicitud invalidada no bloquea a la que la sustituye.
        """
        if not estado_circuito.get("completo"):
            # Nunca se firmó: el camino directo de siempre, sin nada que comparar.
            return

        firmado = estado_circuito.get("payload_sha256")
        vigente = row["cuerpo_sha256"]
        if firmado is None or vigente is None or firmado == vigente:
            return

        raise HTTPException(
            status_code=409,
            detail={
                "error": "texto_modificado_tras_firmar",
                "detail": (
                    "El texto de este borrador cambió después de que el circuito "
                    "lo firmara, así que esas firmas ya no corresponden a lo que "
                    "se radicaría. Devuelve el borrador a edición y abre un "
                    "circuito nuevo para recoger las firmas sobre el texto "
                    "vigente."
                ),
                "status": 409,
            },
        )

    async def devolver_a_borrador(self, borrador_id: uuid.UUID) -> BorradorResponse:
        """Reabre un borrador aprobado para poder corregir el texto (RF-PRO-10).

        Sin esto el ciclo era de una sola dirección —`borrador → aprobado →
        radicado`— y el texto quedaba congelado al aprobar: la única salida era
        radicar. Eso hacía inalcanzable la mitad del trámite que se pidió,
        porque un circuito «a veces se revierte por cambios en el documento y
        se necesitan de nuevo las firmas», y no había forma de introducir esos
        cambios.

        No toca el circuito de firma: reabrir no invalida nada por sí mismo, y
        no debe hacerlo — devolver a borrador y volver a aprobar sin tocar una
        letra deja las aprobaciones intactas, que es lo correcto. Lo que las
        tira es **editar el texto**, porque el disparador de la 030 recalcula
        `cuerpo_sha256` y la cadena deja de casar. La invalidación se deriva del
        hecho, no del acto: si dependiera de que alguien pulse aquí, el caso en
        que se olvida sería exactamente el caso del fraude.
        """
        row = await self._require_editable(borrador_id)
        if row["estado"] != "aprobado":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "estado_invalido",
                    "detail": (
                        f"Solo se devuelve a borrador desde 'aprobado', y este esta "
                        f"en '{row['estado']}'"
                    ),
                    "status": 409,
                },
            )
        return _bo(await self._repo.set_estado(borrador_id, "borrador"))

    async def previsualizar(
        self, borrador_id: uuid.UUID, *, max_clearance: int
    ) -> tuple[bytes, list[str]]:
        """El documento tal como saldrá, SIN persistir nada (RF-PRO-05).

        Es lo que permite ver antes de gastar el número. `*RAD_S*` se queda a la
        vista en la previsualización a propósito: todavía no existe, y sustituirlo
        por un número de ejemplo enseñaría un documento que no es el que se va a
        producir — justo lo contrario de para qué sirve previsualizar.
        """
        row = await self._repo.get_borrador(borrador_id)
        if row is None:
            raise _not_found(f"Borrador {borrador_id} no encontrado")

        contexto = _json_obj(row["combinacion"])
        if not contexto:
            contexto = await ContextoCombinacion(self._conn, self._slug).construir(
                radicado_padre_id=row["radicado_padre_id"],
                max_clearance=max_clearance,
                productor_id=row["creado_por"],
                tenant_client=self._tenant_client,
            )
        resultado = combinar(row["cuerpo"] or "", contexto)
        rendido = await render_documento(
            tenant_slug=self._slug,
            cuerpo_html=resultado.texto,
            pagina=self._pagina(contexto, row, numero=None),
        )
        # A los que sigan sin resolver AHORA se suman los que ya no tenían dato
        # cuando se combinó la plantilla. El cuerpo se guarda ya combinado, así
        # que volver a combinarlo no encuentra ningún marcador que reportar: sin
        # los guardados, esta lista salía siempre vacía y el hueco del oficio
        # llegaba al ciudadano sin que nadie lo hubiera visto como hueco.
        guardados = list(row["faltantes"] or [])
        faltantes = guardados + [f for f in resultado.faltantes if f not in guardados]
        return rendido.contenido, faltantes

    async def radicar(
        self,
        borrador_id: uuid.UUID,
        document_service,
        registered_by: uuid.UUID | None,
        *,
        max_clearance: int,
    ) -> DocumentResponse:
        """Convierte el borrador en radicado real (reutiliza la radicación)."""
        row = await self._repo.get_borrador(borrador_id)
        if row is None:
            raise _not_found(f"Borrador {borrador_id} no encontrado")
        if row["estado"] == "radicado":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "borrador_radicado",
                    "detail": "El borrador ya fue radicado",
                    "status": 409,
                },
            )
        # La aprobación es REQUISITO, y hasta ahora solo la exigía el navegador.
        #
        # El ciclo documentado en la cabecera de este módulo es crear → editar →
        # aprobar → RADICAR, y la pantalla de borradores lo respeta
        # (`canRadicar = estado === "aprobado"`). Pero el servicio solo
        # comprobaba que no estuviera ya radicado, así que un `POST
        # /borradores/{id}/radicar` directo saltaba la aprobación y consumía el
        # número igualmente. Un control que vive solo en el cliente no es un
        # control: el cliente es la parte hostil (D-05).
        #
        # No rompe nada de lo que hoy funciona — la única interfaz que llama a
        # esta ruta no la ofrece sin aprobar — y convierte la marca de
        # aprobación en lo que su nombre dice que es.
        if row["estado"] != "aprobado":
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "borrador_sin_aprobar",
                    "detail": (
                        "El borrador debe aprobarse antes de radicarlo: radicar "
                        "consume un número inmutable."
                    ),
                    "status": 409,
                },
            )
        # Y el circuito abierto tampoco se salta AQUÍ (E23, RF-PRO-09).
        #
        # El gate de `aprobar` no basta, y creerlo suficiente fue un error de
        # análisis: en la pantalla el circuito se abre sobre un borrador **ya
        # aprobado** —es el orden del trámite: se aprueba el texto, se manda a
        # visto bueno, firma quien responde, y entonces sale—. Así que la vía
        # que de verdad se usaba para saltárselo no era aprobar dos veces:
        # era radicar con los turnos sin resolver. Y radicar es peor, porque
        # consume el número inmutable: aprobar de más se deshace devolviendo a
        # edición, un consecutivo gastado no se devuelve nunca.
        #
        # Se comprobó en `tenant_demo`: dos borradores aprobados con su
        # circuito abierto, a un clic de radicarse.
        estado_circuito = await self._gate_circuito_pendiente(borrador_id, acto="radicar")
        # Un circuito CERRADO tampoco basta: hay que comprobar que firmó ESTE texto.
        self._gate_texto_firmado(row, estado_circuito)
        # ── EL ORDEN, y es la mitad del diseño ─────────────────────────────
        #
        #   1. combinar (1.ª pasada)
        #   2. RENDERIZAR                 ← nada irreversible consumido aún
        #   3. subir a storage
        #   4. crear el radicado          ← NÚMERO, irreversible
        #   5. 2.ª pasada *RAD_S* + re-render + subir   (solo si hace falta)
        #   6. anexar
        #
        # El paso 2 va antes del 4 justamente para que un fallo de render NO
        # gaste un consecutivo. Un número no se devuelve: la ley lo hace
        # inmutable, así que un hueco en la secuencia es permanente y hay que
        # explicarlo a un auditor. Mismo orden que usa la radicación de correo:
        # binarios primero, número después.
        contexto = _json_obj(row["combinacion"])
        if not contexto:
            contexto = await ContextoCombinacion(self._conn, self._slug).construir(
                radicado_padre_id=row["radicado_padre_id"],
                max_clearance=max_clearance,
                productor_id=row["creado_por"],
                tenant_client=self._tenant_client,
            )

        documento: _DocumentoProducido | None = None
        cuerpo = row["cuerpo"] or ""
        usa_diferidos = bool(SEGUNDA_PASADA & set(marcadores(cuerpo)))

        if cuerpo.strip():
            documento = await self._producir(cuerpo, contexto, row, numero=None)

        payload = DocumentCreate(
            doc_type=row["doc_type"],
            subject=row["subject"],
            dest_dept=row["dest_dept"],
            observations=row["cuerpo"],
            metadata={"from_borrador": str(borrador_id)},
        )
        result = await document_service.create(payload, registered_by)

        if documento is not None:
            if usa_diferidos:
                # Segunda pasada: el número no existía cuando se combinó. Se
                # vuelve a renderizar porque el documento debe llevar su propio
                # número impreso (Ac. AGN 060/2001 art. 10).
                #
                # **Riesgo residual, declarado**: este render ocurre YA con el
                # número consumido. La primera pasada probó que el motor
                # funciona y que este cuerpo se deja pintar, así que lo único
                # que cambia es una cadena sustituida; pero si aun así fallara,
                # el radicado queda sin documento y hay que reintentar el
                # anexado, no la radicación. Se prefiere eso a pedir el número
                # antes, que dejaría el consecutivo huérfano en el caso mucho
                # más probable de que falle el render inicial.
                contexto = {
                    **contexto,
                    "RAD_S": result.tracking_number,
                    "F_RAD_S": result.registered_at.date().isoformat(),
                }
                try:
                    documento = await self._producir(
                        cuerpo, contexto, row, numero=result.tracking_number
                    )
                except (RenderNoDisponibleError, RenderFallidoError):
                    logger.exception(
                        "El radicado %s se creo pero su documento no se pudo regenerar "
                        "con el numero; queda sin anexo.",
                        result.tracking_number,
                    )
                    documento = None

        if documento is not None:
            await document_service.adjuntar_documento_producido(
                result.id,
                file_id=documento.file_id,
                filename=f"{result.tracking_number}.pdf",
                file_size=documento.file_size,
            )
            await self._repo.set_documento(
                borrador_id,
                file_id=documento.file_id,
                sha256=documento.sha256,
                formato=documento.formato,
                combinacion=contexto,
            )

        await self._repo.set_estado(borrador_id, "radicado", radicado_id=result.id)
        return result

    @staticmethod
    def _pagina(contexto: dict, row, *, numero: str | None) -> dict[str, Any]:
        """Membrete, pie y número (RF-PRO-04; Ac. AGN 060/2001 art. 10).

        Un solo sitio para los dos caminos —previsualizar y radicar—: si la
        previsualización compusiera la página por su cuenta, enseñaría un
        documento distinto del que se produce, que es peor que no previsualizar.
        """
        return {
            "entidad": contexto.get("NOM_E"),
            "dependencia": contexto.get("DEPENDENCIA") or row["dest_dept"],
            "radicado": numero or "",
            "fecha": contexto.get("F_HOY", ""),
            "pie": " · ".join(
                str(contexto[c])
                for c in ("DIR_E", "TEL_E", "MAIL_E", "WEB_E")
                if contexto.get(c)
            ),
        }

    async def _producir(
        self, cuerpo: str, contexto: dict, row, *, numero: str | None
    ) -> _DocumentoProducido:
        """Combina lo que falte, renderiza y sube. No toca la secuencia."""
        resultado = combinar(cuerpo, contexto, diferidos=numero is not None)
        rendido = await render_documento(
            tenant_slug=self._slug,
            cuerpo_html=resultado.texto,
            pagina=self._pagina(contexto, row, numero=numero),
        )
        subido = await upload_artefacto(
            tenant_slug=self._slug,
            filename=f"{numero or 'borrador'}.pdf",
            mime_type="application/pdf",
            data=rendido.contenido,
        )
        return _DocumentoProducido(
            file_id=subido.file_id,
            sha256=subido.sha256,
            file_size=subido.file_size,
            formato=rendido.formato,
        )
