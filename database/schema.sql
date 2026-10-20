-- ============================================================
-- FIRECOMMAND v1.0
-- schema.sql
-- MySQL 8.0.16+
--
-- Generado a partir del diseño final aprobado.
-- MySQL 8.0.16+ se requiere para garantizar enforcement de CHECK.
--
-- Este archivo crea y selecciona la base de datos FireCommand.
-- Los datos semilla se mantienen en seed.sql.
-- ============================================================

-- ============================================================

CREATE DATABASE IF NOT EXISTS firecommand
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_0900_ai_ci;

USE firecommand;


-- ============================================================
-- 1. roles
-- ============================================================

CREATE TABLE roles (
    id_rol INT UNSIGNED AUTO_INCREMENT,
    codigo VARCHAR(30) NOT NULL,
    nombre VARCHAR(30) NOT NULL,
    descripcion VARCHAR(150) NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,

    CONSTRAINT pk_roles
        PRIMARY KEY (id_rol),

    CONSTRAINT uq_roles_codigo
        UNIQUE (codigo),

    CONSTRAINT uq_roles_nombre
        UNIQUE (nombre),

    CONSTRAINT ck_roles_activo
        CHECK (activo IN (0, 1))
) ENGINE=InnoDB;


-- ============================================================
-- 2. estaciones
-- ============================================================

CREATE TABLE estaciones (
    id_estacion INT UNSIGNED AUTO_INCREMENT,
    codigo VARCHAR(20) NOT NULL,
    nombre VARCHAR(100) NOT NULL,
    direccion VARCHAR(200) NOT NULL,
    referencia VARCHAR(200) NULL,
    latitud DECIMAL(9,6) NULL,
    longitud DECIMAL(10,6) NULL,
    telefono VARCHAR(25) NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT pk_estaciones
        PRIMARY KEY (id_estacion),

    CONSTRAINT uq_estaciones_codigo
        UNIQUE (codigo),

    CONSTRAINT ck_estaciones_activo
        CHECK (activo IN (0, 1)),

    CONSTRAINT ck_estaciones_latitud
        CHECK (
            latitud IS NULL
            OR latitud BETWEEN -90 AND 90
        ),

    CONSTRAINT ck_estaciones_longitud
        CHECK (
            longitud IS NULL
            OR longitud BETWEEN -180 AND 180
        ),

    CONSTRAINT ck_estaciones_coordenadas_pareja
        CHECK (
            (latitud IS NULL AND longitud IS NULL)
            OR
            (latitud IS NOT NULL AND longitud IS NOT NULL)
        )
) ENGINE=InnoDB;


-- ============================================================
-- 3. personal
-- ============================================================

CREATE TABLE personal (
    id_personal INT UNSIGNED AUTO_INCREMENT,
    id_estacion INT UNSIGNED NOT NULL,
    codigo VARCHAR(20) NOT NULL,
    nombres VARCHAR(80) NOT NULL,
    apellidos VARCHAR(80) NOT NULL,
    cargo VARCHAR(60) NOT NULL,
    telefono VARCHAR(25) NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT pk_personal
        PRIMARY KEY (id_personal),

    CONSTRAINT uq_personal_codigo
        UNIQUE (codigo),

    CONSTRAINT ck_personal_activo
        CHECK (activo IN (0, 1)),

    CONSTRAINT fk_personal_estacion
        FOREIGN KEY (id_estacion)
        REFERENCES estaciones (id_estacion)
        ON DELETE RESTRICT
) ENGINE=InnoDB;


-- ============================================================
-- 4. usuarios
-- ============================================================

