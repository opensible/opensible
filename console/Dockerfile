# Frontend — Vite/React SPA served via nginx
# Build context: repo root  (docker build -f console/Dockerfile .)

# ---------- build stage ----------
FROM oven/bun:1 AS builder
WORKDIR /app

# Install deps first for better layer caching
COPY console/package.json console/bun.lock* ./
RUN bun install --frozen-lockfile || bun install

# Copy source and blueprint definitions. src/lib/blueprints/index.ts globs
# ../../../IaC/blueprints/**, so those files must sit at the build root.
COPY console/tsconfig.json console/vite.config.ts console/index.html ./
COPY console/public ./public
COPY console/src ./src
COPY IaC/blueprints ./IaC/blueprints

RUN bun run build

# ---------- runtime stage ----------
FROM nginx:alpine

# envsubst for entrypoint
RUN apk add --no-cache gettext

COPY console/nginx-docker.conf /etc/nginx/templates/default.conf.template
COPY console/docker-entrypoint.sh /docker-entrypoint-frontend.sh
RUN chmod +x /docker-entrypoint-frontend.sh

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
ENTRYPOINT ["/docker-entrypoint-frontend.sh"]
CMD ["nginx", "-g", "daemon off;"]
