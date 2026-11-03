-- SI2 Clínica Oftalmológica - Sprint 3 (CU26, CU27 y CU29)
-- APLICAR SOLO EN BASES tenant_* TRAS PREFLIGHT. NO EJECUTAR EN postgres.
-- NO CONTIENE BEGIN/COMMIT: el ejecutor usa una transacción completa y ledger.
-- No copia datos de la base central ni altera información clínica.
-- Proveedores existía en postgres pero puede faltar en tenants antiguos.
DO $migrate$
BEGIN
 IF to_regclass('public.proveedores') IS NULL THEN
   CREATE TABLE public.proveedores (
     id SERIAL PRIMARY KEY,
     razon_social VARCHAR(150) NOT NULL,
     nit VARCHAR(30),
     telefono VARCHAR(20),
     correo VARCHAR(100),
     direccion VARCHAR(255),
     contacto VARCHAR(100),
     estado BOOLEAN
   );
   CREATE INDEX ix_proveedores_id ON public.proveedores(id);
   CREATE UNIQUE INDEX ix_proveedores_nit ON public.proveedores(nit);
   ALTER TABLE public.proveedores ENABLE ROW LEVEL SECURITY;
   REVOKE ALL ON TABLE public.proveedores FROM anon, authenticated;
   REVOKE ALL ON SEQUENCE public.proveedores_id_seq FROM anon, authenticated;
 END IF;
END
$migrate$;

CREATE TABLE public.categoria (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 nombre VARCHAR(100) NOT NULL,
 descripcion TEXT,
 estado BOOLEAN NOT NULL DEFAULT TRUE,
 fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT ck_categoria_nombre_no_vacio CHECK (length(btrim(nombre)) > 0)
);
CREATE UNIQUE INDEX uq_categoria_nombre_normalizado ON public.categoria(lower(btrim(nombre)));

CREATE TABLE public.producto (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 categoria_id BIGINT NOT NULL,
 codigo VARCHAR(50) NOT NULL,
 nombre VARCHAR(160) NOT NULL,
 descripcion TEXT,
 unidad_medida VARCHAR(30) NOT NULL DEFAULT 'UNIDAD',
 stock_minimo INTEGER NOT NULL DEFAULT 0,
 estado BOOLEAN NOT NULL DEFAULT TRUE,
 fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT fk_producto_categoria FOREIGN KEY(categoria_id) REFERENCES public.categoria(id) ON UPDATE CASCADE ON DELETE RESTRICT,
 CONSTRAINT uq_producto_codigo UNIQUE(codigo),
 CONSTRAINT ck_producto_codigo_no_vacio CHECK(length(btrim(codigo)) > 0),
 CONSTRAINT ck_producto_nombre_no_vacio CHECK(length(btrim(nombre)) > 0),
 CONSTRAINT ck_producto_unidad_medida_no_vacia CHECK(length(btrim(unidad_medida)) > 0),
 CONSTRAINT ck_producto_stock_minimo CHECK(stock_minimo >= 0)
);
CREATE INDEX idx_producto_categoria ON public.producto(categoria_id);
CREATE INDEX idx_producto_nombre ON public.producto(nombre);

CREATE TABLE public.lote (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 producto_id BIGINT NOT NULL,
 numero_lote VARCHAR(80) NOT NULL,
 fecha_fabricacion DATE,
 fecha_vencimiento DATE,
 observaciones TEXT,
 estado BOOLEAN NOT NULL DEFAULT TRUE,
 fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT fk_lote_producto FOREIGN KEY(producto_id) REFERENCES public.producto(id) ON UPDATE CASCADE ON DELETE RESTRICT,
 CONSTRAINT uq_lote_producto_numero UNIQUE(producto_id,numero_lote),
 CONSTRAINT ck_lote_numero_no_vacio CHECK(length(btrim(numero_lote)) > 0),
 CONSTRAINT ck_lote_fechas CHECK(fecha_fabricacion IS NULL OR fecha_vencimiento IS NULL OR fecha_vencimiento >= fecha_fabricacion)
);
CREATE INDEX idx_lote_producto ON public.lote(producto_id);
CREATE INDEX idx_lote_vencimiento ON public.lote(fecha_vencimiento) WHERE fecha_vencimiento IS NOT NULL;

