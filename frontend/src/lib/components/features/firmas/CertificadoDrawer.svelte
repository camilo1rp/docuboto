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
  CertificadoDrawer.svelte — Vista de certificado de una firma electrónica
  individual (E06, ADR-016, Fase 3 cierre de desfase API↔UI).

  Muestra qué se firmó, quién, cuándo y con qué nivel de conformidad (XAdES
  B/T/LT/LTA o nativa/HMAC) — con apariencia de certificado, como pide el
  Sistema de Diseño Orpyca para este tipo de comprobante.

  Honestidad del comprobante (frontend/CLAUDE.md, regla no negociable):
    - `acreditado` del backend es SIEMPRE false en este incremento — este
      componente lo declara explícitamente arriba del todo, en un aviso
      permanente, no como letra pequeña.
    - El sello de tiempo (`tsa_acreditado`), el material LT/LTA
      (`revocation_provenance`) y la verificación son SIEMPRE presentados
      con su alcance real: "sello local (no acreditado)", "cadena de
      desarrollo", etc. — nunca como garantía cualificada.
    - "Verificar" es una herramienta técnica que exige el XML firmado
      completo (el backend no expone un atajo de un clic sin ese material);
      si el usuario no lo tiene, el componente lo dice, no simula un
      resultado.

  Props:
    open     boolean  — controla la visibilidad (Drawer interno)
    firmaId  string   — UUID de la firma a mostrar (undefined = oculto)

  Eventos:
    on:close  — el usuario cerró el drawer
