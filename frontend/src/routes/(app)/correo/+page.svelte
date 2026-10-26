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
  /correo — Los buzones que esta persona puede trabajar (E19).

  Una lista corta y nada más: el trabajo ocurre dentro de cada bandeja. Lo que
  sí hace esta pantalla es DISTINGUIR los tres finales posibles —no llegué a
  preguntar, no tengo buzones, aquí están— porque los tres se veían igual
  cuando no existía: como una URL que nadie sabía escribir.
-->
<script>
  import { onMount } from "svelte";
  import { setBreadcrumbs } from "$lib/stores/ui.js";
  import EmptyState from "$lib/components/ui/EmptyState.svelte";

  /** @type {import('./$types').PageData} */
  export let data;

  $: cuentas = data.cuentas ?? [];
  $: cuentasError = data.cuentasError ?? null;

  onMount(() => {
    setBreadcrumbs([{ label: "Correo" }]);
  });

  /** @param {any} c */
  function descripcion(c) {
    const partes = [c?.direccion, c?.tipo === "personal" ? "personal" : "institucional"];
    return partes.filter(Boolean).join(" · ");
  }
</script>

<svelte:head><title>Correo — Docuboto</title></svelte:head>

<section class="correo-indice">
  <h1 class="title is-4">Correo</h1>
  <p class="subtitle is-6">
    Buzones que puede trabajar. Los correos se radican uno a uno desde su bandeja.
  </p>

  {#if cuentasError}
    <div class="notification is-danger is-light" role="alert">
      <strong>No se pudieron leer los buzones.</strong>
      {cuentasError.detail}
    </div>
  {:else if cuentas.length === 0}
    <EmptyState
      icon="fas fa-envelope-open-text"
      title="No tiene buzones asignados"
      description="La ingesta de correo se configura en Administración → Correo, y allí se asigna
        cada buzón institucional a los funcionarios que lo atienden."
    />
  {:else}
    <ul class="correo-indice__lista">
      {#each cuentas as cuenta (cuenta.id)}
        <li class="correo-indice__item">
          <a class="correo-indice__enlace" href="/correo/{cuenta.id}">
            <span class="icon"><i class="fas fa-inbox" aria-hidden="true"></i></span>
            <span class="correo-indice__texto">
              <strong>{cuenta.nombre}</strong>
              <span class="correo-indice__detalle">{descripcion(cuenta)}</span>
            </span>
            {#if cuenta.activa === false}
              <span class="tag is-warning is-light">inactiva</span>
            {/if}
          </a>
        </li>
      {/each}
    </ul>
  {/if}
</section>

<style lang="scss">
  .correo-indice {
    &__lista {
      list-style: none;
      margin: 0;
      padding: 0;
    }

    &__item + &__item {
      margin-top: 0.5rem;
    }

    &__enlace {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.75rem 1rem;
      border: 1px solid var(--op-border, #dbdbdb);
      border-radius: var(--op-radius, 6px);
      background: var(--op-surface, #fff);
      color: inherit;

      &:hover {
        border-color: var(--op-primary, #3273dc);
      }
    }

    // La descripción va en bloque y no como hermana en la fila: es prosa dentro
    // de un contenedor flex, y en columna se trocea palabra a palabra.
    &__texto {
      display: block;
    }

    &__detalle {
      display: block;
      font-size: 0.85rem;
      color: var(--op-text-secondary, #7a7a7a);
    }
  }
</style>
