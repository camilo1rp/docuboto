# Contributing to OrpycaMCP

Thank you for your interest in improving OrpycaMCP!

## Before Contributing

1. Read the [architecture](architecture.md) and the [ADRs](adrs.md)
2. Review the [roadmap](roadmap.md) to see what is planned
3. Open an issue on GitLab describing your proposal before coding

## Contribution Rules

1. **Code in English** — identifiers, comments, commit messages. Every **new** name is in English: tables, columns, `CHECK` values, routes, events, audit actions, permissions, environment variables, Compose services and profiles. Existing Spanish names stay until they are changed for another reason. Colombian archival terms with no English equivalent (radicado, expediente, TRD, TVD, FUID) may be used as nouns inside an English name
2. **Documentation** — complete in Spanish in `docs/es/`; `docs/en/` is a translated selection. ADRs, the test plan, the MCP flows and the data-processing policy template are Spanish only (list in `shared/tests/test_la_documentacion_dice_lo_que_hay.py`)
3. **Tests required** — at least one test per new endpoint
4. **Annotated types** — Python 3.11+ type hints in all functions
5. **AGPL v3** — do not include code with incompatible licenses

## Workflow

```bash
# 1. Fork the repository
# 2. Create a branch in the format feat/name or fix/name
git checkout -b feat/document-service-radicacion-entrada

# 3. Develop with tests
cd services/document-service
pytest tests/ -v

# 4. Update the corresponding documentation
# (the CI verifies it automatically)

# 5. Commit following Conventional Commits
git commit -m "feat(document): implement entrada document registration"

# 6. Push and open a Merge Request
```

## Commit Convention

| Type | When to use it |
|---|---|
| `feat` | New functionality |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | Restructuring without behavior change |
| `test` | Add or fix tests |
| `chore` | Maintenance, dependencies |
| `ci` | Pipeline changes |

**Example:** `feat(archive): add expediente state machine (open → closed → transferred)`

## PR Checklist

- [ ] The code passes `ruff check` and `mypy`
- [ ] New tests pass with `pytest`
- [ ] `docs/es/api.md` updated if I changed endpoints
- [ ] `CHANGELOG.md` has an entry for this change
- [ ] No hardcoded credentials
- [ ] If I changed the schema, I added a numbered `.sql` migration (no ORM, no Alembic, ADR-003)