CREATE TABLE usuarios (
    id_usuario INT UNSIGNED AUTO_INCREMENT,
    id_rol INT UNSIGNED NOT NULL,
    id_personal INT UNSIGNED NULL,
    nombres VARCHAR(80) NOT NULL,
    apellidos VARCHAR(80) NOT NULL,
    nombre_usuario VARCHAR(50) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    email VARCHAR(120) NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    ultimo_acceso DATETIME NULL,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT pk_usuarios
        PRIMARY KEY (id_usuario),

    CONSTRAINT uq_usuarios_nombre_usuario
        UNIQUE (nombre_usuario),

    CONSTRAINT uq_usuarios_id_personal
        UNIQUE (id_personal),

    CONSTRAINT uq_usuarios_email
        UNIQUE (email),

    CONSTRAINT ck_usuarios_activo
        CHECK (activo IN (0, 1)),

    CONSTRAINT fk_usuarios_rol
        FOREIGN KEY (id_rol)
        REFERENCES roles (id_rol)
        ON DELETE RESTRICT,

    CONSTRAINT fk_usuarios_personal
        FOREIGN KEY (id_personal)
        REFERENCES personal (id_personal)
        ON DELETE SET NULL
) ENGINE=InnoDB;


-- ============================================================
-- 5. bitacora
-- ============================================================

CREATE TABLE bitacora (
    id_bitacora INT UNSIGNED AUTO_INCREMENT,
    id_usuario INT UNSIGNED NULL,
    accion VARCHAR(50) NOT NULL,
    entidad VARCHAR(50) NOT NULL,
    id_entidad INT UNSIGNED NULL,
    detalle TEXT NULL,
    fecha_hora DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT pk_bitacora
        PRIMARY KEY (id_bitacora),

    CONSTRAINT fk_bitacora_usuario
        FOREIGN KEY (id_usuario)
        REFERENCES usuarios (id_usuario)
        ON DELETE SET NULL,

    INDEX idx_bitacora_id_usuario (id_usuario),
    INDEX idx_bitacora_fecha_hora (fecha_hora),
    INDEX idx_bitacora_entidad_id_entidad (entidad, id_entidad)
) ENGINE=InnoDB;


-- ============================================================
-- 6. tipos_unidad
-- ============================================================

CREATE TABLE tipos_unidad (
    id_tipo_unidad INT UNSIGNED AUTO_INCREMENT,
    nombre VARCHAR(60) NOT NULL,
    descripcion VARCHAR(200) NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,

    CONSTRAINT pk_tipos_unidad
        PRIMARY KEY (id_tipo_unidad),

    CONSTRAINT uq_tipos_unidad_nombre
        UNIQUE (nombre),

    CONSTRAINT ck_tipos_unidad_activo
        CHECK (activo IN (0, 1))
) ENGINE=InnoDB;


-- ============================================================
-- 7. estados_unidad
-- ============================================================

CREATE TABLE estados_unidad (
    id_estado_unidad INT UNSIGNED AUTO_INCREMENT,
    codigo VARCHAR(40) NOT NULL,
    nombre VARCHAR(40) NOT NULL,
    descripcion VARCHAR(150) NULL,
    orden SMALLINT UNSIGNED NOT NULL,

    CONSTRAINT pk_estados_unidad
        PRIMARY KEY (id_estado_unidad),

    CONSTRAINT uq_estados_unidad_codigo
        UNIQUE (codigo),

    CONSTRAINT uq_estados_unidad_nombre
        UNIQUE (nombre),

    CONSTRAINT ck_estados_unidad_orden
        CHECK (orden > 0)
) ENGINE=InnoDB;


-- ============================================================
-- 8. unidades
-- ============================================================