CREATE TABLE public.inventario (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 lote_id BIGINT NOT NULL,
 stock_actual INTEGER NOT NULL DEFAULT 0,
 stock_reservado INTEGER NOT NULL DEFAULT 0,
 fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT fk_inventario_lote FOREIGN KEY(lote_id) REFERENCES public.lote(id) ON UPDATE CASCADE ON DELETE RESTRICT,
 CONSTRAINT uq_inventario_lote UNIQUE(lote_id),
 CONSTRAINT ck_inventario_stock_actual CHECK(stock_actual >= 0),
 CONSTRAINT ck_inventario_stock_reservado CHECK(stock_reservado >= 0),
 CONSTRAINT ck_inventario_stock_disponible CHECK(stock_reservado <= stock_actual)
);

CREATE TABLE public.movimiento_inventario (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 inventario_id BIGINT NOT NULL,
 usuario_id BIGINT,
 tipo VARCHAR(32) NOT NULL,
 cantidad INTEGER NOT NULL,
 stock_anterior INTEGER NOT NULL,
 stock_posterior INTEGER NOT NULL,
 fecha_hora TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 observaciones TEXT,
 referencia_tipo VARCHAR(40),
 referencia_id BIGINT,
 CONSTRAINT fk_movimiento_inventario_inventario FOREIGN KEY(inventario_id) REFERENCES public.inventario(id) ON UPDATE CASCADE ON DELETE RESTRICT,
 CONSTRAINT fk_movimiento_inventario_usuario FOREIGN KEY(usuario_id) REFERENCES public.usuario(id) ON UPDATE CASCADE ON DELETE SET NULL,
 CONSTRAINT ck_movimiento_inventario_cantidad CHECK(cantidad > 0),
 CONSTRAINT ck_movimiento_inventario_saldos CHECK(stock_anterior >= 0 AND stock_posterior >= 0),
 CONSTRAINT ck_movimiento_inventario_referencia CHECK((referencia_tipo IS NULL AND referencia_id IS NULL) OR (referencia_tipo IS NOT NULL AND length(btrim(referencia_tipo)) > 0 AND referencia_id IS NOT NULL AND referencia_id > 0)),
 CONSTRAINT ck_movimiento_inventario_tipo CHECK(tipo IN ('STOCK_INICIAL','ENTRADA_COMPRA','SALIDA_USO','AJUSTE_ENTRADA','AJUSTE_SALIDA','DEVOLUCION_ENTRADA','DEVOLUCION_PROVEEDOR','SALIDA_VENCIMIENTO')),
 CONSTRAINT ck_movimiento_inventario_delta CHECK(
  (tipo IN ('STOCK_INICIAL','ENTRADA_COMPRA','AJUSTE_ENTRADA','DEVOLUCION_ENTRADA') AND stock_posterior::BIGINT = stock_anterior::BIGINT + cantidad::BIGINT)
  OR (tipo IN ('SALIDA_USO','AJUSTE_SALIDA','DEVOLUCION_PROVEEDOR','SALIDA_VENCIMIENTO') AND stock_anterior::BIGINT = stock_posterior::BIGINT + cantidad::BIGINT)
 )
);
CREATE INDEX idx_movimiento_inventario_fecha ON public.movimiento_inventario(inventario_id,fecha_hora DESC,id DESC);
CREATE INDEX idx_movimiento_inventario_usuario ON public.movimiento_inventario(usuario_id) WHERE usuario_id IS NOT NULL;
CREATE INDEX idx_movimiento_inventario_referencia ON public.movimiento_inventario(referencia_tipo,referencia_id) WHERE referencia_tipo IS NOT NULL AND referencia_id IS NOT NULL;

