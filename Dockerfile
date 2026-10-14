FROM node:24-slim AS console
WORKDIR /src
COPY . .
RUN npm install --global pnpm@10.34.5 && pnpm --dir frontend install --frozen-lockfile && pnpm --dir frontend build

FROM python:3.13-slim
LABEL "language"="python"
LABEL "framework"="fastapi"
WORKDIR /src
COPY . .
COPY --from=console /src/packages/channel-gateway-server/channel_gateway_server/static /src/packages/channel-gateway-server/channel_gateway_server/static
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv
RUN uv sync --locked --no-dev --package channel-gateway-server --no-editable && groupadd --gid 10001 ac && useradd --uid 10001 --gid ac --no-create-home ac && mkdir /data
ENV CGW_DATA_DIR=/data/state
EXPOSE 8080
CMD ["python", "deploy/container-start.py"]
