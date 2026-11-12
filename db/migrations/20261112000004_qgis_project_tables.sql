-- migrate:up

-- The two tables QGIS reads, in the layouts QGIS itself creates, so `lineage-store qgis
-- install` can put a ready-made project and a default style in the database:
--
-- - Projects can live in any schema; ours sit next to the views they open. QGIS lists them
--   under the connection's `qgis` schema (Project > Open From > PostgreSQL).
-- - Default layer styles are only ever read from public.layer_styles; QGIS applies the
--   default one when anyone adds a layer by hand.

CREATE TABLE IF NOT EXISTS qgis.qgis_projects (
    name     text PRIMARY KEY,
    metadata jsonb,
    content  bytea
);

CREATE TABLE IF NOT EXISTS public.layer_styles (
    id                serial PRIMARY KEY,
    f_table_catalog   varchar,
    f_table_schema    varchar,
    f_table_name      varchar,
    f_geometry_column varchar,
    stylename         text,
    styleqml          xml,
    stylesld          xml,
    useasdefault      boolean,
    description       text,
    owner             varchar(63) DEFAULT CURRENT_USER,
    ui                xml,
    update_time       timestamp DEFAULT CURRENT_TIMESTAMP,
    type              varchar,
    r_raster_column   varchar
);

-- migrate:down

DROP TABLE qgis.qgis_projects;
-- public.layer_styles may hold styles people saved themselves, so it stays.
