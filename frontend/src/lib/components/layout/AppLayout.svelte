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
  import SidebarNav from "./SidebarNav.svelte";
  import BreadcrumbBar from "./BreadcrumbBar.svelte";
  import AssistantDock from "./AssistantDock.svelte";
  import {
    sidebarCollapsed,
    sidebarMobileOpen,
    toggleSidebar,
    toggleMobileSidebar,
    closeMobileSidebar,
  } from "$lib/stores/ui.js";
  import { sessionUser } from "$lib/stores/auth.js";

  /** Título de la página actual (usado en el topbar y <title>) */
  export let pageTitle = "";

  // Dock del asistente
  let assistantOpen = false;

  function toggleAssistant() {
    assistantOpen = !assistantOpen;
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

  // Gestión de foco al abrir/cerrar el drawer
  $: if ($sidebarMobileOpen) {
    drawerPreviousFocus = document.activeElement;
    tick().then(() => {
      if (drawerEl) {
        const first = /** @type {HTMLElement|null} */ (drawerEl.querySelector(FOCUSABLE));
        first?.focus();
      }
    });
  } else if (drawerPreviousFocus) {
    tick().then(() => {
      /** @type {HTMLElement} */ (drawerPreviousFocus).focus?.();
      drawerPreviousFocus = null;
    });
  }

  // El listener se registra solo en cliente. La limpieza va como retorno de
  // onMount (no onDestroy): onDestroy SÍ se ejecuta en SSR y `document` no
  // existe allí, lo que rompía el render del layout (500 en rutas (app)).
  onMount(() => {
    document.addEventListener("keydown", trapDrawerFocus);
    return () => document.removeEventListener("keydown", trapDrawerFocus);
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

    <!-- Espacio flexible -->
    <div class="app-layout__topbar-spacer" aria-hidden="true"></div>

    <!-- Acciones derechas del topbar -->
    <div class="app-layout__topbar-actions">
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

      <!-- Logout -->
      <a
        href="/logout"
        class="app-layout__action-btn"
        aria-label="Cerrar sesión"
        title="Cerrar sesión"
      >
        <i class="fas fa-sign-out-alt" aria-hidden="true"></i>
      </a>
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

    &__menu-btn,
    &__collapse-btn,
    &__action-btn {
      background: none;
      border: none;
      cursor: pointer;
      color: var(--op-text-secondary);
      width: 36px;
      height: 36px;
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
  }

  @media screen and (min-width: 769px) {
    .is-hidden-tablet {
      display: none !important;
    }
  }
</style>
