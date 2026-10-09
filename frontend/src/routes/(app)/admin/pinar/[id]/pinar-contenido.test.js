// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * pinar-contenido.test.js — El CONTENIDO de un plan PINAR (Fase 8, 2026-08-29).
 *
 * Hasta ahora el tablero salía siempre vacío y la propia pantalla lo declaraba
 * como «fase posterior de PINAR»: el backend tenía 31 endpoints y la interfaz
 * consumía 6, así que no había forma de crear un aspecto, un objetivo ni un
 * proyecto. Un tablero que no puede llenarse nunca es un falso verde.
 *
 * Lo que se prueba es lo que esta pantalla no se puede permitir decir mal:
 *
 * * **Los dos ciclos de vida no se solapan.** La estructura solo se edita en
 *   `borrador`; el seguimiento solo con el plan `aprobado` o `en_ejecucion`.
 *   Nunca las dos cosas a la vez — ofrecer la que no toca haría descubrir el
 *   límite chocando con un 409.
 * * **Un aspecto sin priorizar no es prioridad cero**: confundirlos lo mandaría
 *   al final de la lista como si ya se hubiera decidido que no urge.
 * * **La tabla se presenta ordenada por prioridad**, que es la razón de ser de
 *   la matriz.
 * * **Un fallo de carga no se lee como «este plan está vacío»** — que es
 *   exactamente lo que la pantalla llevaba diciendo desde que existe.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/svelte";

vi.mock("$app/navigation", () => ({ invalidateAll: vi.fn(), goto: vi.fn() }));

const { default: PinarDetailPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const EJES = [
  { code: 1, nombre: "Administrativo" },
  { code: 2, nombre: "Legal" },
  { code: 3, nombre: "Económico" },
  { code: 4, nombre: "Tecnológico" },
  { code: 5, nombre: "Archivístico" },
];

const sesion = (permissions = ["USUA_PERM_ADMIN"]) => ({
  sub: "u-1",
  email: "admin@orpyca.com",
  name: "Administradora",
  preferred_username: "administradora",
  roles: [],
  tenant_slug: "icetex",
  permissions,
  permission_levels: Object.fromEntries(permissions.map((p) => [p, 3])),
});

const texto = () => document.body.textContent.replace(/\s+/g, " ");

function datos({ estado = "borrador", ...extra } = {}) {
  return {
    plan: {
      id: "p-1",
      version: 1,
      estado,
      vision: "Visión institucional",
      vigencia_desde: "2026-01-01",
      vigencia_hasta: "2029-12-31",
      acto_administrativo: estado === "borrador" ? null : "Res. 100",
      aprobado_por: null,
      aprobado_at: null,
      created_at: "2026-01-01T10:00:00Z",
    },
    tablero: { data: { plan_id: "p-1", por_objetivo: [], por_eje: [] }, error: null },
    ejes: EJES,
    aspectos: [],
    priorizacion: [],
    objetivos: [],
    proyectos: [],
    instrumentos: [],
    contenidoError: null,
    ...extra,
  };
}

const aspecto = (id, descripcion) => ({ id, plan_id: "p-1", descripcion, riesgo: null, orden: 0 });

async function abrirPestana(nombre) {
  await fireEvent.click(screen.getByRole("tab", { name: new RegExp(nombre, "i") }));
}

describe("/admin/pinar/[id] — aspectos y matriz de priorización", () => {
  beforeEach(() => {
    clearSession();
    initSession(sesion());
  });
  afterEach(() => clearSession());

  it("la pestaña existe: el contenido del plan ya se puede alcanzar", () => {
    render(PinarDetailPage, { props: { data: datos() } });
    expect(screen.getByRole("tab", { name: /Aspectos y priorización/i })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Objetivos y proyectos/i })).toBeInTheDocument();
  });

  it("sin aspectos DICE por qué importa, no deja un vacío mudo", async () => {
    render(PinarDetailPage, { props: { data: datos() } });
    await abrirPestana("Aspectos");
    expect(texto()).toMatch(/no hay qué priorizar/i);
  });

  it("en borrador ofrece crear y priorizar", async () => {
    render(PinarDetailPage, {
      props: { data: datos({ aspectos: [aspecto("a-1", "Depósito sin control ambiental")] }) },
    });
    await abrirPestana("Aspectos");
    expect(screen.getByRole("button", { name: /Nuevo aspecto/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /^Priorizar$/i })).toBeInTheDocument();
  });

  it("aprobado: NO ofrece editar, y explica que hay que reformular", async () => {
    render(PinarDetailPage, {
      props: {
        data: datos({
          estado: "aprobado",
          aspectos: [aspecto("a-1", "Depósito sin control ambiental")],
        }),
      },
    });
    await abrirPestana("Aspectos");
    expect(screen.queryByRole("button", { name: /Nuevo aspecto/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Priorizar$/i })).toBeNull();
    expect(texto()).toMatch(/congelado/i);
    expect(texto()).toMatch(/reformular/i);
  });

  it("«sin priorizar» NO se presenta como prioridad cero", async () => {
    render(PinarDetailPage, {
      props: { data: datos({ aspectos: [aspecto("a-1", "Depósito sin control")] }) },
    });
    await abrirPestana("Aspectos");
    expect(texto()).toMatch(/Sin priorizar/i);
  });

  it("la tabla se ordena por prioridad descendente, que es para lo que sirve", async () => {
    render(PinarDetailPage, {
      props: {
        data: datos({
          aspectos: [aspecto("a-1", "AAA poco urgente"), aspecto("a-2", "BBB muy urgente")],
          priorizacion: [
            { aspecto_id: "a-1", descripcion: "AAA poco urgente", prioridad: 12, impactos: {} },
            { aspecto_id: "a-2", descripcion: "BBB muy urgente", prioridad: 44, impactos: {} },
          ],
        }),
      },
    });
    await abrirPestana("Aspectos");
    const t = texto();
    expect(t.indexOf("BBB muy urgente")).toBeLessThan(t.indexOf("AAA poco urgente"));
  });
});

