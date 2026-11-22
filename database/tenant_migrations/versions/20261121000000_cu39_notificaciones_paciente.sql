CREATE TABLE public.notificacion_paciente (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    paciente_id BIGINT NOT NULL REFERENCES public.paciente(id) ON DELETE CASCADE,
    titulo VARCHAR(150) NOT NULL,
    mensaje TEXT NOT NULL,
    tipo VARCHAR(50) NOT NULL,
    fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    fecha_envio TIMESTAMPTZ,
    leida BOOLEAN NOT NULL DEFAULT FALSE,
    fecha_lectura TIMESTAMPTZ,
    clave_evento VARCHAR(200),
    CONSTRAINT uq_notificacion_paciente_evento UNIQUE (paciente_id, clave_evento)
);
CREATE INDEX ix_notificacion_paciente_lista
    ON public.notificacion_paciente (paciente_id, fecha_creacion DESC, id DESC);
