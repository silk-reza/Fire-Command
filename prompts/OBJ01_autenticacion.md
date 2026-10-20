# Prompt para Claude Code — OBJ 01: Autenticación funcional

Implementa **únicamente el OBJ 01 — Autenticación funcional** de FIRECOMMAND v1.0.

Antes de modificar código:
1. Lee `CLAUDE.md`.
2. Inspecciona la estructura actual del proyecto.
3. Usa el agente `firecommand-backend` para diseñar e implementar la solución mínima compatible con lo que ya existe.
4. No construyas todavía la GUI del login; eso pertenece al OBJ 02.
5. No modifiques `schema.sql` ni `seed.sql`.

## Comportamiento requerido

Con el dataset demo:

```text
admin.demo
FireDemo2026!
```

debe autenticar correctamente.

Debe rechazar:

```text
admin.demo + contraseña incorrecta
operador.inactivo + FireDemo2026!
usuario inexistente
```

Aunque la contraseña del usuario inactivo sea válida, no debe iniciar sesión.

## Resultado mínimo de una autenticación exitosa

Devuelve una estructura clara con al menos:

```text
id_usuario
nombre_usuario
nombres
apellidos
rol
activo
```

La consulta debe utilizar el código estable del rol y SQL parametrizado.

La contraseña debe verificarse con `bcrypt`; nunca compares texto plano contra la base.

## Diseño

Respeta:

```text
Controller → Model → MySQL
```

o la estructura MVC equivalente ya existente.

No metas SQL en una View.

No agregues capas o patrones que todavía no hagan falta.

Maneja correctamente:
- conexión/consulta;
- usuario inexistente;
- usuario inactivo;
- contraseña incorrecta;
- contraseña correcta;
- errores técnicos sin convertir credenciales inválidas en excepciones no controladas.

## Pruebas obligatorias

Demuestra de forma reproducible estos cuatro casos:

```text
1. activo + password correcto   → LOGIN EXITOSO
2. activo + password incorrecto → ACCESO DENEGADO
3. inactivo + password correcto → ACCESO DENEGADO
4. usuario inexistente          → ACCESO DENEGADO
```

Después de implementar:
1. pide a `firecommand-db-reviewer` revisar la consulta y compatibilidad con el esquema;
2. pide a `firecommand-qa-reviewer` revisar los cuatro criterios de cierre;
3. corrige únicamente lo que bloquea este objetivo;
4. vuelve a ejecutar las pruebas.

No avances al OBJ 02.

Al final entrega:
- archivos creados/modificados;
- breve explicación del flujo;
- salida/resultados de las cuatro pruebas;
- veredicto del QA;
- cualquier bloqueo real.
