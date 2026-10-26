// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Reglas puras de la firma inobtenible, la política XAdES y las advertencias.
 * Cada caso nombra la mutación que lo haría fallar.
 */
import { describe, it, expect } from "vitest";
import {
  esParteDeLaTransferencia,
  puedeGestionarExcepciones,
  rolesDeclarables,
  rolesRevocables,
  exigeTotp,
  relajaPolitica,
  COMPLETITUD_MAP,
  etiquetaAdvertencia,
  excepcionesDe,
  excepcionDelRol,
  describirPersona,
  puedeVerificarFundamento,
  resumenVerificacionFundamento,
  etiquetaCadenaMotivo,
  etiquetaMotivoEnlace,
  etiquetaMotivosEnlace,
  etiquetaArtefacto,
} from "$lib/utils/firmaInobtenible.js";

const T = {
  id: "t",
  estado: "recibida",
  creada_por: "u-elab",
  enviada_por: "u-env",
  decidida_por: "u-rec",
};
const NIVELES_OK = { USUA_PERM_ADMIN: 3, PERM_FIRMA: 2 };

describe("puedeGestionarExcepciones", () => {
  it("un tercero con administración 3 y firma 2 puede", () => {
    expect(
      puedeGestionarExcepciones({ levels: NIVELES_OK, userId: "u-otro", transferencia: T }),
    ).toBe(true);
  });

  it.each(["u-elab", "u-env", "u-rec"])("quien es parte (%s) NO puede", (id) => {
    expect(puedeGestionarExcepciones({ levels: NIVELES_OK, userId: id, transferencia: T })).toBe(
      false,
    );
  });

  it("administración 2 no alcanza", () => {
    expect(
      puedeGestionarExcepciones({
        levels: { USUA_PERM_ADMIN: 2, PERM_FIRMA: 2 },
        userId: "u-otro",
        transferencia: T,
      }),
    ).toBe(false);
  });

  it("permiso de firma 1 no alcanza", () => {
    expect(
      puedeGestionarExcepciones({
        levels: { USUA_PERM_ADMIN: 3, PERM_FIRMA: 1 },
        userId: "u-otro",
        transferencia: T,
      }),
    ).toBe(false);
  });

  it("sin identidad o sin niveles falla cerrada", () => {
    expect(puedeGestionarExcepciones({ levels: NIVELES_OK, userId: null, transferencia: T })).toBe(
      false,
    );
    expect(puedeGestionarExcepciones({ levels: {}, userId: "u-otro", transferencia: T })).toBe(
      false,
    );
    expect(esParteDeLaTransferencia(null, "u-otro")).toBe(true);
  });
});

describe("rolesDeclarables / rolesRevocables", () => {
  const verif = (firmas = [], excepciones = []) => ({
    firmas_personales: firmas.map((rol) => ({ rol })),
    excepciones,
  });

  it("ofrece los roles sin firma ni excepción", () => {
    expect(
      rolesDeclarables({ transferencia: T, verificacion: verif(["entrega"]), acta: null }),
    ).toEqual(["elabora", "recibe"]);
  });

  it("un rol con excepción REVOCADA se puede declarar de nuevo (cadena FIRMA-2)", () => {
    const r = rolesDeclarables({
      transferencia: T,
      verificacion: verif([], [{ rol: "recibe", ordinal: 1, revocada: true }]),
      acta: null,
    });
    expect(r).toContain("recibe");
  });

  it("manda el último eslabón de la cadena: revocada n.º 1 y vigente n.º 2 no se re-declara", () => {
    const cadena = [
      { rol: "recibe", ordinal: 2, revocada: false },
      { rol: "recibe", ordinal: 1, revocada: true },
    ];
    expect(excepcionDelRol(cadena, "recibe").ordinal).toBe(2);
    const r = rolesDeclarables({ transferencia: T, verificacion: verif([], cadena), acta: null });
    expect(r).not.toContain("recibe");
    expect(rolesRevocables(cadena)).toEqual(["recibe"]);
  });

  it("una parte que ya firmó personalmente no se ofrece aunque su excepción esté revocada", () => {
    const r = rolesDeclarables({
      transferencia: T,
      verificacion: verif(["recibe"], [{ rol: "recibe", ordinal: 1, revocada: true }]),
      acta: null,
    });
    expect(r).not.toContain("recibe");
  });

  it("un rol con excepción vigente tampoco", () => {
    const r = rolesDeclarables({
      transferencia: T,
      verificacion: verif([], [{ rol: "entrega", revocada: false }]),
      acta: null,
    });
    expect(r).not.toContain("entrega");
  });

  it("sin acta recibida o sin verificación no ofrece nada", () => {
    expect(
      rolesDeclarables({
        transferencia: { ...T, estado: "enviada" },
        verificacion: verif(),
        acta: null,
      }),
    ).toEqual([]);
    expect(rolesDeclarables({ transferencia: T, verificacion: null, acta: null })).toEqual([]);
  });

  it("solo las vigentes se pueden revocar", () => {
    expect(
      rolesRevocables([
        { rol: "entrega", revocada: false },
        { rol: "recibe", revocada: true },
      ]),
    ).toEqual(["entrega"]);
  });

  it("prefiere las excepciones de la verificación y cae a las del acta", () => {
    expect(
      excepcionesDe({ excepciones: [{ rol: "a" }] }, { excepciones_firma: [{ rol: "b" }] }),
    ).toEqual([{ rol: "a" }]);
    expect(excepcionesDe(null, { excepciones_firma: [{ rol: "b" }] })).toEqual([{ rol: "b" }]);
  });
});

