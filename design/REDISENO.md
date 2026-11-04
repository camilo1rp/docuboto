# Docuboto 3.0 — UI/UX redesign

**Status:** proposal for review · **Date:** 2026-11-03
**Identity:** Proboto brand applied (azul `#095ECE`, cian `#00D4E0`, noche `#0A1122`; logo mark from proboto.co)
**Scope:** interface layer only. No backend contract changes are required to start; §10 sequences the work.
**Artifacts:** [`prototipo/index.html`](prototipo/index.html) (clickable, self-contained) · [`prototipo/css/tokens.css`](prototipo/css/tokens.css) (drop-in token layer) · this document.

> UI copy is Spanish, matching the deployed product. This document is in English for review.

---

## 1. What is actually wrong today

The current frontend is not badly built — it is **well governed and badly shaped**. Four problems come from its own documents and code, not from taste:

**1.1 The navigation is a permission audit, not a workspace.**
`frontend/src/lib/utils/navItems.js` is a flat list of **20 destinations**, ordered by when they were added, surfaced only if the JWT carries the matching permission. A small team sees `Bandeja`, `Radicar`, `Expedientes`, `Búsqueda`, `Reportes`, `Correo`, `Borradores`, `Envíos`, `Firmas`, `Anular`, `Desclasificar`, `Perfil`, `Archivo físico`, `Transferencias`, `Administración`, `Plantillas`, `Flujos`, `Claves`, `Preservación`. There is no hierarchy and no notion of "this installation is small". Adding a feature always makes the sidebar longer.

**1.2 The product cannot be small or strict at the same time.**
Every capability is either built or not. There is no way to say "this entity uses Docuboto with three people and no signature chain" versus "this entity is audited externally and needs dual control on irreversible acts". The only per-entity switch that exists is `tenants.asistente_habilitado` (ADR-037) — one boolean for the whole intelligence layer.

**1.3 "No se puede deshacer" is used for things that can be undone.**
The evidence is concrete: `RevertirModal.svelte` labels a workflow rollback — a **compensating act** — as irreversible, while `Recibir` a transfer (`transferencias/+page.svelte:257–262`) and `Cerrar` an expediente genuinely are. There are **five different confirmation mechanisms** for destructive acts (a checkbox, typing the batch code, typing the radicado number, a fixed phrase, a minimum-length reason). The user cannot learn the grammar because there isn't one.

**1.4 The intelligence layer has no place to live.**
ADR-046 removed the conversational assistant and `AssistantDock.svelte`. PPI/`proboto-agent` (ADR-045) returns with read → triage → drafts, and the frontend has no surface for *proposals*: only forms. Bolting a chat bubble onto a 20-item sidebar would repeat the mistake that was just retired.

---

## 2. Six principles

| # | Principle | Consequence in the UI |
|---|---|---|
| 1 | **The agent proposes; the person decides.** | No write originates from the agent. Every accepted suggestion is applied *by* the user's identity and carries the suggestion id into audit. This mirrors the intelligence plan's rule 3, and the UI says so out loud: *"proboto propone, no ejecuta."* |
| 2 | **Reversibility is earned per act, not inherited per button.** | Class A gets an undo bar, B gets "Revertir", C gets a lock. A destructive-looking action that is compensable is *never* labelled irreversible. |
| 3 | **Progressive disclosure, not permission diets.** | The mode decides what exists on screen; permissions decide who can use it. Two independent axes, never conflated. |
| 4 | **One control per intent.** | Search, ask and act collapse into one command bar (⌘K) with three modes — not three boxes in three places. |
| 5 | **Evidence over confidence theatre.** | "3 fuentes" and a literal quote, not "92 % confident". A percentage invites trust it hasn't earned; a citation can be checked. |
| 6 | **Silence is a state.** | With intelligence off, the panel is not empty or absent: it explains what would be here and where to turn it on. |

---

## 3. Information architecture

### 3.1 From 20 destinations to 4 + 3

The four work destinations are the three jobs you named plus the day view. Everything else becomes a tab inside one of them.

| Today (`NAV_ITEMS`) | 3.0 destination |
|---|---|
| `/dashboard` | **Escritorio** (unchanged name in spirit; now "what needs a decision") |
| `/bandeja`, `/radicar`, `/borradores`, `/expedientes`, `/busqueda`, `/archivo-fisico`, `/transferencias` | **Documentos** → tabs: Bandeja · Radicar · Expedientes · Buscar |
| `/envios`, `/correo` | **Distribución** → tabs: Envíos · Correo · Canales |
| `/anulaciones`, `/desclasificaciones`, alertas de plazo, `/reportes`? | **Alertas** → tabs: Vencimientos · Reglas · Canales |
| `/admin/*` (plantillas, flujos, claves, preservación, TRD/TVD, usuarios, grupos…), `/perfil` | **Ajustes** (foot of rail) → grouped settings |
| — (new) | **Capacidades** — the activation system, §5 |
| — (new) | **Inteligencia** — proboto configuration, §4.6 |
| — (new) | **Actividad** — the reversibility ledger, §6 |

