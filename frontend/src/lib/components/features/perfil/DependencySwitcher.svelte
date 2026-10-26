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
<script context="module">
  import { mensajeError } from "$lib/utils/apiError.js";
  // Contador de instancias: el `id` del <select> tiene que ser ÚNICO en el
  // documento porque una <label for> lo referencia. En `/perfil` conviven DOS
  // instancias de este componente —la de la barra superior (`compact`) y la de
  // la sección «Dependencia activa»— y ambas emitían `id="dep-switcher-select"`.
  // Con ids repetidos las dos etiquetas apuntan al PRIMER select: quien navega
  // por teclado o con lector de pantalla activa el conmutador de la barra
  // superior creyendo que toca el de la sección que está leyendo.
  let _instancias = 0;
</script>

<script>
  import { invalidateAll } from "$app/navigation";
  import { notifySuccess, notifyError } from "$lib/stores/notifications.js";

  const selectId = `dep-switcher-select-${(_instancias += 1)}`;

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
        notifyError(mensajeError(data, "No se pudo cambiar la dependencia activa."));
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
    <label for={selectId} class="dep-switcher__label" class:sr-only={compact}>
      Dependencia activa
    </label>
    <div class="dep-switcher__control">
      {#if compact}
        <i class="fas fa-building dep-switcher__icon" aria-hidden="true"></i>
      {/if}
      <select
        id={selectId}
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
{:else if !compact}
  <!--
    CERO dependencias asignadas. Sin esta rama la sección entera se quedaba
    en un encabezado, un párrafo que promete un control, y silencio — que se
    lee como una pantalla rota. Y la persona no tiene forma de adivinar que
    lo que falta es una asignación usuario–rol–dependencia hecha por un
    administrador, no un fallo del sistema.

    En la variante `compact` (barra superior) se sigue sin pintar nada: ahí un
    aviso no cabe y no hay nada que conmutar. El sitio donde esto se explica
    es la pantalla de perfil, que es donde la persona vino a mirarlo.
  -->
  <p class="dep-switcher__empty">
    No tiene ninguna dependencia asignada, así que aún no puede radicar ni tramitar desde una unidad
    organizacional. Un administrador debe asignársela en
    <strong>Administración → Usuarios</strong>.
  </p>
{/if}

<style lang="scss">
  @use "$styles/_tokens" as t;

  .dep-switcher {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-1);

    &__empty {
      margin: 0;
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
    }

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
      // En la barra superior el conmutador comparte sitio con la búsqueda, el
      // asistente y el usuario. `min-width: 0` le permite ceder cuando no cabe
      // —un `<select>` no encoge por debajo de su opción más larga si no se le
      // dice—, y en pantalla estrecha se acota más: sigue siendo elegible y
      // sigue mostrando el nombre completo al desplegarse, que es cuando hace
      // falta leerlo. Esconderlo habría sido quitar una capacidad en el único
      // sitio donde se cambia de dependencia.
      min-width: 0;

      .dep-switcher__control {
        min-width: 0;
      }

      .dep-switcher__select {
        min-height: 36px;
        min-width: 0;
        max-width: 220px;
        font-size: var(--op-font-xs);

        @media (max-width: 640px) {
          max-width: 104px;
        }
      }
    }
  }
</style>
