# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
El texto de un error lo lee una persona; el código lo lee una máquina.

`"error": "not_found"` es un identificador y seguirá en inglés — es lo que el
frontend compara y lo que se cita en la documentación de la API. `"detail"`, en
cambio, es la frase que aparece en la pantalla del funcionario que acaba de
quedarse bloqueado, y **la lengua de trabajo de este sistema es el español**. Un
mensaje que la persona no entiende no le dice qué hacer a continuación, que es
lo único para lo que existía.

Llegaron a ser **213 mensajes en inglés repartidos por los once servicios**, y no
por descuido de nadie en particular: se escriben de uno en uno, cada uno junto al
`raise` que lo levanta, y ninguno se ve raro por sí solo. Por eso la comprobación
tiene que ser mecánica.

**Se busca la forma, no la cadena.** El barrido se hace sobre el AST y no sobre
el texto del archivo, porque la mitad de los mensajes no está donde uno los
buscaría: veinte de aquellos 213 los fabricaba un ayudante —`_not_found("…")`,
`_conflict("…")`— o estaban partidos en varias líneas por concatenación
implícita, y ningún grep de la frase completa los habría encontrado.

**La lista es de ADMISIÓN al revés**: no enumera qué está permitido sino qué
palabras delatan el inglés, y son palabras que en español no existen. Si alguna
frase legítima llegara a chocar con una de ellas, lo correcto es reescribir la
frase, no ampliar la excepción.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"

# Ayudantes que fabrican el sobre a partir de un texto suelto. Cuando aparezca
# uno nuevo, se añade aquí: es una línea, y sin ella sus mensajes no se miran.
AYUDANTES = {"_not_found", "_conflict", "_forbidden", "_bad_request", "_unauthorized"}

# Palabras que en español no existen. Deliberadamente cortas y comunes: la gracia
# es que cualquier frase escrita en inglés contenga al menos una. `only` estuvo y
# se quitó: «append-only» y «read-only» son términos de oficio que este proyecto
# usa dentro de frases en español, y una marca que se dispara con ellos entrena a
# ignorar el guardián, que es peor que no tenerlo.
INGLES = re.compile(
    r"\b(?:the|not|found|must|cannot|can't|already|missing|invalid|unknown|unexpected|"
    r"failed|does|doesn't|required|requires|exists|belongs|allowed|this|with|and|"
    r"for|was|were|are|is|be|been|your|you|please|too|before|after|another|there)\b",
    re.IGNORECASE,
)

# Los marcadores de posición llevan identificadores del código (`{payload.code}`,
# `{sorted(CATALOGS)}`) que sí están en inglés y no son texto para nadie.
PLACEHOLDER = re.compile(r"\{[^}]*\}")


def _texto(nodo: ast.AST) -> str | None:
    """Devuelve el texto de un literal, incluida la f-string y la concatenación."""
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, str):
        return nodo.value
    if isinstance(nodo, ast.JoinedStr):
        partes = [
            v.value for v in nodo.values if isinstance(v, ast.Constant) and isinstance(v.value, str)
        ]
        return "".join(partes)
    return None


def _mensajes(ruta: Path) -> list[tuple[int, str]]:
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))
    encontrados: list[tuple[int, str]] = []
    for nodo in ast.walk(arbol):
        if isinstance(nodo, ast.Dict):
            for clave, valor in zip(nodo.keys, nodo.values):
                if isinstance(clave, ast.Constant) and clave.value == "detail":
                    texto = _texto(valor)
                    if texto:
                        encontrados.append((valor.lineno, texto))
        elif isinstance(nodo, ast.Call):
            nombre = getattr(nodo.func, "id", None) or getattr(nodo.func, "attr", None)
            if nombre in AYUDANTES and nodo.args:
                texto = _texto(nodo.args[0])
                if texto:
                    encontrados.append((nodo.lineno, texto))
    return encontrados


def _fuentes() -> list[Path]:
    return sorted(
        p
        for p in SERVICIOS.glob("*/app/**/*.py")
        if "__pycache__" not in p.parts and ".venv" not in p.parts
    )


def test_ningun_mensaje_de_error_en_ingles() -> None:
    culpables = []
    for ruta in _fuentes():
        for linea, texto in _mensajes(ruta):
            limpio = PLACEHOLDER.sub(" ", texto)
            palabras = sorted(set(m.group(0).lower() for m in INGLES.finditer(limpio)))
            if palabras:
                culpables.append(f"{ruta.relative_to(RAIZ)}:{linea} {palabras} → {texto[:80]!r}")
    assert not culpables, (
        "Mensajes de error en inglés (el código puede estarlo; el texto no):\n"
        + "\n".join(culpables)
    )


