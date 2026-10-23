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
  ValoracionTvdModal.svelte — Valoración del Comité sobre una TVD (C-5 / C7).

  Consulta el acto registrado (con los recuentos pendientes/aplicadas/agotadas)
  y, si no existe y la persona puede escribir, ofrece registrarlo. Registrarlo
  levanta la retención legal `valoracion_pendiente` de los expedientes de fondo
  acumulado de esta TVD; la disposición (CT/E/S/M) decide si queda una ventana
  de retención. Con disposición Selección (S) la valoración declara el método de
  muestreo y NO levanta nada por sí sola: cada expediente espera su acto de
  selección (conservar = camino de CT; descartar = levanta y queda elegible para el
  lote), que se registra aquí mismo con TRD crud>=3. M (medio técnico) se conserva.

  Props: open, item (agrupación TVD con id, code, estado), canWrite (TRD crud>=3)
  Eventos: close, registrada
-->
<script>
  import { createEventDispatcher } from "svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";
  import { formatearFecha } from "$lib/utils/fecha.js";
  import { construirSeleccion, construirValoracion, esSeleccion } from "$lib/utils/valoracion.js";
  import { notifySuccess, notifyError } from "$lib/stores/notifications.js";

  export let open = false;
  /** @type {any} */
  export let item = null;
  export let canWrite = false;

  const dispatch = createEventDispatcher();

  /** Respuesta de GET: {valoracion: acto|null, pendientes, aplicadas, agotadas}. */
  /** @type {any} */
  let estado = null;
  $: acto = estado?.valoracion ?? null;
  let loading = false;
  let errorCarga = "";
  let enviando = false;
  let form = { acta_comite: "", fecha_acta: "", fundamento: "", metodo_muestreo: "" };
  let cargadoPara = "";
  /** Expedientes que esperan su acto de selección (solo disposición S). */
  /** @type {{expediente_id: string, expediente_code: string}[]} */
  let pendientesSeleccion = [];
  let totalSeleccion = 0;
  /** Fundamento tecleado por expediente. @type {Record<string, string>} */
  let fundamentos = {};
  /** @type {string | null} */
  let seleccionando = null;
  $: esS = esSeleccion(item);
  $: mostrados = pendientesSeleccion.length;
  $: hayMas = totalSeleccion > mostrados;

  $: if (open && item?.id && cargadoPara !== item.id) cargar(item.id);
  $: if (!open) cargadoPara = "";

  /** @param {string} id */
  async function cargar(id) {
    cargadoPara = id;
    loading = true;
    errorCarga = "";
    estado = null;
    try {
      const res = await fetch(`/admin/tvd/api/${encodeURIComponent(id)}/valoracion`);
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw d;
      estado = d;
      await cargarSeleccion(id);
    } catch (err) {
      errorCarga = mensajeError(err, "No se pudo consultar la valoración.");
    } finally {
      loading = false;
    }
  }

  /** @param {string} id */
  async function cargarSeleccion(id) {
    pendientesSeleccion = [];
    totalSeleccion = 0;
    if (
      !canWrite ||
      estado?.valoracion?.disposicion !== "S" ||
      !(estado?.seleccion_pendiente > 0)
    ) {
      return;
    }
    try {
      const res = await fetch(
        `/admin/tvd/api/${encodeURIComponent(id)}/valoracion/seleccion-pendiente?size=50`,
      );
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw d;
      pendientesSeleccion = d.items ?? [];
      totalSeleccion = d.total ?? pendientesSeleccion.length;
    } catch (err) {
      errorCarga = mensajeError(err, "No se pudo consultar la selección pendiente.");
    }
  }

  /** @param {{expediente_id: string, expediente_code: string}} exp @param {"conservar"|"descartar"} accion */
  async function seleccionar(exp, accion) {
    const c = construirSeleccion({ accion, fundamento: fundamentos[exp.expediente_id] });
    if ("error" in c) return notifyError(c.error);
    seleccionando = exp.expediente_id;
    try {
      const res = await fetch(
        `/admin/tvd/api/${encodeURIComponent(item.id)}/valoracion/expedientes/${encodeURIComponent(exp.expediente_id)}/seleccion`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(c.ok),
        },
      );
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw d;
      notifySuccess(
        accion === "conservar"
          ? `${exp.expediente_code}: seleccionado para conservación.`
          : `${exp.expediente_code}: descartado; queda elegible para el lote.`,
      );
      await cargar(item.id);
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo registrar la selección."));
    } finally {
      seleccionando = null;
    }
  }

  async function registrar() {
    const c = construirValoracion(form, esS);
    if ("error" in c) return notifyError(c.error);
    enviando = true;
    try {
      const res = await fetch(`/admin/tvd/api/${encodeURIComponent(item.id)}/valoracion`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(c.ok),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw d;
      notifySuccess(
        `Valoración registrada. Expedientes afectados: ${d.expedientes_afectados ?? 0}.`,
      );
      form = { acta_comite: "", fecha_acta: "", fundamento: "", metodo_muestreo: "" };
      dispatch("registrada");
      await cargar(item.id);
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo registrar la valoración."));
    } finally {
      enviando = false;
    }
  }
</script>

<Modal
  {open}
  title="Valoración del Comité — TVD {item?.code ?? ''}"
  on:close={() => dispatch("close")}
>
  {#if loading}
    <Loader label="Consultando valoración…" />
  {:else if errorCarga}
    <p class="help is-danger" role="alert">{errorCarga}</p>
  {:else if acto}
    <dl class="val-dl">
      <dt>Acta del Comité</dt>
      <dd>{acto.acta_comite} ({formatearFecha(acto.fecha_acta)})</dd>
      <dt>Disposición</dt>
      <dd>{acto.disposicion ?? "—"}</dd>
      <dt>Fundamento</dt>
      <dd>{acto.fundamento}</dd>
      {#if acto.metodo_muestreo}
        <dt>Método de muestreo</dt>
        <dd>{acto.metodo_muestreo}</dd>
      {/if}
      <dt>Expedientes</dt>
      <dd>
        {#if acto.disposicion === "S"}
          Esperan selección: <strong>{estado.seleccion_pendiente ?? 0}</strong> ·
        {/if}
        Pendientes: <strong>{estado.pendientes ?? 0}</strong> · Aplicadas:
        <strong>{estado.aplicadas ?? 0}</strong> · Agotadas: <strong>{estado.agotadas ?? 0}</strong>
      </dd>
    </dl>
    {#if acto.disposicion === "S" && (estado.seleccion_pendiente ?? 0) > 0}
      {#if canWrite}
        <h3 class="title is-6">Selección por expediente</h3>
        <p class="help">
          Conservar sigue el camino de Conservación Total (fija la retención); descartar levanta la
          retención legal y deja el expediente elegible para el lote. Cada acto queda en el registro
          y no se rehace.
          {#if hayMas}Se muestran {mostrados} de {totalSeleccion}.{/if}
        </p>
        <ul class="val-seleccion">
          {#each pendientesSeleccion as exp (exp.expediente_id)}
            <li>
              <strong>{exp.expediente_code}</strong>
              <input
                class="input is-small"
                type="text"
                aria-label="Fundamento de la selección de {exp.expediente_code}"
                placeholder="Fundamento (mínimo 10 caracteres)"
                bind:value={fundamentos[exp.expediente_id]}
              />
              <Button
                size="sm"
                variant="primary"
                disabled={seleccionando !== null}
                on:click={() => seleccionar(exp, "conservar")}>Conservar</Button
              >
              <Button
                size="sm"
                variant="secondary"
                disabled={seleccionando !== null}
                on:click={() => seleccionar(exp, "descartar")}>Descartar</Button
              >
            </li>
          {/each}
        </ul>
      {:else}
        <p class="help">
          {estado.seleccion_pendiente} expediente(s) esperan el acto de selección, que se registra con
          permiso de TVD de nivel 3.
        </p>
      {/if}
    {/if}
    {#if (estado.agotadas ?? 0) > 0}
      <p class="help is-danger" role="alert">
        Hay expedientes cuya aplicación agotó los reintentos: requieren intervención manual.
      </p>
    {/if}
  {:else if canWrite}
    <p class="help">
      Esta TVD aún no tiene valoración{#if (estado?.pendientes ?? 0) > 0}: hay {estado.pendientes}
        expediente(s) de fondo acumulado en retención legal esperando este acto{/if}. Registrarla
      levanta la retención legal de valoración de los expedientes del fondo acumulado y no se puede
      deshacer.
    </p>
    <div class="field">
      <label class="label" for="val-acta">Acta del Comité *</label>
      <input id="val-acta" class="input" type="text" bind:value={form.acta_comite} />
    </div>
    <div class="field">
      <label class="label" for="val-fecha">Fecha del acta *</label>
      <input id="val-fecha" class="input" type="date" bind:value={form.fecha_acta} />
    </div>
    <div class="field">
      <label class="label" for="val-fund">Fundamento * (mínimo 20 caracteres)</label>
      <textarea id="val-fund" class="textarea" rows="4" bind:value={form.fundamento}></textarea>
    </div>
    {#if esS}
      <div class="field">
        <label class="label" for="val-muestreo">Método de muestreo * (mínimo 10 caracteres)</label>
        <textarea id="val-muestreo" class="textarea" rows="3" bind:value={form.metodo_muestreo}
        ></textarea>
        <p class="help">
          La disposición es Selección: la valoración no levanta nada por sí sola. Cada expediente
          esperará su acto de selección.
        </p>
      </div>
    {/if}
  {:else}
    <p>Esta TVD aún no tiene valoración registrada.</p>
  {/if}
  <div slot="footer" class="val-footer">
    <Button variant="secondary" on:click={() => dispatch("close")}>Cerrar</Button>
    {#if !acto && !loading && !errorCarga && canWrite}
      <Button variant="primary" on:click={registrar} disabled={enviando}>
        Registrar valoración
      </Button>
    {/if}
  </div>
</Modal>

<style>
  .val-dl dt {
    font-weight: var(--op-weight-bold);
  }
  .val-dl dd {
    margin: 0 0 var(--op-space-3, 0.75rem);
  }
  .val-seleccion {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .val-seleccion li {
    display: flex;
    flex-wrap: wrap;
    gap: var(--op-space-2, 0.5rem);
    align-items: center;
    margin-bottom: var(--op-space-2, 0.5rem);
  }
  .val-seleccion .input {
    flex: 1 1 14rem;
    min-width: 0;
  }
  .val-footer {
    display: flex;
    gap: var(--op-space-2, 0.5rem);
    justify-content: flex-end;
  }
</style>
