// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * lotes-page.test.js — /admin/eliminacion (RF-TRD-07, fase 3).
 *
 * La pantalla que faltaba: se podía proponer un lote y no había dónde verlo
 * después. Lo que se prueba es lo que esta pantalla no se puede permitir decir
 * mal, que es casi todo — el procedimiento que gobierna termina destruyendo:
 *
 *  - **El aval no se ofrece con el plazo de publicidad abierto.** Ofrecer un
 *    botón que el backend va a rechazar hace descubrir el límite fallando; y
 *    aquí el límite no es técnico, es que un tercero todavía puede oponerse.
 *  - **El botón no es el gate**: el nivel CRUD gobierna los tres actos, y si
 *    el backend ya dijo 403 al cargar, manda el backend. Publicar, anular y el
 *    acta piden nivel 3; **avalar y ejecutar piden 4**, y quien propuso no
 *    avala ni quien avaló ejecuta (separación de funciones, AREA-06-01).
 *  - **La pantalla no promete lo que no hace**: las cinco fases están a la
 *    vista, y la quinta dice quién la ejecuta.
 *  - **El inventario recortado por clearance se explica**, en vez de dejar una
 *    resta muda entre lo que el lote declara y lo que se ve.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/svelte";
import { writable } from "svelte/store";

vi.mock("$app/navigation", () => ({ goto: vi.fn(), invalidateAll: vi.fn() }));
vi.mock("$app/stores", () => {
  const page = writable({ url: new URL("http://localhost/admin/eliminacion") });
  return { page, navigating: writable(null), updated: writable(false) };
});

const { default: Pagina } = await import("./+page.svelte");
const { initSession, clearSession } = await import("$lib/stores/auth.js");

const HOY = new Date();
const iso = (dias) => new Date(HOY.getTime() + dias * 86400000).toISOString().slice(0, 10);

const sesion = (nivel = 4, user_id = undefined) => ({
  sub: "u-1",
  user_id,
  email: "a@b.co",
  name: "Archivista",
  preferred_username: "archivista",
  roles: [],
  tenant_slug: "icetex",
  permissions: ["USUA_PERM_TRD"],
  permission_levels: { USUA_PERM_TRD: nivel },
});

const item = (o = {}) => ({
  expediente_id: crypto.randomUUID(),
  expediente_code: "EXP-2020-0001",
  expediente_nombre: "Contratos 2020",
  trd_serie_code: "DEM-100",
  disposition_code: "E",
  fecha_vencimiento: "2026-01-01",
  fecha_extrema_inicial: "2020-01-01",
  fecha_extrema_final: "2020-12-31",
  nivel_seguridad: 1,
  radicados_incluidos: 12,
  worm_retain_until: null,
  ...o,
});

const lote = (o = {}) => ({
  id: "11111111-1111-4111-8111-111111111111",
  codigo: "LOTE-2026-001",
  estado: "publicado",
  observaciones: null,
  creado_por: "u-1",
  creado_at: "2026-08-01T00:00:00Z",
  publicado_at: "2026-08-02T00:00:00Z",
  publicado_hasta: iso(30),
  acta_comite: null,
  fecha_aval: null,
  acto_administrativo: null,
  ejecutado_at: null,
  anulado_motivo: null,
  total_items: 1,
  items: [item()],
  ...o,
});

const datos = (o = {}) => ({
  lotes: [],
  lotesError: null,
  sinPermiso: false,
  seleccionado: "",
  detalle: null,
  detalleError: null,
  acta: null,
  actaError: null,
  actaSobreClearance: false,
  lapidas: [],
  ...o,
});

const acta = (o = {}) => ({
  lote_id: "11111111-1111-4111-8111-111111111111",
  acta_xml: "<actaEliminacion/>",
  acta_sha256: "a".repeat(64),
  metodo_borrado: "Borrado seguro de los objetos y picado de soportes",
  observaciones: null,
  generado_por: "u-1",
  generado_at: "2026-08-29T10:00:00Z",
  firma_estado: "firmado",
  firma_id: "22222222-2222-4222-8222-222222222222",
  xades_level: "XAdES-B",
  acreditado: false,
  firmado_at: "2026-08-29T10:00:05Z",
  last_error: null,
  worm_protegido_at: "2026-08-29T10:00:10Z",
  worm_retain_until: "2126-08-29T10:00:10Z",
  ...o,
});

const avalado = (o = {}) =>
  lote({ estado: "avalado", publicado_hasta: iso(-10), acto_administrativo: "Res. 512", ...o });

const conDetalle = (l) => datos({ lotes: [l], seleccionado: l.id, detalle: l });

// El texto se normaliza antes de compararlo: una frase de la plantilla llega a
// `textContent` con los saltos de línea del fuente dentro, y una aserción que
// no lo tenga en cuenta falla por dónde se cortó la línea, no por lo que dice.
const texto = () => document.body.textContent.replace(/\s+/g, " ");

