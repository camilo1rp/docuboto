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
  DependencySwitcher.svelte — Conmutador de dependencia activa (E08,
  ADR-013). Usado en DOS sitios que comparten la MISMA fuente de datos
  (`(app)/+layout.server.js` → `data.user.context`/`data.user.dependencias`):

    1. `AppLayout.svelte` (topbar, `compact`) — visible en cualquier pantalla.
    2. `perfil/+page.svelte` (sección "Dependencia activa", vista completa).

  El cambio de contexto NO reemite el JWT (ADR-013): se persiste en
  auth-service y se relee en cada `/auth/context`/`/auth/me`. Por eso este
  componente SIEMPRE llama a `invalidateAll()` tras un cambio exitoso — sin
  eso, el resto de la interfaz (nombre de dependencia en el topbar, y
  cualquier pantalla que en el futuro dependa del contexto activo) quedaría
  mostrando datos obsoletos: el mismo defecto de "UI que miente sobre lo que
  el usuario puede hacer" que el resto de esta sesión de trabajo corrigió.

  Props:
    activeDepeId    number|null                                  — `context.active_depe_id`
    availableDepts  Array<{depe_id:number, rol_id:string, es_principal:boolean, is_active:boolean}>
    dependencias    Array<{codigo:number, nombre:string, sigla:string}>
    compact         boolean  — variante topbar (icono + <select> angosto), default false

  Accesibilidad:
    - <select> nativo (navegación por teclado gratis, sin popover propio)
    - label asociado (visible en la variante completa, sr-only en compact)
    - Estado "cambiando…" anunciado vía aria-busy + live region
-->
<script>
  import { invalidateAll } from "$app/navigation";
  import { notifySuccess, notifyError } from "$lib/stores/notifications.js";

  /** @type {number|null} */
  export let activeDepeId = null;
  /** @type {Array<{depe_id:number, rol_id?:string, es_principal?:boolean, is_active?:boolean}>} */
  export let availableDepts = [];
  /** @type {Array<{codigo:number, nombre:string, sigla?:string}>} */
  export let dependencias = [];
  export let compact = false;

  let switching = false;

  $: activeDepts = (availableDepts ?? []).filter((d) => d.is_active !== false);

  /** @param {number} codigo */
  function labelFor(codigo) {
    const dep = (dependencias ?? []).find((d) => d.codigo === codigo);
    if (!dep) return `Dependencia ${codigo}`;
    return dep.sigla ? `${dep.sigla} — ${dep.nombre}` : dep.nombre;
  }

  // Valor local del <select>, sincronizado con la prop pero editable en
  // vuelo (no se confirma hasta que la llamada al backend responde OK).
  let selected = activeDepeId;
  $: selected = activeDepeId;

  async function handleChange(event) {
    const nextDepeId = Number(event.currentTarget.value);
    if (!Number.isInteger(nextDepeId) || nextDepeId === activeDepeId) return;

    switching = true;
    try {
      const res = await fetch("/perfil/api/contexto/switch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ depe_id: nextDepeId }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(data.detail ?? "No se pudo cambiar la dependencia activa.");
        selected = activeDepeId; // revertir el <select> visualmente
        return;
      }
      notifySuccess(`Dependencia activa: ${labelFor(nextDepeId)}.`);
      // Refresca (app)/+layout.server.js completo — recarga permisos,
      // contexto y navegación con el nuevo estado del servidor (D-16/D-05:
      // nunca asumir el nuevo contexto solo del lado cliente).
      await invalidateAll();
    } catch {
      notifyError("No se pudo conectar al servidor de autenticación.");
      selected = activeDepeId;
    } finally {
      switching = false;
    }
  }
</script>

{#if activeDepts.length > 0}
  <div class="dep-switcher" class:dep-switcher--compact={compact} aria-busy={switching}>
    <label
      for="dep-switcher-select"
      class="dep-switcher__label"
      class:sr-only={compact}
    >
      Dependencia activa
    </label>
    <div class="dep-switcher__control">
      {#if compact}
        <i class="fas fa-building dep-switcher__icon" aria-hidden="true"></i>
      {/if}
      <select
        id="dep-switcher-select"
        class="dep-switcher__select"
        bind:value={selected}
        disabled={switching || activeDepts.length <= 1}
        on:change={handleChange}
        aria-label={compact ? "Dependencia activa" : undefined}
      >
        {#each activeDepts as d (d.depe_id)}
          <option value={d.depe_id}>
            {labelFor(d.depe_id)}{d.es_principal ? " (principal)" : ""}
          </option>
        {/each}
      </select>
      {#if switching}
        <span class="dep-switcher__spinner" role="status" aria-live="polite">
          <i class="fas fa-spinner fa-spin" aria-hidden="true"></i>
          <span class="sr-only">Cambiando dependencia…</span>
        </span>
      {/if}
    </div>
    {#if !compact && activeDepts.length <= 1}
      <p class="dep-switcher__hint">Solo tiene una dependencia asignada.</p>
    {/if}
  </div>
{/if}

<style lang="scss">
  @use "$styles/_tokens" as t;

  .dep-switcher {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-1);

    &__label {
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-medium);
      color: var(--op-text-secondary);
    }

    &__control {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
    }

    &__icon {
      color: var(--op-text-secondary);
      flex-shrink: 0;
    }

    &__select {
      min-height: 44px;
      padding: 0 var(--op-space-3);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      background: var(--op-surface);
      color: var(--op-text);
      font-size: var(--op-font-sm);
      max-width: 100%;

      &:disabled {
        opacity: 0.7;
        cursor: not-allowed;
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }

      @media (pointer: coarse) {
        min-height: 48px;
      }
    }

    &__spinner {
      color: var(--op-text-secondary);
      flex-shrink: 0;
    }

    &__hint {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &--compact {
      .dep-switcher__select {
        min-height: 36px;
        max-width: 220px;
        font-size: var(--op-font-xs);
      }
    }
  }
</style>
