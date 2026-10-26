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
  AsistenteEntidadCard.svelte — Tarjeta «Asistente y búsqueda semántica» de
  /admin/parametros (C-1, ADR-037).

  La ENTIDAD decide si activa el asistente conversacional y la búsqueda por
  conocimiento (es la responsable del tratamiento de sus datos). Edita solo quien
  tiene USUA_PERM_ADMIN nivel >= 3, igual que el backend (`PUT /config/asistente`),
  que re-valida siempre.

  Props:
    habilitado  boolean|null  — estado actual; null = no se pudo leer
    loadError   string|null   — motivo de la lectura fallida
    canEdit     boolean       — USUA_PERM_ADMIN >= 3
-->
<script>
  import { invalidateAll } from "$app/navigation";
  import { notifyError, notifySuccess } from "$lib/stores/notifications.js";
  import Button from "$lib/components/ui/Button.svelte";
  import { codigoError, mensajeError } from "$lib/utils/apiError.js";

  /** @type {boolean|null} */
  export let habilitado = null;
  /** @type {string|null} */
  export let loadError = null;
  export let canEdit = false;
  /** @type {{asistente?: string, modelo?: string, rag?: string, embeddings?: string, embeddings_local?: boolean}|null} */
  export let proveedores = null;

  let declaracion = false;
  let motivo = "";

  let draft = habilitado === true;
  let lastSeen = habilitado;
  let saving = false;
  /** @type {string|null} */
  let aviso = null;

  // Si el servidor refresca el estado (invalidateAll), el borrador lo sigue.
  $: if (habilitado !== lastSeen) {
    lastSeen = habilitado;
    draft = habilitado === true;
  }

  $: unchanged = draft === (habilitado === true);
  // Encender exige la declaración; apagar no tiene fricción.
  $: encendiendo = draft && habilitado !== true;
  $: puedeAplicar = !unchanged && (!encendiendo || declaracion);
  $: nombreProveedor = proveedores?.asistente ?? "el proveedor del asistente";
  $: sinIndexarAlApagar = proveedores?.embeddings_local === false;

  /** @param {any} body */
  function mensajeAsistente(body) {
    switch (codigoError(body)) {
      case "declaracion_requerida":
        return "Para activar el asistente debe marcar la declaración de contrato de transmisión de datos.";
      case "politica_no_publicada":
        return "Antes de activar el asistente debe publicar la política de tratamiento de datos: fije la clave «Política de tratamiento de datos: versión» en la pestaña Parámetros de esta pantalla.";
      case "tenant_not_found":
        return "No se encontró la entidad.";
      default:
        return mensajeError(body, "No se pudo cambiar la configuración del asistente.");
    }
  }

  async function guardar() {
    saving = true;
    aviso = null;
    try {
      const res = await fetch("/admin/parametros/api/asistente", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          draft
            ? {
                habilitado: true,
                declaracion_transmision: declaracion,
                ...(motivo.trim() ? { motivo: motivo.trim() } : {}),
              }
            : { habilitado: false },
        ),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        notifyError(mensajeAsistente(body));
        return;
      }
      declaracion = false;
      motivo = "";
      if (typeof body?.aviso === "string" && body.aviso.trim()) aviso = body.aviso;
      notifySuccess(
        body?.cambiado === false
          ? "El asistente ya estaba en ese estado."
          : body?.habilitado
            ? "Asistente activado para la entidad."
            : "Asistente desactivado para la entidad.",
      );
      await invalidateAll();
    } catch {
      notifyError("No se pudo conectar al servidor de parámetros.");
    } finally {
      saving = false;
    }
  }
</script>