describe("/admin/eliminacion", () => {
  beforeEach(() => {
    clearSession();
    initSession(sesion());
  });
  afterEach(() => clearSession());

  it("las cinco fases están a la vista y la quinta dice que la ejecuta otra persona", () => {
    render(Pagina, { props: { data: datos() } });
    expect(texto()).toMatch(/Aval del Comité/i);
    expect(texto()).toMatch(/Borrado seguro/i);
    expect(texto()).toMatch(/lo ejecuta otra persona/i);
  });

  it("sin lotes remite a donde se proponen, no a un vacío mudo", () => {
    render(Pagina, { props: { data: datos() } });
    expect(texto()).toMatch(/Disposición vencida/i);
  });

  it("un 403 del backend NO se presenta como «no hay lotes»", () => {
    render(Pagina, { props: { data: datos({ sinPermiso: true }) } });
    expect(texto()).toMatch(/USUA_PERM_TRD/);
    expect(texto()).not.toMatch(/No hay lotes de eliminación/i);
  });

  it("con el plazo de publicidad ABIERTO no se ofrece avalar, y se dice hasta cuándo", () => {
    render(Pagina, { props: { data: conDetalle(lote({ publicado_hasta: iso(15) })) } });
    expect(screen.queryByRole("button", { name: /Registrar el aval/i })).toBeNull();
    expect(texto()).toMatch(/todavía abierto hasta/i);
  });

  it("vencido el plazo, avalar SÍ se ofrece", () => {
    render(Pagina, { props: { data: conDetalle(lote({ publicado_hasta: iso(-1) })) } });
    expect(screen.getByRole("button", { name: /Registrar el aval/i })).toBeInTheDocument();
  });

  it("un lote en borrador ofrece publicar y no avalar", () => {
    render(Pagina, {
      props: {
        data: conDetalle(lote({ estado: "borrador", publicado_at: null, publicado_hasta: null })),
      },
    });
    expect(screen.getByRole("button", { name: /Publicar el inventario/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Registrar el aval/i })).toBeNull();
  });

  it("un lote EJECUTADO no ofrece retirarlo: lo destruido no se des-destruye", () => {
    render(Pagina, {
      props: {
        data: conDetalle(
          lote({
            estado: "ejecutado",
            ejecutado_at: "2026-09-01T00:00:00Z",
            publicado_hasta: iso(-30),
          }),
        ),
      },
    });
    expect(screen.queryByRole("button", { name: /Retirar el lote/i })).toBeNull();
  });

  it("con nivel CRUD 1 no se ofrece ninguno de los tres actos", () => {
    clearSession();
    initSession(sesion(1));
    render(Pagina, { props: { data: conDetalle(lote({ publicado_hasta: iso(-1) })) } });
    expect(screen.queryByRole("button", { name: /Publicar el inventario/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Registrar el aval/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Retirar el lote/i })).toBeNull();
  });

  it("el inventario recortado por clearance se EXPLICA, no deja una resta muda", () => {
    render(Pagina, { props: { data: conDetalle(lote({ total_items: 5, items: [item()] })) } });
    expect(texto()).toMatch(/Se muestran 1 de 5/i);
    expect(texto()).toMatch(/nivel de clasificación superior/i);
  });

  it("un lote retirado muestra su motivo", () => {
    render(Pagina, {
      props: {
        data: conDetalle(
          lote({ estado: "anulado", anulado_motivo: "Oposición de un tercero admitida" }),
        ),
      },
    });
    expect(texto()).toMatch(/Oposición de un tercero admitida/);
  });

  it("un lote publicado enlaza a su vista pública", () => {
    const l = lote();
    render(Pagina, { props: { data: conDetalle(l) } });
    const enlace = screen.getByRole("link", { name: /se ve sin autenticar/i });
    expect(enlace.getAttribute("href")).toBe(`/eliminacion/${l.id}`);
  });
});

