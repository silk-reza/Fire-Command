# FIRECOMMAND v1.0
## Dataset DEMO de `seed.sql`

> **Estado:** Propuesta de semilla para revisión antes de ejecutar en MySQL.  
> **Archivo asociado:** `FIRECOMMAND_seed.sql`

---

# 1. Objetivo

La semilla utiliza pocos registros elegidos con intención para probar:

```text
login
roles
estaciones
personal
unidades
compatibilidades
incidentes
despachos
tripulaciones
bitácora
reportería
```

---

# 2. Credenciales DEMO

Contraseña compartida:

```text
FireDemo2026!
```

| Usuario | Rol | Activo | Propósito |
|---|---|---:|---|
| `admin.demo` | ADMINISTRADOR | Sí | Administración |
| `operador.central` | OPERADOR | Sí | Operación |
| `operador.norte` | OPERADOR | Sí | Operación |
| `consulta.sur` | CONSULTA | Sí | Solo lectura |
| `operador.inactivo` | OPERADOR | No | Probar rechazo de login |

En MySQL solo se almacena el hash bcrypt.

---

# 3. Tamaño del dataset

```text
roles                           3
estaciones                      3
personal                       15
usuarios                        5
tipos_unidad                    6
estados_unidad                  7
unidades                       11
tipos_incidente                 6
prioridades                     4
estados_incidente               6
estados_despacho                4
compatibilidades               21
incidentes                      8
despachos                       9
despacho_unidades              12
despacho_personal              22
bitacora                       18
```

---

# 4. Cobertura de incidentes

```text
INC-2026-001 → REPORTADO
INC-2026-002 → ASIGNADO
INC-2026-003 → EN_ATENCION
INC-2026-004 → CONTROLADO
INC-2026-005 → CERRADO
INC-2026-006 → CANCELADO después de asignación
INC-2026-007 → CERRADO histórico
INC-2026-008 → CANCELADO directamente desde REPORTADO
```

Todos los tipos de incidente y las cuatro prioridades aparecen al menos una vez.

---

# 5. Cobertura de unidades

Están representados los siete estados:

```text
DISPONIBLE
DESPACHADA
EN_RUTA
EN_ESCENA
REGRESANDO
MANTENIMIENTO
FUERA_DE_SERVICIO
```

También hay dos variantes de fuera de servicio:

```text
CM-01 → activo = FALSE
CM-02 → activo = TRUE
```

para separar retiro administrativo de indisponibilidad operativa.

---

# 6. Casos de despacho

Se incluyen los cuatro estados de despacho:

```text
CREADO
ACTIVO
COMPLETADO
CANCELADO
```

Casos destacados:

```text
#1 CREADO
preselección con fecha_asignacion = NULL
id_estacion_origen = NULL
```

```text
#2 ACTIVO
una unidad DESPACHADA
otra EN_RUTA
```

```text
#4 ACTIVO
una participación liberada / REGRESANDO
otra todavía EN_ESCENA
```

```text
incidente #5
dos despachos COMPLETADOS
```

```text
#7 CANCELADO después de ACTIVO
#8 CANCELADO directamente desde CREADO
```

---

# 7. Compatibilidades

Hay 21 relaciones de compatibilidad.

Una está desactivada deliberadamente:

```text
OTRO + AUTOBOMBA
activo = FALSE
```

Esto permite comprobar que la recomendación filtre compatibilidades inactivas.

---

# 8. Filosofía del seed

El archivo utiliza:

```sql
START TRANSACTION;
...
COMMIT;
```

No utiliza:

```text
INSERT IGNORE
ON DUPLICATE KEY UPDATE
FOREIGN_KEY_CHECKS = 0
```

Además usa IDs explícitos para que las pruebas posteriores sean reproducibles.

Está pensado para ejecutarse sobre una base limpia creada por:

```text
FIRECOMMAND_schema_FINAL.sql
```

---

# 9. Reporterías que habilita

Entre otras:

```text
incidentes por estado
incidentes por tipo
incidentes por prioridad
incidentes por rango de fechas
despachos por incidente
refuerzos de un incidente
unidades por estación
unidades por estado
unidades disponibles
historial de unidad
tripulación por participación
tiempos de respuesta
duración de incidentes
bitácora por entidad
```

---

# 10. Siguiente fase

Después de revisar esta semilla:

```text
1. ejecutar schema.sql
2. ejecutar seed.sql
3. comprobar conteos
4. ejecutar sanity checks
5. ejecutar pruebas válidas
6. intentar violar PK
7. intentar violar FK
8. intentar violar UNIQUE
9. intentar violar NOT NULL
10. intentar violar CHECK
11. comprobar RESTRICT y SET NULL
12. probar reglas de Python y concurrencia
```