def test_el_barrido_mira_de_verdad() -> None:
    """Un guardián que recorre cero archivos pasa siempre y no comprueba nada."""
    fuentes = _fuentes()
    assert len(fuentes) > 300, f"solo {len(fuentes)} fuentes: ¿cambió el árbol de servicios?"
    total = sum(len(_mensajes(r)) for r in fuentes)
    assert total > 400, f"solo {total} mensajes hallados: el extractor dejó de encontrarlos"


# ---------------------------------------------------------------------------
# Segundo eje: escrito en español, pero SIN TILDES.
#
# Un mensaje puede pasar el guardián de arriba —no tiene una sola palabra en
# inglés— y seguir estando mal escrito: «No existe la version del codigo de
# serie». Es la lengua de trabajo del sistema y la lee un funcionario; las
# tildes no son un adorno tipográfico, son ortografía.
#
# **Lista de ADMISIÓN de errores**: cada entrada es una forma que, escrita así,
# está mal SIEMPRE. Se excluyen a propósito las ambiguas —`esta`/`está`,
# `cambio`/`cambió`, `valido`/`válido`, `mas`/`más`—, que dependen del contexto
# y sí necesitan que alguien las lea. Este guardián **no demuestra que el texto
# esté bien escrito**: demuestra que no ha vuelto el subconjunto que se puede
# comprobar a máquina, que es un objetivo más modesto y verdadero.
#
# `-ción`/`-sión`: toda palabra española con esa terminación lleva tilde, sin
# excepción, así que la regla es de una precisión inusual.
#
# **`publica` es la excepción, y está aquí porque el guardián se equivocó una
# vez.** «solo se publica un lote» es correcto —es el verbo—, y el guardián lo
# marcó como si faltara la tilde de «pública». Por su propio criterio esa forma
# no debería estar en la lista: no está mal siempre. Pero quitarla del todo sale
# caro en un sistema que habla de «información pública» en cada pantalla, así
# que se acota al único contexto que la desambigua a máquina: precedida de «se »
# es verbo y no se marca. En cualquier otro sitio sigue siendo el adjetivo.
SIN_TILDE = re.compile(
    r"\b(?:\w*ci[o]n|\w*si[o]n|numero|codigo|titulo|metodo|tramite|parametro|"
    r"unico|unica|publico|(?<!se )publica|basico|basica|maximo|minimo|automatico|"
    r"electronico|electronica|juridico|juridica|dias|aqui|asi|estan|segun|"
    r"tambien|despues|ademas|invalido|invalida|analisis|pagina|telefono|"
    r"ultimo|ultima|proximo|proxima|deberia|version)\b",
    re.IGNORECASE,
)


def test_ningun_mensaje_de_error_sin_tildes() -> None:
    culpables = []
    for ruta in _fuentes():
        for linea, texto in _mensajes(ruta):
            limpio = PLACEHOLDER.sub(" ", texto)
            # Un identificador snake_case NO es prosa: `fecha_publicacion` y
            # `justificacion_valoracion` son NOMBRES DE CAMPO y no llevan tilde.
            # Marcarlos entrenaría a ignorar el guardián.
            palabras = sorted(
                {m.group(0).lower() for m in SIN_TILDE.finditer(limpio) if "_" not in m.group(0)}
            )
            if palabras:
                culpables.append(f"{ruta.relative_to(RAIZ)}:{linea} {palabras} → {texto[:80]!r}")
    assert not culpables, (
        "Mensajes de error en español pero sin tildes (los lee una persona):\n" + "\n".join(culpables)
    )


def test_el_barrido_de_tildes_ve_el_defecto_original() -> None:
    """Control positivo: la frase real que tenía archive-service."""
    muestra = "No existe la version  del codigo de serie ''."
    hallados = {m.group(0).lower() for m in SIN_TILDE.finditer(muestra)}
    assert {"version", "codigo"} <= hallados

    # Y control NEGATIVO: la forma correcta no se marca, ni tampoco un nombre
    # de campo, que es donde un guardián mal afinado produce ruido.
    correcta = "No existe la versión del código de serie; falta fecha_publicacion."
    assert not [
        m.group(0) for m in SIN_TILDE.finditer(correcta) if "_" not in m.group(0)
    ]


def test_el_verbo_publicar_no_se_confunde_con_el_adjetivo() -> None:
    """La excepción de `publica`, con sus dos lados.

    Un guardián que se equivoca enseña a ignorarlo, así que la excepción se
    escribe con su control positivo al lado: si alguien la ensancha hasta
    apagar el adjetivo, este test lo dice.
    """
    verbo = "El lote no existe o ya no está en borrador: solo se publica un lote."
    assert not SIN_TILDE.findall(verbo), "«se publica» es el verbo y está bien escrito"

    adjetivo = "La informacion publica no requiere reserva."
    hallado = {m.group(0).lower() for m in SIN_TILDE.finditer(adjetivo)}
    assert "publica" in hallado, "el adjetivo «pública» sin tilde debe seguir marcándose"