CREATE TABLE unidades (
    id_unidad INT UNSIGNED AUTO_INCREMENT,
    id_estacion INT UNSIGNED NOT NULL,
    id_tipo_unidad INT UNSIGNED NOT NULL,
    id_estado_unidad INT UNSIGNED NOT NULL,
    codigo VARCHAR(20) NOT NULL,
    nombre VARCHAR(80) NULL,
    placa VARCHAR(20) NULL,
    capacidad_personal TINYINT UNSIGNED NULL,
    descripcion VARCHAR(200) NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT pk_unidades
        PRIMARY KEY (id_unidad),

    CONSTRAINT uq_unidades_codigo
        UNIQUE (codigo),

    CONSTRAINT uq_unidades_placa
        UNIQUE (placa),

    CONSTRAINT ck_unidades_activo
        CHECK (activo IN (0, 1)),

    CONSTRAINT ck_unidades_capacidad_personal
        CHECK (
            capacidad_personal IS NULL
            OR capacidad_personal > 0
        ),

    CONSTRAINT fk_unidades_estacion
        FOREIGN KEY (id_estacion)
        REFERENCES estaciones (id_estacion)
        ON DELETE RESTRICT,

    CONSTRAINT fk_unidades_tipo
        FOREIGN KEY (id_tipo_unidad)
        REFERENCES tipos_unidad (id_tipo_unidad)
        ON DELETE RESTRICT,

    CONSTRAINT fk_unidades_estado
        FOREIGN KEY (id_estado_unidad)
        REFERENCES estados_unidad (id_estado_unidad)
        ON DELETE RESTRICT,

    INDEX idx_unidades_id_estacion (id_estacion),
    INDEX idx_unidades_id_tipo_unidad (id_tipo_unidad),
    INDEX idx_unidades_estado_tipo (id_estado_unidad, id_tipo_unidad)
) ENGINE=InnoDB;


-- ============================================================
-- 9. tipos_incidente
-- ============================================================

CREATE TABLE tipos_incidente (
    id_tipo_incidente INT UNSIGNED AUTO_INCREMENT,
    nombre VARCHAR(60) NOT NULL,
    descripcion VARCHAR(200) NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,

    CONSTRAINT pk_tipos_incidente
        PRIMARY KEY (id_tipo_incidente),

    CONSTRAINT uq_tipos_incidente_nombre
        UNIQUE (nombre),

    CONSTRAINT ck_tipos_incidente_activo
        CHECK (activo IN (0, 1))
) ENGINE=InnoDB;


-- ============================================================
-- 10. prioridades
-- ============================================================

CREATE TABLE prioridades (
    id_prioridad INT UNSIGNED AUTO_INCREMENT,
    nombre VARCHAR(30) NOT NULL,
    nivel TINYINT UNSIGNED NOT NULL,
    descripcion VARCHAR(150) NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,

    CONSTRAINT pk_prioridades
        PRIMARY KEY (id_prioridad),

    CONSTRAINT uq_prioridades_nombre
        UNIQUE (nombre),

    CONSTRAINT uq_prioridades_nivel
        UNIQUE (nivel),

    CONSTRAINT ck_prioridades_activo
        CHECK (activo IN (0, 1)),

    CONSTRAINT ck_prioridades_nivel
        CHECK (nivel > 0)
) ENGINE=InnoDB;


-- ============================================================
-- 11. estados_incidente
-- ============================================================

CREATE TABLE estados_incidente (
    id_estado_incidente INT UNSIGNED AUTO_INCREMENT,
    codigo VARCHAR(40) NOT NULL,
    nombre VARCHAR(40) NOT NULL,
    descripcion VARCHAR(150) NULL,
    orden SMALLINT UNSIGNED NOT NULL,

    CONSTRAINT pk_estados_incidente
        PRIMARY KEY (id_estado_incidente),

    CONSTRAINT uq_estados_incidente_codigo
        UNIQUE (codigo),

    CONSTRAINT uq_estados_incidente_nombre
        UNIQUE (nombre),

    CONSTRAINT ck_estados_incidente_orden
        CHECK (orden > 0)
) ENGINE=InnoDB;


-- ============================================================
-- 12. incidentes
-- ============================================================

