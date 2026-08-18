// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * DependencySwitcher — los TRES tamaños de `availableDepts`, y el que faltaba.
 *
 * El componente ya distinguía «varias» (selector activo) de «una» (selector
 * bloqueado + aviso). Con **cero** no pintaba nada, y en `/perfil` eso dejaba
 * una tarjeta con su encabezado «Dependencia activa», un párrafo prometiendo
 * que cambiar de dependencia actualiza la navegación… y silencio. Se lee como
 * una pantalla rota, cuando lo que ocurre es que a esa persona nadie le ha
 * asignado todavía una unidad organizacional.
 *
 * El caso es real, no hipotético: el tenant de demostración creaba sus tres
 * dependencias y sus cinco usuarios y **no los relacionaba** — cero filas en
 * `urd_usuaroldep`. El síntoma visible era esta tarjeta vacía, y el invisible
 * (peor) una bandeja de trámite que decía «vas al día» con radicados
 * asignados, porque se filtra por la dependencia de quien mira.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/svelte";
import DependencySwitcher from "./DependencySwitcher.svelte";

const DEPENDENCIAS = [
  { codigo: 901, nombre: "Ventanilla Única de Radicación", sigla: "VUR" },
  { codigo: 902, nombre: "Oficina Jurídica", sigla: "JUR" },
];

describe("DependencySwitcher — sin ninguna dependencia asignada", () => {
  it("explica qué falta y quién lo arregla, en vez de no pintar nada", () => {
    render(DependencySwitcher, {
      props: { activeDepeId: null, availableDepts: [], dependencias: DEPENDENCIAS },
    });

    // No hay selector: no hay nada que conmutar, y eso es correcto.
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();

    // Pero SÍ hay una explicación. Sin ella el usuario no puede distinguir
    // «nadie te ha asignado dependencia» de «esto está roto».
    const aviso = screen.getByText(/no tiene ninguna dependencia asignada/i);
    expect(aviso).toBeInTheDocument();
    // Y dice dónde se resuelve: un aviso que solo constata el problema deja a
    // la persona igual de atascada.
    expect(aviso.textContent).toMatch(/Usuarios/i);
  });

  it("en la barra superior (compact) sigue sin pintar nada", () => {
    // Control negativo: el aviso pertenece a la pantalla donde la persona vino
    // a mirarlo. Si se colara en `compact`, aparecería un párrafo de tres
    // líneas dentro de la barra superior de TODAS las pantallas.
    const { container } = render(DependencySwitcher, {
      props: { activeDepeId: null, availableDepts: [], dependencias: DEPENDENCIAS, compact: true },
    });
    expect(container.textContent.trim()).toBe("");
  });
});

describe("DependencySwitcher — los casos que ya funcionaban (control positivo)", () => {
  it("con una sola dependencia muestra el selector bloqueado y lo dice", () => {
    render(DependencySwitcher, {
      props: {
        activeDepeId: 901,
        availableDepts: [{ depe_id: 901, es_principal: true }],
        dependencias: DEPENDENCIAS,
      },
    });
    expect(screen.getByRole("combobox")).toBeDisabled();
    expect(screen.getByText(/solo tiene una dependencia asignada/i)).toBeInTheDocument();
    // Sin este control positivo, un `{#if}` que nunca entrara dejaría el test
    // del caso cero en verde y el componente inservible.
    expect(screen.queryByText(/no tiene ninguna dependencia/i)).not.toBeInTheDocument();
  });

  it("con varias, el selector está activo y lista todas", () => {
    render(DependencySwitcher, {
      props: {
        activeDepeId: 901,
        availableDepts: [{ depe_id: 901, es_principal: true }, { depe_id: 902 }],
        dependencias: DEPENDENCIAS,
      },
    });
    const select = screen.getByRole("combobox");
    expect(select).not.toBeDisabled();
    expect(screen.getByRole("option", { name: /VUR — Ventanilla/ })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /JUR — Oficina Jurídica/ })).toBeInTheDocument();
  });

  it("ignora las asignaciones inactivas al contar", () => {
    // `is_active: false` es una asignación retirada. Si contara, una persona
    // sin dependencias vigentes vería el selector en vez del aviso.
    render(DependencySwitcher, {
      props: {
        activeDepeId: null,
        availableDepts: [{ depe_id: 901, is_active: false }],
        dependencias: DEPENDENCIAS,
      },
    });
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.getByText(/no tiene ninguna dependencia asignada/i)).toBeInTheDocument();
  });
});

describe("DependencySwitcher — dos instancias en la misma página", () => {
  it("cada <select> tiene un id propio y su etiqueta apunta al suyo", () => {
    // `/perfil` monta las DOS: la de la barra superior (compact) y la de la
    // sección. Con el id repetido, `label[for]` resolvía en ambas al PRIMER
    // select — quien usa teclado o lector de pantalla activaba el conmutador de
    // la barra superior creyendo tocar el de la sección que estaba leyendo.
    const props = {
      activeDepeId: 901,
      availableDepts: [{ depe_id: 901, es_principal: true }],
      dependencias: DEPENDENCIAS,
    };
    const a = render(DependencySwitcher, { props: { ...props, compact: true } });
    const b = render(DependencySwitcher, { props });

    const ids = [a.container.querySelector("select").id, b.container.querySelector("select").id];
    expect(ids[0]).toBeTruthy();
    expect(ids[0]).not.toBe(ids[1]);

    for (const { container } of [a, b]) {
      const select = container.querySelector("select");
      const label = container.querySelector("label");
      expect(label.getAttribute("for")).toBe(select.id);
    }
  });
});