`/reportes` and `/anulaciones` are the two debatable ones: reports are a *view over* documents (candidate for a tab in Documentos), and the two "resolver" queues are *work* (candidate for Alerts/Inbox as a "Decisiones" queue). Recommendation: keep `Resolver anulaciones` / `Resolver desclasificaciones` as **queues inside Alertas → Vencimientos**, rendered as alert groups of type "decisión pendiente". They are rare, consequential and time-bound — the definition of an alert, not of a document.

### 3.2 The shell

```
┌──────────┬────────────────────────────────────────────┬──────────────┐
│          │  topbar: entidad · ⌘K omni · tema · alertas  │              │
│  rail    ├────────────────────────────────────────────┤   proboto    │
│  236px   │                                            │   392px      │
│          │  page: título · acciones · tabs            │  Resumen     │
│ 4+3      │  contenido                                  │  Preguntar   │
│ destinos │                                            │  Actividad   │
│          │                                            │  composer    │
└──────────┴────────────────────────────────────────────┴──────────────┘
```

* **Rail** — 236 px, collapsible to 68 px. Two groups: *Trabajo* and *Gobierno*. Favourites remain (they were a good idea) as a starred section inside Documentos, not a separate block.
* **Topbar** — the context switcher (entity · dependency) is always visible because multi-tenancy and dependency switching are real ([`DependencySwitcher`](../frontend/src/lib/components/features/perfil/DependencySwitcher.svelte)).
* **Agent panel** — persistent, 392 px, three tabs. It is part of the layout, not an overlay: the point is that the agent *sees what you see*. It collapses to a rail icon.
* **Command bar** — opens with `⌘K` / `Ctrl+K`, or by clicking the omni input. Three modes: **Preguntar** (answer + citations), **Buscar** (documents, people, numbers), **Hacer** (actions, filtered by permission). This is the single most important AI-first move: it makes "ask" the default verb rather than a special screen.

### 3.3 Keyboard

`⌘K` command bar · `Esc` close overlay · `⌘Z` undo last class-A act · `⌘↵` run with agent (from command bar) · `/` focus search (kept from today). Every overlay traps focus and restores it — the existing tests in `AppLayout.test.js` stay valid.

---

## 4. The AI-first surface

### 4.1 Where the agent appears

| Surface | What it does | What it never does |
|---|---|---|
| **Command bar** | Answers with citations, finds documents, offers actions | Answer without a source when it read documents |
| **Agent panel · Resumen** | Daily briefing + proposal cards with Accept/Reject | Apply anything |
| **Agent panel · Preguntar** | Scoped chat: *mi dependencia* / *este radicado* | Read a level the entity didn't enable |
| **Record detail** | Inline suggestion for tipificación, plazo, reparto, duplicates | Pre-fill a field as if it were the user's choice |
| **Radicar** | Pre-fills tipo/serie/dependencia, marked with the cyan `PROBOTO` tag | Consume the number |
| **Borradores** (D3) | Creates a *borrador de Docuboto* in the user's name | Sign or radicar |
| **Inteligencia** | Level, scopes, provider declaration, agent ledger | Change any of it without an admin |

### 4.2 Proposal card anatomy

The proposal card is the core new component. It carries six things, in this order:

1. **What kind** — `TIPIFICACIÓN`, `REPARTO`, `DUPLICADO`, `RIESGO`, `PLAZO`. A closed vocabulary makes the card scannable.
2. **Claim** — one sentence, in the domain's words (`"Solicitud de licencia de construcción"`), not "I think this is…".
3. **Why** — the reasoning in one or two lines, with citations.
4. **Structured fields** — the exact fields that would change, as a `dt/dd` list. This is the diff preview, before accepting.
5. **Evidence strength** — `3 fuentes` + a source count, plus an expandable literal quote. Not a confidence percentage.
6. **Actions** — `Aplicar` / `Descartar` / `Preguntar por este documento`. Accept and reject are equally available; the agent's output is never the default focus.

### 4.3 The trust line

Every agent surface ends with a one-line contract, and it changes with the level:

* Nivel 1: *"Solo lee. No escribe nada."*
* Nivel 2: *"proboto propone, no ejecuta. Cada aplicación pasa por tu permiso y queda en el libro de actos."*
* Nivel 3: *"Crea borradores a tu nombre. Radicar y firmar siguen siendo tuyos."*

