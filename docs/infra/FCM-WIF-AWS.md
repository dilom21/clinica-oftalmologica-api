# FCM en AWS EC2 mediante Workload Identity Federation

## Alcance y estado

Proyecto Firebase/Google Cloud: `clinica-oftalmologica-24ff7`.

AWS:
- Cuenta: `414997933714`
- Región EC2: `us-east-2`
- Instancia: `i-0c1077cb35f8f9073`
- Rol adjunto: `clinica-oftalmologica-fcm-role`

Recursos Google propuestos:
- Pool: `clinica-aws-pool`
- Provider AWS: `aws-ec2-fcm`
- Service Account: `fcm-worker`
- Configuración generada: `secrets/fcm-wif/gcp-wif-credentials.json`

Este runbook prepara la federación y el servicio. Los recursos de Google Cloud deben crearse con una cuenta autorizada del proyecto. No se almacena ninguna clave privada de Service Account.

## 1. Requisitos y comprobaciones previas en Google Cloud Shell

Seleccionar el proyecto y verificar su identidad antes de crear recursos:

```bash
gcloud auth list
gcloud config set project clinica-oftalmologica-24ff7
gcloud projects describe clinica-oftalmologica-24ff7 \
  --format='yaml(projectId,projectNumber,lifecycleState)'
gcloud services list --enabled --project=clinica-oftalmologica-24ff7
```

Anotar el `projectNumber` devuelto. No usar el Project ID donde Google exige el número.

Habilitar las APIs requeridas:

```bash
gcloud services enable \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  sts.googleapis.com \
  cloudresourcemanager.googleapis.com \
  fcm.googleapis.com \
  --project=clinica-oftalmologica-24ff7
```

Se requieren permisos administrativos para administrar Workload Identity Pools, cuentas de servicio y habilitación de APIs.

## 2. Crear el Workload Identity Pool

```bash
gcloud iam workload-identity-pools create clinica-aws-pool \
  --project=clinica-oftalmologica-24ff7 \
  --location=global \
  --display-name="Clínica AWS EC2 FCM" \
  --description="Federación restringida a la instancia EC2 que ejecuta el worker FCM"
```

## 3. Crear proveedor AWS con restricción de cuenta, rol e instancia

El mapeo oficial expone el rol asumido y el ID de instancia EC2:

```bash
gcloud iam workload-identity-pools providers create-aws aws-ec2-fcm \
  --project=clinica-oftalmologica-24ff7 \
  --location=global \
  --workload-identity-pool=clinica-aws-pool \
  --account-id=414997933714 \
  --attribute-mapping="google.subject=assertion.arn,attribute.account=assertion.account,attribute.aws_role=assertion.arn.extract('assumed-role/{role_name}/'),attribute.aws_ec2_instance=assertion.arn.extract('assumed-role/{role_and_session}').extract('/{session}')" \
  --attribute-condition="assertion.account == '414997933714' && attribute.aws_role == 'clinica-oftalmologica-fcm-role' && attribute.aws_ec2_instance == 'i-0c1077cb35f8f9073'"
```

Verificar que el provider quedó activo y con la condición exacta:

```bash
gcloud iam workload-identity-pools providers describe aws-ec2-fcm \
  --project=clinica-oftalmologica-24ff7 \
  --location=global \
  --workload-identity-pool=clinica-aws-pool \
  --format=yaml(name,state,attributeMapping,attributeCondition,aws)
```

No continuar si la condición no contiene exactamente la cuenta, rol e instancia anteriores.

## 4. Crear Service Account y conceder permiso FCM

```bash
gcloud iam service-accounts create fcm-worker \
  --project=clinica-oftalmologica-24ff7 \
  --display-name="Worker FCM AWS EC2" \
  --description="Identidad federada para enviar notificaciones FCM desde EC2"

SA="fcm-worker@clinica-oftalmologica-24ff7.iam.gserviceaccount.com"

gcloud projects add-iam-policy-binding clinica-oftalmologica-24ff7 \
  --member="serviceAccount:${SA}" \
  --role="roles/firebasecloudmessaging.admin"
```

El rol predefinido `roles/firebasecloudmessaging.admin` incluye `cloudmessaging.messages.create`, requerido por FCM HTTP v1, además de permisos de gestión de suscripciones a temas y lectura de datos de entrega. Se elige porque es el rol predefinido específico de FCM; no conceder roles Owner, Editor general ni Firebase Admin.

## 5. Permitir que únicamente la EC2 autorizada suplante la Service Account

Obtener el número del proyecto:

```bash
PROJECT_NUMBER="$(gcloud projects describe clinica-oftalmologica-24ff7 --format='value(projectNumber)')"
POOL_MEMBER="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/clinica-aws-pool/attribute.aws_ec2_instance/i-0c1077cb35f8f9073"
SA="fcm-worker@clinica-oftalmologica-24ff7.iam.gserviceaccount.com"

gcloud iam service-accounts add-iam-policy-binding "${SA}" \
  --project=clinica-oftalmologica-24ff7 \
  --role=roles/iam.workloadIdentityUser \
  --member="${POOL_MEMBER}"
```

El provider ya rechaza identidades que no coincidan con la cuenta, rol e instancia. El binding de la Service Account vuelve a restringir la suplantación al atributo de instancia exacta.

