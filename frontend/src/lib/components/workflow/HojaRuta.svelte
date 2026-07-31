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
  HojaRuta.svelte — Trazabilidad de flujo de un radicado (D-05, ADR-011, E05 §Fase 1).

  Decisión de diseño (no rediseñar sin motivo): la hoja de ruta NO es una pantalla
  propia. Siempre se consulta SOBRE un objeto (un radicado) sin perder la cola de
  trabajo — por eso este componente se monta dentro de un Drawer/panel contextual,
  no navega. Reutilizable: no está acoplado a /bandeja, solo necesita `radicadoId`
  y una base de proxies SSR con la forma `{basePath}/{id}/history|events|current`
  (D-05 — el propio componente nunca llama al gateway, solo a proxies del mismo
  origen que ya inyectan Authorization/X-Tenant-Slug/X-User-Id server-side).

  Props:
    radicadoId  string  — id del radicado a trazar (requerido)
    basePath    string  — prefijo de los proxies SSR (default: '/bandeja/api').
                          Al reutilizar en expedientes/[id] pasar el basePath de
                          los proxies equivalentes de esa ruta cuando existan.

  Combina tres fuentes en un único timeline cronológico:
    - GET {basePath}/{id}/history  → pasos de flujo (FlowStepResponse[])
    - GET {basePath}/{id}/events   → eventos de trámite append-only (FlowEventResponse[])
    - GET {basePath}/{id}/current  → paso activo (o 404 no_active_step)

  Las tres cargan en paralelo con Promise.allSettled: el fallo de una NO tumba las
  otras — se muestra qué parte no se pudo cargar en vez de fingir que está vacía.
