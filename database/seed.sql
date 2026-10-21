-- ============================================================
-- FIRECOMMAND v1.0
-- seed.sql
-- MySQL 8.0.16+
--
-- Dataset DEMO intencional para:
--   - login y roles
--   - estaciones, personal y unidades
--   - todos los estados internos
--   - compatibilidad incidente/unidad
--   - incidentes en diferentes estados
--   - despachos CREADO / ACTIVO / COMPLETADO / CANCELADO
--   - participaciones y tripulaciones
--   - reportería y auditoría básica
--
-- IMPORTANTE:
--   Ejecutar sobre una base limpia creada con:
--   FIRECOMMAND_schema_FINAL.sql o en su defecto la versión renombrada a schema.sql
--   FIRECOMMAND_schema_FINAL.sql y schema.sql son el mismo archivo con diferente nombre.
--
--   Este script NO usa INSERT IGNORE ni ON DUPLICATE KEY UPDATE.
--   Si se ejecuta dos veces sobre los mismos datos, debe fallar.
--
-- Cuentas DEMO:
--   admin.demo
--   operador.central
--   operador.norte
--   consulta.sur
--   operador.inactivo
--
-- Contraseña DEMO compartida:
--   FireDemo2026!
--
-- Todas las cuentas almacenan únicamente el hash bcrypt.
-- ============================================================

USE firecommand;

START TRANSACTION;


-- ============================================================
-- 1. ROLES INTERNOS
-- ============================================================

INSERT INTO roles (
    id_rol,
    codigo,
    nombre,
    descripcion,
    activo
) VALUES
    (1, 'ADMINISTRADOR', 'Administrador',
        'Acceso completo a configuración, operación y administración.', 1),
    (2, 'OPERADOR', 'Operador',
        'Gestión operativa de incidentes, despachos y recursos.', 1),
    (3, 'CONSULTA', 'Consulta',
        'Acceso de solo lectura a consultas y reportería.', 1);


-- ============================================================
-- 2. ESTACIONES
-- ============================================================

INSERT INTO estaciones (
    id_estacion,
    codigo,
    nombre,
    direccion,
    referencia,
    latitud,
    longitud,
    telefono,
    activo
) VALUES
    (1, 'EST-CENTRAL', 'Estación Central',
        '7a Avenida 12-45, Zona 1, Ciudad de Guatemala',
        'Frente al parque central del sector',
        14.640720, -90.513270, '2201-0101', 1),

    (2, 'EST-NORTE', 'Estación Norte',
        'Calzada La Paz 18-20, Zona 5, Ciudad de Guatemala',
        'Ingreso por carril auxiliar',
        14.655600, -90.486900, '2201-0202', 1),

    (3, 'EST-SUR', 'Estación Sur',
        'Avenida Petapa 42-10, Zona 12, Ciudad de Guatemala',
        'A un costado del complejo deportivo',
        14.584900, -90.555100, '2201-0303', 1);


-- ============================================================
-- 3. PERSONAL OPERATIVO
-- ============================================================

INSERT INTO personal (
    id_personal,
    id_estacion,
    codigo,
    nombres,
    apellidos,
    cargo,
    telefono,
    activo
) VALUES
    (1,  1, 'PER-001', 'Carlos',  'Méndez',     'Bombero conductor',  '5000-0101', 1),
    (2,  1, 'PER-002', 'Ana',     'López',      'Paramédica',         '5000-0102', 1),
    (3,  1, 'PER-003', 'Luis',    'Herrera',    'Bombero',            '5000-0103', 1),
    (4,  1, 'PER-004', 'Sofía',   'Ramírez',    'Técnica en rescate', '5000-0104', 1),
    (5,  1, 'PER-005', 'Mario',   'Castillo',   'Oficial de turno',   '5000-0105', 1),
    (6,  1, 'PER-006', 'Elena',   'García',     'Bombera',            '5000-0106', 1),

    (7,  2, 'PER-007', 'Diego',   'Morales',    'Bombero conductor',  '5000-0201', 1),
    (8,  2, 'PER-008', 'Valeria', 'Ruiz',       'Técnica en rescate', '5000-0202', 1),
    (9,  2, 'PER-009', 'Jorge',   'Pérez',      'Paramédico',         '5000-0203', 1),
    (10, 2, 'PER-010', 'Andrea',  'Soto',       'Oficial de turno',   '5000-0204', 1),
    (11, 2, 'PER-011', 'Ricardo', 'Vega',       'Bombero',            '5000-0205', 0),

    (12, 3, 'PER-012', 'Pablo',   'López',      'Bombero conductor',  '5000-0301', 1),
    (13, 3, 'PER-013', 'Camila',  'Hernández',  'Paramédica',         '5000-0302', 1),
    (14, 3, 'PER-014', 'Andrés',  'Díaz',       'Bombero',            '5000-0303', 1),
    (15, 3, 'PER-015', 'Laura',   'Gómez',      'Oficial de turno',   '5000-0304', 1);


