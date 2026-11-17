# Stripe local (Test Mode)

Variables requeridas en el entorno local:

```env
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_CURRENCY=bob
```

No se deben versionar los valores de `STRIPE_SECRET_KEY` ni
`STRIPE_WEBHOOK_SECRET`. El secreto `whsec_...` del listener local puede ser
distinto para cada desarrollador.

Levantar el backend:

```bash
uvicorn app.main:app --reload
```

Iniciar el listener de Stripe CLI:

```bash
stripe listen --events payment_intent.succeeded,payment_intent.payment_failed,payment_intent.processing,payment_intent.canceled --forward-to http://127.0.0.1:8000/pagos/stripe/webhook
```

Los PaymentIntent de esta integración utilizan exclusivamente Stripe Test
Mode. La clave publicable se configurará posteriormente en Flutter.
