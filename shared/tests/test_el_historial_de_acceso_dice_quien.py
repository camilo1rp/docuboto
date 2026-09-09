# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un historial de acceso tiene que contestar QUIÉN, no un identificador.

`audit_log` guarda el id interno del actor, y así debe seguir: un `username`
cambia de dueño y un asiento no se toca nunca (Ley 594/2000, append-only). Pero
una pantalla titulada «quién ha visto esto» que responde
`50b64803-568d-49f7-9299-cb140279d947` no responde nada — es
[[patron-dato-cargado-que-nadie-lee]] otra vez, con el agravante de que el
nombre está a un JOIN de distancia.

**Por qué hace falta un guardián y no bastaba con arreglarlo.** Hay DOS lectores
de la pista de acceso, gemelos y escritos a partir del mismo diseño:

* `document-service/app/repositories/audit_read.py` — «¿quién vio este
  radicado?»
* `archive-service/app/repositories/audit_read.py` — «¿quién vio este
  expediente?»

El 2026-09-06 se corrigió el primero. El 2026-09-07, recorriendo B5 del plan de
pruebas, el segundo seguía mostrando el UUID crudo en la pestaña «Acceso» del
expediente: se arregló un gemelo y se dejó el otro. Dos módulos que responden la
misma pregunta y evolucionan por separado acaban respondiendo distinto — es el
mismo patrón que ya obligó a compartir la lista de acciones de acceso entre
ambos, escrito en sus propios docstrings.

