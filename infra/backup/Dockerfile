# Imagen del servicio `backup` (ISO 27001 A.8.13). Ver infra/backup/README.md.
#
# Misma imagen base que el servicio `db` (mismo digest): `pg_dump` y
# `pg_restore` tienen que ser de la misma versión mayor que el servidor.
# Encima: `age` (cifrado con llave pública) y `rclone` (subida S3/R2).
FROM postgres:18@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336

RUN apt-get update \
    && apt-get install -y --no-install-recommends age rclone ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --chmod=0755 backup.sh restore.sh loop.sh /usr/local/bin/

# El volumen de métricas hereda en su primer montaje el dueño de este
# directorio, así el usuario sin privilegios puede escribir en él.
RUN mkdir /metrics && chown 999:999 /metrics

# Usuario sin privilegios de la propia imagen de postgres (UID 999). El
# contenedor no necesita ser root: solo habla con la BD por red, escribe en
# /tmp (tmpfs en compose) y en el volumen de métricas.
USER 999:999
WORKDIR /tmp

ENTRYPOINT []
CMD ["loop.sh"]
