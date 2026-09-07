# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Una regla de distribución no puede detener el reparto de toda la entidad.

El texto sobre el que se evalúa lo trae quien radica —el asunto de un documento
entra aquí sin que nadie lo haya elegido— y las reglas se evalúan al procesar
cada evento. Con una expresión de retroceso desmedido, un asunto construido a
propósito bloqueaba el hilo que distribuye todo el trabajo.
"""

import time

from app.services.rules_engine import eval_condition

#: Expresión de retroceso desmedido para ESTE motor, comprobada midiendo.
#:
#: La primera versión de esta prueba usaba `(a+)+$`, que es el ejemplo de manual
#: y hace tardar 43 segundos al motor de la biblioteca estándar sobre 28
#: caracteres. Con el motor que ahora se usa no tarda nada: la prueba pasaba
#: igual con el plazo quitado, o sea que no vigilaba nada. Se sustituyó por una
#: que sí explota aquí. Un patrón patológico lo es SIEMPRE respecto de un motor
#: concreto, no en abstracto.
_PATOLOGICA = "(a|a)*$"


def _condicion(patron: str) -> dict:
    return {"field": "subject", "op": "regex", "value": patron}


def test_un_asunto_largo_no_dispara_el_retroceso_exponencial():
    """Sin el tope, este mismo caso no termina en un tiempo humano."""
    # 30 caracteres bastan. Acotar la LONGITUD del texto no habría servido de
    # nada —la explosión ocurre con textos diminutos— y esa fue la primera
    # versión de este arreglo, que era teatro: parecía una defensa sin serlo.
    asunto = "a" * 30 + "!"

    inicio = time.monotonic()
    resultado = eval_condition(_condicion(_PATOLOGICA), {"subject": asunto})
    duracion = time.monotonic() - inicio

    assert duracion < 3.0, (
        f"la evaluación tardó {duracion:.1f}s: el texto no se está acotando y una "
        "sola radicación detiene el reparto de toda la entidad"
    )
    assert resultado is False


def test_una_expresion_desmesurada_no_da_por_cumplida_la_condicion():
    """Enrutar de más por no poder comprobar sería el lado inseguro."""
    assert eval_condition(_condicion("a" * 5000), {"subject": "x"}) is False


def test_las_reglas_normales_siguen_funcionando():
    """El tope no puede dejar inservible la distribución que protege."""
    assert eval_condition(_condicion(r"^Tutela"), {"subject": "Tutela de Juan"}) is True
    assert eval_condition(_condicion(r"^Tutela"), {"subject": "Derecho de petición"}) is False


def test_una_expresion_invalida_sigue_devolviendo_falso():
    assert eval_condition(_condicion("(sin cerrar"), {"subject": "x"}) is False