describe("/admin/pinar/[id] — objetivos, proyectos y seguimiento", () => {
  beforeEach(() => {
    clearSession();
    initSession(sesion());
  });
  afterEach(() => clearSession());

  const proyecto = (o = {}) => ({
    id: "pr-1",
    plan_id: "p-1",
    objetivo_id: "o-1",
    nombre: "Adecuación del depósito",
    meta: null,
    indicador: null,
    responsable: "Archivo central",
    recurso: null,
    fecha_inicio: "2026-02-01",
    fecha_fin: "2026-11-30",
    estado: "planeado",
    avance_pct: 0,
    ...o,
  });
  const objetivo = { id: "o-1", plan_id: "p-1", descripcion: "Mejorar la conservación", orden: 0 };

  it("en BORRADOR: se edita la estructura y NO se ofrece seguimiento", async () => {
    render(PinarDetailPage, {
      props: { data: datos({ objetivos: [objetivo], proyectos: [proyecto()] }) },
    });
    await abrirPestana("Objetivos");
    expect(screen.getByRole("button", { name: /Nuevo proyecto/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Seguimiento$/i })).toBeNull();
  });

  it("en EJECUCIÓN: se ofrece seguimiento y NO se edita la estructura", async () => {
    render(PinarDetailPage, {
      props: {
        data: datos({ estado: "en_ejecucion", objetivos: [objetivo], proyectos: [proyecto()] }),
      },
    });
    await abrirPestana("Objetivos");
    expect(screen.getByRole("button", { name: /^Seguimiento$/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Nuevo proyecto/i })).toBeNull();
    expect(texto()).toMatch(/registrar el avance/i);
  });

  it("CERRADO: ni estructura ni seguimiento", async () => {
    render(PinarDetailPage, {
      props: {
        data: datos({ estado: "cerrado", objetivos: [objetivo], proyectos: [proyecto()] }),
      },
    });
    await abrirPestana("Objetivos");
    expect(screen.queryByRole("button", { name: /^Seguimiento$/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Nuevo proyecto/i })).toBeNull();
    expect(texto()).toMatch(/ni admite cambios estructurales ni nuevos registros/i);
  });

  it("un proyecto SIN objetivo se señala: no entra en el tablero por objetivo", async () => {
    render(PinarDetailPage, {
      props: {
        data: datos({
          objetivos: [objetivo],
          proyectos: [proyecto({ id: "pr-2", objetivo_id: null, nombre: "Proyecto suelto" })],
        }),
      },
    });
    await abrirPestana("Objetivos");
    expect(texto()).toMatch(/no aparece en el tablero por objetivo/i);
  });

  it("sin nada, explica que por eso el tablero sigue vacío", async () => {
    render(PinarDetailPage, { props: { data: datos() } });
    await abrirPestana("Objetivos");
    expect(texto()).toMatch(/el tablero seguirá vacío/i);
  });
});

describe("/admin/pinar/[id] — instrumentos y errores de carga", () => {
  beforeEach(() => {
    clearSession();
    initSession(sesion());
  });
  afterEach(() => clearSession());

  it("la articulación se declara POR REFERENCIA, no por copia", async () => {
    render(PinarDetailPage, { props: { data: datos() } });
    await abrirPestana("Instrumentos");
    expect(texto()).toMatch(/por referencia, no por copia/i);
  });

  it("lista los instrumentos articulados con su tipo legible", async () => {
    render(PinarDetailPage, {
      props: {
        data: datos({
          instrumentos: [
            { id: "i-1", plan_id: "p-1", tipo: "trd", ref: "TRD v3", descripcion: null },
          ],
        }),
      },
    });
    await abrirPestana("Instrumentos");
    expect(texto()).toMatch(/Tabla de Retención/i);
    expect(texto()).toMatch(/TRD v3/);
  });

  it("un fallo de carga NO se presenta como «este plan está vacío»", async () => {
    render(PinarDetailPage, {
      props: { data: datos({ contenidoError: "No se pudo cargar «aspectos» del plan." }) },
    });
    await abrirPestana("Aspectos");
    expect(texto()).toMatch(/No se pudo cargar/i);
  });

  it("sin USUA_PERM_ADMIN no se ofrece ninguna edición del contenido", async () => {
    clearSession();
    initSession(sesion([]));
    render(PinarDetailPage, {
      props: { data: datos({ aspectos: [aspecto("a-1", "Depósito")] }) },
    });
    await abrirPestana("Aspectos");
    expect(screen.queryByRole("button", { name: /Nuevo aspecto/i })).toBeNull();
  });
});
