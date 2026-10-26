// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Mensajes en español de la firma (ADR-016 addendum, ADR-030) vía `mensajeError`.
 */
import { describe, it, expect } from "vitest";
import { mensajeError, MENSAJES_FIRMA } from "$lib/utils/apiError.js";

const CODIGOS = [
  // cadenas de firma
  "radicado_sin_documento",
  "indice_no_disponible",
  "anexo_sin_huella",
  "documento_modificado",
  "cadena_sin_huella",
  "verificacion_documento_no_disponible",
  // firma inobtenible
  "declarante_es_parte_de_la_transferencia",
  "declarante_sin_permiso_de_firma",
  "excepcion_ya_declarada",
  "parte_ya_firmo",
  "parte_sin_identidad",
  "transferencia_no_recibida",
  "excepcion_ya_revocada",
  "referencia_documental_invalida",
  "firma_personal_declarante_requerida",
  "sin_certificado_activo",
  "totp_incorrecto",
  "sello_institucional_no_disponible",
  "sellado_constancia_no_obtenido",
  "document_service_no_disponible",
  "canal_firma_personal_no_disponible",
  "firma_excepcion_declarada",
  "motivo_requerido",
];

describe("mensajes de firma", () => {
  it.each(CODIGOS)("`%s` tiene mensaje propio en español, en sobre plano y anidado", (codigo) => {
    const plano = mensajeError({ error: codigo, detail: "english detail", status: 409 }, "x");
    const anidado = mensajeError({ detail: { error: codigo, detail: "english detail" } }, "x");
    expect(plano).toBe(MENSAJES_FIRMA[codigo]);
    expect(anidado).toBe(MENSAJES_FIRMA[codigo]);
    expect(plano).not.toBe("english detail");
    expect(plano).not.toBe(codigo);
  });

  it("los mensajes llevan tildes donde corresponde", () => {
    expect(MENSAJES_FIRMA.documento_modificado).toMatch(/cambió|después/);
    expect(MENSAJES_FIRMA.canal_firma_personal_no_disponible).toMatch(/está/);
  });
});