CREATE TABLE incidentes (
    id_incidente INT UNSIGNED AUTO_INCREMENT,
    codigo VARCHAR(25) NOT NULL,
    id_tipo_incidente INT UNSIGNED NOT NULL,
    id_prioridad INT UNSIGNED NOT NULL,
    id_estado_incidente INT UNSIGNED NOT NULL,
    id_usuario_registro INT UNSIGNED NOT NULL,
    fecha_reporte DATETIME NOT NULL,
    direccion VARCHAR(200) NOT NULL,
    referencia VARCHAR(200) NULL,
    latitud DECIMAL(9,6) NULL,
    longitud DECIMAL(10,6) NULL,
    descripcion TEXT NOT NULL,
    personas_afectadas SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    fecha_asignacion DATETIME NULL,
    fecha_inicio_atencion DATETIME NULL,
    fecha_control DATETIME NULL,
    fecha_cierre DATETIME NULL,
    fecha_cancelacion DATETIME NULL,
    motivo_cancelacion VARCHAR(250) NULL,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT pk_incidentes
        PRIMARY KEY (id_incidente),

    CONSTRAINT uq_incidentes_codigo
        UNIQUE (codigo),

    CONSTRAINT ck_incidentes_latitud
        CHECK (
            latitud IS NULL
            OR latitud BETWEEN -90 AND 90
        ),

    CONSTRAINT ck_incidentes_longitud
        CHECK (
            longitud IS NULL
            OR longitud BETWEEN -180 AND 180
        ),

    CONSTRAINT ck_incidentes_coordenadas_pareja
        CHECK (
            (latitud IS NULL AND longitud IS NULL)
            OR
            (latitud IS NOT NULL AND longitud IS NOT NULL)
        ),

    CONSTRAINT ck_incidentes_personas_afectadas
        CHECK (personas_afectadas >= 0),

    CONSTRAINT ck_incidentes_fecha_asignacion
        CHECK (
            fecha_asignacion IS NULL
            OR fecha_asignacion >= fecha_reporte
        ),

    CONSTRAINT ck_incidentes_fecha_inicio_reporte
        CHECK (
            fecha_inicio_atencion IS NULL
            OR fecha_inicio_atencion >= fecha_reporte
        ),

    CONSTRAINT ck_incidentes_inicio_requiere_asignacion
        CHECK (
            fecha_inicio_atencion IS NULL
            OR fecha_asignacion IS NOT NULL
        ),

    CONSTRAINT ck_incidentes_inicio_despues_asignacion
        CHECK (
            fecha_inicio_atencion IS NULL
            OR fecha_asignacion IS NULL
            OR fecha_inicio_atencion >= fecha_asignacion
        ),

    CONSTRAINT ck_incidentes_control_requiere_inicio
        CHECK (
            fecha_control IS NULL
            OR fecha_inicio_atencion IS NOT NULL
        ),

    CONSTRAINT ck_incidentes_fecha_control
        CHECK (
            fecha_control IS NULL
            OR fecha_control >= fecha_inicio_atencion
        ),

    CONSTRAINT ck_incidentes_cierre_requiere_control
        CHECK (
            fecha_cierre IS NULL
            OR fecha_control IS NOT NULL
        ),

    CONSTRAINT ck_incidentes_cierre_reporte
        CHECK (
            fecha_cierre IS NULL
            OR fecha_cierre >= fecha_reporte
        ),

    CONSTRAINT ck_incidentes_cierre_control
        CHECK (
            fecha_cierre IS NULL
            OR fecha_control IS NULL
            OR fecha_cierre >= fecha_control
        ),

    CONSTRAINT ck_incidentes_cancelacion_reporte
        CHECK (
            fecha_cancelacion IS NULL
            OR fecha_cancelacion >= fecha_reporte
        ),

    CONSTRAINT ck_incidentes_cancelacion_asignacion
        CHECK (
            fecha_cancelacion IS NULL
            OR fecha_asignacion IS NULL
            OR fecha_cancelacion >= fecha_asignacion
        ),

    CONSTRAINT ck_incidentes_cancelacion_inicio
        CHECK (
            fecha_cancelacion IS NULL
            OR fecha_inicio_atencion IS NULL
            OR fecha_cancelacion >= fecha_inicio_atencion
        ),

    CONSTRAINT ck_incidentes_cierre_cancelacion_excluyentes
        CHECK (
            fecha_cierre IS NULL
            OR fecha_cancelacion IS NULL
        ),

    CONSTRAINT ck_incidentes_control_cancelacion_excluyentes
        CHECK (
            fecha_cancelacion IS NULL
            OR fecha_control IS NULL
        ),

    CONSTRAINT ck_incidentes_cancelacion_motivo
        CHECK (
            fecha_cancelacion IS NULL
            OR motivo_cancelacion IS NOT NULL
        ),

    CONSTRAINT fk_incidentes_tipo
        FOREIGN KEY (id_tipo_incidente)
        REFERENCES tipos_incidente (id_tipo_incidente)
        ON DELETE RESTRICT,

    CONSTRAINT fk_incidentes_prioridad
        FOREIGN KEY (id_prioridad)
        REFERENCES prioridades (id_prioridad)
        ON DELETE RESTRICT,

    CONSTRAINT fk_incidentes_estado
        FOREIGN KEY (id_estado_incidente)
        REFERENCES estados_incidente (id_estado_incidente)
        ON DELETE RESTRICT,

    CONSTRAINT fk_incidentes_usuario_registro
        FOREIGN KEY (id_usuario_registro)
        REFERENCES usuarios (id_usuario)
        ON DELETE RESTRICT,

    INDEX idx_incidentes_fecha_reporte (fecha_reporte),
    INDEX idx_incidentes_id_tipo_incidente (id_tipo_incidente),
    INDEX idx_incidentes_id_prioridad (id_prioridad),
    INDEX idx_incidentes_estado_prioridad (
        id_estado_incidente,
        id_prioridad
    )
) ENGINE=InnoDB;


