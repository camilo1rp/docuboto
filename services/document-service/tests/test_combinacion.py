# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Combinación de campos (E23, Inc.1 — RF-PRO-01/02)."""

from __future__ import annotations

from datetime import date

import pytest

from app.core.combinacion import (
    CAMPOS,
    SEGUNDA_PASADA,
    combinar,
    desconocidos,
    marcadores,
    vocabulario,
)
from app.services.contexto_combinacion import PARAMS_ENTIDAD, fecha_en_letras


class TestVocabulario:
    def test_los_diferidos_son_exactamente_los_de_la_salida(self) -> None:
        # Si algún día se marca `diferido` otro campo sin querer, la primera
        # pasada dejaría de resolverlo y saldría el marcador crudo en el oficio.
        assert SEGUNDA_PASADA == {"RAD_S", "F_RAD_S"}

    def test_todo_campo_declara_grupo_y_descripcion(self) -> None:
        # El vocabulario alimenta la ayuda de la pantalla: un campo sin
        # descripción es un campo que nadie sabrá usar.
        vacios = [n for n, c in CAMPOS.items() if not c.descripcion or not c.grupo]
        assert not vacios, f"campos sin descripción o grupo: {vacios}"

    def test_el_vocabulario_publica_las_dos_sintaxis(self) -> None:
        entrada = next(c for c in vocabulario() if c["nombre"] == "NOM_R")
        assert entrada["marcador"] == "*NOM_R*"
        assert entrada["alias"] == "{{nom_r}}"

    def test_los_params_de_entidad_apuntan_a_campos_reales(self) -> None:
        # Un parámetro que alimenta un marcador inexistente se configuraría y no
        # haría nada: el fallo sería mudo y solo visible en el oficio impreso.
        assert set(PARAMS_ENTIDAD).issubset(CAMPOS)


class TestSustitucion:
    def test_sustituye_las_dos_sintaxis(self) -> None:
        r = combinar("*NOM_R* / {{nom_r}} / {{ NOM_R }}", {"NOM_R": "Ana Ruiz"})
        assert r.texto == "Ana Ruiz / Ana Ruiz / Ana Ruiz"
        assert r.faltantes == []

    def test_un_campo_sin_valor_se_vacia_y_se_reporta(self) -> None:
        # Las DOS mitades: no dejar `*DIR_P*` impreso en lo que recibe el
        # ciudadano, y no ocultar que el dato faltó.
        r = combinar("Dirección: *DIR_P*.", {"DIR_P": None})
        assert r.texto == "Dirección: ."
        assert r.faltantes == ["DIR_P"]

    def test_un_valor_de_solo_espacios_cuenta_como_faltante(self) -> None:
        assert combinar("*DIR_P*", {"DIR_P": "   "}).faltantes == ["DIR_P"]

    def test_los_diferidos_sobreviven_a_la_primera_pasada(self) -> None:
        # `*RAD_S*` no existe hasta consumir el número, y el número se consume al
        # final. Si la primera pasada lo vaciara, la segunda no tendría qué
        # sustituir y el oficio saldría sin su propio número.
        r = combinar("Salida *RAD_S* del *F_RAD_S*", {})
        assert r.texto == "Salida *RAD_S* del *F_RAD_S*"
        assert r.faltantes == []

    def test_la_segunda_pasada_los_resuelve(self) -> None:
        r = combinar(
            "Salida *RAD_S*", {"RAD_S": "2026-DEMO-S-000007"}, diferidos=True
        )
        assert r.texto == "Salida 2026-DEMO-S-000007"

    def test_un_marcador_desconocido_no_se_toca(self) -> None:
        # Borrarlo escondería el defecto justo donde hay que verlo.
        r = combinar("Hola *NO_EXISTE*", {})
        assert r.texto == "Hola *NO_EXISTE*"
        assert r.faltantes == []

    def test_el_valor_sustituido_no_se_vuelve_a_combinar(self) -> None:
        # Un remitente que se llamara literalmente `*NOM_P*` no puede acabar
        # resolviéndose a otro dato: la sustitución es de una pasada.
        r = combinar("*NOM_R* para *NOM_P*", {"NOM_R": "*NOM_P*", "NOM_P": "Ana"})
        assert r.texto == "*NOM_P* para Ana"

    def test_el_valor_tampoco_se_lee_con_la_otra_sintaxis(self) -> None:
        # Variante fina del anterior: con dos pasadas (una por sintaxis), el
        # valor insertado por la primera lo leería la segunda. Es el mismo
        # defecto, solo que más difícil de ver.
        r = combinar("*NOM_R* y *NOM_P*", {"NOM_R": "{{nom_p}}", "NOM_P": "Ana"})
        assert r.texto == "{{nom_p}} y Ana"

    def test_un_campo_repetido_se_reporta_una_sola_vez(self) -> None:
        assert combinar("*DIR_P* / *DIR_P*", {"DIR_P": None}).faltantes == ["DIR_P"]

    @pytest.mark.parametrize("texto", ["", None])
    def test_texto_vacio(self, texto) -> None:
        assert combinar(texto, {}).texto == ""


class TestDeteccion:
    def test_solo_mayusculas_cuentan_como_marcador_del_legado(self) -> None:
        # Con minúsculas admitidas, cualquier *texto en cursiva* de un redactor
        # pasaría por marcador y la plantilla sería irrechazable sin falsos
        # positivos constantes.
        assert marcadores("un *texto* en cursiva") == []

    def test_el_asterisco_escapado_no_es_marcador(self) -> None:
        assert desconocidos("&#42;IMPORTANTE&#42;") == []

    def test_desconocidos_lista_todos_los_que_sobran(self) -> None:
        assert desconocidos("*NOM_R* *INVENTADO* {{otro_mas}}") == [
            "INVENTADO",
            "OTRO_MAS",
        ]

    def test_una_plantilla_valida_no_tiene_desconocidos(self) -> None:
        assert desconocidos("Señor *NOM_P*, sobre *RAD_E*: *ASUNTO*. *F_HOY*") == []


class TestFecha:
    def test_fecha_en_letras_sin_locale_del_sistema(self) -> None:
        # El formato de un oficio no puede depender de cómo esté configurado el
        # contenedor: con `locale` sin instalar saldría en inglés.
        assert fecha_en_letras(date(2026, 9, 1)) == "1 de septiembre de 2026"
        assert fecha_en_letras(date(2026, 12, 31)) == "31 de diciembre de 2026"
