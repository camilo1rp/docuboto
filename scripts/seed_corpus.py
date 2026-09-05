#!/usr/bin/env python3
# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Corpus de pruebas: un archivo con volumen y variedad, no un ejemplo.

`seed_demo.py` siembra **un recorrido**: una Entrada, su anexo, un expediente,
un traslado y una Salida firmada. Sirve para enseñar el sistema y no sirve para
probarlo: con tres radicados no hay paginación que ver, ni bandejas que se
llenen distinto según quién mira, ni un expediente cerrado frente a uno abierto,
ni nada clasificado que el no-read-up tenga que ocultarle a alguien.

Este guion añade **volumen y variedad** sobre esa base:

* radicados de los tres tipos, repartidos entre dependencias y a lo largo del
  año, unos con anexos y otros sin ellos —un radicado sin adjuntos es lo normal
  en ventanilla, y el índice tiene que poder decir «sin huella» sin que parezca
  una avería—;
* radicados **clasificados** en los tres niveles, sin los cuales el clearance y
  el no-read-up no se pueden observar: son la mitad del sistema que solo se ve
  cuando hay algo que ocultar;
* terceros con geografía DIVIPOLA de verdad, repartidos por el país, para que
  la deduplicación por identidad+destino tenga contra qué chocar;
* expedientes en distintos estados —abiertos con muchos radicados, cerrados con
  su índice, y uno vacío—, porque casi todos los defectos de esta pantalla
  aparecen en los bordes;
* trámite repartido: asignaciones y traslados que dejan bandejas DISTINTAS para
  cada funcionario;
* envíos postales en varios estados, archivo físico con sus unidades y un
  préstamo vivo.

── Dos reglas de construcción, y no son negociables ────────────────────────

**1. Todo se siembra POR LA API, nunca por SQL.** Un `INSERT` directo se salta
la numeración atómica del radicado, las reglas de negocio y —sobre todo— la
cadena de hashes de `public.audit_log`: ya ocurrió en esta instalación, y las
únicas filas no conformes de la auditoría son fixtures de SQL crudo. Un corpus
que corrompe la traza probatoria no sirve para probar un SGDEA.

**2. Converge, no acumula.** Cada ejecución deja el MISMO archivo, no uno más
grande: lo que ya está se reutiliza. Un corpus que crece en cada pasada consume
números de radicado —que son inmutables y no se devuelven— y hace que dos
ejecuciones den resultados distintos, que es lo contrario de una base de pruebas.

── Lo que este guion NO hace ───────────────────────────────────────────────

