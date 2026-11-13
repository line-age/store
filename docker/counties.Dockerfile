# The county loader (scripts/load-counties.sh): GDAL plus unzip, which the Newberry zip needs.
# Published as ghcr.io/line-age/store-counties.
FROM ghcr.io/osgeo/gdal:ubuntu-small-3.10.3
RUN apt-get update && apt-get install -y --no-install-recommends unzip && rm -rf /var/lib/apt/lists/* \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin lineage
COPY scripts/load-counties.sh /usr/local/bin/load-counties
# Not root; it only needs a writable /tmp for the download.
USER 10001
CMD ["load-counties"]
