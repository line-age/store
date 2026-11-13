# The county loader (scripts/load-counties.sh): GDAL plus unzip, which the Newberry zip needs.
FROM ghcr.io/osgeo/gdal:ubuntu-small-3.10.3
RUN apt-get update && apt-get install -y --no-install-recommends unzip && rm -rf /var/lib/apt/lists/*
COPY scripts/load-counties.sh /usr/local/bin/load-counties
CMD ["load-counties"]