No firma, no cierra transferencias ni ejecuta disposición final: son actos
irreversibles que queman su objeto y deben ejecutarse a mano, en el orden que
declara el plan de pruebas (`docs/es/plan-pruebas.md`). Sembrar un expediente
ya transferido dejaría el sistema sin nada que transferir.
"""

from __future__ import annotations

import argparse
import asyncio
import random
import sys
from datetime import UTC, datetime
from typing import Any

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from seed_demo import (  # noqa: E402
    Api,
    Fallo,
    aviso,
    ok,
    paso,
    pdf_documento,
)

# La marca que distingue lo del corpus de lo que sembró el demo o de lo que
# alguien esté trabajando de verdad. La purga y la convergencia se apoyan en
# ella: sin una marca, «limpiar el corpus» sería «vaciar el tenant».
MARCA = "[corpus]"

# Semilla FIJA: dos ejecuciones tienen que producir el mismo archivo. Un corpus
# aleatorio hace que un test que falla hoy pase mañana sin que nadie toque nada,
# y eso convierte cada rojo en una discusión sobre si era real.
RNG = random.Random(20260831)

DEPENDENCIAS_EXTRA = (
    (904, "FIN", "Subdirección Financiera"),
    (905, "THU", "Talento Humano"),
    (906, "DIR", "Dirección General"),
    (907, "CTR", "Control Interno"),
    (908, "TIC", "Oficina de Tecnología"),
)

# Series con las CUATRO disposiciones finales: sin una serie de eliminación no
# hay nada que someta el barrido de vencidos ni el acta, y sin una de selección
# la pantalla de disposición no se puede probar entera.
SERIES = (
    ("COR-200", "Correspondencia externa", 2, 8, "E", "Comunicaciones oficiales recibidas."),
    ("CON-300", "Contratos y convenios", 5, 15, "CT", "Expedientes contractuales."),
    ("HIS-400", "Historias laborales", 5, 70, "S", "Historias laborales del personal."),
    ("INF-500", "Informes de gestión", 2, 10, "M", "Informes periódicos de gestión."),
)

TIPOS = (
    ("COR-PET", "COR-200", "Petición"),
    ("COR-QUE", "COR-200", "Queja"),
    ("CON-MIN", "CON-300", "Minuta contractual"),
    ("HIS-HOJ", "HIS-400", "Hoja de vida"),
    ("INF-TRI", "INF-500", "Informe trimestral"),
)

ASUNTOS_E = (
    "Solicitud de certificación de tiempo laborado",
    "Petición de información sobre contratación",
    "Queja por demora en la respuesta",
    "Solicitud de copia de expediente",
    "Derecho de petición sobre convocatoria",
    "Reclamación por descuento indebido",
    "Solicitud de constancia de estudios",
    "Traslado por competencia desde otra entidad",
)
ASUNTOS_S = (
    "Respuesta a solicitud de certificación",
    "Remisión de documentos contractuales",
    "Comunicación de resultados de convocatoria",
    "Traslado por competencia",
)
ASUNTOS_I = (
    "Memorando de asignación de funciones",
    "Circular sobre horarios de atención",
    "Informe trimestral de gestión documental",
)

# Las cinco tipologías de tercero del catálogo (verificadas contra el 422 que
# devuelve el servicio: no se adivinan).
TIPOLOGIAS = ("ciudadano", "empresa", "entidad_publica", "usuario_interno", "direccion_libre")

NOMBRES = (
    "María Fernanda Ruiz", "Carlos Andrés Gómez", "Luz Marina Peña", "Jorge Iván Restrepo",
    "Sandra Milena Ortiz", "Diego Alejandro Cárdenas", "Claudia Patricia Nieto",
    "Héctor Fabio Salazar", "Ana Lucía Bermúdez", "Óscar Eduardo Villamil",
    "Gloria Esperanza Rincón", "Julián David Mosquera", "Paola Andrea Ceballos",
    "Wilson Alberto Trujillo", "Nubia Estela Cárdenas",
)
ENTIDADES = (
    None, None, "Fundación Casa Abierta", "Cooperativa El Progreso",
    "Constructora Los Andes S.A.S.", None, "Junta de Acción Comunal Villa Luz",
)


def _items(datos: Any) -> list[dict[str, Any]]:
    """Los elementos de un listado, venga paginado o como lista pelada.

    Los endpoints del sistema no responden todos igual: unos devuelven
    `{"items": [...]}` y otros la lista directa. Adivinar una sola forma es lo
    que hace que un guion falle en el tercer endpoint con un `AttributeError`
    que no dice nada del contrato.
    """
    if isinstance(datos, dict):
        valor = datos.get("items", [])
        return [x for x in valor if isinstance(x, dict)]
    if isinstance(datos, list):
        return [x for x in datos if isinstance(x, dict)]
    return []


async def _listar(api: Api, ruta: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Lista COMPLETA de un recurso, paginando de 100 en 100.

    Dos decisiones aprendidas a golpes en este mismo guion:

    * **El tope de página es 100.** Pedir `size=200` devuelve 422, y como la
      lectura era «opcional» el error se leía como «no hay nada»: cada ejecución
      volvía a crear lo que ya existía. El corpus creció en silencio hasta
      duplicar expedientes.
    * **Esta lectura NO es opcional.** Una comprobación de convergencia que no
      puede leer tiene que ABORTAR, nunca suponer vacío: suponer vacío es
      exactamente lo que produce los duplicados, y encima sin dejar rastro.
    """
    salida: list[dict[str, Any]] = []
    pagina = 1
    while True:
        datos = await api.get(ruta, params={**(params or {}), "page": pagina, "size": 100})
        items = _items(datos)
        salida += items
        if len(items) < 100 or pagina >= 30:
            return salida
        pagina += 1


