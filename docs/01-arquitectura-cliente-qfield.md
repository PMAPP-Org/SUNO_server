# Arquitectura del cliente QField

Repositorio: `../../QField`

## 1. Stack tecnológico

- **Lenguajes**: C++ (lógica/core) + QML/JS (UI declarativa).
- **Framework**: Qt 6 (`Concurrent, Core, Qml, Gui, Xml, Positioning, Widgets, Network, Quick, Svg, Sql, Sensors, WebView, Multimedia, Bluetooth, Nfc, RemoteObjects, WebSockets, Quick3D` — `CMakeLists.txt:173`). UI cargada vía `QQmlApplicationEngine`.
- **Motor SIG**: **QGIS embebido como librería** (no como servidor externo) — QField es literalmente un frontend táctil sobre el motor de QGIS (`qgsproject.h`, `qgsvectorlayer.h`, `qgsapplication.h`, etc. usados directo en el core).
- **Build**: CMake ≥ 3.19 + **vcpkg** para dependencias, con overlay ports/triplets propios (`vcpkg/ports`, `vcpkg/triplets`).
- **Dependencias notables** (`vcpkg.json`): `gdal`, `ffmpeg` (video), `zxing-cpp` (QR/códigos de barras), `qca` (cripto), `qtwebdav`, `sentry-native`/`sentry-cocoa` (crash reporting), `catch2` (tests), `spix-qt6` (tests funcionales UI).
- **Plataformas**: Android, iOS, Linux, macOS, Windows (`platform/{android,ios,linux,macos,windows}/`).

### Estructura de carpetas

| Carpeta | Contenido |
|---|---|
| `src/core/` | Lógica de negocio pura en C++, sin dependencia directa de UI (geoespacial, cloud/sync, positioning, deltas). |
| `src/gui/` | Modelos/adaptadores orientados a UI (list models, image providers) — capa intermedia. |
| `src/qml/` | Interfaz completa (~150+ archivos `.qml`). |
| `src/app/` | Ejecutable: `main.cpp`, `QgisMobileapp` (orquestador), plugin manager, servicios Android. |
| `src/service/` | Servicios en background. |
| `platform/` | Empaquetado/nativo por SO. |
| `test/` | Tests Catch2, QML (QuickTest), Spix (funcional/UI). |
| `fastlane/` | Automatización de builds/firma/distribución móvil. |

### Compilación en 3 capas estáticas encadenadas

```
qfield_core (STATIC)  →  qfield_gui (STATIC, linka core)  →  qfield_app (STATIC, linka gui)  →  ejecutable
```
(`src/core/CMakeLists.txt:292`, `src/gui/CMakeLists.txt:53,66`, `src/app/CMakeLists.txt:91,114`)

## 2. Arquitectura general

- **Patrón**: MVVM implícito — objetos C++ (`QObject`) con `Q_PROPERTY` + señales `NOTIFY` actúan de ViewModel/Model; QML es la View pura (solo binding, sin lógica de negocio).
- **Orquestador central**: `QgisMobileapp` (`src/app/qgismobileapp.h/.cpp`), hereda de `QQmlApplicationEngine` y de `AppController`. Carga el proyecto QGIS, inicializa `QgsOfflineEditing`, `LayerObserver`, `PluginManager`, modelos de tracking, bookmarks, etc.
- **Exposición a QML** — dos mecanismos combinados:
  1. **Context properties** (singletons inyectados en `rootContext()`), en `QgisMobileapp::initDeclarativeContext` (`qgismobileapp.cpp:429-485`):
     ```cpp
     rootContext()->setContextProperty( "qgisProject", mProject );
     rootContext()->setContextProperty( "iface", mIface );
     rootContext()->setContextProperty( "pluginManager", mPluginManager );
     rootContext()->setContextProperty( "layerObserver", mLayerObserver.get() );
     rootContext()->setContextProperty( "gpkgFlusher", mGpkgFlusher.get() );
     rootContext()->setContextProperty( "trackingModel", mTrackingModel );
     ```
  2. **Registro de tipos QML** (`import org.qfield`), centralizado por capa: `src/core/qfieldcoreqmlregistration.cpp` (~50 tipos vía `qmlRegisterType<T>`, singletons con macro `REGISTER_SINGLETON`), `src/gui/qfieldguiqmlregistration.cpp`, `src/app/qfieldappqmlregistration.cpp`.