-- ============================================================
-- 13. estados_despacho
-- ============================================================

CREATE TABLE estados_despacho (
    id_estado_despacho INT UNSIGNED AUTO_INCREMENT,
    codigo VARCHAR(30) NOT NULL,
    nombre VARCHAR(30) NOT NULL,
    descripcion VARCHAR(150) NULL,
    orden SMALLINT UNSIGNED NOT NULL,

    CONSTRAINT pk_estados_despacho
        PRIMARY KEY (id_estado_despacho),

    CONSTRAINT uq_estados_despacho_codigo
        UNIQUE (codigo),

    CONSTRAINT uq_estados_despacho_nombre
        UNIQUE (nombre),

    CONSTRAINT ck_estados_despacho_orden
        CHECK (orden > 0)
) ENGINE=InnoDB;


-- ============================================================
-- 14. despachos
-- ============================================================

CREATE TABLE despachos (
    id_despacho INT UNSIGNED AUTO_INCREMENT,
    id_incidente INT UNSIGNED NOT NULL,
    id_estado_despacho INT UNSIGNED NOT NULL,
    id_usuario INT UNSIGNED NOT NULL,
    fecha_despacho DATETIME NULL,
    fecha_finalizacion DATETIME NULL,
    fecha_cancelacion DATETIME NULL,
    motivo_cancelacion VARCHAR(250) NULL,
    observaciones TEXT NULL,
    creado_en DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actualizado_en DATETIME NOT NULL
        DEFAULT CURRENT_TIMESTAMP
        ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT pk_despachos
        PRIMARY KEY (id_despacho),

    CONSTRAINT ck_despachos_finalizacion_requiere_despacho
        CHECK (
            fecha_finalizacion IS NULL
            OR fecha_despacho IS NOT NULL
        ),

    CONSTRAINT ck_despachos_fecha_finalizacion
        CHECK (
            fecha_finalizacion IS NULL
            OR fecha_finalizacion >= fecha_despacho
        ),

    CONSTRAINT ck_despachos_fecha_cancelacion
        CHECK (
            fecha_cancelacion IS NULL
            OR fecha_despacho IS NULL
            OR fecha_cancelacion >= fecha_despacho
        ),

    CONSTRAINT ck_despachos_final_cancelacion_excluyentes
        CHECK (
            fecha_finalizacion IS NULL
            OR fecha_cancelacion IS NULL
        ),

    CONSTRAINT ck_despachos_cancelacion_motivo
        CHECK (
            fecha_cancelacion IS NULL
            OR motivo_cancelacion IS NOT NULL
        ),

    CONSTRAINT fk_despachos_incidente
        FOREIGN KEY (id_incidente)
        REFERENCES incidentes (id_incidente)
        ON DELETE RESTRICT,

    CONSTRAINT fk_despachos_estado
        FOREIGN KEY (id_estado_despacho)
        REFERENCES estados_despacho (id_estado_despacho)
        ON DELETE RESTRICT,

    CONSTRAINT fk_despachos_usuario
        FOREIGN KEY (id_usuario)
        REFERENCES usuarios (id_usuario)
        ON DELETE RESTRICT,

    INDEX idx_despachos_id_incidente (id_incidente),
    INDEX idx_despachos_id_estado_despacho (id_estado_despacho),
    INDEX idx_despachos_fecha_despacho (fecha_despacho)
) ENGINE=InnoDB;