def _hoy() -> datetime:
    return datetime.now(tz=UTC)


async def _corpus_existente(api: Api, tipo: str) -> list[dict[str, Any]]:
    """Lo que este guion ya sembró, para converger en vez de acumular.

    Se pagina y se filtra por la MARCA en el asunto: el listado no acepta
    búsqueda por texto, y usar el índice de búsqueda aquí ataría la convergencia
    a que la ingesta del FTS haya corrido — un corpus que se duplica porque un
    índice iba con retraso es peor que no converger.
    """
    todos = await _listar(api, "/api/v1/documents", {"doc_type": tipo})
    return [r for r in todos if MARCA in (r.get("subject") or "")]


# ---------------------------------------------------------------------------
# Instrumentos: dependencias, series y tipos
# ---------------------------------------------------------------------------
async def instrumentos(api: Api) -> dict[str, Any]:
    """Lo que tiene que existir ANTES de que se pueda radicar nada.

    Sin serie y tipo documental no hay clasificación posible, y sin varias
    dependencias el flujo no tiene a dónde ir: un traslado dentro de la misma
    unidad no prueba el reparto entre unidades, que es de lo que trata un SGDEA.
    """
    paso("Instrumentos: dependencias, series TRD y tipos documentales")

    codigos = {d["codigo"] for d in await _listar(api, "/api/v1/dependencias")}
    nuevas = 0
    for codigo, sigla, nombre in DEPENDENCIAS_EXTRA:
        if codigo in codigos:
            continue
        await api.post(
            "/api/v1/dependencias",
            json={"codigo": codigo, "sigla": sigla, "nombre": nombre},
            esperado=(200, 201, 409),
        )
        nuevas += 1
    ok(f"{len(DEPENDENCIAS_EXTRA)} dependencias operativas ({nuevas} nuevas)")

    series_previas = {s["code"]: s for s in await _listar(api, "/api/v1/trd")}
    series: dict[str, Any] = {}
    for code, nombre, gestion, central, disposicion, descripcion in SERIES:
        if code in series_previas:
            series[code] = series_previas[code]
            continue
        series[code] = await api.post(
            "/api/v1/trd",
            json={
                "code": code,
                "name": f"{nombre} {MARCA}",
                "description": descripcion,
                "archivo_gestion_years": gestion,
                "archivo_central_years": central,
                "disposition": disposicion,
            },
        )
    ok(
        "4 series con las CUATRO disposiciones (E, CT, S, M) — sin una de "
        "eliminación no hay nada que someter al barrido de vencidos"
    )

    codigos_tipo = {t["code"] for t in await _listar(api, "/api/v1/tipos-documentales")}
    for code, serie_code, nombre in TIPOS:
        if code in codigos_tipo or serie_code not in series:
            continue
        await api.post(
            "/api/v1/tipos-documentales",
            json={
                "trd_serie_id": series[serie_code]["id"],
                "code": code,
                "nombre": f"{nombre} {MARCA}",
            },
            esperado=(200, 201, 409),
        )
    ok(f"{len(TIPOS)} tipos documentales colgando de sus series")
    return {"series": series}


