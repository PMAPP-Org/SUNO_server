# Despliegue continuo de `master`

## Decisión

Separar despliegue y release. `deploy-master.yml` publica y despliega cada commit de `master`; `release.yml` conserva el versionado y los artefactos de release.

## Tareas

- [x] Construir una imagen inmutable por SHA de `master`.
- [x] Firmar su procedencia y entregar el digest al workflow reutilizable.
- [x] Permitir que `deploy.yml` reciba tag o SHA y ejecute el script del código objetivo.
- [x] Hacer `deploy.sh` compatible con tags, SHA y ambos nombres históricos de la variable de imagen.
- [x] Mantener rollback y despliegue manual por tag.
- [x] Actualizar pruebas de infraestructura y runbook.

## Riesgos y mitigaciones

- **Código e imagen distintos:** ambos se fijan al mismo SHA y la imagen se pasa por digest.
- **Cambio incompatible del propio script:** el workflow obtiene `deploy.sh` del commit objetivo antes de ejecutarlo.
- **Rollback entre nombres de paquete:** se exportan y persisten las dos variables de imagen durante la transición.
- **Producción sin secretos:** el workflow falla explícitamente en lugar de informar éxito sin despliegue.