This is not decoration. It answers the question a records officer actually asks — *who is responsible for this document* — every time, in the same place.

### 4.4 Failure and absence states (design these, don't omit them)

| State | What the panel shows |
|---|---|
| Intelligence off | A single card: what proboto would do, and `Activar en Capacidades →`. Not an empty panel. |
| Level enabled, but the document's level is not authorized | `"Este radicado es reservado. La entidad no ha habilitado ese nivel para proboto."` + link to Inteligencia. Silence would look like a bug. |
| Provider unreachable | Banner with `Reintentar`; the document, search and radicar keep working. The panel degrades; the product doesn't. |
| No evidence found | `"No encontré antecedentes. Eso no significa que no existan: puedo buscar en otros niveles si están habilitados."` — the same honesty the public *consulta* screen already practices. |

### 4.5 Mapping to the intelligence plan

| Plan phase | UI affordance | Gate |
|---|---|---|
| D1 read | Command bar answers, citations, `Preguntar` tab | `PERM_CONOCIMIENTO` level 1 + `asistente_habilitado` |
| D2 triage | Proposal cards, inline suggestions, Accept → applies with audit + undo | level 2 (edit) for accepting |
| D3 drafts | `Crear borrador` → opens `/borradores` with the cited draft | level 2; radicar stays manual |

The plan's rule "**lo aceptado lo ejecuta el funcionario**" becomes a visual rule: the Accept button is a *user* action (azul, never cyan). Cyan means "this came from proboto"; azul means "you did this". No exception.

### 4.6 The Inteligencia screen

One screen, four blocks:

1. **Ladder** — off / read / triage / drafts, each with what you gain, what gets written, and what it requires. Rungs above the current one are dimmed, not hidden: the admin needs to see the road.
2. **Scopes** — Pública (forced on) · Reservada · Clasificada, each with the legal requirement named (`declaración de transmisión`, `política publicada`, `revisión de seguridad`). This is ADR-037's asymmetry made visible.
3. **What proboto never does** — four lines, always visible, next to the switches.
4. **Agent ledger** — every proposal, its target, its outcome (applied/rejected/pending), and the person. This is the audit answer to "what has the robot been telling people".

---

## 5. The feature activation system

### 5.1 Two axes, never confused

```
                   persona (JWT permissions)  ×   entidad (capabilities)
                            │                            │
                    who may do this            what exists in this installation
```

`Capacidades` only moves the second axis. A capability that is on but for which the user lacks the permission still shows nothing — and the backend still returns 403. The UI is defence in depth, as it is today.

### 5.2 Three modes, as presets

| | **Esencial** | **Escala** | **Regulado** |
|---|---|---|---|
| For | 1–15 people, 1–3 dependencies | 15–300 people, 4+ dependencies | any size with external audit |
| Capabilities on | 5 | 14 | 25 |
| Irreversible acts | reason required | reason required | **reason + second person** |
| Audit | standard | standard | **immutable** |
| Retention | — | — | **TRD/TVD + WORM** |
| Signature | — | chain + visto bueno | **XAdES + TOTP** |
| Undo | everything class A | same | same, plus dual control on B where legal |

The modes are **cumulative presets, not cages**: any capability can be overridden, and the header then reads `Modo Escala · con ajustes propios` with a `Volver al preset` action.

### 5.3 The capability model

```js
{
  id: "worm",
  group: "Documento y archivo",
  name: "Preservación WORM",
  desc: "Objetos inmutables con retención legal forzada. No se puede revertir.",
  icon: "shield",
  tiers: { esencial: false, escala: false, regulado: true },  // default per mode
  deps: ["retencion"]                                         // must be on
}
```

Three rules the UI enforces:

1. **Enabling requires dependencies.** A switch whose deps are off is disabled and says which one it needs (`Requiere Retención y disposición final`). It never silently cascades — turning on a chain of five things from one click is how a small entity ends up in Regulado by accident.
2. **Disabling is blocked by dependents.** If something that is on depends on it, the switch refuses and names them (`Apagarla exige apagar antes 2 capacidades: Archivo físico, Transferencias`). Cascading off is destructive to someone's workflow; blocking with names teaches the graph.
3. **Off is not delete.** Copy states it, and the undo toast repeats it: *"no se borró ningún dato"*. When re-enabled, data is restored as it was — which is true because capabilities gate reads and rules, not storage.

### 5.4 Mode changes get a pre-flight, because they change the rules

