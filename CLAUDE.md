# FIRECOMMAND v1.0 — Instrucciones del Proyecto

## Estado del proyecto

La fase de diseño de base de datos está cerrada y ya fue validada en MySQL.

Trata como fuente de verdad los siguientes artefactos aprobados:

- `schema.sql`
- `seed.sql`
- máquinas de estado aprobadas
- documentos de control del proyecto
- reglas de negocio ya cerradas

No modifiques el esquema de base de datos, la estructura del seed, las máquinas de estado ni reglas de negocio aprobadas salvo que el usuario solicite explícitamente un cambio de diseño.

## Tecnología

- Python 3.x
- MySQL 8.0.16+
- `mysql-connector-python`
- `bcrypt`
- CustomTkinter / Tkinter
- ReportLab para reportes PDF
- Funcionalidad de mapa únicamente si los objetivos obligatorios ya están seguros

## Arquitectura obligatoria

FireCommand utiliza MVC.

La dirección de dependencias es:

```text
View
  ↓
Controller
  ↓
Model
  ↓
MySQL
```

Las Views nunca ejecutan SQL directamente.

Los Controllers son responsables de:

- autorización;
- coordinación de acciones de negocio;
- validaciones dependientes del flujo;
- comunicación entre View y Model.

Los Models son responsables de:

- acceso a datos;
- consultas SQL parametrizadas;
- transformación básica de filas devueltas por MySQL.

Se pueden introducir Services o Utilities únicamente cuando resuelvan una responsabilidad compartida concreta, por ejemplo:

- sesión;
- generación de reportes;
- configuración;
- lógica de dominio reutilizable.

No crear capas, patrones o abstracciones solo para aparentar mayor complejidad.

## Convenciones de idioma y estilo de código

Las instrucciones, explicaciones, reportes y comunicación sobre el proyecto deben escribirse preferentemente en español.

El código Python debe utilizar nombres en inglés para:

- variables;
- funciones;
- métodos;
- clases;
- módulos;
- constantes;
- atributos internos;
- nombres de tests.

Ejemplos correctos:

```python
user_id
username
password_hash
authenticate_user()
get_available_units()
AuthController
UserModel
```

Evitar identificadores Python como:

```python
id_usuario
autenticar_usuario()
obtener_unidades()
```

excepto cuando se haga referencia directa a nombres físicos existentes del esquema MySQL.

### Nombres del esquema MySQL

Los nombres físicos ya aprobados de tablas y columnas permanecen exactamente como están.

Ejemplos:

```text
usuarios
id_usuario
nombre_usuario
fecha_asignacion
despacho_unidades
```

No traduzcas ni renombres columnas o tablas existentes para hacerlas coincidir con las convenciones Python.

Cuando sea útil, transforma esos nombres al salir del Model hacia estructuras Python con claves en inglés.

Ejemplo conceptual:

```python
{
    "user_id": row["id_usuario"],
    "username": row["nombre_usuario"],
    "role_code": row["codigo_rol"],
}
```

### Comentarios

Los comentarios dentro del código deben escribirse en español.

Deben ser breves, naturales y aportar contexto real.

Preferir:

```python
# Rechaza cuentas desactivadas aunque la contraseña sea correcta
if not user["active"]:
    return None
```

o:

```python
# Bloquea la unidad para evitar que otro despacho la tome al mismo tiempo
cursor.execute(query, params)
```

Evitar comentarios decorativos o encabezados grandes como:

```python
# ==========================================================
# AUTENTICACIÓN DEL USUARIO
# ==========================================================
```

Evitar también comentarios que únicamente repitan lo que ya dice el código:

```python
# Suma a y b
result = a + b
```

Los comentarios deben parecer comentarios normales de desarrollo, no documentación académica generada automáticamente.

### Docstrings

Cuando una función, método o clase realmente necesite documentación, utilizar docstrings breves en español.

Ejemplo:

```python
def authenticate_user(username: str, password: str):
    """Valida las credenciales y devuelve el usuario autenticado."""
```

No agregar docstrings extensos a todas las funciones si el código ya es suficientemente claro.

### Texto visible para el usuario

Los mensajes, labels, errores y textos de interfaz de FireCommand deben mostrarse en español, salvo que el usuario solicite lo contrario.

## Reglas de base de datos

