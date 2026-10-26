// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Quien participó en la transferencia ve su botón de firmar.
 *
 * La firma personal del acta (E17/E06) estaba construida en el backend y era
 * **inalcanzable desde la interfaz**, por dos defectos encadenados que se
 * tapaban el uno al otro:
 *
 *  1. Se comparaba `$sessionUser.sub` (Keycloak) contra `creada_por` /
 *     `enviada_por` / `decidida_por`, que el backend rellena con la identidad
 *     INTERNA del tenant (`auth_users.id`). Son dos UUID distintos para la
 *     misma persona: la comparación daba siempre falso.
 *  2. `$: misRoles = derivedRoles()` no declaraba dependencias: Svelte las
 *     deduce del statement, y lo que la función leía por dentro no contaba, así
 *     que el valor se congelaba tras el primer cálculo. Este segundo no llegó a
 *     manifestarse —`data` viene completo del `load` SSR— y se corrigió porque
 *     basta con que los datos pasen a llegar más tarde para que aparezca. El
 *     test de abajo lo comprueba con datos que llegan DESPUÉS del render, que
 *     es la única forma de distinguirlo: con datos presentes desde el principio
 *     el defecto es invisible, y un test que no distingue no vale.
 *
 * El primero por sí solo ya bastaba para que el botón no apareciera nunca.
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

const INTERNO = "aa7133ed-4a7c-4d26-84de-7af5355ae1dd";
const SUB_KEYCLOAK = "2d3f4045-c921-4fc0-91c6-4f8048dd0a17";

function datos() {
  return {
    transferencia: {
      data: {
        id: "t-1",
        tipo: "primaria",
        estado: "recibida",
        expediente_id: "exp-1",
        creada_por: INTERNO,
        enviada_por: INTERNO,
        decidida_por: INTERNO,
        created_at: "2026-08-19T20:50:00Z",
        enviada_at: "2026-08-19T21:04:00Z",
        recibida_at: "2026-08-19T21:18:00Z",
        observacion: "",
      },
      error: null,
    },
    acta: {
      data: {
        elaborado_por: INTERNO,
        entregado_por: INTERNO,
        recibido_por: INTERNO,
        congelada_at: "2026-08-19T21:18:00Z",
        indice_version: 4,
        acta_sha256: "a".repeat(64),
      },
      error: null,
    },
    verificacion: { data: { firmas_personales: [] }, error: null },
    expedienteCodigo: "EXP-2026-0020",
    expedienteNombre: "Certificación laboral",
    ubicaciones: [
      { id: "ub-1", codigo: "DEP-001", nombre: "Depósito Central 1", ruta: "SED-001 / DEP-001" },
    ],
  };
}

const originalFetch = globalThis.fetch;

beforeEach(() => {
  clearSession();
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({})));
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession();
  vi.restoreAllMocks();
});

describe("detalle de transferencia — firmar como el rol propio", () => {
  it("con la identidad INTERNA en sesión, aparece el botón de firmar", async () => {
    initSession({
      sub: SUB_KEYCLOAK,
      user_id: INTERNO,
      name: "Jefe",
      tenant_slug: "demo",
      permissions: ["USUA_PERM_EXPEDIENTE"],
      permission_levels: { USUA_PERM_EXPEDIENTE: 3 },
    });

    render(DetallePage, { props: { data: datos() } });

    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: /firmar como/i }).length).toBeGreaterThan(0),
    );
  });

  it("si la sesión solo trae el sub de Keycloak, NO se atribuye ningún rol", async () => {
    // Gemelo: la corrección no puede consistir en enseñar el botón siempre.
    // Firmar un acta como remitente sin haberla remitido sería peor que no
    // poder firmarla.
    initSession({
      sub: SUB_KEYCLOAK,
      name: "Jefe",
      tenant_slug: "demo",
      permissions: ["USUA_PERM_EXPEDIENTE"],
      permission_levels: { USUA_PERM_EXPEDIENTE: 3 },
    });

    render(DetallePage, { props: { data: datos() } });
    await new Promise((r) => setTimeout(r, 60));

    expect(screen.queryByRole("button", { name: /firmar como/i })).toBeNull();
  });

  it("quien NO participó no ve ningún botón de firmar", async () => {
    // El acta la firman sus responsables, no cualquiera con permiso de
    // escritura sobre expedientes.
    initSession({
      sub: "otro-sub",
      user_id: "11111111-2222-4333-8444-555555555555",
      name: "Otra",
      tenant_slug: "demo",
      permissions: ["USUA_PERM_EXPEDIENTE"],
      permission_levels: { USUA_PERM_EXPEDIENTE: 3 },
    });

    render(DetallePage, { props: { data: datos() } });
    await new Promise((r) => setTimeout(r, 60));

    expect(screen.queryByRole("button", { name: /firmar como/i })).toBeNull();
  });

  it("si los datos llegan DESPUÉS del primer render, los roles se recalculan", async () => {
    // Discrimina el segundo defecto: con `$: misRoles = derivedRoles()` sin
    // argumentos, este caso se queda en `[]` para siempre.
    initSession({
      sub: SUB_KEYCLOAK,
      user_id: INTERNO,
      name: "Jefe",
      tenant_slug: "demo",
      permissions: ["USUA_PERM_EXPEDIENTE"],
      permission_levels: { USUA_PERM_EXPEDIENTE: 3 },
    });

    const vacio = {
      transferencia: { data: null, error: null },
      acta: { data: null, error: null },
      verificacion: { data: null, error: null },
    };
    const { component } = render(DetallePage, { props: { data: vacio } });
    expect(screen.queryByRole("button", { name: /firmar como/i })).toBeNull();

    await component.$set({ data: datos() });

    await waitFor(() =>
      expect(screen.getAllByRole("button", { name: /firmar como/i }).length).toBeGreaterThan(0),
    );
  });
});

describe("detalle de transferencia — identidad legible", () => {
  it("el acta muestra el CÓDIGO del expediente, no su identificador interno", async () => {
    // El acta de entrega es un documento con valor probatorio: quien la firma
    // tiene que reconocer qué está firmando. `7ce8f33d-0dd9-…` no aparece
    // impreso en ninguna carátula.
    initSession({
      sub: SUB_KEYCLOAK,
      user_id: INTERNO,
      name: "Jefe",
      tenant_slug: "demo",
      permissions: ["USUA_PERM_EXPEDIENTE"],
      permission_levels: { USUA_PERM_EXPEDIENTE: 3 },
    });

    render(DetallePage, { props: { data: datos() } });

    await waitFor(() => expect(screen.getAllByText(/EXP-2026-0020/).length).toBeGreaterThan(0));
    expect(screen.queryByText(/^exp-1$/)).toBeNull();
  });

  it("sin catálogo, cae al identificador en vez de dejarlo vacío", async () => {
    // Gemelo: degradar a lo que había antes es aceptable; borrar el dato no.
    initSession({
      sub: SUB_KEYCLOAK,
      user_id: INTERNO,
      name: "Jefe",
      tenant_slug: "demo",
      permissions: ["USUA_PERM_EXPEDIENTE"],
      permission_levels: { USUA_PERM_EXPEDIENTE: 3 },
    });
    const sinCatalogo = { ...datos(), expedienteCodigo: null, expedienteNombre: null };

    render(DetallePage, { props: { data: sinCatalogo } });

    await waitFor(() => expect(screen.getAllByText(/exp-1/).length).toBeGreaterThan(0));
  });
});