describe("exigeTotp", () => {
  it("`todos` lo exige siempre", () => {
    expect(exigeTotp({ politica: "todos", nivelExpediente: 1 })).toBe("si");
    expect(exigeTotp({ politica: "todos" })).toBe("si");
  });
  it("`clasificados` lo exige desde nivel 2 y no por debajo", () => {
    expect(exigeTotp({ politica: "clasificados", nivelExpediente: 2 })).toBe("si");
    expect(exigeTotp({ politica: "clasificados", nivelExpediente: 1 })).toBe("no");
  });
  it("`clasificados` con nivel desconocido lo ofrece como posible", () => {
    expect(exigeTotp({ politica: "clasificados" })).toBe("posible");
  });
  it("`off` no lo exige", () => {
    expect(exigeTotp({ politica: "off", nivelExpediente: 3 })).toBe("no");
  });
});

describe("relajaPolitica", () => {
  it("bajar la exigencia relaja", () => {
    expect(relajaPolitica("todos", "clasificados")).toBe(true);
    expect(relajaPolitica("clasificados", "off")).toBe(true);
  });
  it("endurecer o dejar igual no relaja", () => {
    expect(relajaPolitica("off", "todos")).toBe(false);
    expect(relajaPolitica("todos", "todos")).toBe(false);
  });
  it("desde null (heredado) se supone el más estricto: todo menos `todos` relaja", () => {
    expect(relajaPolitica(null, "off")).toBe(true);
    expect(relajaPolitica(null, "clasificados")).toBe(true);
    expect(relajaPolitica(null, "todos")).toBe(false);
  });
  it("un vigente desconocido se trata como `todos`", () => {
    expect(relajaPolitica("raro", "off")).toBe(true);
  });
});

describe("etiquetas", () => {
  it("`sellada_con_excepcion` NO se llama firmada y no es éxito", () => {
    const e = COMPLETITUD_MAP.sellada_con_excepcion;
    expect(e.label).toMatch(/ninguna parte firmó/i);
    expect(e.label).toMatch(/solo el sello de la entidad/i);
    expect(e.label).not.toMatch(/^firmada/i);
    expect(e.status).not.toBe("success");
    expect(COMPLETITUD_MAP.firmada_con_excepcion.status).not.toBe("success");
  });

  it("tiene etiqueta cada valor de completitud del backend", () => {
    for (const k of [
      "sellada",
      "firma_personal_pendiente",
      "firmada_receptor",
      "firmada_remitente",
      "firmada_completa",
      "firmada_con_excepcion",
      "sellada_con_excepcion",
    ]) {
      expect(COMPLETITUD_MAP[k]).toBeTruthy();
    }
  });

  it("traduce la advertencia conocida y no esconde una desconocida", () => {
    expect(etiquetaAdvertencia("tsa_sin_esscertid_anterior_al_corte")).toMatch(/ESSCertID/);
    expect(etiquetaAdvertencia("algo_nuevo")).toMatch(/algo_nuevo/);
  });
});

describe("FIRMA-2: personas, soporte y verificación del fundamento", () => {
  it("describe la persona con su fuente y declara la no resuelta", () => {
    expect(describirPersona("Ana Ruiz", "Jefe de archivo", "certificado")).toBe(
      "Ana Ruiz, Jefe de archivo (certificado)",
    );
    expect(describirPersona(null, null, "no_resuelta")).toMatch(/No resuelta/);
    expect(describirPersona(null, null, undefined)).toMatch(/No resuelta/);
  });

  it("solo administración 3 verifica el fundamento", () => {
    expect(puedeVerificarFundamento({ USUA_PERM_ADMIN: 3 })).toBe(true);
    expect(puedeVerificarFundamento({ USUA_PERM_ADMIN: 2 })).toBe(false);
    expect(puedeVerificarFundamento(undefined)).toBe(false);
  });

  it("una copia no comprobable (null) no se da por buena", () => {
    const r = resumenVerificacionFundamento({
      verificable: true,
      columna: true,
      xml: null,
      asiento: true,
      coincide: false,
    });
    expect(r.status).toBe("error");
    expect(r.copias.find((c) => c.nombre === "Documento firmado").estado).toBe("no comprobada");
  });

  it("todas las copias coinciden es éxito; una v1 no es verificable", () => {
    expect(
      resumenVerificacionFundamento({
        verificable: true,
        columna: true,
        xml: true,
        asiento: true,
        coincide: true,
      }).status,
    ).toBe("success");
    const v1 = resumenVerificacionFundamento({
      verificable: false,
      motivo: "constancia_sin_compromiso_salado",
    });
    expect(v1.status).toBe("warning");
    expect(v1.texto).toMatch(/sin compromiso con sal/);
  });
});

describe("etiquetas de la cadena", () => {
  it("traduce un motivo de cadena con rol y número de declaración", () => {
    expect(etiquetaCadenaMotivo("recibe#2: ordinal_no_consecutivo")).toBe(
      "Receptor(a) (recibe), declaración 2: la numeración no es consecutiva",
    );
  });
  it("un motivo desconocido no se esconde ni inventa", () => {
    expect(etiquetaMotivoEnlace("algo_nuevo")).toMatch(/algo_nuevo/);
    expect(etiquetaMotivosEnlace("ordinal_distinto, ")).toMatch(/número de declaración/);
  });
  it("describe los artefactos del reintento", () => {
    expect(etiquetaArtefacto("sobre", "agotado")).toBe(
      "sobre firmado: se agotaron los reintentos automáticos",
    );
  });
});