-- ============================================================
-- 4. USUARIOS
-- ============================================================
-- Todos usan la contraseña demo indicada en el encabezado.
-- operador.inactivo permite probar el rechazo de login por activo = 0.

INSERT INTO usuarios (
    id_usuario,
    id_rol,
    id_personal,
    nombres,
    apellidos,
    nombre_usuario,
    password_hash,
    email,
    activo
) VALUES
    (1, 1, NULL, 'Administrador', 'Demo',
        'admin.demo',
        '$2b$12$.hiksr6Ub268/YidhRYL1eKvmAeehU9OESZev88U.YED0O2pxrJ6W',
        'admin.demo@firecommand.local', 1),

    (2, 2, 5, 'Mario', 'Castillo',
        'operador.central',
        '$2b$12$.hiksr6Ub268/YidhRYL1eKvmAeehU9OESZev88U.YED0O2pxrJ6W',
        'operador.central@firecommand.local', 1),

    (3, 2, 10, 'Andrea', 'Soto',
        'operador.norte',
        '$2b$12$.hiksr6Ub268/YidhRYL1eKvmAeehU9OESZev88U.YED0O2pxrJ6W',
        'operador.norte@firecommand.local', 1),

    (4, 3, 15, 'Laura', 'Gómez',
        'consulta.sur',
        '$2b$12$.hiksr6Ub268/YidhRYL1eKvmAeehU9OESZev88U.YED0O2pxrJ6W',
        'consulta.sur@firecommand.local', 1),

    (5, 2, 11, 'Ricardo', 'Vega',
        'operador.inactivo',
        '$2b$12$.hiksr6Ub268/YidhRYL1eKvmAeehU9OESZev88U.YED0O2pxrJ6W',
        'operador.inactivo@firecommand.local', 0);


-- ============================================================
-- 5. TIPOS DE UNIDAD
-- ============================================================

INSERT INTO tipos_unidad (
    id_tipo_unidad,
    nombre,
    descripcion,
    activo
) VALUES
    (1, 'AUTOBOMBA', 'Unidad principal para combate de incendios.', 1),
    (2, 'AMBULANCIA', 'Atención prehospitalaria y traslado de pacientes.', 1),
    (3, 'RESCATE', 'Equipo y personal para rescate técnico.', 1),
    (4, 'CISTERNA', 'Transporte y suministro adicional de agua.', 1),
    (5, 'HAZMAT', 'Respuesta a materiales peligrosos.', 1),
    (6, 'UNIDAD_DE_MANDO', 'Coordinación y comando de incidentes.', 1);


-- ============================================================
-- 6. ESTADOS DE UNIDAD
-- ============================================================

INSERT INTO estados_unidad (
    id_estado_unidad,
    codigo,
    nombre,
    descripcion,
    orden
) VALUES
    (1, 'DISPONIBLE', 'Disponible',
        'Unidad apta y disponible para una nueva participación.', 1),
    (2, 'DESPACHADA', 'Despachada',
        'Unidad comprometida en un despacho activo, aún sin salida registrada.', 2),
    (3, 'EN_RUTA', 'En ruta',
        'Unidad desplazándose hacia el incidente.', 3),
    (4, 'EN_ESCENA', 'En escena',
        'Unidad presente y participando en la atención.', 4),
    (5, 'REGRESANDO', 'Regresando',
        'Participación finalizada; unidad aún en retorno operativo.', 5),
    (6, 'MANTENIMIENTO', 'Mantenimiento',
        'Unidad temporalmente no disponible por mantenimiento.', 6),
    (7, 'FUERA_DE_SERVICIO', 'Fuera de servicio',
        'Unidad no apta para uso operativo.', 7);