El censo se DERIVA del árbol: un tercer lector entra el día que exista.
"""

from __future__ import annotations

import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"
PANTALLAS = RAIZ / "frontend" / "src"

#: El JOIN que resuelve el identificador a un nombre. Se exige por FORMA y no
#: por texto exacto: lo que importa es que se una con `auth_users` y que el
#: nombre viaje en la proyección.
_RESUELVE = re.compile(r"JOIN\s+auth_users", re.I)
_PROYECTA = re.compile(r"\bactor_nombre\b")

#: Comparar por TEXTO y no castear a `uuid` es parte del contrato: `actor` no
#: siempre es un usuario (hay asientos del propio sistema, y migraciones), y un
#: `::uuid` haría fallar la consulta ENTERA por una fila que ni se preguntaba.
_CASTEA_A_UUID = re.compile(r"a\.actor\s*::\s*uuid|actor\s*=\s*u\.id\b")

#: Lectores que NO resuelven el nombre, con su motivo. Vacía a propósito.
EXCEPCIONES: dict[str, str] = {}

#: Cualquier plantilla que PINTE un actor. El primer censo miraba solo los
#: repositorios y por eso se le escapó la hoja de ruta del expediente, que lee
#: otra tabla (`expediente_eventos`, no `audit_log`) y mostraba el UUID igual:
#: un guardián que vigila la capa equivocada no vigila nada. Lo que importa no
#: es de qué tabla sale, es qué lee la persona.
#: Casa TAMBIÉN la forma ya corregida (`.actor_nombre`) a propósito. La primera
#: versión solo casaba con el defecto, así que una pantalla arreglada SALÍA del
#: censo y dejaba de vigilarse — y la prueba anti-vacuidad de abajo lo cazó al
#: primer intento. Un censo que solo ve lo roto se vacía a medida que se
#: arregla, y entonces el guardián deja de guardar justo cuando ya nadie mira.
#: El censo iba atado al NOMBRE del campo (`actor`), y por eso se le escapó la
#: ficha del acta de entrega de una transferencia, que pinta a sus tres
#: responsables desde campos llamados `elaborado_por` / `entregado_por` /
#: `recibido_por`. Séptima aparición del mismo defecto, y la segunda vez que el
#: guardián mira el sitio equivocado: primero vigilaba la CAPA equivocada (los
#: repositorios de la pista de acceso), ahora el NOMBRE equivocado. Lo que
#: importa no es cómo se llame el campo: es que la pantalla diga quién.
#:
#: Se censan los campos que nombran a una PERSONA: el actor, los `*_por` de
#: responsabilidad y el `firmante_id` de las firmas.
_CAMPO_DE_PERSONA = (
    r"(?:actor|firmante)(?:_nombre|_id)?"
    r"|(?:elaborado|entregado|recibido|decidida|creada|enviada|aprobado|rechazado"
    r"|solicitado|revisado|cerrado|abierto)_por(?:_nombre)?"
)

#: **Se mira lo que se PINTA, no lo que se menciona.** Al ensanchar el censo
#: por nombre de campo salieron cuatro pantallas más, y tres eran falsos
#: positivos míos: comparaban el identificador contra el del usuario en sesión
#: (`turno.firmante_id === yo`) o lo ataban a un `<select>`. Comparar no es
#: pintar, y un guardián que no distingue las dos cosas obliga a declarar
#: excepciones que no lo son — y una lista de excepciones inflada es la forma
#: en que un censo deja de leerse.
#:
#: Solo cuenta una interpolación de plantilla (`{...}`) en el marcado, fuera
#: del bloque `<script>`, que de verdad se MUESTRE. Quedan fuera, y por el
#: mismo motivo que las comparaciones:
#:
#:  * los bloques de lógica de Svelte —`{#each … (clave)}`, `{#if …}`,
#:    `{@const …}`, `{:else …}`, `{/if}`—: una clave de lista y una condición
#:    no las lee nadie;
#:  * las directivas de atributo —`bind:`, `on:`, `use:`— y el `title=`, que
#:    es justamente donde el identificador SÍ debe seguir estando cuando la
#:    pantalla ya enseña el nombre.
_INTERPOLACION = re.compile(r"\{([^{}]*)\}")
_BLOQUE_DE_LOGICA = re.compile(r"^\s*[#/:@]")
_ATRIBUTO_NO_VISIBLE = re.compile(r"(?:bind:|on:|use:)\w+=$|\btitle=$")
_PINTA_CAMPO = re.compile(r"\.(?:" + _CAMPO_DE_PERSONA + r")\b")
#: La forma corregida, de las dos maneras válidas: el backend devuelve el
#: nombre resuelto (`*_nombre`), o la pantalla lo resuelve con un ayudante
#: (`nombreDe(...)`) contra un catálogo que ya tiene cargado.
_RESUELVE_EN_PANTALLA = re.compile(r"_nombre\b|\bnombre[A-Z]\w*\s*\(")


def _marcado(texto: str) -> str:
    """El marcado de un `.svelte`: todo lo que no es el bloque `<script>`."""
    return re.sub(r"<script[\s\S]*?</script>", "", texto)


#: Pantallas que pintan el identificador a propósito, con su motivo.
PANTALLAS_EXCEPTUADAS: dict[str, str] = {
    "routes/(app)/admin/auditoria/+page.svelte": (
        "Visor forense del registro de auditoría: ahí el identificador ES la "
        "clave con la que se cruzan las entradas entre pantallas y con "
        "`/audit/verify`, y el actor no siempre es un usuario del catálogo "
        "(hay asientos del sistema y de otros servicios). Añadir el nombre "
        "sería una mejora de legibilidad, no la corrección de un defecto: "
        "esta pantalla no promete responder «quién», promete mostrar el "
        "asiento tal como está guardado."
    ),
}


def _pantallas_que_pintan_un_actor() -> dict[str, list[str]]:
    """Derivado del árbol: una pantalla nueva entra el día que existe.

    Devuelve, por pantalla, las INTERPOLACIONES concretas que pintan un campo
    de persona — no el archivo entero. Así el fallo señala la línea y no
    obliga a leer mil líneas para encontrarla.
    """
    hallados: dict[str, list[str]] = {}
    for ruta in sorted(PANTALLAS.rglob("*.svelte")):
        texto = ruta.read_text(encoding="utf-8")
        marcado = _marcado(texto)
        pintadas = [
            m.group(1)
            for m in _INTERPOLACION.finditer(marcado)
            if _PINTA_CAMPO.search(m.group(1))
            and not _BLOQUE_DE_LOGICA.search(m.group(1))
            and not _ATRIBUTO_NO_VISIBLE.search(marcado[max(0, m.start() - 40) : m.start()])
        ]
        if pintadas:
            hallados[str(ruta.relative_to(PANTALLAS))] = pintadas
    return hallados


def test_toda_pantalla_que_dice_quien_tiene_un_nombre_que_decir() -> None:
    """El defecto no vive en una tabla: vive en lo que lee la persona.

    Se corrigió tres veces —el historial del radicado, el del expediente y la
    hoja de ruta— y las tres veces el síntoma fue el mismo UUID en pantalla,
    saliendo de dos tablas distintas. Por eso el censo mira las plantillas.
    """
    culpables = [
        f"{rel} → {{{expr.strip()}}}"
        for rel, pintadas in _pantallas_que_pintan_un_actor().items()
        if rel not in PANTALLAS_EXCEPTUADAS
        for expr in pintadas
        if not _RESUELVE_EN_PANTALLA.search(expr)
    ]
    assert not culpables, (
        "Estas pantallas pintan el identificador del actor y no tienen ningún "
        "`actor_nombre` a mano, así que contestan «quién» con un UUID que "
        "nadie puede resolver. Resuelve el nombre en el backend y cae al "
        "identificador solo cuando no exista, o declara la pantalla en "
        "`PANTALLAS_EXCEPTUADAS` con su motivo:\n  " + "\n  ".join(sorted(culpables))
    )


def test_el_censo_de_pantallas_encuentra_algo() -> None:
    """Anti-vacuidad: sin esto, cambiar la sintaxis vaciaría el censo en silencio."""
    assert len(_pantallas_que_pintan_un_actor()) >= 2, (
        "El censo de pantallas que pintan un actor se quedó casi vacío: el "
        "patrón dejó de casar y este guardián ya no vigila nada."
    )


def _lectores_de_la_pista() -> dict[str, str]:
    """Derivado del árbol: cualquier `audit_read.py` de cualquier servicio."""
    hallados: dict[str, str] = {}
    for ruta in sorted(SERVICIOS.glob("*/app/repositories/audit_read.py")):
        hallados[str(ruta.relative_to(SERVICIOS))] = ruta.read_text(encoding="utf-8")
    return hallados


def test_todo_lector_de_la_pista_resuelve_el_nombre() -> None:
    culpables = [
        rel
        for rel, texto in _lectores_de_la_pista().items()
        if rel not in EXCEPCIONES
        and not (_RESUELVE.search(texto) and _PROYECTA.search(texto))
    ]
    assert not culpables, (
        "Estos lectores de la pista de acceso devuelven el identificador del "
        "actor y no su nombre, así que la pantalla que pregunta «quién» "
        "contesta con un UUID que nadie puede resolver. Añade el "
        "`LEFT JOIN auth_users` y proyecta `actor_nombre` —como sus gemelos— o "
        "declara el lector en `EXCEPCIONES` con su motivo:\n  "
        + "\n  ".join(sorted(culpables))
    )


def test_el_join_no_castea_el_actor_a_uuid() -> None:
    """El actor no siempre es un usuario, y un `::uuid` tumbaría la consulta.

    Un asiento escrito por el propio sistema lleva un actor que no es un
    identificador —«system», «proceso-de-migracion»—, y castearlo haría fallar
    el historial ENTERO por una fila que ni se estaba preguntando. Sin nombre
    resoluble se cae al identificador: decir menos es aceptable, perder el
    historial no.
    """
    culpables = [
        rel for rel, texto in _lectores_de_la_pista().items() if _CASTEA_A_UUID.search(texto)
    ]
    assert not culpables, (
        "Estos lectores comparan el actor casteándolo a `uuid`: un asiento del "
        "sistema haría fallar la consulta entera. Únelos por texto "
        "(`u.id::text = a.actor`):\n  " + "\n  ".join(sorted(culpables))
    )


def test_la_lista_de_excepciones_no_caduca() -> None:
    actuales = _lectores_de_la_pista()
    sobran = [
        rel
        for rel in EXCEPCIONES
        if rel not in actuales
        or (_RESUELVE.search(actuales[rel]) and _PROYECTA.search(actuales[rel]))
    ]
    assert not sobran, (
        "Estas excepciones ya no hacen falta: quítalas para que la lista siga "
        "contando la deuda real:\n  " + "\n  ".join(sorted(sobran))
    )


def test_el_censo_encuentra_los_dos_gemelos() -> None:
    """Anti-vacuidad: si el patrón deja de casar, el guardián deja de guardar.

    Ya ocurrió dos veces en este repositorio con censos que salían vacíos o
    casaban con una subcadena demasiado laxa. Son DOS lectores hoy; si el censo
    encuentra menos, es que el patrón dejó de casar, no que el defecto se
    arreglara solo.
    """
    assert len(_lectores_de_la_pista()) >= 2, (
        "El censo de lectores de la pista de acceso se quedó corto: el patrón "
        "de rutas dejó de casar y este guardián ya no vigila nada."
    )


#: Quien escribe un evento de la hoja de ruta tiene una columna TIPADA para la
#: identidad (`actor`). Meterla además en el texto libre del comentario la
#: convierte en un UUID que ninguna pantalla puede resolver — y el comentario
#: se pinta tal cual.
_IDENTIDAD_EN_TEXTO_LIBRE = re.compile(
    r'comentario\s*=\s*f?"[^"]*(firmante|actor|usuario)\s*=\s*\{', re.I
)

#: Servicios que escriben en la hoja de ruta del expediente.
_ESCRIBE_EVENTO = re.compile(r"insert_expediente_evento\s*\(")


def _servicios_que_escriben_la_hoja_de_ruta() -> dict[str, str]:
    """Derivado del árbol: un servicio nuevo entra en el censo el día que existe."""
    hallados: dict[str, str] = {}
    for ruta in sorted(SERVICIOS.glob("*/app/services/*.py")):
        texto = ruta.read_text(encoding="utf-8")
        if _ESCRIBE_EVENTO.search(texto):
            hallados[str(ruta.relative_to(SERVICIOS))] = texto
    return hallados


def test_ningun_evento_mete_la_identidad_en_el_texto_libre() -> None:
    """La identidad va en la columna `actor`, no interpolada en el comentario.

    `close_expediente` escribía `comentario=f"firmante={firmante}"` mientras el
    router le pasaba el MISMO `Actor.audit_actor` a `actor_id`. En la hoja de
    ruta se veía el resultado: el asiento «cerrado» mostraba
    `firmante=50b64803-568d-49f7-9299-…` y, dos líneas más abajo, «OPERADOR» —
    esa misma persona, ya resuelta. El nombre estaba al lado del identificador.

    Sus dos gemelos —`transfer_expediente` y `TransferenciaService.recibir`— ya
    lo habían retirado, y el comentario del primero lo dejaba escrito. Era el
    tercer sitio de tres.
    """
    culpables = []
    for rel, texto in _servicios_que_escriben_la_hoja_de_ruta().items():
        for n, linea in enumerate(texto.splitlines(), 1):
            # Las menciones en comentarios explican por qué se retiró: no son
            # el defecto, son su lápida.
            if linea.lstrip().startswith("#"):
                continue
            if _IDENTIDAD_EN_TEXTO_LIBRE.search(linea):
                culpables.append(f"{rel}:{n} {linea.strip()[:90]}")

    assert not culpables, (
        "Estos eventos de la hoja de ruta interpolan una identidad en el texto "
        "libre del comentario, que la pantalla pinta tal cual: un identificador "
        "que nadie puede resolver, junto a la columna `actor` que sí se "
        "resuelve. Quita el comentario y deja la columna tipada:\n  "
        + "\n  ".join(sorted(culpables))
    )


def test_el_censo_de_la_hoja_de_ruta_encuentra_algo() -> None:
    """Anti-vacuidad: si el patrón deja de casar, el guardián deja de guardar."""
    assert len(_servicios_que_escriben_la_hoja_de_ruta()) >= 2, (
        "El censo de servicios que escriben la hoja de ruta se quedó corto: el "
        "patrón dejó de casar y este guardián ya no vigila nada."
    )