- Utiliza SQL parametrizado. Nunca concatenes entrada del usuario dentro de una consulta.
- Usa el usuario MySQL de aplicación existente; no uses `root`.
- Nunca hardcodees credenciales de base de datos.
- No imprimas ni expongas el contenido de `.env`.
- No agregues `.env` al repositorio.
- Las operaciones de negocio que afecten varias filas/tablas deben utilizar transacciones.
- Utiliza `SELECT ... FOR UPDATE` cuando las reglas aprobadas requieran protección contra concurrencia.
- Las entradas de bitácora que formen parte de una operación crítica deben hacer `COMMIT` o `ROLLBACK` junto con esa operación.
- No agregues triggers, procedimientos almacenados, `ENUM`, nuevas tablas de estado o nuevas entidades sin aprobación explícita.
- Conserva los registros históricos según las reglas del esquema aprobado.
- No modifiques `schema.sql` o `seed.sql` para resolver rápidamente un problema de aplicación sin autorización.

## Reglas de máquinas de estado

No evites ni simplifiques las máquinas aprobadas de:

- incidentes;
- despachos;
- unidades.

Los estados operativos cambian únicamente mediante acciones explícitas de negocio.

No implementar un ComboBox genérico que permita cambiar libremente estados operativos.

En v1.0 no existe reapertura de incidentes o despachos terminales.

## Autorización

Roles internos:

- `ADMINISTRADOR`
- `OPERADOR`
- `CONSULTA`

No confíes únicamente en ocultar botones de la GUI para implementar autorización.

Los Controllers deben rechazar operaciones no autorizadas aunque sean invocadas directamente por código.

`CONSULTA` es de solo lectura desde la aplicación.

## Desarrollo guiado por objetivos

Implementa únicamente el objetivo actual y la infraestructura mínima reutilizable que realmente necesite.

No construyas por adelantado objetivos posteriores salvo que sean estrictamente necesarios para completar correctamente el objetivo actual.

Un objetivo no está terminado únicamente porque existe código.

Antes de declarar un objetivo completado, verifica:

1. funciona el caso normal;
2. los casos inválidos requeridos fallan correctamente;
3. se respetan las reglas de negocio aprobadas;
4. no se corrompen datos existentes;
5. funciona desde la capa o interfaz correspondiente al objetivo;
6. el resultado puede demostrarse de forma reproducible.

Al finalizar un objetivo reporta:

- archivos creados o modificados;
- comportamiento implementado;
- pruebas ejecutadas;
- criterios de aceptación aprobados o fallidos;
- riesgos o bloqueos pendientes.

No avances automáticamente al siguiente objetivo.

El usuario decide cuándo iniciar el siguiente objetivo.

## Delegación entre agentes

Utiliza los agentes personalizados del proyecto únicamente cuando su especialidad sea necesaria para el objetivo actual.

### `firecommand-backend`

Utilizar para:

- Models;
- Controllers;
- Services;
- autenticación;
- autorización;
- CRUD backend;
- transacciones;
- máquinas de estado;
- recomendación de despacho;
- auditoría;
- lógica de negocio.

### `firecommand-frontend`

Utilizar únicamente cuando el objetivo actual incluya GUI o presentación.

Responsabilidades:

- CustomTkinter / Tkinter;
- Views;
- navegación;
- formularios;
- tablas;
- dashboard;
- reportes visuales;
- mensajes al usuario.

No utilizar este agente en objetivos puramente backend.

### `firecommand-db-reviewer`

Utilizar para revisar trabajo sensible a base de datos:

- SQL;
- compatibilidad con el esquema;
- transacciones;
- locking;
- concurrencia;
- integridad histórica;
- consultas complejas.

Es un revisor, no el dueño del esquema.

### `firecommand-qa-reviewer`

Utilizar al finalizar un objetivo funcional para comprobar:

- happy path;
- casos negativos;
- autorización;
- regresiones;
- reglas de negocio;
- manejo de errores;
- reproducibilidad.

No declarar un objetivo completado sin la revisión de QA cuando el objetivo tenga comportamiento funcional verificable.

El Claude Code principal coordina el trabajo, integra los resultados y mantiene el alcance.

## Disciplina de alcance

Prefiere cambios pequeños y revisables.

No refactorices módulos no relacionados durante un objetivo.

No inventes requisitos.

No implementes funcionalidades de objetivos futuros sin autorización.

Si los documentos aprobados del proyecto entran en conflicto con la implementación existente:

1. detente;
2. describe el conflicto;
3. no elijas silenciosamente una nueva regla;
4. solicita decisión al usuario.

Si una prueba revela que un dato del `seed.sql` no cumple una expectativa funcional —por ejemplo, una contraseña demo que no corresponde al hash almacenado— no modifiques el seed silenciosamente. Reporta el problema con evidencia y espera autorización antes de cambiar datos aprobados.
