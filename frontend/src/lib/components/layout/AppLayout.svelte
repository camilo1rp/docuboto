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
  AppLayout.svelte — Plantilla única de todas las vistas privadas (RF-UI-01).

  Estructura:
  ┌─────────────────────────────────────────────┐
  │  topbar (52px fijo)                         │
  ├──────────┬──────────────────────────────────┤
  │          │  breadcrumb (36px)               │
  │ sidebar  ├──────────────────────────────────┤
  │ (220px / │  <slot> — contenido de la página │
  │  52px    │                                  │
  │ colaps.) │                                  │
  └──────────┴──────────────────────────────────┘
  + AssistantDock (panel flotante derecho, Fase 3)
  + Overlay drawer en móvil

  Responsivo:
  - ≤768px: el sidebar se oculta; el botón de menú abre un drawer overlay.
  - El dock del asistente ocupa toda la pantalla en móvil (<600px).
-->
<script>
  import { onMount, tick } from "svelte";
  import { goto } from "$app/navigation";
  import SidebarNav from "./SidebarNav.svelte";
  import BreadcrumbBar from "./BreadcrumbBar.svelte";
  import AssistantDock from "./AssistantDock.svelte";
  import DependencySwitcher from "$lib/components/features/perfil/DependencySwitcher.svelte";
  import {
    sidebarCollapsed,
    sidebarMobileOpen,
    toggleSidebar,
    toggleMobileSidebar,
    closeMobileSidebar,
  } from "$lib/stores/ui.js";
  import { sessionUser } from "$lib/stores/auth.js";
  import { ROUTES } from "$lib/utils/config.js";

  /** Título de la página actual (usado en el topbar y <title>) */
  export let pageTitle = "";

  // Dock del asistente
  let assistantOpen = false;

  function toggleAssistant() {
    assistantOpen = !assistantOpen;
  }

  // ── Búsqueda global (§9: "barra prominente y siempre accesible") ─────────
  // No llama al gateway desde aquí: navega a /busqueda?q=..., que ya
  // implementa la carga SSR real (busqueda/+page.server.js lee `q` de la URL).
  let searchQuery = "";
  /** @type {HTMLInputElement} */
  let searchInputEl;
  // En móvil el campo empieza oculto para no ahogar el topbar; se revela con
  // el botón de lupa. En desktop el campo siempre está visible (§9).
  let searchExpandedMobile = false;

  function submitSearch() {
    const trimmed = searchQuery.trim();
    const target = trimmed
      ? `${ROUTES.busqueda}?q=${encodeURIComponent(trimmed)}`
      : ROUTES.busqueda;
    goto(target);
    searchExpandedMobile = false;
  }

  function toggleSearchMobile() {
    searchExpandedMobile = !searchExpandedMobile;
    if (searchExpandedMobile) {
      tick().then(() => searchInputEl?.focus());
    }
  }

  /**
   * Atajo "/" para enfocar la búsqueda global, salvo que el usuario ya esté
   * escribiendo en otro campo (input/textarea/contenteditable) — nunca debe
   * secuestrar la escritura de un formulario en curso.
   * @param {KeyboardEvent} e
   */
  function handleSearchShortcut(e) {
    if (e.key !== "/" || e.defaultPrevented) return;
    const target = /** @type {HTMLElement|null} */ (e.target);
    const isTyping =
      !!target &&
      (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.isContentEditable);
    if (isTyping) return;

    e.preventDefault();
    searchExpandedMobile = true;
    tick().then(() => searchInputEl?.focus());
  }

  // ── Trampa de foco + ESC para el drawer de navegación móvil ──────────────
  let drawerEl;
  /** @type {Element|null} */
  let drawerPreviousFocus = null;

  const FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function trapDrawerFocus(e) {
    if (!$sidebarMobileOpen || !drawerEl) return;
    const focusable = Array.from(drawerEl.querySelectorAll(FOCUSABLE));
    if (!focusable.length) return;
    const first = /** @type {HTMLElement} */ (focusable[0]);
    const last = /** @type {HTMLElement} */ (focusable[focusable.length - 1]);

    if (e.key === "Tab") {
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
    if (e.key === "Escape") {
      closeMobileSidebar();
    }
  }

  // Gestión de foco al abrir/cerrar el drawer.
  // Guard `typeof document !== "undefined"` (D-16): este bloque reactivo corre
  // también durante el render SSR, donde `document` no existe — sin el guard,
  // un $sidebarMobileOpen=true en el primer render del servidor rompería con
  // ReferenceError toda ruta que monte AppLayout.
  $: if (typeof document !== "undefined" && $sidebarMobileOpen) {
    drawerPreviousFocus = document.activeElement;
    tick().then(() => {
      if (drawerEl) {
        const first = /** @type {HTMLElement|null} */ (drawerEl.querySelector(FOCUSABLE));
        first?.focus();
      }
    });
  } else if (drawerPreviousFocus) {
    // Copia local + limpieza inmediata (no dentro del `.then`): evita que un
    // cierre/apertura rápidos del drawer móvil pisen la referencia en vuelo
    // de un `tick()` anterior o la dejen en null antes de tiempo.
    const elementToRefocus = /** @type {HTMLElement} */ (drawerPreviousFocus);
    drawerPreviousFocus = null;
    tick().then(() => {
      elementToRefocus?.focus?.();
    });
  }

  // El listener se registra solo en cliente. La limpieza va como retorno de
  // onMount (no onDestroy): onDestroy SÍ se ejecuta en SSR y `document` no
  // existe allí, lo que rompía el render del layout (500 en rutas (app)).
  onMount(() => {
    document.addEventListener("keydown", trapDrawerFocus);
    document.addEventListener("keydown", handleSearchShortcut);
    return () => {
      document.removeEventListener("keydown", trapDrawerFocus);
      document.removeEventListener("keydown", handleSearchShortcut);
    };
  });
</script>

<svelte:head>
  {#if pageTitle}
    <title>{pageTitle} — OrpycaMCP</title>
  {/if}
</svelte:head>

<!-- Skip-to-content para lectores de pantalla y navegación por teclado -->
<a href="#main-content" class="skip-link">Ir al contenido principal</a>

<div class="app-layout">
  <!-- ── Topbar ──────────────────────────────────────────────── -->
  <header class="app-layout__topbar">
    <!-- Botón de menú (visible solo en móvil) -->
    <button
      class="app-layout__menu-btn is-hidden-tablet"
      type="button"
      aria-label="Abrir menú de navegación"
      aria-expanded={$sidebarMobileOpen}
      aria-controls="sidebar-drawer"
      on:click={toggleMobileSidebar}
    >
      <i class="fas fa-bars" aria-hidden="true"></i>
    </button>

    <!--
      Marca en móvil: en escritorio el isotipo ya preside el panel lateral, pero
      en móvil ese panel es un cajón cerrado y la aplicación se quedaba sin
      ninguna señal de marca. Se oculta al desplegar la búsqueda, igual que las
      acciones de la derecha, para no ahogar el topbar.
    -->
    <img
      src="/brand/orpyca-mark.png"
      alt=""
      class="app-layout__topbar-mark is-hidden-tablet"
      class:is-hidden-mobile={searchExpandedMobile}
    />

    <!-- Botón colapsar sidebar (solo en desktop) -->
    <button
      class="app-layout__collapse-btn is-hidden-mobile"
      type="button"
      aria-label={$sidebarCollapsed ? "Expandir menú lateral" : "Contraer menú lateral"}
      aria-pressed={$sidebarCollapsed}
      on:click={toggleSidebar}
    >
      <i
        class="fas"
        class:fa-angle-double-left={!$sidebarCollapsed}
        class:fa-angle-double-right={$sidebarCollapsed}
        aria-hidden="true"
      ></i>
    </button>

    <!-- Búsqueda global (§9): entrada principal al sistema, siempre accesible -->
    <form
      class="app-layout__search"
      class:app-layout__search--expanded={searchExpandedMobile}
      role="search"
      aria-label="Búsqueda global de radicados y expedientes"
      on:submit|preventDefault={submitSearch}
    >
      <label for="app-layout-search-input" class="sr-only">
        Buscar radicados, expedientes o dependencias
      </label>
      <i class="fas fa-search app-layout__search-icon" aria-hidden="true"></i>
      <input
        id="app-layout-search-input"
        bind:this={searchInputEl}
        bind:value={searchQuery}
        type="search"
        class="app-layout__search-input"
        placeholder="Buscar radicados, expedientes… (/)"
        autocomplete="off"
      />
    </form>

    <!-- Alternador de búsqueda en móvil: revela el campo sin ahogar el topbar -->
    <button
      class="app-layout__action-btn app-layout__search-toggle is-hidden-tablet"
      type="button"
      aria-label={searchExpandedMobile ? "Cerrar búsqueda" : "Abrir búsqueda"}
      aria-expanded={searchExpandedMobile}
      aria-controls="app-layout-search-input"
      on:click={toggleSearchMobile}
    >
      <i class="fas {searchExpandedMobile ? 'fa-xmark' : 'fa-search'}" aria-hidden="true"></i>
    </button>

    <!-- Espacio flexible -->
    <div class="app-layout__topbar-spacer" aria-hidden="true"></div>

    <!-- Acciones derechas del topbar -->
    <div class="app-layout__topbar-actions" class:is-hidden-mobile={searchExpandedMobile}>
      <!-- Botón asistente -->
      <button
        class="app-layout__action-btn"
        type="button"
        aria-label="Abrir asistente conversacional"
        aria-pressed={assistantOpen}
        on:click={toggleAssistant}
      >
        <i class="fas fa-robot" aria-hidden="true"></i>
      </button>

      <!-- Conmutador de dependencia activa (E08/ADR-013) — visible en
           cualquier pantalla; oculto si el usuario solo tiene una
           dependencia o si el contexto no cargó (best-effort, ver
           (app)/+layout.server.js). -->
      {#if $sessionUser?.context}
        <DependencySwitcher
          compact
          activeDepeId={$sessionUser.context.active_depe_id}
          availableDepts={$sessionUser.context.available_depts ?? []}
          dependencias={$sessionUser.dependencias ?? []}
        />
      {/if}

      <!-- Avatar / nombre del usuario -->
      {#if $sessionUser}
        <div class="app-layout__user" aria-label="Usuario activo">
          <span class="app-layout__user-avatar" aria-hidden="true">
            {($sessionUser.name ?? $sessionUser.preferred_username ?? "?").charAt(0).toUpperCase()}
          </span>
          <span class="app-layout__user-name is-hidden-mobile">
            {$sessionUser.name ?? $sessionUser.preferred_username ?? "Usuario"}
          </span>
        </div>
      {/if}

      <!--
        Logout — formulario POST, NO un enlace.

        Era `<a href="/logout">` y el `+page.server.js` de esa ruta hacía el
        cierre de sesión dentro del `load`. Con `data-sveltekit-preload-data="hover"`
        activo en `app.html`, SvelteKit precarga el `load` de un enlace **al pasar
        el ratón por encima**: rozar este icono cerraba la sesión sin un solo
        clic. Y como el efecto vivía en un GET, cualquier cosa que "visite" la
        URL —un prefetch del navegador, un escáner, una imagen remota apuntando
        ahí— la dispara igual.

        Un GET no debe tener efectos. El cierre de sesión vive ahora en la
        `action` POST, que además pasa por la comprobación de origen (CSRF) de
        SvelteKit. El formulario funciona sin JavaScript.
      -->
      <form method="POST" action="/logout" class="app-layout__logout-form">
        <button
          type="submit"
          class="app-layout__action-btn"
          aria-label="Cerrar sesión"
          title="Cerrar sesión"
        >
          <i class="fas fa-sign-out-alt" aria-hidden="true"></i>
        </button>
      </form>
    </div>
  </header>

  <!-- ── Cuerpo (sidebar + contenido) ──────────────────────── -->
  <div class="app-layout__body">
    <!-- Sidebar: desktop (siempre visible, colapsa) -->
    <aside class="app-layout__sidebar is-hidden-mobile" aria-label="Menú de navegación">
      <SidebarNav />
    </aside>

    <!-- Sidebar: móvil — drawer overlay -->
    {#if $sidebarMobileOpen}
      <div
        class="app-layout__drawer-overlay"
        role="presentation"
        on:click={closeMobileSidebar}
        on:keydown={() => {}}
      ></div>

      <aside
        id="sidebar-drawer"
        bind:this={drawerEl}
        class="app-layout__drawer is-hidden-tablet"
        role="dialog"
        aria-modal="true"
        aria-label="Menú de navegación"
      >
        <SidebarNav />
      </aside>
    {/if}

    <!-- Área principal: breadcrumb + slot -->
    <div class="app-layout__main-wrapper">
      <BreadcrumbBar />

      <!--
        Aviso de permisos degradados.

        Cuando `GET /auth/me` no responde, la sesión sigue viva pero sin
        permisos granulares: el menú se queda vacío y las acciones desaparecen
        de todas las pantallas, porque la UI por rol se calcula con
        `permissions`. Sin este aviso es indistinguible de una revocación de
        permisos, y el funcionario acaba llamando al administrador — que ve sus
        permisos intactos y no encuentra nada que arreglar.

        No es una alerta de seguridad: nada se ha franqueado, el RBAC real vive
        en el backend. Es una explicación.
      -->
      {#if $sessionUser?.permisosDegradados}
        <div class="app-layout__degradado" role="status">
          <i class="fas fa-triangle-exclamation" aria-hidden="true"></i>
          <span>
            No pudimos cargar tus permisos: el menú y las acciones aparecen incompletos. No es un
            cambio en tu cuenta — recarga la página en unos minutos o avisa a soporte si sigue así.
          </span>
        </div>
      {/if}

      <main id="main-content" class="app-layout__content" tabindex="-1">
        <slot />
      </main>
    </div>
  </div>
</div>

<!-- Dock del asistente (Fase 3 — placeholder por ahora) -->
<AssistantDock open={assistantOpen} onClose={() => (assistantOpen = false)} />

<style lang="scss">
  @use "$styles/_tokens" as t;

  .app-layout {
    display: flex;
    flex-direction: column;
    min-height: 100vh;
    background: var(--op-bg);

    // ── Topbar ──
    &__topbar {
      position: sticky;
      top: 0;
      z-index: 200;
      display: flex;
      align-items: center;
      height: var(--op-topbar-height);
      padding: 0 var(--op-space-4);
      background: var(--op-surface);
      border-bottom: 1px solid var(--op-border);
      box-shadow: var(--op-shadow-sm);
      gap: var(--op-space-2);
    }

    &__topbar-mark {
      display: block;
      width: 28px;
      height: 28px;
      object-fit: contain;
      flex-shrink: 0;
    }

    // El logout es un `<form>` para que su efecto viva en un POST; el
    // contenedor no debe alterar la alineación del topbar.
    &__logout-form {
      display: flex;
      margin: 0;
    }

    &__menu-btn,
    &__collapse-btn,
    &__action-btn {
      background: none;
      border: none;
      cursor: pointer;
      color: var(--op-text-secondary);
      // Objetivo táctil >=44px (v1.0 A11y) — hamburguesa (única entrada a la
      // navegación en móvil), asistente y logout son controles independientes.
      width: 44px;
      height: 44px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: var(--op-radius);
      font-size: var(--op-font-base);
      flex-shrink: 0;
      text-decoration: none;

      &:hover {
        background: var(--op-bg);
        color: var(--op-primary);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }

    // ── Búsqueda global (§9) ──
    &__search {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      flex: 1 1 420px;
      max-width: 420px;
      height: 36px;
      padding: 0 var(--op-space-3);
      background: var(--op-bg);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius);
      transition: border-color var(--op-transition);

      &:focus-within {
        border-color: var(--op-primary);
        box-shadow: 0 0 0 2px var(--op-primary-light);
      }
    }

    &__search-icon {
      flex-shrink: 0;
      color: var(--op-text-secondary);
      font-size: var(--op-font-sm);
    }

    &__search-input {
      flex: 1;
      min-width: 0;
      border: none;
      background: none;
      font-family: var(--op-font-family);
      font-size: var(--op-font-sm);
      color: var(--op-text);

      &:focus {
        outline: none;
      }

      &::placeholder {
        color: var(--op-text-secondary);
      }

      // Oculta el botón nativo "x" de <input type=search> en WebKit — look
      // consistente entre navegadores (el borrado se hace con Backspace).
      &::-webkit-search-cancel-button {
        -webkit-appearance: none;
      }
    }

    &__topbar-spacer {
      flex: 1;
    }

    &__topbar-actions {
      display: flex;
      align-items: center;
      gap: var(--op-space-1);
    }

    &__user {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      padding: 0 var(--op-space-2);
    }

    &__user-avatar {
      width: 30px;
      height: 30px;
      border-radius: 50%;
      background: var(--op-primary-light);
      color: var(--op-primary-dark);
      display: flex;
      align-items: center;
      justify-content: center;
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-bold);
      flex-shrink: 0;
    }

    &__user-name {
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
      color: var(--op-text);
      max-width: 140px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    // ── Body ──
    &__body {
      display: flex;
      flex: 1;
      min-height: 0;
    }

    // ── Sidebar desktop ──
    &__sidebar {
      flex-shrink: 0;
      height: calc(100vh - var(--op-topbar-height));
      position: sticky;
      top: var(--op-topbar-height);
      overflow-y: auto;
    }

    // ── Drawer móvil ──
    &__drawer-overlay {
      position: fixed;
      inset: 0;
      background: rgba(33, 36, 33, 0.4);
      z-index: 150;
    }

    &__drawer {
      position: fixed;
      top: 0;
      left: 0;
      height: 100vh;
      z-index: 151;
      box-shadow: var(--op-shadow-lg);
    }

    // ── Main wrapper ──
    &__main-wrapper {
      flex: 1;
      display: flex;
      flex-direction: column;
      min-width: 0;
    }

    &__degradado {
      display: flex;
      align-items: flex-start;
      gap: var(--op-space-2);
      margin: var(--op-space-3) var(--op-space-5) 0;
      padding: var(--op-space-3);
      border: 1px solid var(--op-warning);
      border-radius: var(--op-radius);
      background: var(--op-surface);
      color: var(--op-text);
      font-size: var(--op-font-sm);
      line-height: var(--op-line-height);

      i {
        color: var(--op-warning);
        margin-top: 2px;
        flex-shrink: 0;
      }
    }

    &__content {
      flex: 1;
      padding: var(--op-space-5);
      overflow-y: auto;

      // Sin outline visible al recibir foco programático
      &:focus {
        outline: none;
      }
    }
  }

  // Helpers Bulma para responsive (is-hidden-*)
  // Bulma usa 769px para tablet breakpoint
  @media screen and (max-width: 768px) {
    .is-hidden-mobile {
      display: none !important;
    }

    // Búsqueda global: oculta por defecto en móvil (no ahoga el topbar),
    // se revela con el botón de lupa y ocupa el espacio disponible.
    .app-layout__search {
      display: none;
      flex: 1 1 auto;
      max-width: none;
    }

    .app-layout__search--expanded {
      display: flex;
    }
  }

  @media screen and (min-width: 769px) {
    .is-hidden-tablet {
      display: none !important;
    }
  }
</style>
