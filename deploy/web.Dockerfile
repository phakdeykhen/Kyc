FROM node:22-bookworm-slim AS build
WORKDIR /app
COPY Frontend/package.json Frontend/package-lock.json ./
RUN npm ci
COPY Frontend/ ./
# Same-origin API: the web container proxies /v1/ to the API container.
ENV VITE_KYC_API_BASE=""
RUN npm run build

FROM nginx:stable-alpine
COPY deploy/security-headers.conf /etc/nginx/snippets/kyc-security-headers.conf
COPY deploy/web.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/out /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/healthz || exit 1
