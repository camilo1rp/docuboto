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
  WorkflowCard.svelte — Tarjeta de un paso de flujo activo, para
  WorkflowBoard.svelte (T-Fase1, RF-FLU-03).

  Renderiza SOLO lo que `FlowStepResponse` ofrece (no hay `subject`/asunto en
  este contrato — ver la cabecera de WorkflowBoard.svelte). Las acciones de
  trámite son SIEMPRE botones visibles y con etiqueta de texto (nunca solo
  icono, nunca un menú oculto): el operativo por teclado es idéntico al de
  mouse/touch, no un camino alternativo degradado.

  "Ver detalle" es la única acción de CONSULTA de la tarjeta (abre el cajón
  de detalle, que sí trae el asunto vía GET /documents): siempre visible, sin
  gate de RBAC (leer no requiere PERM_TRAMITAR).

  Las acciones de EJECUCIÓN (Trasladar/Trámite/Completar/Revertir/En trámite)
  se ocultan si `canTramitar` es falso (espejo de RBAC — el backend las gatea
  con PERM_TRAMITAR y devolvería 403) o si el paso ya no está activo
  (`completed`/`cancelled` → 409 garantizado, ver columna "Otros" en
  WorkflowBoard.svelte).

  Props:
    step          FlowStepResponse (requerido)
    busy          boolean — deshabilita las acciones mientras ESTA tarjeta tiene una
                  operación en vuelo
    boardBusy     boolean (default false) — I10 (revisión UX): el tablero es de un
                  único `busyStepId` global (WorkflowBoard.svelte), así que mientras
                  CUALQUIER tarjeta procesa una acción, las botones de TODAS las demás
                  deben quedar visiblemente deshabilitados también — antes solo la
                  propia tarjeta bloqueaba sus botones y pulsar "Completar" en otra
                  tarjeta durante ese lapso no producía ningún efecto visible (sin
                  toast, sin spinner, sin `aria-disabled`).
    canTramitar   boolean (default true) — espejo de PERM_TRAMITAR; el padre lo calcula
                  desde el JWT (D-05: el backend siempre re-valida)
    poseedor      boolean (default true) — tenencia del paso (ADR-028): las acciones de
                  ejecución solo se ofrecen al poseedor, no a quien solo lo ve

  Eventos: verDetalle, trasladar, tramite, completar, revertir, enTramite (solo
  se dispara desde el botón "Marcar en trámite", visible solo cuando
  step.status==='pending').
-->
<script>
  import { usarNombresDependencia } from "$lib/stores/dependencias.js";
  const nombresDependencia = usarNombresDependencia();
  import { nombreActual } from "$lib/utils/dependenciaNombre.js";
  import { PASO_ESTADO_CHIP, PASO_ESTADO_LABEL } from "$lib/utils/flujo.js";
  import { formatearFecha } from "$lib/utils/fecha.js";
  import { createEventDispatcher } from "svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";

  /** @type {import('$lib/types/api.js').FlowStepResponse} */
  export let step;
  export let busy = false;
  export let boardBusy = false;
  export let canTramitar = true;
  /** ADR-028: ¿soy el POSEEDOR del paso? (`$lib/utils/tenencia.js`, lo calcula el
   * padre). Un colega de la dependencia VE el paso pero no lo posee: el backend
   * le da 404 `no_active_step`/`step_not_found` en todo acto que lo mueve. */
  export let poseedor = true;

  const dispatch = createEventDispatcher();

  // Solo pending/in_progress admiten acciones de trámite — completed/cancelled
  // (columna "Otros") devolverían 409 en todas ellas.
  $: isActionable = step.status === "pending" || step.status === "in_progress";
  // I10: cualquier acción en vuelo en OTRA tarjeta también bloquea esta.
  $: actionsDisabled = busy || boardBusy;

  const DOC_TYPE_LABELS = { E: "Entrada", S: "Salida", I: "Interno" };
  // Fuente única en `$lib/utils/flujo.js`. Este mapa era el CORRECTO de los dos
  // que había; el de HojaRuta tenía un `active` inexistente y le faltaba
  // `pending`, el valor por defecto — y era el que se veía en la hoja de ruta.
  const STATUS_CHIP = PASO_ESTADO_CHIP;
  const STATUS_LABEL = PASO_ESTADO_LABEL;
  const SEMAFORO_LABEL = {
    verde: "A tiempo",
    amarillo: "Por vencer",
    rojo: "Urgente",
    vencido: "Vencido",
  };
  const SEMAFORO_CHIP = { verde: "success", amarillo: "warning", rojo: "error", vencido: "error" };

  /** @param {string|null|undefined} iso */
  function formatDate(iso) {
    return formatearFecha(iso);
  }
</script>