-- ============================================================
-- 7. UNIDADES
-- ============================================================
-- El conjunto representa todos los estados de unidad.
-- U6:  activo = 0 + FUERA_DE_SERVICIO (retiro administrativo)
-- U11: activo = 1 + FUERA_DE_SERVICIO (indisponibilidad operativa)

INSERT INTO unidades (
    id_unidad,
    id_estacion,
    id_tipo_unidad,
    id_estado_unidad,
    codigo,
    nombre,
    placa,
    capacidad_personal,
    descripcion,
    activo
) VALUES
    (1,  1, 1, 1, 'B-01',  'Autobomba Central 1',  'BOM-001', 6,
        'Autobomba de primera respuesta.', 1),
    (2,  1, 2, 2, 'A-01',  'Ambulancia Central 1', 'AMB-001', 4,
        'Ambulancia comprometida y pendiente de salida.', 1),
    (3,  1, 3, 4, 'R-01',  'Rescate Central 1',    'RES-001', 6,
        'Unidad de rescate actualmente en escena.', 1),
    (4,  2, 4, 5, 'C-01',  'Cisterna Norte 1',     'CIS-001', 3,
        'Cisterna liberada y actualmente regresando.', 1),
    (5,  2, 5, 6, 'HZ-01', 'HazMat Norte 1',       'HAZ-001', 5,
        'Unidad HazMat en mantenimiento programado.', 1),
    (6,  2, 6, 7, 'CM-01', 'Mando Norte 1',        'CMD-001', 4,
        'Unidad de mando retirada administrativamente.', 0),
    (7,  3, 1, 1, 'B-02',  'Autobomba Sur 1',      'BOM-002', 6,
        'Autobomba disponible.', 1),
    (8,  3, 2, 1, 'A-02',  'Ambulancia Sur 1',     'AMB-002', 4,
        'Ambulancia disponible y preseleccionada en un borrador.', 1),
    (9,  2, 3, 3, 'R-02',  'Rescate Norte 1',      'RES-002', 6,
        'Unidad de rescate actualmente en ruta.', 1),
    (10, 1, 1, 4, 'B-03',  'Autobomba Central 2',  'BOM-003', 6,
        'Autobomba actualmente en escena.', 1),
    (11, 3, 6, 7, 'CM-02', 'Mando Sur 1',          'CMD-002', 4,
        'Unidad vigente pero fuera de servicio por inspección pendiente.', 1);


-- ============================================================
-- 8. TIPOS DE INCIDENTE
-- ============================================================

INSERT INTO tipos_incidente (
    id_tipo_incidente,
    nombre,
    descripcion,
    activo
) VALUES
    (1, 'INCENDIO', 'Incendios estructurales, vehiculares o de vegetación.', 1),
    (2, 'ACCIDENTE_VEHICULAR', 'Colisiones, vuelcos y otros accidentes viales.', 1),
    (3, 'EMERGENCIA_MEDICA', 'Atención prehospitalaria por lesión o enfermedad.', 1),
    (4, 'RESCATE', 'Rescates técnicos y extracción de personas.', 1),
    (5, 'MATERIALES_PELIGROSOS', 'Incidentes con sustancias peligrosas.', 1),
    (6, 'OTRO', 'Incidente que no corresponde a los tipos anteriores.', 1);


-- ============================================================
-- 9. PRIORIDADES
-- ============================================================

INSERT INTO prioridades (
    id_prioridad,
    nombre,
    nivel,
    descripcion,
    activo
) VALUES
    (1, 'BAJA',    1, 'Situación estable con bajo riesgo inmediato.', 1),
    (2, 'MEDIA',   2, 'Requiere atención pronta sin riesgo crítico inmediato.', 1),
    (3, 'ALTA',    3, 'Riesgo importante para personas o bienes.', 1),
    (4, 'CRITICA', 4, 'Riesgo inmediato elevado; máxima prioridad operativa.', 1);


-- ============================================================
-- 10. ESTADOS DE INCIDENTE
-- ============================================================

