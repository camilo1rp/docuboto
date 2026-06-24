# ADR-009: Historial de flujos como eventos append-only + proyección de estado

**Estado**: Propuesto
**Fecha**: 2026-06-15
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

El módulo de flujos (RT-05) enruta documentos entre dependencias: asignaciones, reenvíos, devoluciones, visto bueno, cierres. El Orfeo legado mantenía un **historial mutable** (filas que se actualizaban), lo que hacía que el rastro de "quién tuvo qué y cuándo" se pudiera perder o sobreescribir — un problema de **trazabilidad legal**.

A la vez, la operación diaria necesita consultas de **estado actual** baratas: la bandeja de un usuario, los pasos abiertos, los vencimientos de SLA. Un historial puro de eventos no responde eso de forma directa sin recorrer todo el historial.

Hay que conciliar **inmutabilidad del rastro** con **eficiencia del estado actual**.

## Decisión

**`flow_events` (append-only) es la fuente de verdad del flujo; `flow_steps` (y la bandeja) es una proyección mutable reconstruible a partir de los eventos.**

- Cada acción del flujo (asignar, reenviar, devolver, VoBo, cerrar) se registra como un **evento inmutable** en `flow_events`.
- El **estado actual** (`flow_steps`, bandejas, pasos abiertos) se mantiene como **proyección** actualizada por un consumidor de esos eventos; puede reconstruirse desde cero reproduciendo `flow_events`.
- Los mismos eventos se publican en **Redis Streams** (ver contrato de eventos) para que otros servicios reaccionen (notificaciones, búsqueda, SLA).

## Implementación

```sql
-- Fuente de verdad: inmutable, append-only (en tenant_{slug})
CREATE TABLE IF NOT EXISTS flow_events (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    objeto_tipo   TEXT NOT NULL,            -- 'radicado' | 'expediente'
    objeto_ref    TEXT NOT NULL,
    tipo_evento   TEXT NOT NULL,            -- 'asignado' | 'reenviado' | 'devuelto' | 'vobo' | 'cerrado'
    from_dep      TEXT,
    to_dep        TEXT,
    actor         TEXT NOT NULL,
    ts            TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb
);
-- Sin UPDATE/DELETE para el rol de aplicación (igual filosofía que ADR-008).

-- Proyección mutable: estado actual para bandeja/SLA (reconstruible)
CREATE TABLE IF NOT EXISTS flow_steps (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    objeto_tipo   TEXT NOT NULL,
    objeto_ref    TEXT NOT NULL,
    dependencia   TEXT NOT NULL,
    estado        TEXT NOT NULL,            -- 'abierto' | 'cerrado'
    asignado_en   TIMESTAMPTZ NOT NULL,
    vence_en      TIMESTAMPTZ,              -- SLA (días hábiles, vía E14)
    last_event_id BIGINT NOT NULL           -- hasta qué evento está proyectado
);
```

- El consumidor aplica cada `flow_event` a `flow_steps` de forma **idempotente** (usa `last_event_id`).
- El *scheduler* de SLA (ver catálogo de alertas) lee `flow_steps`, no el historial completo.

## Consecuencias

**Positivas:**
- Rastro del flujo **inmutable** y reproducible — coherente con la filosofía de auditoría ([ADR-008](ADR-008-auditoria-inmutable.md)).
- Estado actual eficiente para bandeja y SLA.
- La proyección puede **reconstruirse** si se corrompe (los eventos son la verdad).

**Negativas:**
- **Doble escritura** (evento + proyección) y complejidad del consumidor.
- **Consistencia eventual** de la proyección respecto al evento (ventana corta; mitigable proyectando en la misma transacción para los casos críticos).

## Alternativas consideradas

- **Solo `flow_steps` mutable (modelo legado)**: descartado — es justamente la causa de la pérdida de historial que este ADR corrige.
- **Event sourcing completo** (sin tablas de proyección, todo derivado en lectura): descartado por sobre-ingeniería; el híbrido evento+proyección da inmutabilidad sin penalizar la lectura.

## Relacionados

- [ADR-008](ADR-008-auditoria-inmutable.md) — misma filosofía append-only; los eventos de flujo también generan auditoría.
- Contrato de eventos (specDrive) — `flow_events` se publica en `orpycamcp.workflow.events`.
