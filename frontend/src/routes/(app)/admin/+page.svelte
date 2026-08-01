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
  /admin — Dashboard de administración (Fase 0, cierre de desfase API↔UI).

  `navItems.js` ya enlazaba aquí ("Administración"), pero la ruta no existía →
  404 real. Esta pantalla es el ÍNDICE de tarjetas por área de configuración;
  cada entidad se administra a SÍ MISMA (una tarjeta = una subruta propia),
  nunca desde aquí.

  I11 (revisión UX): el ítem de `navItems.js` gateaba por `role: "tenant_admin"`
  mientras esta pantalla siempre gateó cada tarjeta por `USUA_PERM_ADMIN` — dos
  ejes independientes (el rol de Keycloak no otorga el permiso automáticamente,
  no hay mapeo rol→permiso en auth-service). Unificado: `navItems.js` gatea
  ahora también por `USUA_PERM_ADMIN`, el mismo permiso que exigen de verdad
  todas las subrutas (`/admin/plantillas`, `/admin/flujos/reglas`,
  `/admin/flujos/reasignacion`).

  Tarjetas "Próximamente": las áreas que aún no tienen subruta implementada
  se muestran DESHABILITADAS (no navegables, sin href) en vez de omitirse —
  así el dashboard comunica el mapa completo de administración sin mentir
  sobre lo que funciona hoy, y sin crear más enlaces muertos (el problema que
  esta pantalla resuelve). La distinción no depende solo del color: llevan
  un badge de texto "Próximamente" + icono de candado + `aria-disabled`.

  RBAC (espejo del backend): cada tarjeta declara su propio permiso; se
  filtran con la MISMA `filterVisibleNavItems` que usa SidebarNav/Dashboard
  (D-05/D-07) para que un administrador solo vea lo que su permiso autoriza.
  Si no tiene ningún permiso de administración, se muestra un EmptyState
  (no una página vacía).
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import { permissions, roles } from "$lib/stores/auth.js";
  import { filterVisibleNavItems } from "$lib/utils/navItems.js";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";

  /**
   * Catálogo de áreas de administración. `implemented: false` = subruta de
   * una fase posterior del plan de cierre del desfase API↔UI: la tarjeta se
   * muestra como mapa (no como enlace) hasta que exista.
   *
   * Todas comparten hoy `USUA_PERM_ADMIN` (único permiso de administración
   * general vigente en el catálogo RBAC, D-11). Cuando una subruta futura
   * adopte un permiso más específico, actualizar aquí su `permission`.
   */
  const ADMIN_AREAS = [
    {
      label: "Plantillas",
      description: "Plantillas de contenido reutilizables para borradores y radicación.",
      icon: "fas fa-copy",
      href: "/admin/plantillas",
      permission: "USUA_PERM_ADMIN",
      implemented: true,
    },
    {
      label: "Reglas de enrutamiento",
      description: "Reglas automáticas que asignan radicados a una dependencia (E05).",
      icon: "fas fa-diagram-project",
      href: "/admin/flujos/reglas",
      permission: "USUA_PERM_ADMIN",
      implemented: true,
    },
    {
      label: "Reasignación en cascada",
      description: "Traslada en bloque los pasos pendientes de un usuario o dependencia.",
      icon: "fas fa-people-arrows",
      href: "/admin/flujos/reasignacion",
      permission: "USUA_PERM_ADMIN",
      implemented: true,
    },
    {
      label: "Usuarios",
      description: "Altas, bajas, roles y permisos de los usuarios del tenant.",
      icon: "fas fa-users",
      href: "/admin/usuarios",
      permission: "USUA_PERM_ADMIN",
      implemented: true,
    },
    {
      label: "Grupos",
      description: "Grupos de trabajo para distribución de radicados y flujos.",
      icon: "fas fa-people-group",
      href: "/admin/grupos",
      permission: "USUA_PERM_ADMIN",
      implemented: true,
    },
    {
      label: "Dependencias",
      description: "Organigrama institucional: dependencias emisoras y receptoras.",
      icon: "fas fa-sitemap",
      href: "/admin/dependencias",
      permission: "USUA_PERM_ADMIN",
      implemented: true,
    },
    {
      label: "TRD / CCD",
      description: "Tablas de Retención Documental y Cuadro de Clasificación (E04).",
      icon: "fas fa-folder-tree",
      href: "/admin/trd",
      permission: "USUA_PERM_ADMIN",
      implemented: true,
    },
    {
      label: "TVD",
      description: "Tablas de Valoración Documental: fondo acumulado, con fechas extremas y justificación (ADR-026).",
      icon: "fas fa-box-archive",
      href: "/admin/tvd",
      // ADR-026 reutiliza a propósito el permiso de TRD (misma potestad
      // archivística, mismo Comité) — igual que la tarjeta "TRD / CCD".
      permission: "USUA_PERM_TRD",
      implemented: true,
    },
    {
      label: "Metadatos",
      description: "Esquemas de metadatos variables por tipo documental (E03).",
      icon: "fas fa-tags",
      href: "/admin/metadatos",
      permission: "USUA_PERM_ADMIN",
      implemented: true,
    },
    {
      label: "Catálogos",
      description: "Catálogos institucionales: tipos documentales, series, causales.",
      icon: "fas fa-list",
      href: "/admin/catalogos",
      permission: "USUA_PERM_ADMIN",
      implemented: true,
    },
    {
      label: "Parámetros",
      description: "Parámetros generales de configuración del tenant.",
      icon: "fas fa-sliders",
      href: "/admin/parametros",
      permission: "USUA_PERM_ADMIN",
      implemented: true,
    },
    {
      label: "Auditoría",
      description: "Consulta del registro de auditoría inmutable (audit_log).",
      icon: "fas fa-clipboard-list",
      href: "/admin/auditoria",
      permission: "USUA_PERM_ADMIN",
      implemented: true,
    },
    {
      label: "Notificaciones",
      description: "Historial de envíos y registro de webhooks salientes.",
      icon: "fas fa-bell",
      href: "/admin/notificaciones",
      permission: "USUA_PERM_ADMIN",
      implemented: true,
    },
    {
      label: "PINAR",
      description: "Plan Institucional de Archivos: ciclo de vida y tablero de avance (Ac. AGN 003/2015).",
      icon: "fas fa-map",
      href: "/admin/pinar",
      permission: "USUA_PERM_ADMIN",
      implemented: true,
    },
    {
      label: "Colas de errores",
      description: "Eventos en dead-letter pendientes de reintento (ADR-021).",
      icon: "fas fa-triangle-exclamation",
      href: "/admin/colas",
      // El gate real de los tres routers de dead-letter es PERM_DLQ_ADMIN, NO
      // USUA_PERM_ADMIN (ver `colas/+page.server.js`). Anunciar la tarjeta por
      // el permiso equivocado ofrecería la sección a un administrador que al
      // entrar solo se encontraría un 403 — la UI debe ser espejo del RBAC
      // real, no de un permiso vecino que suena parecido.
      permission: "PERM_DLQ_ADMIN",
      implemented: true,
    },
  ];

  $: visibleAreas = filterVisibleNavItems(ADMIN_AREAS, $permissions, $roles);

  onMount(() => {
    setBreadcrumbs([{ label: "Administración" }]);
  });