## 6. Generar el JSON de credenciales externas con IMDSv2

En un equipo administrativo con Google Cloud CLI autenticado y con permisos de lectura de los recursos:

```bash
PROJECT_NUMBER="$(gcloud projects describe clinica-oftalmologica-24ff7 --format='value(projectNumber)')"
SA="fcm-worker@clinica-oftalmologica-24ff7.iam.gserviceaccount.com"
mkdir -p secrets/fcm-wif
chmod 700 secrets secrets/fcm-wif

gcloud iam workload-identity-pools create-cred-config \
  "projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/clinica-aws-pool/providers/aws-ec2-fcm" \
  --service-account="${SA}" \
  --aws \
  --enable-imdsv2 \
  --output-file="secrets/fcm-wif/gcp-wif-credentials.json"

chmod 600 secrets/fcm-wif/gcp-wif-credentials.json
python -m json.tool secrets/fcm-wif/gcp-wif-credentials.json >/dev/null
```

Este archivo es una configuración de credenciales externas, no una clave privada ni un JSON de Service Account. El flujo obtiene credenciales temporales del Instance Metadata Service usando IMDSv2.

No copiar el archivo a Git, imágenes Docker, artefactos públicos ni mensajes de chat. Entregarlo a EC2 mediante un canal seguro y crear el archivo en `secrets/fcm-wif/` con permisos restringidos.

## 7. Configurar el entorno del worker

En EC2, copiar `.env.fcm-worker.example` a `.env.fcm-worker`, completar los valores reales requeridos por la configuración común del backend y protegerlo:

```bash
umask 077
cp .env.fcm-worker.example .env.fcm-worker
chmod 600 .env.fcm-worker
mkdir -p secrets/fcm-wif
# Dockerfile ejecuta el proceso como UID/GID 10001 (clinic).
sudo chown -R 10001:10001 secrets/fcm-wif
sudo chmod 0500 secrets/fcm-wif
sudo chmod 0400 secrets/fcm-wif/gcp-wif-credentials.json
```

La configuración actual de `app/core/config.py` valida también DATABASE_URL, JWT, URL de restablecimiento y variables Gmail al importar el backend. Aunque Gmail no forma parte del trabajo del worker FCM, la plantilla incluye esos valores porque son obligatorios por la validación global vigente. No usar valores ficticios en producción.

## 8. Validar Compose sin iniciar workers

```bash
docker compose --env-file .env config --quiet
APP_ENV_FILE=.env.fcm-worker FCM_WORKER_ENV_FILE=.env.fcm-worker docker compose --profile fcm config --quiet
docker compose ps
```

El worker FCM usa el perfil opt-in `fcm`, un env_file dedicado, el JSON montado en solo lectura y no publica puertos. El Dockerfile ejecuta la aplicación como UID/GID `10001:10001`; por eso el directorio montado y el archivo deben ser legibles por ese UID (directorio `0500`, archivo `0400`, propiedad `10001:10001`). El worker de pagos/Resend no se modifica.

## 9. Validar ADC y luego iniciar el worker

Antes de iniciar procesos, confirmar que la identidad del contenedor coincide con el rol esperado y que la política IAM de Google está aplicada. En EC2:

```bash
AWS_REGION=us-east-2 aws sts get-caller-identity
```

El ARN debe corresponder a `arn:aws:sts::414997933714:assumed-role/clinica-oftalmologica-fcm-role/i-0c1077cb35f8f9073` (el formato exacto de la sesión puede variar; verificar el ID de instancia extraído).

Probar la resolución ADC sin imprimir tokens:

```bash
APP_ENV_FILE=.env.fcm-worker FCM_WORKER_ENV_FILE=.env.fcm-worker \
  docker compose --profile fcm run --rm --no-deps worker-fcm \
  python -c "import google.auth; c,p=google.auth.default(scopes=['https://www.googleapis.com/auth/firebase.messaging']); c.refresh(__import__('google.auth.transport.requests',fromlist=['Request']).Request()); print('ADC_OK', bool(c.token), 'PROJECT', p)"
```

La prueba anterior ejecuta solo una validación de credenciales y no llama al endpoint de envío FCM. No imprimir access tokens, AWS secret keys ni session tokens.

Cuando ADC esté confirmado y el equipo autorice el procesamiento de la cola, iniciar el worker como único proceso:

```bash
APP_ENV_FILE=.env.fcm-worker FCM_WORKER_ENV_FILE=.env.fcm-worker \
  docker compose --profile fcm up -d worker-fcm

docker compose ps
docker compose logs --tail=100 worker-fcm
```

No escalar el servicio. Mantener exactamente una instancia de worker FCM para evitar ciclos concurrentes no previstos.

## 10. Rollback

```bash
docker compose --profile fcm stop worker-fcm
docker compose --profile fcm rm -f worker-fcm
```

No eliminar el pool, provider, Service Account o IAM bindings durante un rollback operativo sin revisar primero las dependencias. No usar `docker compose down -v`.

## Referencias oficiales

- https://cloud.google.com/iam/docs/workload-identity-federation-with-other-clouds
- https://cloud.google.com/sdk/gcloud/reference/iam/workload-identity-pools/create-cred-config
- https://cloud.google.com/iam/docs/roles-permissions/firebasecloudmessaging
