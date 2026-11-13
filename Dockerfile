# The store image: the API (`lineage-store serve`), the admin CLI, and the database
# migrations with dbmate to run them, so a deployment needs nothing but this image and a
# PostGIS database. Published as ghcr.io/line-age/store.
FROM python:3.14-slim
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
COPY --from=ghcr.io/amacneil/dbmate:2.38 /usr/local/bin/dbmate /usr/local/bin/dbmate
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY model ./model
COPY src ./src
RUN uv sync --frozen --no-dev
COPY db/migrations ./db/migrations
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    DBMATE_MIGRATIONS_DIR=/app/db/migrations \
    DBMATE_NO_DUMP_SCHEMA=true
# Not root. A numeric id, so Kubernetes can check runAsNonRoot.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin lineage
USER 10001
EXPOSE 8000
CMD ["lineage-store", "serve", "--port", "8000"]
