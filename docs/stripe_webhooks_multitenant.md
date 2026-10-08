# Webhooks Stripe multitenant

Los PaymentIntent nuevos de una clínica tenant llevan identidad lógica creada
por el backend (`empresa_id`, `empresa_codigo` y `tenant_id`). Nunca llevan el
nombre de la base ni aceptan ese dato desde el cliente. El webhook valida la
firma Stripe antes de resolver el Control Plane y abrir la base clínica.

Los PaymentIntent legacy sin marcadores tenant continúan procesándose solo en
la base legacy. Un marcador tenant parcial, inconsistente, suspendido o no
disponible falla cerrado y nunca se redirige a legacy.

## Reintento y conciliación

Stripe puede reintentar el mismo webhook sin riesgo: la transición del pago es
idempotente y la protección de servicios ya pagados permanece activa. Si el
Control Plane o la base tenant están temporalmente indisponibles, el endpoint
responde con error para que Stripe vuelva a intentar.

Un PaymentIntent tenant histórico que no contiene metadata tenant suficiente
no puede asignarse automáticamente sin riesgo de mezclar empresas. Debe
conciliarse administrativamente usando evidencia externa verificable (ID del
PaymentIntent, cuenta Stripe, importe, moneda y registro interno de creación),
y después reprocesarse con una herramienta operativa explícita. Este backend no
adivina la empresa ni modifica comprobantes o historiales durante ese proceso.
