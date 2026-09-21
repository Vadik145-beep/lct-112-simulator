# Stage 1: build the frontend.
FROM node:22-alpine AS frontend
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# Stage 2: nginx with TLS and the built static files.
FROM nginx:1.27-alpine
RUN apk add --no-cache openssl
COPY deploy/nginx/nginx.conf /etc/nginx/nginx.conf
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY deploy/nginx/locations.conf /etc/nginx/locations.conf
COPY deploy/nginx/security-headers.conf /etc/nginx/security-headers.conf
RUN mkdir -p /etc/nginx/conf.d/public /var/www/certbot
COPY deploy/certs/generate.sh /usr/local/bin/generate-certs.sh
COPY deploy/nginx/entrypoint.sh /docker-entrypoint.d/05-generate-certs.sh
RUN chmod +x /usr/local/bin/generate-certs.sh /docker-entrypoint.d/05-generate-certs.sh
COPY --from=frontend /app/dist /usr/share/nginx/html
