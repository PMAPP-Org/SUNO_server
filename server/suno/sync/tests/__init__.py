"""Tests del motor de sincronización (Paso 8).

**Reparto deliberado entre lo que se ejecuta aquí y lo que no.** El push es
código de Postgres: `pg_advisory_xact_lock`, `ON CONFLICT ... RETURNING` y el
`UPDATE ... WHERE version = %s RETURNING` no tienen equivalente en SQLite, así
que la mitad de integración de esta suite se marca `skip_without_db` igual que
en los Pasos 5-7. Lo que sí se ejecuta sin base de datos es todo lo que se
pudo escribir como función pura, y no es poco: la validación por operación
(`test_payloads.py`), el texto exacto de las sentencias CAS (`test_sql.py`),
el reparto de `change_seq` y la guarda de transacción del lock
(`test_changeseq.py`) y la coherencia de los límites de lote
(`test_limits.py`).

`test_concurrency.py` está escrito y **no se ha ejecutado nunca**: exige dos
conexiones reales compitiendo por el mismo advisory lock. Su docstring lo dice
en sus propios términos.
"""