<section class="asistente-card" aria-labelledby="asistente-card-title" data-testid="asistente-card">
  <h2 id="asistente-card-title" class="asistente-card__title">Asistente y búsqueda semántica</h2>

  {#if loadError}
    <div class="asistente-card__error" role="alert">
      <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
      {loadError}
    </div>
  {:else}
    <p class="asistente-card__estado" data-testid="asistente-estado">
      Estado actual:
      <strong>{habilitado ? "activado" : "desactivado"}</strong>
    </p>
  {/if}

  <ul class="asistente-card__notas">
    <li>
      Activarlo habilita el asistente conversacional, la búsqueda semántica y de antecedentes, y la
      ayuda de redacción con citas. Cada persona, además, necesita el permiso
      <code>PERM_CONOCIMIENTO</code> (se asigna en Administración &gt; Grupos).
      <code>PERM_CONOCIMIENTO</code> se concedió a los grupos que ya tenían algún permiso; revise a quién
      se le dio.
    </li>
    <li data-testid="asistente-egreso">
      Al activarlo, el texto de las consultas y el contenido no reservado que el asistente lee
      —incluidos datos personales— se envía a {nombreProveedor} (EE. UU.), que actúa como encargado del
      tratamiento. El contenido marcado Reservado o Clasificado se sustituye por un aviso. Lo que usted
      escriba o dicte se envía tal cual.
    </li>
    <li>
      Lo ya indexado se conserva al apagarlo, pero no se sirve. Apagar no detiene la indexación.
      {#if sinIndexarAlApagar}
        <span data-testid="asistente-aviso-indexacion"
          >Con el asistente apagado no se indexa lo que se radica, y lo omitido no se reindexa al
          reactivarlo.</span
        >
      {/if}
    </li>
  </ul>

  {#if proveedores}
    <dl class="asistente-card__proveedores" data-testid="asistente-proveedores">
      <dt>Asistente conversacional</dt>
      <dd>{proveedores.asistente ?? "—"}{proveedores.modelo ? ` (${proveedores.modelo})` : ""}</dd>
      <dt>Respuestas con citas</dt>
      <dd>{proveedores.rag ?? "—"}</dd>
      <dt>Embeddings</dt>
      <dd>
        {proveedores.embeddings ?? "—"}{proveedores.embeddings_local === true
          ? " (local, no sale de la entidad)"
          : proveedores.embeddings_local === false
            ? " (externo)"
            : ""}
      </dd>
    </dl>
  {/if}

  {#if !loadError}
    <div class="asistente-card__control">
      <label class="asistente-card__switch" for="asistente-habilitado">
        <input
          id="asistente-habilitado"
          type="checkbox"
          role="switch"
          bind:checked={draft}
          disabled={!canEdit || saving}
          aria-describedby="asistente-card-ayuda"
        />
        <span>Activar el asistente en esta entidad</span>
      </label>
      {#if canEdit}
        <Button
          icon="fas fa-floppy-disk"
          loading={saving}
          disabled={!puedeAplicar}
          on:click={guardar}>Aplicar cambio</Button
        >
      {/if}
    </div>
    {#if canEdit && encendiendo}
      <div class="asistente-card__declaracion" data-testid="asistente-declaracion">
        <label class="asistente-card__switch" for="asistente-declaracion">
          <input id="asistente-declaracion" type="checkbox" bind:checked={declaracion} />
          <span
            >Declaro que la entidad tiene contrato de transmisión de datos con el proveedor del
            asistente y una política de tratamiento que cubre esta finalidad</span
          >
        </label>
        <label class="asistente-card__motivo" for="asistente-motivo">
          Motivo (opcional)
          <input id="asistente-motivo" type="text" maxlength="1000" bind:value={motivo} />
        </label>
      </div>
    {/if}
    {#if !canEdit}
      <p
        id="asistente-card-ayuda"
        class="asistente-card__hint"
        data-testid="asistente-solo-lectura"
      >
        Cambiarlo exige administración nivel 3 (crear y borrar). Puede consultar el estado.
      </p>
    {:else}
      <p id="asistente-card-ayuda" class="asistente-card__hint">
        El cambio aplica de inmediato a toda la entidad y queda en el registro de auditoría.
      </p>
    {/if}
    {#if aviso}
      <p class="asistente-card__aviso" role="status" data-testid="asistente-aviso">{aviso}</p>
    {/if}
  {/if}
</section>

<style lang="scss">
  .asistente-card {
    display: flex;
    flex-direction: column;
    gap: var(--op-space-3);
    padding: var(--op-space-4);
    border: 1px solid var(--op-border);
    border-radius: var(--op-radius);
    background: var(--op-surface);
    margin-bottom: var(--op-space-4);

    &__title {
      margin: 0;
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-bold);
      color: var(--op-text);
    }

    &__estado,
    &__hint {
      margin: 0;
      color: var(--op-text-secondary);
    }

    &__notas {
      margin: 0;
      padding-left: var(--op-space-4);
      color: var(--op-text-secondary);
      display: flex;
      flex-direction: column;
      gap: var(--op-space-2);
    }

    &__control {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: var(--op-space-3);
    }

    &__switch {
      display: inline-flex;
      align-items: center;
      gap: var(--op-space-2);
      color: var(--op-text);
      cursor: pointer;

      input {
        inline-size: 1.25rem;
        block-size: 1.25rem;
        accent-color: var(--op-primary);
      }
    }

    &__proveedores {
      margin: 0;
      display: grid;
      grid-template-columns: max-content 1fr;
      gap: var(--op-space-1) var(--op-space-3);
      color: var(--op-text-secondary);

      dt {
        font-weight: var(--op-weight-bold);
        color: var(--op-text);
      }
      dd {
        margin: 0;
      }
    }

    &__declaracion {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-2);
    }

    &__motivo {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
      color: var(--op-text);

      input {
        padding: var(--op-space-2);
        border: 1px solid var(--op-border);
        border-radius: var(--op-radius);
        background: var(--op-surface);
        color: var(--op-text);
      }
    }

    &__error {
      color: var(--op-error);
    }

    &__aviso {
      margin: 0;
      padding: var(--op-space-2) var(--op-space-3);
      border-left: 3px solid var(--op-warning);
      background: var(--op-bg);
      color: var(--op-text);
    }
  }
</style>