-->
<script>
  import { onMount } from "svelte";
  import Loader from "$lib/components/ui/Loader.svelte";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";

  export let radicadoId;
  export let basePath = "/bandeja/api";

  // Traducción de `action` (pasos de flujo) a copy en español (fallback: valor crudo).
  const ACTION_LABELS = {
    assign: "Asignación",
    transfer: "Traslado",
    return: "Devolución",
    close: "Cierre",
  };

  // A9 (revisión UX): `tipo_tx` (eventos de trámite) es un código de catálogo
  // distinto de `action` — sin este mapa el operador ve identificadores crudos
  // de base de datos (marcar_leido, validate_trd_send, nrr…). Los 12 códigos
  // vienen de workflow-service/migrations/tenant/007_transaction_types.sql;
  // fallback al valor crudo si el catálogo incorpora un código nuevo.
  const TIPO_TX_LABELS = {
    informar: "Informar (copia)",
    borrar_informado: "Borrar informado",
    nrr: "No requiere respuesta",
    agendar: "Agendar",
    no_agendar: "Quitar de agenda",
    change_folder: "Cambiar de carpeta",
    marcar_leido: "Marcar como leído",
    validate_trd_send: "Validar TRD para envío",
    solicitar_firma: "Solicitar firma",
    vobo: "Visto bueno",
    cerrar_exp: "Cerrar expediente",
    anular: "Anular",
  };

  const STEP_STATUS_CHIP = {
    active: "info",
    completed: "success",
    cancelled: "error",
  };

  const STEP_STATUS_LABEL = {
    active: "Activo",
    completed: "Completado",
    cancelled: "Cancelado",
  };

  let loading = true;
  let mounted = false;

  /** @type {{status: 'fulfilled', value: any} | {status: 'rejected', reason: any} | null} */
  let historyResult = null;
  let eventsResult = null;
  let currentResult = null;

  $: trackingNumber =
    historyResult?.status === "fulfilled"
      ? historyResult.value?.tracking_number
      : currentResult?.status === "fulfilled"
        ? currentResult.value?.tracking_number
        : "";

  // 404 no_active_step es un resultado legítimo (fulfilled, pero con cuerpo de
  // error en vez de un paso real) — no es un paso activo ni una falla de carga.
  $: currentStep =
    currentResult?.status === "fulfilled" && !currentResult.value?.error
      ? currentResult.value
      : null;
  $: currentHasError = currentResult?.status === "rejected";

  $: historySteps =
    historyResult?.status === "fulfilled" && Array.isArray(historyResult.value?.steps)
      ? historyResult.value.steps
      : [];
  $: historyError = historyResult?.status === "rejected";

  $: flowEvents =
    eventsResult?.status === "fulfilled" && Array.isArray(eventsResult.value)
      ? eventsResult.value
      : [];
  $: eventsError = eventsResult?.status === "rejected";

  // Timeline combinado: pasos + eventos, orden cronológico ascendente (narrativa
  // de "qué pasó primero"). El paso activo (si existe) se marca aparte.
  $: timeline = [
    ...historySteps.map((step) => ({
      key: `step-${step.id}`,
      kind: "step",
      ts: step.created_at,
      data: step,
      isCurrent: !!currentStep && currentStep.id === step.id,
    })),
    ...flowEvents.map((evt) => ({
      key: `event-${evt.id}`,
      kind: "event",
      ts: evt.ts,
      data: evt,
      isCurrent: false,
    })),
  ].sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime());

  /**
   * Envuelve un fetch a un proxy SSR: convierte una respuesta no-ok en un
   * rechazo, así Promise.allSettled distingue "cargó vacío" de "falló".
   * @param {string} path
   */
  async function fetchOrThrow(path) {
    const res = await fetch(path);
    const data = await res.json().catch(() => ({}));
    if (!res.ok && data?.error !== "no_active_step") {
      throw new Error(data?.detail || data?.error || `Error ${res.status}`);
    }
    return data;
  }

  async function loadData(id) {
    loading = true;
    const encoded = encodeURIComponent(id);
    const [h, e, c] = await Promise.allSettled([
      fetchOrThrow(`${basePath}/${encoded}/history`),
      fetchOrThrow(`${basePath}/${encoded}/events`),
      fetchOrThrow(`${basePath}/${encoded}/current`),
    ]);
    historyResult = h;
    eventsResult = e;
    currentResult = c;
    loading = false;
  }

  // Guard de montaje: nunca dispara fetch durante SSR. En la práctica este
  // componente solo se monta client-side (dentro de un Drawer ya abierto por
  // interacción del usuario), pero el guard lo hace seguro también si se reutiliza
  // en un contexto renderizado en servidor.
  onMount(() => {
    mounted = true;
  });

  $: if (mounted && radicadoId) loadData(radicadoId);

  /** @param {string} iso */
  function formatDateTime(iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleString("es-CO", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }
</script>

<div class="op-hoja-ruta">
  {#if loading}
    <Loader type="skeleton" lines={5} label="Cargando hoja de ruta…" />
  {:else}
    {#if trackingNumber}
      <p class="op-hoja-ruta__tracking">
        <span class="tracking-number">{trackingNumber}</span>
      </p>
    {/if}

    <!-- Ubicación actual -->
    <section class="op-hoja-ruta__section" aria-labelledby="hoja-ruta-current-heading">
      <h4 id="hoja-ruta-current-heading" class="op-hoja-ruta__heading">Ubicación actual</h4>
      {#if currentHasError}
        <p class="op-hoja-ruta__error" role="alert">
          <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
          No se pudo cargar el paso actual.
        </p>
      {:else if currentStep}
        <div class="op-hoja-ruta__current">
          <i class="fas fa-location-dot" aria-hidden="true"></i>
          <span class="op-hoja-ruta__current-dept">{currentStep.to_dept}</span>
          <StatusChip
            status={STEP_STATUS_CHIP[currentStep.status] ?? "neutral"}
            label={STEP_STATUS_LABEL[currentStep.status] ?? currentStep.status}
            dot
          />
        </div>
      {:else}
        <EmptyState
          icon="fas fa-route"
          title="Sin paso activo"
          description="Este radicado no tiene un paso de flujo en curso."
        />
      {/if}
    </section>

    <!-- Timeline -->
    <section class="op-hoja-ruta__section" aria-labelledby="hoja-ruta-timeline-heading">
      <h4 id="hoja-ruta-timeline-heading" class="op-hoja-ruta__heading">Trazabilidad</h4>

      {#if historyError}
        <p class="op-hoja-ruta__error" role="alert">
          <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
          No se pudo cargar el historial de pasos.
        </p>
      {/if}
      {#if eventsError}
        <p class="op-hoja-ruta__error" role="alert">
          <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
          No se pudieron cargar los eventos de trámite.
        </p>
      {/if}

      {#if timeline.length === 0 && !historyError && !eventsError}
        <!-- Solo se afirma "no hay nada" cuando AMBAS fuentes cargaron y de
          verdad vinieron vacías — si alguna falló, arriba ya se avisó del
          error y aquí no se contradice diciendo que no hay trazabilidad. -->
        <EmptyState
          icon="fas fa-timeline"
          title="Sin movimientos registrados"
          description="Este radicado aún no tiene trazabilidad de flujo."
        />
      {:else if timeline.length > 0}
        <ol class="op-hoja-ruta__timeline" aria-label="Línea de tiempo del radicado">
          {#each timeline as item (item.key)}
            <li
              class="op-hoja-ruta__item"
              class:op-hoja-ruta__item--current={item.isCurrent}
              aria-current={item.isCurrent ? "step" : undefined}
            >
              <span class="op-hoja-ruta__marker" aria-hidden="true"></span>
              <div class="op-hoja-ruta__content">
                <div class="op-hoja-ruta__row">
                  {#if item.kind === "step"}
                    <span class="op-hoja-ruta__action">
                      {ACTION_LABELS[item.data.action] ?? item.data.action}
                    </span>
                    <StatusChip
                      status={STEP_STATUS_CHIP[item.data.status] ?? "neutral"}
                      label={STEP_STATUS_LABEL[item.data.status] ?? item.data.status}
                      dot
                    />
                    {#if item.isCurrent}
                      <span class="op-hoja-ruta__current-badge">
                        <i class="fas fa-location-dot" aria-hidden="true"></i>
                        Paso actual
                      </span>
                    {/if}
                  {:else}
                    <span class="op-hoja-ruta__action">
                      {TIPO_TX_LABELS[item.data.tipo_tx] ?? item.data.tipo_tx}
                    </span>
                  {/if}
                </div>

                <p class="op-hoja-ruta__meta">
                  {#if item.data.from_dept}
                    <span>{item.data.from_dept}</span>
                    <i class="fas fa-arrow-right" aria-hidden="true"></i>
                  {/if}
                  <span>{item.data.to_dept ?? "—"}</span>
                </p>

                {#if item.data.notes || item.data.comentario}
                  <p class="op-hoja-ruta__comment">{item.data.notes ?? item.data.comentario}</p>
                {/if}

                <time class="op-hoja-ruta__time" datetime={item.ts}>{formatDateTime(item.ts)}</time>
              </div>
            </li>
          {/each}
        </ol>
      {/if}
    </section>
  {/if}
</div>

<style lang="scss">
  .op-hoja-ruta {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-5);

    &__tracking {
      margin: 0;
    }

    &__section {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-2);
    }

    &__heading {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0;
      text-transform: uppercase;
      letter-spacing: 0.02em;
    }

    &__error {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      color: var(--op-error);
      font-size: var(--op-font-sm);
      margin: 0;
    }

    &__current {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-3);
      background: var(--op-primary-light);
      border-radius: var(--op-radius);
      color: var(--op-primary-dark);

      i {
        color: var(--op-primary-dark);
      }
    }

    &__current-dept {
      flex: 1;
      font-weight: var(--op-weight-medium);
    }

    &__timeline {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
    }

    &__item {
      position: relative;
      display: flex;
      gap: var(--op-space-3);
      padding-bottom: var(--op-space-5);
      padding-left: var(--op-space-2);

      &:last-child {
        padding-bottom: 0;
      }

      // Línea vertical del timeline
      &:not(:last-child)::before {
        content: "";
        position: absolute;
        left: 9px;
        top: 18px;
        bottom: 0;
        width: 2px;
        background: var(--op-border);
      }

      &--current .op-hoja-ruta__marker {
        background: var(--op-primary);
        box-shadow: 0 0 0 4px var(--op-primary-light);
      }
    }

    &__marker {
      flex-shrink: 0;
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--op-border);
      margin-top: 5px;
    }

    &__content {
      flex: 1;
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
      min-width: 0;
    }

    &__row {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      flex-wrap: wrap;
    }

    &__action {
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
      font-size: var(--op-font-sm);
    }

    // El paso actual se distingue por texto + icono, no solo por color (A11y v1.0).
    &__current-badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-bold);
      color: var(--op-primary-dark);
    }

    &__meta {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      margin: 0;
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      flex-wrap: wrap;
    }

    &__comment {
      margin: 0;
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      font-style: italic;
    }

    &__time {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }
  }

  // Reutiliza el mismo patrón visual que el resto de la app para el tracking_number.
  .tracking-number {
    font-family: monospace;
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-medium);
    color: var(--op-primary-dark);
    background: var(--op-primary-light);
    padding: 2px var(--op-space-2);
    border-radius: var(--op-radius-sm);
    white-space: nowrap;
  }
</style>