INSERT INTO estados_incidente (
    id_estado_incidente,
    codigo,
    nombre,
    descripcion,
    orden
) VALUES
    (1, 'REPORTADO', 'Reportado',
        'Incidente registrado sin recursos oficialmente comprometidos.', 1),
    (2, 'ASIGNADO', 'Asignado',
        'Existe al menos un despacho activo con recursos comprometidos.', 2),
    (3, 'EN_ATENCION', 'En atención',
        'Al menos una unidad llegó formalmente a escena.', 3),
    (4, 'CONTROLADO', 'Controlado',
        'La emergencia fue declarada bajo control; el incidente sigue abierto.', 4),
    (5, 'CERRADO', 'Cerrado',
        'Incidente finalizado de forma normal.', 5),
    (6, 'CANCELADO', 'Cancelado',
        'Incidente terminado mediante cancelación.', 6);


-- ============================================================
-- 11. ESTADOS DE DESPACHO
-- ============================================================

INSERT INTO estados_despacho (
    id_estado_despacho,
    codigo,
    nombre,
    descripcion,
    orden
) VALUES
    (1, 'CREADO', 'Creado',
        'Borrador de despacho; todavía no compromete recursos.', 1),
    (2, 'ACTIVO', 'Activo',
        'Despacho confirmado con recursos comprometidos.', 2),
    (3, 'COMPLETADO', 'Completado',
        'Despacho cuyo ciclo operativo terminó normalmente.', 3),
    (4, 'CANCELADO', 'Cancelado',
        'Despacho abortado explícitamente.', 4);


-- ============================================================
-- 12. COMPATIBILIDAD INCIDENTE / TIPO DE UNIDAD
-- ============================================================
-- prioridad_recomendacion: menor número = mayor preferencia.
-- La última fila está inactiva a propósito.

INSERT INTO compatibilidad_incidente_unidad (
    id_tipo_incidente,
    id_tipo_unidad,
    prioridad_recomendacion,
    observacion,
    activo
) VALUES
    (1, 1, 1, 'Autobomba como recurso principal.', 1),
    (1, 4, 2, 'Cisterna como apoyo de suministro de agua.', 1),
    (1, 2, 3, 'Ambulancia para atención de posibles lesionados.', 1),
    (1, 6, 4, 'Mando para coordinación en incidentes de mayor escala.', 1),

    (2, 3, 1, 'Rescate para extracción y estabilización.', 1),
    (2, 2, 2, 'Ambulancia para atención prehospitalaria.', 1),
    (2, 1, 3, 'Autobomba como apoyo de seguridad y control de riesgos.', 1),
    (2, 6, 4, 'Mando para eventos complejos.', 1),

    (3, 2, 1, 'Ambulancia como recurso principal.', 1),
    (3, 3, 2, 'Rescate como apoyo cuando existe acceso complicado.', 1),

    (4, 3, 1, 'Rescate como recurso principal.', 1),
    (4, 2, 2, 'Ambulancia para atención de víctimas.', 1),
    (4, 1, 3, 'Autobomba como apoyo operativo.', 1),

    (5, 5, 1, 'HazMat como recurso especializado principal.', 1),
    (5, 1, 2, 'Autobomba como soporte y protección.', 1),
    (5, 2, 3, 'Ambulancia para atención de exposición o lesiones.', 1),
    (5, 6, 4, 'Mando para coordinación del perímetro y recursos.', 1),
    (5, 4, 5, 'Cisterna como apoyo de abastecimiento y descontaminación.', 1),

    (6, 6, 1, 'Mando para valoración y coordinación inicial.', 1),
    (6, 3, 2, 'Rescate como recurso general de apoyo.', 1),
    (6, 1, 5, 'Compatibilidad histórica desactivada.', 0);


-- ============================================================
-- 13. INCIDENTES DEMO
-- ============================================================
-- #1 REPORTADO
-- #2 ASIGNADO
-- #3 EN_ATENCION
-- #4 CONTROLADO
-- #5 CERRADO
-- #6 CANCELADO después de asignación
-- #7 CERRADO histórico
-- #8 CANCELADO directamente desde REPORTADO

