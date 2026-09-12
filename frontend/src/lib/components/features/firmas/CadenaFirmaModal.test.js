// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * La puerta que le faltaba a una capacidad construida, probada y sembrada.
 *
 * `/firmas/api/requests` existía con sus pruebas y NINGUNA vista lo llamaba, así
 * que sobre un expediente o un radicado no había forma de abrir una cadena de
 * firmantes desde el navegador — aunque el sembrado del entorno crea las dos y
 * dice del expediente que «es donde una entidad la usa de verdad».
 *
 * Lo que estos tests fijan es lo que el orden significa: la posición en la lista
 * ES el turno, y eso viaja al backend tal cual.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/svelte";
import CadenaFirmaModal from "./CadenaFirmaModal.svelte";

const EXP = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee";
const ANA = "11111111-1111-1111-1111-111111111111";
const LUIS = "22222222-2222-2222-2222-222222222222";

const PERSONAS = [
  { id: ANA, nombre: "Ana Restrepo" },
  { id: LUIS, nombre: "Luis Gómez" },
];

afterEach(() => vi.restoreAllMocks());

/** @param {{cadena?: any, onPost?: (b: any) => Response}} opts */
function mockFetch({ cadena = null, onPost } = {}) {
  const posts = [];
  globalThis.fetch = vi.fn(async (input, init) => {
    const url = String(input);
    if (url === "/borradores/api/firmantes") {
      return new Response(JSON.stringify(PERSONAS), { status: 200 });
    }
    if (url.startsWith("/firmas/api/requests?")) {
      // 404 = no hay ninguna abierta. Es la respuesta NORMAL, no un fallo.
      return cadena
        ? new Response(JSON.stringify(cadena), { status: 200 })
        : new Response(JSON.stringify({ error: "not_found" }), { status: 404 });
    }
    if (url === "/firmas/api/requests" && init?.method === "POST") {
      const body = JSON.parse(init.body);
      posts.push(body);
      if (onPost) return onPost(body);
      return new Response(JSON.stringify({ solicitud_id: "s1", estado: "pendiente" }), {
        status: 201,
      });
    }
    throw new Error(`URL inesperada: ${url}`);
  });
  return posts;
}

function montar(props = {}) {
  return render(CadenaFirmaModal, {
    props: {
      open: true,
      objetoTipo: "expediente",
      objetoId: EXP,
      titulo: "EXP-2026-0001",
      ...props,
    },
  });
}

async function elegir(idx, valor) {
  const select = await screen.findByLabelText(`Firmante del turno ${idx}`);
  await fireEvent.change(select, { target: { value: valor } });
  return select;
}

describe("CadenaFirmaModal", () => {
  it("manda los firmantes EN ORDEN: la posición es el turno", async () => {
    const posts = mockFetch();
    montar();

    await screen.findByText(/cuando le toca/i);
    await fireEvent.click(screen.getByRole("button", { name: /Añadir firmante/i }));
    await elegir(1, ANA);
    await fireEvent.click(screen.getByRole("button", { name: /Añadir firmante/i }));
    await elegir(2, LUIS);

    await fireEvent.click(screen.getByRole("button", { name: /Abrir cadena/i }));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].firmantes).toEqual([ANA, LUIS]);
    expect(posts[0].objeto_tipo).toBe("expediente");
    expect(posts[0].objeto_id).toBe(EXP);
  });

  it("no deja abrirla con la misma persona dos veces", async () => {
    const posts = mockFetch();
    montar();

    await screen.findByText(/cuando le toca/i);
    await fireEvent.click(screen.getByRole("button", { name: /Añadir firmante/i }));
    await elegir(1, ANA);
    await fireEvent.click(screen.getByRole("button", { name: /Añadir firmante/i }));
    await elegir(2, ANA);

    expect(await screen.findByText(/persona repetida/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Abrir cadena/i })).toBeDisabled();
    expect(posts).toHaveLength(0);
  });

  it("con una cadena ya abierta la muestra y no ofrece crear otra", async () => {
    mockFetch({
      cadena: {
        estado: "pendiente",
        items: [
          { orden: 1, firmante_id: ANA, estado: "pendiente" },
          { orden: 2, firmante_id: LUIS, estado: "pendiente" },
        ],
      },
    });
    montar();

    expect(await screen.findByText(/ya tiene una cadena abierta/i)).toBeInTheDocument();
    expect(screen.getByText(/Ana Restrepo/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Abrir cadena/i })).toBeNull();
  });

  it("sin cadena abierta (404) no anuncia ningún error", async () => {
    mockFetch();
    montar();

    await screen.findByText(/cuando le toca/i);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("emite 'created' con la solicitud cuando el backend la acepta", async () => {
    mockFetch();
    const { component } = montar();
    const onCreated = vi.fn();
    component.$on("created", onCreated);

    await screen.findByText(/cuando le toca/i);
    await fireEvent.click(screen.getByRole("button", { name: /Añadir firmante/i }));
    await elegir(1, ANA);
    await fireEvent.click(screen.getByRole("button", { name: /Abrir cadena/i }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledOnce());
    expect(onCreated.mock.calls[0][0].detail.solicitud_id).toBe("s1");
  });

  it("un rechazo del backend se enseña y no se da por creada", async () => {
    mockFetch({
      onPost: () =>
        new Response(JSON.stringify({ error: "cadena_existente", detail: "Ya hay una cadena." }), {
          status: 409,
        }),
    });
    const { component } = montar();
    const onCreated = vi.fn();
    component.$on("created", onCreated);

    await screen.findByText(/cuando le toca/i);
    await fireEvent.click(screen.getByRole("button", { name: /Añadir firmante/i }));
    await elegir(1, ANA);
    await fireEvent.click(screen.getByRole("button", { name: /Abrir cadena/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/Ya hay una cadena/i);
    expect(onCreated).not.toHaveBeenCalled();
  });
});