Picking a mode opens a preview that lists: capabilities turned on, capabilities turned off (with "no data is deleted"), and the **rule deltas** — `los actos irreversibles pasan a exigir dos personas`, `la auditoría pasa a ser inalterable`, `las series TRD deben estar convalidadas antes de cerrar el primer expediente`. The confirmation checkbox is required, and the change is class A (undoable for 10 s) because it is configuration, not a legal act.

### 5.5 What is *not* a capability

The floor of the product, stated on the screen so nobody looks for the switch:

* Traceability of who did what.
* Tenant isolation.
* Server-side permission checks over UI visibility.

### 5.6 How this maps onto the existing backend

| Capability group | Existing implementation |
|---|---|
| Radicación, expedientes | `document-service`, `archive-service` |
| Firmas simple/certificada | `signature-service`, TOTP + PKI in `auth-service` |
| TRD/TVD, retención, WORM, legal hold | `archive-service`, `storage-service` (ADR-023/025/026/043) |
| Distribución | `notification-service`, postal operator, `/envios` |
| Alertas y SLA | `document-service` due dates (RF-RAD-04) + `notification-service` |
| Auditoría, doble control, clasificación | `auth-service`, ADR-039, audit log |
| Inteligencia | ADR-037 flag + PPI (ADR-045) |

The frontend does not invent state: it reads a **capabilities payload** (new `GET /config/capacidades`, or derived from `GET /auth/me` + tenant config during the transition) and renders from it. §10.4 lists the minimal backend addition.

---

## 6. Undo: the model the product is missing

### 6.1 Three classes, one grammar

| Class | Name in UI | Mechanism | Examples (verified in the codebase) |
|---|---|---|---|
| **A** | **Deshacer** | In-place restore. Undo bar for 10 s + `⌘Z`. Both the act and its reversal are written. | asignar a flujo (`AsignarModal`), etiquetar, archivar, marcar leído, corregir plazo (`canCorregirPlazo`), editar metadatos de expediente, silenciar alerta, apagar regla, encender capacidad, aplicar sugerencia de tipificación, retirar mi solicitud de desclasificación |
| **B** | **Revertir** | A compensating act that is itself a fact. Requires a reason; may require approval. | devolver a dependencia, trasladar, rollback de paso (`RevertirModal`, only on `pending`), retirar publicación, levantar retención legal, rechazar anulación / desclasificación, devolver préstamo, cambiar estado de un envío, anular lote de eliminación, nueva versión de TRD |
| **C** | **No reversible** | Cannot be undone. Corrected by a subsequent legal act. | radicar (número inmutable), finalizar trámite, aprobar anulación, firmar (individual o lote), cerrar expediente, recibir transferencia, convalidar/derogar TRD, registrar RUSD, ejecutar eliminación, emitir acta, restablecer TOTP |

### 6.2 Mechanics

**Class A — the undo bar.**
After the action, a bar appears at the bottom centre with a countdown ring, the act, and `Deshacer`. Up to three stack. `⌘Z` triggers the last one. When the countdown expires, the act is committed; the ledger keeps both entries either way. Bulk actions undo **as one unit** (`Deshacer 12 cambios`), which is the only acceptable behaviour after a bulk operation.

**Class B — "Revertir", a verb, not an eraser.**
No dialog before the act if the act is cheap (devolver, trasladar). A reason field, then the record shows *what the reversal will be*, not "are you sure". The ledger shows the original and the reversal chained. Copy rule: **never say "eliminar" when the system appends**. `Retirar publicación` is not `Borrar publicación`.

**Class C — no undo, by design, and it says why.**
The button carries a lock. The pre-flight shows: what changes, who is affected, what cannot be undone, **and the correct compensating path** (`Solicitar anulación`, `Dejar constancia`, `Nueva transferencia`). In Regulado, class C also requires a second person. This is the "constancia" modal in the prototype.

### 6.3 The confirmation ladder

Five mechanisms exist today for the same intent. Replace them with one ladder, keyed to class and blast radius:

| Level | When | Pattern |
|---|---|---|
| 0 | Class A | No dialog. Undo bar. |
| 1 | Class B, single record | Reason field in the pre-flight sheet. |
| 2 | Class B, bulk / public exposure | Pre-flight + reason + checkbox acknowledging the consequence. |
| 3 | Class C, single record | Pre-flight + reason + `no se puede deshacer` block + **the alternative path**. |
| 4 | Class C, catastrophic (eliminación, reasignación masiva) | Level 3 + type the identifier (batch code / radicado number) + dual control in Regulado. |

Typing a phrase survives only at level 4, where it belongs. `Revertir` moves from level 3 to level 1 — that alone removes the most misleading label in the product.

### 6.4 Naming rules (write these into the design system, not just the screens)