- **Gestión de estado**: sin store centralizado tipo Redux; el estado vive en `QObject`s con propiedades reactivas. Máquinas de estado explícitas con `enum class` + `Q_ENUM`: `QFieldCloudConnection::ConnectionStatus` (Disconnected/Connecting/LoggedIn), `QFieldCloudProject::ProjectStatus` (Idle/Downloading/Pushing/Uploading/Failing), `DeltaFileStatus` (Local/Pending/Busy/Conflict/NotApplied/Applied/Error).
- **Separación de responsabilidades**: `core` sin UI, `gui` aporta modelos/proveedores de imágenes, `app` maneja ciclo de vida, permisos, deep-linking (`qfieldurlhandler.cpp`, esquema `qfield://`) y gestor de plugins.

## 3. Datos geoespaciales y modo offline

- **Proyecto**: archivos `.qgs`/`.qgz` (XML/zip) o embebido en **GeoPackage** vía `QgsGeoPackageProjectStorage` (`qgismobileapp.cpp:644-657`). Extensiones soportadas: `SUPPORTED_PROJECT_EXTENSIONS = {"qgs", "qgz"}` (`src/core/qfield.h.in:66`).
- **Almacenamiento local de datos**: capas vectoriales en **GeoPackage (SQLite/OGR)**, gestionadas con `QgsVectorLayer`/`QgsOfflineEditing` (`mOfflineEditing = new QgsOfflineEditing()`, `qgismobileapp.cpp:318`).
- **`QgsGpkgFlusher`** (context property `gpkgFlusher`): fuerza escritura periódica/segura de `.gpkg` a disco — crítico para apps de campo con apagones o cierres abruptos.
- **Preparación offline/cloud de capas**: propiedad personalizada `QFieldSync/cloud_action` por capa (`NO_ACTION`, `REMOVE`, etc., leída en `qfieldcloudutils.cpp:63-70`). El plugin de escritorio **QFieldSync** (en QGIS Desktop, fuera de este repo) marca las capas antes de subir el proyecto; QField solo interpreta esas marcas — separación clara entre "preparación de datos" (desktop) y "consumo en campo" (móvil).
- **Gestión de capas**: `LayerTreeModel`/`FlatLayerTreeModel`/`LayerTreeMapCanvasBridge`, `LayerResolver` (resuelve referencias rotas de capas).
- **GPS/sensores** (`src/core/positioning/`):
  - `Positioning`: orquestador con `positionInformation`, `sourcePosition`, `projectedPosition`, corrección de elevación, promediado de posición, soporte NTRIP (RTK).
  - Patrón **Strategy** vía `AbstractGnssReceiver`, con implementaciones intercambiables: `InternalGnssReceiver`, `BluetoothReceiver`/`BluetoothLowEnergyReceiver`, `SerialPortReceiver`, `TcpReceiver`/`UdpReceiver`, `NmeaGnssReceiver`, `FileReceiver` (reproducción de logs NMEA para test), `EgeniousReceiver`.
  - `Geofencer` (cercas geográficas), `NtripClient`/`NtripSettings` (correcciones RTK).
  - Servicio Android dedicado: `platform/android/src/ch/opengis/qfield/QFieldPositioningService.java`.
- **Modo offline**: funciona sin red porque (a) proyecto y datos viven localmente en GeoPackage, (b) las ediciones se registran como "deltas" locales en vez de escribir directo al servidor, (c) `QFieldCloudConnection::isReachable()` encola pushes (`queueProjectPush`) cuando no hay red y reintenta al reconectar (`tryFlushQueuedProjectPushes`).

## 4. Autenticación