INSERT INTO incidentes (
    id_incidente,
    codigo,
    id_tipo_incidente,
    id_prioridad,
    id_estado_incidente,
    id_usuario_registro,
    fecha_reporte,
    direccion,
    referencia,
    latitud,
    longitud,
    descripcion,
    personas_afectadas,
    fecha_asignacion,
    fecha_inicio_atencion,
    fecha_control,
    fecha_cierre,
    fecha_cancelacion,
    motivo_cancelacion,
    creado_en,
    actualizado_en
) VALUES
    (
        1, 'INC-2026-001', 1, 2, 1, 2,
        '2026-10-17 15:15:00',
        '12 Calle 18-40, Zona 12, Ciudad de Guatemala',
        'Bodega con portón azul',
        14.604200, -90.545300,
        'Reporte de humo visible desde el techo de una bodega.',
        0,
        NULL, NULL, NULL, NULL, NULL, NULL,
        '2026-10-17 15:15:00', '2026-10-17 15:18:00'
    ),
    (
        2, 'INC-2026-002', 2, 4, 2, 2,
        '2026-10-17 15:00:00',
        'Boulevard Los Próceres, Zona 10, Ciudad de Guatemala',
        'Intersección cercana a paso a desnivel',
        14.594900, -90.509600,
        'Colisión múltiple con posible atrapamiento.',
        3,
        '2026-10-17 15:05:00',
        NULL, NULL, NULL, NULL, NULL,
        '2026-10-17 15:00:00', '2026-10-17 15:10:00'
    ),
    (
        3, 'INC-2026-003', 3, 3, 3, 2,
        '2026-10-17 14:20:00',
        '6a Avenida 5-30, Zona 1, Ciudad de Guatemala',
        'Edificio comercial, segundo nivel',
        14.638000, -90.512000,
        'Paciente con dificultad respiratoria y acceso complicado.',
        1,
        '2026-10-17 14:25:00',
        '2026-10-17 14:37:00',
        NULL, NULL, NULL, NULL,
        '2026-10-17 14:20:00', '2026-10-17 14:37:00'
    ),
    (
        4, 'INC-2026-004', 5, 4, 4, 3,
        '2026-10-17 13:10:00',
        'Calzada La Paz 24-15, Zona 5, Ciudad de Guatemala',
        'Área de descarga industrial',
        14.660100, -90.482500,
        'Derrame químico contenido dentro de instalación industrial.',
        0,
        '2026-10-17 13:14:00',
        '2026-10-17 13:25:00',
        '2026-10-17 14:05:00',
        NULL, NULL, NULL,
        '2026-10-17 13:10:00', '2026-10-17 14:20:00'
    ),
    (
        5, 'INC-2026-005', 1, 3, 5, 2,
        '2026-10-10 14:00:00',
        '3a Calle 9-80, Zona 8, Ciudad de Guatemala',
        'Taller mecánico',
        14.615500, -90.528200,
        'Incendio en taller con propagación limitada a un área de almacenamiento.',
        2,
        '2026-10-10 14:05:00',
        '2026-10-10 14:12:00',
        '2026-10-10 15:10:00',
        '2026-10-10 16:20:00',
        NULL, NULL,
        '2026-10-10 14:00:00', '2026-10-10 16:20:00'
    ),
    (
        6, 'INC-2026-006', 4, 2, 6, 3,
        '2026-10-12 19:30:00',
        'Ruta al Atlántico, Km 8, Zona 18, Ciudad de Guatemala',
        'Ingreso a colonia residencial',
        14.682000, -90.474800,
        'Reporte de persona atrapada posteriormente identificado como duplicado.',
        0,
        '2026-10-12 19:35:00',
        NULL, NULL, NULL,
        '2026-10-12 19:42:00',
        'Reporte duplicado de un evento atendido por otra jurisdicción.',
        '2026-10-12 19:30:00', '2026-10-12 19:42:00'
    ),
    (
        7, 'INC-2026-007', 3, 1, 5, 3,
        '2026-09-30 06:50:00',
        '4a Avenida 16-25, Zona 6, Ciudad de Guatemala',
        'Residencial, casa esquina',
        14.664100, -90.500900,
        'Evaluación médica por caída de adulto mayor.',
        1,
        '2026-09-30 06:54:00',
        '2026-09-30 07:02:00',
        '2026-09-30 07:35:00',
        '2026-09-30 08:00:00',
        NULL, NULL,
        '2026-09-30 06:50:00', '2026-09-30 08:00:00'
    ),
    (
        8, 'INC-2026-008', 6, 1, 6, 2,
        '2026-10-01 22:15:00',
        'Zona 11, Ciudad de Guatemala',
        'Sin referencia adicional',
        NULL, NULL,
        'Llamada recibida durante prueba de comunicación; no existía emergencia real.',
        0,
        NULL, NULL, NULL, NULL,
        '2026-10-01 22:20:00',
        'Llamada de prueba confirmada por el operador.',
        '2026-10-01 22:15:00', '2026-10-01 22:20:00'
    );


