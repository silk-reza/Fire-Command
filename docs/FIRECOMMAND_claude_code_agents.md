# FIRECOMMAND v1.0
## Configuración de Claude Code y agentes del proyecto

> **Objetivo:** Usar Claude Code como ejecutor principal del desarrollo, con subagentes especializados y un flujo por objetivos.

---

# 1. Decisión

Se usarán cuatro subagentes personalizados:

```text
firecommand-backend
firecommand-frontend
firecommand-db-reviewer
firecommand-qa-reviewer
```

El Claude Code principal será el coordinador.

No se crea un subagente de arquitectura por ahora.

Claude Code ya dispone de agentes integrados de exploración/planificación, y las reglas persistentes del proyecto se mantienen en `CLAUDE.md`.

Tampoco se activan Agent Teams en esta etapa.

---

# 2. Estructura

Copiar al repositorio:

```text
/
├── CLAUDE.md
└── .claude/
    └── agents/
        ├── firecommand-backend.md
        ├── firecommand-frontend.md
        ├── firecommand-db-reviewer.md
        └── firecommand-qa-reviewer.md
```

El archivo:

```text
CLAUDE.md
```

contiene invariantes y reglas que aplican durante todo el proyecto.

Los archivos bajo:

```text
.claude/agents/
```

son especialistas reutilizables del proyecto.

---

# 3. Por qué solo cuatro

## Backend

Cubre:

```text
autenticación
roles
CRUD
models
controllers
transactions
state machines
dispatch recommendation
audit
reporting queries
```

## Frontend

Cubre:

```text
CustomTkinter
login GUI
navigation
CRUD screens
tables
dashboard
report screens
optional map UI
```

## Database reviewer

Cubre revisión especializada de:

```text
SQL
schema compatibility
transactions
FOR UPDATE
concurrency
referential integrity
historical consistency
```

Es revisor, no dueño del esquema.

## QA reviewer

Cubre:

```text
acceptance criteria
negative cases
authorization
regressions
error handling
objective closure
```

No se crea un agente por módulo porque produciría demasiada fragmentación.

Si una especialidad repetitiva aparece después —por ejemplo generación compleja de PDF— se evalúa crearla en ese momento.

---

# 4. Flujo de trabajo

Para cada objetivo:

```text
tú defines / confirmas objetivo
        ↓
ChatGPT prepara el prompt de ejecución
        ↓
Claude Code principal coordina
        ↓
subagente especialista implementa/revisa
        ↓
QA revisa criterio de cierre
        ↓
Claude principal reporta
        ↓
solo entonces marcamos ✅
```

El agente frontend no debe intervenir si el objetivo es puramente backend.

El agente backend no debe meterse a rediseñar pantallas.

El reviewer de DB se usa cuando la tarea toca consultas, transacciones o concurrencia.

QA se usa al final de cada objetivo funcional.

---

# 5. Regla de control de objetivos

No avanzar automáticamente al siguiente objetivo.

Claude Code debe terminar y reportar el objetivo actual.

El cambio de objetivo lo decide el usuario.

---

# 6. Primer objetivo

El desarrollo comienza con:

```text
OBJ 01 — Autenticación funcional
```

El prompt listo para pegar en Claude Code se encuentra en:

```text
prompts/OBJ01_autenticacion.md
```

Ese prompt prohíbe construir todavía la GUI del login y exige probar:

```text
password correcto
password incorrecto
usuario inactivo
usuario inexistente
```

---

# 7. Instalación

Desde la raíz del repositorio, colocar:

```text
CLAUDE.md
.claude/agents/*.md
```

Si `.claude/agents/` no existía al iniciar la sesión actual de Claude Code, reiniciar la sesión después de crear el directorio para asegurar que los agentes sean detectados.

Después se puede pedir explícitamente:

```text
Use firecommand-backend to inspect the authentication implementation.
```

o simplemente utilizar el prompt del objetivo y dejar que Claude Code delegue según las descripciones.

---

# 8. Uso futuro con ChatGPT

A partir de aquí el flujo será:

```text
usuario:
"vamos con OBJ X"

ChatGPT:
- revisa objetivo y estado anterior;
- define qué agentes deben intervenir;
- produce el prompt exacto para Claude Code;
- evita meter requisitos de objetivos posteriores;
- ayuda a revisar el reporte de Claude Code;
- actualiza el control del proyecto.
```

ChatGPT actuará como planificación/control y Claude Code como ejecutor del código.
