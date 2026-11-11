# Web image. Build from frontend/:  docker build -t scribe-lite-web frontend
# Listens on $PORT (Cloud Run sets 8080) and proxies /api/* to $API_URL at runtime.

FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:24-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:24-alpine
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    HOSTNAME=0.0.0.0 \
    PORT=8080
RUN addgroup -S -g 10001 web && adduser -S -u 10001 -G web web
# The standalone server does not include static assets or public/; copy both.
COPY --from=build --chown=web:web /app/.next/standalone ./
COPY --from=build --chown=web:web /app/.next/static ./.next/static
COPY --from=build --chown=web:web /app/public ./public
USER web
EXPOSE 8080
CMD ["node", "server.js"]