1. `Deshacer` only for class A.
2. `Revertir` only for class B.
3. `No reversible` / `queda constancia` for class C. Never "eliminar" for an append-only act.
4. A lock icon appears **only** on class C. It is a claim about the law, so it must be true.
5. When an action is unavailable, say *why* (already closed, retention until 2041, another person must approve) instead of hiding it silently — the current pattern of hiding controls by state is correct, but the reason must be discoverable.

### 6.5 Edge cases the design must cover

* **Partial bulk failure.** Report per row (`9 de 12`), offer undo of the 9, never claim success.
* **Concurrency.** If the record changed since the screen loaded, the pre-flight switches to a conflict state rather than overwriting.
* **Undo after navigation.** The bar survives route changes; `⌘Z` works anywhere.
* **Undo of an undone act.** Not offered. The ledger records the reversal; a second reversal is a new act.
* **Cross-session.** Only the ledger persists; the 10-second bar is session-scoped and says so (`Desde la propia pantalla`).

---

## 7. Visual system

### 7.1 The brand layer is Proboto's, adopted verbatim

The palette is taken from `proboto.co` exactly — same five values, same names in spirit:

| Token | proboto.co | Role in Docuboto |
|---|---|---|
| `--db-brand` | `--azul` `#095ECE` | Primary action, active navigation, links |
| `--db-ai` | `--cian` `#00D4E0` | The intelligence layer: avatar, proposals, AI-filled fields |
| `--db-ink` | `--noche` `#0A1122` | Text, and the dark theme canvas |
| `--db-canvas` | `--superficie` `#F0F5FC` | Page background |
| `--db-line` | `--linea` `#DDE1E9` | Hairlines |
| `--db-brand-line` | `--tinte` `#C2D7F3` | Soft borders, selection |
| `--db-ink-2` / `--db-ink-3` | `--txt-2` / `--txt-3` | Secondary / tertiary text |

Everything else is derived from those five — including the status pairs, which stay measured rather than eyeballed.

**The logo.** The rail carries the Proboto mark plus the `docuboto` wordmark (the product name stays, the brand mark signals whose product it is). The same mark, on a white tile with a cyan ring, is the **agent's avatar** — because the agent *is* Proboto Agent. That is the one place where the brand and the AI signal deliberately overlap.

**The type is deliberately unchanged**: Space Grotesk + Public Sans, self-hosted. proboto.co uses **Geist / Geist Mono**; switching is a four-value change in `tokens.css` but it is a bigger visual shift than a palette, so it is a decision, not a detail (see §11).

**Compatibility.** 3.0 adds a `--db-*` layer and **aliases every `--op-*` to it**, so all 845 files keep working and `frontend/src/styles/tokens-existen.test.js` stays green. Adoption is per screen. Also changed: softer lines, one shadow scale tinted with noche instead of black (black muddies a blue surface), larger radii (6/8/12/16/22), and a 4 px spacing base with a wider top end (24/32/40/56/72).

### 7.2 Why cyan belongs to the agent — and azul does not

Azul is the institution's colour. If the agent used azul, a proposal would look like a decision. So the split is semantic:

```
azul  = you did this, or the entity requires it
cian  = this came from proboto
```

Cyan has one practical constraint: `#00D4E0` measures **1.6:1 on white**, so it can never carry text. It lives only in fills, borders, the avatar and the status dot; all AI *text* uses a derived deep teal (`--db-ai-ink: #066169`, 6.5:1 on the soft cyan). The rule survives the palette change: **a colour that cannot be read cannot be used to make a claim.**

The reversibility trio also had to be re-derived, because the old teal for "Deshacer" would now collide with cyan:

| Class | Colour | Why |
|---|---|---|
| A · Deshacer | azul | It is an ordinary action; it should feel normal, not special |
| B · Revertir | ámbar | It creates a new legal act; caution, not danger — the button is amber, not red |
| C · No reversible | grafito | Final, neutral, locked. Red would over-alarm an act the law allows |

### 7.3 Accessibility commitments

* Text/background pairs keep **≥ 4.5:1**, measured with the WCAG relative-luminance formula (not estimated) rather than asserted:

  | Pair | Ratio |
  |---|---|
  | `--db-ink` (`#0A1122`) on `--db-surface` | 18.8 : 1 |
  | `--db-ink-2` (`#4A5163`) on `--db-surface` | 7.9 : 1 |
  | `--db-ink-3` (`#5F6778`) on `--db-surface` (smallest meta text) | 5.7 : 1 |
  | `--db-brand` (`#095ECE`) on `--db-surface` (all branded text) | 6.0 : 1 |
  | `--db-ok` / `--db-warn` / `--db-danger` on their `-soft` | 4.8 / 4.9 / 5.7 : 1 |
  | `--db-ai-ink` on `--db-ai-soft` (the only cyan-family text) | 6.5 : 1 |
  | `--db-ai` (`#00D4E0`) on `--db-surface` | **1.6 : 1 — never used as text** |

  Two values changed during review, for the same reason: the earlier tertiary ink measured **3.6:1**, and the earlier agent ink was only safe against its own tint. Both were re-derived instead of asserted. A token that claims a contrast band must carry the number — the repo already does this in `_tokens.scss`, and the habit is worth keeping.
