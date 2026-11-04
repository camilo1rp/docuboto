/* ============================================================================
   Docuboto 3.0 — vistas de trabajo: Escritorio, Documentos, Bandeja, Radicar
   ========================================================================== */
window.VIEWS = window.VIEWS || {};
(function (V) {
  const esc = window.esc;

  const chip = (tone, label, iconName) =>
    `<span class="chip ${
      tone ? "chip--" + tone : ""
    }">${iconName ? icon(iconName) : ""}${esc(label)}</span>`;

  const cite = (n) => `<button class="cite" data-act="cite" data-n="${n}" aria-label="Ver fuente ${n}">${n}</button>`;

  const avatar = (initials, size) =>
    `<span class="avatar" style="${
      size ? `width:${size}px;height:${size}px;font-size:${size * 0.38}px;` : ""
    }">${esc(initials)}</span>`;

  const pageHead = ({ eyebrow, title, sub, actions }) => `
    <header class="page__head">
      <div class="page__heading">
        ${eyebrow ? `<div class="page__eyebrow">${eyebrow}</div>` : ""}
        <h1 class="page__title">${title}</h1>
        ${sub ? `<p class="page__sub">${sub}</p>` : ""}
      </div>
      ${actions ? `<div class="page__actions">${actions}</div>` : ""}
    </header>`;

  const tabs = (items, current, act) => `
    <div class="tabs" role="tablist">
      ${items
        .map(
          (t) => `
        <button class="tab" role="tab" aria-selected="${t.id === current}"
          data-act="${act}" data-tab="${t.id}">
          ${esc(t.label)}${t.count != null ? `<span class="tab__count">${t.count}</span>` : ""}
        </button>`,
        )
        .join("")}
    </div>`;

  /* ==========================================================================
     ESCRITORIO
     ========================================================================== */
  V.escritorio = function (s) {
    const recs = DB.records.filter((r) => r.owner === "Tú" || r.dueTone === "danger");
    const dueToday = DB.records.filter((r) => r.dueTone === "danger").length;
    const overdue = DB.records.filter((r) => r.due.indexOf("Venció") === 0).length;
    const aiCount = Object.keys(DB.triage).length;

    return `
    <div class="page">
      <div class="briefing">
        <div class="briefing__top">
          ${icon("sparkles")} Resumen de proboto · 08:42
          <span class="chip chip--ai" style="margin-left:auto">Nivel 2 · Triage</span>
        </div>
        <h1 class="briefing__title">Buenos días, Camilo. Tienes <b>${dueToday} cosas que vencen hoy</b> y una que ya venció.</h1>
        <p class="briefing__text">
          Llegaron 4 radicados desde las 6:00. proboto ya tipificó tres y encontró un posible
          duplicado en <span class="mono">E-2026-4824</span>: mismo NIT y mismo expediente que
          <span class="mono">E-2026-4798</span> del 27 de octubre. La respuesta
          <span class="mono">S-2026-0912</span> espera tu visto bueno desde ayer y su plazo legal
          se cumple hoy a las 17:00.
        </p>
        <div class="briefing__actions">
          <button class="btn btn--primary" data-act="go" data-route="documentos" data-tab="bandeja">
            ${icon("inbox")} Abrir mi bandeja
          </button>
          <button class="btn btn--ai" data-act="agent-open" data-agent-tab="brief">
            ${icon("sparkles")} Ver el resumen completo
          </button>
          <button class="btn" data-act="go" data-route="documentos" data-tab="radicar">
            ${icon("plus")} Radicar
          </button>
        </div>
      </div>

      <div class="grid grid--4" style="margin-bottom:var(--db-space-4)">
        <div class="stat">
          <div class="stat__label">${icon("inbox")} En mi bandeja</div>
          <div class="stat__value">6</div>
          <div class="stat__delta">3 sin abrir</div>
        </div>
        <div class="stat stat--hot">
          <div class="stat__label">${icon("clock")} Vencen hoy</div>
          <div class="stat__value">${dueToday}</div>
          <div class="stat__delta">${overdue} ya vencido</div>
        </div>
        <div class="stat stat--ai">
          <div class="stat__label">${icon("sparkles")} Sugerencias de proboto</div>
          <div class="stat__value">${aiCount}</div>
          <div class="stat__delta">3 requieren decisión hoy</div>
        </div>
        <div class="stat">
          <div class="stat__label">${icon("send")} Envíos en curso</div>
          <div class="stat__value">3</div>
          <div class="stat__delta">1 en preparación</div>
        </div>
      </div>

      <div class="desk-grid">
        <section class="card">
          <div class="card__head">
            <div>
              <div class="card__title">${icon("target")} Lo que requiere tu decisión</div>
              <div class="card__sub">Ordenado por consecuencia, no por fecha de llegada</div>
            </div>
            <div class="spacer"></div>
            <button class="btn btn--sm btn--ghost" data-act="go" data-route="documentos" data-tab="bandeja">Ver todo ${icon("right")}</button>
          </div>
          <div class="list">
            ${[
              {
                num: "E-2026-4819",
                title: "Recurso de reposición sin respuesta",
                sub: "Venció hace 1 día · Despacho del Alcalde",
                tone: "danger",
                chip: "Vencido",
                id: "r6",
              },
              {
                num: "S-2026-0912",
                title: "Respuesta lista, falta visto bueno",
                sub: "Vence hoy 17:00 · Laura Restrepo",
                tone: "warn",
                chip: "Vence hoy",
                id: "r3",
              },
              {
                num: "E-2026-4824",
                title: "Posible duplicado de E-2026-4798",
                sub: "Mismo NIT y mismo expediente contractual",
                tone: "ai",
                chip: "proboto",
                id: "r8",
              },
              {
                num: "E-2026-4821",
                title: "Licencia de construcción sin inspección agendada",
                sub: "Vence hoy 18:00 · Inspección de Planeación",
                tone: "warn",
                chip: "Vence hoy",
                id: "r1",
              },
            ]
              .map(
                (x) => `
              <button class="list-item" data-act="open-record" data-id="${x.id}">
                <span class="chip chip--${x.tone}" style="margin-top:1px">${x.chip}</span>
                <span style="min-width:0;flex:1">
                  <span style="display:block;font-weight:600;line-height:1.4">${esc(x.title)}</span>
                  <span style="display:block;font-size:11.5px;color:var(--db-ink-3);margin-top:2px">
                    <span class="mono">${x.num}</span> · ${esc(x.sub)}
                  </span>
                </span>
                ${icon("right", "faint")}
              </button>`,
              )
              .join("")}
          </div>
          <div class="card__foot">
            ${icon("info")} El escritorio no ordena por llegada: primero lo que tiene consecuencia legal.
          </div>
        </section>

        <div style="display:flex;flex-direction:column;gap:var(--db-space-4)">
          <section class="card">
            <div class="card__head">
              <div class="card__title">${icon("bell")} Alertas de hoy</div>
              <div class="spacer"></div>
              <button class="btn btn--sm btn--ghost" data-act="go" data-route="alertas">Todas ${icon("right")}</button>
            </div>
            <div class="list">
              ${DB.alertGroups[0].items
                .concat(DB.alertGroups[1].items)
                .map(
                  (a) => `
                <div class="list-item" style="align-items:center">
                  <span class="dot--${a.tone === "danger" ? "off" : "idle"}" style="background:${
                    a.tone === "danger" ? "var(--db-danger)" : "var(--db-warn)"
                  };margin-top:6px"></span>
                  <span style="min-width:0;flex:1">
                    <span style="display:block;line-height:1.4">${esc(a.title)}</span>
                    <span style="display:block;font-size:11.5px;color:var(--db-ink-3);margin-top:2px">${esc(a.detail)}</span>
                  </span>
                </div>`,
                )
                .join("")}
            </div>
          </section>

          <section class="card">
            <div class="card__head"><div class="card__title">${icon("zap")} Accesos</div></div>
            <div class="quick">
              ${[
                ["documentos", "radicar", "file", "Radicar documento"],
                ["documentos", "expedientes", "folder", "Expedientes"],
                ["distribucion", "envios", "truck", "Preparar remesa"],
                ["alertas", "reglas", "sliders", "Reglas de alerta"],
              ]
                .map(
                  ([route, tab, ic, label]) => `
                <button class="quick__item" data-act="go" data-route="${route}" data-tab="${tab}">
                  ${icon(ic)} ${esc(label)}
                </button>`,
                )
                .join("")}
            </div>
          </section>
        </div>
      </div>

      <section class="card" style="margin-top:var(--db-space-4)">
        <div class="card__head">
          <div class="card__title">${icon("undo")} Actividad reciente</div>
          <div class="card__sub" style="margin:0 0 0 auto">Todo acto reversible se puede deshacer</div>
          <button class="btn btn--sm btn--ghost" data-act="go" data-route="actividad">Ver libro ${icon("right")}</button>
        </div>
        <div class="card__body" style="padding-block:var(--db-space-2)">
          ${DB.activity
            .slice(0, 4)
            .map(
              (l) => `
            <div class="row-flex" style="padding:8px 0;border-bottom:1px solid var(--db-line-soft);gap:10px">
              <span class="ledger__badge ledger__badge--${
                l.cls === "A" ? "undo" : l.cls === "B" ? "revert" : "final"
              }">${icon(l.icon)}</span>
              <span style="flex:1;min-width:0;font-size:var(--db-text-sm)">${esc(l.title)}
                <span style="display:block;font-size:11px;color:var(--db-ink-3)">${esc(l.meta)}</span>
              </span>
              ${l.undoable ? `<button class="btn btn--sm" data-act="undo-entry" data-id="${l.id}">${icon("undo")} Deshacer</button>` : `<span class="chip chip--outline">${l.cls === "B" ? "Requiere reversión" : "No reversible"}</span>`}
            </div>`,
            )
            .join("")}
        </div>
      </section>
    </div>`;
  };

  /* ==========================================================================
     DOCUMENTOS — contenedor con pestañas
     ========================================================================== */
  V.documentos = function (s) {
    const tab = s.tab || "bandeja";
    const body =
      tab === "radicar"
        ? V.radicar(s)
        : tab === "expedientes"
          ? V.expedientes(s)
          : tab === "busqueda"
            ? V.busqueda(s)
            : V.bandeja(s);

    return `
    <div class="page">
      ${pageHead({
        eyebrow: "Documentos",
        title:
          tab === "radicar"
            ? "Radicar"
            : tab === "expedientes"
              ? "Expedientes"
              : tab === "busqueda"
                ? "Buscar"
                : "Bandeja",
        sub:
          tab === "bandeja"
            ? "Todo lo que espera una decisión tuya o de tu dependencia. La lista y el detalle dicen dónde está el radicado ahora."
            : tab === "radicar"
              ? "Tres pasos. proboto propone; el número lo gastas tú."
              : tab === "expedientes"
                ? "Agrupaciones con serie, retención y estado de publicación."
                : "Buscar, preguntar y encontrar por significado, en la misma caja.",
        actions:
          tab === "bandeja"
            ? `<button class="btn" data-act="bulk">${icon("filter")} Filtros</button>
               <button class="btn btn--primary" data-act="go" data-route="documentos" data-tab="radicar">${icon("plus")} Radicar</button>`
            : tab === "radicar"
              ? `<button class="btn" data-act="toast" data-msg="Borrador guardado">${icon("file")} Guardar borrador</button>`
              : tab === "expedientes"
                ? `<button class="btn btn--primary" data-act="toast" data-msg="Asistente de expediente abierto">${icon("plus")} Nuevo expediente</button>`
                : "",
      })}
      ${tabs(
        [
          { id: "bandeja", label: "Bandeja", count: 6 },
          { id: "radicar", label: "Radicar" },
          { id: "expedientes", label: "Expedientes", count: 148 },
          { id: "busqueda", label: "Buscar" },
        ],
        tab,
        "doc-tab",
      )}
      ${body}
    </div>`;
  };

  /* --------------------------------------------------------------------------
     BANDEJA
     ----------------------------------------------------------------------- */
  V.bandeja = function (s) {
    const sel = s.selected || new Set();
    const active = s.activeRecord || "r1";
    const list = DB.records.filter((r) => r.status !== "closed");

    return `
    <div class="split">
      <div class="inbox">
        <div class="inbox__tools">
          <input class="input inbox__search" placeholder="Filtrar por asunto, número o remitente…"
                 data-act="noop" aria-label="Filtrar la bandeja">
          <button class="btn btn--sm btn--icon" data-act="toast" data-msg="Filtros avanzados" title="Filtros">${icon("filter")}</button>
          <button class="btn btn--sm btn--icon" data-act="toast" data-msg="Orden: por consecuencia" title="Ordenar">${icon("sort")}</button>
        </div>
        ${
          sel.size
            ? `<div style="padding:8px 8px 0">
                 <div class="bulkbar">
                   <b>${sel.size}</b> seleccionado${sel.size > 1 ? "s" : ""}
                   <div class="spacer"></div>
                   <button class="btn btn--sm" data-act="bulk-assign">${icon("user")} Asignar</button>
                   <button class="btn btn--sm" data-act="bulk-tag">${icon("tag")} Etiquetar</button>
                   <button class="btn btn--sm" data-act="bulk-archive">${icon("archive")} Archivar</button>
                   <button class="btn btn--sm btn--ghost" data-act="clear-selection">${icon("x")}</button>
                 </div>
               </div>`
            : ""
        }
        <div class="inbox__list">
          ${list
            .map((r) => {
              const isSel = sel.has(r.id);
              const isActive = r.id === active;
              return `
            <div class="mail ${isActive ? "is-active" : ""} ${
              r.unread ? "is-unread" : ""
            }" data-act="open-record" data-id="${r.id}" role="button" tabindex="0">
              <span class="mail__check check" data-act="noop">
                <input type="checkbox" ${isSel ? "checked" : ""} data-act="toggle-select"
                       data-id="${r.id}" aria-label="Seleccionar ${r.num}">
              </span>
              <span style="min-width:0">
                <span class="mail__subject">${esc(r.subject)}</span>
                <span class="mail__meta">
                  <span class="mono">${r.num}</span>
                  <span class="sep">·</span>
                  <span>${esc(r.from)}</span>
                  <span class="sep">·</span>
                  <span>${esc(r.current)}</span>
                  ${r.due.startsWith("Venció") ? `<span class="chip chip--danger" title="${esc(r.due)}">${icon("alert")} Vencido</span>` : ""}
                </span>
              </span>
              <span class="mail__right">
                <span class="mail__time">${esc(r.received.split(" · ")[0])}</span>
                ${r.ai ? `<span class="mail__ai">${icon("sparkles")} IA</span>` : ""}
                <span class="chip chip--${r.statusChip}">${esc(r.statusLabel)}</span>
              </span>
            </div>`;
            })
            .join("")}
        </div>
        <div class="card__foot" style="justify-content:space-between">
          <span>${list.length} radicados activos</span>
          <span>${icon("info")} Los cerrados viven en el histórico</span>
        </div>
      </div>

      <div id="record-detail">${V.recordDetail(s)}</div>
    </div>`;
  };

  /* --------------------------------------------------------------------------
     DETALLE DEL RADICADO
     ----------------------------------------------------------------------- */
  V.recordDetail = function (s) {
    const id = s.activeRecord || "r1";
    const r = DB.records.find((x) => x.id === id) || DB.records[0];
    const tab = s.recordTab || "resumen";
    const sug = DB.triage[r.id];
    const ev = DB.evidence[r.id] || [];

    const suggested = s.appliedSuggestions && s.appliedSuggestions.has(r.id);

    const body = (() => {
      if (tab === "anexos") {
        return `
          <div style="display:flex;flex-direction:column;gap:6px">
            ${[
              ["PDF", "Solicitud de licencia — formulario FP-04.pdf", "1,2 MB · 4 págs"],
              ["PDF", "Certificado de tradición y libertad.pdf", "860 KB · 2 págs"],
              ["JPG", "Registro fotográfico del predio.jpg", "2,4 MB"],
              ["PDF", "Plano arquitectónico — planta baja.pdf", "3,1 MB"],
            ]
              .map(
                ([t, n, m]) => `
              <div class="attach">
                <span class="attach__icon">${t}</span>
                <span class="attach__name"><b style="font-weight:500">${esc(n)}</b>
                  <span style="display:block;font-size:11px;color:var(--db-ink-3)">${esc(m)} · SHA-256 verificado</span>
                </span>
                <button class="btn btn--sm btn--ghost" data-act="toast" data-msg="Vista previa del anexo">${icon("eye")}</button>
                <button class="btn btn--sm btn--ghost" data-act="toast" data-msg="Descarga iniciada">${icon("download")}</button>
              </div>`,
              )
              .join("")}
          </div>`;
      }
      if (tab === "flujo") {
        return `
          <ol class="timeline">
            <li class="tl-item tl-item--done">
              <div class="tl-item__title">Radicado en la ventanilla única en línea</div>
              <div class="tl-item__meta">3 nov 2026, 08:14 · ${r.channel} · código de verificación emitido</div>
            </li>
            <li class="tl-item tl-item--done">
              <div class="tl-item__title">Repartido a Planeación · Inspección</div>
              <div class="tl-item__meta">3 nov 2026, 08:20 · regla «licencias urbanísticas»</div>
            </li>
            <li class="tl-item tl-item--now">
              <div class="tl-item__title">Inspección de Planeación — en tu bandeja</div>
              <div class="tl-item__meta">Responsable actual: ${esc(r.owner)} · vence ${esc(r.due)}</div>
              <div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">
                <button class="btn btn--sm btn--primary" data-act="preflight" data-kind="tramitar">${icon("check")} Marcar en trámite</button>
                <button class="btn btn--sm" data-act="preflight" data-kind="devolver">${icon("undo")} Devolver</button>
              </div>
            </li>
            <li class="tl-item">
              <div class="tl-item__title">Cierre del trámite</div>
              <div class="tl-item__meta">Requiere motivo de cierre · no reversible</div>
            </li>
          </ol>`;
      }
      if (tab === "historial") {
        return `
          <div class="ledger">
            ${[
              ["check", "A", "Marcaste el radicado como leído", "Hoy 08:41 · Tú"],
              ["sparkles", "A", "proboto propuso tipificación y reparto", "Hoy 08:15 · agente · sin efecto por sí sola"],
              ["file", "A", "Se registró el radicado y se emitió el acuse", "Hoy 08:14 · ventanilla única"],
              ["link", "A", "Se verificó la firma del formulario adjunto", "Hoy 08:14 · sistema"],
            ]
              .map(
                ([ic, cls, t, m]) => `
              <div class="ledger__item">
                <span class="ledger__badge ledger__badge--${cls === "A" ? "undo" : "final"}">${icon(ic)}</span>
                <div>
                  <div class="ledger__title">${esc(t)}</div>
                  <div class="ledger__meta">${esc(m)}</div>
                </div>
                <span class="chip chip--outline">${cls === "A" ? "Reversible" : "Registro"}</span>
              </div>`,
              )
              .join("")}
          </div>`;
      }
      return `
        ${
          sug
            ? `<div class="ai-summary">
                 <div class="ai-summary__head">
                   ${icon("sparkles")} proboto sugiere · ${esc(sug.kind)}
                   <div class="spacer"></div>
                   <span class="chip chip--ai">${sug.confidence} fuentes</span>
                 </div>
                 <div style="font-weight:600;margin-bottom:4px">${esc(sug.title)}</div>
                 <div style="color:var(--db-ink-2);font-size:var(--db-text-xs);line-height:1.55">
                   ${esc(sug.why)} ${sug.confidence >= 2 ? cite(1) : ""}${cite(2)}
                 </div>
                 <dl class="sugg__fields" style="margin-top:9px">
                   ${sug.fields
                     .map(
                       ([k, v]) =>
                         `<div class="sugg__field"><dt>${esc(k)}</dt><dd>${esc(v)}</dd></div>`,
                     )
                     .join("")}
                 </dl>
                 <div class="sugg__actions" style="padding:9px 0 0">
                   <button class="btn btn--sm btn--ai" data-act="apply-suggestion" data-id="${r.id}"
                     ${suggested ? "disabled" : ""}>
                     ${icon("check")} ${suggested ? "Aplicada" : "Aplicar sugerencia"}
                   </button>
                   <button class="btn btn--sm" data-act="reject-suggestion" data-id="${r.id}">${icon("x")} Descartar</button>
                   <button class="btn btn--sm btn--ghost" data-act="agent-open" data-agent-tab="chat">${icon("sparkles")} Preguntar por este documento</button>
                 </div>
               </div>`
            : `<div class="ai-summary">
                 <div class="ai-summary__head">${icon("sparkles")} proboto · resumen</div>
                 <ul>
                   <li>Queja por ruido de establecimiento comercial. Competencia: Inspección de Policía.</li>
                   <li>El radicado cita dos antecedentes del mismo local en 2025.</li>
                   <li>No tiene anexos probatorios: conviene pedirlos antes de la visita.</li>
                 </ul>
                 <div class="msg__foot">${cite(1)}${cite(2)} <span>· lectura, sin efecto</span></div>
               </div>`
        }

        <dl class="dl">
          <div><dt>Remitente</dt><dd>${esc(r.from)} <span class="faint tiny">· ${esc(r.fromType)}</span></dd></div>
          <div><dt>Canal</dt><dd>${esc(r.channel)}</dd></div>
          <div><dt>Dependencia de radicación</dt><dd>${esc(r.dept)}</dd></div>
          <div><dt>Ubicación actual</dt><dd>${esc(r.current)}</dd></div>
          <div><dt>Responsable</dt><dd>${esc(r.owner)}</dd></div>
          <div><dt>Vence</dt><dd><span class="chip chip--${r.dueTone}">${esc(r.due)}</span></dd></div>
          <div><dt>Serie TRD</dt><dd>${esc(r.serie)}</dd></div>
          <div><dt>Retención</dt><dd>${esc(r.retention)}</dd></div>
        </dl>

        <div class="section-title" style="margin-top:var(--db-space-5);font-size:var(--db-text-base)">${icon("clipboard")} Flujo</div>
        <ol class="timeline" style="margin-top:var(--db-space-3)">
          <li class="tl-item tl-item--done">
            <div class="tl-item__title">Radicado en ${esc(r.channel)}</div>
            <div class="tl-item__meta">${esc(r.receivedLong)}</div>
          </li>
          <li class="tl-item tl-item--now">
            <div class="tl-item__title">${esc(r.current)}</div>
            <div class="tl-item__meta">Responsable: ${esc(r.owner)} · vence ${esc(r.due)}</div>
          </li>
          <li class="tl-item">
            <div class="tl-item__title">Cierre del trámite</div>
            <div class="tl-item__meta">${icon("lock")} No reversible · exige motivo de cierre</div>
          </li>
        </ol>`;
    })();

    return `
      <div class="card" style="overflow:visible">
        <div class="record__head">
          <div class="record__top">
            <span class="record__id">${r.num}</span>
            <span class="chip chip--${r.statusChip}">${esc(r.statusLabel)}</span>
            ${r.priority === "Alta" ? `<span class="chip chip--warn">Prioridad alta</span>` : ""}
            ${r.ai ? `<span class="chip chip--ai">${icon("sparkles")} Con sugerencia</span>` : ""}
            <div class="spacer"></div>
            <button class="icon-btn" title="Más acciones" data-act="toast" data-msg="Menú de acciones">${icon("more")}</button>
          </div>
          <h2 class="record__title">${esc(r.subject)}</h2>
          <div class="record__chips">
            <span class="chip chip--outline">${esc(r.type)}</span>
            <span class="chip chip--outline">${icon("building")} ${esc(r.dept)}</span>
            <span class="chip chip--outline">${icon("clip")} ${r.attachments} anexos</span>
            <span class="chip chip--outline">${icon("file")} ${r.folios} folios</span>
          </div>
        </div>

        <div class="record__bar">
          <div class="btn-split">
            <button class="btn btn--primary btn--sm" data-act="preflight" data-kind="tramitar">
              ${icon("check")} Marcar en trámite
            </button>
            <button class="btn btn--primary btn--sm" data-act="toast" data-msg="Más acciones del flujo" aria-label="Más acciones">${icon("down")}</button>
          </div>
          <button class="btn btn--sm" data-act="preflight" data-kind="devolver">${icon("undo")} Devolver</button>
          <button class="btn btn--sm" data-act="toast" data-msg="Asignar a otra persona">${icon("user")} Asignar</button>
          <button class="btn btn--sm btn--ghost" data-act="go" data-route="documentos" data-tab="expedientes">${icon("folder")} Al expediente</button>
          <div class="spacer"></div>
          <span class="chip chip--outline" title="Esta acción no se puede deshacer">
            ${icon("lock")} Cerrar es irreversible
          </span>
        </div>

        <div class="rec-tabs">
          ${[
            ["resumen", "Resumen"],
            ["anexos", "Anexos · " + r.attachments],
            ["flujo", "Flujo"],
            ["historial", "Historial"],
          ]
            .map(
              ([id, label]) =>
                `<button class="tab" aria-selected="${tab === id}" data-act="record-tab" data-tab="${id}">${esc(label)}</button>`,
            )
            .join("")}
        </div>

        <div class="record__body">${body}</div>
      </div>`;
  };

  /* --------------------------------------------------------------------------
     RADICAR — asistente en 3 pasos
     ----------------------------------------------------------------------- */
  V.radicar = function (s) {
    const step = s.wizardStep || 1;
    const stepCls = (n) => (n < step ? "step step--done" : n === step ? "step step--now" : "step");
    const stepIcon = (n) => (n < step ? icon("check") : `<span class="step__n">${n}</span>`);

    const body =
      step === 1
        ? `
        <div class="wizard__card card">
          <div class="wizard__title">¿Cómo llegó y de quién?</div>
          <p class="wizard__sub">El canal determina el acuse y el plazo. Si el remitente es una entidad, cambian los campos de notificación.</p>
          <div class="form-grid">
            <div class="field span-2">
              <label class="field__label" for="canal">Canal de entrada</label>
              <select class="select" id="canal">
                <option>Ventanilla única en línea</option>
                <option>Presencial</option>
                <option>Correo institucional</option>
                <option>Mensajería postal</option>
              </select>
            </div>
            <div class="field span-2">
              <span class="field__label">Tipo de remitente</span>
              <label class="check"><input type="radio" name="tipo" checked> Persona natural</label>
              <label class="check"><input type="radio" name="tipo"> Persona jurídica o entidad</label>
            </div>
            <div class="field">
              <label class="field__label" for="nombre">Nombre completo <span class="req">*</span></label>
              <input class="input" id="nombre" value="María Fernanda Ospina" data-act="noop">
            </div>
            <div class="field">
              <label class="field__label" for="doc">Documento de identidad <span class="req">*</span></label>
              <input class="input" id="doc" value="CC 52.884.310" data-act="noop">
            </div>
            <div class="field">
              <label class="field__label" for="correo">Correo de notificación <span class="req">*</span></label>
              <input class="input" id="correo" value="mf.ospina@correo.com" data-act="noop">
            </div>
            <div class="field">
              <label class="field__label" for="tel">Teléfono</label>
              <input class="input" id="tel" value="310 884 2210" data-act="noop">
            </div>
            <div class="field span-2">
              <label class="field__label" for="dir">Dirección de notificación</label>
              <input class="input" id="dir" value="Calle 12 #4-33, barrio La Esperanza" data-act="noop">
            </div>
          </div>
          <div class="card__foot" style="margin:var(--db-space-5) calc(var(--db-space-5) * -1) calc(var(--db-space-5) * -1);border-radius:0 0 var(--db-radius-md) var(--db-radius-md);justify-content:flex-end">
            <button class="btn" data-act="toast" data-msg="Borrador guardado">Guardar</button>
            <button class="btn btn--primary" data-act="wizard-next">Continuar ${icon("right")}</button>
          </div>
        </div>`
        : step === 2
          ? `
        <div class="wizard__card card">
          <div class="wizard__title">El documento</div>
          <p class="wizard__sub">Adjunta el soporte. proboto leerá el texto para proponer la tipificación, pero no aplica nada sin tu visto bueno.</p>
          <div class="form-grid">
            <div class="field">
              <label class="field__label" for="tipo">Tipo documental</label>
              <select class="select" id="tipo">
                <option>Solicitud — licencia urbanística</option>
                <option>Derecho de petición</option>
                <option>Queja o reclamo</option>
                <option>Comunicación oficial</option>
              </select>
            </div>
            <div class="field">
              <label class="field__label" for="clasif">Clasificación de acceso</label>
              <select class="select" id="clasif">
                <option>Pública</option>
                <option>Reservada</option>
                <option>Clasificada</option>
              </select>
            </div>
            <div class="field span-2">
              <label class="field__label" for="asunto">Asunto <span class="req">*</span></label>
              <input class="input" id="asunto" value="Solicitud de licencia de construcción — predio Calle 12 #4-33" data-act="noop">
            </div>
            <div class="field span-2">
              <label class="field__label" for="cuerpo">Cuerpo o resumen</label>
              <textarea class="textarea" id="cuerpo" data-act="noop">Solicito licencia de construcción para el predio ubicado en la Calle 12 #4-33, área 180 m², uso vivienda.</textarea>
            </div>
            <div class="span-2">
              <div class="dropzone">
                <div class="dropzone__icon">${icon("upload")}</div>
                <div class="dropzone__title">Arrastra los anexos o elige un archivo</div>
                <div class="dropzone__sub">PDF, JPG, PNG o TIFF · hasta 20 MB por archivo · se verifica el contenido, no la extensión</div>
              </div>
            </div>
          </div>
          <div class="card__foot" style="margin:var(--db-space-5) calc(var(--db-space-5) * -1) calc(var(--db-space-5) * -1);border-radius:0 0 var(--db-radius-md) var(--db-radius-md)">
            <button class="btn btn--ghost" data-act="wizard-back">${icon("left")} Atrás</button>
            <div class="spacer"></div>
            <button class="btn" data-act="toast" data-msg="Borrador guardado">Guardar</button>
            <button class="btn btn--primary" data-act="wizard-next">Continuar ${icon("right")}</button>
          </div>
        </div>`
          : `
        <div class="wizard__card card">
          <div class="wizard__title">Clasificación, destino y plazo</div>
          <p class="wizard__sub">Aquí se amarra la serie documental, la dependencia que responde y el plazo que genera la alerta.</p>
          <div class="form-grid">
            <div class="field field--ai span-2">
              <label class="field__label" for="serie">Serie TRD
                <span class="field__ai-tag">proboto</span>
              </label>
              <select class="select" id="serie">
                <option>Solicitudes de licencia urbanística · 5/15 años</option>
              </select>
              <span class="field__hint">Coincide con el 92 % de las solicitudes del formato FP-04.</span>
            </div>
            <div class="field field--ai">
              <label class="field__label" for="dependencia">Dependencia que responde
                <span class="field__ai-tag">proboto</span>
              </label>
              <select class="select" id="dependencia">
                <option>Planeación · Inspección</option>
              </select>
            </div>
            <div class="field">
              <label class="field__label" for="plazo">Plazo</label>
              <select class="select" id="plazo">
                <option>15 días hábiles — vence 24 nov 2026</option>
                <option>10 días hábiles</option>
                <option>Sin plazo legal</option>
              </select>
            </div>
            <div class="field span-2">
              <span class="field__label">Alertas</span>
              <label class="check"><input type="checkbox" checked> Avisar al responsable 2 días antes del vencimiento</label>
              <label class="check"><input type="checkbox" checked> Escalar a la jefatura si no hay movimiento en 3 días</label>
            </div>
          </div>
          <div class="card__foot" style="margin:var(--db-space-5) calc(var(--db-space-5) * -1) calc(var(--db-space-5) * -1);border-radius:0 0 var(--db-radius-md) var(--db-radius-md)">
            <button class="btn btn--ghost" data-act="wizard-back">${icon("left")} Atrás</button>
            <div class="spacer"></div>
            <button class="btn" data-act="toast" data-msg="Borrador guardado">Guardar borrador</button>
            <button class="btn btn--primary" data-act="radicar-submit">${icon("check")} Radicar y emitir acuse</button>
          </div>
        </div>`;

    return `
    <div class="wizard">
      <div>
        <div class="steps">
          <div class="${stepCls(1)}">${stepIcon(1)} Entrada</div>
          <div class="${stepCls(2)}">${stepIcon(2)} Documento</div>
          <div class="${stepCls(3)}">${stepIcon(3)} Clasificación</div>
        </div>
        ${body}
      </div>
      <aside class="wizard__aside">
        <div class="summary-card">
          <div class="summary-card__head">${icon("file")} Resumen del radicado</div>
          <div class="summary-card__body">
            <div class="summary-row"><dt>Tipo</dt><dd>Entrada</dd></div>
            <div class="summary-row"><dt>Remitente</dt><dd>María Fernanda Ospina</dd></div>
            <div class="summary-row"><dt>Canal</dt><dd>Ventanilla en línea</dd></div>
            <div class="summary-row"><dt>Serie</dt><dd>Solicitudes de licencia urbanística</dd></div>
            <div class="summary-row"><dt>Destino</dt><dd>Planeación · Inspección</dd></div>
            <div class="summary-row"><dt>Vence</dt><dd><span class="chip chip--warn">24 nov 2026</span></dd></div>
          </div>
          <div class="summary-card__foot">
            ${icon("info")} <span class="tiny muted">El número se asigna al radicar, no antes.</span>
          </div>
        </div>

        <div class="card card--ai card--pad">
          <div class="ledger__meta" style="margin:0 0 6px">
            ${icon("sparkles")} <b style="color:var(--db-ai-ink)">proboto</b> · Nivel 2
          </div>
          <div style="font-size:var(--db-text-sm);line-height:1.55">
            Ya leí el formulario adjunto. La tipificación y el reparto están precargados;
            revisa y cambia lo que haga falta.
          </div>
          <div style="display:flex;gap:6px;margin-top:10px;flex-wrap:wrap">
            <span class="chip chip--ai">3 fuentes</span>
            <span class="chip chip--outline">sin efecto automático</span>
          </div>
        </div>

        <div class="card card--pad card--sunken">
          <div class="tiny strong" style="margin-bottom:6px">${icon("lock")} Lo que no se puede deshacer</div>
          <p class="tiny muted" style="line-height:1.55;margin:0">
            Radicar consume el número y lo deja en el consecutivo. Si te equivocas,
            la ruta es una <b>solicitud de anulación</b> que decide otra persona; no hay
            «deshacer» sobre un radicado emitido.
          </p>
        </div>
      </aside>
    </div>`;
  };
})(window.VIEWS);