</script>

<svelte:head>
  <title>Administración — OrpycaMCP</title>
</svelte:head>

<div class="admin-page">
  <header class="admin-page__header">
    <h1 class="admin-page__title">Administración</h1>
    <p class="admin-page__subtitle">
      Cada área se administra por separado. Elige una tarjeta para configurarla.
    </p>
  </header>

  {#if visibleAreas.length === 0}
    <EmptyState
      icon="fas fa-lock"
      title="Sin permisos de administración"
      description="Tu usuario no tiene ningún permiso de administración asignado. Contacta a un administrador del tenant si crees que esto es un error."
    />
  {:else}
    <ul class="admin-grid" role="list" aria-label="Áreas de administración">
      {#each visibleAreas as area (area.label)}
        <li class="admin-grid__item">
          {#if area.implemented}
            <a
              class="admin-card admin-card--enabled"
              href={area.href}
              aria-label="{area.label} — {area.description}"
            >
              <span class="admin-card__icon" aria-hidden="true"><i class={area.icon}></i></span>
              <span class="admin-card__body">
                <span class="admin-card__label">{area.label}</span>
                <span class="admin-card__description">{area.description}</span>
              </span>
              <span class="admin-card__chevron" aria-hidden="true">
                <i class="fas fa-chevron-right"></i>
              </span>
            </a>
          {:else}
            <!--
              M11 (revisión UX Fase 0): sin `role`, este <div> tiene el rol
              implícito ARIA `generic` — `aria-label` está prohibido en ese
              rol por ARIA 1.2 y los lectores de pantalla lo ignoran;
              `aria-disabled` tampoco se expone sin un rol que lo soporte.
              El contenido visible (badge de texto + candado) ya comunica el
              estado "próximamente" sin necesidad de andamiaje ARIA.
            -->
            <div class="admin-card admin-card--disabled">
              <span class="admin-card__icon" aria-hidden="true"><i class={area.icon}></i></span>
              <span class="admin-card__body">
                <span class="admin-card__label">{area.label}</span>
                <span class="admin-card__description">{area.description}</span>
              </span>
              <span class="admin-card__badge">
                <i class="fas fa-lock" aria-hidden="true"></i>
                Próximamente
              </span>
            </div>
          {/if}
        </li>
      {/each}
    </ul>
  {/if}
</div>

<style lang="scss">
  @use "$styles/_tokens" as t;

  .admin-page {
    padding: var(--op-space-5);
    max-width: 1100px;

    &__header {
      margin-bottom: var(--op-space-5);
    }

    &__title {
      font-size: var(--op-font-lg);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      margin: 0 0 var(--op-space-1);
    }

    &__subtitle {
      font-size: var(--op-font-sm);
      color: var(--op-text-secondary);
      margin: 0;
    }
  }

  .admin-grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: var(--op-space-4);
    list-style: none;
    margin: 0;
    padding: 0;

    &__item {
      display: flex;
    }
  }

  .admin-card {
    display: flex;
    align-items: flex-start;
    gap: var(--op-space-3);
    width: 100%;
    padding: var(--op-space-4);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius-lg);
    background: var(--op-surface);
    text-decoration: none;
    min-height: 44px;
    transition:
      border-color var(--op-transition),
      box-shadow var(--op-transition);

    &__icon {
      flex-shrink: 0;
      width: 40px;
      height: 40px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: var(--op-radius);
      background: var(--op-primary-light);
      color: var(--op-primary-dark);
      font-size: 1.1rem;
    }

    &__body {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
      flex: 1;
    }

    &__label {
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
      font-size: var(--op-font-sm);
    }

    &__description {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      line-height: 1.4;
    }

    &__chevron {
      flex-shrink: 0;
      color: var(--op-text-secondary);
      align-self: center;
    }

    &--enabled {
      cursor: pointer;

      &:hover,
      &:focus-visible {
        border-color: var(--op-primary);
        box-shadow: var(--op-shadow-sm);
      }

      &:focus-visible {
        outline: 2px solid var(--op-primary);
        outline-offset: 2px;
      }
    }

    &--disabled {
      cursor: not-allowed;
      // M10 (revisión UX Fase 0): la revisión reportó que este bloque caía por
      // debajo de AA, pero al recalcular los ratios el original ya cumplía
      // (--op-text-secondary sobre --op-bg = 4.69:1, AA exige 4.5:1). Se
      // conserva el ajuste igualmente porque la tarjeta no es un control
      // inactivo sino texto informativo que debe leerse con holgura: fondo
      // --op-surface (base de .admin-card) y label en --op-text.
      // Ratios vigentes: label 15.28:1 · descripción 4.83:1.
      // La señal de "inerte" la dan el icono gris, el candado y el badge.
      .admin-card__icon {
        background: var(--op-border);
        color: var(--op-text-secondary);
      }

      .admin-card__label {
        color: var(--op-text);
      }

      .admin-card__description {
        color: var(--op-text-secondary);
      }
    }

    &__badge {
      flex-shrink: 0;
      align-self: center;
      display: inline-flex;
      align-items: center;
      gap: var(--op-space-1);
      font-size: var(--op-font-xs);
      font-weight: var(--op-weight-medium);
      color: var(--op-text-secondary);
      background: var(--op-surface);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-sm);
      padding: 2px var(--op-space-2);
      white-space: nowrap;
    }
  }
</style>
