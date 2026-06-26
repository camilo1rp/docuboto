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
  SidebarNav.svelte — Barra de navegación lateral del AppLayout.

  - Renderiza solo los ítems autorizados por los permisos del JWT (can/hasRole).
  - En modo colapsado muestra solo íconos; el label se sirve como aria-label y tooltip.
  - Responde a $sidebarCollapsed del store ui.js.
  - Navegación completamente accesible por teclado (rol navigation, aria-current).
-->
<script>
  import { page } from "$app/stores";
  import { permissions, roles } from "$lib/stores/auth.js";
  import { sidebarCollapsed, closeMobileSidebar } from "$lib/stores/ui.js";
  import { ROUTES } from "$lib/utils/config.js";

  /**
   * Definición de ítems de navegación.
   * - permission: se muestra si el usuario tiene ese permiso (string)
   * - role: se muestra si el usuario tiene ese rol (string)
   * - Si no hay permission ni role, el ítem es público (dashboard, etc.)
   */
  const NAV_ITEMS = [
    {
      label: "Panel principal",
      href: ROUTES.dashboard,
      icon: "fas fa-home",
    },
    {
      label: "Bandeja",
      href: ROUTES.bandeja,
      icon: "fas fa-inbox",
      permission: "VER_BANDEJA",
    },
    {
      label: "Radicar",
      href: ROUTES.radicar,
      icon: "fas fa-file-import",
      // La página permite RADICAR_ENTRADA || RADICAR_SALIDA || RADICAR_INTERNO (ADR-013)
      permissions: ["RADICAR_ENTRADA", "RADICAR_SALIDA", "RADICAR_INTERNO"],
    },
    {
      label: "Expedientes",
      href: ROUTES.expedientes,
      icon: "fas fa-folder-open",
      permission: "VER_EXPEDIENTES",
    },
    {
      label: "Búsqueda",
      href: ROUTES.busqueda,
      icon: "fas fa-search",
      permission: "BUSCAR_DOCUMENTOS",
    },
    {
      label: "Reportes",
      href: ROUTES.reportes,
      icon: "fas fa-chart-bar",
      permission: "VER_REPORTES",
    },
    {
      label: "Administración",
      href: ROUTES.admin,
      icon: "fas fa-cog",
      role: "tenant_admin",
    },
  ];

  /** Filtra los ítems según los permisos/roles del usuario actual */
  $: visibleItems = NAV_ITEMS.filter((item) => {
    if (item.permissions) return item.permissions.some((p) => $permissions.includes(p));
    if (item.permission) return $permissions.includes(item.permission);
    if (item.role) return $roles.includes(item.role);
    return true;
  });

  /** Determina si una ruta está activa */
  function isActive(href) {
    if (href === ROUTES.dashboard) return $page.url.pathname === href;
    return $page.url.pathname.startsWith(href);
  }

  /** Al hacer clic en un enlace en móvil, cerrar el drawer */
  function handleLinkClick() {
    closeMobileSidebar();
  }
</script>

<nav
  class="sidebar-nav"
  class:sidebar-nav--collapsed={$sidebarCollapsed}
  aria-label="Navegación principal"
>
  <!-- Logo / marca -->
  <div class="sidebar-nav__brand" aria-hidden="true">
    <span class="sidebar-nav__logo-icon">
      <i class="fas fa-archive"></i>
    </span>
    {#if !$sidebarCollapsed}
      <span class="sidebar-nav__logo-text">OrpycaMCP</span>
    {/if}
  </div>

  <!-- Lista de ítems -->
  <ul class="sidebar-nav__list" role="list">
    {#each visibleItems as item (item.href)}
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
      display: flex;
      align-items: center;
      justify-content: center;
      width: 28px;
      height: 28px;
      background: var(--op-primary);
      color: var(--op-surface);
      border-radius: var(--op-radius-sm);
      font-size: var(--op-font-sm);
      flex-shrink: 0;
    }

    &__logo-text {
      font-size: var(--op-font-md);
      font-weight: var(--op-weight-bold);
      color: var(--op-primary-dark);
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
  }
</style>
