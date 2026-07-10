# syntax=docker/dockerfile:1

FROM node:20-alpine AS build

ARG VITE_DATASET_API_URL
ARG VITE_AGENT_API_URL
ARG VITE_CHUNKER_URL

ENV VITE_DATASET_API_URL=$VITE_DATASET_API_URL
ENV VITE_AGENT_API_URL=$VITE_AGENT_API_URL
ENV VITE_CHUNKER_URL=$VITE_CHUNKER_URL

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install

COPY . .
RUN npm run build

FROM nginx:alpine AS runtime

RUN apk add --no-cache openssl \
  && mkdir -p /etc/nginx/ssl \
  && openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
       -keyout /etc/nginx/ssl/key.pem \
       -out /etc/nginx/ssl/cert.pem \
       -subj "/CN=localhost/O=GenA/C=RU"

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 443

CMD ["nginx", "-g", "daemon off;"]