-->
<script>
  import { createEventDispatcher } from "svelte";
  import Drawer from "$lib/components/ui/Drawer.svelte";
  import { formatearFechaHora } from "$lib/utils/fecha.js";
  import Loader from "$lib/components/ui/Loader.svelte";
  import Button from "$lib/components/ui/Button.svelte";
  import FormField from "$lib/components/ui/FormField.svelte";
  import { mensajeError } from "$lib/utils/apiError.js";
  import AdvertenciasVerificacion from "./AdvertenciasVerificacion.svelte";

  export let open = false;
  /** @type {string|null} */
  export let firmaId = null;

  const dispatch = createEventDispatcher();

  let loading = false;
  /** @type {import('$lib/types/api.js').SignatureCertificado|null} */
  let cert = null;
  let loadError = "";

  const PROVIDER_LABEL = /** @type {Record<string,string>} */ ({
    nativa: "Sello nativo (HMAC-SHA256)",
    xades_local: "Sello institucional XAdES",
    xades_personal: "Firma personal XAdES",
  });

  function resetVerify() {
    verifyOpen = false;
    verifyPayloadXml = "";
    verifyResult = null;
    verifyError = "";
  }

  async function load(/** @type {string} */ id) {
    loading = true;
    loadError = "";
    cert = null;
    resetVerify();
    try {
      const res = await fetch(`/firmas/api/certificado/${encodeURIComponent(id)}`);
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        cert = data;
      } else if (res.status === 404) {
        loadError = "No se encontró información de esta firma.";
      } else {
        loadError = mensajeError(data, `No se pudo cargar el certificado (error ${res.status}).`);
      }
    } catch {
      loadError = "No se pudo conectar al servidor de firmas.";
    } finally {
      loading = false;
    }
  }

  // Carga cuando se abre con un firmaId válido — nunca reutiliza datos de
  // una firma anterior (evita mostrar el certificado equivocado durante la
  // transición de un item a otro).
  $: if (open && firmaId) {
    load(firmaId);
  }

  function handleClose() {
    dispatch("close");
  }

  function formatDateTime(/** @type {string|null|undefined} */ iso) {
    if (!iso) return "—";
    return formatearFechaHora(iso);
  }

  // ---------------------------------------------------------------------------
  // Verificación técnica (requiere el XML/documento firmado completo)
  // ---------------------------------------------------------------------------
  let verifyOpen = false;
  let verifyPayloadXml = "";
  let verifying = false;
  /** @type {import('$lib/types/api.js').SignatureVerifyResult|null} */
  let verifyResult = null;
  let verifyError = "";

  async function ejecutarVerificacion() {
    verifyError = "";
    verifyResult = null;
    if (!verifyPayloadXml.trim()) {
      verifyError = "Pegue el documento firmado completo para verificarlo.";
      return;
    }
    verifying = true;
    try {
      const res = await fetch(`/firmas/api/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ firma_id: cert?.firma_id, payload_xml: verifyPayloadXml }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        verifyResult = data;
      } else {
        verifyError = mensajeError(data, `No se pudo verificar (error ${res.status}).`);
      }
    } catch {
      verifyError = "No se pudo conectar al servidor de firmas para verificar.";
    } finally {
      verifying = false;
    }
  }
</script>

<Drawer {open} title="Certificado de firma" width="480px" on:close={handleClose}>
  {#if loading}
    <div class="cert-drawer__loader">
      <Loader />
    </div>
  {:else if loadError}
    <p class="cert-drawer__error" role="alert">
      <i class="fas fa-exclamation-triangle" aria-hidden="true"></i>
      {loadError}
    </p>
  {:else if cert}
    <!-- Aviso permanente de alcance legal — nunca letra pequeña -->
    <div class="cert-drawer__legal-banner" role="note">
      <i class="fas fa-info-circle" aria-hidden="true"></i>
      <span>
        Firma electrónica bajo Ley 527/1999 <strong>art. 7</strong> (reconocimiento general). No es firma
        digital acreditada (art. 28) ni implica control exclusivo del firmante: la clave se custodia en
        el servidor institucional.
      </span>
    </div>

    <div class="cert-drawer__card" aria-label="Certificado de firma">
      <div class="cert-drawer__card-header">
        <i class="fas fa-certificate" aria-hidden="true"></i>
        <span>Comprobante de firma</span>
      </div>

      <dl class="cert-drawer__fields">
        <div class="cert-drawer__row">
          <dt>Objeto firmado</dt>
          <dd>{cert.objeto_tipo}{cert.objeto_id ? ` — ${cert.objeto_id}` : ""}</dd>
        </div>
        <div class="cert-drawer__row">
          <dt>Firmante</dt>
          <!-- Decía «Firmante» y enseñaba el identificador interno: un
               comprobante que no dice quién firmó no certifica nada. El
               nombre lo resuelve el servicio de firma al leer; el
               identificador queda en el `title`, que es donde sigue haciendo
               falta para cruzar el comprobante con el registro de firmas. -->
          <dd
            class={cert.firmante_nombre ? "" : "cert-drawer__mono"}
            title={cert.firmante_id ?? undefined}
          >
            {cert.firmante_nombre ?? cert.firmante_id ?? "—"}
          </dd>
        </div>
        <div class="cert-drawer__row">
          <dt>Fecha y hora</dt>
          <dd>{formatDateTime(cert.sello_tiempo)}</dd>
        </div>
        <div class="cert-drawer__row">
          <dt>Tipo de sello</dt>
          <dd>{PROVIDER_LABEL[cert.provider] ?? cert.provider}</dd>
        </div>
        {#if cert.xades_level}
          <div class="cert-drawer__row">
            <dt>Nivel XAdES</dt>
            <dd>{cert.xades_level}</dd>
          </div>
        {/if}
        {#if cert.nivel_conformidad}
          <div class="cert-drawer__row">
            <dt>Conformidad</dt>
            <dd>{cert.nivel_conformidad}</dd>
          </div>
        {/if}
        {#if cert.certificado_fingerprint}
          <div class="cert-drawer__row">
            <dt>Huella del certificado</dt>
            <dd class="cert-drawer__mono cert-drawer__fingerprint">
              {cert.certificado_fingerprint}
            </dd>
          </div>
        {/if}
        <div class="cert-drawer__row">
          <dt>Acreditación</dt>
          <dd>
            <span class="cert-drawer__no-acreditado">
              <i class="fas fa-times-circle" aria-hidden="true"></i>
              No acreditada (ONAC)
            </span>
          </dd>
        </div>
        {#if cert.tsa_timestamp}
          <div class="cert-drawer__row">
            <dt>Sello de tiempo</dt>
            <dd>
              {formatDateTime(cert.tsa_timestamp)}
              <span class="cert-drawer__hint">
                ({cert.tsa_acreditado ? "acreditado" : "local, no acreditado"})
              </span>
            </dd>
          </div>
        {/if}
        {#if cert.lt_material_present}
          <div class="cert-drawer__row">
            <dt>Material LT/LTA</dt>
            <dd>
              Presente
              <span class="cert-drawer__hint">
                (procedencia: {cert.revocation_provenance ?? "desconocida"} — nunca PKI acreditada en
                este incremento)
              </span>
            </dd>
          </div>
        {/if}
      </dl>
    </div>

    <!-- Verificación técnica — herramienta explícita, no automática -->
    <div class="cert-drawer__verify">
      <Button
        variant="ghost"
        icon={verifyOpen ? "fas fa-chevron-up" : "fas fa-chevron-down"}
        on:click={() => (verifyOpen = !verifyOpen)}
        aria-expanded={verifyOpen}
      >
        Verificar firma
      </Button>

      {#if verifyOpen}
        <div class="cert-drawer__verify-panel">
          <p class="cert-drawer__hint">
            Pegue el documento <strong>firmado completo</strong> (el XML descargado, incluyendo la firma)
            para verificar su integridad contra este certificado. Sin ese documento exacto no es posible
            verificar — este panel no simula un resultado.
          </p>
          <FormField
            id="cert-verify-xml"
            label="Documento firmado (XML)"
            type="textarea"
            bind:value={verifyPayloadXml}
            rows={6}
            placeholder="Pegue aquí el XML firmado completo…"
          />
          <Button
            variant="primary"
            icon="fas fa-search"
            loading={verifying}
            on:click={ejecutarVerificacion}
          >
            Verificar
          </Button>

          {#if verifyError}
            <p class="cert-drawer__error" role="alert">{verifyError}</p>
          {:else if verifyResult}
            <p
              class="cert-drawer__verify-result"
              class:cert-drawer__verify-result--ok={verifyResult.valida}
              class:cert-drawer__verify-result--bad={!verifyResult.valida}
              role="status"
            >
              <i
                class={verifyResult.valida ? "fas fa-check-circle" : "fas fa-times-circle"}
                aria-hidden="true"
              ></i>
              {verifyResult.valida
                ? "La firma es válida para este documento."
                : "La firma NO es válida para este documento."}
              {#if verifyResult.motivo}
                <span class="cert-drawer__hint"> — {verifyResult.motivo}</span>
              {/if}
            </p>
            <AdvertenciasVerificacion advertencias={verifyResult.advertencias} />
            {#if verifyResult.revocacion_status && verifyResult.revocacion_status !== "vigente"}
              <p class="cert-drawer__error" role="alert">
                Estado de revocación del certificado: <strong
                  >{verifyResult.revocacion_status}</strong
                >.
              </p>
            {/if}
          {/if}
        </div>
      {/if}
    </div>
  {/if}
</Drawer>

<style lang="scss">
  .cert-drawer {
    &__loader {
      display: flex;
      justify-content: center;
      padding: var(--op-space-6);
    }

    &__error {
      display: flex;
      align-items: flex-start;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      color: var(--op-error-fg);
      background: var(--op-error-bg);
      border: 1px solid var(--op-error);
      border-radius: var(--op-radius-sm);
      padding: var(--op-space-3);
      margin: 0 0 var(--op-space-3);
    }

    &__legal-banner {
      display: flex;
      align-items: flex-start;
      gap: var(--op-space-2);
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
      background: var(--op-bg);
      border: 1px solid var(--op-border);
      border-radius: var(--op-radius-sm);
      padding: var(--op-space-3);
      margin: 0 0 var(--op-space-4);
    }

    &__card {
      border: 1px solid var(--op-primary);
      border-radius: var(--op-radius-lg);
      overflow: hidden;
      margin-bottom: var(--op-space-4);
      background: var(--op-surface);
    }

    &__card-header {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      background: var(--op-primary-light);
      color: var(--op-primary-dark);
      font-weight: var(--op-weight-bold);
      padding: var(--op-space-3) var(--op-space-4);
    }

    &__fields {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-1);
      margin: 0;
      padding: var(--op-space-3) var(--op-space-4);
    }

    &__row {
      display: grid;
      grid-template-columns: 130px 1fr;
      gap: var(--op-space-2);
      padding: var(--op-space-2) 0;
      border-bottom: 1px solid var(--op-border);
      font-size: var(--op-font-sm);

      &:last-child {
        border-bottom: none;
      }

      dt {
        color: var(--op-text-secondary);
        font-weight: var(--op-weight-medium);
      }

      dd {
        color: var(--op-text);
        margin: 0;
        word-break: break-all;
      }
    }

    &__mono {
      font-family: var(--op-font-family-mono);
      font-size: var(--op-font-xs);
    }

    &__fingerprint {
      word-break: break-all;
    }

    &__no-acreditado {
      display: inline-flex;
      align-items: center;
      gap: var(--op-space-1);
      color: var(--op-error);
      font-weight: var(--op-weight-medium);
    }

    &__hint {
      font-size: var(--op-font-xs);
      color: var(--op-text-secondary);
    }

    &__verify {
      border-top: 1px solid var(--op-border);
      padding-top: var(--op-space-3);
    }

    &__verify-panel {
      display: flex;
      flex-direction: column;
      gap: var(--op-space-3);
      margin-top: var(--op-space-3);
    }

    &__verify-result {
      display: flex;
      align-items: center;
      gap: var(--op-space-2);
      font-size: var(--op-font-sm);
      font-weight: var(--op-weight-medium);
      margin: 0;

      &--ok {
        color: var(--op-success);
      }

      &--bad {
        color: var(--op-error);
      }
    }
  }
</style>
