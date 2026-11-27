# ADR-058: Prácticas Anti-Slop y Gates de Calidad de Código

**Estado**: Propuesto  
**Fecha**: 2026-11-27  
**Autores**: Agente de Cursor (diagnóstico), Camilo Romero (decisión)  
**Relacionados**: ADR-053 (esquema legible y lanzamientos), ADR-047 (un proceso)

---

## Contexto

**Slop** = poner menos esfuerzo en producir algo que el que se le pide al lector para entenderlo. Se compone porque el próximo agente copia lo que está en el repo. Las 12 reglas de Dex Horthy (AI Engineer CODE 2026, "Software Factory Without Slop") definen cómo construir sistemas mantenibles con agentes de IA.

### Diagnóstico Actual (2026-11-27)

**Puntuación**: 7/12 reglas implementadas completamente, 3 parciales, 2 faltantes.

**Lo que ya está bien**:
- ✅ CI robusto con múltiples gates (lint, tests, security, audit)
- ✅ 57 ADRs de alta calidad documentan decisiones arquitectónicas
- ✅ Ratio test:producción saludable (1.08:1, 524 test files)
- ✅ Guardians de arquitectura en tests (`test_matriz_ci_cubre_servicios`, `test_un_solo_proceso`)
- ✅ CONTRIBUTING.md exhaustivo con workflow claro

**Deuda técnica medida**:
- Complejidad promedio: **A (3.24)** ✅ — excelente
- Funciones con complejidad >30 (E-F): **25 funciones** ❌ — crítico
- Duplicación de código: **6.87%** (1.488 clones) ⚠️ — mejorable
- Top hotspots: `document/services/tercero_service.py` (38), `signature/providers/local.py` (45), `archive/services/transferencia_service.py` (41)

**Gaps críticos**:
- ❌ No hay regla de complejidad (C901) en ruff — permite introducir funciones >15
- ❌ No hay pre-commit hooks — feedback loop lento
- ❌ No hay feature flags — experimentos van directo a main
- ❌ No hay evals para features de IA (asistente ADR-037, PPI ADR-045)
- ❌ No hay diagramas en ADRs (walls of text)
- ⚠️ No hay AGENTS.md con instrucciones para agentes de IA

Ver `docs/es/anti-slop/diagnostico.md` para el análisis completo con mediciones de radon, scb-check, jscpd y vulture.

---

## Decisión

Se adoptan **prácticas anti-slop como política de proyecto**, con gates automáticos en CI y cultura de prevención.

### 1. Gates de Complejidad (Inmediato)

**Agregar a `pyproject.toml`**:
```toml
[tool.ruff.lint]
select = [
    # ... existentes ...
    "C90",    # McCabe complexity
    "PLR",    # Pylint refactor (too-many-*)
]

[tool.ruff.lint.mccabe]
max-complexity = 15

[tool.ruff.lint.per-file-ignores]
# Funciones E-F existentes (25 funciones) — refactorizar en fases
"services/docuboto/document/services/tercero_service.py" = ["C901", "PLR0912"]
"services/docuboto/signature/providers/local.py" = ["C901"]
# ... (lista completa en plan.md)
```

**Efecto**: CI rechaza PRs con nueva función >15 de complejidad. Código existente se refactoriza en fases.

### 2. Pre-commit Hooks (Inmediato)

**Crear `.pre-commit-config.yaml`** con ruff, mypy, trailing-whitespace, check-yaml.

**Efecto**: Lint local antes de commit (ciclo <5s), menos rebotes de CI.

### 3. Template de PR con Checklist

**Crear `.github/pull_request_template.md`** que pregunte:
- ¿ADR creado si cambio >500 LOC?
- ¿Complejidad de funciones nuevas ≤15?
- ¿Mockup/screenshot si cambio UI?
- ¿Tests con ratio ≥1:1?

**Efecto**: Fuerza reflexión antes de merge.

### 4. AGENTS.md para IA

**Crear `/workspace/AGENTS.md`** con:
- Reglas de código (NO ORM, complejidad ≤15, type hints)
- Hotspots críticos (tercero_service, firma, transferencias)
- Workflow (ADRs, lint.sh, no ruff desde raíz)
- ADRs clave (047, 049, 003, 053)

**Efecto**: Agentes de IA (Cursor, Copilot) conocen contexto del repo.

### 5. Monitoreo de Erosión (Fase 2)

**Agregar job a CI** que reporte:
- Complejidad promedio (radon cc)
- Funciones >15 (count)
- Duplicación (jscpd)