- **Login** (`QFieldCloudConnection::login`, `qfieldcloudconnection.cpp:412-543`): usuario/contraseña clásico (`POST /api/v1/auth/token/`) o **SSO/OAuth2** vía "authentication providers", delegando el flujo al `QgsAuthManager` de QGIS.
- **Credenciales nunca en texto plano**: tokens persistidos en la base de autenticación cifrada de QGIS (`QgsApplication::authManager()`, SQLite cifrado); solo el *id* de la config (`tokenConfigId`) se guarda en `QSettings`.
- **Master password del almacén**: en plataformas no-Linux se integra con el keychain del sistema (Keychain/DPAPI/Credential Manager) y se genera una password aleatoria de 32 caracteres si no existe (`main.cpp:80-112`). En Linux usa una fija (`"qfield"`) — limitación documentada.
- **Sesión expirada**: cualquier HTTP 401 invalida el token y fuerza `ConnectionStatus::Disconnected`.

## 5. Plugins/extensibilidad

- Sistema propio en `src/app/pluginmanager.{h,cpp}` + `pluginmodel.{h,cpp}`.
- **Plugins basados en QML** (no binarios nativos): paquete con un `main.qml` cargado dinámicamente vía `QQmlComponent`.
- Metadatos vía `PluginInformation` (Q_GADGET): `uuid`, `name`, `description`, `author`, `homepage`, `icon`, `version`, `bundled`.
- **Permisos por plugin**: persistidos en `QSettings` (`/qfield/plugins/{pluginKey}/permissionGranted`); requiere consentimiento explícito del usuario antes de cargar. Distingue **plugins de proyecto** (embebidos en un proyecto QGIS/cloud) de plugins de app instalados globalmente.

## 6. Testing

- **Unitario**: Catch2 v3 + CTest, linkeado contra las 3 libs de producción (`qfield_app/qfield_gui/qfield_core`) — los tests corren contra el código real, no mocks separados. Cobertura ~1:1 por modelo/servicio del core y gui.
- **QML**: `QuickTest` (Qt) — formularios, editor de geometría, login cloud, widgets de edición.
- **Funcional end-to-end**: `test/spix/` con **Spix** (bot que interactúa con la UI real compilada) + scripts Python (`smoke_test.py`).
- **Infra auxiliar de test**: `test/nmea_server` (GPS simulado), `test/postgis_docker`, `test/qgis_server_docker` (backend real para pruebas de integración).

## 7. CI/CD

- `.github/workflows/`: pipelines separados por plataforma (`android.yml`, `ios.yml`, `linux.yml`, `macos.yml`, `windows.yml`), más `codeql.yml`, `pre-commit.yml`, `documentation.yml` (mkdocs), `translations.yml` (Transifex), `dependabot.yaml`, `stale.yml`.
- **Android**: matriz por arquitectura (`arm64/arm-neon/x64/x86-android`) × variante de permisos de archivos — 8 builds por push.
- **Fastlane**: `upload_s3` (Windows/iOS sube artefactos a S3-compatible), `setup_signing`/`match` (certificados iOS), `upload_testflight`.
- **Sentry** para crash reporting en producción.

## 8. Patrones reutilizables para VisitaCampo

1. **3 capas C++ estáticas** (`core` sin UI → `gui` presentación → `app` orquestación), cada una con su registro QML — facilita testear el core aislado y mantener la UI "tonta".
2. **Offline-first con cola de cambios tipo outbox/delta**: ediciones locales se acumulan como operaciones versionadas (create/update/delete) con snapshot old/new, se aplican de inmediato (UI optimista) y se sincronizan en batch — aplicable directo a "visitas de campo" (cada visita/formulario = un delta).
3. **Servicios nativos en background** para seguir subiendo datos/tracking GPS con la app minimizada.
4. **Abstracción de fuente de posición** (`AbstractGnssReceiver`) — permite testear sin hardware real y soportar receptores externos a futuro.
5. **Credenciales nunca en texto plano** — usar el keychain/keystore nativo en vez de preferencias planas.
6. **Máquinas de estado explícitas** (`enum class` + señales) para status de red/proyecto/sync, en vez de banderas booleanas sueltas.
7. **Sistema de plugins QML dinámico con permisos explícitos** — inspiración para "formularios configurables" sin recompilar el binario.
