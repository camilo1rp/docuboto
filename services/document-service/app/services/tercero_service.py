# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Terceros y actores de la comunicación — E01, RF-RAD-05.

**La regla que gobierna todo este módulo**: *identidad por referencia, datos de
contacto por copia congelada.*

- La **identidad** (quién es) vive en `tercero` y se comparte entre radicados:
  es lo que permite responder «¿qué le hemos enviado a esta persona?».
- Los **datos de contacto** usados en un envío se congelan en `radicado_actor`.
  Si se leyeran de la ficha, actualizar una dirección cambiaría lo que dicen
  todos los envíos anteriores — información errónea sobre actos hechos con un
  dato que era verdad entonces.

De ahí salen dos comportamientos que parecen detalles y no lo son:

1. Al vincular un actor se admite **corregir el contacto para ese envío** sin
   tocar la ficha. Al radicar se busca a alguien y se le ajusta el correo,
   porque pasa; ese ajuste es lo que se congela.
2. Actualizar la ficha del catálogo es un acto **aparte y explícito**, y no
   arrastra nada hacia atrás.

**Y el canal de envío, que es una condición material y no una preferencia.** Un
destinatario se atiende por correo electrónico, en físico o por los dos, y cada
canal **exige su dato**: sin correo no hay envío electrónico, sin dirección no
hay envío físico. Lo imponen dos `CHECK` de la migración 026 —un envío imposible
no se puede ni registrar—, y aquí se comprueba antes para poder decir **quién**
y **qué falta**, en vez de dejar que suba una violación de restricción que no
nombra a nadie.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit

from app.repositories.tercero_repository import TerceroRepository

logger = logging.getLogger(__name__)

_SERVICE = "document-service"

#: Con qué se verificó la identidad al vincular o confirmar una ficha provisional (C17).
MEDIOS_VERIFICACION = (
    "presencial_documento",
    "respuesta_canal_registrado",
    "llamada_telefono_registrado",
    "otro",
)

# Misma lista que `DocumentService`: consulta real de un usuario, no ruido interno.
_CANALES_AUDITABLES = frozenset({"api", "ui", "mcp"})

# Campos de contacto que se congelan en el envío. Se enumeran una vez: dos
# listas de campos se separan en cuanto alguien añade uno, y la que se quede
# corta congelaría de menos sin que nada avisara.
_CAMPOS_CONTACTO = (
    "nombre",
    "entidad",
    "email",
    "telefono",
    "direccion",
    "municipio",
    "departamento",
    "municipio_codigo",
    "departamento_codigo",
    "pais",
)


