// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * La publicidad previa vista por un ciudadano (RF-TRD-07, fase 2).
 *
 * Esta pantalla existe para que alguien pueda OPONERSE antes de que un
 * documento se destruya, así que lo que se prueba es que diga las tres cosas
 * sin las que no sirve: **hasta cuándo** se puede objetar, **por dónde**, y
 * **por qué falta una descripción** cuando falta — un hueco mudo se lee como
 * un dato que no llegó, y de eso depende que alguien sepa si tiene algo que
 * objetar.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/svelte";

const { default: DetallePage } = await import("./[id]/+page.svelte");
const { default: ListaPage } = await import("./+page.svelte");

const FUTURO = new Date(Date.now() + 30 * 864e5).toISOString().slice(0, 10);
const PASADO = "2020-01-01";

const item = (o = {}) => ({
  expediente_code: "EXP-2020-0001",
  expediente_nombre: "Contratos de obra 2020",
  nombre_reservado: false,
  trd_serie_code: "DEM-100",
  disposition_code: "E",
  fecha_vencimiento: "2026-01-01",
  fecha_extrema_inicial: "2020-01-01",
  fecha_extrema_final: "2020-12-31",
  radicados_incluidos: 12,
  ...o,
});

const lote = (o = {}) => ({
  id: "l-1",
  codigo: "LOTE-2026-001",
  estado: "publicado",
  publicado_at: "2026-08-28T00:00:00Z",
  publicado_hasta: FUTURO,
  fecha_aval: null,
  acto_administrativo: null,
  ejecutado_at: null,
  observaciones: null,
  anulado_motivo: null,
  total_items: 1,
  items: [item()],
  ...o,
});

describe("el inventario publicado dice cómo oponerse", () => {
  it("en plazo: dice hasta cuándo y por dónde", () => {
    render(DetallePage, { props: { data: { lote: lote() } } });
    expect(screen.getByRole("note").textContent).toMatch(/Puede oponerse hasta/);
    // El canal es la ventanilla que ya existe, no un formulario nuevo.
    const enlace = screen.getByRole("link", { name: /ventanilla única/i });
    expect(enlace.getAttribute("href")).toBe("/radicacion");
    expect(screen.getAllByText(/LOTE-2026-001/).length).toBeGreaterThan(0);
  });

  it("fuera de plazo: lo dice, y NO invita a oponerse", () => {
    render(DetallePage, { props: { data: { lote: lote({ publicado_hasta: PASADO }) } } });
    expect(document.body.textContent).toMatch(/El plazo para oponerse terminó/);
    expect(document.body.textContent).not.toMatch(/Puede oponerse hasta/);
  });

  it("ya ejecutado: el inventario se conserva como constancia", () => {
    render(DetallePage, {
      props: {
        data: {
          lote: lote({ estado: "ejecutado", ejecutado_at: "2026-10-01T00:00:00Z" }),
        },
      },
    });
    expect(document.body.textContent).toMatch(/ya se ejecutó/);
    expect(document.body.textContent).toMatch(/constancia de qué se eliminó/);
  });

  it("retirado: se dice que NO se va a eliminar, y por qué", () => {
    // El caso que la lista de estados dejaba fuera: un lote publicado y luego
    // anulado desaparecía de la vista pública, justo de quien leyó el
    // inventario y podía estar esperando saber en qué quedó su oposición.
    render(DetallePage, {
      props: {
        data: {
          lote: lote({
            estado: "anulado",
            publicado_hasta: PASADO,
            anulado_motivo: "Oposición de un tercero admitida por el Comité",
          }),
        },
      },
    });
    expect(document.body.textContent).toMatch(/se retiró y no se va a ejecutar/);
    expect(document.body.textContent).toMatch(/Oposición de un tercero admitida/);
    expect(document.body.textContent).not.toMatch(/Puede oponerse hasta/);
  });
});

describe("la descripción reservada se explica, no se deja muda", () => {
  it("dice que la denominación está reservada y por qué", () => {
    render(DetallePage, {
      props: {
        data: {
          lote: lote({
            items: [item({ expediente_nombre: null, nombre_reservado: true })],
          }),
        },
      },
    });
    expect(document.body.textContent).toMatch(/Denominación reservada por ley/);
    expect(document.body.textContent).toMatch(/Ley 1712/);
    // Y la fila SIGUE trayendo lo que permite objetar.
    expect(document.body.textContent).toMatch(/DEM-100/);
    expect(document.body.textContent).toMatch(/EXP-2020-0001/);
  });

  it("sin reservados no aparece el aviso, para no alarmar de nada", () => {
    render(DetallePage, { props: { data: { lote: lote() } } });
    expect(document.body.textContent).not.toMatch(/Denominación reservada/);
    expect(document.body.textContent).toMatch(/Contratos de obra 2020/);
  });
});

describe("el listado", () => {
  it("sin portal configurado se declara no disponible, no vacío", () => {
    render(ListaPage, { props: { data: { disponible: false, lotes: [], loadError: null } } });
    expect(document.body.textContent).toMatch(/Publicidad no disponible/);
    expect(document.body.textContent).not.toMatch(/No hay inventarios publicados/);
  });

  it("vacío legítimo se distingue del fallo de carga", () => {
    render(ListaPage, { props: { data: { disponible: true, lotes: [], loadError: null } } });
    expect(document.body.textContent).toMatch(/No hay inventarios publicados/);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("fallo de carga se dice como tal", () => {
    render(ListaPage, {
      props: { data: { disponible: true, lotes: [], loadError: "No se pudo conectar." } },
    });
    expect(screen.getByRole("alert").textContent).toMatch(/No se pudo conectar/);
    expect(document.body.textContent).not.toMatch(/No hay inventarios publicados/);
  });

  it("un lote en plazo invita a oponerse desde la lista", () => {
    render(ListaPage, {
      props: { data: { disponible: true, lotes: [lote()], loadError: null } },
    });
    expect(document.body.textContent).toMatch(/Puede oponerse hasta/);
  });
});