# ---------------------------------------------------------------------------
# Terceros: identidad + geografía real
# ---------------------------------------------------------------------------
async def terceros(api: Api, cuantos: int) -> list[dict[str, Any]]:
    """Ciudadanos y entidades con municipio DIVIPOLA de verdad.

    Repartidos por el país a propósito: la deduplicación es por identidad **y
    destino**, así que sin varios municipios no hay forma de comprobar que el
    mismo señor en dos direcciones son dos envíos legítimos y no un duplicado.
    """
    paso("Terceros con geografía DIVIPOLA")

    departamentos = await api.get("/api/v1/terceros/divipola/departamentos", opcional=True)
    departamentos = _items(departamentos)
    if not departamentos:
        aviso("no se pudo leer el catálogo DIVIPOLA; los terceros van sin geografía")

    municipios: list[dict[str, Any]] = []
    for depto in RNG.sample(departamentos, min(8, len(departamentos))):
        lista = await api.get(
            "/api/v1/terceros/divipola/municipios",
            params={"departamento": depto["codigo"]},
            opcional=True,
        )
        municipios += _items(lista)[:6]

    creados: list[dict[str, Any]] = []
    for i in range(cuantos):
        nombre = NOMBRES[i % len(NOMBRES)]
        # Las CINCO tipologías del catálogo, no dos: cada una se comporta
        # distinto en el formulario de radicación y en la exigencia de datos
        # de contacto, y una que no aparezca en el corpus no se prueba.
        cuerpo: dict[str, Any] = {
            "tipo": TIPOLOGIAS[i % len(TIPOLOGIAS)],
            "nombre": f"{nombre} {i // len(NOMBRES) + 1}" if i >= len(NOMBRES) else nombre,
            "entidad": ENTIDADES[i % len(ENTIDADES)],
            "tipo_documento": "CC",
            "numero_documento": f"10{i:07d}",
            "email": f"tercero{i}@ejemplo.test",
            "telefono": f"3{i:09d}"[:10],
            "direccion": f"Calle {10 + i % 80} #{i % 50}-{i % 90}",
            "pais": "CO",
        }
        if municipios:
            m = municipios[i % len(municipios)]
            cuerpo["municipio_codigo"] = m["codigo"]
        # `crear_o_recuperar` no vale aquí: el servicio ya es idempotente por
        # documento de identidad y devuelve el existente — pedirlo dos veces es
        # un no-op, no un error. Es la misma regla que impide que cambiar de
        # dirección cree un tercero nuevo.
        creados.append(await api.post("/api/v1/terceros", json=cuerpo, esperado=(200, 201)))
    ok(f"{len(creados)} terceros ({len(municipios)} municipios distintos disponibles)")
    return creados


