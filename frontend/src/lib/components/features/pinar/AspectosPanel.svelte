<!--
  OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
  Contacto: aurigadl@gmail.com

  Software libre: este archivo se distribuye bajo la Licencia Pública General
  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
-->

<!--
  AspectosPanel — Aspectos críticos y su MATRIZ DE PRIORIZACIÓN (RF-ADM-08;
  Ac. AGN 003/2015; Manual de formulación del PINAR §3).

  La matriz es lo que convierte una lista de problemas en un plan: cada aspecto
  se puntúa (1..10) frente a los cinco ejes, y la suma decide qué se atiende
  primero. **La prioridad la calcula el backend** y aquí solo se muestra — si se
  calculara en pantalla, dos vistas del mismo plan podrían ordenar distinto.

  Editable solo con el plan en `borrador`: al aprobar, el contenido se congela
  (Ac. AGN 003/2015) y la pantalla lo dice en vez de dejar botones que el
  backend rechazaría con un 409.
-->
<script>
  import { createEventDispatcher } from "svelte";

  import Button from "$lib/components/ui/Button.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";

  export let planId = "";
  export let aspectos = [];
  export let priorizacion = [];
  export let ejes = [];
  /** El plan admite cambios estructurales (solo en `borrador`). */
  export let editable = false;

  const dispatch = createEventDispatcher();

  // Índice por aspecto para no recorrer la lista dentro del bucle de la tabla.
  $: prioridadPorAspecto = new Map(priorizacion.map((p) => [p.aspecto_id, p]));
  // Orden de atención: es la razón de ser de la matriz, así que la tabla se
  // presenta YA ordenada por prioridad descendente en vez de por el orden de
  // captura — leerla desordenada obligaría a hacer la comparación a ojo.
  $: aspectosOrdenados = [...aspectos].sort(
    (a, b) =>
      (prioridadPorAspecto.get(b.id)?.prioridad ?? -1) -
      (prioridadPorAspecto.get(a.id)?.prioridad ?? -1),
  );

  let modal = null; // "crear" | "editar" | "priorizar"
  let enviando = false;
  let errorModal = "";
  let editando = null;
  let fDescripcion = "";
  let fRiesgo = "";
  // Los valores viven como TEXTO mientras están en el formulario —es lo que
  // devuelve un `<input>`— y se convierten a número al enviarlos. Tipar esto
  // como `number` obligaría a convertir en cada tecla y perdería el estado
  // «vacío», que aquí significa «sin valorar» y no «cero».
  /** @type {Record<number, string>} eje_code → impacto sin convertir */
  let impactos = {};

  function abrirCrear() {
    modal = "crear";
    editando = null;
    fDescripcion = "";
    fRiesgo = "";
    errorModal = "";
  }

  function abrirEditar(aspecto) {
    modal = "editar";
    editando = aspecto;
    fDescripcion = aspecto.descripcion ?? "";
    fRiesgo = aspecto.riesgo ?? "";
    errorModal = "";
  }

  function abrirPriorizar(aspecto) {
    modal = "priorizar";
    editando = aspecto;
    errorModal = "";
    const actual = prioridadPorAspecto.get(aspecto.id);
    impactos = {};
    for (const eje of ejes) {
      // Sin valor previo se deja en blanco, NO en 1: un impacto por defecto
      // sería una valoración que nadie hizo, y la suma la daría por buena.
      const previo = actual?.impactos?.[eje.code] ?? actual?.impactos?.[String(eje.code)];
      impactos[eje.code] = previo == null ? "" : String(previo);
    }
  }

  function cerrar() {
    modal = null;
    enviando = false;
    errorModal = "";
    editando = null;
  }

  async function enviar(url, method, cuerpo) {
    enviando = true;
    errorModal = "";
    try {
      const res = await fetch(url, {
        method,
        headers: cuerpo ? { "Content-Type": "application/json" } : undefined,
        body: cuerpo ? JSON.stringify(cuerpo) : undefined,
      });
      const body = await res.json().catch(() => ({}));
      enviando = false;
      if (!res.ok) {
        // El 409 del ciclo de vida se muestra tal cual: dice que el plan ya
        // está aprobado y que hay que reformular con una versión nueva.
        errorModal = mensajeError(body, `Error ${res.status}`);
        return false;
      }
      cerrar();
      dispatch("cambio");
      return true;
    } catch {
      enviando = false;
      errorModal = "No se pudo conectar con el servidor.";
      return false;
    }
  }

  const crear = () =>
    enviar(`/admin/pinar/api/planes/${planId}/aspectos`, "POST", {
      descripcion: fDescripcion,
      riesgo: fRiesgo || undefined,
    });

  const guardar = () =>
    enviar(`/admin/pinar/api/aspectos/${editando.id}`, "PATCH", {
      descripcion: fDescripcion,
      riesgo: fRiesgo || null,
    });

  const quitar = (aspecto) => enviar(`/admin/pinar/api/aspectos/${aspecto.id}`, "DELETE", null);

  function guardarPriorizacion() {
    const celdas = Object.entries(impactos)
      .filter(([, valor]) => valor !== "" && valor != null)
      .map(([eje, valor]) => ({ eje_code: Number(eje), impacto: Number(valor) }));
    return enviar(`/admin/pinar/api/planes/${planId}/priorizacion`, "PUT", {
      aspecto_id: editando.id,
      celdas,
    });
  }

  $: celdasCompletas =
    ejes.length > 0 && ejes.every((e) => impactos[e.code] !== "" && impactos[e.code] != null);