<article class="wf-card-body" aria-label="Radicado {step.tracking_number}">
  <header class="wf-card-body__header">
    <span class="tracking-number">{step.tracking_number}</span>
    {#if step.doc_type}
      <span class="wf-card-body__doctype">{DOC_TYPE_LABELS[step.doc_type] ?? step.doc_type}</span>
    {/if}
  </header>

  <div class="wf-card-body__deps">
    {#if step.from_dept}
      <span class="wf-card-body__dept"
        >{nombreActual(step.from_dept_code, step.from_dept, $nombresDependencia)}</span
      >
      <i class="fas fa-arrow-right" aria-hidden="true"></i>
    {/if}
    <span class="wf-card-body__dept wf-card-body__dept--current"
      >{nombreActual(step.to_dept_code, step.to_dept, $nombresDependencia)}</span
    >
  </div>

  <div class="wf-card-body__chips">
    <StatusChip
      status={STATUS_CHIP[step.status] ?? "neutral"}
      label={STATUS_LABEL[step.status] ?? step.status}
      dot
    />
    {#if step.semaforo}
      <StatusChip
        status={SEMAFORO_CHIP[step.semaforo] ?? "neutral"}
        label={SEMAFORO_LABEL[step.semaforo] ?? step.semaforo}
        dot
      />
    {/if}
  </div>

  {#if step.due_at}
    <p class="wf-card-body__due">
      <i class="fas fa-clock" aria-hidden="true"></i>
      Vence: <time datetime={step.due_at}>{formatDate(step.due_at)}</time>
    </p>
  {/if}

  {#if step.notes}
    <p class="wf-card-body__notes">{step.notes}</p>
  {/if}

  <div class="wf-card-body__actions">
    <button
      type="button"
      class="wf-card-btn"
      aria-label="Ver detalle de radicado {step.tracking_number}"
      on:click={() => dispatch("verDetalle")}
    >
      <i class="fas fa-eye" aria-hidden="true"></i>
      Detalle
    </button>

    {#if canTramitar && poseedor && isActionable}
      {#if step.status === "pending"}
        <button
          type="button"
          class="wf-card-btn"
          disabled={actionsDisabled}
          on:click={() => dispatch("enTramite")}
        >
          <i class="fas fa-play" aria-hidden="true"></i>
          En trámite
        </button>
      {/if}
      <button
        type="button"
        class="wf-card-btn"
        disabled={actionsDisabled}
        on:click={() => dispatch("trasladar")}
      >
        <i class="fas fa-share" aria-hidden="true"></i>
        Trasladar
      </button>
      <button
        type="button"
        class="wf-card-btn"
        disabled={actionsDisabled}
        on:click={() => dispatch("tramite")}
      >
        <i class="fas fa-gears" aria-hidden="true"></i>
        Trámite
      </button>
      <button
        type="button"
        class="wf-card-btn"
        disabled={actionsDisabled}
        on:click={() => dispatch("completar")}
      >
        <i class="fas fa-check" aria-hidden="true"></i>
        Completar
      </button>
      {#if step.status === "pending"}
        <!-- R19: rollback solo cancela un paso aún `pending` -->
        <button
          type="button"
          class="wf-card-btn wf-card-btn--danger"
          disabled={actionsDisabled}
          on:click={() => dispatch("revertir")}
        >
          <i class="fas fa-rotate-left" aria-hidden="true"></i>
          Revertir
        </button>
      {/if}
    {/if}
  </div>
</article>

<style lang="scss">
  .wf-card-body {
    background: var(--op-surface);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    padding: var(--op-space-3);
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);

    &__header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--op-space-2);
    }

    &__doctype {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      font-weight: var(--op-weight-medium);
    }

    &__deps {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      flex-wrap: wrap;

      i {
        font-size: var(--op-font-xs);
      }
    }

    &__dept--current {
      color: var(--op-text);
      font-weight: var(--op-weight-medium);
    }

    &__chips {
      display: flex;
      gap: var(--op-space-2);
      flex-wrap: wrap;
    }

    &__due {
      display: flex;
      align-items: center;
      gap: var(--op-space-1);
      margin: 0;
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    &__notes {
      margin: 0;
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      font-style: italic;
      overflow-wrap: anywhere;
    }

    &__actions {
      display: flex;
      flex-wrap: wrap;
      gap: var(--op-space-1);
      margin-top: var(--op-space-1);
    }
  }

  .wf-card-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    font-family: var(--op-font-family);
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-medium);
    color: var(--op-primary-dark);
    background: var(--op-primary-light);
    border: 1px solid transparent;
    border-radius: var(--op-radius-sm);
    padding: var(--op-space-1) var(--op-space-2);
    cursor: pointer;
    transition:
      background var(--op-transition),
      color var(--op-transition);

    // Objetivo táctil >=44px (v1.0 A11y) en dispositivos táctiles.
    @media (pointer: coarse) {
      min-height: 44px;
    }

    &:hover:not(:disabled) {
      background: var(--op-primary);
      color: var(--op-surface);
    }
    &:focus-visible {
      outline: 2px solid var(--op-primary);
      outline-offset: 2px;
    }
    &:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }

    &--danger {
      // --op-error-fg (no --op-error) sobre --op-error-bg da ~7.9:1 (AA
      // exige 4.5:1) — verificado; --op-error sobre este fondo daba 4.21:1.
      color: var(--op-error-fg);
      background: var(--op-error-bg);

      &:hover:not(:disabled) {
        background: var(--op-error);
        color: var(--op-surface);
      }
    }
  }

  .tracking-number {
    font-family: var(--op-font-family-mono);
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-medium);
    color: var(--op-primary-dark);
    background: var(--op-primary-light);
    padding: 2px var(--op-space-2);
    border-radius: var(--op-radius-sm);
    white-space: nowrap;
  }
</style>
