// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * El formulario de transferencia se puede rellenar sin abrir la base de datos.
 *
 * Pedía tres UUID escritos a mano —expediente, ubicación origen y ubicación
 * destino— y ninguna pantalla del sistema muestra esos identificadores. Es el
 * mismo defecto que se corrigió en `/archivo-fisico`, que aquí había quedado
 * sin corregir: transferir un expediente, que es el acto por el que sale del
 * archivo de gestión, exigía consultar la base de datos para poder empezar.
 *
 * Además las etiquetas obligatorias salían con doble asterisco («Tipo * *»),
 * porque el componente ya añade su propia marca.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";

vi.mock("$app/navigation", () => ({ goto: vi.fn() }));

const { default: TransferenciasPage } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const UBICACIONES = [
  {
    id: "11111111-2222-4333-8444-555555555555",
    codigo: "DEP-001",
    nombre: "Depósito Central 1",
    ruta: "SED-001 / DEP-001",
  },
  {
    id: "66666666-7777-4888-8999-aaaaaaaaaaaa",
    codigo: "DEP-002",
    nombre: "Depósito Central 2",
    ruta: "SED-001 / DEP-002",
  },
];

function datos(overrides = {}) {
  return {
    transferencias: { data: [], error: null },
    ubicaciones: UBICACIONES,
    ...overrides,
  };
}

const originalFetch = globalThis.fetch;

beforeEach(() => {
  clearSession();
  initSession({
    sub: "u-1",
    name: "Jefe",
    tenant_slug: "demo",
    permissions: ["USUA_PERM_EXPEDIENTE", "USUA_PERM_TRANSFERENCIA"],
    permission_levels: { USUA_PERM_EXPEDIENTE: 3, USUA_PERM_TRANSFERENCIA: 3 },
  });
  globalThis.fetch = vi.fn(async () => new Response(JSON.stringify({ items: [], total: 0 })));
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  clearSession();
  vi.restoreAllMocks();
});

async function abrirModal() {
  render(TransferenciasPage, { props: { data: datos() } });
  await fireEvent.click(screen.getByRole("button", { name: /nueva transferencia/i }));
  await waitFor(() => expect(document.getElementById("tr-expte-id")).toBeTruthy());
}

describe("/transferencias — el formulario no pide UUID", () => {
  it("el expediente se pide por su código, no por su identificador interno", async () => {
    await abrirModal();
    const campo = /** @type {HTMLInputElement} */ (document.getElementById("tr-expte-id"));
    expect(campo.placeholder).toMatch(/EXP-/);
    expect(campo.placeholder).not.toMatch(/UUID/i);
  });

  it("las ubicaciones se eligen de una lista, con su ruta completa", async () => {
    // Un código suelto («DEP-001») no dice dónde está: en un depósito hay
    // muchas gavetas tres. La ruta es lo que ubica.
    await abrirModal();
    const origen = /** @type {HTMLSelectElement} */ (document.getElementById("tr-origen-id"));
    expect(origen.tagName).toBe("SELECT");
    const textos = [...origen.options].map((o) => o.textContent.trim());
    expect(textos.some((t) => t.includes("SED-001 / DEP-001"))).toBe(true);
  });

  it("sin catálogo de ubicaciones, degrada a campo libre y lo explica", async () => {
    // Gemelo: la corrección no puede dejar la transferencia sin ubicación
    // posible en una entidad que aún no cargó su topografía.
    render(TransferenciasPage, { props: { data: datos({ ubicaciones: [] }) } });
    await fireEvent.click(screen.getByRole("button", { name: /nueva transferencia/i }));
    await waitFor(() => expect(document.getElementById("tr-origen-id")).toBeTruthy());

    expect(document.getElementById("tr-origen-id").tagName).toBe("INPUT");
    expect(screen.getByText(/no hay ubicaciones cargadas/i)).toBeTruthy();
  });

  it("ninguna etiqueta obligatoria lleva el asterisco escrito a mano", async () => {
    // `FormField` ya añade su marca: escribirlo también producía «Tipo * *».
    await abrirModal();
    const etiquetas = [...document.querySelectorAll(".op-modal label")].map((l) =>
      l.textContent.trim(),
    );
    const dobles = etiquetas.filter((t) => /\*[^*]*\*/.test(t));
    expect(dobles, `etiquetas con doble marca: ${dobles.join(" | ")}`).toEqual([]);
  });

  it("el aviso de campos obligatorios nombra lo que la persona ve, no el campo del API", async () => {
    await abrirModal();
    const { toasts } = await import("$lib/stores/notifications.js");
    const vistos = [];
    const off = toasts.subscribe((lista) => vistos.push(...lista.map((n) => n.message)));

    await fireEvent.click(screen.getByRole("button", { name: /^crear transferencia$/i }));
    await waitFor(() => expect(vistos.some((m) => /indique el tipo/i.test(m))).toBe(true));
    expect(vistos.some((m) => /expediente_id/.test(m))).toBe(false);
    off();
  });

  it("cada ubicación muestra su tipo de archivo, que es lo que decide si sirve", async () => {
    // Una transferencia primaria exige origen de «gestión» y destino de
    // «central» (Ley 594/2000 art. 23). Sin ver el tipo, elegir es adivinar y
    // el error solo aparece al enviar: 422 `ubicacion_incoherente_con_tipo`.
    render(TransferenciasPage, {
      props: {
        data: datos({
          ubicaciones: [
            { ...UBICACIONES[0], tipo_archivo: "gestion" },
            { ...UBICACIONES[1], tipo_archivo: "central" },
          ],
        }),
      },
    });
    await fireEvent.click(screen.getByRole("button", { name: /nueva transferencia/i }));
    await waitFor(() => expect(document.getElementById("tr-origen-id")).toBeTruthy());

    const textos = [...document.getElementById("tr-origen-id").options].map((o) =>
      o.textContent.trim(),
    );
    expect(textos.some((t) => /gestion/.test(t))).toBe(true);
    expect(textos.some((t) => /central/.test(t))).toBe(true);
  });

  it("una ubicación SIN tipo lo dice, en vez de parecer válida", async () => {
    // Gemelo: el silencio es el defecto. Una ubicación sin tipo no admite
    // ninguna transferencia, y hoy tampoco se puede corregir después.
    await abrirModal();
    const textos = [...document.getElementById("tr-origen-id").options].map((o) =>
      o.textContent.trim(),
    );
    expect(textos.some((t) => /sin tipo de archivo/i.test(t))).toBe(true);
  });
});

