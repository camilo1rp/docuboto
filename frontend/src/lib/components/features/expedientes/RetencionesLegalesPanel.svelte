<!--
  Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
  Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
  Contacto: aurigadl@gmail.com

  Software libre: este archivo se distribuye bajo la Licencia Pública General
  Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
  conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
  SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
-->

<!--
  RetencionesLegalesPanel.svelte — Retenciones legales de un expediente (C-5 / C7).

  Lista las retenciones por motivo (judicial, disciplinario, fiscal,
  administrativo y la `valoracion_pendiente` del sistema). Levantar una NO libera
  las demás: el backend decide si el almacén se libera. Por eso el levantamiento
  exige repetir el motivo (un acto no puede levantar la retención de otro) y la
  retención de valoración se ve pero no ofrece botón: la levanta la valoración
  del Comité sobre la TVD.

  Props:
    expedienteId string  — UUID del expediente
    puedeEscribir boolean — USUA_PERM_ADMIN crud>=3 (espejo; el backend re-valida)
-->
<script>
  import { onMount } from "svelte";
  import Modal from "$lib/components/ui/Modal.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import { notifySuccess, notifyError } from "$lib/stores/notifications.js";
  import {
    MOTIVOS_MANUALES,
    motivoLabel,
    esDeSistema,
    estaActivo,
    construirFijar,
    construirLevantar,
  } from "$lib/utils/legalHold.js";

  export let expedienteId = "";
  export let puedeEscribir = false;

  /** @type {any[]} */
  let holds = [];
  let loading = true;
  let errorCarga = "";

  $: base = `/expedientes/${encodeURIComponent(expedienteId)}/api/legal-holds`;
  $: activos = holds.filter(estaActivo);
  $: valoracion = activos.find(esDeSistema);

  async function cargar() {
    loading = true;
    errorCarga = "";
    try {
      const res = await fetch(base);
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw d;
      holds = Array.isArray(d) ? d : (d.items ?? []);
    } catch (err) {
      holds = [];
      errorCarga = mensajeError(err, "No se pudieron cargar las retenciones legales.");
    } finally {
      loading = false;
    }
  }
  onMount(cargar);

  // --- Fijar ----------------------------------------------------------------
  let showFijar = false;
  let fijar = { motivo_tipo: "", fundamento: "", acta_ref: "" };
  let enviando = false;

  async function confirmarFijar() {
    const c = construirFijar(fijar);
    if ("error" in c) return notifyError(c.error);
    enviando = true;
    try {
      const res = await fetch(base, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(c.ok),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw d;
      notifySuccess("Retención legal fijada.");
      showFijar = false;
      fijar = { motivo_tipo: "", fundamento: "", acta_ref: "" };
      await cargar();
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo fijar la retención legal."));
    } finally {
      enviando = false;
    }
  }

  // --- Levantar -------------------------------------------------------------
  let showLevantar = false;
  /** @type {any} */
  let objetivo = null;
  let lev = { motivo_confirmado: "", fundamento: "", acta_ref: "" };

  function abrirLevantar(/** @type {any} */ h) {
    objetivo = h;
    lev = { motivo_confirmado: "", fundamento: "", acta_ref: "" };
    showLevantar = true;
  }

  // El botón solo se habilita cuando el motivo repetido es el de la retención:
  // el backend lo exige igual (409 motivo_no_coincide).
  $: motivoCoincide = !!objetivo && lev.motivo_confirmado === objetivo.motivo_tipo;

  async function confirmarLevantar() {
    const c = construirLevantar(lev);
    if ("error" in c) return notifyError(c.error);
    enviando = true;
    try {
      const res = await fetch(`${base}/${encodeURIComponent(objetivo.hold_id)}/levantar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(c.ok),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) throw d;
      notifySuccess(
        d.holds_restantes > 0
          ? "Retención levantada. Quedan otras retenciones activas: el expediente sigue retenido."
          : "Retención levantada. El expediente ya no tiene retenciones legales activas.",
      );
      showLevantar = false;
      await cargar();
    } catch (err) {
      notifyError(mensajeError(err, "No se pudo levantar la retención legal."));
    } finally {
      enviando = false;
    }
  }
</script>

<section class="rl" aria-labelledby="rl-titulo">
  <header class="rl__head">
    <h2 id="rl-titulo" class="title is-5">Retenciones legales</h2>
    {#if puedeEscribir}
      <Button variant="primary" size="sm" on:click={() => (showFijar = true)}>
        <i class="fas fa-lock" aria-hidden="true"></i> Fijar retención
      </Button>
    {/if}
  </header>

  {#if valoracion}
    <div class="notification is-warning is-light" role="status">
      Retención legal hasta la valoración del Comité ({valoracion.acta_ref}). Este expediente
      pertenece a un fondo acumulado con calendario vencido: sus archivos de conservación no se
      destruyen mientras no se valore la TVD.
    </div>
  {/if}

  {#if loading}
    <Loader label="Cargando retenciones legales…" />
  {:else if errorCarga}
    <p class="help is-danger" role="alert">{errorCarga}</p>
  {:else if holds.length === 0}
    <EmptyState
      icon="fas fa-lock-open"
      title="Sin retenciones legales"
      description="Este expediente no tiene ni ha tenido retenciones legales."
    />
  {:else}
    <!-- svelte-ignore a11y-no-noninteractive-tabindex -->
    <div
      class="rl__wrap op-table-scroll"
      role="region"
      aria-label="Retenciones legales"
      tabindex="0"
    >
      <table class="table is-fullwidth">
        <caption class="is-sr-only">Retenciones legales del expediente</caption>
        <thead>
          <tr>
            <th scope="col">Motivo</th>
            <th scope="col">Fundamento</th>
            <th scope="col">Acta / oficio</th>
            <th scope="col">Fijada</th>
            <th scope="col">Estado</th>
            <th scope="col"><span class="is-sr-only">Acciones</span></th>
          </tr>
        </thead>
        <tbody>
          {#each holds as h (h.hold_id)}
            <tr>
              <td>{motivoLabel(h.motivo_tipo)}</td>
              <td>{h.fundamento ?? "—"}</td>
              <td>{h.acta_ref ?? "—"}</td>
              <td>{formatearFechaHora(h.fijado_at)}</td>
              <td>
                {#if estaActivo(h)}
                  <StatusChip status="warning" label="Activa" dot />
                {:else}
                  <StatusChip
                    status="neutral"
                    label="Levantada {formatearFechaHora(h.levantado_at)}"
                  />
                {/if}
              </td>
              <td>
                {#if estaActivo(h) && esDeSistema(h)}
                  <span class="help">La levanta la valoración del Comité</span>
                {:else if estaActivo(h) && puedeEscribir}
                  <Button size="sm" variant="secondary" on:click={() => abrirLevantar(h)}>
                    Levantar
                  </Button>
                {/if}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</section>

<Modal open={showFijar} title="Fijar retención legal" on:close={() => (showFijar = false)}>
  <div class="field">
    <label class="label" for="rl-motivo">Motivo *</label>
    <div class="select is-fullwidth">
      <select id="rl-motivo" bind:value={fijar.motivo_tipo}>
        <option value="">— Elija el motivo —</option>
        {#each MOTIVOS_MANUALES as m}<option value={m}>{motivoLabel(m)}</option>{/each}
      </select>
    </div>
  </div>
  <div class="field">
    <label class="label" for="rl-fundamento">Fundamento * (mínimo 10 caracteres)</label>
    <textarea id="rl-fundamento" class="textarea" rows="3" bind:value={fijar.fundamento}></textarea>
  </div>
  <div class="field">
    <label class="label" for="rl-acta">Acta u oficio que la ordena *</label>
    <input id="rl-acta" class="input" type="text" bind:value={fijar.acta_ref} />
  </div>
  <div slot="footer" class="rl__footer">
    <Button variant="secondary" on:click={() => (showFijar = false)}>Cancelar</Button>
    <Button variant="primary" on:click={confirmarFijar} disabled={enviando}>Fijar retención</Button>
  </div>
</Modal>

<Modal open={showLevantar} title="Levantar retención legal" on:close={() => (showLevantar = false)}>
  {#if objetivo}
    <p>
      Va a levantar la retención <strong>{motivoLabel(objetivo.motivo_tipo)}</strong>
      ({objetivo.acta_ref}). Si el expediente tiene otras retenciones activas, seguirá retenido.
    </p>
    <div class="field">
      <label class="label" for="rl-confirma">Repita el motivo para confirmar *</label>
      <div class="select is-fullwidth">
        <select id="rl-confirma" bind:value={lev.motivo_confirmado}>
          <option value="">— Elija el motivo —</option>
          {#each MOTIVOS_MANUALES as m}<option value={m}>{motivoLabel(m)}</option>{/each}
        </select>
      </div>
    </div>
    <div class="field">
      <label class="label" for="rl-lev-fund">Fundamento * (mínimo 10 caracteres)</label>
      <textarea id="rl-lev-fund" class="textarea" rows="3" bind:value={lev.fundamento}></textarea>
    </div>
    <div class="field">
      <label class="label" for="rl-lev-acta">Acta u oficio del levantamiento *</label>
      <input id="rl-lev-acta" class="input" type="text" bind:value={lev.acta_ref} />
    </div>
  {/if}
  <div slot="footer" class="rl__footer">
    <Button variant="secondary" on:click={() => (showLevantar = false)}>Cancelar</Button>
    <Button variant="danger" on:click={confirmarLevantar} disabled={enviando || !motivoCoincide}>
      Levantar retención
    </Button>
  </div>
</Modal>

<style>
  .rl__head {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: var(--op-space-3, 0.75rem);
    flex-wrap: wrap;
    margin-bottom: var(--op-space-3, 0.75rem);
  }
  .rl__footer {
    display: flex;
    gap: var(--op-space-2, 0.5rem);
    justify-content: flex-end;
  }
</style>
