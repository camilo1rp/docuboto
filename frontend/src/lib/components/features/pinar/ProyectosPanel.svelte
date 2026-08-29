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
  ProyectosPanel — Objetivos y proyectos del plan, y el SEGUIMIENTO
  (RF-ADM-08; Ac. AGN 003/2015 §4, mapa de ruta).

  Dos ciclos de vida distintos conviven aquí, y la pantalla los distingue
  porque el backend los distingue:

    * La ESTRUCTURA (objetivos, proyectos, articulación con aspectos) solo se
      edita con el plan en `borrador`.
    * El SEGUIMIENTO solo se registra con el plan `aprobado` o `en_ejecucion`
      — y es APPEND-ONLY: se añaden cortes de avance, nunca se corrigen.

  Es decir: **nunca están habilitadas las dos cosas a la vez**, y ofrecer la que
  no toca haría descubrir el límite fallando contra un 409.

  El avance de un proyecto NO se teclea: sale del último corte de seguimiento.
  Un porcentaje declarado a mano sería un avance sin nada que lo respalde.
-->
<script>
  import { createEventDispatcher } from "svelte";

  import Button from "$lib/components/ui/Button.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import { formatearFecha } from "$lib/utils/fecha.js";

  export let planId = "";
  export let objetivos = [];
  export let proyectos = [];
  export let aspectos = [];
  /** Estructura editable: solo con el plan en `borrador`. */
  export let editable = false;
  /** Seguimiento admitido: solo con el plan `aprobado` o `en_ejecucion`. */
  export let rastreable = false;

  const dispatch = createEventDispatcher();

  const ESTADO_CHIP = {
    planeado: "neutral",
    en_curso: "active",
    terminado: "info",
    cancelado: "neutral",
  };
  const ESTADO_LABEL = {
    planeado: "Planeado",
    en_curso: "En curso",
    terminado: "Terminado",
    cancelado: "Cancelado",
  };

  $: objetivoPorId = new Map(objetivos.map((o) => [o.id, o]));
  $: sinObjetivo = proyectos.filter((p) => !p.objetivo_id);
  // Agrupación calculada arriba y no con `{@const}` dentro del `{#each}`:
  // Svelte solo admite `{@const}` como hijo inmediato de un bloque, y un
  // `filter` por fila recorrería la lista entera una vez por objetivo.
  $: porObjetivo = proyectos.reduce((acc, p) => {
    if (!p.objetivo_id) return acc;
    (acc[p.objetivo_id] ??= []).push(p);
    return acc;
  }, /** @type {Record<string, any[]>} */ ({}));

  let modal = null;
  let enviando = false;
  let errorModal = "";
  let editando = null;
  let fDescripcion = "";
  let fNombre = "";
  let fObjetivo = "";
  let fMeta = "";
  let fIndicador = "";
  let fResponsable = "";
  let fRecurso = "";
  let fInicio = "";
  let fFin = "";
  let fFecha = new Date().toISOString().slice(0, 10);
  let fAvance = "";
  let fObservacion = "";
  let seleccionAspectos = new Set();
  let seguimiento = [];
  let seguimientoError = "";

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
        errorModal = mensajeError(body, `Error ${res.status}`);
        return;
      }
      cerrar();
      dispatch("cambio");
    } catch {
      enviando = false;
      errorModal = "No se pudo conectar con el servidor.";
    }
  }

  // ── Objetivos ──────────────────────────────────────────────────────────────
  function abrirObjetivo(objetivo = null) {
    modal = "objetivo";
    editando = objetivo;
    fDescripcion = objetivo?.descripcion ?? "";
    errorModal = "";
  }

  const guardarObjetivo = () =>
    editando
      ? enviar(`/admin/pinar/api/objetivos/${editando.id}`, "PATCH", {
          descripcion: fDescripcion,
        })
      : enviar(`/admin/pinar/api/planes/${planId}/objetivos`, "POST", {
          descripcion: fDescripcion,
        });

  const quitarObjetivo = (objetivo) =>
    enviar(`/admin/pinar/api/objetivos/${objetivo.id}`, "DELETE", null);

  // ── Proyectos ──────────────────────────────────────────────────────────────
  function abrirProyecto(proyecto = null, objetivoId = "") {
    modal = "proyecto";
    editando = proyecto;
    errorModal = "";
    fNombre = proyecto?.nombre ?? "";
    fObjetivo = proyecto?.objetivo_id ?? objetivoId ?? "";
    fMeta = proyecto?.meta ?? "";
    fIndicador = proyecto?.indicador ?? "";
    fResponsable = proyecto?.responsable ?? "";
    fRecurso = proyecto?.recurso ?? "";
    fInicio = proyecto?.fecha_inicio ?? "";
    fFin = proyecto?.fecha_fin ?? "";
  }

  function cuerpoProyecto() {
    return {
      objetivo_id: fObjetivo || null,
      nombre: fNombre,
      meta: fMeta || null,
      indicador: fIndicador || null,
      responsable: fResponsable || null,
      recurso: fRecurso || null,
      fecha_inicio: fInicio || null,
      fecha_fin: fFin || null,
    };
  }

  const guardarProyecto = () =>
    editando
      ? enviar(`/admin/pinar/api/proyectos/${editando.id}`, "PATCH", cuerpoProyecto())
      : enviar(`/admin/pinar/api/planes/${planId}/proyectos`, "POST", cuerpoProyecto());

  const quitarProyecto = (proyecto) =>
    enviar(`/admin/pinar/api/proyectos/${proyecto.id}`, "DELETE", null);

  // ── Articulación con aspectos ──────────────────────────────────────────────
  function abrirArticulacion(proyecto) {
    modal = "aspectos";
    editando = proyecto;
    errorModal = "";
    seleccionAspectos = new Set(proyecto.aspecto_ids ?? []);
  }

  function alternarAspecto(id) {
    const s = new Set(seleccionAspectos);
    if (s.has(id)) s.delete(id);
    else s.add(id);
    seleccionAspectos = s;
  }

  const guardarArticulacion = () =>
    enviar(`/admin/pinar/api/proyectos/${editando.id}/aspectos`, "PUT", {
      aspecto_ids: [...seleccionAspectos],
    });

  // ── Seguimiento ────────────────────────────────────────────────────────────
  async function abrirSeguimiento(proyecto) {
    modal = "seguimiento";
    editando = proyecto;
    errorModal = "";
    seguimientoError = "";
    seguimiento = [];
    fFecha = new Date().toISOString().slice(0, 10);
    fAvance = "";
    fObservacion = "";
    try {
      const res = await fetch(`/admin/pinar/api/proyectos/${proyecto.id}/seguimiento`);
      if (res.ok) seguimiento = await res.json().catch(() => []);
      else seguimientoError = "No se pudo cargar el histórico de avance.";
    } catch {
      seguimientoError = "No se pudo cargar el histórico de avance.";
    }
  }

  const registrarAvance = () =>
    enviar(`/admin/pinar/api/proyectos/${editando.id}/seguimiento`, "POST", {
      fecha: fFecha,
      avance_pct: Number(fAvance),
      observacion: fObservacion || null,
    });

  const fecha = (iso) => formatearFecha(iso, { estilo: "medio" }) || "—";