describe("/transferencias — el LISTADO tampoco muestra UUID", () => {
  /**
   * La mitad que faltaba de la corrección anterior. El detalle y el acta ya
   * resolvían el código del expediente y el nombre de los depósitos; el
   * listado, que es la primera pantalla que se ve, seguía pintando tres UUID.
   *
   * Y lo hacía con `t.expediente_id ?? t.expediente?.code`: el identificador
   * iba PRIMERO en la cadena de `??`, así que el código no podía aparecer ni
   * aunque llegara — la misma forma de defecto que ya ha salido varias veces
   * en este proyecto.
   */
  const EXP_ID = "7ce8f33d-0dd9-4ecc-9785-423f731032a8";

  function listado(overrides = {}) {
    return datos({
      transferencias: {
        data: [
          {
            id: "b4d1188c-066b-44ef-b47c-de1fd6519a3f",
            tipo: "primaria",
            estado: "recibida",
            expediente_id: EXP_ID,
            expediente_codigo: "EXP-2026-0020",
            expediente_nombre: "Certificación laboral",
            ubicacion_origen_id: UBICACIONES[0].id,
            ubicacion_destino_id: UBICACIONES[1].id,
            created_at: "2026-08-19T10:00:00Z",
          },
        ],
        error: null,
      },
      ...overrides,
    });
  }

  it("muestra el código del expediente y el nombre de los depósitos, no los identificadores", async () => {
    render(TransferenciasPage, { props: { data: listado() } });

    await waitFor(() => expect(screen.getByText("EXP-2026-0020")).toBeTruthy());
    expect(screen.getByText(/Depósito Central 1/)).toBeTruthy();
    expect(screen.getByText(/Depósito Central 2/)).toBeTruthy();
    // Y los UUID desaparecen de la fila: si siguieran, la corrección sería
    // cosmética (añadir la etiqueta al lado del identificador no arregla nada).
    expect(screen.queryByText(EXP_ID)).toBeNull();
    expect(screen.queryByText(UBICACIONES[0].id)).toBeNull();
  });

  it("cae al identificador cuando NO se pudo resolver, en vez de callar", async () => {
    // Control negativo, y es una decisión, no un descuido: si el catálogo no
    // cargó —o si es un expediente que quien mira no tiene permiso de leer—,
    // mostrar «—» borraría la fila de la vista. El identificador al menos
    // permite referirse a ella.
    const sinResolver = listado({ ubicaciones: [] });
    // El proxy no pudo resolver el expediente (catálogo caído, o sin permiso
    // de lectura): la fila llega SIN `expediente_codigo`.
    delete sinResolver.transferencias.data[0].expediente_codigo;
    render(TransferenciasPage, { props: { data: sinResolver } });

    await waitFor(() => expect(screen.getByText(EXP_ID)).toBeTruthy());
    expect(screen.getByText(UBICACIONES[0].id)).toBeTruthy();
  });
});
