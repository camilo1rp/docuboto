# Contribuir a OrpycaMCP / Contributing to OrpycaMCP

¡Gracias por tu interés en mejorar OrpycaMCP!
Thank you for your interest in improving OrpycaMCP!

## Antes de contribuir / Before Contributing

1. Lee la [arquitectura](architecture.md) y los [ADRs](adrs.md)
2. Revisa el [roadmap](roadmap.md) para ver qué está planificado
3. Abre un issue en GitLab describiendo tu propuesta antes de codificar

## Reglas de contribución / Contribution Rules

1. **Código en inglés** — identificadores, comentarios, mensajes de commit. Todo nombre **nuevo** va en inglés: tablas, columnas, valores de `CHECK`, rutas, eventos, acciones de auditoría, permisos, variables de entorno, servicios y perfiles de Compose. Los nombres en español que ya existen se quedan hasta que se toquen por otro motivo. Los términos archivísticos colombianos sin equivalente en inglés (radicado, expediente, TRD, TVD, FUID) pueden usarse como sustantivos dentro de un nombre en inglés
2. **Documentación** — completa en español en `docs/es/`; `docs/en/` es una selección traducida. Los ADR, el plan de pruebas, los flujos MCP y la plantilla de política de tratamiento son solo en español (lista en `shared/tests/test_la_documentacion_dice_lo_que_hay.py`)
3. **Tests obligatorios** — al menos un test por endpoint nuevo
4. **Tipos anotados** — Python 3.11+ type hints en todas las funciones
5. **AGPL v3** — no incluyas código con licencias incompatibles

## Flujo de trabajo / Workflow

```bash
# 1. Fork del repositorio
# 2. Crea una rama con formato feat/nombre o fix/nombre
git checkout -b feat/document-service-radicacion-entrada

# 3. Desarrolla con tests
cd services/document-service
pytest tests/ -v

# 4. Actualiza la documentación correspondiente
# (el CI lo verifica automáticamente)

# 5. Commit siguiendo Conventional Commits
git commit -m "feat(document): implement entrada document registration"

# 6. Push y abre Merge Request
```

## Convención de commits / Commit Convention

| Tipo | Cuándo usarlo |
|---|---|
| `feat` | Nueva funcionalidad |
| `fix` | Corrección de bug |
| `docs` | Solo documentación |
| `refactor` | Reestructuración sin cambio de comportamiento |
| `test` | Añadir o corregir pruebas |
| `chore` | Mantenimiento, dependencias |
| `ci` | Cambios en el pipeline |

**Ejemplo:** `feat(archive): add expediente state machine (open → closed → transferred)`

## Checklist de PR / PR Checklist

- [ ] El código pasa `ruff check` y `mypy`
- [ ] Los tests nuevos pasan con `pytest`
- [ ] `docs/es/api.md` actualizado si cambié endpoints
- [ ] `CHANGELOG.md` tiene entrada para este cambio
- [ ] No hay credenciales hardcodeadas
- [ ] Si cambié el esquema, añadí una migración `.sql` numerada (sin ORM ni Alembic, ADR-003)

<!-- TODO: translate to English -->