</script>

<div class="pinar-aspectos">
  <div class="pinar-aspectos__cab">
    <p class="pinar-aspectos__intro">
      Los <strong>aspectos críticos</strong> son los problemas que el plan va a atender. La
      <strong>matriz de priorización</strong> los puntúa de 1 a 10 frente a los cinco ejes, y la suma
      decide el orden de atención.
    </p>
    {#if editable}
      <Button variant="primary" icon="fas fa-plus" on:click={abrirCrear}>Nuevo aspecto</Button>
    {/if}
  </div>

  {#if !editable}
    <p class="pinar-aspectos__congelado">
      El plan ya no está en borrador: su contenido quedó <strong>congelado</strong> al aprobarse
      (Acuerdo AGN 003/2015). Para cambiarlo hay que <strong>reformular</strong> creando una versión nueva.
    </p>
  {/if}

  {#if aspectos.length === 0}
    <EmptyState
      icon="fas fa-triangle-exclamation"
      title="Este plan todavía no tiene aspectos críticos"
      description="Un PINAR se formula a partir de los aspectos críticos identificados: sin ellos no hay qué priorizar, ni objetivos que deriven de ellos."
    />
  {:else}
    <!-- Técnica WCAG SCR29: región desplazable alcanzable con teclado (WCAG 2.1.1). -->
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div class="op-table-scroll" role="region" aria-label="Aspectos del diagnóstico" tabindex="0">
      <table class="pinar-aspectos__tabla">
        <caption class="is-sr-only">Aspectos críticos y su priorización</caption>
        <thead>
          <tr>
            <th scope="col">Aspecto crítico</th>
            <th scope="col">Riesgo</th>
            {#each ejes as eje (eje.code)}
              <th scope="col" class="pinar-aspectos__eje" title={eje.nombre}>{eje.code}</th>
            {/each}
            <th scope="col">Prioridad</th>
            {#if editable}<th scope="col"><span class="is-sr-only">Acciones</span></th>{/if}
          </tr>
        </thead>
        <tbody>
          {#each aspectosOrdenados as aspecto (aspecto.id)}
            {@const p = prioridadPorAspecto.get(aspecto.id)}
            <tr>
              <td>{aspecto.descripcion}</td>
              <td>{aspecto.riesgo ?? "—"}</td>
              {#each ejes as eje (eje.code)}
                <td class="pinar-aspectos__eje">
                  {p?.impactos?.[eje.code] ?? p?.impactos?.[String(eje.code)] ?? "—"}
                </td>
              {/each}
              <td>
                {#if p}
                  <strong>{p.prioridad}</strong>
                {:else}
                  <!-- Sin priorizar NO es prioridad cero: es que nadie la ha
                       valorado todavía, y confundirlas la mandaría al final de
                       la lista como si ya se hubiera decidido que no urge. -->
                  <span class="pinar-aspectos__sin">Sin priorizar</span>
                {/if}
              </td>
              {#if editable}
                <td class="pinar-aspectos__acciones">
                  <Button size="sm" variant="ghost" on:click={() => abrirPriorizar(aspecto)}>
                    Priorizar
                  </Button>
                  <Button size="sm" variant="ghost" on:click={() => abrirEditar(aspecto)}>
                    Editar
                  </Button>
                  <Button size="sm" variant="ghost" on:click={() => quitar(aspecto)}>Quitar</Button>
                </td>
              {/if}
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
    <p class="pinar-aspectos__leyenda">
      Ejes: {#each ejes as eje, i (eje.code)}{i > 0 ? " · " : ""}<strong>{eje.code}</strong>
        {eje.nombre}{/each}
    </p>
  {/if}
</div>

<Modal
  open={modal === "crear" || modal === "editar"}
  title={modal === "crear" ? "Nuevo aspecto crítico" : "Editar aspecto crítico"}
  size="sm"
  on:close={cerrar}
>
  <FormField
    id="aspecto-descripcion"
    name="descripcion"
    type="textarea"
    label="Aspecto crítico"
    required
    bind:value={fDescripcion}
    hint="El problema identificado, en los términos del diagnóstico."
  />
  <FormField
    id="aspecto-riesgo"
    name="riesgo"
    type="textarea"
    label="Riesgo asociado (opcional)"
    bind:value={fRiesgo}
  />
  {#if errorModal}<p class="pinar-aspectos__error" role="alert">{errorModal}</p>{/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={cerrar}>Cancelar</Button>
    <Button
      variant="primary"
      loading={enviando}
      disabled={!fDescripcion.trim()}
      on:click={modal === "crear" ? crear : guardar}
    >
      Guardar
    </Button>
  </svelte:fragment>
</Modal>

<Modal open={modal === "priorizar"} title="Priorizar el aspecto" size="sm" on:close={cerrar}>
  <p>{editando?.descripcion}</p>
  <p class="pinar-aspectos__intro">
    Puntúa de <strong>1 a 10</strong> el impacto del aspecto en cada eje. La prioridad es la
    <strong>suma</strong>, y la calcula el sistema.
  </p>
  {#each ejes as eje (eje.code)}
    <FormField
      id="impacto-{eje.code}"
      name="impacto-{eje.code}"
      type="number"
      label="{eje.code}. {eje.nombre}"
      bind:value={impactos[eje.code]}
    />
  {/each}
  {#if !celdasCompletas}
    <p class="pinar-aspectos__aviso">
      Faltan ejes por puntuar. La matriz <strong>reemplaza por completo</strong> la priorización anterior:
      una matriz a medias no es una priorización, es un cálculo con huecos.
    </p>
  {/if}
  {#if errorModal}<p class="pinar-aspectos__error" role="alert">{errorModal}</p>{/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={cerrar}>Cancelar</Button>
    <Button
      variant="primary"
      loading={enviando}
      disabled={!celdasCompletas}
      on:click={guardarPriorizacion}
    >
      Guardar priorización
    </Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .pinar-aspectos__cab {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--op-space-3);
    margin-bottom: var(--op-space-3);
  }
  .pinar-aspectos__intro {
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
  }
  .pinar-aspectos__congelado,
  .pinar-aspectos__aviso {
    margin: var(--op-space-2) 0;
    padding: var(--op-space-2);
    border-left: 3px solid var(--op-warning);
    background: var(--op-bg);
    color: var(--op-text);
    font-size: var(--op-font-sm);
  }
  .pinar-aspectos__tabla {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--op-font-sm);

    th,
    td {
      padding: var(--op-space-2);
      border-bottom: 1px solid var(--op-border);
      text-align: left;
      vertical-align: top;
    }
    th {
      color: var(--op-text-secondary);
      font-weight: var(--op-weight-medium);
    }
  }
  .pinar-aspectos__eje {
    text-align: center;
    width: 3rem;
  }
  .pinar-aspectos__sin {
    color: var(--op-text-secondary);
  }
  .pinar-aspectos__acciones {
    display: flex;
    flex-wrap: wrap;
    gap: var(--op-space-1);
  }
  .pinar-aspectos__leyenda {
    margin-top: var(--op-space-2);
    color: var(--op-text-secondary);
    font-size: var(--op-font-xs);
  }
  .pinar-aspectos__error {
    color: var(--op-error);
    font-size: var(--op-font-sm);
    margin: var(--op-space-2) 0;
  }
</style>