**Efecto**: Visibilidad de tendencias, compound improvements (#8).

### 6. Refactorización Incremental (Fases 2-3)

**Objetivo**: Reducir 25 funciones E-F a <10.

**Estrategia**: Extract method, pattern matching, uno por iteración, prioridad por criticidad de negocio (firma, transferencias, AIPs > tests).

**Gate**: No remover excepciones de ruff hasta que función pase complejidad ≤15.

---

## Alternativas Consideradas

### A. No hacer nada (status quo)
- **Pro**: Sin esfuerzo.
- **Con**: Deuda técnica crece, agentes de IA copian slop, mantenibilidad se degrada.
- **Descartado**: Contradice cultura de ADRs rigurosos del proyecto.

### B. Gran refactor (reescribir 25 funciones E-F de golpe)
- **Pro**: Elimina deuda de una vez.
- **Con**: Alto riesgo (firma electrónica, transferencias físicas, AIPs de preservación), bloquea desarrollo, difícil de verificar.
- **Descartado**: Incremental es más seguro.

### C. Comprar herramienta (SonarQube, CodeClimate)
- **Pro**: Dashboard bonito, tracking histórico.
- **Con**: Costo, vendor lock-in, no resuelve la deuda existente.
- **Descartado por ahora**: Gates simples (ruff C901 + radon) son suficientes; evaluar en Fase 4 si se necesita dashboard.

### D. Solo documentar (sin gates)
- **Pro**: Sin friction en PRs.
- **Con**: No se cumple — "guideline" sin gate es wishful thinking.
- **Descartado**: Se necesita back pressure (#1).

---

## Consecuencias

### Positivas

1. **Prevención**: Nuevas PRs no introducen slop (gate C901 lo bloquea).
2. **Visibilidad**: Métricas en CI muestran tendencias, compound improvements rastreables.
3. **Cultura**: Template de PR + AGENTS.md educan sobre anti-slop.
4. **Remediación incremental**: 25 funciones E-F se reducen en fases sin gran refactor.
5. **Compatibilidad con IA**: Agentes tienen contexto (AGENTS.md), guardrails (C901).

### Negativas / Trade-offs

1. **Friction inicial**: Pre-commit hooks + C901 pueden bloquear PRs legítimos.
   - **Mitigación**: Excepciones temporales (`per-file-ignores`) cubren código existente; solo se aplica a código nuevo.
2. **Esfuerzo de refactorización**: 40-60 horas para reducir 25 funciones E-F.
   - **Mitigación**: Se hace en fases, priorizando criticidad de negocio.
3. **Complejidad de setup**: Pre-commit hooks requieren `pip install pre-commit` local.
   - **Mitigación**: Documentado en CONTRIBUTING.md, `--no-verify` escape hatch.
4. **Falsos positivos de C901**: Algunas funciones legítimamente complejas (parsers, state machines).
   - **Mitigación**: Agregar a `per-file-ignores` con justificación en comentario.

### Dependencias

- **Ninguna**: Todas las herramientas (ruff, radon, jscpd, pre-commit) son OSS y no requieren servicios externos.
- **Compatible con ADR-047** (un proceso): No cambia arquitectura.
- **Compatible con ADR-053** (esquema legible): Mejora mantenibilidad.

---

## Plan de Implementación

Ver `docs/es/anti-slop/plan.md` para el plan completo con prioridades, esfuerzos y verificación.

**Quick wins (Fase 1, esta semana)**:
1. Gate de complejidad (C901 + PLR en pyproject.toml) — 2h
2. Pre-commit hooks (.pre-commit-config.yaml) — 1h
3. AGENTS.md — 1h
4. Template de PR — 30m

**Remediación (Fase 2, 2-4 iteraciones)**:
1. Refactorizar top 10 funciones E-F — 40-60h (6-8h por función)
2. Eliminar duplicación crítica (HTTPException boilerplate, indice_reconciliation.py) — 12h
3. Monitoreo de complejidad en CI — 3h
4. Cobertura de tests como gate — 2h

**Cultura (Fase 3, medio plazo)**:
1. Feature flags simples — 4h
2. ADRs con diagramas (template + ejemplos) — 10h
3. Evals para features de IA — 8h
4. ast-grep rules custom — 6h

---

## Métricas de Éxito (3 meses)

| Métrica | Actual | Objetivo |
|---------|--------|----------|
| Funciones >30 complejidad | 25 | <10 |
| Duplicación | 6.87% | <5% |
| Cobertura tests | ❓ | >80% |
| ADRs con diagramas | ~0% | >50% |
| Scorecard anti-slop | 7/12 | 10/12 |

---

## Referencias

- Dex Horthy, "12 Rules for a Software Factory That Doesn't Ship Slop", AI Engineer CODE 2026
- https://slopmanifesto.com
- SlopCodeBench: https://github.com/gabeorlanski/scb-check
- Diagnóstico completo: `docs/es/anti-slop/diagnostico.md`
- Plan de mejora: `docs/es/anti-slop/plan.md`
