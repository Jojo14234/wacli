# syntax=docker/dockerfile:1@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32

FROM golang:1.27.1-alpine@sha256:8a5910f31396cd4d89662f56c68b3ae31d374308270a1c3bd96672ee5ed43414 AS build
RUN apk add --no-cache build-base ca-certificates git
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=1 CGO_CFLAGS="-Wno-error=missing-braces" GOOS=linux \
    go build -tags sqlite_fts5 -trimpath -ldflags="-s -w" -o /out/wacli ./cmd/wacli

FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6
RUN apk add --no-cache ca-certificates ffmpeg tzdata python3 py3-pip \
    && adduser -D -u 10001 -h /home/wacli wacli \
    && mkdir -p /data/store /data/state /data/config /data/cache \
    && chown -R wacli:wacli /data
ENV HOME=/home/wacli \
    WACLI_STORE_DIR=/data/store \
    XDG_STATE_HOME=/data/state \
    XDG_CONFIG_HOME=/data/config \
    XDG_CACHE_HOME=/data/cache
WORKDIR /data
COPY --from=build /out/wacli /usr/local/bin/wacli
RUN python3 -m venv /opt/wa-mcp \
    && /opt/wa-mcp/bin/pip install --no-cache-dir 'mcp==2.3.0'

COPY whatsapp_mcp.py /opt/whatsapp_mcp.py
COPY --from=ghcr.io/openai/tunnel-client:v0.0.16 /usr/bin/tunnel-client /usr/local/bin/tunnel-client
COPY start.sh /usr/local/bin/start.sh
RUN chmod +x /usr/local/bin/start.sh
USER wacli
ENTRYPOINT ["wacli"]
CMD ["--help"]