* Dark mode derives from tokens; both themes ship in `tokens.css` and are swappable without a rebuild (tenant rebranding keeps working through `--op-*` overrides).
* Focus is one language: 2 px brand ring, 2 px offset, `:focus-visible` only.
* Touch targets ≥ 44 px for icon-only controls (the existing `sidebar-nav__pin-btn` precedent).
* Reversibility is never encoded by colour alone: `Deshacer` / `Revertir` / `No reversible` carry an icon **and** a word.
* The countdown in the undo bar is announced via `aria-live="polite"`, and the bar is dismissible from the keyboard.

---

## 8. Component contracts

| Component | States | Notes |
|---|---|---|
| `Button` | primary · secondary · soft · ghost · danger · danger-ghost · with lock | `--sm 28 / base 34 / --lg 42`. Lock only for class C. |
| `Chip` | brand · ok · warn · danger · info · ai · undo · outline | 21 px. `ai` is cyan-family; `undo` shares the brand azul because a reversible act *is* an ordinary action. |
| `Field` | default · focus · invalid · disabled · `field--ai` | `field--ai` = cyan tint + `PROBOTO` tag; the tag is required, not optional. |
| `ProposalCard` | pending · accepted · rejected · no-evidence | Accept/Reject same visual weight. |
| `Citation` | default · visited | Opens the literal quote + `Abrir en el documento`. |
| `UndoBar` | counting · paused (hover) · expired | Stacks to 3; ring shows seconds; `aria-live`. |
| `PreFlightSheet` | A · B · C · dual-control · conflict | One component, five configurations. §6.3. |
| `LedgerRow` | deshacer · revertir · no-reversible · undone | Diff in mono; class conveyed by icon + word. |
| `CapabilityRow` | on · off · locked (missing dep) · blocking (dependents) | Disabled switches keep their reason in text. |
| `ModeCard` | inactive · active · customised | Count of enabled capabilities, never a wall of feature names. |
| `AgentLadder` | active · available · locked | Locked rungs stay visible with the requirement named. |
| `EmptyState` | — | Every empty state names the next action, never just "no data". |

---

## 9. Screen by screen

| Screen | The change |
|---|---|
| **Escritorio** | A briefing header written by the agent, then four decisions ordered *by consequence, not by arrival* (the current dashboard already argues this in its own copy). Aggregate tenant metrics move behind a "Ver tablero" link — they were the least personal thing on the most personal screen. |
| **Bandeja** | Master–detail in one screen; status and location in the row; suggestion inline in the detail with Accept/Reject; bulk bar; irreversible acts visibly locked in the action bar. |
| **Radicar** | Three steps instead of one long form; live summary aside; AI-pre-filled fields tagged; the irreversibility of the number stated *before* the button, in the aside. |
| **Expedientes** | Retention and publication as first-class columns; the TRD rule stated in a banner instead of discovered through an error. |
| **Buscar** | One box, three modes (exact / semantic / ask). The answer carries citations; the result list says *why* each document matched. |
| **Distribución** | Remesas with a route track and acuse state; channels with connection state; the "entregado requires acuse" rule inline. |
| **Alertas** | Grouped by time-to-consequence; rules written as sentences (*Cuando… → avisar a…*) instead of a condition builder; silence is undoable. |
| **Capacidades** | §5. |
| **Inteligencia** | §4.6. |
| **Actividad** | §6. The ledger with three filters and a one-paragraph explanation of the model at the top. |
| **Sistema** | The living style guide: tokens, type, components, reversibility vocabulary. Exists so the next agent (human or not) doesn't invent a fourth shadow. |

---

## 10. Implementing this in the current SvelteKit app

No big-bang. Six phases, each independently shippable and testable.

**Phase 0 — Tokens (1–2 days).** Add `tokens.css` alongside `_tokens.scss`, alias `--op-*`, run the existing token guardians (`tokens-existen`, `tokens-mono`, `el-titulo-de-pagina-sale-del-token`, `el-velo-sale-del-token`). Zero visual regression expected; fix any literal that the alias exposes.

