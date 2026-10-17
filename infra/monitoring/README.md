# Monitoreo de salud del servidor

Decisión de diseño: **ADR-014** en [`docs/08-arquitectura-propuesta-visitacampo.md`](../../docs/08-arquitectura-propuesta-visitacampo.md). Control ISO/IEC 27001:2022 A.8.16 (actividades de monitorización).

Son dos capas complementarias:

| Capa | Dónde corre | Qué ve | Avisa por |
|---|---|---|---|
| **Interna** (`docker-compose.monitoring.yml`) | En el servidor | CPU, memoria, disco, carga; PostgreSQL; sondas HTTPS a la API a través de Caddy; caducidad del certificado; antigüedad del último backup | Correo (Alertmanager) |
| **Externa** (`.github/workflows/uptime.yml`) | En GitHub, cada 15 min | Si la API responde **desde internet** (DNS, red, TLS, Caddy, API, BD) | Issue con etiqueta `incidente` |

La externa existe porque la interna no puede avisar si se cae el servidor entero.

## Activar

En el `.env` del servidor:

```bash
COMPOSE_FILE=docker-compose.yml:docker-compose.monitoring.yml
ALERT_EMAIL_TO=equipo@example.com      # vacío = sin avisos por correo
ALERT_EMAIL_FROM=alertas@tu-dominio
GRAFANA_ADMIN_PASSWORD=<generada>      # p. ej. openssl rand -base64 24
# El correo sale por el SMTP de la app: EMAIL_HOST, EMAIL_PORT,
# EMAIL_HOST_USER, EMAIL_HOST_PASSWORD, EMAIL_USE_TLS.
```

Después: `docker compose up -d`. Para la vigilancia externa, define la variable de repositorio `PRODUCTION_URL` (p. ej. `https://visitas.example.com`).

**Memoria:** el monitoreo suma unos 400–500 MB (Prometheus ~200, Grafana ~100, el resto ~20–30 cada uno). Con el stack de la app hace falta un VPS de **al menos 2 GB**. Prometheus guarda 15 días de métricas (`PROMETHEUS_RETENTION`).

## Acceder

Nada se publica a internet: las interfaces escuchan solo en `127.0.0.1` del servidor. Se abren con un túnel SSH desde tu equipo:

```bash
ssh -L 3000:localhost:3000 -L 9090:localhost:9090 -L 9093:localhost:9093 deploy@servidor
```

- **Grafana**: http://localhost:3000, usuario `admin`. Abre directamente el panel *VisitaCampo — Salud del servidor*.
- **Prometheus**: http://localhost:9090 (targets, reglas, consultas).
- **Alertmanager**: http://localhost:9093 (alertas activas, silencios durante mantenimientos).

## Alertas

Definidas en [`prometheus/alerts.yml`](prometheus/alerts.yml), con pruebas unitarias en [`prometheus/alerts_test.yml`](prometheus/alerts_test.yml).

| Alerta | Severidad | Cuándo |
|---|---|---|
| `ApiDown` | critical | `/v1/healthz` falla 2 min |
| `ApiNotReady` | critical | `/v1/readyz` falla 5 min (BD o almacenamiento) |
| `ApiSlow` | warning | Sondas por encima de 2 s durante 10 min |
| `TlsCertExpiringSoon` / `TlsCertExpiryCritical` | warning / critical | Certificado a menos de 14 / 3 días |
| `BackupStale` | critical | Último backup correcto hace más de 26 h |
| `BackupMissing` | critical | Nunca hubo un backup correcto (26 h) |
| `PostgresDown` | critical | PostgreSQL no responde 1 min |
| `PostgresTooManyConnections` | warning | Más del 80 % de `max_connections` |
| `HostCpuHigh` / `HostMemoryLow` | warning | CPU > 90 % 15 min / memoria libre < 10 % |
| `HostDiskAlmostFull` / `HostDiskWillFillIn24h` | warning / critical | < 15 % libre / proyección a 24 h |
| `TargetDown` | warning | Un exportador no responde 5 min |

Mientras `ApiDown` está activa, se suprimen `ApiSlow` y `ApiNotReady`, para no recibir tres correos por un mismo problema.

## Decisiones

- **Sin cAdvisor:** para métricas por contenedor necesita el socket de Docker, y acceder a ese socket equivale a ser root en el host. La salud de cada servicio se ve por sus efectos: sondas a la API, `pg_up` y la métrica del backup.
- **La sonda verifica el certificado** y no lo ignora. Un certificado inválido es una caída para la app móvil, que no acepta certificados inválidos (RD-01).
- **Configuración versionada:** los dashboards y las reglas se cambian por PR, y los cambios hechos en la interfaz de Grafana no persisten (A.8.32).

## Cómo se verifica

`.github/workflows/monitoring.yml`, en cada PR:
- valida la configuración con `promtool` y `amtool` y ejecuta las pruebas unitarias de las alertas;
- levanta el stack de producción con el monitoreo y comprueba que los 7 targets están `up`, que las sondas pasan con TLS verificado, que Grafana tiene datasource y dashboard y que rechaza peticiones sin credenciales;
- **detiene la API y exige que `ApiDown` llegue a Alertmanager.**
