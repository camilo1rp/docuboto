// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * vencidos-tab.test.js — La pestaña «Disposición vencida» de /admin/trd (RF-TRD-08).
 *
 * El endpoint existía y no tenía puerta: el patrón que este proyecto lleva
 * persiguiendo toda la revisión. Lo que se prueba aquí es lo que la pantalla
 * NO puede permitirse decir mal:
 *
 *  - Una fila de **Conservación Total** no puede leerse como «hay que borrar
 *    esto». Vencer el archivo central en CT significa transferir al histórico y
 *    conservar para siempre; presentarla junto a las de eliminación sin
 *    distinguirlas convertiría la cola en una lista de destrucción que incluye
 *    justo los expedientes que jamás se destruyen.
 *  - Una fila con disposición de eliminación cuyo instrumento congelado **no
 *    está convalidado** debe decir que no puede gobernarla. Si no, el
 *    funcionario descubriría el límite fallando, delante del gate.
 *  - La pantalla no promete ejecutar nada: la eliminación efectiva no existe
 *    todavía y prometerla sería la clase de mecanismo que reporta éxito sin
 *    verificar nada.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/admin/trd") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: TrdPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const sesion = {
  sub: "u-1",
  email: "a@b.co",
  name: "Archivista",
  preferred_username: "archivista",
  roles: [],
  tenant_slug: "icetex",
  permissions: ["USUA_PERM_TRD"],
};

const vencido = (o = {}) => ({
  id: crypto.randomUUID(),
  code: "EXP-2026-0031",
  status: "closed",
  nivel_seguridad: 1,
  trd_serie_code: "DEM-100",
  closed_at: "2016-01-01T00:00:00Z",
  fase: "central",
  fecha_vencimiento: "2026-07-29",
  dias_vencido: 30,
  disposition_code: "E",
  disposition_label: "Eliminación",
  serie_estado: "convalidada",
  acto_administrativo: "Res. 214 de 2026",
  gobierna_eliminacion: true,
  accion: "Eliminar con acta, previo aval del Comité y publicidad del inventario",
  ...o,
});

const datos = (o = {}) => ({
  series: [],
  seriesError: null,
  tipos: [],
  tiposError: null,
  revision: [],
  revisionError: null,
  vencidos: [],
  vencidosError: null,
  ...o,
});

describe("/admin/trd — pestaña de disposición vencida", () => {
  beforeEach(() => {
    clearSession();
    initSession(sesion);
  });
  afterEach(() => clearSession());

  it("la pestaña existe y se puede alcanzar", () => {
    render(TrdPage, { props: { data: datos() } });
    expect(screen.getByRole("tab", { name: /Disposición vencida/i })).toBeInTheDocument();
  });

  it("Conservación Total NO se presenta como algo que eliminar", () => {
    render(TrdPage, {
      props: {
        data: datos({
          vencidos: [
            vencido({
              disposition_code: "CT",
              disposition_label: "Conservación Total",
              accion: "Transferir al archivo histórico y conservar permanentemente (no se elimina)",
            }),
          ],
        }),
      },
    });
    const panel = document.getElementById("panel-vencidos");
    expect(panel.textContent).toMatch(/conservar permanentemente/i);
    expect(panel.textContent).toMatch(/no se elimina/i);
    expect(panel.textContent).toMatch(/Ninguno tiene disposición de eliminación/i);
  });

  it("una eliminación con instrumento no convalidado avisa de que no la gobierna", () => {
    render(TrdPage, {
      props: {
        data: datos({
          vencidos: [vencido({ serie_estado: "migrada", gobierna_eliminacion: false })],
        }),
      },
    });
    const panel = document.getElementById("panel-vencidos");
    expect(panel.textContent).toMatch(/no está convalidado/i);
  });

  it("una eliminación con instrumento convalidado no muestra ese aviso", () => {
    render(TrdPage, { props: { data: datos({ vencidos: [vencido()] }) } });
    const panel = document.getElementById("panel-vencidos");
    expect(panel.textContent).not.toMatch(/no está convalidado/i);
    expect(panel.textContent).toMatch(/1 con disposición de eliminación/i);
  });

  it("la pantalla dice que todavía no ejecuta ningún acto", () => {
    render(TrdPage, { props: { data: datos({ vencidos: [vencido()] }) } });
    const panel = document.getElementById("panel-vencidos");
    expect(panel.textContent).toMatch(/no ejecuta ninguno todav[ií]a/i);
  });

  it("el error de carga no se confunde con «no hay vencimientos»", () => {
    render(TrdPage, {
      props: { data: datos({ vencidosError: "No se pudo conectar al servidor." }) },
    });
    const panel = document.getElementById("panel-vencidos");
    expect(panel.textContent).toMatch(/No se pudo conectar/i);
    expect(panel.textContent).not.toMatch(/Ningún expediente con la retención vencida/i);
  });

  it("vacío legítimo: lo dice sin alarmar", () => {
    render(TrdPage, { props: { data: datos() } });
    const panel = document.getElementById("panel-vencidos");
    expect(panel.textContent).toMatch(/Ningún expediente con la retención vencida/i);
  });
});
