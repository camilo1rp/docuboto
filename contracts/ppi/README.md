# Contratos PPI versionados

Cada versión publicada del Intelligence Provider Protocol (PPI) vive en su propio
directorio, congelado: el OpenAPI, los esquemas, los *fixtures* de conformidad y su
README normativo.

| Versión | Directorio | Estado |
| --- | --- | --- |
| 1.4.0 | [`v1.4.0/`](v1.4.0/README.md) | Vigente (ADR-051) |

Las versiones 1.0.0 a 1.3.0 vivieron en `docs/contratos/ppi/` y se leen en la historia de
git; ningún par las necesita, porque la cabecera `PPI-Version` acepta cualquier `1.x.y`.

## Reglas

- **Un directorio publicado no se edita.** `v1.4.0/SHA256SUMS` lista la suma de cada
  archivo y `shared/tests/test_ppi_contratos.py` falla si alguno cambia. Una errata se
  publica como versión de parche en un directorio nuevo.
- **Una versión nueva es un directorio hermano** (`v1.5.0/`), copiado del anterior y
  cambiado allí, con su entrada en *Changes* y sus sumas.
- **Un par se ancla por directorio, no por commit.** proboto-agent apunta
  `PROBOTO_PPI_SPEC_DIR` a `contracts/ppi/v1.4.0` de cualquier commit de Docuboto que lo
  contenga.

Se licencia, como todo el repositorio, bajo AGPL-3.0-or-later.