-- ============================================================
-- 15. despacho_unidades
-- ============================================================

CREATE TABLE despacho_unidades (
    id_despacho_unidad INT UNSIGNED AUTO_INCREMENT,
    id_despacho INT UNSIGNED NOT NULL,
    id_unidad INT UNSIGNED NOT NULL,
    id_estacion_origen INT UNSIGNED NULL,
    fecha_asignacion DATETIME NULL,
    fecha_salida DATETIME NULL,
    fecha_llegada_escena DATETIME NULL,
    fecha_liberacion DATETIME NULL,
    fecha_cancelacion DATETIME NULL,
    motivo_cancelacion VARCHAR(250) NULL,
    observaciones TEXT NULL,

    CONSTRAINT pk_despacho_unidades
        PRIMARY KEY (id_despacho_unidad),

    CONSTRAINT uq_despacho_unidades_despacho_unidad
        UNIQUE (id_despacho, id_unidad),

    CONSTRAINT ck_du_asignacion_estacion
        CHECK (
            fecha_asignacion IS NULL
            OR id_estacion_origen IS NOT NULL
        ),

    CONSTRAINT ck_du_preseleccion_sin_hitos
        CHECK (
            fecha_asignacion IS NOT NULL
            OR (
                fecha_salida IS NULL
                AND fecha_llegada_escena IS NULL
                AND fecha_liberacion IS NULL
                AND fecha_cancelacion IS NULL
            )
        ),

    CONSTRAINT ck_du_salida_requiere_asignacion
        CHECK (
            fecha_salida IS NULL
            OR fecha_asignacion IS NOT NULL
        ),

    CONSTRAINT ck_du_fecha_salida
        CHECK (
            fecha_salida IS NULL
            OR fecha_salida >= fecha_asignacion
        ),

    CONSTRAINT ck_du_llegada_requiere_salida
        CHECK (
            fecha_llegada_escena IS NULL
            OR fecha_salida IS NOT NULL
        ),

    CONSTRAINT ck_du_fecha_llegada
        CHECK (
            fecha_llegada_escena IS NULL
            OR fecha_llegada_escena >= fecha_salida
        ),

    CONSTRAINT ck_du_liberacion_requiere_llegada
        CHECK (
            fecha_liberacion IS NULL
            OR fecha_llegada_escena IS NOT NULL
        ),

    CONSTRAINT ck_du_fecha_liberacion
        CHECK (
            fecha_liberacion IS NULL
            OR fecha_liberacion >= fecha_llegada_escena
        ),

    CONSTRAINT ck_du_cancelacion_requiere_asignacion
        CHECK (
            fecha_cancelacion IS NULL
            OR fecha_asignacion IS NOT NULL
        ),

    CONSTRAINT ck_du_cancelacion_asignacion
        CHECK (
            fecha_cancelacion IS NULL
            OR fecha_cancelacion >= fecha_asignacion
        ),

    CONSTRAINT ck_du_cancelacion_salida
        CHECK (
            fecha_cancelacion IS NULL
            OR fecha_salida IS NULL
            OR fecha_cancelacion >= fecha_salida
        ),

    CONSTRAINT ck_du_cancelacion_llegada
        CHECK (
            fecha_cancelacion IS NULL
            OR fecha_llegada_escena IS NULL
            OR fecha_cancelacion >= fecha_llegada_escena
        ),

    CONSTRAINT ck_du_cancelacion_motivo
        CHECK (
            fecha_cancelacion IS NULL
            OR motivo_cancelacion IS NOT NULL
        ),

    CONSTRAINT ck_du_liberacion_cancelacion_excluyentes
        CHECK (
            fecha_liberacion IS NULL
            OR fecha_cancelacion IS NULL
        ),

    CONSTRAINT fk_du_despacho
        FOREIGN KEY (id_despacho)
        REFERENCES despachos (id_despacho)
        ON DELETE RESTRICT,

    CONSTRAINT fk_du_unidad
        FOREIGN KEY (id_unidad)
        REFERENCES unidades (id_unidad)
        ON DELETE RESTRICT,

    CONSTRAINT fk_du_estacion_origen
        FOREIGN KEY (id_estacion_origen)
        REFERENCES estaciones (id_estacion)
        ON DELETE RESTRICT,

    INDEX idx_du_estacion_origen (id_estacion_origen),
    INDEX idx_du_unidad_fecha_asignacion (
        id_unidad,
        fecha_asignacion
    )
) ENGINE=InnoDB;