class TerceroService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._repo = TerceroRepository(conn, tenant_slug)

    async def buscar(self, texto: str | None, *, tipo: str | None, limit: int) -> list[dict]:
        return [dict(r) for r in await self._repo.buscar(texto, tipo=tipo, limit=limit)]

    async def _resolver_geografia(self, datos: dict) -> dict:
        """Convierte el código DIVIPOLA en los nombres OFICIALES, y **deriva el
        departamento del municipio en lugar de creérselo al cliente**.

        El código de municipio lleva dentro el de su departamento, así que
        aceptar el segundo por separado sería aceptar una contradicción que hay
        que detectar; derivarlo la hace imposible. La base lo comprueba además
        con un CHECK, pero para entonces el error ya no sabe nombrar el
        municipio.

        Si no viene código —una dirección en el extranjero, una ficha anterior
        al catálogo— el texto libre se respeta tal cual: forzar el código
        cerraría la ventanilla por un dato de presentación.
        """
        resuelto = dict(datos)
        codigo_mun = (datos.get("municipio_codigo") or "").strip() or None
        codigo_dep = (datos.get("departamento_codigo") or "").strip() or None

        if codigo_mun:
            municipio = await self._repo.get_municipio(codigo_mun)
            if municipio is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "municipio_no_encontrado",
                        "detail": (
                            f"El código DIVIPOLA '{codigo_mun}' no está en el catálogo."
                        ),
                        "status": 422,
                    },
                )
            departamento = await self._repo.get_departamento(municipio["departamento_codigo"])
            resuelto["municipio_codigo"] = municipio["codigo"]
            resuelto["departamento_codigo"] = municipio["departamento_codigo"]
            # El nombre pasa a ser el CANÓNICO. Es lo que hace que «Bogotá»,
            # «BOGOTA» y «Bogota D.C.» dejen de ser tres municipios para todo
            # lo que lee estas columnas: búsqueda, reportes, índice electrónico.
            resuelto["municipio"] = municipio["nombre"]
            if departamento is not None:
                resuelto["departamento"] = departamento["nombre"]
            return resuelto

        if codigo_dep:
            departamento = await self._repo.get_departamento(codigo_dep)
            if departamento is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "departamento_no_encontrado",
                        "detail": (
                            f"El código DIVIPOLA '{codigo_dep}' no está en el catálogo."
                        ),
                        "status": 422,
                    },
                )
            resuelto["departamento_codigo"] = departamento["codigo"]
            resuelto["departamento"] = departamento["nombre"]
            # Sin municipio no hay municipio: dejar el texto de uno anterior
            # colgando de otro departamento es la incoherencia silenciosa.
            resuelto["municipio_codigo"] = None
            return resuelto

        return resuelto

    async def list_departamentos(self) -> list[dict]:
        return [dict(r) for r in await self._repo.list_departamentos()]

    async def list_municipios(self, departamento_codigo: str, *, texto: str | None) -> list[dict]:
        return [
            dict(r)
            for r in await self._repo.list_municipios(departamento_codigo, texto=texto)
        ]

    async def crear_o_recuperar(self, datos: dict, *, actor: uuid.UUID | None) -> tuple[dict, bool]:
        """Crea el tercero, o devuelve el que ya existe con ese documento.

        **Idempotente a propósito.** Chocar contra el índice único y traducir el
        error obligaría a quien registra a distinguir «ya existe» de «error», y
        lo que quiere es el tercero. Devolver el existente además **evita el
        duplicado por reintento**, que es una de las formas en que el catálogo
        del Orfeo original se llenó de copias.

        El segundo valor dice si se creó o si ya estaba: quien llama necesita
        saberlo para avisar de que se reutilizó una ficha, no para fallar.
        """
        tipo_doc = datos.get("tipo_documento")
        num_doc = datos.get("numero_documento")
        if tipo_doc and num_doc:
            existente = await self._repo.get_por_documento(tipo_doc, num_doc)
            if existente is not None:
                return dict(existente), False

        fila = await self._repo.crear(
            {**(await self._resolver_geografia(datos)), "creado_por": actor}
        )
        await audit.append(
            self._conn,
            tenant_slug=self._tenant_slug,
            service=_SERVICE,
            action="document.tercero_creado",
            object_type="tercero",
            object_ref=str(fila["id"]),
            actor=str(actor) if actor else None,
            payload={"tipo": fila["tipo"], "nombre": fila["nombre"]},
            canal=audit.CANAL_PETICION,
        )
        return dict(fila), True

    async def crear_provisional(self, datos: dict, *, radicado: str) -> dict:
        """Ficha PROVISIONAL del remitente anónimo, atómica con su radicado (C17).

        La llama `DocumentService.create` DENTRO de la transacción del radicado
        (no abre otra): si el radicado falla no queda ficha huérfana. No consulta
        el directorio por documento —un solo camino, sin oráculo— y deja su asiento
        en la misma transacción. El asiento NO lleva el número de documento: el
        `audit_log` es inmutable y el dato personal ya vive en la ficha.
        """
        fila = await self._repo.crear_provisional(datos)
        await audit.append(
            self._conn,
            tenant_slug=self._tenant_slug,
            service=_SERVICE,
            action="document.tercero_provisional_creado",
            object_type="tercero",
            object_ref=str(fila["id"]),
            actor=None,
            payload={"tipo": fila["tipo"], "radicado": radicado},
            canal=audit.CANAL_PETICION,
        )
        return dict(fila)

    # ── Cola y acto de conciliación (C17, ADR-038) ──────────────────────────

    async def por_conciliar(
        self, *, max_clearance: int, limit: int, offset: int
    ) -> tuple[list[dict], int]:
        """La cola de fichas provisionales y su total, con el MISMO clearance."""
        filas = []
        for fila in await self._repo.listar_por_conciliar(
            max_clearance=max_clearance, limit=limit, offset=offset
        ):
            d = dict(fila)
            # El pool no registra códec de `jsonb`: llega como texto (ver `grupos_duplicados`).
            for campo in ("radicados", "candidata"):
                if isinstance(d.get(campo), str):
                    d[campo] = json.loads(d[campo])
            filas.append(d)
        total = await self._repo.contar_por_conciliar(max_clearance=max_clearance)
        return filas, total

    @staticmethod
    def _error(codigo_http: int, error: str, detalle: str) -> HTTPException:
        return HTTPException(
            status_code=codigo_http,
            detail={"error": error, "detail": detalle, "status": codigo_http},
        )

    async def conciliar(
        self,
        tercero_id: uuid.UUID,
        *,
        accion: str,
        destino_id: uuid.UUID | None,
        motivo: str | None,
        medio_verificacion: str | None = None,
        actor: uuid.UUID | None,
        max_clearance: int,
    ) -> dict:
        """Concilia una ficha provisional: vincular, confirmar o descartar.

        Atómico con su asiento. `actor` es `auth_users.id` (el `X-User-Id` que
        FIRMA el gateway), nunca el `sub` de Keycloak; sin actor no se concilia.
        El número del radicado no se toca: ni `radicados` ni `radicado_actor` se
        escriben (el trigger de la 026 lo impide de todos modos).

        Visibilidad: la ficha se lee con el clearance en el `WHERE`; invisible o
        inexistente es 404 (indistinguibles), visible y no pendiente es 409.
        """
        if actor is None:
            raise self._error(403, "forbidden", "Se necesita una identidad para conciliar.")
        motivo = (motivo or "").strip() or None
        if motivo is not None and len(motivo) < 10:
            raise self._error(
                422, "motivo_insuficiente", "El motivo debe tener al menos 10 caracteres."
            )
        if accion == "vincular" and destino_id is None:
            raise self._error(422, "destino_requerido", "Vincular exige la ficha destino.")
        if accion != "vincular" and destino_id is not None:
            raise self._error(
                422, "destino_no_aplica", "Solo vincular lleva una ficha destino."
            )
        if accion == "descartar" and motivo is None:
            raise self._error(
                422, "motivo_requerido", "Descartar exige un motivo de al menos 10 caracteres."
            )
        # Con qué se verificó la identidad: evidencia del acto. Vincular y confirmar la
        # exigen; descartar no verifica nada y no la lleva.
        if accion in ("vincular", "confirmar"):
            if medio_verificacion not in MEDIOS_VERIFICACION:
                raise self._error(
                    422,
                    "medio_verificacion_requerido",
                    "Indique con qué se verificó la identidad: "
                    + ", ".join(MEDIOS_VERIFICACION) + ".",
                )
            if medio_verificacion == "otro" and motivo is None:
                raise self._error(
                    422,
                    "motivo_requerido",
                    "Con medio de verificación «otro» el motivo es obligatorio.",
                )
        elif medio_verificacion is not None:
            raise self._error(
                422, "medio_verificacion_no_aplica", "Descartar no lleva medio de verificación."
            )

        async with self._conn.transaction():
            # Vincular toca DOS fichas: ambas se bloquean juntas y en orden de id (el mismo
            # orden que `fusionar`), así que un destino no puede fusionarse ni darse de
            # baja entre su validación y el UPDATE, y los dos actos no se interbloquean.
            await self._repo.bloquear_fichas(
                [tercero_id, *([destino_id] if destino_id else [])]
            )
            ficha = await self._repo.bloquear_para_conciliar(
                tercero_id, max_clearance=max_clearance
            )
            if ficha is None:
                raise self._error(
                    404, "tercero_no_encontrado", f"No existe el tercero {tercero_id}"
                )
            if ficha["conciliacion"] != "pendiente":
                raise self._error(
                    409,
                    "tercero_ya_conciliado",
                    "Esa ficha no está pendiente de conciliación.",
                )

            destino = None
            coincide: bool | None = None
            declarado = (
                ficha["documento_declarado_tipo"],
                ficha["documento_declarado_numero"],
            )
            if accion == "vincular":
                assert destino_id is not None
                destino = await self._repo.get(destino_id)
                if destino is None:
                    raise self._error(
                        404, "tercero_no_encontrado", f"No existe el tercero {destino_id}"
                    )
                if (
                    destino_id == tercero_id
                    or destino["identidad"] != "confirmada"
                    or destino["fusionado_en"] is not None
                    or not destino["activo"]
                ):
                    raise self._error(
                        409,
                        "destino_invalido",
                        "El destino debe ser una ficha confirmada, viva y distinta de la provisional.",
                    )
                coincide = (destino["tipo_documento"], destino["numero_documento"]) == declarado
                if not coincide and motivo is None:
                    raise self._error(
                        422,
                        "motivo_requerido",
                        "El documento de la ficha destino no coincide con el declarado: "
                        "explique el motivo (al menos 10 caracteres).",
                    )
                fila = await self._repo.conciliar_vincular(
                    tercero_id,
                    destino_id,
                    actor=actor,
                    motivo=motivo,
                    medio=str(medio_verificacion),
                    max_clearance=max_clearance,
                )
            elif accion == "confirmar":
                if not ficha["documento_declarado_tipo"]:
                    raise self._error(
                        422,
                        "documento_sin_tipo",
                        "El remitente no declaró el tipo de documento: no se puede confirmar "
                        "como ficha nueva. Vincúlela o descártela.",
                    )
                try:
                    fila = await self._repo.conciliar_confirmar(
                        tercero_id,
                        actor=actor,
                        motivo=motivo,
                        medio=str(medio_verificacion),
                        max_clearance=max_clearance,
                    )
                except asyncpg.UniqueViolationError:
                    raise self._error(
                        409,
                        "documento_ya_registrado",
                        "Ese documento ya pertenece a otra ficha del directorio: vincule la "
                        "provisional a la existente.",
                    ) from None
                coincide = True
            elif accion == "descartar":
                assert motivo is not None
                fila = await self._repo.conciliar_descartar(
                    tercero_id, actor=actor, motivo=motivo, max_clearance=max_clearance
                )
            else:  # pragma: no cover — el esquema lo restringe a las tres acciones
                raise self._error(422, "accion_invalida", "Acción no reconocida.")
            if fila is None:
                # Perdió una carrera o salió de alcance: nada se tocó. Se RELEE con el
                # clearance en el WHERE: si ya no es visible para el llamante es el 404
                # indistinguible, no un 409 que confirmaría que existe.
                if (
                    await self._repo.bloquear_para_conciliar(
                        tercero_id, max_clearance=max_clearance
                    )
                    is None
                ):
                    raise self._error(
                        404, "tercero_no_encontrado", f"No existe el tercero {tercero_id}"
                    )
                if accion == "vincular":
                    raise self._error(
                        409,
                        "destino_invalido",
                        "El destino ya no es una ficha confirmada, viva y sin fusionar.",
                    )
                raise self._error(
                    409, "tercero_ya_conciliado", "Esa ficha no está pendiente de conciliación."
                )

            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service=_SERVICE,
                action="document.tercero_conciliado",
                object_type="tercero",
                object_ref=str(tercero_id),
                actor=str(actor),
                payload={
                    "accion": accion,
                    "destino_id": str(destino_id) if destino_id else None,
                    # Solo identificadores: el `audit_log` es inmutable y el nombre es un dato
                    # personal que ya vive en la ficha (y se puede rectificar allí).
                    "documento_coincide": coincide,
                    "medio_verificacion": medio_verificacion,
                    "radicados": await self._repo.radicados_de_provisional(tercero_id),
                    "motivo": motivo,
                },
                canal=audit.CANAL_PETICION,
            )
        resultado: dict[str, Any] = {
            "id": tercero_id,
            "accion": accion,
            "conciliacion": {"vincular": "vinculada", "confirmar": "confirmada"}.get(
                accion, "descartada"
            ),
            "destino_id": destino_id,
        }
        return resultado

    # Qué exige cada canal. Se escribe como MAPA y no como una cadena de `if`
    # para que un canal nuevo tenga que declarar su requisito: un `elif` que
    # falte deja pasar el envío imposible en silencio.
    _REQUISITO_CANAL = {
        "email": ("email", "un correo electrónico"),
        "fisico": ("direccion", "una dirección física"),
    }

    @classmethod
    def _falta_para_el_canal(cls, canal: str | None, contacto: dict) -> str | None:
        """Qué dato falta para poder enviar por ese canal, o `None` si no falta.

        `ambos` exige los dos: es la unión, no una alternativa."""
        if not canal:
            return None
        canales = ("email", "fisico") if canal == "ambos" else (canal,)
        for c in canales:
            campo, descripcion = cls._REQUISITO_CANAL[c]
            if not (contacto.get(campo) or "").strip():
                return descripcion
        return None

    async def vincular_actores(
        self,
        radicado_id: uuid.UUID,
        actores: list[dict],
        *,
        actor: uuid.UUID | None,
        provisionales_permitidas: frozenset[uuid.UUID] = frozenset(),
    ) -> list[dict]:
        """Ata los actores al radicado congelando su contacto.

        Una ficha PROVISIONAL (C17) no se puede vincular desde el radicador interno
        —no tiene identidad verificada—: solo la ventanilla pública la ata, a la
        que ella misma acaba de crear en la misma transacción
        (`provisionales_permitidas`). Cualquier otro intento es 422.

        Cada entrada trae `tercero_id`, `rol` y, opcionalmente, **los datos de
        contacto corregidos para ESTE envío**. Lo que no se corrija se toma de
        la ficha vigente; lo que se corrija se congela tal cual, sin tocar el
        catálogo — porque el ajuste vale para este envío y no dice nada sobre
        dónde vive esa persona en general.
        """
        vinculados: list[dict] = []
        for orden, entrada in enumerate(actores):
            tercero = await self._repo.get(entrada["tercero_id"])
            if tercero is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": "tercero_no_encontrado",
                        "detail": f"No existe el tercero {entrada['tercero_id']}",
                        "status": 404,
                    },
                )
            if tercero["identidad"] == "provisional" and (
                entrada["tercero_id"] not in provisionales_permitidas
            ):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "tercero_provisional",
                        "detail": (
                            f"«{tercero['nombre']}» es una ficha provisional (la declaró un "
                            "remitente anónimo) y no se puede vincular: concíliela primero."
                        ),
                        "status": 422,
                    },
                )
            # La ficha vigente rellena lo que no se corrigió; la corrección
            # manda sobre ella. `is not None` y no un `or`: un correo borrado a
            # propósito (`""`) no debe reponerse desde el catálogo.
            contacto = {}
            for campo in _CAMPOS_CONTACTO:
                aportado = entrada.get(campo)
                contacto[campo] = aportado if aportado is not None else tercero[campo]

            # El canal se comprueba contra la copia CONGELADA, que es la que
            # se va a usar: la ficha del catálogo puede tener un correo que
            # aquí no se aportó, y enviar por un dato que no viaja en el
            # radicado sería enviar a un sitio que el radicado no dice.
            # La geografía del contacto congelado se resuelve igual que la de
            # la ficha: si el envío se dirige a un municipio del catálogo, lo
            # que se congela es su nombre oficial y su código, no lo que se
            # tecleó. Lo congelado es lo que se leerá dentro de diez años.
            contacto = await self._resolver_geografia(contacto)

            falta = self._falta_para_el_canal(entrada.get("canal"), contacto)
            if falta is not None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "canal_sin_dato",
                        # Se nombra a QUIÉN y QUÉ falta: un 422 genérico sobre
                        # una lista de veinte destinatarios obliga a buscarlos
                        # de uno en uno.
                        "detail": (
                            f"«{contacto['nombre']}» está marcado para envío "
                            f"'{entrada['canal']}' y no tiene {falta}. Complete el dato "
                            "para este envío o cambie el canal."
                        ),
                        "status": 422,
                    },
                )

            creado = await self._repo.vincular_actor(
                radicado_id,
                entrada["tercero_id"],
                rol=entrada["rol"],
                orden=orden,
                principal=bool(entrada.get("principal")) or orden == 0,
                canal=entrada.get("canal"),
                contacto=contacto,
            )
            vinculados.append({**contacto, "tercero_id": entrada["tercero_id"],
                               "rol": entrada["rol"], "canal": entrada.get("canal"),
                               "nuevo": creado})

        await audit.append(
            self._conn,
            tenant_slug=self._tenant_slug,
            service=_SERVICE,
            action="document.actores_vinculados",
            object_type="radicado",
            object_ref=str(radicado_id),
            actor=str(actor) if actor else None,
            payload={
                "total": len(vinculados),
                # Se anota CUÁNTOS se congelaron con un contacto distinto al de
                # la ficha: es la señal de que alguien corrigió una dirección al
                # radicar, y quien audite querrá poder verlo.
                "con_contacto_corregido": sum(
                    1
                    for v, e in zip(vinculados, actores, strict=True)
                    if any(e.get(c) is not None for c in _CAMPOS_CONTACTO)
                ),
            },
            canal=audit.CANAL_PETICION,
        )
        return vinculados

    async def list_actores(self, radicado_id: uuid.UUID) -> list[dict]:
        return [dict(r) for r in await self._repo.list_actores(radicado_id)]

    async def resolver_por_email(
        self, email: str | None, *, nombre: str | None, actor: uuid.UUID | None
    ) -> dict | None:
        """La ficha del corresponsal de un correo: la existente, o una nueva.

        Devuelve `None` —y el correo se radica sin ficha, como antes— cuando:

        * **no hay dirección**: sin ella no hay nada que reconocer, y crear una
          ficha por cada correo anónimo llenaría el directorio de entradas
          irrepetibles, que es lo contrario de lo que esto persigue;
        * **no hay nombre**: una ficha sin nombre no se puede buscar ni elegir
          después, así que no sirve a nadie;
        * **hay VARIAS fichas con ese correo**: eso es un duplicado sin
          resolver, y elegir una sería decidir en silencio a quién pertenece el
          documento. Lo resuelve una persona en `/admin/terceros`.

        Cuando sí resuelve, el alta **no** pisa los datos de una ficha existente:
        un remitente que cambia la firma de su correo no debe reescribir su
        dirección postal en el catálogo.
        """
        if not (email and email.strip()) or not (nombre and nombre.strip()):
            return None
        candidatas = await self._repo.buscar_por_email(email.strip())
        if len(candidatas) > 1:
            # Varias fichas con el mismo correo: duplicado sin resolver. Ni se
            # elige una —sería decidir en silencio a quién pertenece el
            # documento— ni se crea otra, que era el defecto de la primera
            # versión: empeoraba el problema que venía a resolver.
            return None
        if candidatas:
            return dict(candidatas[0])
        creado, _ = await self.crear_o_recuperar(
            {
                "tipo": "ciudadano",
                "nombre": nombre.strip()[:255],
                "email": email.strip()[:255],
            },
            actor=actor,
        )
        return creado

    # ── Duplicados y fusión ─────────────────────────────────────────────────

    async def grupos_duplicados(self, *, limit: int = 50) -> list[dict]:
        """Fichas que probablemente son la misma persona. Ver el repositorio.

        `fichas` llega como TEXTO y no como lista: el pool de este servicio no
        registra un códec de `jsonb`, así que asyncpg devuelve la cadena cruda.
        Se decodifica aquí y no se da por hecho —`isinstance`, no un
        `json.loads` a secas— porque el día que alguien registre el códec esto
        seguiría funcionando en vez de romperse con un `TypeError`.
        """
        grupos = []
        for fila in await self._repo.grupos_duplicados(limit=limit):
            g = dict(fila)
            if isinstance(g.get("fichas"), str):
                g["fichas"] = json.loads(g["fichas"])
            grupos.append(g)
        return grupos

    async def radicados_de(
        self,
        tercero_id: uuid.UUID,
        *,
        max_clearance: int,
        limit: int = 50,
        user_id: uuid.UUID | None = None,
        canal: str = "system",
    ) -> list[dict]:
        """Radicados de la ficha que el llamante PUEDE ver (no-read-up).

        Deja una traza de lectura agregada (RF-BUS-10) cuando hay un actor real
        y un canal de usuario; best-effort, como la del resto de lecturas: un
        fallo del asiento nunca degrada la consulta a 500.
        """
        filas = [
            dict(r)
            for r in await self._repo.radicados_de(
                tercero_id, limit=limit, max_clearance=max_clearance
            )
        ]
        if user_id is not None and canal in _CANALES_AUDITABLES:
            try:
                await audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="document.tercero_radicados_consultado",
                    object_type="tercero",
                    object_ref=str(tercero_id),
                    actor=str(user_id),
                    canal=canal,
                    payload={"total": len(filas), "limit": limit},
                )
            except Exception:
                logger.warning("audit.append (tercero_radicados_consultado) failed", exc_info=True)
        return filas

    async def fusionar(
        self,
        superviviente_id: uuid.UUID,
        absorbido_id: uuid.UUID,
        *,
        actor: uuid.UUID | None,
    ) -> dict:
        """Consolida dos fichas en una (ver `_fusionar_bloqueado`). **Irreversible.**

        Bloquea las DOS fichas (`FOR UPDATE`, en orden de id) ANTES de validar: así
        una conciliación concurrente que las toque espera, y las guardas se evalúan
        sobre un estado que ya no cambia. El mismo orden de bloqueo que `conciliar`
        impide el deadlock entre ambos actos.
        """
        async with self._conn.transaction():
            await self._repo.bloquear_fichas([superviviente_id, absorbido_id])
            return await self._fusionar_bloqueado(superviviente_id, absorbido_id, actor=actor)

    async def _fusionar_bloqueado(
        self,
        superviviente_id: uuid.UUID,
        absorbido_id: uuid.UUID,
        *,
        actor: uuid.UUID | None,
    ) -> dict:
        """Consolida dos fichas en una. **Irreversible.**

        Cinco guardas, y ninguna es de adorno:

        1. **No fusionar consigo mismo.** Un identificador repetido por error
           marcaría la ficha como fusionada en sí misma y la haría desaparecer
           de la búsqueda sin que nadie lo pidiera.
        2. **Las dos tienen que existir** en este tenant. El repositorio ya
           filtra por schema, así que un id de otra entidad simplemente no
           aparece — y aquí se traduce a 404, no a un fallo interno.
        3. **Ninguna puede estar ya fusionada.** Encadenar fusiones haría que
           el destino de un vínculo dependiera de recorrer una cadena, y una
           cadena con un ciclo colgaría al lector. Se fusiona siempre contra
           una ficha viva.
        4. **Documentos distintos no se fusionan.** Si las dos fichas traen
           número de documento, `ux_tercero_documento` (UNIQUE sobre tipo +
           número entre las fichas vivas) garantiza que no coinciden — y dos
           documentos distintos son dos personas. La base ya afirmaba que
           coincidir en documento es ser la misma ficha; lo que faltaba era usar
           la otra mitad de esa afirmación justo donde el acto no tiene vuelta. Si uno está mal capturado, se
           corrige el documento y después se fusiona — parar es barato,
           destruir una identidad no tiene vuelta.
        5. **Atómico con su asiento**: repunte + marca + `audit.append` en una
           sola transacción. Una fusión sin traza es un cambio en el catálogo de
           una entidad sin autor ni fecha.
        """
        if superviviente_id == absorbido_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "fusion_consigo_mismo",
                    "detail": "Una ficha no puede fusionarse consigo misma",
                    "status": 422,
                },
            )

        superviviente = await self._repo.get(superviviente_id)
        absorbido = await self._repo.get(absorbido_id)
        for ident, ficha in ((superviviente_id, superviviente), (absorbido_id, absorbido)):
            if ficha is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={
                        "error": "tercero_no_encontrado",
                        "detail": f"No existe el tercero {ident}",
                        "status": 404,
                    },
                )
        for ficha in (superviviente, absorbido):
            # Una provisional se concilia (`POST /{id}/conciliar`), no se fusiona: la
            # fusión no deja el motivo ni el asiento de la conciliación, y el hueco
            # de documentos (`doc_sup and doc_abs`) la dejaría pasar siempre.
            if ficha["identidad"] == "provisional":  # type: ignore[index]
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "tercero_provisional",
                        "detail": (
                            f"«{ficha['nombre']}» es una ficha provisional: se concilia "  # type: ignore[index]
                            "desde la cola de fichas por conciliar, no se fusiona."
                        ),
                        "status": 409,
                    },
                )
            if ficha["fusionado_en"] is not None:  # type: ignore[index]
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "tercero_ya_fusionado",
                        "detail": (
                            f"La ficha «{ficha['nombre']}» ya se fusionó en otra; "  # type: ignore[index]
                            "fusiona contra la que quedó viva"
                        ),
                        "status": 409,
                    },
                )

        doc_sup = (superviviente["numero_documento"] or "").strip()  # type: ignore[index]
        doc_abs = (absorbido["numero_documento"] or "").strip()  # type: ignore[index]
        if doc_sup and doc_abs and doc_sup != doc_abs:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "documentos_distintos",
                    "detail": (
                        f"«{superviviente['nombre']}» tiene documento {doc_sup} y "  # type: ignore[index]
                        f"«{absorbido['nombre']}» tiene {doc_abs}: son dos personas "
                        "distintas, no un duplicado. Si uno está mal capturado, "
                        "corrige el documento antes de fusionar"
                    ),
                    "status": 409,
                },
            )

        async with self._conn.transaction():
            heredados = await self._repo.fusionar(
                superviviente_id, absorbido_id, actor=actor
            )
            await audit.append(
                self._conn,
                tenant_slug=self._tenant_slug,
                service="document-service",
                action="document.tercero_fusionado",
                object_type="tercero",
                object_ref=str(superviviente_id),
                actor=str(actor) if actor else None,
                payload={
                    "superviviente": str(superviviente_id),
                    "absorbido": str(absorbido_id),
                    # Los nombres viajan al asiento porque la ficha absorbida
                    # deja de aparecer en cualquier listado: sin esto, quien
                    # audite vería un identificador que no puede resolver.
                    "superviviente_nombre": superviviente["nombre"],  # type: ignore[index]
                    "absorbido_nombre": absorbido["nombre"],  # type: ignore[index]
                    "vinculos_heredados": heredados,
                },
                canal=audit.CANAL_PETICION,
            )
        return {
            "superviviente_id": superviviente_id,
            "absorbido_id": absorbido_id,
            "vinculos_heredados": heredados,
        }
