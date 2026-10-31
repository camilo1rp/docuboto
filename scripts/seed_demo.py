#!/usr/bin/env python3
# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
seed_demo.py — Deja un tenant con un recorrido COMPLETO del sistema, para verlo.

Su público es alguien que acaba de levantar OrpycaMCP y quiere ver qué hace: cómo
se radica, cómo se tipifica, cómo se abre y se cierra un expediente, cómo viaja un
radicado por las dependencias, cómo se prepara una respuesta desde una plantilla y
cómo se firma —por una persona y por varias—. No es un juego de datos de prueba
para los tests: es una demostración navegable.

## Se construye LLAMANDO A LA API, no insertando en la base

Es más lento, y es la única forma honesta. Un seed por SQL directo dejaría
radicados sin su asiento en `public.audit_log` —que es una cadena de hashes y se
verifica entera—, sin sus eventos en Redis y sin entrar en el índice electrónico
del expediente: un estado que el propio sistema **no habría podido producir**. Un
demo cuyo propósito es enseñar cómo funciona el sistema no puede estar hecho de
algo que el sistema no hace. Todo lo que se ve aquí pasó por el gateway, por su
RBAC y por su auditoría, exactamente igual que si lo hubiera hecho una persona.

La única parte que sí es SQL directo es el **aprovisionamiento**: grupos, permisos
y membresías. Eso es administración de la plataforma, no gestión documental — la
misma frontera que respeta `init_tenant.py`.

## Se puede borrar

`--purge` deshace lo que este script crea… hasta donde la ley lo permite. NO se
borran `public.audit_log` (cadena de hashes inmutable, ADR-010) ni `flow_events`
(la base lo impide con un trigger: «append-only, Ley 594/2000»). Los rastros del
demo quedan, y eso **es correcto**: dicen la verdad sobre lo que ocurrió en esa
instalación, y un sistema donde un guion de demostración pudiera borrar la hoja
de ruta de un radicado tendría un defecto grave. Conviene saberlo antes de
sembrar el demo en una instalación que vaya a ser la definitiva.

## Uso

    # el stack tiene que estar levantado
    python scripts/seed_demo.py --gateway http://localhost:19080
    python scripts/seed_demo.py --purge

Nunca se ejecuta solo. Un tenant de demostración que apareciera en una entidad
real sería un incidente, así que no hay ningún gancho automático: se invoca a
mano o no ocurre.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from typing import Any

import asyncpg
import httpx

TENANT = "demo"
USUARIO = "operador"
CLAVE = "orpycamcp_dev"

# Los otros firmantes del realm de desarrollo (`infra/keycloak/realm-dev.json`).
# Existen para que la cadena de firma MÚLTIPLE sea de verdad: una cadena con el
# mismo usuario repetido enseñaría una pantalla que no corresponde a nada.
FIRMANTES = ("jefe", "secretaria", "archivista")

# Keycloak NO reimporta un realm que ya existe: quien levantó el sistema antes de
# que estos usuarios entraran en `realm-dev.json` no los tendría, y el demo
# fallaría con un 401 sin explicar por qué. Se crean por la API de
# administración si faltan — con las credenciales de DESARROLLO que documenta el
# CLAUDE.md, y solo en el realm `orpycamcp`.
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "orpycamcp")
KEYCLOAK_ADMIN = os.getenv("KEYCLOAK_ADMIN", "admin")
KEYCLOAK_ADMIN_PASS = os.getenv("KEYCLOAK_ADMIN_PASSWORD", "orpycamcp_dev")

# Marca con la que se reconoce lo que crea este script, para poder deshacerlo sin
# tocar nada más. Va SOLO en asuntos y descripciones —donde se lee como «esto es
# de demostración»— y nunca en el nombre de una dependencia: ahí rompería el
# flujo (`to_dept` casa por nombre exacto contra el catálogo) y saldría fea en
# cualquier captura de pantalla. Los objetos estructurales se reconocen por su
# código: dependencias 9xx, serie `DEM-100`, tipo `DEM-PET`.
MARCA = "[demo]"

# Códigos altos, poco probables en un catálogo real de dependencias.
DEPENDENCIAS = (
    (901, "VUR", "Ventanilla Única de Radicación"),
    (902, "JUR", "Oficina Jurídica"),
    (903, "ARC", "Gestión Documental y Archivo"),
)

# El demo concede un juego AMPLIO de permisos a un solo usuario, que es lo que
# permite recorrer el sistema entero de una sentada. Eso solo es aceptable en un
# tenant de demostración, así que el slug NO es parametrizable: apuntar este
# script a la entidad de un cliente convertiría a alguien en administrador de
# archivo sin que nadie lo hubiera decidido.
GRUPO_DEMO = "Demostración (todos los permisos)"

# El nombre del grupo dice «todos los permisos», así que los concede todos: la
# lista es el catálogo real de `auth_permissions` **menos `USUA_PERM_ROOT`**.
# Antes eran ocho, y el nombre mentía — con la consecuencia práctica de que
# `/envios`, `/admin/colas`, `/anulaciones`, la publicación de oficio y el
# índice de clasificados quedaban fuera del demo sin que nada lo dijera: la
# pantalla simplemente aparecía vacía, indistinguible de una función que no
# existe.
#
# ROOT queda fuera a propósito. No es un permiso más: salta comprobaciones en
# vez de habilitar una función, y un demo que enseña el sistema desde ROOT no
# enseña el sistema — enseña el sistema sin sus controles.
PERMISOS_DEMO = (
    # Radicación y producción documental
    "PERM_RADI",                # radicar, anexos, borradores
    "PERM_RADI_ENTRADA",        # (declarado; hoy ningún gate lo usa — ver docs)
    "PERM_RADI_SALIDA",         # envíos postales
    "PERM_RADI_INTERNO",        # (declarado; hoy ningún gate lo usa — ver docs)
    "PERM_TIPIF_ANEXO",         # tipificar anexos
    "PERM_BORRAR_ANEXO",        # retirar un anexo
    # Trámite
    "PERM_TRAMITAR",            # asignar y trasladar
    "PERM_VOBO",                # vistos buenos
    "PERM_RESPUESTA_VINCULADA", # responder un radicado con una Salida
    "PERM_INFORMAR",            # informar a terceros
    "PERM_AGENDAR",             # agenda de vencimientos
    "PERM_NRR",                 # radicado sin registro previo
    # Anulación (las tres piezas del circuito: solicitar, aprobar, decidir)
    "PERM_SOL_ANULAR",
    "PERM_ANULAR",
    "PERM_APRO_ANULAR",
    "PERM_PANU_CODI",
    # Expedientes y archivo
    "USUA_PERM_EXPEDIENTE",     # expedientes, vinculación, foliación
    "PERM_CERRAR_EXP",          # cierre de expediente
    "PERM_DESCARGAEXP",         # exportación del expediente
    "USUA_PERM_TRD",            # series TRD/TVD y tipos documentales
    # Clasificación de seguridad (Ley 1712)
    "PERM_RECLASIFICAR",        # reclasificar y publicar de oficio
    # Firma
    "PERM_FIRMA",               # cadenas de firma
    # Administración y observabilidad
    "USUA_PERM_ADMIN",          # dependencias, usuarios, grupos, auditoría
    "PERM_DLQ_ADMIN",           # colas de eventos fallidos
    "SGD_PERM_ESTADISTICA",     # reportes y tablero
    # Conocimiento (ADR-037, ADR-046): además exige que la entidad lo active
    "PERM_CONOCIMIENTO",        # buscar y antecedentes (1) / aceptar una sugerencia (2)
)

# El clearance NO se hereda de los permisos: es un eje aparte (RF-SEG-08) y su
# default es fail-closed a 1 (Pública). Sin esta línea el demo puede *conceder*
# `PERM_RECLASIFICAR` y aun así no ver nunca un documento reservado — y el
# recorrido de clasificación quedaría enseñando una pantalla vacía.
CLEARANCE_DEMO = 3  # 1=Pública 2=Reservada 3=Clasificada

# Avalar y ejecutar un lote de eliminación piden `USUA_PERM_TRD` con crud>=4
# (AREA-06-01): son los actos que habilitan y ejecutan la destrucción, y el nivel
# 3 de las fases reversibles ya no basta. Sin ningún perfil con 4 el recorrido
# de eliminación del demo queda cortado en el aval, y se lee como «la pantalla
# no funciona». Los DOS grupos de gestión lo llevan (`secretaria` y los
# firmantes), así que propone una persona, avala otra y ejecuta una tercera: la
# separación de funciones (quien propone no avala, quien avala no ejecuta) se
# puede recorrer entera. `consulta` NO lo tiene.
#
# Igual `PERM_RECLASIFICAR` (C4, ADR-039): solicitar una desclasificación pide crud 3, pero
# APROBARLA (y con ello bajar el nivel de un radicado clasificado) pide 4. Sin ningún perfil
# con 4 nadie aprueba y la cola de desclasificaciones queda eternamente pendiente.
CRUD_POR_PERMISO_GESTION = {"USUA_PERM_TRD": 4, "PERM_RECLASIFICAR": 4}

