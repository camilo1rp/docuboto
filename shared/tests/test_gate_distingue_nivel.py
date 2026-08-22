# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un 403 debe decir CUÁL de los dos problemas es.

Faltar un permiso y tenerlo con nivel insuficiente son dos cosas distintas con
dos soluciones distintas: en la primera hay que **conceder** el permiso; en la
segunda hay que **subir el nivel** de uno que ya está concedido. Los ocho
`require_permission` del sistema respondían lo mismo en ambos casos —
``"Se requiere el permiso X"``—, así que un funcionario al que le sobraba el
permiso y le faltaba el nivel mandaba a su administrador a buscar una concesión
que ya existía. El asiento de `audit_log` **sí** guardaba `min_crud`: el dato
bueno estaba, y el mensaje decía otra cosa.

Descubierto en el barrido negativo del plan de pruebas (B13): el usuario
`consulta` tiene `USUA_PERM_EXPEDIENTE` con nivel 1, y al intentar cerrar un
expediente (nivel 3) el sistema le contestaba que le faltaba un permiso que
tiene.

**El censo deriva la lista de `services/*/`**, no la escribe a mano: un servicio
nuevo entra el día que existe. Escribirla a mano ya dejó fuera tres de once en
este proyecto.
"""

from __future__ import annotations

import ast
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"

#: Marca de que el mensaje habla del NIVEL, no solo del permiso. Se acepta
#: cualquiera de las dos formas en uso: el ayudante compartido y el texto
#: en línea de auth-service (que ya nombraba el nivel antes que nadie).
SENALES_NIVEL = ("mensaje_denegacion", "nivel {min_crud}", "crud>={min_crud}")

#: Lo que NO puede quedar: el mensaje que no distingue.
MENSAJE_CIEGO = "Se requiere el permiso {perm_name}\""


def _archivos_con_gate() -> list[Path]:
    """Todo módulo que DEFINE un `require_permission`, derivado del árbol."""
    hallados = []
    for ruta in sorted(SERVICIOS.glob("*/app/**/*.py")):
        try:
            arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
        except SyntaxError:  # pragma: no cover — un .py roto ya falla en otro sitio
            continue
        for nodo in ast.walk(arbol):
            if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if nodo.name == "require_permission":
                    hallados.append(ruta)
                    break
    return hallados


def test_hay_gates_que_censar():
    """Anti-vacuidad: si el descubrimiento se rompe, este test cae, no el censo."""
    archivos = _archivos_con_gate()
    assert len(archivos) >= 8, (
        f"solo se hallaron {len(archivos)} implementaciones de require_permission; "
        "el descubrimiento está roto y el censo de abajo aprobaría sin mirar"
    )


def test_todo_gate_distingue_permiso_ausente_de_nivel_insuficiente():
    culpables = []
    for ruta in _archivos_con_gate():
        texto = ruta.read_text(encoding="utf-8")
        if not any(s in texto for s in SENALES_NIVEL):
            culpables.append(str(ruta.relative_to(RAIZ)))
    assert not culpables, (
        "estos `require_permission` responden lo mismo tanto si falta el permiso "
        "como si solo falta el nivel, y son dos problemas con dos soluciones "
        f"distintas: {culpables}"
    )


def test_no_queda_el_mensaje_ciego():
    """Control directo sobre la cadena exacta que causaba el equívoco."""
    culpables = [
        str(r.relative_to(RAIZ))
        for r in SERVICIOS.glob("*/app/**/*.py")
        if MENSAJE_CIEGO in r.read_text(encoding="utf-8")
    ]
    assert not culpables, f"vuelve el mensaje que no distingue: {culpables}"


def test_el_censo_ve_el_mensaje_ciego(tmp_path):
    """Control positivo del detector, sobre una muestra fabricada."""
    muestra = tmp_path / "gate.py"
    muestra.write_text(
        'def require_permission(perm_name, min_crud=1):\n'
        '    raise HTTPException(detail=f"Se requiere el permiso {perm_name}")\n',
        encoding="utf-8",
    )
    texto = muestra.read_text(encoding="utf-8")
    assert MENSAJE_CIEGO in texto
    assert not any(s in texto for s in SENALES_NIVEL)