-- ============================================================
-- 14. DESPACHOS DEMO
-- ============================================================

INSERT INTO despachos (
    id_despacho,
    id_incidente,
    id_estado_despacho,
    id_usuario,
    fecha_despacho,
    fecha_finalizacion,
    fecha_cancelacion,
    motivo_cancelacion,
    observaciones,
    creado_en,
    actualizado_en
) VALUES
    (1, 1, 1, 2, NULL, NULL, NULL, NULL,
        'Borrador en preparación para el reporte de humo.',
        '2026-10-17 15:18:00', '2026-10-17 15:18:00'),

    (2, 2, 2, 2, '2026-10-17 15:05:00', NULL, NULL, NULL,
        'Despacho inicial para colisión múltiple.',
        '2026-10-17 15:04:00', '2026-10-17 15:10:00'),

    (3, 3, 2, 2, '2026-10-17 14:25:00', NULL, NULL, NULL,
        'Respuesta médica con apoyo de rescate.',
        '2026-10-17 14:24:00', '2026-10-17 14:37:00'),

    (4, 4, 2, 3, '2026-10-17 13:14:00', NULL, NULL, NULL,
        'Respuesta inicial a derrame químico.',
        '2026-10-17 13:13:00', '2026-10-17 14:20:00'),

    (5, 5, 3, 2, '2026-10-10 14:05:00', '2026-10-10 15:55:00', NULL, NULL,
        'Primer despacho del incendio de taller.',
        '2026-10-10 14:04:00', '2026-10-10 15:55:00'),

    (6, 5, 3, 2, '2026-10-10 14:30:00', '2026-10-10 15:40:00', NULL, NULL,
        'Refuerzo médico solicitado durante la atención.',
        '2026-10-10 14:29:00', '2026-10-10 15:40:00'),

    (7, 6, 4, 3, '2026-10-12 19:35:00', NULL, '2026-10-12 19:42:00',
        'El incidente fue identificado como reporte duplicado.',
        'Despacho cancelado antes de que la unidad iniciara desplazamiento.',
        '2026-10-12 19:34:00', '2026-10-12 19:42:00'),

    (8, 6, 4, 3, NULL, NULL, '2026-10-12 19:42:00',
        'El incidente fue cancelado antes de confirmar este borrador.',
        'Segundo borrador que nunca llegó a activarse.',
        '2026-10-12 19:38:00', '2026-10-12 19:42:00'),

    (9, 7, 3, 3, '2026-09-30 06:54:00', '2026-09-30 07:45:00', NULL, NULL,
        'Atención médica histórica completada.',
        '2026-09-30 06:53:00', '2026-09-30 07:45:00');


-- ============================================================
-- 15. PARTICIPACIÓN DE UNIDADES
-- ============================================================