-- ============================================================
-- 16. despacho_personal
-- ============================================================

CREATE TABLE despacho_personal (
    id_despacho_personal INT UNSIGNED AUTO_INCREMENT,
    id_despacho_unidad INT UNSIGNED NOT NULL,
    id_personal INT UNSIGNED NOT NULL,
    observaciones VARCHAR(200) NULL,

    CONSTRAINT pk_despacho_personal
        PRIMARY KEY (id_despacho_personal),

    CONSTRAINT uq_dp_despacho_unidad_personal
        UNIQUE (id_despacho_unidad, id_personal),

    CONSTRAINT fk_dp_despacho_unidad
        FOREIGN KEY (id_despacho_unidad)
        REFERENCES despacho_unidades (id_despacho_unidad)
        ON DELETE RESTRICT,

    CONSTRAINT fk_dp_personal
        FOREIGN KEY (id_personal)
        REFERENCES personal (id_personal)
        ON DELETE RESTRICT,

    INDEX idx_dp_id_personal (id_personal)
) ENGINE=InnoDB;


-- ============================================================
-- 17. compatibilidad_incidente_unidad
-- ============================================================

CREATE TABLE compatibilidad_incidente_unidad (
    id_tipo_incidente INT UNSIGNED NOT NULL,
    id_tipo_unidad INT UNSIGNED NOT NULL,
    prioridad_recomendacion SMALLINT UNSIGNED NOT NULL,
    observacion VARCHAR(200) NULL,
    activo BOOLEAN NOT NULL DEFAULT TRUE,

    CONSTRAINT pk_compatibilidad_incidente_unidad
        PRIMARY KEY (
            id_tipo_incidente,
            id_tipo_unidad
        ),

    CONSTRAINT ck_compatibilidad_prioridad
        CHECK (prioridad_recomendacion > 0),

    CONSTRAINT ck_compatibilidad_activo
        CHECK (activo IN (0, 1)),

    CONSTRAINT fk_compatibilidad_tipo_incidente
        FOREIGN KEY (id_tipo_incidente)
        REFERENCES tipos_incidente (id_tipo_incidente)
        ON DELETE RESTRICT,

    CONSTRAINT fk_compatibilidad_tipo_unidad
        FOREIGN KEY (id_tipo_unidad)
        REFERENCES tipos_unidad (id_tipo_unidad)
        ON DELETE RESTRICT,

    INDEX idx_compatibilidad_id_tipo_unidad (id_tipo_unidad)
) ENGINE=InnoDB;


-- ============================================================
-- FIN DE schema.sql
-- ============================================================