# Tercer grupo, con el MISMO juego de permisos y nivel CRUD que el demo pero
# clearance **2**. Existe por una razón concreta: con solo los niveles 1 y 3, el
# no-read-up se comprueba sobre un único salto, y el peldaño intermedio es justo
# donde vive el error de comparación clásico (`>` donde va `>=`). Con `secretaria`
# en 2 hay tres escalones y un documento RESERVADA (nivel 2) que ella SÍ ve y
# `consulta` no, frente a uno CLASIFICADA (nivel 3) que no ve ninguna de las dos.
#
# Es un grupo aparte y no una excepción dentro del demo porque el clearance se
# resuelve por grupo —`MAX(rc.max_level)` entre los del usuario—, así que
# `secretaria` tiene que estar SOLO aquí: dejarla también en el grupo demo le
# devolvería el 3 y el peldaño desaparecería sin que nada lo dijera.
GRUPO_INTERMEDIO = "Gestión (permisos completos, nivel 2)"
USUARIO_INTERMEDIO = "secretaria"
CLEARANCE_INTERMEDIO = 2

# Segundo usuario, deliberadamente pobre. No es un adorno del demo: es el único
# modo de VER que los controles existen. Con un solo usuario que lo puede todo,
# un botón que debería desaparecer y un backend que debería negarse son
# indistinguibles de que no haya control ninguno.
USUARIO_CONSULTA = "consulta"
GRUPO_CONSULTA = "Consulta (solo lectura, nivel 1)"
PERMISOS_CONSULTA = ("PERM_RADI", "USUA_PERM_EXPEDIENTE", "PERM_CONOCIMIENTO")
CRUD_CONSULTA = 1
CLEARANCE_CONSULTA = 1


# ---------------------------------------------------------------------------
# PDF generado aquí y sin dependencias
# ---------------------------------------------------------------------------
def _escapar_pdf(texto: str) -> str:
    """`(`, `)` y `\\` son sintaxis dentro de una cadena PDF.

    Sin esto, un asunto con paréntesis —«Solicitud (urgente)», que en ventanilla
    es de lo más normal— produce un PDF que abre mal o no abre. Un corpus con
    documentos rotos es peor que uno pequeño: enseña fallos que no existen.
    """
    return texto.replace("\\", r"\\\\").replace("(", r"\\(").replace(")", r"\\)")


def pdf_documento(titulo: str, *, paginas: int = 1, lineas_por_pagina: int = 44) -> bytes:
    """Un PDF 1.4 válido de `paginas` páginas con texto de verdad en cada una.

    Se genera en código y no se guarda un binario en el repositorio: un fichero
    de ejemplo versionado es algo que nadie vuelve a mirar y que engorda el clon
    para siempre. Aquí el contenido se ve, se entiende y se cambia editando una
    cadena.

    **Por qué varias páginas.** Un anexo de una línea no ejercita nada: no hay
    paginación en el visor, la descarga es instantánea, el hash se calcula sobre
    cuatro bytes y cualquier problema de tamaño —límites de subida, tiempos de
    render, el PDF/A que storage tiene que admitir— queda fuera del alcance. El
    corpus necesita documentos con peso para que esas rutas se recorran.

    No pretende ser PDF/A — el demo enseña el flujo documental, no la
    conformidad de preservación, que tiene su propio circuito (veraPDF).
    """
    paginas = max(1, paginas)
    catalogo_id, pages_id = 1, 2
    primer_pagina_id = 3
    primer_contenido_id = primer_pagina_id + paginas
    fuente_id = primer_contenido_id + paginas

    contenidos: list[bytes] = []
    for n in range(paginas):
        lineas = [f"BT /F1 14 Tf 72 750 Td ({_escapar_pdf(titulo)}) Tj ET"]
        lineas.append(
            f"BT /F1 9 Tf 72 733 Td (Pagina {n + 1} de {paginas} "
            f"- documento de prueba generado por seed_demo) Tj ET"
        )
        y = 706
        for i in range(lineas_por_pagina):
            # Texto con longitud variable para que las páginas no sean idénticas:
            # un corpus donde todo pesa lo mismo esconde los problemas de tamaño.
            relleno = "referencia " * (2 + ((i + n) % 5))
            lineas.append(
                f"BT /F1 10 Tf 72 {y} Td ({_escapar_pdf(f'{i + 1:02d}. {relleno.strip()}')}) Tj ET"
            )
            y -= 15
            if y < 60:
                break
        flujo = "\n".join(lineas).encode("latin-1", "replace")
        contenidos.append(
            b"<< /Length " + str(len(flujo)).encode() + b" >>\nstream\n" + flujo + b"\nendstream"
        )

    kids = " ".join(f"{primer_pagina_id + n} 0 R" for n in range(paginas))
    objetos: list[bytes] = [
        f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode(),
        f"<< /Type /Pages /Kids [{kids}] /Count {paginas} >>".encode(),
    ]
    for n in range(paginas):
        objetos.append(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {fuente_id} 0 R >> >> "
            f"/Contents {primer_contenido_id + n} 0 R >>".encode()
        )
    objetos.extend(contenidos)
    objetos.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    salida = bytearray(b"%PDF-1.4\n")
    desplazamientos = []
    for i, cuerpo in enumerate(objetos, start=1):
        desplazamientos.append(len(salida))
        salida += f"{i} 0 obj\n".encode() + cuerpo + b"\nendobj\n"
    inicio_xref = len(salida)
    salida += f"xref\n0 {len(objetos) + 1}\n".encode()
    salida += b"0000000000 65535 f \n"
    for d in desplazamientos:
        salida += f"{d:010d} 00000 n \n".encode()
    salida += (
        f"trailer\n<< /Size {len(objetos) + 1} /Root {catalogo_id} 0 R >>\n"
        f"startxref\n{inicio_xref}\n"
    ).encode()
    salida += b"%%EOF\n"
    return bytes(salida)


def pdf_minimo(texto: str) -> bytes:
    """Compatibilidad: una página, como antes. Ver `pdf_documento`."""
    return pdf_documento(texto, paginas=1, lineas_por_pagina=1)


# ---------------------------------------------------------------------------
# Salida legible: quien corre esto quiere ver QUÉ se creó, no un log
# ---------------------------------------------------------------------------
_paso = 0


def paso(titulo: str) -> None:
    global _paso
    _paso += 1
    print(f"\n\033[1m{_paso}. {titulo}\033[0m")


def ok(detalle: str) -> None:
    print(f"   ✓ {detalle}")


def aviso(detalle: str) -> None:
    print(f"   ! {detalle}")


class Fallo(Exception):
    """Un paso no se pudo completar. Se aborta: un demo a medias engaña más que
    ninguno, porque quien lo mira no sabe qué falta."""


