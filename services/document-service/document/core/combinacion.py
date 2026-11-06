# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Combinación de campos en plantillas y borradores (E23, RF-PRO-01/02).

Paridad con `combinaCampos.php` del Orfeo legado, con tres diferencias
deliberadas:

1. **El vocabulario es cerrado y declarativo.** En el legado los marcadores eran
   una lista de `str_replace` encadenados: escribir un marcador que no existía
   no fallaba, se quedaba impreso en el oficio que recibía el ciudadano. Aquí el
   conjunto está declarado y una plantilla con un marcador desconocido **no se
   puede guardar** (RF-PRO-01) — el error aparece al escribirla, no al usarla.

2. **Sustitución literal, nunca un motor de plantillas.** Nada de Jinja2: la
   plantilla la edita un administrador desde el navegador, y un motor con
   condicionales y bucles ahí es ejecución de código gobernada por datos.

3. **Un campo que no resuelve se declara, no se adivina.** `combinar` devuelve
   los faltantes; ni deja el marcador crudo ni lo vacía en silencio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ── Vocabulario ─────────────────────────────────────────────────────────────
#
# La clave es el nombre canónico, en mayúsculas, tal como lo escribe el legado.
# `diferido=True` marca los dos campos que NO existen todavía cuando se combina:
# el número de la salida y su fecha se consumen al radicar, y radicar es el
# último paso (invariante 3 del spec). Ver `SEGUNDA_PASADA`.


@dataclass(frozen=True)
class Campo:
    """Un marcador del vocabulario: qué es y de dónde sale."""

    descripcion: str
    grupo: str
    diferido: bool = False


CAMPOS: dict[str, Campo] = {
    # -- Radicado padre: la comunicación que se está respondiendo -------------
    "RAD_E": Campo("Número del radicado que se responde", "Radicado padre"),
    "F_RAD_E": Campo("Fecha de radicación del radicado que se responde", "Radicado padre"),
    "ASUNTO": Campo("Asunto del radicado que se responde", "Radicado padre"),
    "NRO_PAGS": Campo("Número de folios del radicado que se responde", "Radicado padre"),
    "DESC_ANEXOS": Campo("Descripción de los anexos del radicado padre", "Radicado padre"),
    # -- Remitente: quien escribió el radicado padre --------------------------
    "NOM_R": Campo("Nombre del remitente", "Remitente"),
    "ENT_R": Campo("Entidad del remitente", "Remitente"),
    "DIR_R": Campo("Dirección del remitente", "Remitente"),
    "DEPTO_R": Campo("Departamento del remitente", "Remitente"),
    "MPIO_R": Campo("Municipio del remitente", "Remitente"),
    "TEL_R": Campo("Teléfono del remitente", "Remitente"),
    "MAIL_R": Campo("Correo electrónico del remitente", "Remitente"),
    "DOC_R": Campo("Documento de identidad del remitente", "Remitente"),
    # -- Destinatario: a quién va dirigida esta salida ------------------------
    "NOM_P": Campo("Nombre del destinatario", "Destinatario"),
    "ENT_P": Campo("Entidad del destinatario", "Destinatario"),
    "DIR_P": Campo("Dirección del destinatario", "Destinatario"),
    "DEPTO_P": Campo("Departamento del destinatario", "Destinatario"),
    "MPIO_P": Campo("Municipio del destinatario", "Destinatario"),
    "TEL_P": Campo("Teléfono del destinatario", "Destinatario"),
    "MAIL_P": Campo("Correo electrónico del destinatario", "Destinatario"),
    "DOC_P": Campo("Documento de identidad del destinatario", "Destinatario"),
    # -- Entidad productora ---------------------------------------------------
    "NOM_E": Campo("Nombre de la entidad", "Entidad"),
    "SIGLA_E": Campo("Sigla de la entidad", "Entidad"),
    "NIT_E": Campo("NIT de la entidad", "Entidad"),
    "DIR_E": Campo("Dirección de la entidad", "Entidad"),
    "TEL_E": Campo("Teléfono de la entidad", "Entidad"),
    "MAIL_E": Campo("Correo electrónico de la entidad", "Entidad"),
    "WEB_E": Campo("Sitio web de la entidad", "Entidad"),
    # -- Dependencia y funcionario que produce --------------------------------
    "DEPENDENCIA": Campo("Nombre de la dependencia productora", "Dependencia"),
    "DEP_SIGLA": Campo("Sigla de la dependencia productora", "Dependencia"),
    "DEP_CODIGO": Campo("Código de la dependencia productora", "Dependencia"),
    "FUNCIONARIO": Campo("Nombre del funcionario que produce", "Funcionario"),
    "CARGO": Campo("Cargo del funcionario que produce", "Funcionario"),
    "LOGIN": Campo("Usuario del funcionario que produce", "Funcionario"),
    # -- Fechas ---------------------------------------------------------------
    "F_HOY": Campo("Fecha de hoy en letras (p. ej. 1 de septiembre de 2026)", "Fechas"),
    "F_HOY_CORTO": Campo("Fecha de hoy en formato corto (2026-09-01)", "Fechas"),
    # -- Expediente -----------------------------------------------------------
    "NUM_EXPEDIENTE": Campo("Código del expediente al que se asocia", "Expediente"),
    "NOM_EXPEDIENTE": Campo("Nombre del expediente al que se asocia", "Expediente"),
    # -- Salida: DIFERIDOS ----------------------------------------------------
    "RAD_S": Campo("Número de esta salida", "Salida", diferido=True),
    "F_RAD_S": Campo("Fecha de radicación de esta salida", "Salida", diferido=True),
}

