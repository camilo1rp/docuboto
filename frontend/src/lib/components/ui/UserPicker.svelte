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
  UserPicker.svelte — Selector de usuarios (T-08c).

  Nació para una cadena de revisión (vistos buenos), donde el ORDEN importa: el
  primer revisor decide primero, luego el segundo. Pero se reutiliza en otros
  cuatro sitios que NO son una cadena —el responsable de un traslado, el de una
  asignación, y el usuario origen y destino de una reasignación—, y allí llevaba
  el vocabulario del primer caso pegado: el botón anunciaba «Agregar a X a la
  cadena», la columna se titulaba «Cadena de revisión», y al elegir al único
  responsable posible el aviso «Máximo 1 revisores» BORRABA la ayuda que el
  padre había escrito. Quien usa lector de pantalla oía un acto que no era.

  Ahora el vocabulario se declara. Los valores por defecto son NEUTROS a
  propósito: un uso nuevo que olvide declararlos dirá «usuario», que es cierto
  en todos los casos, en vez de «revisor», que solo lo es en uno. El default
  tiene que ser el que nunca miente.

  Props:
    id        string  — prefijo de id para asociar label/controles (a11y)
    label     string  — texto del label visible
    users     Array<{id:string; username:string; email?:string; is_active?:boolean}>
              — universo de usuarios disponibles (cargado por el padre vía SSR).
    value     string[] — ids seleccionados EN ORDEN (bindable). Default [].
    disabled  boolean
    error     string  — mensaje de error
    hint      string  — texto de ayuda
    max       number  — máximo de selecciones (default 20, paridad backend CadenaCreate)
    noun      string  — cómo se llama lo que se elige, en singular (default "usuario")
    nounPlural string — su plural (default "usuarios")
    listTitle string  — título de la columna de seleccionados (default "Seleccionados")
    emptyText string  — texto cuando no hay ninguno (default: derivado de nounPlural)
    ordered   boolean|null — ¿importa el orden? `null` (default) lo deriva de
              `max > 1`: así, un selector de uno solo no ofrece flechas de
              reordenar que no pueden hacer nada, y el olvido cae del lado bueno.

  El componente NO llama al gateway: recibe `users` ya resueltos por el padre
  (un proxy SSR que lee la cookie httpOnly — D-05). Solo gestiona la selección.

  Accesibilidad:
    - búsqueda con label asociado
    - cada acción (agregar/subir/bajar/quitar) tiene aria-label descriptivo
    - una live region anuncia los cambios de la cadena
-->
<script>
  export let id = "userpicker";
  export let label = "";
  /** @type {Array<{id:string; username:string; email?:string; is_active?:boolean}>} */
  export let users = [];
  /** @type {string[]} — ids seleccionados, en orden */
  export let value = [];
  export let disabled = false;
  export let error = "";
  export let hint = "";
  export let max = 20;
  export let noun = "usuario";
  export let nounPlural = "usuarios";
  export let listTitle = "";
  export let emptyText = "";
  /** @type {boolean|null} */
  export let ordered = null;

  let query = "";

  // El orden solo importa si caben varios. Derivarlo evita que un selector de
  // uno solo pinte dos flechas permanentemente deshabilitadas por fila.
  $: isOrdered = ordered === null ? max > 1 : ordered;
  $: titulo = listTitle || "Seleccionados";
  $: vacio = emptyText || (isOrdered ? `Agregue ${nounPlural}.` : `Agregue un ${noun}.`);

  // Mapa id → usuario para resolver nombres en la cadena seleccionada.
  $: usersById = new Map(users.map((u) => [u.id, u]));

  // Disponibles: activos, no seleccionados, que casen con la búsqueda.
  $: available = users
    .filter((u) => u.is_active !== false && !value.includes(u.id))
    .filter((u) => {
      if (!query.trim()) return true;
      const q = query.trim().toLowerCase();
      return u.username.toLowerCase().includes(q) || (u.email ?? "").toLowerCase().includes(q);
    });

  // Cadena seleccionada resuelta a objetos (preserva el orden de `value`).
  $: chain = value.map((uid) => usersById.get(uid)).filter(Boolean);

  $: atMax = value.length >= max;

  // Live region: describe la cadena EN ORDEN para lectores de pantalla. Se recompone
  // tras cada cambio (agregar/quitar/subir/bajar) para que el reordenamiento se anuncie.
  $: liveMessage = !isOrdered
    ? chain.length === 0
      ? `Sin ${noun} seleccionado.`
      : `${noun} seleccionado: ${chain[0].username}.`
    : chain.length === 0
      ? `Sin ${nounPlural} seleccionados.`
      : `Cadena de ${chain.length} ${chain.length === 1 ? noun : nounPlural}: ` +
        chain.map((u, i) => `${i + 1}. ${u.username}`).join(", ") +
        ".";

  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  $: describedBy = [hint ? hintId : null, errorId].filter(Boolean).join(" ") || undefined;

  function add(uid) {
    if (disabled || atMax || value.includes(uid)) return;
    value = [...value, uid];
  }

  function remove(uid) {
    if (disabled) return;
    value = value.filter((x) => x !== uid);
  }

  function move(index, delta) {
    if (disabled) return;
    const target = index + delta;
    if (target < 0 || target >= value.length) return;
    const next = [...value];
    const [item] = next.splice(index, 1);
    next.splice(target, 0, item);
    value = next;
  }
