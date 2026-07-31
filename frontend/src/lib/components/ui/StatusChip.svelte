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
  StatusChip.svelte — Etiqueta de estado semántico para radicados y expedientes.

  Props:
    status  string  — clave del estado ('active'|'pending'|'closed'|'error'|'neutral')
    label   string  — texto visible (si no se pasa, usa el status capitalizado)
    dot     boolean — muestra un punto de color antes del texto (default false)

  Mapeo de estados:
    active/open/success → verde
    pending/warning     → amarillo
    closed/error        → rojo
    info                → azul
    neutral             → gris
-->
<script>
  /** @type {'active'|'open'|'success'|'pending'|'warning'|'closed'|'error'|'info'|'neutral'|string} */
  export let status = "neutral";
  export let label = "";
  export let dot = false;

  const STATUS_VARIANT_MAP = {
    active: "success",
    open: "success",
    success: "success",
    pending: "warning",
    warning: "warning",
    closed: "error",
    error: "error",
    info: "info",
    neutral: "neutral",
  };

  $: variant = STATUS_VARIANT_MAP[status] ?? "neutral";
  $: displayLabel = label || status.charAt(0).toUpperCase() + status.slice(1);
</script>

<!--
  Menor (revisión UX): `aria-label` sobre un <span> sin `role` explícito
  queda sobre el rol implícito ARIA `generic`, donde `aria-label` está
  PROHIBIDO por ARIA 1.2 y los lectores de pantalla lo ignoran (mismo
  defecto ya corregido en admin/+page.svelte, M11). El texto ya es visible
  (`displayLabel` se renderiza como contenido), así que no hace falta ningún
  nombre accesible adicional — se retira el `aria-label` en vez de añadir un
  `role` que cambiaría la semántica (p. ej. `status` dispararía anuncios de
  región viva no deseados en un chip estático).
-->
<span class="op-status-chip op-status-chip--{variant}">
  {#if dot}
    <span class="op-status-chip__dot" aria-hidden="true"></span>
  {/if}
  {displayLabel}
</span>

<style lang="scss">
  .op-status-chip {
    display: inline-flex;
    align-items: center;
    gap: var(--op-space-1);
    padding: 2px var(--op-space-2);
    border-radius: var(--op-radius-sm);
    font-size: var(--op-font-xs);
    font-weight: var(--op-weight-medium);
    line-height: 1.4;
    white-space: nowrap;

    &--success {
      background: var(--op-success-bg);
      color: var(--op-success-fg);

      .op-status-chip__dot {
        background: var(--op-success);
      }
    }
    &--warning {
      background: var(--op-warning-bg);
      color: var(--op-warning-fg);

      .op-status-chip__dot {
        background: var(--op-warning);
      }
    }
    &--error {
      background: var(--op-error-bg);
      color: var(--op-error-fg);

      .op-status-chip__dot {
        background: var(--op-error);
      }
    }
    &--info {
      background: var(--op-info-bg);
      color: var(--op-info-fg);

      .op-status-chip__dot {
        background: var(--op-info);
      }
    }
    &--neutral {
      background: var(--op-bg);
      color: var(--op-text-secondary);
      border: 1px solid var(--op-border);

      .op-status-chip__dot {
        background: var(--op-text-secondary);
      }
    }

    &__dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      flex-shrink: 0;
    }
  }
</style>
