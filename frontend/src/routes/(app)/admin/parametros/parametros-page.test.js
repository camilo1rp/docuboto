// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/parametros — la precarga de festivos tiene que VERSE.
 *
 * Encontrado navegando: se pulsa «Precargar festivos», el backend siembra 36
 * días correctamente (18 por año, calendario colombiano de la Ley 51/1983 con
 * Emiliani y móviles de Pascua)… y la tabla sigue diciendo «Sin festivos
 * registrados». El aviso de éxito llegaba a pedir «Recarga la página para
 * verlos en la tabla», así que la pantalla quedaba contradiciendo a la acción
 * que acababa de tener éxito — indistinguible de que no hubiera hecho nada.
 *
 * El calendario no es cosmético: gobierna el cálculo de vencimientos
 * (RF-RAD-04), y un plazo mal calculado es un incumplimiento de términos.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import { writable } from "svelte/store";

const invalidateAll = vi.fn(() => Promise.resolve());
vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll }));
vi.mock("$app/stores", () => ({
  page: writable({ url: new URL("http://localhost/admin/parametros") }),
  navigating: writable(null),
  updated: writable(false),
}));

const { default: ParametrosPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

function baseData(overrides = {}) {
  return { params: [], holidays: [], loadError: null, ...overrides };
}

beforeEach(() => {
  initSession({
    sub: "u-1",
    email: "admin@orpyca.com",
    name: "Administradora",
    preferred_username: "administradora",
    roles: [],
    tenant_slug: "demo",
    permissions: ["USUA_PERM_ADMIN"],
    permission_levels: { USUA_PERM_ADMIN: 3 },
  });
  invalidateAll.mockClear();
});

afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
});

async function irAFestivos() {
  await fireEvent.click(screen.getByRole("tab", { name: /festivos/i }));
}

describe("/admin/parametros — precarga de festivos", () => {
  it("refresca la tabla en vez de pedir que recargues la página", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve({ ok: true, status: 202, json: async () => ({ created: 36 }) })),
    );
    render(ParametrosPage, { props: { data: baseData() } });
    await irAFestivos();

    await fireEvent.click(screen.getByRole("button", { name: /precargar festivos/i }));

    // Lo que arregla el defecto: la vista se invalida sola.
    await waitFor(() => expect(invalidateAll).toHaveBeenCalledTimes(1));
    // Y el aviso ya no traslada el trabajo a la persona.
    expect(screen.queryByText(/recarga la página/i)).toBeNull();
  });

  it("cuando llegan los datos nuevos, la tabla DEJA de decir que no hay festivos", async () => {
    // Este es el test que faltaba, y la lección: el primero comprobaba que se
    // llamara a `invalidateAll()` y pasaba en verde mientras la pantalla seguía
    // rota. `holidays` era una copia ÚNICA de `data.holidays`, así que el `load`
    // se re-ejecutaba, `data` se actualizaba y la tabla seguía pintando el array
    // viejo. Comprobar la llamada no es comprobar el resultado.
    const { rerender } = render(ParametrosPage, { props: { data: baseData() } });
    await irAFestivos();
    expect(screen.getByText(/sin festivos registrados/i)).toBeTruthy();

    await rerender({
      data: baseData({
        holidays: [
          { id: "h-1", fecha: "2026-01-01", descripcion: "Año Nuevo" },
          { id: "h-2", fecha: "2026-01-12", descripcion: "Reyes Magos (Emiliani)" },
        ],
      }),
    });

    await waitFor(() => expect(screen.getByText(/Reyes Magos/i)).toBeTruthy());
    expect(screen.queryByText(/sin festivos registrados/i)).toBeNull();
  });

  it("si la precarga falla NO se invalida ni se dice que fue bien", async () => {
    // Control negativo: sin él, un `invalidateAll()` incondicional pasaría el
    // test de arriba y además borraría el estado tras un fallo, dejando a la
    // persona sin saber que no se sembró nada.
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve({
          ok: false,
          status: 400,
          json: async () => ({ detail: "rango inválido" }),
        }),
      ),
    );
    render(ParametrosPage, { props: { data: baseData() } });
    await irAFestivos();

    await fireEvent.click(screen.getByRole("button", { name: /precargar festivos/i }));

    // El error se comunica por aviso flotante, que lo monta el layout y aquí no
    // existe: la propiedad que este control tiene que fijar es la otra —que un
    // fallo NO refresque la vista como si hubiera ido bien.
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalled());
    expect(invalidateAll).not.toHaveBeenCalled();
  });
});
