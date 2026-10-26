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
  SidebarNav.svelte — Barra de navegación lateral del AppLayout.

  - Renderiza solo los ítems autorizados por los permisos del JWT (can/hasRole).
  - En modo colapsado muestra solo íconos; el label se sirve como aria-label y tooltip.
  - Responde a $sidebarCollapsed del store ui.js.
  - Navegación completamente accesible por teclado (rol navigation, aria-current).
  - Sección "Favoritos" (Sistema de Diseño §10, módulo 04): accesos fijados por el
    usuario, persistidos por tenant+usuario. SIEMPRE se derivan de `visibleItems`
    (ya permission-filtrado) — un favorito nunca puede mostrar un destino para el
    que el usuario perdió el permiso entre sesiones (ver favorites.js).
-->
<script>
  import { page } from "$app/stores";
  import { browser } from "$app/environment";
  import {
    permissions,
    permissionLevels,
    roles,
    sessionUser,
    tenantSlug,
  } from "$lib/stores/auth.js";
  import { sidebarCollapsed, closeMobileSidebar } from "$lib/stores/ui.js";
  import { ROUTES } from "$lib/utils/config.js";
  import { NAV_ITEMS, filterVisibleNavItems } from "$lib/utils/navItems.js";
  import {
    favoriteHrefs,
    initFavorites,
    toggleFavorite,
    pickFavoriteNavItems,
  } from "$lib/stores/favorites.js";

  /** Filtra los ítems según los permisos/roles del usuario actual */
  $: visibleItems = filterVisibleNavItems(NAV_ITEMS, $permissions, $roles, $permissionLevels);

  // ── Favoritos (§10 módulo 04) ─────────────────────────────────────────────
  // Reinicializa la persistencia de favoritos cada vez que cambia el tenant o
  // el usuario de la sesión activa (login/logout dentro del mismo navegador).
  // Guard `browser` (D-16): en SSR no hay localStorage y `initFavorites` no
  // debe ejecutarse — el store queda vacío hasta que el cliente hidrata.
  //
  // DEBE declararse ANTES del bloque reactivo de `favoriteItems`: ambos son
  // estatutos reactivos sin dependencia de dato explícita entre sí (ninguno
  // asigna una variable que el otro lea como store), así que Svelte los
  // ejecuta en orden de declaración en el primer render. Si `favoriteItems`
  // se calculara antes, el primer render de un componente nuevo (p. ej. tras
  // cambiar de usuario) leería el `$favoriteHrefs` TODAVÍA con los datos del
  // usuario anterior — el store es un singleton de módulo y solo lo corrige
  // esta reinicialización.
  let favoritesInitKey = null;
  $: if (browser) {
    const nextKey = `${$tenantSlug ?? ""}:${$sessionUser?.sub ?? ""}`;
    if (nextKey !== favoritesInitKey) {
      favoritesInitKey = nextKey;
      initFavorites($tenantSlug, $sessionUser?.sub);
    }
  }

  // `pickFavoriteNavItems` recibe SIEMPRE `visibleItems` (ya permission-
  // filtrado más arriba) — nunca la lista cruda NAV_ITEMS. Así un favorito
  // fijado con un permiso que el usuario ya perdió deja de listarse solo.
  $: favoriteItems = pickFavoriteNavItems(visibleItems, $favoriteHrefs);

  /** Determina si una ruta está activa */
  function isActive(href) {
    if (href === ROUTES.dashboard) return $page.url.pathname === href;
    return $page.url.pathname.startsWith(href);
  }

  /** Al hacer clic en un enlace en móvil, cerrar el drawer */
  function handleLinkClick() {
    closeMobileSidebar();
  }

  /**
   * Fija/desfija un ítem sin navegar (el botón es hermano del <a>, no hace
   * falta preventDefault, pero sí evitar que el click "burbujee" a nada más).
   * @param {MouseEvent} event
   * @param {string} href
   */
  function handleToggleFavorite(event, href) {
    event.stopPropagation();
    toggleFavorite(href);
  }
