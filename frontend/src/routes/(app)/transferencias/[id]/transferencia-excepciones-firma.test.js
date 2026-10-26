// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /transferencias/[id] — las decisiones de firma (ADR-030, AREA-07-10) en el
 * certificado del acta: etiquetas honestas, avisos, política y excepciones.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));
vi.mock("$app/stores", () => ({
  page: writable({ url: new URL("http://localhost/transferencias/t-1"), params: { id: "t-1" } }),
  navigating: writable(null),
  updated: writable(false),
}));

const { default: DetallePage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const REC = "aa7133ed-4a7c-4d26-84de-7af5355ae1dd";
const ENV = "bb7133ed-4a7c-4d26-84de-7af5355ae1dd";
const ELAB = "cc7133ed-4a7c-4d26-84de-7af5355ae1dd";
const ADMIN = "dd7133ed-4a7c-4d26-84de-7af5355ae1dd";

function excepcion(rol, extra = {}) {
  return {
    id: `x-${rol}`,
    rol,
    motivo: "retiro",
    fundamento: null,
    fundamento_sha256: "cd".repeat(32),
    referencia_documental: "radicado:r1",
    declarada_en: "2026-10-09T10:00:00Z",
    firmada: true,
    sello_valida: true,
    revocada: false,
    ...extra,
  };
}

function datos(verificacion = {}) {
  return {
    transferencia: {
      data: {
        id: "t-1",
        tipo: "primaria",
        estado: "recibida",
        expediente_id: "e-1",
        creada_por: ELAB,
        enviada_por: ENV,
        decidida_por: REC,
        created_at: "2026-08-19T20:50:00Z",
      },
      error: null,
    },
    acta: {
      data: {
        elaborado_por: ELAB,
        entregado_por: ENV,
        recibido_por: REC,
        congelada_at: "2026-08-19T21:18:00Z",
        indice_version: 4,
        acta_fuid_sha256: "a".repeat(64),
        excepciones_firma: [],
      },
      error: null,
    },
    verificacion: {
      data: { firmas_personales: [], excepciones: [], advertencias: [], ...verificacion },
      error: null,
    },
    expedienteCodigo: "EXP-1",
    expedienteNombre: "X",
    expedienteRadicados: [
      { radicado_id: "11111111-1111-4111-8111-111111111111", tracking_number: "R-1" },
    ],
    ubicaciones: [],
  };
}

function sesion(userId, levels = {}) {
  initSession({
    sub: "kc",
    user_id: userId,
    name: "U",
    tenant_slug: "demo",
    permissions: ["USUA_PERM_EXPEDIENTE", ...Object.keys(levels)],
    permission_levels: { USUA_PERM_EXPEDIENTE: 3, ...levels },
  });
}

const originalFetch = globalThis.fetch;
beforeEach(() => {
  clearSession();
  globalThis.fetch = vi.fn(async () => new Response("{}"));
});
afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession();
  vi.restoreAllMocks();
});

describe("/transferencias/[id] — completitud honesta", () => {
  it("`sellada_con_excepcion` dice que ninguna parte firmó y NO dice «firmada»", async () => {
    sesion(ADMIN);
    render(DetallePage, {
      props: {
        data: datos({
          completitud: "sellada_con_excepcion",
          excepciones: [excepcion("entrega"), excepcion("recibe")],
        }),
      },
    });
    await waitFor(() =>
      expect(screen.getByText(/ninguna parte firmó; solo el sello de la entidad/i)).toBeTruthy(),
    );
    expect(screen.queryByText(/^Firmada con excepción/)).toBeNull();
    expect(screen.queryByText("Firma personal completa")).toBeNull();
  });

  it("`firmada_con_excepcion` se distingue de la completa", async () => {
    sesion(ADMIN);
    render(DetallePage, {
      props: {
        data: datos({
          completitud: "firmada_con_excepcion",
          firmas_personales: [{ rol: "entrega", valida: true }],
          excepciones: [excepcion("recibe")],
        }),
      },
    });
    await waitFor(() => expect(screen.getByText(/^Firmada con excepción/)).toBeTruthy());
    expect(screen.queryByText("Firma personal completa")).toBeNull();
  });
});

describe("/transferencias/[id] — avisos y política", () => {
  it("muestra las advertencias, la política y las constancias sin firma", async () => {
    sesion(ADMIN);
    render(DetallePage, {
      props: {
        data: datos({
          completitud: "sellada",
          advertencias: ["tsa_sin_esscertid_anterior_al_corte"],
          politica_xades: "clasificados",
          constancias_sin_firma: 2,
        }),
      },
    });
    await waitFor(() => expect(screen.getByText(/ESSCertID/)).toBeTruthy());
    expect(screen.getByTestId("politica-xades").textContent).toMatch(/clasificados/);
    expect(screen.getByTestId("constancias-sin-firma").textContent).toMatch(
      /2 constancias.*sin firma/,
    );
  });

  it("sin constancias sin firma ni advertencias no hay avisos", async () => {
    sesion(ADMIN);
    render(DetallePage, { props: { data: datos({ completitud: "sellada" }) } });
    await waitFor(() => expect(screen.getByText("Solo sello institucional")).toBeTruthy());
    expect(screen.queryByTestId("constancias-sin-firma")).toBeNull();
    expect(screen.queryByTestId("advertencias-verificacion")).toBeNull();
  });
});

describe("/transferencias/[id] — excepciones en la pantalla", () => {
  it("un administrador ajeno ve la lista y el botón de declarar", async () => {
    sesion(ADMIN, { USUA_PERM_ADMIN: 3, PERM_FIRMA: 2 });
    render(DetallePage, { props: { data: datos() } });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /declarar firma inobtenible/i })).toBeTruthy(),
    );
  });

  it("el remitente (parte) no ve el botón aunque tenga administración 3", async () => {
    sesion(ENV, { USUA_PERM_ADMIN: 3, PERM_FIRMA: 2 });
    render(DetallePage, { props: { data: datos() } });
    await waitFor(() => expect(screen.getByTestId("excepciones-firma")).toBeTruthy());
    expect(screen.queryByRole("button", { name: /declarar firma inobtenible/i })).toBeNull();
  });

  it("una parte con excepción VIGENTE no ve «Firmar como» (la API respondería 409)", async () => {
    sesion(REC);
    render(DetallePage, {
      props: { data: datos({ excepciones: [excepcion("recibe")] }) },
    });
    await waitFor(() => expect(screen.getByText("Firma inobtenible declarada")).toBeTruthy());
    expect(screen.queryByRole("button", { name: /firmar como/i })).toBeNull();
  });

  it("tras REVOCAR, la parte vuelve a ver «Firmar como»", async () => {
    sesion(REC);
    render(DetallePage, {
      props: { data: datos({ excepciones: [excepcion("recibe", { revocada: true })] }) },
    });
    await waitFor(() => expect(screen.getByRole("button", { name: /firmar como/i })).toBeTruthy());
    expect(screen.getByText("Excepción revocada · sin firma")).toBeTruthy();
  });
});