#: Marcadores que solo pueden resolverse **después** de consumir el número.
SEGUNDA_PASADA: frozenset[str] = frozenset(n for n, c in CAMPOS.items() if c.diferido)

# ── Sintaxis ────────────────────────────────────────────────────────────────
#
# Dos formas para el mismo campo. `*CAMPO*` es la del legado y se mantiene
# porque las entidades llegan con plantillas del Orfeo original: reescribirlas a
# mano es exactamente como una migración se queda a medias. `{{campo}}` es el
# alias moderno, insensible a mayúsculas.
#
# El patrón del legado exige MAYÚSCULAS a propósito. Con minúsculas admitidas,
# cualquier `*texto en cursiva*` de un redactor pasaría por marcador y la
# plantilla se volvería irrechazable sin falsos positivos constantes. Un
# `*IMPORTANTE*` en mayúsculas sí colisiona; para ese caso se escribe el
# asterisco como entidad HTML (`&#42;IMPORTANTE&#42;`), que el editor conserva y
# el renderizador pinta como asterisco.
_RE_LEGADO = re.compile(r"\*([A-Z][A-Z0-9_]{0,29})\*")
_RE_ALIAS = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]{0,29})\s*\}\}")

#: Las dos sintaxis en UNA alternancia. Aplicarlas en dos pasadas dejaría que el
#: valor insertado por la primera fuese leído como plantilla por la segunda —el
#: mismo defecto que la pasada única cierra, solo que más difícil de ver.
_RE_MARCADOR = re.compile(f"{_RE_LEGADO.pattern}|{_RE_ALIAS.pattern}")


@dataclass
class Resultado:
    """Texto ya combinado y los campos que no se pudieron resolver."""

    texto: str
    faltantes: list[str] = field(default_factory=list)


def marcadores(texto: str) -> list[str]:
    """Todos los marcadores presentes en el texto, en nombre canónico.

    Devuelve también los desconocidos: distinguirlos es trabajo de
    `desconocidos`, no de aquí.
    """
    vistos: list[str] = []
    for m in _RE_MARCADOR.finditer(texto or ""):
        nombre = (m.group(1) or m.group(2)).upper()
        if nombre not in vistos:
            vistos.append(nombre)
    return vistos


def desconocidos(texto: str) -> list[str]:
    """Marcadores que no están en el vocabulario. Vacío = la plantilla es válida."""
    return [n for n in marcadores(texto) if n not in CAMPOS]


def combinar(texto: str, contexto: dict[str, object], *, diferidos: bool = False) -> Resultado:
    """Sustituye los marcadores del texto por los valores del contexto.

    `diferidos=False` (primera pasada) **deja intactos** `*RAD_S*` y `*F_RAD_S*`
    y no los cuenta como faltantes: todavía no existen porque el número se
    consume al final. La segunda pasada los resuelve con `diferidos=True`.

    Un campo del vocabulario cuyo valor sea `None` o vacío se sustituye por
    cadena vacía **y se reporta en `faltantes`**. Las dos mitades importan: no
    dejar `*DIR_P*` impreso en el oficio que recibe el ciudadano, y no ocultar
    que el dato faltó.
    """
    if not texto:
        return Resultado(texto="", faltantes=[])

    faltantes: list[str] = []
    resueltos: set[str] = set()

    def _valor(nombre: str) -> str | None:
        """El texto con que sustituir, o `None` para dejar el marcador intacto."""
        campo = CAMPOS.get(nombre)
        if campo is None:
            # Desconocido: no se toca. Lo rechaza `desconocidos` al guardar la
            # plantilla; llegado aquí, borrarlo escondería el defecto.
            return None
        if campo.diferido and not diferidos:
            return None
        bruto = contexto.get(nombre)
        texto_valor = "" if bruto is None else str(bruto).strip()
        if not texto_valor and nombre not in resueltos:
            faltantes.append(nombre)
        resueltos.add(nombre)
        return texto_valor

    def _reemplazo(m: re.Match[str]) -> str:
        valor = _valor((m.group(1) or m.group(2)).upper())
        return m.group(0) if valor is None else valor

    # ── UNA sola pasada, y esto no es una optimización ──────────────────────
    #
    # Sustituir marcador a marcador con `str.replace` vuelve a barrer el texto
    # que acaba de insertarse. Un dato de contacto que contuviera un marcador
    # —el nombre de un tercero, una dirección copiada de un formulario que
    # rellena el ciudadano— se resolvería en la vuelta siguiente y traería al
    # oficio un dato que nadie pidió. El valor combinado es DATO, nunca
    # plantilla; con `re.sub` sobre el texto original, lo insertado no se vuelve
    # a mirar.
    #
    # De paso resuelve solo el problema del orden largo-primero que el legado
    # arrastraba: la alternancia del regex casa el nombre completo entre
    # delimitadores, así que un prefijo no puede comerse al marcador que lo
    # contiene.
    resultado = _RE_MARCADOR.sub(_reemplazo, texto)

    return Resultado(texto=resultado, faltantes=faltantes)


def vocabulario() -> list[dict[str, object]]:
    """El vocabulario como lo consume `GET /plantillas/campos` (RF-PRO-01)."""
    return [
        {
            "nombre": nombre,
            "marcador": f"*{nombre}*",
            "alias": "{{" + nombre.lower() + "}}",
            "descripcion": campo.descripcion,
            "grupo": campo.grupo,
            "diferido": campo.diferido,
        }
        for nombre, campo in CAMPOS.items()
    ]