**Phase 1 — Shell and IA (1 week).** New `AppShell` + `RailNav`; keep `filterVisibleNavItems` as the single permission source but feed it a **grouped** catalogue (`navGroups.js` with `items[]`). Redirect table for the old URLs (`/bandeja` → `/documentos/bandeja`) so deep links, `favoritos` and e2e specs keep working. Update `SidebarNav.test.js` and `navItems` tests to assert the new tree, not the flat list.

**Phase 2 — Undo (1–2 weeks).** Introduce `src/lib/stores/undo.js` (stack, timers, reversers) and `UndoBar.svelte`. Retrofit class A first — the low-risk set: asignar, etiquetar, archivar, marcar leído, corregir plazo, reglas. Then add `ReversibilityChip` and the `PreFlightSheet` in place of the five confirm dialogs, starting with `RevertirModal` (which gets *less* ceremony and an honest label). Do not change class C behaviour until the sheet is reviewed by whoever owns the legal wording.

**Phase 3 — Agent surface (2 weeks, needs PPI D1).** `AgentPanel.svelte`, `ProposalCard.svelte`, `Citation.svelte`, `CommandBar.svelte`. All read-only at first: the Accept button is disabled until D2 lands. Feature-flag behind the existing `asistente_habilitado` + `PERM_CONOCIMIENTO`.

**Phase 4 — Capabilities (1 week + 1 backend endpoint).** `GET /config/capacidades` (tenant-service) returning the enabled set; the UI derives the rail, tabs and rule copy from it. Until it exists, derive from `GET /auth/me` + a static preset so the screens can be built. Add a guardian test: *no screen may hard-code a capability name*.

**Phase 5 — Screens (rolling).** Convert in this order: Escritorio → Bandeja → Alertas → Capacidades → Inteligencia → the rest. Each conversion keeps the existing `+page.server.js` and `api/` BFF untouched, so it is purely presentation.

**Phase 6 — Clean-up.** Delete the old `SidebarNav` markup, the duplicated confirm dialogs, and the now-unused `--op-*` literals. Keep the aliases.

### Risks and mitigations

| Risk | Mitigation |
|---|---|
| 845 files, many token guardians | Alias, never rename. The `--op-*` layer is frozen, not removed. |
| Legal wording on irreversible acts | Class C copy is reviewed by the domain owner before it ships; the prototype's text is a proposal. |
| The capability model drifts from the backend | One endpoint, one guardian test, one screen that edits it. |
| Mode switching becomes a support burden | Modes are presets; the customised state is visible; every change is undoable for 10 s and logged. |
| AI panel increases screen density | It collapses; below 1080 px it becomes an overlay; the work surface never shrinks below 640 px. |

---

## 11. Decisions I need from you

1. **Alertas vs Documentos** — should `Resolver anulaciones` / `Resolver desclasificaciones` live in Alertas as decision queues (my recommendation), or keep their own destinations?
2. **`/reportes`** — a tab under Documentos, or a fifth destination? Today it is the only "analysis" screen and it is buried.
3. **Mode naming** — `Esencial / Escala / Regulado` in Spanish, or the more institutional `Básico / Ampliado / Regulado`?
4. **Dark mode** — ship it, or keep light-only for now? It is ~40 lines of tokens but doubles visual QA.
5. **Class B ceremony** — is a reason mandatory for every reversal, or only when it touches public exposure (publication, transfer, annulment)?
6. **Dual control scope in Regulado** — which class-C acts require a second person? My proposal: finalize, annul, declassify, delete, sign with certificate. Not: radicar (the ventanilla is a counter, two people per radicado is unworkable).
7. **AI level default for existing entities** — ADR-037 turns existing entities on and new ones off. Should the *level* default to read-only for everyone, with triage opt-in?
8. **Scope of the first release** — Phase 0–2 (tokens, shell, undo) is coherent without any backend change and already answers "cleaner and undoable". Is that the first milestone?
9. **Typography** — proboto.co uses **Geist / Geist Mono**. I kept Space Grotesk + Public Sans because the palette was the ask; switching them makes the product read as Proboto and takes one token edit. Do you want it?
10. **Logo lockup** — the prototype uses the Proboto mark + `docuboto` wordmark, and the mark as the agent's avatar. The alternative is the full `proboto` lockup with `Docuboto` as a sub-label (right if Docuboto is marketed as a Proboto product rather than a standalone).

---

## Appendix A — Reversibility inventory (condensed, verified)

