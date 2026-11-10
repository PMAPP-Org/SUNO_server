# Prueba E2E de la Fase 0

Demuestra el criterio de salida de la Fase 0 ([doc 08 §8](../08-arquitectura-propuesta-suno.md#8-plan-de-evolución-por-fases)): un registro creado
**en modo avión** en un emulador Android llega al servidor al reconectar.
Cierra el Paso 14 del plan de construcción.

## Requisitos

- Docker Desktop en marcha.
- Flutter 3.44.9 y Android SDK en el PATH; `openssl` en el PATH (Windows:
  `C:\Program Files\Git\usr\bin`).
- JDK 17 o superior para Gradle. Si el sistema tiene un Java 8, apúntale
  Flutter con `flutter config --jdk-dir <ruta del JDK 17>`.
- Un emulador Android arrancado (cualquiera; API 36 probado, `emulator -avd suno_api36`
  en esta configuración).
- Los puertos 80 y 443 del equipo libres (Caddy los publica; en Windows suelen
  ocuparlos IIS u otro stack de Docker).
- SUNO_server y SUNO_client clonados uno al lado del otro.

## Ejecutar

Desde un checkout de SUNO_client (el script vive allí, en `tool/e2e.sh`):

    bash tool/e2e.sh

Opciones por variable: `SUNO_SERVER_DIR` (por defecto `../SUNO_server`),
`ANDROID_SERIAL` (si hay más de un dispositivo).

**Borra los datos de la app en el emulador** (`pm clear`): sesión, registros
locales y certificados aprobados.

## Qué hace

1. Genera `infra/.env.e2e` con secretos de un solo uso
   (`infra/scripts/ci_dast_env.sh`, settings de producción, `localfs`). Está
   fuera de git (`.env.*`) y se borra al final.
2. Levanta `db`, `api` y `caddy` en el proyecto de compose `suno-e2e`.
   Caddy firma `10.0.2.2` con su CA interna y lo sirve también sin SNI
   (`default_sni`).
3. Siembra una organización y un usuario únicos (`bootstrap_demo --with-schedule`; la tarea solo la usa la Parte B del cliente): además de
   la plantilla, un proyecto con la etapa 1 "Preliminares" (el agente es su responsable) y la
   tarea de detalle 1.1 "Excavación", que hereda la responsabilidad.
4. Lanza `integration_test/e2e_fase0_test.dart` con `flutter drive --no-dds`
   (con `flutter test`, el DDS no llega a conectarse al emulador API 36), que:
   acepta el aviso de privacidad, hace login, **acepta el certificado
   (TOFU)**, sincroniza, espera a que el script active el modo avión,
   captura un registro con una marca única, lo vincula a la tarea 1.1 con 35 % de avance
   acumulado (requiere la Parte B del cliente), comprueba que queda *Pendiente*,
   y tras reconectar espera a *Sincronizada*.
5. Verifica en el servidor que el registro existe en su organización con
   `version` y `change_seq` asignados, vinculado a la tarea 1.1 con `progress_pct` 35 (requiere la Parte B del cliente).
6. Siempre, también si falla: desactiva el modo avión, hace `down -v` y borra
   `infra/.env.e2e`.

El script y la prueba se coordinan por marcas en `adb logcat`:
`E2E:READY_FOR_OFFLINE`, `E2E:SAVED_OFFLINE`, `E2E:SYNCED`.

## Variante de cantidades (costos, pieza 4b)

    E2E_FLOW=quantities bash tool/e2e.sh

Siembra con `bootstrap_demo --with-schedule --with-quantities`: la tarea 1.1 se mide por
cantidades (1.1 "Excavación mecánica", 100 m3 planeados) y el catálogo del proyecto tiene además
1.2 "Retiro de sobrantes". Lanza `integration_test/e2e_quantities_test.dart`, que (con red) captura
12,5 m3 del 1.1 y 3 m3 del 1.2 con nota, sincroniza y marca `E2E:SYNCED`. El script valida
entonces el registro en el servidor (una `RecordReview` aprobada); la prueba sincroniza hasta
ver "validado 12,5 m3" en la tarea y marca `E2E:VALIDATED_SEEN`. Al final el script verifica en
el servidor las dos filas, con `client_id` y `location_mode`, y la revisión aprobada.

## Si falla

- Cada espera tiene límite: la API 120 s (script); en la prueba, cada pantalla
  30–60 s, el modo avión 60 s y la sincronización 120 s. El mensaje de error
  indica en qué paso se quedó.
- `adb: device offline` o `Service connection disposed`: el emulador perdió la
  conexión con adb (suele pasar con el equipo muy cargado). Vuelve a ejecutar.
- Ante un fallo, el script imprime las últimas líneas de
  `docker compose logs api caddy` y termina con código distinto de cero.
- Las marcas de la prueba son líneas `print` leídas con `adb logcat -s flutter`;
  ese comando muestra en qué punto va la ejecución.
- El emulador no se deja en modo avión: `adb shell settings get global airplane_mode_on`
  debe devolver `0`.

## Notas para Windows (Git Bash)

- Git Bash reescribe las rutas absolutas de contenedor (p. ej.
  `docker compose exec ... cat /data/...`). El script exporta
  `MSYS_NO_PATHCONV=1` para evitarlo.
- curl en Windows usa Schannel, que no puede comprobar la revocación de la CA
  privada de Caddy y la conexión falla con un error de revocación. El script
  pasa `--ssl-no-revoke`; el certificado se sigue validando contra la raíz de
  Caddy.

## Fuera de alcance

Fotos y adjuntos (Paso 18; necesitan un S3 local, issue #21), iOS y CI.
