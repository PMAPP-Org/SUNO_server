##########################
# BUILD                  #
##########################
# Instala las dependencias en un stage aparte para no arrastrar caché de pip
# ni artefactos de compilación a la imagen final.
#
# La imagen base va fijada por digest (ISO 27001 A.8.9): el tag solo es
# legible para humanos; Dependabot (ecosistema docker) propone el digest nuevo.
FROM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83 AS build

WORKDIR /usr/src/app

COPY requirements.lock ./
# psycopg[binary]==3.3.4 (server/requirements.txt) ya empaqueta libpq: no se
# instala libpq-dev/gcc/python3-dev aquí (diferencia deliberada con
# qfieldcloud/docker-app/Dockerfile, que no fija el extra [binary]).
#
# Se instala el lockfile (requirements.txt + requirements-runtime.txt
# resueltos con hashes) y no las fuentes: con `--require-hashes` el build es
# reproducible y pip rechaza cualquier paquete cuyo contenido no coincida.
RUN pip install --no-cache-dir --require-hashes --prefix=/install -r requirements.lock

##########################
# WEBSERVER RUNTIME      #
##########################
FROM python:3.14-slim-bookworm@sha256:48b13b003dda20b16f9442b8475aa05fe21bf6579a8c881db92ffb4d8fd20f83 AS webserver_runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# tzdata: VisitaCampo fija TIME_ZONE="America/Bogota" (config/settings/base.py)
# y necesita la base de datos de zonas horarias del sistema para resolverla.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# UID/GID numéricos: un orquestador puede verificar `runAsNonRoot` sin
# resolver nombres (hadolint DL3066).
RUN addgroup --system --gid 10001 app && adduser --system --uid 10001 --ingroup app app

WORKDIR /usr/src/app

COPY --from=build /install /usr/local

# El código queda propiedad de root y de solo lectura para el proceso: si
# alguien lograra ejecutar código en el contenedor, no podría reescribir la
# aplicación (OWASP A02/A08). El usuario de runtime solo escribe donde hace
# falta: `staticfiles/` (collectstatic en entrypoint.sh) y `.blobstore/`
# (adaptador LocalFsBlobStore, ADR-007).
COPY . .
RUN mkdir -p staticfiles .blobstore \
    && chmod +x entrypoint.sh \
    && chown -R 10001:10001 staticfiles .blobstore

USER 10001:10001

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
# uvicorn_worker.UvicornWorker (paquete uvicorn-worker) en vez de la clase
# uvicorn.workers.UvicornWorker empaquetada dentro de uvicorn (deprecada/
# retirada en versiones recientes de uvicorn).
CMD ["gunicorn", "config.asgi:application", "--bind", "0.0.0.0:8000", "--worker-class", "uvicorn_worker.UvicornWorker", "--workers", "3"]