| Action | Class | Evidence |
|---|---|---|
| Radicar | C | `radicar/+page.svelte:1250–1293`; number is immutable, only annulment after |
| Guardar borrador / crear | A | `borradores/+page.svelte:1142` |
| Aprobar borrador | B | `…:1195`; compensated by "Devolver a edición" (`:1253`) |
| Radicar desde borrador | C | `…:1306` |
| Marcar en trámite | A | `bandeja/+page.svelte:2225` |
| Trasladar / Trámite | B | `WorkflowCard.svelte:167–185` |
| Completar paso | B | `:190` |
| Revertir paso | B (labelled C in UI) | `RevertirModal.svelte:155`; only if step `pending` |
| Asignar a flujo | A | `AsignarModal.svelte:160` |
| Devolver | B | `bandeja/+page.svelte:2157` |
| Finalizar trámite | C | `FinalizarTramiteModal.svelte:144–186` |
| Solicitar anulación | B | `bandeja/+page.svelte:1747,2213` |
| Aprobar anulación | C | `anulaciones/+page.svelte:741–770` |
| Aprobar desclasificación | C | `desclasificaciones/+page.svelte:352–383`; level 4 + another person |
| Firmar / firmar en lote | C | `firmas/+page.svelte:899,613,978` |
| Dar visto bueno / rechazar | B | `…:890,948` |
| Vincular / excluir radicado | B | `expedientes/[id]/+page.svelte:1215,1256` |
| Firmar índice | C | `…:1367,517` |
| Publicar / retirar publicación | B | `…:1024`; reason both ways |
| Cerrar expediente | C | `…:1029,149` |
| Transferir expediente | C | `…:1039,150` |
| Fijar / levantar retención legal | B | `RetencionesLegalesPanel.svelte:247,214` |
| Recibir transferencia | C | `transferencias/+page.svelte:531,257–262` |
| Enviar / rechazar transferencia | B | `…:519,543` |
| Firmar acta (personal) | C | `transferencias/[id]/+page.svelte:722` |
| Declarar firma inobtenible | C | `ExcepcionesFirma.svelte:444` |
| Revocar excepción | B | `…:723` |
| Cambiar estado de envío | B (terminal on `entregado`) | `envios/+page.svelte:967,957` |
| Registrar devolución | B | `…:978` |
| Préstamo / devolución | B | `archivo-fisico/+page.svelte:1737,1326` |
| Corregir ubicación | A | `…:962,981` |
| Aprobar / convalidar / derogar / RUSD | B / C / C / C | `InstrumentLifecycleActions.svelte:273–298` |
| Ejecutar eliminación | C | `admin/eliminacion/+page.svelte:362,679–700`; requires signed acta + typed code |
| Retirar lote | B | `…:367` |
| Restablecer segundo factor | C | `admin/usuarios/+page.svelte:553` |
| Guardar vencimiento | A | `bandeja/+page.svelte:1883` |

**Cross-cutting facts a redesign must respect**

* **Undo almost never deletes.** Revocation, rollback, devolver, retirar lote, TRD versions, preservation plan versions and AIP versioning are all append-only (ADR-042: *se versiona, no se reescribe*). The UI must name the compensating act, not an eraser.
* **Visibility is already state-aware** (`canClose` xor `canTransfer`, "Editar" only in `borrador`, actions disappear after `entregado`). Keep that; add the *reason* next to the absence.
* **WORM makes some undos impossible** (COMPLIANCE retention + legal hold). When an action is missing, show `retenido hasta 2041`, not a gap.
* **Dual control is asymmetric today**: annulment (approve ≠ request), declassification (level 4 + another person + 15-day expiry), deletion (publicity → committee → signed acta → execute). The Regulado mode should surface these as one pattern.
* **TOTP/PKI only on personal signature**, never on the institutional seal. The UI should not ask for a second factor where the backend won't.

## Appendix B — Clicking through the prototype

Open `prototipo/index.html` in a browser (no server, no build).

1. **`⌘K`** → switch between *Preguntar / Buscar / Hacer*.
2. **Escritorio → "Posible duplicado"** → the record opens with the proposal card; **Aplicar** shows the undo bar.
3. **Bandeja** → tick two rows → **Etiquetar** → undo bar says *2 radicados*; press **⌘Z**.
4. **Bandeja → Devolver** → pre-flight says *se revierte con un traslado nuevo* (class B).
5. **Bandeja → "Cerrar es irreversible"** / **Actividad → No reversible** → constancia modal with the legal alternative.
6. **Capacidades → Regulado** → pre-flight listing rule deltas → activate → undo bar.
7. **Capacidades** → try to switch off *Radicación* and read why it refuses.
8. **Inteligencia** → change the level; **Reservada** stays off until declared.
9. **Actividad** → filter by class, undo a class A row, try to undo a class C row.
10. **Tema** (top bar) and **Densidad** (Sistema) — both are token-level preferences.