INSERT INTO despacho_unidades (
    id_despacho_unidad,
    id_despacho,
    id_unidad,
    id_estacion_origen,
    fecha_asignacion,
    fecha_salida,
    fecha_llegada_escena,
    fecha_liberacion,
    fecha_cancelacion,
    motivo_cancelacion,
    observaciones
) VALUES
    (1, 1, 8, NULL,
        NULL, NULL, NULL, NULL, NULL, NULL,
        'Ambulancia preseleccionada en borrador.'),

    (2, 2, 2, 1,
        '2026-10-17 15:05:00',
        NULL, NULL, NULL, NULL, NULL,
        'Unidad confirmada, pendiente de salida.'),

    (3, 2, 9, 2,
        '2026-10-17 15:05:00',
        '2026-10-17 15:10:00',
        NULL, NULL, NULL, NULL,
        'Unidad de rescate en desplazamiento.'),

    (4, 3, 3, 1,
        '2026-10-17 14:25:00',
        '2026-10-17 14:28:00',
        '2026-10-17 14:37:00',
        NULL, NULL, NULL,
        'Primera unidad que llegó a escena.'),

    (5, 4, 4, 2,
        '2026-10-17 13:14:00',
        '2026-10-17 13:16:00',
        '2026-10-17 13:25:00',
        '2026-10-17 14:20:00',
        NULL, NULL,
        'Unidad de apoyo liberada después del control del derrame.'),

    (6, 4, 10, 1,
        '2026-10-17 13:14:00',
        '2026-10-17 13:17:00',
        '2026-10-17 13:28:00',
        NULL, NULL, NULL,
        'Unidad permanece en escena realizando aseguramiento.'),

    (7, 5, 1, 1,
        '2026-10-10 14:05:00',
        '2026-10-10 14:07:00',
        '2026-10-10 14:12:00',
        '2026-10-10 15:40:00',
        NULL, NULL,
        'Autobomba principal del incidente.'),

    (8, 5, 7, 3,
        '2026-10-10 14:05:00',
        '2026-10-10 14:09:00',
        '2026-10-10 14:18:00',
        '2026-10-10 15:55:00',
        NULL, NULL,
        'Autobomba de apoyo liberada al final del despacho.'),

    (9, 6, 8, 3,
        '2026-10-10 14:30:00',
        '2026-10-10 14:32:00',
        '2026-10-10 14:40:00',
        '2026-10-10 15:40:00',
        NULL, NULL,
        'Refuerzo médico solicitado durante la atención.'),

    (10, 7, 7, 3,
        '2026-10-12 19:35:00',
        NULL, NULL, NULL,
        '2026-10-12 19:42:00',
        'Reporte duplicado; la unidad no llegó a salir.',
        'Participación cancelada antes del desplazamiento.'),

    (11, 8, 1, NULL,
        NULL, NULL, NULL, NULL, NULL, NULL,
        'Preselección conservada históricamente; nunca fue asignada.'),

    (12, 9, 8, 3,
        '2026-09-30 06:54:00',
        '2026-09-30 06:56:00',
        '2026-09-30 07:02:00',
        '2026-09-30 07:45:00',
        NULL, NULL,
        'Participación histórica de ambulancia en emergencia médica.');


-- ============================================================
-- 16. TRIPULACIONES
-- ============================================================

INSERT INTO despacho_personal (
    id_despacho_personal,
    id_despacho_unidad,
    id_personal,
    observaciones
) VALUES
    (1,  1, 12, 'Conductor preseleccionado para el borrador.'),
    (2,  1, 13, 'Paramédica preseleccionada para el borrador.'),

    (3,  2, 1,  'Conductor.'),
    (4,  2, 2,  'Paramédica.'),

    (5,  3, 7,  'Conductor.'),
    (6,  3, 8,  'Rescate técnico.'),

    (7,  4, 3,  'Bombero.'),
    (8,  4, 4,  'Rescate técnico.'),

    (9,  5, 9,  'Atención médica preventiva.'),
    (10, 5, 10, 'Coordinación de apoyo.'),

    (11, 6, 5,  'Oficial a cargo.'),
    (12, 6, 6,  'Bombera.'),

    (13, 7, 1,  'Conductor.'),
    (14, 7, 3,  'Bombero.'),

    (15, 8, 12, 'Conductor.'),
    (16, 8, 14, 'Bombero.'),

    (17, 9, 13, 'Paramédica.'),
    (18, 9, 15, 'Apoyo de coordinación.'),

    (19, 10, 7, 'Conductor asignado antes de la cancelación.'),
    (20, 10, 8, 'Rescate técnico asignado antes de la cancelación.'),

    (21, 12, 13, 'Paramédica.'),
    (22, 12, 15, 'Oficial de turno.');


-- ============================================================
-- 17. BITÁCORA DEMO
-- ============================================================

