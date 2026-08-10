# Frontend OrpycaMCP — build de producción (SvelteKit + adapter-node SSR).
# Patrón multi-stage alineado con sgdINTI. El build ocurre SIEMPRE en Docker
# (regla Docker-only del proyecto); no se instalan dependencias en el host.

# --- Build stage ---
FROM node:22-alpine AS builder
WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
RUN npm run build

# --- Production stage ---
FROM node:22-alpine AS runner
WORKDIR /app

COPY --from=builder /app/package.json ./
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/build ./build

ENV NODE_ENV=production
ENV PORT=3000

# Tamaño máximo del cuerpo que acepta el servidor SSR (adapter-node).
#
# El default de adapter-node es 524288 — **512 KB**— y se aplica a TODA petición
# antes de llegar al handler. Como las subidas de anexos pasan por el SSR
# (`radicar/api/storage`, y la importación interoperable en
# `admin/interoperabilidad/api/importar`), ese default rechazaba con 413
# cualquier PDF escaneado real: el usuario veía «Error al subir el archivo» sin
# más pista, y E11 INT-05 era inservible. La variable no aparecía en ningún sitio
# del repositorio, así que el límite estaba activo en todos los entornos.
#
# 512 MiB coincide con el tope del gateway (`max_request_body_bytes`) y con el
# del ZIP de importación. Subir esto exige el flujo por trozos del gateway y su
# tope explícito, que van en el mismo cambio: sin ellos, esto solo cambia un 413
# por un OOM.
ENV BODY_SIZE_LIMIT=536870912

# Resolución de la IP real del navegador (adapter-node). Sin esto,
# `getClientAddress()` devuelve la dirección del reverse proxy, y como el SSR la
# propaga al gateway para la limitación de tasa, TODO el tráfico seguiría
# compartiendo un único cubo. XFF_DEPTH=1 = hay UN proxy de confianza delante
# (nginx); súbelo si hay más saltos, porque cada salto adicional deja al usuario
# poder inyectar un valor que este índice no descarta.
ENV ADDRESS_HEADER=x-forwarded-for
ENV XFF_DEPTH=1

# --- URLs públicas (navegador del usuario) ---
# Sobreescribir en despliegue con la URL real accesible por los usuarios.
ENV PUBLIC_API_URL=http://localhost:19080
ENV PUBLIC_KEYCLOAK_URL=http://localhost:19180

# --- URLs internas (SSR dentro de la red Docker) ---
# El servidor SSR usa estas URLs para hacer fetch al gateway y a Keycloak.
# Con $env/dynamic/private se leen en runtime, no se hornean en el build.
ENV INTERNAL_API_URL=http://api-gateway:8080
ENV INTERNAL_KEYCLOAK_URL=http://keycloak:8080

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://localhost:3000/ || exit 1

# No ejecutar como root para evitar artefactos root en volúmenes y reducir superficie de ataque.
USER node

CMD ["node", "build"]
