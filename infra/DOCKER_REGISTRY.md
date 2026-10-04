# Registro de imágenes (GitLab Container Registry)

OrpycaMCP publica sus imágenes en el **GitLab Container Registry** del proyecto, desde el
pipeline de `.gitlab-ci.yml` (job `build-images`). No hay pipeline de GitHub Actions ni GHCR:
este repositorio no tiene `.github/`.

## Cuándo se construye y se publica

- **En cada tag** (`$CI_COMMIT_TAG`): se construyen las imágenes de los servicios y el frontend,
  se escanean con Trivy (una CRÍTICA con corrección disponible falla el job **antes** del push) y se
  publican con el SHA del commit y con `latest`.
- **En cualquier otro commit**: el job es manual (botón), para cuando haga falta una imagen de un
  commit concreto. Construir once imágenes en cada push producía decenas de etiquetas que nadie iba
  a desplegar.

## Nombre y etiqueta

```
$CI_REGISTRY_IMAGE/<servicio>:$CI_COMMIT_SHA     # inmutable: es la que se despliega
$CI_REGISTRY_IMAGE/<servicio>:latest             # solo informativa
```

Producción (`infra/docker-compose.prod.yml`) exige `ORPYCA_REGISTRY` (= `$CI_REGISTRY_IMAGE`) y
`ORPYCA_TAG` (= el SHA del commit; **nunca `latest`**, para que "la versión anterior" exista y se
pueda volver a ella). Un guardián (`shared/tests/test_prod_inmutable.py`) impide reintroducir
`build:` o un tag `latest`.

```bash
export ORPYCA_REGISTRY=registry.gitlab.com/<grupo>/<proyecto> ORPYCA_TAG=<sha-del-commit>
docker compose -f infra/docker-compose.prod.yml pull
docker compose -f infra/docker-compose.prod.yml up -d
```

## Sin registro: construir en el propio servidor

```bash
export ORPYCA_REGISTRY=local ORPYCA_TAG=$(git rev-parse HEAD)
docker compose -f infra/docker-compose.prod.yml -f infra/docker-compose.build.yml build
```

## Autenticación

- **En el pipeline**: `CI_REGISTRY_USER` / `CI_REGISTRY_PASSWORD` (variables que GitLab inyecta).
- **En un servidor de producción**: un *deploy token* de solo lectura (`read_registry`) y
  `docker login registry.gitlab.com`.

## Arquitectura de las imágenes

Se construyen para `linux/amd64`. ARM64 no está soportado hoy (habría que añadir `buildx` + QEMU al
job `build-images`).

## Pendiente

- [ ] Firma de imágenes (Cosign)
- [ ] Multi-arquitectura (ARM64)
- [ ] Manifiestos de Kubernetes / Helm: **no existen** (ver `docs/es/deployment.md`)
- [ ] Espejo de registro privado (entornos aislados)