# ---------------------------------------------------------------------------
# Radicación en volumen
# ---------------------------------------------------------------------------
async def radicados(
    api: Api, cuantos: int, series: dict[str, Any], gente: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """El grueso del corpus: Entradas, Salidas e Internos.

    Tres decisiones que no son de relleno:

    * **Unos con anexos y otros sin ellos.** Un radicado sin adjuntos es lo
      normal en ventanilla; el índice electrónico tiene que poder declararlo
      «sin huella» sin que se lea como una avería, y eso solo se ve si el
      corpus tiene de los dos.
    * **Clasificados en los tres niveles.** El clearance y el no-read-up son la
      mitad del sistema que únicamente se observa cuando hay algo que ocultarle
      a alguien. Un corpus todo-público los deja sin probar.
    * **Repartidos entre dependencias.** Para que la bandeja de cada funcionario
      sea distinta y un filtro que no filtre se note.
    """
    paso(f"Radicación en volumen ({cuantos} radicados)")

    ya = {}
    for tipo in ("E", "S", "I"):
        ya[tipo] = await _corpus_existente(api, tipo)
    total_previos = sum(len(v) for v in ya.values())
    if total_previos >= cuantos:
        ok(f"{total_previos} radicados del corpus ya sembrados: se reutilizan")
        return [r for v in ya.values() for r in v]

    reparto = [("E", 0.55), ("S", 0.28), ("I", 0.17)]
    creados: list[dict[str, Any]] = [r for v in ya.values() for r in v]
    por_crear = cuantos - total_previos
    deps = [d[2] for d in DEPENDENCIAS_EXTRA]

    for i in range(por_crear):
        r = RNG.random()
        tipo = "E" if r < reparto[0][1] else ("S" if r < reparto[0][1] + reparto[1][1] else "I")
        asuntos = {"E": ASUNTOS_E, "S": ASUNTOS_S, "I": ASUNTOS_I}[tipo]
        indice = total_previos + i
        persona = gente[indice % len(gente)] if gente else None

        # Clasificación: mayoría pública, algunas reservadas y unas pocas
        # clasificadas. El fundamento es OBLIGATORIO al clasificar (E08): sin
        # él, «reservado» sería una etiqueta sin acto administrativo detrás.
        sorteo = RNG.random()
        if sorteo < 0.08:
            nivel, causal = 3, "Información clasificada (Ley 1712/2014 art. 18)"
        elif sorteo < 0.22:
            nivel, causal = 2, "Información reservada (Ley 1712/2014 art. 19)"
        else:
            nivel, causal = 1, None

        cuerpo: dict[str, Any] = {
            "doc_type": tipo,
            "subject": f"{asuntos[indice % len(asuntos)]} #{indice:04d} {MARCA}",
            "doc_class": TIPOS[indice % len(TIPOS)][0],
            "sender_name": (persona or {}).get("nombre", NOMBRES[indice % len(NOMBRES)]),
            "sender_entity": (persona or {}).get("entidad") or "Ciudadano",
            "origin_dept": deps[indice % len(deps)],
            "dest_dept": deps[(indice + 1) % len(deps)],
            "pages": RNG.randint(1, 25),
            "observations": f"Radicado del corpus de pruebas {MARCA}.",
            "nivel_seguridad": nivel,
            "response_days": RNG.choice([10, 15, 30]),
        }
        if causal:
            cuerpo["clasificacion_fundamento"] = causal
            cuerpo["clasificacion_causal"] = causal
            cuerpo["clasificacion_plazo_meses"] = 60

        # ~45 % con anexos: el resto queda deliberadamente sin contenido binario.
        if RNG.random() < 0.45:
            anexos = []
            for k in range(RNG.randint(1, 3)):
                subido = await api.post(
                    "/api/v1/storage/upload",
                    files={
                        "file": (
                            f"anexo-{indice:04d}-{k}.pdf",
                            # Tamaño VARIABLE, no constante: un corpus donde todo
                            # pesa lo mismo esconde los problemas de tamaño —
                            # límites de subida, tiempos de render, paginación del
                            # visor— que solo aparecen en los extremos.
                            pdf_documento(
                                f"Corpus {indice} anexo {k}",
                                paginas=RNG.choice([1, 2, 5, 12, 30]),
                            ),
                            "application/pdf",
                        )
                    },
                )
                anexos.append(
                    {
                        "file_id": subido["id"],
                        "filename": subido["filename"],
                        "file_size": subido["file_size"],
                        "mime_type": subido.get("mime_type", "application/pdf"),
                        "es_principal": k == 0,
                        "folios": RNG.randint(1, 8),
                    }
                )
            cuerpo["anexos"] = anexos

        creados.append(await api.post("/api/v1/documents", json=cuerpo))
        if (i + 1) % 25 == 0:
            ok(f"  … {i + 1}/{por_crear}")

    niveles = {}
    for r in creados:
        niveles[r.get("nivel_seguridad", 1)] = niveles.get(r.get("nivel_seguridad", 1), 0) + 1
    ok(
        f"{len(creados)} radicados del corpus — "
        f"públicos {niveles.get(1, 0)}, reservados {niveles.get(2, 0)}, "
        f"clasificados {niveles.get(3, 0)}"
    )
    return creados


# ---------------------------------------------------------------------------
# Expedientes: el archivo, no una lista de documentos
# ---------------------------------------------------------------------------
async def _rellenar_vacios(
    api: Api, exps: list[dict[str, Any]], docs: list[dict[str, Any]]
) -> None:
    """Vincula radicados a los expedientes del corpus que estén sin contenido.

    Se deja UNO vacío a propósito (el último): es el borde que la pantalla suele
    olvidar, y sin él nadie comprueba qué enseña un expediente recién abierto.
    """
    disponibles = [d for d in docs if d.get("nivel_seguridad", 1) == 1]
    RNG.shuffle(disponibles)
    cursor = 0
    rellenados = 0
    for exp in exps[:-1]:
        # `GET /expedientes/{id}` YA trae los radicados vinculados; NO existe
        # `/expedientes/{id}/radicados` en GET. La primera versión lo pedía con
        # `opcional=True`, así que el 404 se leía como «no tiene ninguno» y cada
        # pasada volvía a rellenar expedientes que ya estaban llenos. Es el
        # MISMO defecto que el `size=200`: una lectura opcional convierte «no
        # pude leer» en «no hay», y con eso se rompe la convergencia.
        detalle = await api.get(f"/api/v1/expedientes/{exp['id']}")
        if detalle.get("radicados"):
            continue
        lote = disponibles[cursor : cursor + RNG.choice([3, 5, 8, 12, 20])]
        cursor += len(lote)
        if not lote:
            break
        await api.post(
            f"/api/v1/expedientes/{exp['id']}/radicados/batch",
            json={
                "radicados": [
                    {
                        "radicado_id": r["id"],
                        "tracking_number": r["tracking_number"],
                        "folios": RNG.randint(1, 10),
                    }
                    for r in lote
                ]
            },
            esperado=(200, 201),
        )
        rellenados += 1
    if rellenados:
        ok(f"  {rellenados} expedientes que estaban vacíos quedaron con contenido")


async def expedientes(
    api: Api, cuantos: int, series: dict[str, Any], docs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Expedientes con volumen desigual y uno vacío.

    Desigual a propósito: casi todos los defectos de esta pantalla aparecen en
    los bordes —el expediente con veinte radicados que pagina, y el que no
    tiene ninguno—, no en el que tiene tres.

    **No se cierra ninguno aquí.** Cerrar dispara índice, firma y sellado, y
    congela la foliación: es un acto irreversible que quema su objeto y debe
    ejecutarse a mano, en el orden del plan de pruebas.
    """
    paso(f"Expedientes ({cuantos})")

    lista_previa = [
        e for e in await _listar(api, "/api/v1/expedientes") if MARCA in (e.get("name") or "")
    ]
    # Converger NO es «no hacer nada si ya hay expedientes»: la primera versión
    # se daba por satisfecha con que existieran, y los dejaba VACÍOS porque los
    # radicados se habían creado después. Un expediente sin radicados no prueba
    # ni la paginación de su contenido, ni la foliación, ni el índice — es decir,
    # casi nada de lo que un expediente es. Se completan los que estén vacíos.
    if len(lista_previa) >= cuantos:
        ok(f"{len(lista_previa)} expedientes del corpus ya sembrados: se completan los vacíos")
        await _rellenar_vacios(api, lista_previa, docs)
        return lista_previa

    codigos_serie = list(series.keys())
    creados = list(lista_previa)
    disponibles = [d for d in docs if d.get("nivel_seguridad", 1) == 1]
    RNG.shuffle(disponibles)
    cursor = 0

    for i in range(len(lista_previa), cuantos):
        serie = series[codigos_serie[i % len(codigos_serie)]]
        exp = await api.post(
            "/api/v1/expedientes",
            json={
                "name": f"Expediente de pruebas {i:02d} {MARCA}",
                "description": "Expediente del corpus de pruebas.",
                "trd_serie_id": serie["id"],
                "nivel_seguridad": 1,
            },
        )
        creados.append(exp)

        # El último queda VACÍO: el estado que la pantalla suele olvidar.
        if i == cuantos - 1:
            continue

        cuantos_rad = RNG.choice([1, 3, 5, 8, 12, 20])
        lote = disponibles[cursor : cursor + cuantos_rad]
        cursor += cuantos_rad
        if not lote:
            continue
        await api.post(
            f"/api/v1/expedientes/{exp['id']}/radicados/batch",
            json={
                "radicados": [
                    {
                        "radicado_id": r["id"],
                        "tracking_number": r["tracking_number"],
                        "folios": RNG.randint(1, 10),
                    }
                    for r in lote
                ]
            },
            esperado=(200, 201),
        )
    ok(
        f"{len(creados)} expedientes — de 1 a 20 radicados, y el último VACÍO "
        "(el borde que la pantalla suele olvidar)"
    )
    return creados


# ---------------------------------------------------------------------------
# Trámite: bandejas que se ven distintas según quién mira
# ---------------------------------------------------------------------------
async def tramite(api: Api, docs: list[dict[str, Any]], user_id: str) -> int:
    """Asignaciones y traslados sobre una parte del corpus.

    Sin esto, todas las bandejas salen vacías y un filtro que no filtra pasa
    desapercibido: «vas al día» es indistinguible de «la consulta está rota».
    """
    paso("Trámite: reparto entre dependencias")
    deps = [d[2] for d in DEPENDENCIAS_EXTRA]
    movidos = 0
    for i, r in enumerate(docs[:60]):
        if r.get("doc_type") != "E":
            continue
        try:
            await api.post(
                "/api/v1/workflows/assign",
                json={
                    "action": "assign",
                    "radicado_id": r["id"],
                    "tracking_number": r["tracking_number"],
                    "to_dept": deps[i % len(deps)],
                    "assigned_to": user_id,
                    "notes": f"Reparto del corpus {MARCA}",
                    "doc_type": "E",
                },
                esperado=(200, 201, 409),
            )
            movidos += 1
        except Fallo as exc:
            # Un radicado que no admite el movimiento no puede tumbar el
            # reparto entero: se anota y se sigue. Abortar aquí dejaría el
            # corpus a medias por un caso de borde del flujo.
            aviso(f"no se pudo asignar {r['tracking_number']}: {str(exc)[:80]}")
    ok(f"{movidos} radicados repartidos entre {len(deps)} dependencias")
    return movidos


# ---------------------------------------------------------------------------
# Envíos postales y archivo físico
# ---------------------------------------------------------------------------
async def envios(api: Api, docs: list[dict[str, Any]]) -> int:
    """Envíos de las Salidas, en varios estados.

    Varios estados a propósito: una pantalla de envíos con todo en «registrado»
    no prueba la máquina de estados, que es lo único que tiene de interesante.
    """
    paso("Envíos postales")
    salidas = [d for d in docs if d.get("doc_type") == "S"][:20]
    estados = ("en_transito", "entregado", "devuelto")
    hechos = 0
    for i, s in enumerate(salidas):
        try:
            envio = await api.post(
                f"/api/v1/documents/{s['id']}/envios",
                json={
                    # Operadores del catálogo cerrado del servicio (`4-72`,
                    # `servientrega`, `otro`), no nombres comerciales inventados:
                    # el campo es un vocabulario, no texto libre.
                    "operador": RNG.choice(["4-72", "servientrega", "otro"]),
                    "destinatario": NOMBRES[i % len(NOMBRES)],
                    "direccion": f"Calle {20 + i} #{i}-{i + 10}",
                },
                esperado=(200, 201, 409),
            )
            hechos += 1
            envio_id = envio.get("id") if isinstance(envio, dict) else None
            if envio_id and i % 3 != 2:
                await api.post(
                    f"/api/v1/envios/{envio_id}/estado",
                    json={"estado": estados[i % len(estados)], "descripcion": MARCA},
                    esperado=(200, 201, 409, 422),
                )
        except Fallo as exc:
            aviso(f"envío de {s['tracking_number']}: {str(exc)[:80]}")
    ok(f"{hechos} envíos registrados, con estados repartidos")
    return hechos


async def archivo_fisico(api: Api, exps: list[dict[str, Any]]) -> int:
    """Ubicaciones, unidades de conservación y un préstamo vivo.

    El préstamo sin devolver es el que importa: es el estado que bloquea
    transferencias y el que una pantalla puede olvidar mostrar.
    """
    paso("Archivo físico: ubicaciones, unidades y un préstamo")
    # Se listan primero y se reutilizan: un 409 devuelve el error, no la fila,
    # así que crear-y-esperar-conflicto dejaba sin `id` a la segunda ejecución
    # y la jerarquía se quedaba coja sin decirlo.
    previas = {u["codigo"]: u for u in await _listar(api, "/api/v1/ubicaciones")}
    ubicaciones = []
    for nivel, codigo, nombre in (
        ("deposito", "DEP-01", "Depósito principal"),
        ("estante", "EST-01", "Estante 1"),
        ("entrepano", "ENT-01", "Entrepaño 1"),
    ):
        u = previas.get(codigo)
        if u is None:
            u = await api.post(
                "/api/v1/ubicaciones",
                json={
                    "nivel": nivel,
                    "codigo": codigo,
                    "nombre": f"{nombre} {MARCA}",
                    "parent_id": ubicaciones[-1]["id"] if ubicaciones else None,
                    "tipo_archivo": "central",
                },
            )
        ubicaciones.append(u)
    creadas = len(ubicaciones)

    previas_un = {u["codigo"]: u for u in await _listar(api, "/api/v1/unidades")}
    unidades = []
    for i in range(8):
        codigo = f"CAJA-{i:03d}"
        un = previas_un.get(codigo)
        if un is None:
            un = await api.post(
                "/api/v1/unidades",
                json={
                    "tipo": "caja",
                    "codigo": codigo,
                    "ubicacion_id": ubicaciones[-1]["id"] if ubicaciones else None,
                    "soporte": "papel",
                    "num_folios": RNG.randint(50, 400),
                },
            )
        unidades.append(un)

    vinculados = 0
    for un, exp in zip(unidades, exps, strict=False):
        try:
            await api.post(
                f"/api/v1/unidades/{un['id']}/expedientes",
                json={"expediente_id": exp["id"]},
                # 204: la ubicación se registra y no devuelve cuerpo. Tratarlo
                # como fallo dejaba ocho unidades «sin ubicar» que sí lo estaban.
                esperado=(200, 201, 204, 409),
            )
            vinculados += 1
        except Fallo as exc:
            aviso(f"unidad {un['codigo']}: {str(exc)[:80]}")

    if unidades:
        await api.post(
            f"/api/v1/unidades/{unidades[0]['id']}/prestamos",
            json={
                "prestado_a": "Oficina Jurídica",
                "prestado_por": "Archivo Central",
                "fecha_devolucion_esperada": "2026-12-31",
            },
            esperado=(200, 201, 409),
        )
    ok(
        f"{creadas} ubicaciones, {len(unidades)} unidades, {vinculados} expedientes "
        "ubicados y 1 préstamo SIN devolver"
    )
    return len(unidades)


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------
async def principal(gateway: str, escala: dict[str, int]) -> int:
    inicio = _hoy()
    async with Api(gateway) as api:
        user_id = await api.autenticar()
        instr = await instrumentos(api)
        gente = await terceros(api, escala["terceros"])
        docs = await radicados(api, escala["radicados"], instr["series"], gente)
        exps = await expedientes(api, escala["expedientes"], instr["series"], docs)
        await tramite(api, docs, user_id)
        await envios(api, docs)
        await archivo_fisico(api, exps)

    dur = (_hoy() - inicio).total_seconds()
    paso("Corpus sembrado")
    ok(f"{len(docs)} radicados, {len(exps)} expedientes, {len(gente)} terceros — {dur:.0f}s")
    ok("converge: volver a ejecutarlo deja el MISMO archivo, no uno más grande")
    aviso(
        "los actos irreversibles (cerrar, firmar, transferir, disponer) NO se "
        "siembran: queman su objeto y van a mano, en el orden del plan de pruebas"
    )
    return 0


def cli() -> int:
    p = argparse.ArgumentParser(
        description="Siembra un corpus de pruebas con volumen y variedad en el tenant demo"
    )
    p.add_argument("--gateway", default="http://localhost:19080")
    p.add_argument("--radicados", type=int, default=180)
    p.add_argument("--expedientes", type=int, default=12)
    p.add_argument("--terceros", type=int, default=40)
    args = p.parse_args()
    escala = {
        "radicados": args.radicados,
        "expedientes": args.expedientes,
        "terceros": args.terceros,
    }
    try:
        return asyncio.run(principal(args.gateway, escala))
    except Fallo as exc:
        print(f"\n\033[31mABORTADO:\033[0m {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