class Api:
    """Cliente del gateway que habla como una persona: con su token y su tenant."""

    def __init__(self, base: str) -> None:
        self._base = base.rstrip("/")
        self._cliente: httpx.AsyncClient | None = None
        self._token = ""

    async def __aenter__(self) -> Api:
        self._cliente = httpx.AsyncClient(base_url=self._base, timeout=60.0)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._cliente is not None:
            await self._cliente.aclose()

    @property
    def cabeceras(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "X-Tenant-Slug": TENANT}

    async def autenticar(self) -> str:
        resp = await self._cliente.post(  # type: ignore[union-attr]
            "/api/v1/auth/token",
            json={"username": USUARIO, "password": CLAVE, "tenant_slug": TENANT},
        )
        if resp.status_code != 200:
            raise Fallo(
                f"No se pudo autenticar como «{USUARIO}» ({resp.status_code}). "
                "¿Está el stack levantado y aprovisionado el tenant «demo»?"
            )
        self._token = resp.json()["access_token"]
        me = await self.get("/api/v1/auth/me")
        return str(me.get("user_id") or "")

    async def _pedir(self, metodo: str, ruta: str, **kw: Any) -> httpx.Response:
        """Una petición, esperando si el gateway pide calma.

        El seed hace decenas de llamadas seguidas y el gateway limita la tasa: un
        429 no es un fallo del demo, es el sistema funcionando. Abortar ahí
        dejaría el recorrido a medias por algo que se resuelve esperando.
        """
        extra = kw.pop("headers", {})
        renovado = False
        for intento in range(5):
            cabeceras = {**self.cabeceras, **extra}
            r = await self._cliente.request(metodo, ruta, headers=cabeceras, **kw)  # type: ignore[union-attr]
            # El token de Keycloak caduca en minutos y una siembra con volumen
            # dura más que eso: sin renovarlo, el guion abortaba a mitad con un
            # 401 que parecía un problema de permisos y era un reloj. Se renueva
            # UNA vez por petición —si el token recién pedido tampoco vale, el
            # problema no es la caducidad y reintentar en bucle solo lo esconde.
            if r.status_code == 401 and not renovado:
                renovado = True
                aviso("el token caducó a mitad de la siembra: renovando sesión")
                await self.autenticar()
                continue
            if r.status_code != 429:
                return r
            # Crece rápido: la ventana del limitador es de minutos, no de
            # segundos, y reintentar cada dos segundos solo gasta la cuota.
            espera = 5 * (2**intento)
            aviso(f"el gateway pide calma (429): esperando {espera}s")
            await asyncio.sleep(espera)
        return r

    async def get(self, ruta: str, *, opcional: bool = False, **kw: Any) -> Any:
        """Lee. Un fallo ABORTA salvo que se declare opcional.

        La primera versión devolvía `{}` ante cualquier respuesta que no fuera
        200, y el paso que consultaba el marco de alcance no imprimió nada
        cuando mcp-server no estaba levantado: ni el dato ni el motivo. Un
        script de demostración que calla un fallo es peor que uno que aborta,
        porque quien lo mira cree haber visto el sistema entero.
        """
        r = await self._pedir("GET", ruta, **kw)
        if r.status_code == 200:
            return r.json()
        if opcional:
            return None
        raise Fallo(f"GET {ruta} → {r.status_code}: {r.text[:300]}")

    async def post(self, ruta: str, *, esperado: tuple[int, ...] = (200, 201), **kw: Any) -> Any:
        r = await self._pedir("POST", ruta, **kw)
        if r.status_code not in esperado:
            raise Fallo(f"POST {ruta} → {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    async def put(self, ruta: str, *, esperado: tuple[int, ...] = (200, 201), **kw: Any) -> Any:
        r = await self._pedir("PUT", ruta, **kw)
        if r.status_code not in esperado:
            raise Fallo(f"PUT {ruta} → {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}

    async def patch(self, ruta: str, *, esperado: tuple[int, ...] = (200,), **kw: Any) -> Any:
        r = await self._pedir("PATCH", ruta, **kw)
        if r.status_code not in esperado:
            raise Fallo(f"PATCH {ruta} → {r.status_code}: {r.text[:300]}")
        return r.json() if r.content else {}


#: Política de tratamiento de datos de la entidad DEMO (B2, ADR-031). Sin ella la
#: ventanilla pública responde 409 `politica_no_publicada` (Ley 1581/2012 art. 12):
#: una entidad real redacta la suya; esta es solo de demostración y lo dice.
POLITICA_DEMO = {
    "politica_tratamiento_version": "demo-2026-10",
    "politica_tratamiento_url": "http://localhost:13000/politica-de-tratamiento-demo",
    "politica_tratamiento_texto": (
        "AVISO DE PRIVACIDAD (DEMOSTRACION). Los datos que usted entrega se usan "
        "únicamente para radicar y tramitar su solicitud en esta entidad de prueba. "
        "Puede conocer, actualizar o rectificar sus datos y presentar reclamos; la "
        "supresión no procede mientras exista el deber legal de conservar el "
        "documento (Ley 594/2000, TRD). Esta política es de ejemplo: cada entidad "
        "debe publicar la suya."
    ),
}


def _dsn() -> str:
    """DSN de asyncpg. Acepta el `postgresql+asyncpg://` de los servicios."""
    crudo = os.getenv("DATABASE_URL", "postgresql://orpycamcp:orpycamcp@postgres:5432/orpycamcp_db")
    return crudo.replace("postgresql+asyncpg://", "postgresql://")


async def _crear_en_keycloak(username: str) -> bool:
    """Crea el usuario en Keycloak si no está. `True` si quedó creado.

    Solo tiene sentido en desarrollo, y por eso usa las credenciales de
    desarrollo: en una instalación real los usuarios los crea la entidad, no un
    guion de demostración.
    """
    async with httpx.AsyncClient(base_url=KEYCLOAK_URL, timeout=30.0) as kc:
        tok = await kc.post(
            "/realms/master/protocol/openid-connect/token",
            data={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": KEYCLOAK_ADMIN,
                "password": KEYCLOAK_ADMIN_PASS,
            },
        )
        if tok.status_code != 200:
            return False
        cab = {"Authorization": f"Bearer {tok.json()['access_token']}"}

        # Keycloak 24+ descarta en silencio los atributos que el «user profile»
        # no declara, y `tenant_slug` es uno de ellos: el usuario se crea, el
        # atributo desaparece, y entrar falla luego con `tenant_mismatch` sin
        # que nada mencione el atributo perdido. (Los usuarios del
        # `realm-dev.json` sí lo tienen porque la importación no pasa por ese
        # filtro.) Se habilita la política de atributos no gestionados antes de
        # crear a nadie.
        perfil = await kc.get(f"/admin/realms/{KEYCLOAK_REALM}/users/profile", headers=cab)
        if perfil.status_code == 200:
            cfg = perfil.json()
            if cfg.get("unmanagedAttributePolicy") != "ENABLED":
                cfg["unmanagedAttributePolicy"] = "ENABLED"
                await kc.put(
                    f"/admin/realms/{KEYCLOAK_REALM}/users/profile", headers=cab, json=cfg
                )
        # `firstName`/`lastName` y `requiredActions: []` NO son adorno: sin ellos
        # Keycloak deja la cuenta con acciones pendientes y responde
        # «Account is not fully set up» al primer intento de entrar — un 400 que
        # no menciona en ningún sitio que falte el apellido.
        cuerpo = {
            "username": username,
            "email": f"{username}@demo.orpycamcp.local",
            "emailVerified": True,
            "firstName": username.capitalize(),
            "lastName": "Demo",
            "enabled": True,
            "requiredActions": [],
            "attributes": {"tenant_slug": [TENANT]},
            "credentials": [{"type": "password", "value": CLAVE, "temporary": False}],
        }
        alta = await kc.post(f"/admin/realms/{KEYCLOAK_REALM}/users", headers=cab, json=cuerpo)
        if alta.status_code == 201:
            return True
        if alta.status_code != 409:
            return False

        # Ya existía pero no podía entrar: se completa en vez de rendirse. Es el
        # caso de quien probó una versión anterior de este guion y se quedó con
        # una cuenta a medio crear.
        busca = await kc.get(
            f"/admin/realms/{KEYCLOAK_REALM}/users",
            headers=cab,
            params={"username": username, "exact": "true"},
        )
        filas = busca.json() if busca.status_code == 200 else []
        if not filas:
            return False
        uid = filas[0]["id"]
        arreglo = await kc.put(
            f"/admin/realms/{KEYCLOAK_REALM}/users/{uid}", headers=cab, json=cuerpo
        )
        return arreglo.status_code in (204, 200)


async def _asegurar_usuario(api_base: str, username: str) -> None:
    """Provoca que el auth-service aprovisione al usuario en el tenant.

    `auth_users` la escribe el auth-service al validar el PRIMER token de cada
    persona, así que un usuario del realm que nunca ha entrado no existe en el
    esquema del tenant y no se le puede conceder nada. En vez de insertar la
    fila por detrás —donde es fácil que el id no case con el de Keycloak y el
    fallo aparezca semanas después— se pide un token en su nombre, que es
    exactamente lo que haría esa persona al entrar.
    """
    async with httpx.AsyncClient(base_url=api_base, timeout=30.0) as c:
        cuerpo = {"username": username, "password": CLAVE, "tenant_slug": TENANT}
        r = await c.post("/api/v1/auth/token", json=cuerpo)
        if r.status_code != 200 and await _crear_en_keycloak(username):
            aviso(f"«{username}» no estaba en Keycloak: creado")
            r = await c.post("/api/v1/auth/token", json=cuerpo)
        if r.status_code != 200:
            raise Fallo(
                f"No se pudo entrar como «{username}» ({r.status_code}), ni crearlo "
                f"en Keycloak. Comprueba que Keycloak responde en {KEYCLOAK_URL} y "
                "que las credenciales de administración de desarrollo son las del "
                "docker-compose."
            )
        me = await c.get(
            "/api/v1/auth/me",
            headers={
                "Authorization": f"Bearer {r.json()['access_token']}",
                "X-Tenant-Slug": TENANT,
            },
        )
        if me.status_code != 200:
            raise Fallo(f"«{username}» entró pero /auth/me devolvió {me.status_code}")


async def _montar_grupo(
    conn: Any,
    *,
    nombre: str,
    permisos: tuple[str, ...],
    crud: int,
    clearance: int,
    miembros: tuple[str, ...],
    crud_por_permiso: dict[str, int] | None = None,
) -> int:
    """Crea (o converge) un grupo con sus permisos, su clearance y su gente.

    Los tres ejes se fijan JUNTOS a propósito, porque son independientes y
    olvidar uno no se nota: el permiso dice *qué función*, el `crud` dice *hasta
    qué acto* dentro de ella, y el clearance dice *hasta qué nivel de reserva*.
    Un grupo con `PERM_RECLASIFICAR` y clearance 1 puede pulsar el botón de
    reclasificar y no ver jamás un documento reservado — el default de
    `role_clearance` es fail-closed y no hay fila, así que la omisión es
    silenciosa y se lee como «la pantalla no funciona».
    """
    faltan = [
        p
        for p in permisos
        if not await conn.fetchval("SELECT 1 FROM auth_permissions WHERE nombre = $1", p)
    ]
    if faltan:
        # No se sigue: sin estos permisos el recorrido aborta más adelante y con
        # un mensaje peor. Si faltan, es que hay migraciones sin aplicar.
        raise Fallo(
            f"Estos permisos no existen en el tenant: {faltan}. "
            "Corre las migraciones de auth-service en este tenant."
        )

    grupo_id = await conn.fetchval(
        "INSERT INTO auth_groups (name) VALUES ($1) "
        "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        nombre,
    )
    concedidos = await conn.fetchval(
        """
        WITH p AS (SELECT id FROM auth_permissions WHERE nombre = ANY($2::text[]))
        INSERT INTO auth_group_permissions (group_id, permission_id, crud)
        SELECT $1, p.id, $3 FROM p
        ON CONFLICT (group_id, permission_id) DO UPDATE SET crud = EXCLUDED.crud
        RETURNING (SELECT count(*) FROM p)
        """,
        grupo_id,
        list(permisos),
        crud,
    )
    # Excepciones de nivel por permiso: el resto del grupo queda en `crud`.
    for permiso, nivel in (crud_por_permiso or {}).items():
        await conn.execute(
            "UPDATE auth_group_permissions SET crud = $3 WHERE group_id = $1 "
            "AND permission_id = (SELECT id FROM auth_permissions WHERE nombre = $2)",
            grupo_id,
            permiso,
            nivel,
        )
    await conn.execute(
        "INSERT INTO role_clearance (group_id, max_level) VALUES ($1, $2) "
        "ON CONFLICT (group_id) DO UPDATE SET max_level = EXCLUDED.max_level",
        grupo_id,
        clearance,
    )
    filas = await conn.fetch(
        "SELECT id, username FROM auth_users WHERE upper(username) = ANY($1::text[])",
        [u.upper() for u in miembros],
    )
    # Un grupo montado con CERO miembros es el fallo silencioso de este script:
    # el SQL funciona, el grupo existe con todos sus permisos, y no se lo aplica
    # a nadie. Se nota sobre todo en `consulta`, cuyo trabajo entero es ser el
    # control negativo: sin membresía no tendría permiso NINGUNO, el barrido
    # vería desaparecer todos los botones, y pasaría en verde sin haber probado
    # el control que dice probar.
    encontrados = {str(f["username"]).upper() for f in filas}
    ausentes = sorted(u for u in miembros if u.upper() not in encontrados)
    if ausentes:
        raise Fallo(
            f"No están en auth_users y por tanto se quedarían fuera de «{nombre}»: "
            f"{ausentes}. Deberían haberse aprovisionado al pedir su token; si esto "
            "salta, el aprovisionamiento previo no hizo lo que dice."
        )
    await conn.executemany(
        "INSERT INTO auth_memberships (user_id, group_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        [(fila["id"], grupo_id) for fila in filas],
    )
    # Y quitar a quien ya NO debe estar. Sin esto la pertenencia es aditiva y el
    # guion converge a un estado distinto según la historia de la instalación,
    # que es lo contrario de idempotente: dejar de incluir a alguien en la lista
    # no lo saca del grupo, así que su clearance —que se resuelve como el MÁXIMO
    # de sus grupos— sigue siendo el viejo. Pasó al mover `secretaria` al grupo
    # de nivel 2: se quedó también en el de nivel 3 y el peldaño intermedio que
    # ese grupo existe para crear no llegó a existir, sin que nada lo dijera.
    #
    # Se acota a los usuarios que ESTE guion administra: sacar a alguien que una
    # persona hubiera añadido a mano sería un borrado que nadie pidió.
    administrados = [u.upper() for u in (USUARIO, *FIRMANTES, USUARIO_CONSULTA)]
    sobrantes = await conn.fetch(
        "SELECT m.user_id, u.username FROM auth_memberships m "
        "JOIN auth_users u ON u.id = m.user_id "
        "WHERE m.group_id = $1 AND upper(u.username) = ANY($2::text[]) "
        "AND upper(u.username) <> ALL($3::text[])",
        grupo_id,
        administrados,
        [u.upper() for u in miembros],
    )
    if sobrantes:
        await conn.executemany(
            "DELETE FROM auth_memberships WHERE user_id = $1 AND group_id = $2",
            [(f["user_id"], grupo_id) for f in sobrantes],
        )
        aviso(
            f"sacados de «{nombre}»: {', '.join(str(f['username']) for f in sobrantes)} "
            "(ya no figuran entre sus miembros)"
        )
    return int(concedidos or 0)


async def aprovisionar(api_base: str) -> None:
    """Grupos, permisos y membresía: la ÚNICA parte que va por SQL.

    Es administración de la plataforma, no gestión documental — la misma
    frontera que respeta `init_tenant.py`. El resto del demo pasa por la API
    precisamente porque produce documentos, y esos tienen que nacer con su
    auditoría y su índice.

    El usuario se toma de `auth_users`, donde el auth-service lo aprovisiona al
    entrar por primera vez; si no está, se dice qué hacer en vez de crearlo por
    detrás y arriesgar una identidad que no case con la de Keycloak.
    """
    paso("Aprovisionando usuarios y permisos (única parte por SQL: es administración)")
    # Antes que el SQL: que existan. Cada uno entra una vez y el auth-service
    # crea su fila con el id que le corresponde.
    for quien in (USUARIO, *FIRMANTES, USUARIO_CONSULTA):
        await _asegurar_usuario(api_base, quien)
    ok(
        f"{2 + len(FIRMANTES)} usuarios aprovisionados: {USUARIO}, "
        f"{', '.join(FIRMANTES)}, {USUARIO_CONSULTA}"
    )

    conn = await asyncpg.connect(_dsn())
    try:
        esquema = f"tenant_{TENANT}"
        await conn.execute(f'SET search_path TO "{esquema}"')

        # `upper(...)` en los dos lados: el auth-service guarda el nombre en
        # MAYÚSCULAS (convención heredada de Orfeo), así que una comparación
        # exacta contra lo que teclea la persona no encuentra nunca la fila —y
        # el mensaje de error habría culpado al aprovisionamiento.
        usuario = await conn.fetchrow(
            "SELECT id, username FROM auth_users WHERE upper(username) = upper($1)", USUARIO
        )
        if usuario is None:
            raise Fallo(
                f"El usuario «{USUARIO}» no está aprovisionado en {esquema}.auth_users. "
                f"Entra una vez en la interfaz con «{USUARIO}» y vuelve a ejecutar esto: "
                "el auth-service lo crea al validar su primer token, y así su id "
                "coincide con el de Keycloak."
            )

        # Los dos grupos se montan con el MISMO código. Que el grupo pobre no
        # sea un caso especial es justamente lo que lo hace creíble: si se
        # provisionara por otra vía, un fallo del provisionamiento podría
        # dejarlo sin permisos y el barrido negativo saldría verde por la razón
        # equivocada — «el botón no está» porque no hay usuario, no porque el
        # control funcione.
        n_demo = await _montar_grupo(
            conn,
            nombre=GRUPO_DEMO,
            permisos=PERMISOS_DEMO,
            crud=3,
            crud_por_permiso=CRUD_POR_PERMISO_GESTION,
            clearance=CLEARANCE_DEMO,
            # Todos los firmantes al mismo grupo: este demo enseña QUÉ hace el
            # sistema, no cómo repartir competencias entre cargos. Un reparto de
            # roles realista es otra demostración, y probablemente otra decisión
            # de cada entidad.
            miembros=(USUARIO, *(f for f in FIRMANTES if f != USUARIO_INTERMEDIO)),
        )
        ok(
            f"«{GRUPO_DEMO}» con {n_demo} permisos, nivel CRUD 3 (TRD en 4), "
            f"clearance {CLEARANCE_DEMO}; «{USUARIO}» dentro"
        )

        n_med = await _montar_grupo(
            conn,
            nombre=GRUPO_INTERMEDIO,
            permisos=PERMISOS_DEMO,
            crud=3,
            crud_por_permiso=CRUD_POR_PERMISO_GESTION,
            clearance=CLEARANCE_INTERMEDIO,
            miembros=(USUARIO_INTERMEDIO,),
        )
        ok(
            f"«{GRUPO_INTERMEDIO}» con {n_med} permisos, nivel CRUD 3 (TRD en 4), "
            f"clearance {CLEARANCE_INTERMEDIO}; «{USUARIO_INTERMEDIO}» dentro "
            "(el peldaño intermedio del no-read-up)"
        )

        n_cons = await _montar_grupo(
            conn,
            nombre=GRUPO_CONSULTA,
            permisos=PERMISOS_CONSULTA,
            crud=CRUD_CONSULTA,
            clearance=CLEARANCE_CONSULTA,
            miembros=(USUARIO_CONSULTA,),
        )
        ok(
            f"«{GRUPO_CONSULTA}» con {n_cons} permisos, nivel CRUD {CRUD_CONSULTA}, "
            f"clearance {CLEARANCE_CONSULTA}; «{USUARIO_CONSULTA}» dentro"
        )
    finally:
        await conn.close()


async def principal(gateway: str) -> int:
    await aprovisionar(gateway)
    async with Api(gateway) as api:
        paso("Entrando al sistema como haría una persona")
        user_id = await api.autenticar()
        ok(f"autenticado «{USUARIO}» en «{TENANT}» (id {user_id[:8]}…)")

        creado = await sembrar(api, user_id)
        resumen(creado)
    return 0


async def crear_o_recuperar(
    api: Api, ruta: str, cuerpo: dict[str, Any], *, buscar_en: str, clave: str, valor: Any
) -> dict[str, Any]:
    """Crea el objeto; si ya existe (409), RECUPERA el que hay.

    Un seed tiene que poder ejecutarse dos veces. La primera versión aceptaba el
    409 como «bien» y seguía usando el CUERPO DEL ERROR como si fuera el objeto
    creado —`serie["id"]` sobre `{"error": "conflict"}`—, así que la segunda
    ejecución reventaba con un `KeyError` que no decía nada del problema real.
    Aceptar un conflicto no es lo mismo que resolverlo.
    """
    r = await api._pedir("POST", ruta, json=cuerpo)
    if r.status_code in (200, 201):
        return r.json()
    if r.status_code != 409:
        raise Fallo(f"POST {ruta} → {r.status_code}: {r.text[:300]}")
    existentes = await api.get(buscar_en)
    filas = existentes if isinstance(existentes, list) else existentes.get("items", [])
    for fila in filas:
        if fila.get(clave) == valor:
            return fila
    raise Fallo(
        f"{ruta} dice que «{valor}» ya existe (409) pero no aparece en {buscar_en}. "
        "El conflicto es de otra cosa, o el listado no lo muestra."
    )


async def ids_de(api: Api, usuarios: tuple[str, ...]) -> list[str]:
    """Los ids de esos usuarios, tal como los conoce el sistema.

    Se piden al selector que ya existe (`/auth/users/pickable`, la proyección
    mínima `{id, username}` pensada justo para esto) en vez de leer la base: el
    id que vale es el que el backend usará al comprobar quién firma.
    """
    filas = await api.get("/api/v1/auth/users/pickable", params={"size": 200})
    por_nombre = {
        str(f.get("username", "")).upper(): f["id"]
        for f in (filas if isinstance(filas, list) else filas.get("items", []))
    }
    faltan = [u for u in usuarios if u.upper() not in por_nombre]
    if faltan:
        raise Fallo(f"no aparecen en el selector de usuarios: {faltan}")
    return [por_nombre[u.upper()] for u in usuarios]


async def buscar_o_crear(
    api: Api, ruta: str, cuerpo: dict[str, Any], *, buscar_en: str, clave: str
) -> tuple[dict[str, Any], bool]:
    """Para objetos SIN restricción de unicidad: se busca ANTES de crear.

    `crear_o_recuperar` se apoya en el 409, y eso solo funciona donde la base
    impone unicidad. El expediente y la plantilla no la tienen —dos expedientes
    pueden llamarse igual, y con razón—, así que la segunda ejecución del demo
    creaba un duplicado sin que nada avisara. Devuelve `(objeto, ya_estaba)`.
    """
    existentes = await api.get(buscar_en)
    filas = existentes if isinstance(existentes, list) else (
        existentes.get("items") or existentes.get("hits") or existentes.get("results") or []
    )
    for fila in filas:
        if fila.get(clave) == cuerpo.get(clave):
            return fila, True
    return await api.post(ruta, json=cuerpo), False


async def buscar_radicado(api: Api, asunto: str) -> dict[str, Any] | None:
    """El radicado del demo, si ya se sembró. `None` si no."""
    resp = await api.get("/api/v1/search", params={"q": asunto, "size": 5})
    # `hits`, que es como lo llama `/api/v1/search`. La primera versión probaba
    # `items`/`results`/`documents` —tres nombres plausibles y ninguno el bueno—
    # y no encontraba nada nunca: cada ejecución creaba otro radicado y consumía
    # otro número inmutable, en silencio. Adivinar la forma de una respuesta es
    # como se escriben los fallos que no fallan.
    filas = resp.get("hits") or []
    for fila in filas:
        if fila.get("subject") == asunto:
            return fila
    return None


async def sembrar(api: Api, user_id: str) -> dict[str, Any]:
    """El recorrido. Cada fase deja algo que se puede abrir en la interfaz."""
    creado: dict[str, Any] = {}

    # ── Instrumentos: sin TRD no hay retención, y sin dependencias no hay flujo ──
    paso("Instrumentos archivísticos: dependencias y TRD")
    creado["dependencias"] = []
    for codigo, sigla, nombre in DEPENDENCIAS:
        d = await crear_o_recuperar(
            api,
            "/api/v1/dependencias",
            {"codigo": codigo, "sigla": sigla, "nombre": nombre},
            buscar_en="/api/v1/dependencias",
            clave="codigo",
            valor=codigo,
        )
        creado["dependencias"].append(d)
    ok(f"{len(creado['dependencias'])} dependencias (ventanilla, jurídica, archivo)")

    # ── Política de tratamiento (Ley 1581): sin ella la ventanilla no radica ──
    paso("Política de tratamiento de datos de la entidad demo")
    for clave, valor in POLITICA_DEMO.items():
        await api.put(f"/api/v1/config/params/{clave}", json={"value": valor})
    ok(f"política «{POLITICA_DEMO['politica_tratamiento_version']}» publicada (aviso de demostración)")

    # ── Conocimiento: las entidades nuevas nacen APAGADAS (ADR-037) ──
    # Se enciende por la API, como lo haría su administrador: es el camino de opt-in
    # que el demo tiene que ejercitar, no un atajo por SQL. Va después de publicar la
    # política, porque encender exige política publicada y la declaración.
    paso("Búsqueda y antecedentes de la entidad demo")
    await api.put(
        "/api/v1/config/asistente", json={"habilitado": True, "declaracion_transmision": True}
    )
    ok("conocimiento activado por la API (PUT /config/asistente)")

    # ── URD: quién trabaja DESDE dónde ──
    #
    # `urd_usuaroldep` (usuario–rol–dependencia) es el concepto de Orfeo que
    # dice desde qué unidad radica y tramita cada persona. Sin una fila aquí el
    # sistema funciona a medias y **de forma silenciosa**: la tarjeta
    # «Dependencia activa» del perfil sale vacía, y sobre todo la bandeja de
    # trámite aparece en «vas al día» aunque haya radicados asignados — porque
    # se filtra por la dependencia de quien mira, y esa persona no tiene
    # ninguna. Se lee como «el sistema no reparte trabajo», que es justo lo
    # contrario de lo que el demo quiere enseñar.
    #
    # El reparto es lo que hace visible el flujo ENTRE dependencias: quien
    # radica está en ventanilla y quien recibe está en jurídica, así que el
    # traslado del paso 6 va de verdad de una unidad a otra.
    reparto = (
        (USUARIO, 901, GRUPO_DEMO),        # ventanilla: radica
        ("jefe", 902, GRUPO_DEMO),         # jurídica: recibe y da visto bueno
        # Su URD apunta al grupo INTERMEDIO, que es el único al que pertenece:
        # el rol de la URD tiene que ser un grupo suyo o la dependencia activa
        # quedaría colgando de uno que no le da nada.
        (USUARIO_INTERMEDIO, 902, GRUPO_INTERMEDIO),  # jurídica: proyecta la respuesta
        ("archivista", 903, GRUPO_DEMO),   # archivo: expedientes y transferencias
        (USUARIO_CONSULTA, 902, GRUPO_CONSULTA),
    )
    grupos = await api.get("/api/v1/auth/groups", params={"size": 200})
    por_nombre = {
        g["name"]: g["id"] for g in (grupos if isinstance(grupos, list) else grupos.get("items", []))
    }
    nombres = tuple(n for n, _, _ in reparto)
    ids = await ids_de(api, nombres)
    for (_quien, depe, grupo), user_id in zip(reparto, ids, strict=True):
        if grupo not in por_nombre:
            raise Fallo(f"el grupo «{grupo}» no aparece en /auth/groups; ¿falló el aprovisionamiento?")
        await api.post(
            f"/api/v1/auth/users/{user_id}/urd",
            json={"depe_id": depe, "rol_id": por_nombre[grupo], "es_principal": True},
            # 409: ya estaba asignado de una ejecución anterior. Es convergencia,
            # no un fallo — la restricción única (user_id, depe_id) la garantiza
            # la base, no una comprobación previa que podría competir consigo misma.
            esperado=(201, 409),
        )
    ok(
        f"{len(reparto)} asignaciones usuario–dependencia: ventanilla {USUARIO}; "
        f"jurídica jefe/secretaria/{USUARIO_CONSULTA}; archivo archivista"
    )

    serie = await crear_o_recuperar(
        api,
        "/api/v1/trd",
        {
            "code": "DEM-100",
            "name": f"Derechos de Petición {MARCA}",
            "description": "Serie de demostración: peticiones ciudadanas (Ley 1755/2015).",
            "archivo_gestion_years": 2,
            "archivo_central_years": 8,
            "disposition": "CT",  # conservación total
            # C-4/C8: sin dependencia productora la serie no entra al cuadro de
            # clasificación ni se puede aprobar. La Jurídica recibe las peticiones.
            "dependencias_productoras": [902],
        },
        buscar_en="/api/v1/trd?size=100&incluir_en_tramite=true",
        clave="code",
        valor="DEM-100",
    )
    creado["serie"] = serie
    ok("serie TRD DEM-100 — gestión 2 años, central 8, conservación total")

    tipo = await crear_o_recuperar(
        api,
        "/api/v1/tipos-documentales",
        {
            "trd_serie_id": serie["id"],
            "code": "DEM-PET",
            "nombre": f"Petición ciudadana {MARCA}",
        },
        buscar_en="/api/v1/tipos-documentales?size=100",
        clave="code",
        valor="DEM-PET",
    )
    creado["tipo"] = tipo
    ok("tipo documental «Petición ciudadana» colgando de la serie")

    # ── Radicación: el acto que da origen a todo ──
    paso("Radicación: una Entrada con su anexo")
    # Converge en vez de acumular: si el demo ya sembró su Entrada, se reutiliza.
    # Un `seed` que crea un radicado nuevo en cada ejecución deja una bandeja con
    # seis copias del mismo ejemplo y consume seis números inmutables — que es
    # justo lo que este sistema enseña a no malgastar.
    asunto = f"Solicitud de certificación laboral {MARCA}"
    ya = await buscar_radicado(api, asunto)

    # La remitente entra al DIRECTORIO, no solo como texto en el radicado. Es lo
    # que hace que el recorrido enseñe el modelo completo: su ficha se puede
    # buscar, su contacto queda congelado en el radicado, y `/admin/terceros`
    # tiene algo que mostrar. El alta es idempotente por documento, así que
    # repetir el seed no crea una segunda María Fernanda.
    remitente = await api.post(
        "/api/v1/terceros",
        json={
            "tipo": "ciudadano",
            "nombre": "María Fernanda Ruiz",
            "tipo_documento": "CC",
            "numero_documento": "52987654",
            "email": "mfruiz@ejemplo.test",
            "telefono": "3001234567",
            "direccion": "Calle 45 #12-30",
        },
        esperado=(200, 201),
    )

    entrada = ya or await api.post(
        "/api/v1/documents",
        json={
            "doc_type": "E",
            "subject": asunto,
            "doc_class": "DEM-PET",
            "sender_name": "María Fernanda Ruiz",
            "sender_entity": "Ciudadana",
            "actores": [
                {"tercero_id": remitente["id"], "rol": "remitente", "principal": True}
            ],
            "origin_dept": DEPENDENCIAS[0][2],
            "origin_dept_code": DEPENDENCIAS[0][0],
            "dest_dept": DEPENDENCIAS[1][2],
            "dest_dept_code": DEPENDENCIAS[1][0],
            "pages": 2,
            "observations": "Radicado de demostración. Solicita certificación de tiempo laborado.",
            "response_days": 15,
        },
    )
    creado["entrada"] = entrada
    ok(
        f"Entrada {entrada['tracking_number']} — el número es inmutable"
        + (" (ya estaba sembrada; se reutiliza)" if ya else "")
    )

    anexo = await api.post(
        "/api/v1/storage/upload",
        files={
            "file": (
                "peticion.pdf",
                # Seis páginas y no una: el anexo del recorrido es el que alguien
                # abre en el visor, descarga y verifica por hash. Con una línea no
                # se ve paginación, la descarga es instantánea y el hash se calcula
                # sobre cuatro bytes — ninguna de las rutas que importa se recorre.
                pdf_documento("Peticion ciudadana - documento de ejemplo", paginas=6),
                "application/pdf",
            )
        },
    )
    creado["anexo"] = anexo
    # Sin `.get(..., "?")`: el campo se llama `file_size` y el mensaje llevaba
    # tiempo imprimiendo «? bytes» porque adivinaba `size_bytes`. Un valor por
    # defecto convierte un contrato roto en un detalle cosmético — y el mismo
    # descuido en el `sha256` habría dejado la frase «SHA-256 calculado»
    # afirmando algo que nadie comprobó. Se leen directos: si el contrato
    # cambia, esto revienta aquí y no en la primera descarga que no cuadre.
    ok(
        f"anexo peticion.pdf subido ({anexo['file_size']} bytes, "
        f"SHA-256 {anexo['sha256'][:12]}…)"
    )

    # ── Expediente: agrupar el asunto ──
    paso("Expediente: agrupar el asunto y foliar")
    nombre_exp = f"Certificación laboral — M. F. Ruiz {MARCA}"
    exp, exp_ya = await buscar_o_crear(
        api,
        "/api/v1/expedientes",
        {
            "name": nombre_exp,
            "description": "Expediente de demostración con un radicado vinculado.",
            "trd_serie_id": serie["id"],
        },
        buscar_en="/api/v1/expedientes?size=100",
        clave="name",
    )
    creado["expediente"] = exp
    ok(
        f"expediente «{exp['name'][:40]}…» sobre la serie DEM-100"
        + (" (ya estaba; se reutiliza)" if exp_ya else " — abierto")
    )

    # 409 `radicado_already_linked` es «ya está», no un fallo: en la segunda
    # ejecución el radicado ya cuelga del expediente y eso es exactamente el
    # estado que este guion quiere dejar. Aceptarlo es lo que hace que el demo
    # converja en vez de abortar a mitad del recorrido.
    vinculo = await api.post(
        f"/api/v1/expedientes/{exp['id']}/radicados",
        json={
            "radicado_id": entrada["id"],
            "tracking_number": entrada["tracking_number"],
            "folios": 2,
        },
        esperado=(200, 201, 409),
    )
    ya_vinculado = isinstance(vinculo, dict) and vinculo.get("detail", {}).get(
        "error"
    ) == "radicado_already_linked"
    ok(
        f"{entrada['tracking_number']} "
        + ("ya estaba vinculado" if ya_vinculado else "vinculado con 2 folios")
        + " — entra en el índice electrónico"
    )

    # ── Flujo: el radicado se mueve entre dependencias ──
    paso("Flujo de trabajo: asignar y trasladar")
    await api.post(
        "/api/v1/workflows/assign",
        json={
            "action": "assign",
            "radicado_id": entrada["id"],
            "tracking_number": entrada["tracking_number"],
            "to_dept": DEPENDENCIAS[1][2],
            "to_dept_code": DEPENDENCIAS[1][0],
            "assigned_to": user_id,
            "notes": f"Asignación de demostración {MARCA}",
            "doc_type": "E",
        },
        esperado=(200, 201),
    )
    ok(f"asignado a {DEPENDENCIAS[1][2]} — queda en la bandeja de trámite")

    # ── Respuesta: plantilla → borrador → aprobación → radicación ──
    paso("Respuesta: de la plantilla al número de radicado")
    plantilla, plantilla_ya = await buscar_o_crear(
        api,
        "/api/v1/plantillas",
        {
            "nombre": f"Respuesta a petición {MARCA}",
            "tipo_documental": "DEM-PET",
            "cuerpo": (
                "Respetado(a) ciudadano(a):\n\n"
                "En atención a su solicitud radicada bajo el número indicado, "
                "la entidad se permite informar lo siguiente:\n\n"
                "[Cuerpo de la respuesta]\n\n"
                "Cordialmente,"
            ),
        },
        buscar_en="/api/v1/plantillas",
        clave="nombre",
    )
    creado["plantilla"] = plantilla
    ok(
        "plantilla de respuesta"
        + (" ya sembrada; se reutiliza" if plantilla_ya else " creada")
        + " (la usa cualquier borrador)"
    )

    asunto_salida = f"Respuesta a solicitud de certificación {MARCA}"
    salida_previa = await buscar_radicado(api, asunto_salida)
    if salida_previa:
        creado["salida"] = salida = salida_previa
        ok(f"Salida {salida['tracking_number']} ya sembrada: se reutiliza")
        return await _firmar(api, user_id, creado, exp, salida)

    borrador = await api.post(
        "/api/v1/borradores",
        json={
            "doc_type": "S",
            "subject": asunto_salida,
            "dest_dept": "Ciudadana María Fernanda Ruiz",
            **({"plantilla_id": plantilla["id"]} if plantilla else {"cuerpo": "Respuesta de demostración."}),
        },
    )
    creado["borrador"] = borrador
    ok("borrador redactado — todavía SIN número: no es un documento oficial")

    await api.post(f"/api/v1/borradores/{borrador['id']}/aprobar", esperado=(200, 201))
    ok("borrador aprobado — requisito para radicarlo")

    salida = await api.post(f"/api/v1/borradores/{borrador['id']}/radicar", esperado=(200, 201))
    creado["salida"] = salida
    ok(f"Salida {salida['tracking_number']} — aquí se consume el número, y ya no se edita")

    # ── Firma ──
    return await _firmar(api, user_id, creado, exp, salida)
def _id_cadena(respuesta: Any) -> str | None:
    """El id de la cadena creada. El campo es `solicitud_id`, no `id`.

    Buscar `id` daba «no se pudo abrir» sobre una cadena que se había creado
    perfectamente: el aviso describía un fallo inexistente. Se aceptan los dos
    nombres para no volver a atarse a una suposición sobre la forma del
    contrato.
    """
    if not isinstance(respuesta, dict):
        return None
    valor = respuesta.get("solicitud_id") or respuesta.get("id")
    return str(valor) if valor else None


def _ya_existe(respuesta: Any) -> bool:
    """¿El 409 dice «esto ya está»?

    La primera versión colapsaba TODO lo que no fuera un 201 en un solo aviso —
    «la firma no está disponible»— y con el servicio perfectamente levantado
    seguía diciendo eso, cuando lo que pasaba es que la cadena ya estaba
    sembrada. Dos situaciones opuestas con el mismo mensaje: una es «te falta
    configurar algo» y la otra es «ya está hecho».
    """
    if not isinstance(respuesta, dict):
        return False
    detalle = respuesta.get("detail")
    error = detalle.get("error") if isinstance(detalle, dict) else respuesta.get("error")
    return error in ("cadena_existente", "conflict")


async def _firmar(
    api: Api, user_id: str, creado: dict[str, Any], exp: dict[str, Any], salida: dict[str, Any]
) -> dict[str, Any]:
    """Firma de la Salida y del expediente, y además el circuito sobre un BORRADOR."""
    creado = await _firmar_base(api, user_id, creado, exp, salida)
    return await _circuito_sobre_borrador(api, user_id, creado)


CUERPO_BORRADOR_V1 = (
    "Respetado(a) ciudadano(a):\n\nSe informa que su solicitud fue atendida "
    "favorablemente.\n\nCordialmente,"
)
CUERPO_BORRADOR_V2 = CUERPO_BORRADOR_V1.replace(
    "atendida favorablemente", "atendida favorablemente (texto editado tras abrir el circuito)"
)


async def _circuito_sobre_borrador(
    api: Api, user_id: str, creado: dict[str, Any]
) -> dict[str, Any]:
    """Un borrador con circuito de DOS turnos cuyo texto cambia tras abrirlo.

    Deja en el entorno el caso del gate de orden de `cadena_service` (AREA-05-08):
    la pertenencia a la cadena y el turno se comprueban ANTES que el estado del
    documento. Con el texto cambiado y la cadena todavía `pendiente`, quien NO está
    en la cadena debe recibir `403 no_es_firmante` sin que su respuesta revele que
    existe un circuito, que el texto cambió ni la versión vigente
    (`documento_modificado`/`version`); y el firmante con turno SÍ recibe
    `409 documento_modificado` — control positivo.
    La prueba e2e `frontend/e2e/authenticated/firma-gate-orden.spec.js` lo ejerce.

    **Por qué el cambio de texto va por SQL.** Editar el borrador por la API emite el
    evento de invalidación, que cierra la cadena en segundos (`invalidada`): entonces
    los dos reciben `409 solicitud_no_pendiente` y el gate de orden no se ejerce. El
    caso que el gate existe para cubrir es el OTRO: la invalidación es *at-least-once*
    y puede retrasarse, y en esa ventana la cadena sigue `pendiente` con el texto ya
    cambiado. Para tener esa ventana de forma estable, el texto se cambia en la base
    sin pasar por el servicio —la misma frontera que el aprovisionamiento: es
    preparar el escenario, no gestión documental— y por eso no hay evento. El
    servidor además IGNORA la huella que mande el cliente y fija la vigente.
    """
    paso("Circuito sobre un borrador: dos turnos y texto cambiado en la ventana de invalidación")
    asunto = f"Borrador con circuito de firma {MARCA}"
    borrador, ya = await buscar_o_crear(
        api,
        "/api/v1/borradores",
        {
            "doc_type": "S",
            "subject": asunto,
            "dest_dept": "Ciudadana María Fernanda Ruiz",
            "cuerpo": CUERPO_BORRADOR_V1,
        },
        buscar_en="/api/v1/borradores",
        clave="subject",
    )
    creado["borrador_circuito"] = borrador
    if ya:
        # Idempotente: el escenario ya está montado (y abrir otro circuito sobre un
        # texto ya cambiado quedaría con la huella nueva, sin nada que demostrar).
        ok("borrador con circuito ya sembrado: se reutiliza")
        return creado
    ok("borrador del circuito redactado")

    firmantes = await ids_de(api, FIRMANTES[:2])
    solicitud = await api.post(
        "/api/v1/signature/requests",
        json={
            "objeto_tipo": "borrador",
            "objeto_id": borrador["id"],
            "titulo": f"Circuito de dos turnos sobre un borrador {MARCA}",
            # El servidor fija la huella VIGENTE del texto: la del cliente se ignora.
            "payload_sha256": hashlib.sha256(CUERPO_BORRADOR_V1.encode("utf-8")).hexdigest(),
            "firmantes": firmantes,
            "roles": ["firma", "firma"],
        },
        esperado=(200, 201, 403, 409, 422, 503),
    )
    if not _id_cadena(solicitud):
        if _ya_existe(solicitud):
            ok("circuito del borrador ya sembrado")
        else:
            aviso(f"el circuito sobre el borrador no se pudo abrir: {str(solicitud)[:140]}")
        return creado
    ok(f"circuito de {len(firmantes)} turnos abierto sobre el borrador: {' → '.join(FIRMANTES[:2])}")

    # Cambiar el texto SIN emitir el evento de invalidación (ver el docstring).
    conn = await asyncpg.connect(_dsn())
    try:
        await conn.execute(f'SET search_path TO "tenant_{TENANT}"')
        await conn.execute(
            "UPDATE borrador SET cuerpo = $2 WHERE id = $1::uuid", borrador["id"], CUERPO_BORRADOR_V2
        )
    finally:
        await conn.close()
    ok("texto cambiado con la cadena todavía pendiente: el gate de orden se puede ejercer")
    return creado


async def _firmar_base(
    api: Api, user_id: str, creado: dict[str, Any], exp: dict[str, Any], salida: dict[str, Any]
) -> dict[str, Any]:
    """El paso de firma, en su propia función porque se llega por dos caminos.

    Si la Salida ya estaba sembrada, `sembrar` salta directo aquí. Con el
    bloque duplicado en los dos sitios, cualquier cambio se aplicaría a uno
    solo y el demo enseñaría cosas distintas según fuera la primera ejecución
    o la segunda."""
    paso("Firma: una persona y varias")
    solicitud = await api.post(
        "/api/v1/signature/requests",
        json={
            "objeto_tipo": "radicado",
            "objeto_id": salida["id"],
            "titulo": f"Firma de la respuesta {MARCA}",
            # Lista de UUID, no de objetos: el contrato real de `SolicitudCreate`.
            # Un solo firmante aquí; la cadena de varios se siembra abajo sobre
            # el expediente, que es donde una entidad la usa de verdad.
            "firmantes": [user_id],
        },
        esperado=(200, 201, 403, 409, 422, 503),
    )
    if _id_cadena(solicitud):
        creado["firma"] = solicitud
        ok("cadena de firma de UNA persona, abierta sobre la Salida")
    elif _ya_existe(solicitud):
        ok("cadena de UNA firma ya sembrada sobre la Salida: se reutiliza")
    else:
        # 503 = signature-service no arrancó. Su guardián de secretos lo impide
        # sin `SIGNATURE_SECRET`, que es correcto: una firma con una clave de
        # ejemplo del repositorio no valdría nada. Se dice qué falta y el
        # recorrido documental sigue entero, en vez de abortarlo por una pieza
        # opcional.
        aviso(
            "signature-service no responde. Ejecuta `./scripts/dev-secrets.sh` y "
            "`docker compose up -d signature-service`; el resto del demo no "
            "depende de la firma."
        )
        return creado

    # Cadena de VARIOS: firmantes distintos y reales, en orden. El orden importa
    # —cada uno firma cuando le toca— y con el mismo usuario repetido no habría
    # nada que ver.
    otros = await ids_de(api, FIRMANTES)
    cadena = await api.post(
        "/api/v1/signature/requests",
        json={
            "objeto_tipo": "expediente",
            "objeto_id": exp["id"],
            "titulo": f"Cierre del expediente — cadena de tres firmas {MARCA}",
            "firmantes": [user_id, *otros],
        },
        esperado=(200, 201, 403, 409, 422, 503),
    )
    if _id_cadena(cadena):
        creado["cadena"] = cadena
        ok(
            f"cadena de {1 + len(otros)} firmas sobre el expediente: "
            f"{USUARIO} → {' → '.join(FIRMANTES)}"
        )
    elif _ya_existe(cadena):
        ok(f"cadena de {1 + len(otros)} firmas ya sembrada sobre el expediente")
    else:
        aviso(f"la cadena múltiple no se pudo abrir: {str(cadena)[:140]}")

    return creado


def resumen(creado: dict[str, Any]) -> None:
    print("\n\033[1mDemo sembrado. Qué abrir para verlo:\033[0m")
    if e := creado.get("entrada"):
        print(f"   /bandeja        → Entrada {e['tracking_number']} con su anexo")
    if s := creado.get("salida"):
        print(f"   /borradores     → la Salida {s['tracking_number']} nació de un borrador")
    if x := creado.get("expediente"):
        print(f"   /expedientes    → «{x['name'][:45]}…» con su índice y su foliación")
    print("   /admin/trd      → serie DEM-100 y su tipo documental")
    print("   /reportes       → los conteos ya incluyen lo sembrado")
    if creado.get("cadena"):
        print("   /firmas         → cadena de 4 firmas en orden sobre el expediente")
    print(f"\n   Todo lleva la marca {MARCA}. Para deshacerlo: --purge")


async def purgar() -> int:
    """Deshace lo que este script sembró, y NADA más.

    Borra por la MARCA y por los códigos del demo (`DEM-*`, dependencias 9xx),
    nunca por «todo lo que haya en el tenant»: alguien puede haber estado
    trabajando de verdad en esa instalación.

    **`public.audit_log` no se toca.** Es inmutable por diseño (ADR-010) y su
    cadena de hashes se verifica entera: borrar sus filas la rompería y dejaría
    la auditoría de esa instalación sin poder verificarse nunca más. Los asientos
    del demo se quedan, y es lo correcto — dicen la verdad sobre lo que ocurrió.
    """
    paso("Deshaciendo el demo")
    conn = await asyncpg.connect(_dsn())
    try:
        esquema = f"tenant_{TENANT}"
        await conn.execute(f'SET search_path TO "{esquema}"')
        borrados: list[str] = []

        async def borrar(etiqueta: str, sql: str, *args: Any) -> None:
            resultado = await conn.execute(sql, *args)
            n = int(resultado.rsplit(" ", 1)[-1] or 0)
            if n:
                borrados.append(f"{n} {etiqueta}")

        # Orden: primero lo que referencia, luego lo referenciado.
        await borrar(
            "vínculos de expediente",
            "DELETE FROM expediente_radicados WHERE radicado_id IN "
            "(SELECT id FROM radicados WHERE subject LIKE $1)",
            f"%{MARCA}%",
        )
        # `flow_events` NO se borra: la base lo impide con un trigger
        # —«flow_events is append-only (Ley 594/2000)»— y hace bien. La hoja de
        # ruta de un radicado es registro legal: que un guion de demostración
        # pudiera borrarla sería un defecto del sistema, no una comodidad. Sus
        # filas quedan, y `--purge` lo dice en vez de fingir un borrado completo.
        # Los pasos se buscan por DOS caminos unidos, y el segundo es el que
        # importa: `notes`, que lleva la marca del demo y NO depende de que el
        # radicado siga existiendo. Con solo el subquery, un paso cuyo radicado
        # ya se hubiera borrado quedaba huérfano PARA SIEMPRE —repetir --purge no
        # lo alcanzaba, porque el subquery preguntaba justamente por lo que ya no
        # está—, y la bandeja seguía pintándolo: cuatro tarjetas con cinco
        # acciones cada una que solo podían devolver 404. Un borrado que depende
        # de la existencia de lo que borra no es idempotente.
        await borrar("pasos de flujo",
            "DELETE FROM flow_steps WHERE radicado_id IN "
            "(SELECT id FROM radicados WHERE subject LIKE $1) "
            "   OR notes LIKE $1", f"%{MARCA}%")
        # Cadenas de firma del demo, por su título. `firma` (la firma en sí) NO
        # se toca: si alguien llegó a firmar de verdad, esa firma es un hecho
        # criptográfico sobre un documento, no un dato de prueba.
        await borrar("eslabones de cadena",
            "DELETE FROM firma_cadena WHERE solicitud_id IN "
            "(SELECT id FROM firma_solicitud WHERE titulo LIKE $1)", f"%{MARCA}%")
        await borrar("retos de firma",
            "DELETE FROM firma_challenge WHERE solicitud_id IN "
            "(SELECT id FROM firma_solicitud WHERE titulo LIKE $1)", f"%{MARCA}%")
        await borrar("cadenas de firma",
            "DELETE FROM firma_solicitud WHERE titulo LIKE $1", f"%{MARCA}%")
        await borrar("borradores", "DELETE FROM borrador WHERE subject LIKE $1", f"%{MARCA}%")
        await borrar("radicados", "DELETE FROM radicados WHERE subject LIKE $1", f"%{MARCA}%")
        await borrar("expedientes", "DELETE FROM expedientes WHERE name LIKE $1", f"%{MARCA}%")
        await borrar("plantillas", "DELETE FROM plantilla WHERE nombre LIKE $1", f"%{MARCA}%")
        await borrar("tipos documentales", "DELETE FROM tipos_documentales WHERE code LIKE $1", "DEM-%")
        await borrar("series TRD", "DELETE FROM trd_series WHERE code LIKE $1", "DEM-%")
        await borrar(
            "dependencias",
            "DELETE FROM dependencias WHERE codigo = ANY($1::int[])",
            [c for c, _, _ in DEPENDENCIAS],
        )
        # Los dos grupos, no solo el rico. `role_clearance` y
        # `auth_group_permissions` cuelgan de `auth_groups` con ON DELETE
        # CASCADE, así que se van con él; las membresías también.
        # ANTES que los grupos: `urd_usuaroldep.rol_id` referencia `auth_groups`
        # y esa clave foránea **no** lleva ON DELETE CASCADE (a diferencia de
        # las membresías y los permisos). Borrar el grupo primero reventaría la
        # purga con una violación de integridad a media faena.
        await borrar(
            "asignaciones usuario–dependencia",
            "DELETE FROM urd_usuaroldep WHERE depe_id = ANY($1::int[])",
            [c for c, _, _ in DEPENDENCIAS],
        )
        await borrar(
            "grupos de permisos",
            "DELETE FROM auth_groups WHERE name = ANY($1::text[])",
            [GRUPO_DEMO, GRUPO_INTERMEDIO, GRUPO_CONSULTA],
        )

        if borrados:
            ok("borrado: " + ", ".join(borrados))
        else:
            ok("no había nada del demo que borrar")
        aviso(
            "quedan, y es lo correcto: los asientos de `audit_log` y la hoja de "
            "ruta en `flow_events`. Las dos son append-only por ley (594/2000) y "
            "dicen la verdad sobre lo que ocurrió en esta instalación"
        )
    finally:
        await conn.close()
    return 0


def cli() -> int:
    p = argparse.ArgumentParser(description="Siembra un recorrido completo en el tenant demo")
    p.add_argument("--gateway", default="http://localhost:19080")
    p.add_argument("--purge", action="store_true", help="Borra lo que este script creó")
    args = p.parse_args()
    if args.purge:
        try:
            return asyncio.run(purgar())
        except Fallo as exc:
            print(f"\n\033[31mABORTADO:\033[0m {exc}", file=sys.stderr)
            return 1
    try:
        return asyncio.run(principal(args.gateway))
    except Fallo as exc:
        print(f"\n\033[31mABORTADO:\033[0m {exc}", file=sys.stderr)
        print("No se sembró nada más. Un demo a medias engaña más que ninguno.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