INSERT INTO bitacora (
    id_usuario,
    accion,
    entidad,
    id_entidad,
    detalle,
    fecha_hora
) VALUES
    (2, 'CREAR', 'INCIDENTE', 5,
        'Registro inicial de INC-2026-005.', '2026-10-10 14:00:00'),

    (2, 'CAMBIO_ESTADO', 'INCIDENTE', 5,
        'REPORTADO → ASIGNADO por activación del despacho #5.',
        '2026-10-10 14:05:00'),

    (2, 'CAMBIO_ESTADO', 'INCIDENTE', 5,
        'ASIGNADO → EN_ATENCION por primera llegada a escena.',
        '2026-10-10 14:12:00'),

    (2, 'CAMBIO_ESTADO', 'INCIDENTE', 5,
        'EN_ATENCION → CONTROLADO.',
        '2026-10-10 15:10:00'),

    (2, 'CAMBIO_ESTADO', 'INCIDENTE', 5,
        'CONTROLADO → CERRADO.',
        '2026-10-10 16:20:00'),

    (3, 'CAMBIO_ESTADO', 'INCIDENTE', 6,
        'ASIGNADO → CANCELADO. Motivo: reporte duplicado.',
        '2026-10-12 19:42:00'),

    (3, 'CAMBIO_ESTADO', 'DESPACHO', 7,
        'ACTIVO → CANCELADO. Recursos activos resueltos.',
        '2026-10-12 19:42:00'),

    (2, 'CREAR', 'DESPACHO', 1,
        'Borrador creado para INC-2026-001.',
        '2026-10-17 15:18:00'),

    (2, 'CAMBIO_ESTADO', 'DESPACHO', 2,
        'CREADO → ACTIVO para INC-2026-002.',
        '2026-10-17 15:05:00'),

    (2, 'CAMBIO_ESTADO', 'UNIDAD', 2,
        'DISPONIBLE → DESPACHADA por despacho #2.',
        '2026-10-17 15:05:00'),

    (2, 'CAMBIO_ESTADO', 'UNIDAD', 9,
        'DESPACHADA → EN_RUTA; salida registrada.',
        '2026-10-17 15:10:00'),

    (2, 'CAMBIO_ESTADO', 'UNIDAD', 3,
        'EN_RUTA → EN_ESCENA; primera llegada del incidente #3.',
        '2026-10-17 14:37:00'),

    (2, 'CAMBIO_ESTADO', 'INCIDENTE', 3,
        'ASIGNADO → EN_ATENCION por llegada de R-01.',
        '2026-10-17 14:37:00'),

    (3, 'CAMBIO_ESTADO', 'INCIDENTE', 4,
        'EN_ATENCION → CONTROLADO.',
        '2026-10-17 14:05:00'),

    (3, 'CAMBIO_ESTADO', 'UNIDAD', 4,
        'EN_ESCENA → REGRESANDO por liberación normal.',
        '2026-10-17 14:20:00'),

    (1, 'CAMBIO_ESTADO', 'UNIDAD', 5,
        'DISPONIBLE → MANTENIMIENTO por mantenimiento programado.',
        '2026-10-16 09:00:00'),

    (1, 'CAMBIO_ESTADO', 'UNIDAD', 11,
        'DISPONIBLE → FUERA_DE_SERVICIO por inspección pendiente.',
        '2026-10-16 10:15:00'),

    (1, 'DESACTIVAR', 'UNIDAD', 6,
        'Unidad retirada administrativamente: activo = 0, FUERA_DE_SERVICIO.',
        '2026-10-15 17:00:00');


COMMIT;


-- ============================================================
-- RESUMEN ESPERADO DEL DATASET
-- ============================================================
--
-- roles:                           3
-- estaciones:                     3
-- personal:                      15
-- usuarios:                       5
-- tipos_unidad:                   6
-- estados_unidad:                 7
-- unidades:                      11
-- tipos_incidente:                6
-- prioridades:                    4
-- estados_incidente:              6
-- estados_despacho:               4
-- compatibilidades:              21
-- incidentes:                     8
-- despachos:                      9
-- despacho_unidades:             12
-- despacho_personal:             22
-- bitacora:                      18
--
-- ============================================================
-- FIN DE seed.sql
-- ============================================================