describe("/admin/eliminacion — el acta (fase 4)", () => {
  beforeEach(() => {
    clearSession();
    initSession(sesion());
  });
  afterEach(() => clearSession());

  it("un lote avalado SIN acta ofrece generarla, y dice que va antes del borrado", () => {
    render(Pagina, { props: { data: conDetalle(avalado()) } });
    expect(screen.getByRole("button", { name: /Generar el acta/i })).toBeInTheDocument();
    expect(texto()).toMatch(/antes del borrado, no después/i);
  });

  it("un lote solo PUBLICADO no ofrece el acta: no hay nada que documentar", () => {
    render(Pagina, { props: { data: conDetalle(lote({ publicado_hasta: iso(-1) })) } });
    expect(screen.queryByRole("button", { name: /Generar el acta/i })).toBeNull();
  });

  it("con acta firmada muestra su SHA y declara que NO es firma acreditada", () => {
    const l = avalado();
    render(Pagina, { props: { data: { ...conDetalle(l), acta: acta() } } });
    expect(texto()).toMatch(/conservación permanente/i);
    expect(texto()).toMatch(new RegExp("a".repeat(64)));
    expect(texto()).toMatch(/no es una firma digital acreditada/i);
    expect(screen.queryByRole("button", { name: /Reintentar el sellado/i })).toBeNull();
  });

  it("dice si el acta está de verdad PROTEGIDA contra borrado, no solo firmada", () => {
    // Durante una fase entera el WORM del acta estuvo declarado como
    // pendiente: quien mira tiene derecho a ver la diferencia.
    const l = avalado();
    render(Pagina, { props: { data: { ...conDetalle(l), acta: acta() } } });
    expect(texto()).toMatch(/Protección WORM/i);
    expect(texto()).toMatch(/Protegida hasta/i);
    expect(texto()).toMatch(/Object-Lock/i);
  });

  it("firmada pero SIN candado todavía se dice «pendiente», no se calla", () => {
    const l = avalado();
    render(Pagina, {
      props: {
        data: {
          ...conDetalle(l),
          acta: acta({ worm_protegido_at: null, worm_retain_until: null }),
        },
      },
    });
    const datos = screen.getByText(/Protección WORM/i).closest("div");
    expect(datos.textContent).toMatch(/Pendiente/i);
  });

  it("con el sellado pendiente dice POR QUÉ y ofrece reintentarlo", () => {
    const l = avalado();
    render(Pagina, {
      props: {
        data: {
          ...conDetalle(l),
          acta: acta({
            firma_estado: "pendiente_firma",
            firma_id: null,
            last_error: "sello_ausente",
          }),
        },
      },
    });
    expect(texto()).toMatch(/Pendiente de firma — sello_ausente/i);
    expect(screen.getByRole("button", { name: /Reintentar el sellado/i })).toBeInTheDocument();
  });

  it("un acta por encima del clearance se EXPLICA, y no se ofrece recortada", () => {
    const l = avalado();
    render(Pagina, { props: { data: { ...conDetalle(l), actaSobreClearance: true } } });
    expect(texto()).toMatch(/no se puede mostrar recortada/i);
    expect(texto()).toMatch(/rompería su firma/i);
    expect(screen.queryByRole("button", { name: /Generar el acta/i })).toBeNull();
  });

  it("con nivel CRUD 1 no se ofrece generar ni reintentar", () => {
    clearSession();
    initSession(sesion(1));
    const l = avalado();
    render(Pagina, {
      props: {
        data: {
          ...conDetalle(l),
          acta: acta({ firma_estado: "pendiente_firma", firma_id: null }),
        },
      },
    });
    expect(screen.queryByRole("button", { name: /Reintentar el sellado/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /Generar el acta/i })).toBeNull();
  });
});

