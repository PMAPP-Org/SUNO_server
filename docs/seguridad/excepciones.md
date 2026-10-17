# Registro de excepciones de seguridad

Toda vulnerabilidad o hallazgo que se acepta en vez de corregirse queda registrado aquí: qué es, por qué no aplica o no se puede corregir aún, quién lo aceptó y **cuándo caduca la excepción**. Es la evidencia de gestión de vulnerabilidades técnicas y de aceptación de riesgo de ISO/IEC 27001:2022 (A.8.8 y cláusula 6.1.3).

Reglas:
- Una excepción tiene fecha de caducidad. Al vencer, el gate correspondiente vuelve a fallar solo, porque la supresión en la herramienta también caduca o se revisa en ese momento.
- Se suprime por identificador exacto y en el alcance mínimo: una CVE en un lockfile concreto, nunca una regla entera.
- Los falsos positivos de SAST (Bandit, Semgrep, gitleaks) no se registran aquí: se justifican junto a la supresión, en el código o en `.gitleaksignore`.

| ID | Hallazgo | Alcance | Motivo | Mitigación | Aceptado | Caduca |
|---|---|---|---|---|---|---|
| EXC-001 | CVE-2026-59951 / GHSA-84m7-p3x7-pcfv en `mcp` 1.29.0: acumulación ilimitada de sesiones en el transporte StreamableHTTP del **servidor** MCP (denegación de servicio) | Solo `server/requirements-sast.lock`, dependencia transitiva de Semgrep, que la fija con `mcp==1.29.0` | En CI solo se ejecuta `semgrep scan`, que no arranca el servidor MCP ni abre puertos; el runner es efímero. Semgrep 1.181.0 es la última versión publicada | Dependabot propondrá la versión de Semgrep que suba `mcp`; supresiones con caducidad en `osv-scanner.toml` y en el paso `pip-audit` de SAST | 2026-10-17, PR de gobierno del repo (pendiente de aprobación) | 2026-12-31 |