</script>

<nav
  class="sidebar-nav"
  class:sidebar-nav--collapsed={$sidebarCollapsed}
  aria-label="Navegación principal"
>
  <!-- Logo / marca -->
  <div class="sidebar-nav__brand" aria-hidden="true">
    <!--
      El isotipo institucional va como imagen; el nombre va como TEXTO y no como
      parte del mapa de bits. Así la marca hereda la tipografía y el color de los
      tokens (`--op-*`), se mantiene nítida en cualquier densidad de pantalla y
      sigue siendo legible cuando el usuario amplía el navegador — cosas que un
      logotipo rasterizado con el nombre incrustado no puede dar.
    -->
    <img src="/brand/orpyca-mark.svg" alt="" class="sidebar-nav__logo-icon" />
    {#if !$sidebarCollapsed}
      <span class="sidebar-nav__logo-text">OrpycaMCP</span>
    {/if}
  </div>

  <!-- Favoritos: accesos configurables por el usuario (§10 módulo 04) -->
  {#if favoriteItems.length}
    <div class="sidebar-nav__section">
      {#if !$sidebarCollapsed}
        <p class="sidebar-nav__section-title" id="sidebar-nav-favorites-heading">Favoritos</p>
      {/if}
      <ul
        class="sidebar-nav__list"
        role="list"
        aria-labelledby={$sidebarCollapsed ? undefined : "sidebar-nav-favorites-heading"}
        aria-label={$sidebarCollapsed ? "Favoritos" : undefined}
      >
        {#each favoriteItems as item (item.href)}
          {@const active = isActive(item.href)}
          <li class="sidebar-nav__item">
            <a
              href={item.href}
              class="sidebar-nav__link"
              class:sidebar-nav__link--active={active}
              aria-current={active ? "page" : undefined}
              aria-label={$sidebarCollapsed ? item.label : undefined}
              title={$sidebarCollapsed ? item.label : undefined}
              on:click={handleLinkClick}
            >
              <i class="{item.icon} sidebar-nav__icon" aria-hidden="true"></i>
              {#if !$sidebarCollapsed}
                <span class="sidebar-nav__label">{item.label}</span>
              {/if}
            </a>
            {#if !$sidebarCollapsed}
              <button
                type="button"
                class="sidebar-nav__pin-btn"
                aria-pressed="true"
                aria-label={`Quitar ${item.label} de favoritos`}
                title={`Quitar ${item.label} de favoritos`}
                on:click={(e) => handleToggleFavorite(e, item.href)}
              >
                <i class="fas fa-star" aria-hidden="true"></i>
              </button>
            {/if}
          </li>
        {/each}
      </ul>
    </div>
  {/if}

  <!-- Lista de ítems -->
  <ul class="sidebar-nav__list" role="list">
    {#each visibleItems as item (item.href)}
      {@const active = isActive(item.href)}
      {@const isFav = $favoriteHrefs.includes(item.href)}
      <li class="sidebar-nav__item">
        <a
          href={item.href}
          class="sidebar-nav__link"
          class:sidebar-nav__link--active={active}
          aria-current={active ? "page" : undefined}
          aria-label={$sidebarCollapsed ? item.label : undefined}
          title={$sidebarCollapsed ? item.label : undefined}
          on:click={handleLinkClick}
        >
          <i class="{item.icon} sidebar-nav__icon" aria-hidden="true"></i>
          {#if !$sidebarCollapsed}
            <span class="sidebar-nav__label">{item.label}</span>
          {/if}
        </a>
        {#if !$sidebarCollapsed}
          <button
            type="button"
            class="sidebar-nav__pin-btn"
            class:sidebar-nav__pin-btn--active={isFav}
            aria-pressed={isFav}
            aria-label={isFav
              ? `Quitar ${item.label} de favoritos`
              : `Fijar ${item.label} a favoritos`}
            title={isFav ? `Quitar ${item.label} de favoritos` : `Fijar ${item.label} a favoritos`}
            on:click={(e) => handleToggleFavorite(e, item.href)}
          >
            <i class="fa-star" class:fas={isFav} class:far={!isFav} aria-hidden="true"></i>
          </button>
        {/if}
      </li>
    {/each}
  </ul>
</nav>

<style lang="scss">
  @use "$styles/_tokens" as t;

  .sidebar-nav {
    display: flex;
    flex-direction: column;
    width: var(--op-sidebar-width);
    height: 100%;
    background: var(--op-surface);
    border-right: 1px solid var(--op-border);
    transition: width var(--op-transition);
    overflow: hidden;

    &--collapsed {
      width: var(--op-sidebar-collapsed-width);
    }

    // Marca / logo
    &__brand {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
      padding: var(--op-space-4) var(--op-space-3);
      height: var(--op-topbar-height);
      border-bottom: 1px solid var(--op-border);
      flex-shrink: 0;
    }

    &__logo-icon {
      display: block;
      width: 30px;
      height: 30px;
      // El activo ya viene cuadrado y con transparencia: no lleva fondo ni
      // recorte propios, se apoya sobre la superficie del panel.
      object-fit: contain;
      flex-shrink: 0;
    }

    &__logo-text {
      font-size: var(--op-font-md);
      font-weight: var(--op-weight-bold);
      color: var(--op-primary-dark);
      white-space: nowrap;
    }

    // Sección (Favoritos)
    &__section {
      flex-shrink: 0;
      border-bottom: 1px solid var(--op-border);
      padding-bottom: var(--op-space-1);

      .sidebar-nav__list {
        flex: none;
        overflow-y: visible;
      }
    }

    &__section-title {
      margin: 0;
      padding: var(--op-space-2) var(--op-space-4) var(--op-space-1);
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-bold);
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: var(--op-text-secondary);
      white-space: nowrap;
    }

    // Lista de navegación
    &__list {
      list-style: none;
      margin: 0;
      padding: var(--op-space-2) 0;
      flex: 1;
      overflow-y: auto;
    }

    &__item {
      display: flex;
      align-items: center;
      margin: 1px var(--op-space-2);
    }

    &__link {
      display: flex;
      align-items: center;
      gap: var(--op-space-3);
      padding: var(--op-space-2) var(--op-space-3);
      border-radius: var(--op-radius);
      color: var(--op-text-secondary);
      text-decoration: none;
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
      transition:
        background var(--op-transition),
        color var(--op-transition);
      white-space: nowrap;
      flex: 1 1 auto;
      min-width: 0;

      &:hover {
        background: var(--op-primary-light);
        color: var(--op-primary-dark);
      }

      &--active {
        background: var(--op-primary-light);
        color: var(--op-primary-dark);
        font-weight: var(--op-weight-bold);

        .sidebar-nav__icon {
          color: var(--op-primary);
        }
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 1px;
      }
    }

    &__icon {
      width: 16px;
      text-align: center;
      font-size: var(--op-font-base);
      flex-shrink: 0;
      color: inherit;
    }

    &__label {
      overflow: hidden;
      text-overflow: ellipsis;
    }

    // Botón fijar/desfijar de favoritos: objetivo táctil >=44px real (el
    // icono visual es más pequeño, pero el área clicable cumple AA).
    &__pin-btn {
      flex-shrink: 0;
      width: 44px;
      height: 44px;
      display: flex;
      align-items: center;
      justify-content: center;
      background: none;
      border: none;
      cursor: pointer;
      border-radius: var(--op-radius-sm);
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
      transition: color var(--op-transition);

      &:hover {
        color: var(--op-primary-dark);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 1px;
      }

      &--active {
        color: var(--op-primary-dark);
      }
    }
  }
</style>