CREATE TABLE public.compra (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 proveedor_id INTEGER NOT NULL,
 usuario_id BIGINT NOT NULL,
 numero_documento VARCHAR(80),
 fecha_compra TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 fecha_recepcion TIMESTAMPTZ,
 moneda VARCHAR(3) NOT NULL DEFAULT 'BOB',
 estado VARCHAR(20) NOT NULL DEFAULT 'BORRADOR',
 observaciones TEXT,
 fecha_creacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 fecha_actualizacion TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
 CONSTRAINT fk_compra_proveedor FOREIGN KEY(proveedor_id) REFERENCES public.proveedores(id) ON UPDATE CASCADE ON DELETE RESTRICT,
 CONSTRAINT fk_compra_usuario FOREIGN KEY(usuario_id) REFERENCES public.usuario(id) ON UPDATE CASCADE ON DELETE RESTRICT,
 CONSTRAINT ck_compra_documento CHECK(numero_documento IS NULL OR length(btrim(numero_documento)) > 0),
 CONSTRAINT ck_compra_moneda CHECK(moneda ~ '^[A-Z]{3}$'),
 CONSTRAINT ck_compra_estado CHECK(estado IN ('BORRADOR','CONFIRMADA','RECIBIDA','ANULADA')),
 CONSTRAINT ck_compra_recepcion CHECK((estado='RECIBIDA' AND fecha_recepcion IS NOT NULL) OR (estado<>'RECIBIDA' AND fecha_recepcion IS NULL)),
 CONSTRAINT ck_compra_fecha_recepcion CHECK(fecha_recepcion IS NULL OR fecha_recepcion >= fecha_compra)
);
CREATE UNIQUE INDEX uq_compra_proveedor_documento ON public.compra(proveedor_id,numero_documento) WHERE numero_documento IS NOT NULL;
CREATE INDEX idx_compra_proveedor_fecha ON public.compra(proveedor_id,fecha_compra DESC);
CREATE INDEX idx_compra_estado_fecha ON public.compra(estado,fecha_compra DESC);
CREATE INDEX idx_compra_usuario ON public.compra(usuario_id);

CREATE TABLE public.detalle_compra (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 compra_id BIGINT NOT NULL,
 lote_id BIGINT NOT NULL,
 cantidad INTEGER NOT NULL,
 costo_unitario NUMERIC(12,2) NOT NULL,
 observaciones TEXT,
 CONSTRAINT fk_detalle_compra_compra FOREIGN KEY(compra_id) REFERENCES public.compra(id) ON UPDATE CASCADE ON DELETE RESTRICT,
 CONSTRAINT fk_detalle_compra_lote FOREIGN KEY(lote_id) REFERENCES public.lote(id) ON UPDATE CASCADE ON DELETE RESTRICT,
 CONSTRAINT uq_detalle_compra_compra_lote UNIQUE(compra_id,lote_id),
 CONSTRAINT ck_detalle_compra_cantidad CHECK(cantidad>0),
 CONSTRAINT ck_detalle_compra_costo_unitario CHECK(costo_unitario>=0)
);
CREATE INDEX idx_detalle_compra_lote ON public.detalle_compra(lote_id);
CREATE UNIQUE INDEX uq_movimiento_entrada_detalle_compra ON public.movimiento_inventario(referencia_tipo,referencia_id) WHERE tipo='ENTRADA_COMPRA' AND referencia_tipo='DETALLE_COMPRA' AND referencia_id IS NOT NULL;

COMMENT ON TABLE public.producto IS 'CU26. stock_minimo es umbral de alertas; NO es existencia actual.';
COMMENT ON TABLE public.inventario IS 'CU27. Existencias por lote, actualizadas por transaccion de backend.';
COMMENT ON TABLE public.movimiento_inventario IS 'CU27. Registro inmutable de movimientos; requiere actualizacion atomica de inventario.';
COMMENT ON TABLE public.compra IS 'CU29. Registrar compras, recepcion integral; sin recepciones parciales.';
COMMENT ON TABLE public.detalle_compra IS 'CU29. Detalle por lote; producto_id se obtiene de lote.';

ALTER TABLE public.categoria ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.producto ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lote ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventario ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.movimiento_inventario ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.compra ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.detalle_compra ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.categoria,public.producto,public.lote,public.inventario,public.movimiento_inventario,public.compra,public.detalle_compra FROM anon,authenticated;
-- El backend existente usa conexiones de confianza PostgreSQL y aplica RBAC.
-- Nunca insertar registros de postgres en las tablas nuevas de las clínicas.
REVOKE ALL ON SEQUENCE public.categoria_id_seq, public.producto_id_seq, public.lote_id_seq,
 public.inventario_id_seq, public.movimiento_inventario_id_seq, public.compra_id_seq,
 public.detalle_compra_id_seq FROM anon, authenticated;
