# Paso 14 — Prueba E2E de la Fase 0 en emulador (diseño)

**Fecha**: 2026-10-29
**Cierra**: Paso 14, checkpoints 14c–14e ([visitacampo-construccion.md §12](visitacampo-construccion.md#12-ajuste-post-diagnóstico-2026-08-03--restricción-de-entorno-resecuenciación-y-metas-intermedias)).
**Criterio de salida** (doc 08 §8): una visita creada offline en un emulador aparece en el servidor tras reconectar, con un procedimiento reproducible y documentado.

## Decisiones

| Tema | Decisión | Descartado |
|---|---|---|
| Backend | Stack local con Docker, efímero por ejecución | VPS de pruebas: ensucia datos compartidos y exige SSH |
| TLS | TOFU real: Caddy firma con su CA interna y la prueba acepta el certificado en `TrustCertificateScreen` | CA instalada en el emulador (toca `network_security_config`); HTTP en claro (rompe RD-01) |
| Ejecución | Script local; sin job de CI | CI con emulador: 15–20 min por ejecución y frágil |
| Offline | Modo avión real del emulador, controlado por el script con `adb` | Parar Caddy: menos fiel al criterio "en modo avión" |
| Adjuntos | Fuera de alcance: la visita va sin fotos | Necesitan un S3 local (issue #21); entran con el Paso 18 |

## Componentes

| Pieza | Repo | Responsabilidad |
|---|---|---|
| `integration_test/e2e_fase0_test.dart` | SUNO_client | Conduce la app real: login, TOFU, captura offline y espera a *Sincronizada* |
| `tool/e2e.sh` | SUNO_client | Orquesta el stack, la siembra, el emulador, el modo avión y la verificación final |
| `infra/scripts/ci_dast_env.sh` | SUNO_server | Acepta `ENV_FILE` y `DAST_HOST` para generar un `.env` efímero sin pisar `infra/.env` |
| `infra/Caddyfile` | SUNO_server | `default_sni {$CADDY_DOMAIN:localhost}` en el bloque global |
| `docs/testing/e2e-fase0.md` | SUNO_server | Procedimiento reproducible y requisitos |

`integration_test/` queda fuera de `flutter test` normal y de la CI.

## Stack del servidor

- El script usa un checkout de SUNO_server (`SUNO_SERVER_DIR`, por defecto `../SUNO_server`).
- Genera `infra/.env.e2e` con `ci_dast_env.sh` (`DAST_HOST=10.0.2.2`): settings de producción, `STORAGE_BACKEND=localfs`, secretos aleatorios de un solo uso. Lo cubre `.gitignore` (`.env.*`).
- Proyecto de compose propio (`-p suno-e2e`) con `--env-file infra/.env.e2e` y solo `infra/docker-compose.yml`: no usa el override de desarrollo, que depende de MinIO.
- Levanta `db`, `api` y `caddy`, y espera a `https://10.0.2.2/v1/healthz` desde el host con `curl --connect-to 10.0.2.2:443:127.0.0.1:443 --cacert <root.crt>`: valida el certificado contra la CA interna de Caddy (`/data/caddy/pki/authorities/local/root.crt`), sin `-k`.
- Siembra con `bootstrap_demo --org-name "E2E <ts>" --username e2e-<ts> --password <aleatoria>`.

### TLS sin SNI

La app se conecta a `https://10.0.2.2` (el host visto desde el emulador). A una IP el cliente no envía SNI, y sin SNI Caddy no sabe qué certificado servir. `default_sni` le dice que use el de `CADDY_DOMAIN`.

- **Desarrollo y E2E**: necesario para conectar por IP.
- **Producción**: los clientes envían SNI; solo cambia que una conexión directa a la IP del VPS recibe el certificado público en vez de un error de TLS. El dominio ya es público (registros de Certificate Transparency).
- **Verificación**: `openssl s_client -connect 127.0.0.1:443` **sin** `-servername` contra el stack E2E devuelve el certificado de `10.0.2.2`. Es la primera tarea del plan; si Caddy no emite certificados por IP con la CA interna, se para y se reabre esta decisión.

## Flujo

1. **Preflight**: `adb devices` muestra un emulador; Docker responde; Flutter en el PATH. Si falta algo, el script sale con instrucciones.
2. **Stack** arriba y sano; siembra; el script guarda usuario y contraseña en variables.
3. El script lanza `flutter test integration_test/e2e_fase0_test.dart -d <emulador>` con `--dart-define`:
   `VISITACAMPO_API_BASE_URL=https://10.0.2.2`, `E2E_USERNAME`, `E2E_PASSWORD`, `E2E_MARKER=e2e-<ts>`.
4. **Prueba**: acepta el aviso de privacidad si aparece, hace login y acepta el certificado (`tofu_compared_check` → `tofu_trust_button`). Llega a la pantalla de inicio y imprime `E2E:READY_FOR_OFFLINE`.
5. **Script** (lee `adb logcat -s flutter`): `adb shell cmd connectivity airplane-mode enable`.
6. **Prueba**: espera a que el modo avión esté activo comprobándolo ella misma: intenta `Socket.connect('10.0.2.2', 443)` cada segundo hasta que falle (máximo 60 s). Luego captura una visita con `notas = <E2E_MARKER>` y comprueba que aparece *Pendiente*. Imprime `E2E:SAVED_OFFLINE`.
7. **Script**: `airplane-mode disable`.
8. **Prueba**: espera a que la visita pase a *Sincronizada* (sincronización automática; máximo 120 s). Imprime `E2E:SYNCED`.
9. **Script**: verifica en el servidor con `manage.py shell -c` que existe exactamente una visita con esa marca en la organización E2E, con `server_version >= 1` y `change_seq` asignado. Es lo que el Admin mostraría.
10. **Limpieza**: `trap` en `EXIT` que desactiva el modo avión, mata el lector de logcat y hace `docker compose -p suno-e2e down -v`.

## Errores

- Cada espera tiene un máximo y falla diciendo en qué paso se quedó: healthz 120 s, marcas de log 180 s, sincronización 120 s.
- Si la prueba falla, el script imprime las últimas líneas de `docker compose logs api caddy` y sale con código distinto de cero.
- El `trap` se ejecuta también con Ctrl+C.
- La prueba no depende de datos previos: organización, usuario y marca son únicos por ejecución.

## Verificación del propio arnés

- `shellcheck tool/e2e.sh` (el lint de CI ya revisa los scripts).
- Dos ejecuciones seguidas en la máquina de desarrollo terminan en verde.
- Un fallo provocado (Docker parado) termina con mensaje claro y sin dejar el emulador en modo avión.

## Fuera de alcance

Adjuntos y fotos (Paso 18), iOS, CI, y conflictos contra el backend real (Paso 23).
