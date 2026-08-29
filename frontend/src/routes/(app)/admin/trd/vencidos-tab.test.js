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

describe("/admin/trd — decidir sobre un vencimiento (RF-TRD-06)", () => {
  const conDecision = (o = {}) => ({ ...vencido(), ...o });

  beforeEach(() => {
    clearSession();
  });
  afterEach(() => clearSession());

  function sesionCon(levels) {
    initSession({ ...sesion, permissions: ["USUA_PERM_TRD"], permission_levels: levels });
  }

  it("con nivel 3 se ofrecen las dos decisiones", () => {
    sesionCon({ USUA_PERM_TRD: 3 });
    render(TrdPage, { props: { data: datos({ vencidos: [conDecision()] }) } });
    const panel = document.getElementById("panel-vencidos");
    expect(panel.textContent).toMatch(/Confirmar/);
    expect(panel.textContent).toMatch(/Aplazar/);
  });

  it("con nivel de lectura NO se ofrece decidir — espejo del gate real (min_crud=3)", () => {
    sesionCon({ USUA_PERM_TRD: 1 });
    render(TrdPage, { props: { data: datos({ vencidos: [conDecision()] }) } });
    const panel = document.getElementById("panel-vencidos");
    expect(panel.textContent).toMatch(/Sin decidir/);
    expect(panel.querySelector("button")).toBeNull();
  });

  it("sin nivel en la sesión falla cerrado: no se ofrece el acto", () => {
    sesionCon({});
    render(TrdPage, { props: { data: datos({ vencidos: [conDecision()] }) } });
    expect(document.getElementById("panel-vencidos").textContent).toMatch(/Sin decidir/);
  });

  it("una fila ya confirmada muestra su estado, no los botones", () => {
    sesionCon({ USUA_PERM_TRD: 3 });
    render(TrdPage, {
      props: { data: datos({ vencidos: [conDecision({ decision: "confirmada" })] }) },
    });
    const panel = document.getElementById("panel-vencidos");
    expect(panel.textContent).toMatch(/Confirmada/);
    // Se acota a la FILA: el panel tiene ahora otros botones (proponer lote)
    // que no tienen nada que ver con decidir sobre esta fila.
    const botonesDeLaFila = [...panel.querySelectorAll("tbody button")].map((b) => b.textContent);
    expect(botonesDeLaFila.join(" ")).not.toMatch(/Confirmar|Aplazar/);
  });

  it("una fila aplazada dice cuándo vuelve", () => {
    sesionCon({ USUA_PERM_TRD: 3 });
    render(TrdPage, {
      props: {
        data: datos({
          vencidos: [conDecision({ decision: "aplazada", revisar_de_nuevo_el: "2027-03-01" })],
        }),
      },
    });
    expect(document.getElementById("panel-vencidos").textContent).toMatch(/Se revisa el/);
  });
});

describe("/admin/trd — proponer un lote de eliminación (RF-TRD-07, fase 1)", () => {
  const confirmado = (o = {}) => ({ ...vencido(), decision: "confirmada", ...o });

  beforeEach(() => {
    clearSession();
    initSession({
      ...sesion,
      permissions: ["USUA_PERM_TRD"],
      permission_levels: { USUA_PERM_TRD: 3 },
    });
  });
  afterEach(() => clearSession());

  it("solo las filas CONFIRMADAS y que destruyen llevan casilla", () => {
    render(TrdPage, {
      props: {
        data: datos({
          vencidos: [
            confirmado({ id: "a", code: "EXP-E", disposition_code: "E" }),
            confirmado({ id: "b", code: "EXP-CT", disposition_code: "CT" }),
            vencido({ id: "c", code: "EXP-SIN-DECIDIR", decision: null }),
          ],
        }),
      },
    });
    const panel = document.getElementById("panel-vencidos");
    const casillas = panel.querySelectorAll('input[type="checkbox"]');
    // Conservación Total NO se elimina jamás, y sin confirmar no hay propuesta.
    expect(casillas.length).toBe(1);
    expect(casillas[0].getAttribute("aria-label")).toMatch(/EXP-E/);
  });

  it("sin ninguna fila elegible no se ofrece proponer nada", () => {
    render(TrdPage, {
      props: { data: datos({ vencidos: [vencido({ decision: null })] }) },
    });
    expect(document.getElementById("panel-vencidos").textContent).not.toMatch(
      /Proponer lote de eliminación/,
    );
  });

  it("con filas elegibles se ofrece, pero deshabilitado hasta seleccionar", () => {
    render(TrdPage, { props: { data: datos({ vencidos: [confirmado()] }) } });
    const panel = document.getElementById("panel-vencidos");
    expect(panel.textContent).toMatch(/Proponer lote de eliminación/);
    const boton = [...panel.querySelectorAll("button")].find((b) =>
      b.textContent.includes("Proponer lote de eliminación"),
    );
    expect(boton.disabled).toBe(true);
  });

  it("sin nivel 3 no se ofrece seleccionar ni proponer", () => {
    clearSession();
    initSession({
      ...sesion,
      permissions: ["USUA_PERM_TRD"],
      permission_levels: { USUA_PERM_TRD: 1 },
    });
    render(TrdPage, { props: { data: datos({ vencidos: [confirmado()] }) } });
    const panel = document.getElementById("panel-vencidos");
    expect(panel.querySelectorAll('input[type="checkbox"]').length).toBe(0);
    expect(panel.textContent).not.toMatch(/Proponer lote de eliminación/);
  });
});
