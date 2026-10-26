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
  DependenciaTreeNode.svelte — Nodo recursivo del árbol de dependencias
  (admin/dependencias, `GET /dependencias/tree`).

  No existía ningún componente de árbol reutilizable en el proyecto (se buscó
  entre lib/components y routes antes de escribir este); se construyó uno
  nuevo, deliberadamente pequeño, con `<details>/<summary>` nativos en vez de
  un patrón ARIA `role="tree"` completo — accesibilidad (expandir/colapsar,
  foco, teclado) gratis del navegador, sin reinventar el manejo de teclas.

  Props:
    node      DependenciaNode  — { id, parent_id, codigo, sigla, nombre,
                                    depe_rad_tp2, activo, children: [...] }
    depth     number           — profundidad (0 = raíz), solo para indentar
    canWrite  boolean          — gate de permiso REAL (resuelto por la página)

  Eventos (burbujean desde cualquier profundidad del árbol):
    on:edit      detail: node       — pide abrir el formulario de edición
    on:toggle    detail: node       — pide activar/desactivar (la página
                                       decide si hace falta advertencia)
-->
<script>
  import { createEventDispatcher } from "svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import StatusChip from "$lib/components/ui/StatusChip.svelte";

  /** @type {Record<string, any>} */
  export let node;
  export let depth = 0;
  export let canWrite = false;

  const dispatch = createEventDispatcher();

  function forwardEdit(n) {
    dispatch("edit", n);
  }
  function forwardToggle(n) {
    dispatch("toggle", n);
  }

  $: hasChildren = Array.isArray(node.children) && node.children.length > 0;
</script>

<li class="dep-tree__item" style="--dep-tree-depth: {depth}">
  {#if hasChildren}
    <details class="dep-tree__details" open>
      <summary class="dep-tree__summary">
        <span class="dep-tree__label">
          <span class="dep-tree__codigo">{node.codigo}</span>
          <span class="dep-tree__sigla">{node.sigla}</span>
          <span class="dep-tree__nombre">{node.nombre}</span>
          <StatusChip
            status={node.activo ? "success" : "neutral"}
            label={node.activo ? "Activa" : "Inactiva"}
            dot
          />
        </span>
        {#if canWrite}
          <span class="dep-tree__actions">
            <Button
              variant="ghost"
              size="sm"
              icon="fas fa-pen"
              aria-label="Editar {node.nombre}"
              on:click={() => forwardEdit(node)}
            >
              Editar
            </Button>
            <Button
              variant="ghost"
              size="sm"
              icon={node.activo ? "fas fa-ban" : "fas fa-check"}
              aria-label="{node.activo ? 'Desactivar' : 'Activar'} {node.nombre}"
              on:click={() => forwardToggle(node)}
            >
              {node.activo ? "Desactivar" : "Activar"}
            </Button>
          </span>
        {/if}
      </summary>

      <ul class="dep-tree__children">
        {#each node.children as child (child.id)}
          <svelte:self node={child} depth={depth + 1} {canWrite} on:edit on:toggle />
        {/each}
      </ul>
    </details>
  {:else}
    <div class="dep-tree__leaf">
      <span class="dep-tree__label">
        <span class="dep-tree__codigo">{node.codigo}</span>
        <span class="dep-tree__sigla">{node.sigla}</span>
        <span class="dep-tree__nombre">{node.nombre}</span>
        <StatusChip
          status={node.activo ? "success" : "neutral"}
          label={node.activo ? "Activa" : "Inactiva"}
          dot
        />
      </span>
      {#if canWrite}
        <span class="dep-tree__actions">
          <Button
            variant="ghost"
            size="sm"
            icon="fas fa-pen"
            aria-label="Editar {node.nombre}"
            on:click={() => forwardEdit(node)}
          >
            Editar
          </Button>
          <Button
            variant="ghost"
            size="sm"
            icon={node.activo ? "fas fa-ban" : "fas fa-check"}
            aria-label="{node.activo ? 'Desactivar' : 'Activar'} {node.nombre}"
            on:click={() => forwardToggle(node)}
          >
            {node.activo ? "Desactivar" : "Activar"}
          </Button>
        </span>
      {/if}
    </div>
  {/if}
</li>

<style lang="scss">
  .dep-tree {
    &__item {
      list-style: none;
    }

    &__details {
      margin-left: calc(var(--dep-tree-depth, 0) * 0px);
    }

    &__summary {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--op-space-3);
      flex-wrap: wrap;
      padding: var(--op-space-2) var(--op-space-3);
      border-radius: var(--op-radius);
      cursor: pointer;

      &:hover {
        background: var(--op-primary-light);
      }

      // Quita el marcador triángulo por defecto del navegador y usa uno propio
      // vía ::marker para no depender de estilos inconsistentes entre navegadores.
      &::marker {
        color: var(--op-text-secondary);
      }
    }

    &__leaf {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: var(--op-space-3);
      flex-wrap: wrap;
      padding: var(--op-space-2) var(--op-space-3);
      margin-left: 1.2em; // alinea con el texto de los <summary> hermanos
      border-radius: var(--op-radius);

      &:hover {
        background: var(--op-primary-light);
      }
    }

    &__label {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      flex-wrap: wrap;
    }

    &__codigo {
      font-family: var(--op-font-family-mono);
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      background: var(--op-bg);
      border-radius: var(--op-radius-sm);
      padding: 1px var(--op-space-1);
    }

    &__sigla {
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
    }

    &__nombre {
      color: var(--op-text);
    }

    &__actions {
      display: flex;
      gap: var(--op-space-1);
      white-space: nowrap;
    }

    &__children {
      margin: 0;
      padding-left: var(--op-space-5);
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
  }
</style>
