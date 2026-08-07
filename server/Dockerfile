##########################
# BUILD                  #
##########################
# Instala las dependencias en un stage aparte para no arrastrar caché de pip
# ni artefactos de compilación a la imagen final.
FROM python:3.12-slim-bookworm AS build

WORKDIR /usr/src/app

COPY requirements.txt requirements-runtime.txt ./
# psycopg[binary]==3.3.4 (server/requirements.txt) ya empaqueta libpq: no se
# instala libpq-dev/gcc/python3-dev aquí (diferencia deliberada con
# qfieldcloud/docker-app/Dockerfile, que no fija el extra [binary]).
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt -r requirements-runtime.txt

##########################
# WEBSERVER RUNTIME      #
##########################
FROM python:3.12-slim-bookworm AS webserver_runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# tzdata: VisitaCampo fija TIME_ZONE="America/Bogota" (config/settings/base.py)
# y necesita la base de datos de zonas horarias del sistema para resolverla.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system app && adduser --system app --ingroup app

WORKDIR /usr/src/app

COPY --from=build /install /usr/local

COPY . .
RUN mkdir -p staticfiles \
    && chmod +x entrypoint.sh \
    && chown -R app:app /usr/src/app

USER app

EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
# uvicorn_worker.UvicornWorker (paquete uvicorn-worker) en vez de la clase
# uvicorn.workers.UvicornWorker empaquetada dentro de uvicorn (deprecada/
# retirada en versiones recientes de uvicorn).
CMD ["gunicorn", "config.asgi:application", "--bind", "0.0.0.0:8000", "--worker-class", "uvicorn_worker.UvicornWorker", "--workers", "3"]