</script>

<div class="op-userpicker" class:op-userpicker--error={!!error}>
  {#if label}
    <span class="op-userpicker__label" id="{id}-label">{label}</span>
  {/if}

  <!-- Búsqueda de usuarios disponibles -->
  <div class="op-userpicker__search">
    <label class="sr-only" for="{id}-search">Buscar usuario por nombre o correo</label>
    <span class="op-userpicker__search-icon" aria-hidden="true">
      <i class="fas fa-search"></i>
    </span>
    <input
      id="{id}-search"
      class="op-userpicker__search-input"
      type="text"
      bind:value={query}
      {disabled}
      placeholder="Buscar usuario…"
      autocomplete="off"
      aria-describedby={describedBy}
    />
  </div>

  <div class="op-userpicker__panels">
    <!-- Disponibles -->
    <div class="op-userpicker__panel">
      <p class="op-userpicker__panel-title">
        Disponibles{#if query.trim()}&nbsp;({available.length}){/if}
      </p>
      {#if available.length === 0}
        <p class="op-userpicker__empty">
          {query.trim() ? "Sin coincidencias." : "No hay más usuarios disponibles."}
        </p>
      {:else}
        <ul class="op-userpicker__list" aria-labelledby="{id}-label">
          {#each available as u (u.id)}
            <li class="op-userpicker__item">
              <span class="op-userpicker__user">
                <span class="op-userpicker__username">{u.username}</span>
                {#if u.email}
                  <span class="op-userpicker__email">{u.email}</span>
                {/if}
              </span>
              <button
                type="button"
                class="op-userpicker__btn op-userpicker__btn--add"
                on:click={() => add(u.id)}
                disabled={disabled || atMax}
                aria-label={isOrdered
                  ? `Agregar a ${u.username} a la cadena`
                  : `Agregar a ${u.username} como ${noun}`}
              >
                <i class="fas fa-plus" aria-hidden="true"></i>
              </button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>

    <!-- Cadena seleccionada (ordenada) -->
    <div class="op-userpicker__panel">
      <p class="op-userpicker__panel-title" id="{id}-chain-title">
        {titulo} ({chain.length})
      </p>
      {#if chain.length === 0}
        <p class="op-userpicker__empty">{vacio}</p>
      {:else}
        <svelte:element
          this={isOrdered ? "ol" : "ul"}
          class="op-userpicker__list"
          class:op-userpicker__list--ordered={isOrdered}
          aria-labelledby="{id}-chain-title"
        >
          {#each chain as u, i (u.id)}
            <li class="op-userpicker__item">
              {#if isOrdered}
                <span class="op-userpicker__order" aria-hidden="true">{i + 1}</span>
              {/if}
              <span class="op-userpicker__user">
                <span class="op-userpicker__username">
                  {#if isOrdered}
                    <span class="sr-only">Posición {i + 1} de {chain.length}: </span>
                  {/if}
                  {u.username}
                </span>
                {#if u.email}
                  <span class="op-userpicker__email">{u.email}</span>
                {/if}
              </span>
              <span class="op-userpicker__actions">
                {#if isOrdered}
                  <button
                    type="button"
                    class="op-userpicker__btn"
                    on:click={() => move(i, -1)}
                    disabled={disabled || i === 0}
                    aria-label={`Subir a ${u.username} a la posición ${i}`}
                  >
                    <i class="fas fa-arrow-up" aria-hidden="true"></i>
                  </button>
                  <button
                    type="button"
                    class="op-userpicker__btn"
                    on:click={() => move(i, 1)}
                    disabled={disabled || i === chain.length - 1}
                    aria-label={`Bajar a ${u.username} a la posición ${i + 2}`}
                  >
                    <i class="fas fa-arrow-down" aria-hidden="true"></i>
                  </button>
                {/if}
                <button
                  type="button"
                  class="op-userpicker__btn op-userpicker__btn--remove"
                  on:click={() => remove(u.id)}
                  {disabled}
                  aria-label={isOrdered
                    ? `Quitar a ${u.username} de la cadena`
                    : `Quitar a ${u.username}`}
                >
                  <i class="fas fa-times" aria-hidden="true"></i>
                </button>
              </span>
            </li>
          {/each}
        </svelte:element>
      {/if}
    </div>
  </div>

  <!--
    La ayuda del padre gana. Antes, al llegar al máximo, «Máximo N revisores»
    SUSTITUÍA al `hint`: en un selector de uno solo eso significaba que elegir al
    responsable borraba de la pantalla la única frase que explicaba qué era ese
    campo — y la sustituía por un plural agramatical. El aviso de tope solo tiene
    sentido cuando caben varios y no hay nada mejor que decir.
  -->
  {#if hint && !error}
    <p id={hintId} class="op-userpicker__hint">{hint}</p>
  {:else if atMax && isOrdered}
    <p id={hintId} class="op-userpicker__hint">Máximo {max} {nounPlural}.</p>
  {/if}

  <p id={errorId} class="op-userpicker__error" aria-live="polite" aria-atomic="true">
    {error || ""}
  </p>

  <!-- Live region: estado de la cadena para lectores de pantalla -->
  <span class="sr-only" aria-live="polite" aria-atomic="true">{liveMessage}</span>
</div>

<style lang="scss">
  .op-userpicker {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-2);

    &__label {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
    }

    &__search {
      position: relative;
    }

    &__search-icon {
      position: absolute;
      left: var(--op-space-3);
      top: 50%;
      transform: translateY(-50%);
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      pointer-events: none;
    }

    &__search-input {
      font-family: var(--op-font-family);
      font-size: var(--op-font-sm);
      color: var(--op-text);
      background: var(--op-surface);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      height: 36px;
      width: 100%;
      padding: 0 var(--op-space-3) 0 var(--op-space-6);
      outline: none;
      transition:
        border-color var(--op-transition),
        box-shadow var(--op-transition);

      &:focus {
        border-color: var(--op-primary);
        box-shadow: 0 0 0 3px var(--op-focus-ring);
      }

      &:disabled {
        background: var(--op-bg);
        color: var(--op-text-secondary);
        cursor: not-allowed;
      }
    }

    &__panels {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: var(--op-space-3);

      @media (max-width: 640px) {
        grid-template-columns: 1fr;
      }
    }

    &__panel {
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      background: var(--op-surface);
      padding: var(--op-space-2);
      min-height: 120px;
    }

    &__panel-title {
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-bold);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--op-text-secondary);
      margin: 0 0 var(--op-space-2);
    }

    &__empty {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin: var(--op-space-2) 0;
    }

    &__list {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
      max-height: 220px;
      overflow-y: auto;
    }

    &__item {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: var(--op-space-1) var(--op-space-2);
      border-radius: var(--op-radius);
      background: var(--op-bg);
    }

    &__order {
      flex: 0 0 auto;
      width: 22px;
      height: 22px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-bold);
      color: var(--op-on-primary);
      background: var(--op-primary-dark);
      border-radius: 50%;
    }

    &__user {
      display: flex;
      flex-direction: column;
      min-width: 0;
      flex: 1 1 auto;
    }

    &__username {
      font-size: var(--op-font-sm);
      color: var(--op-text);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    &__email {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    &__actions {
      display: flex;
      gap: var(--op-space-1);
      flex: 0 0 auto;
    }

    &__btn {
      position: relative;
      width: 28px;
      height: 28px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      background: var(--op-surface);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      cursor: pointer;
      transition:
        background var(--op-transition),
        color var(--op-transition),
        border-color var(--op-transition);

      // Objetivo táctil >=44px (v1.0 A11y) sin alterar el tamaño visual.
      &::after {
        content: "";
        position: absolute;
        inset: -8px;
      }

      &:hover:not(:disabled) {
        border-color: var(--op-primary);
        color: var(--op-primary);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
        box-shadow: 0 0 0 3px var(--op-focus-ring);
      }

      &:disabled {
        opacity: 0.5;
        cursor: not-allowed;
      }

      &--add:hover:not(:disabled) {
        border-color: var(--op-success, var(--op-primary));
        color: var(--op-success, var(--op-primary));
      }

      &--remove:hover:not(:disabled) {
        border-color: var(--op-error);
        color: var(--op-error);
      }
    }

    &__hint {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      margin: 0;
    }

    &__error {
      font-size: var(--op-font-xs);
      color: var(--op-error);
      margin: 0;
    }

    &--error {
      .op-userpicker__panel {
        border-color: var(--op-error);
      }
    }
  }
</style>