</script>

<div class="pinar-proy">
  <div class="pinar-proy__cab">
    <p class="pinar-proy__intro">
      Los <strong>objetivos</strong> derivan de los aspectos priorizados, y los
      <strong>proyectos</strong> son las fichas del mapa de ruta que los cumplen. El avance de cada
      proyecto sale de su <strong>seguimiento</strong>, no se teclea.
    </p>
    {#if editable}
      <div class="pinar-proy__acciones">
        <Button variant="secondary" icon="fas fa-plus" on:click={() => abrirObjetivo()}>
          Nuevo objetivo
        </Button>
        <Button variant="primary" icon="fas fa-plus" on:click={() => abrirProyecto()}>
          Nuevo proyecto
        </Button>
      </div>
    {/if}
  </div>

  {#if !editable && !rastreable}
    <p class="pinar-proy__aviso">
      El plan está <strong>cerrado</strong>: ni admite cambios estructurales ni nuevos registros de
      avance.
    </p>
  {:else if !editable}
    <p class="pinar-proy__aviso">
      El contenido quedó <strong>congelado</strong> al aprobarse. Lo que sí se puede es
      <strong>registrar el avance</strong> de cada proyecto.
    </p>
  {/if}

  {#if objetivos.length === 0 && proyectos.length === 0}
    <EmptyState
      icon="fas fa-diagram-project"
      title="Este plan todavía no tiene objetivos ni proyectos"
      description="Sin proyectos no hay mapa de ruta ni avance que medir: el tablero seguirá vacío hasta que existan."
    />
  {:else}
    {#each objetivos as objetivo (objetivo.id)}
      <section class="pinar-proy__grupo">
        <header class="pinar-proy__grupo-cab">
          <h4 class="pinar-proy__grupo-titulo">{objetivo.descripcion}</h4>
          {#if editable}
            <div class="pinar-proy__acciones">
              <Button size="sm" variant="ghost" on:click={() => abrirProyecto(null, objetivo.id)}>
                Añadir proyecto
              </Button>
              <Button size="sm" variant="ghost" on:click={() => abrirObjetivo(objetivo)}>
                Editar objetivo
              </Button>
              <Button size="sm" variant="ghost" on:click={() => quitarObjetivo(objetivo)}>
                Quitar
              </Button>
            </div>
          {/if}
        </header>
        {#if (porObjetivo[objetivo.id] ?? []).length === 0}
          <p class="pinar-proy__vacio">Sin proyectos que lo cumplan todavía.</p>
        {:else}
          <table class="pinar-proy__tabla">
            <caption class="is-sr-only">Proyectos de: {objetivo.descripcion}</caption>
            <thead>
              <tr>
                <th scope="col">Proyecto</th>
                <th scope="col">Responsable</th>
                <th scope="col">Plazo</th>
                <th scope="col">Estado</th>
                <th scope="col">Avance</th>
                <th scope="col"><span class="is-sr-only">Acciones</span></th>
              </tr>
            </thead>
            <tbody>
              {#each porObjetivo[objetivo.id] ?? [] as proyecto (proyecto.id)}
                <tr>
                  <td>
                    <strong>{proyecto.nombre}</strong>
                    {#if proyecto.meta}<br /><span class="pinar-proy__meta">{proyecto.meta}</span
                      >{/if}
                  </td>
                  <td>{proyecto.responsable ?? "—"}</td>
                  <td>{fecha(proyecto.fecha_inicio)} – {fecha(proyecto.fecha_fin)}</td>
                  <td>
                    <StatusChip
                      status={ESTADO_CHIP[proyecto.estado] ?? "neutral"}
                      label={ESTADO_LABEL[proyecto.estado] ?? proyecto.estado}
                    />
                  </td>
                  <td>{proyecto.avance_pct}%</td>
                  <td class="pinar-proy__acciones">
                    {#if rastreable}
                      <Button size="sm" variant="ghost" on:click={() => abrirSeguimiento(proyecto)}>
                        Seguimiento
                      </Button>
                    {/if}
                    {#if editable}
                      <Button
                        size="sm"
                        variant="ghost"
                        on:click={() => abrirArticulacion(proyecto)}
                      >
                        Aspectos
                      </Button>
                      <Button size="sm" variant="ghost" on:click={() => abrirProyecto(proyecto)}>
                        Editar
                      </Button>
                      <Button size="sm" variant="ghost" on:click={() => quitarProyecto(proyecto)}>
                        Quitar
                      </Button>
                    {/if}
                  </td>
                </tr>
              {/each}
            </tbody>
          </table>
        {/if}
      </section>
    {/each}

    {#if sinObjetivo.length > 0}
      <section class="pinar-proy__grupo">
        <header class="pinar-proy__grupo-cab">
          <h4 class="pinar-proy__grupo-titulo">Proyectos sin objetivo asociado</h4>
        </header>
        <p class="pinar-proy__aviso">
          Un proyecto sin objetivo <strong>no aparece en el tablero por objetivo</strong>: el avance
          se registra pero no se atribuye a nada del plan.
        </p>
        <ul class="pinar-proy__sueltos">
          {#each sinObjetivo as proyecto (proyecto.id)}
            <li>
              <strong>{proyecto.nombre}</strong> — {proyecto.avance_pct}%
              {#if editable}
                <Button size="sm" variant="ghost" on:click={() => abrirProyecto(proyecto)}>
                  Asociar a un objetivo
                </Button>
              {/if}
            </li>
          {/each}
        </ul>
      </section>
    {/if}
  {/if}
</div>

<Modal
  open={modal === "objetivo"}
  title={editando ? "Editar objetivo" : "Nuevo objetivo"}
  size="sm"
  on:close={cerrar}
>
  <FormField
    id="objetivo-descripcion"
    name="descripcion"
    type="textarea"
    label="Objetivo"
    required
    bind:value={fDescripcion}
    hint="Deriva de los aspectos priorizados."
  />
  {#if errorModal}<p class="pinar-proy__error" role="alert">{errorModal}</p>{/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={cerrar}>Cancelar</Button>
    <Button
      variant="primary"
      loading={enviando}
      disabled={!fDescripcion.trim()}
      on:click={guardarObjetivo}
    >
      Guardar
    </Button>
  </svelte:fragment>
</Modal>

<Modal
  open={modal === "proyecto"}
  title={editando ? "Editar proyecto" : "Nuevo proyecto"}
  size="md"
  on:close={cerrar}
>
  <FormField
    id="proyecto-nombre"
    name="nombre"
    label="Nombre del proyecto"
    required
    bind:value={fNombre}
  />
  <FormField
    id="proyecto-objetivo"
    name="objetivo_id"
    type="select"
    label="Objetivo que cumple"
    bind:value={fObjetivo}
    options={[
      { value: "", label: "Sin objetivo asociado" },
      ...objetivos.map((o) => ({ value: o.id, label: o.descripcion })),
    ]}
    hint="Sin objetivo, el proyecto no aparece en el tablero por objetivo."
  />
  <FormField id="proyecto-meta" name="meta" type="textarea" label="Meta" bind:value={fMeta} />
  <FormField
    id="proyecto-indicador"
    name="indicador"
    label="Indicador"
    bind:value={fIndicador}
    hint="Cómo se mide el cumplimiento."
  />
  <FormField
    id="proyecto-responsable"
    name="responsable"
    label="Responsable"
    bind:value={fResponsable}
  />
  <FormField id="proyecto-recurso" name="recurso" label="Recursos" bind:value={fRecurso} />
  <FormField
    id="proyecto-inicio"
    name="fecha_inicio"
    type="date"
    label="Inicio"
    bind:value={fInicio}
  />
  <FormField id="proyecto-fin" name="fecha_fin" type="date" label="Fin" bind:value={fFin} />
  {#if errorModal}<p class="pinar-proy__error" role="alert">{errorModal}</p>{/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={cerrar}>Cancelar</Button>
    <Button
      variant="primary"
      loading={enviando}
      disabled={!fNombre.trim() || (fInicio && fFin && fFin < fInicio)}
      on:click={guardarProyecto}
    >
      Guardar
    </Button>
  </svelte:fragment>
</Modal>

<Modal
  open={modal === "aspectos"}
  title="Aspectos que atiende el proyecto"
  size="sm"
  on:close={cerrar}
>
  <p class="pinar-proy__intro">
    Es la <strong>articulación</strong> que hace que el avance del proyecto se atribuya a un eje del PINAR.
    Sin ella, el proyecto avanza y el tablero por eje no lo recoge.
  </p>
  {#if aspectos.length === 0}
    <p>Este plan todavía no tiene aspectos críticos que asociar.</p>
  {:else}
    <ul class="pinar-proy__aspectos">
      {#each aspectos as aspecto (aspecto.id)}
        <li>
          <label>
            <input
              type="checkbox"
              checked={seleccionAspectos.has(aspecto.id)}
              on:change={() => alternarAspecto(aspecto.id)}
            />
            {aspecto.descripcion}
          </label>
        </li>
      {/each}
    </ul>
  {/if}
  {#if errorModal}<p class="pinar-proy__error" role="alert">{errorModal}</p>{/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={cerrar}>Cancelar</Button>
    <Button variant="primary" loading={enviando} on:click={guardarArticulacion}>Guardar</Button>
  </svelte:fragment>
</Modal>

<Modal open={modal === "seguimiento"} title="Seguimiento del proyecto" size="md" on:close={cerrar}>
  <p><strong>{editando?.nombre}</strong></p>
  <p class="pinar-proy__intro">
    El seguimiento es <strong>append-only</strong>: se registra un corte de avance, nunca se corrige
    uno anterior. Un histórico que se puede reescribir no sirve para rendir cuentas.
  </p>

  {#if seguimientoError}
    <p class="pinar-proy__error" role="alert">{seguimientoError}</p>
  {:else if seguimiento.length === 0}
    <p class="pinar-proy__vacio">Todavía no hay cortes de avance registrados.</p>
  {:else}
    <table class="pinar-proy__tabla">
      <caption class="is-sr-only">Histórico de avance</caption>
      <thead>
        <tr>
          <th scope="col">Fecha</th>
          <th scope="col">Avance</th>
          <th scope="col">Observación</th>
        </tr>
      </thead>
      <tbody>
        {#each seguimiento as corte (corte.id)}
          <tr>
            <td>{fecha(corte.fecha)}</td>
            <td>{corte.avance_pct}%</td>
            <td>{corte.observacion ?? "—"}</td>
          </tr>
        {/each}
      </tbody>
    </table>
  {/if}

  <FormField
    id="seg-fecha"
    name="fecha"
    type="date"
    label="Fecha del corte"
    required
    bind:value={fFecha}
  />
  <FormField
    id="seg-avance"
    name="avance_pct"
    type="number"
    label="Avance (%)"
    required
    bind:value={fAvance}
  />
  <FormField
    id="seg-observacion"
    name="observacion"
    type="textarea"
    label="Observación"
    bind:value={fObservacion}
  />
  {#if errorModal}<p class="pinar-proy__error" role="alert">{errorModal}</p>{/if}
  <svelte:fragment slot="footer">
    <Button variant="ghost" on:click={cerrar}>Cerrar</Button>
    <Button
      variant="primary"
      loading={enviando}
      disabled={fAvance === "" || Number(fAvance) < 0 || Number(fAvance) > 100 || !fFecha}
      on:click={registrarAvance}
    >
      Registrar avance
    </Button>
  </svelte:fragment>
</Modal>

<style lang="scss">
  .pinar-proy__cab {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: var(--op-space-3);
    margin-bottom: var(--op-space-3);
  }
  .pinar-proy__intro,
  .pinar-proy__meta,
  .pinar-proy__vacio {
    color: var(--op-text-secondary);
    font-size: var(--op-font-sm);
  }
  .pinar-proy__aviso {
    margin: var(--op-space-2) 0;
    padding: var(--op-space-2);
    border-left: 3px solid var(--op-warning);
    background: var(--op-bg);
    color: var(--op-text);
    font-size: var(--op-font-sm);
  }
  .pinar-proy__acciones {
    display: flex;
    flex-wrap: wrap;
    gap: var(--op-space-1);
  }
  .pinar-proy__grupo {
    margin-bottom: var(--op-space-4);
  }
  .pinar-proy__grupo-cab {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: var(--op-space-3);
    margin-bottom: var(--op-space-2);
  }
  .pinar-proy__grupo-titulo {
    font-weight: var(--op-weight-bold);
    color: var(--op-text);
  }
  .pinar-proy__tabla {
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
  .pinar-proy__sueltos,
  .pinar-proy__aspectos {
    margin: var(--op-space-2) 0 0 var(--op-space-4);
    font-size: var(--op-font-sm);
    color: var(--op-text);
    list-style: disc;
  }
  .pinar-proy__error {
    color: var(--op-error);
    font-size: var(--op-font-sm);
    margin: var(--op-space-2) 0;
  }
</style>
