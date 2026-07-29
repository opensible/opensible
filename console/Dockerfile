# Frontend — Vite/React SPA served via nginx
# Build context: repo root  (docker build -f Dockerfile .)

# ---------- build stage ----------
FROM oven/bun:1 AS builder
WORKDIR /app

# Install deps first for better layer caching
COPY package.json bun.lock* ./
RUN bun install --frozen-lockfile || bun install

# Copy source and build
COPY tsconfig.json vite.config.ts index.html ./
COPY public ./public
COPY src ./src

RUN bun run build

# ---------- runtime stage ----------
FROM nginx:alpine

# envsubst for entrypoint
RUN apk add --no-cache gettext

COPY nginx-docker.conf /etc/nginx/templates/default.conf.template
COPY docker-entrypoint.sh /docker-entrypoint-frontend.sh
RUN chmod +x /docker-entrypoint-frontend.sh

COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
ENTRYPOINT ["/docker-entrypoint-frontend.sh"]
CMD ["nginx", "-g", "daemon off;"]