describe("/admin/eliminacion — la ejecución (fase 5)", () => {
  beforeEach(() => {
    clearSession();
    initSession(sesion());
  });
  afterEach(() => clearSession());

  it("SIN acta firmada no se ofrece ejecutar", () => {
    const l = avalado();
    render(Pagina, {
      props: {
        data: {
          ...conDetalle(l),
          acta: acta({ firma_estado: "pendiente_firma", firma_id: null }),
        },
      },
    });
    expect(screen.queryByRole("button", { name: /Ejecutar la eliminación/i })).toBeNull();
  });

  it("con acta firmada SÍ se ofrece, y el aviso dice qué queda y qué no", async () => {
    const l = avalado();
    render(Pagina, { props: { data: { ...conDetalle(l), acta: acta() } } });
    const boton = screen.getByRole("button", { name: /Ejecutar la eliminación/i });
    await fireEvent.click(boton);
    expect(texto()).toMatch(/destruye documentos de archivo y no se puede deshacer/i);
    expect(texto()).toMatch(/lápida/i);
    expect(texto()).toMatch(/No quedará el contenido ni la denominación/i);
  });

  it("el botón de destruir exige escribir el código del lote", async () => {
    const l = avalado();
    render(Pagina, { props: { data: { ...conDetalle(l), acta: acta() } } });
    await fireEvent.click(screen.getByRole("button", { name: /Ejecutar la eliminación/i }));
    const destruir = screen.getByRole("button", { name: /Eliminar definitivamente/i });
    // Un acto irreversible no debe estar a un clic de distancia.
    expect(destruir).toBeDisabled();

    const campo = document.getElementById("ejecutar-confirmacion");
    await fireEvent.input(campo, { target: { value: "LOTE-2026-001" } });
    expect(screen.getByRole("button", { name: /Eliminar definitivamente/i })).not.toBeDisabled();
  });

  it("un código equivocado NO habilita la destrucción", async () => {
    const l = avalado();
    render(Pagina, { props: { data: { ...conDetalle(l), acta: acta() } } });
    await fireEvent.click(screen.getByRole("button", { name: /Ejecutar la eliminación/i }));
    const campo = document.getElementById("ejecutar-confirmacion");
    await fireEvent.input(campo, { target: { value: "LOTE-2026-002" } });
    expect(screen.getByRole("button", { name: /Eliminar definitivamente/i })).toBeDisabled();
  });

  it("un lote ejecutado muestra las lápidas y dice que ya no existen", () => {
    const l = lote({ estado: "ejecutado", publicado_hasta: iso(-30) });
    render(Pagina, {
      props: {
        data: {
          ...conDetalle(l),
          acta: acta(),
          lapidas: [
            {
              expediente_id: "e-1",
              expediente_code: "EXP-2020-0001",
              trd_serie_code: "DEM-100",
              fecha_extrema_inicial: "2020-01-01",
              fecha_extrema_final: "2020-12-31",
              radicados_eliminados: 12,
              anexos_eliminados: 30,
              lote_id: l.id,
              lote_codigo: l.codigo,
              acta_comite: "Acta 07",
              acto_administrativo: "Res. 512",
              acta_sha256: "a".repeat(64),
              eliminado_por: "u-1",
              eliminado_at: "2026-09-01T10:00:00Z",
            },
          ],
        },
      },
    });
    expect(texto()).toMatch(/ya no existen/i);
    // La aserción se acota a la TABLA DE LÁPIDAS y no a la página entera: el
    // inventario congelado del lote sí conserva la denominación —es lo que el
    // acta describe, y su lectura ya está acotada por clearance—, así que un
    // `not.toMatch` sobre todo el documento estaría midiendo otra cosa.
    const tabla = screen.getByRole("table", { name: /Lápidas del lote/i });
    expect(tabla.textContent).toMatch(/EXP-2020-0001/);
    expect(tabla.textContent).toMatch(/DEM-100/);
    // La denominación NO está en la lápida: era contenido.
    expect(tabla.textContent).not.toMatch(/Contratos 2020/);
  });

  it("con nivel CRUD 1 no se ofrece ejecutar", () => {
    clearSession();
    initSession(sesion(1));
    const l = avalado();
    render(Pagina, { props: { data: { ...conDetalle(l), acta: acta() } } });
    expect(screen.queryByRole("button", { name: /Ejecutar la eliminación/i })).toBeNull();
  });

  // ── Los actos irreversibles: nivel 4 y separación de funciones ────────────

  it("con nivel 3 se ofrece publicar y retirar, pero NO avalar ni ejecutar", () => {
    clearSession();
    initSession(sesion(3));
    render(Pagina, { props: { data: conDetalle(lote({ publicado_hasta: iso(-1) })) } });
    expect(screen.getByRole("button", { name: /Retirar el lote/i })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Registrar el aval/i })).toBeNull();
  });

  it("con nivel 3 no se ofrece ejecutar un lote ya avalado", () => {
    clearSession();
    initSession(sesion(3));
    render(Pagina, { props: { data: { ...conDetalle(avalado()), acta: acta() } } });
    expect(screen.queryByRole("button", { name: /Ejecutar la eliminación/i })).toBeNull();
  });

  it("quien propuso el lote NO ve el botón de avalar; otra persona SÍ", () => {
    clearSession();
    initSession(sesion(4, "u-proponente"));
    const l = lote({ publicado_hasta: iso(-1), creado_por: "u-proponente" });
    const { unmount } = render(Pagina, { props: { data: conDetalle(l) } });
    expect(screen.queryByRole("button", { name: /Registrar el aval/i })).toBeNull();
    unmount();

    clearSession();
    initSession(sesion(4, "u-otra"));
    render(Pagina, { props: { data: conDetalle(l) } });
    expect(screen.getByRole("button", { name: /Registrar el aval/i })).toBeInTheDocument();
  });

  it("quien avaló el lote NO ve el botón de ejecutar; otra persona SÍ", () => {
    clearSession();
    initSession(sesion(4, "u-avalador"));
    const l = avalado({ avalado_por: "u-avalador" });
    const { unmount } = render(Pagina, { props: { data: { ...conDetalle(l), acta: acta() } } });
    expect(screen.queryByRole("button", { name: /Ejecutar la eliminación/i })).toBeNull();
    unmount();

    clearSession();
    initSession(sesion(4, "u-otra"));
    render(Pagina, { props: { data: { ...conDetalle(l), acta: acta() } } });
    expect(screen.getByRole("button", { name: /Ejecutar la eliminación/i })).toBeInTheDocument();
  });
});
