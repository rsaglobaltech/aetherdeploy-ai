# Requerimientos: Credenciales y Barra de Progreso

## Problema 1 — Barra de progreso no funciona correctamente

### Síntoma
La `ProgressBar` en el CLI UI usa valores estáticos por estado (`analyzing=20`, `planning=55`, `deploying=80`) y nunca avanza de forma continua. El backend no emite eventos `progress` durante la ejecución real (Terraform, Docker), por lo que la barra se queda congelada en el porcentaje inicial del estado.

### Solución requerida

**Backend (Python):** Emitir eventos `{ "type": "progress", "step": "<nombre>", "percentage": <0-100> }` en los nodos de larga duración:

| Fase | Pasos con progreso |
|------|-------------------|
| Análisis | discovery(5) → analysis(20) |
| Planificación | proposal(40) → generation(55→70) |
| Despliegue feature | docker-up(72) → localstack-ready(78) → tf-init(82) → tf-apply(85→98) → done(100) |
| Despliegue prod | tf-init(72) → tf-plan(80) → tf-apply(82→98) → done(100) |

**Frontend (TypeScript):** 
- La barra debe renderizarse siempre que `isBusy || agentState === "deploying"`, no solo cuando `realProgress !== null`.
- Usar el progreso estimado por estado como fallback inicial, pero actualizarlo con los eventos reales.
- Limpiar el progreso (`null`) solo al llegar a `success`, `error` o `idle`.

---

## Problema 2 — Verificación de credenciales antes del despliegue

### Flujo requerido (por entorno)

#### Entorno `feature` (LocalStack)

1. Antes de ejecutar `_deploy_feature`, verificar `LOCALSTACK_AUTH_TOKEN` en el entorno.
2. Si no existe:
   - Emitir `{ "type": "waiting_credentials", "provider": "localstack", "message": "...", "fields": ["LOCALSTACK_AUTH_TOKEN"] }`
   - El frontend muestra un prompt especial (`CredentialsInput`) para que el usuario introduzca el token.
   - El usuario envía `{ "type": "credentials", "values": { "LOCALSTACK_AUTH_TOKEN": "ls-xxxx..." } }`
   - El backend inyecta las credenciales en el entorno del proceso y reintenta el despliegue.
3. Si la credencial es inválida (LocalStack responde 401/403), emitir error recuperable con mensaje claro.

#### Entorno `prod` (AWS / GCP / Azure)

**AWS:**
- Verificar presencia de `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` **o** perfil en `~/.aws/credentials`.
- Si no hay credenciales: pedir `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, y opcionalmente `AWS_SESSION_TOKEN`.

**GCP:**
- Verificar `GOOGLE_APPLICATION_CREDENTIALS` o `~/.config/gcloud/application_default_credentials.json`.
- Si no hay credenciales: pedir `GOOGLE_APPLICATION_CREDENTIALS` (ruta al JSON de service account).

**Azure:**
- Verificar `AZURE_CLIENT_ID` + `AZURE_CLIENT_SECRET` + `AZURE_TENANT_ID` **o** token de `az login`.
- Si no hay credenciales: pedir los tres campos.

### Protocolo IPC nuevo (eventos y comandos)

```typescript
// Evento backend → frontend
| { type: "waiting_credentials"; provider: "localstack" | "aws" | "gcp" | "azure"; message: string; fields: string[] }

// Comando frontend → backend  
| { type: "credentials"; values: Record<string, string> }
```

### Componente nuevo: `CredentialsInput`

Un componente Ink que muestra un formulario de un campo a la vez:
- Muestra el nombre del campo (`LOCALSTACK_AUTH_TOKEN`, `AWS_ACCESS_KEY_ID`, etc.)
- Oculta la entrada con `*` si el campo contiene `SECRET`, `KEY`, `TOKEN`, `PASSWORD`
- Al completar todos los campos, envía el comando `credentials` al backend
- Se renderiza en lugar del `ChatInput` mientras `waitingCredentials === true`

### Archivos a modificar

**Backend:**
- `src/aetherdeploy/agent/nodes/execution.py` — añadir `_check_feature_credentials()` y `_check_prod_credentials(provider)`
- `src/aetherdeploy/cli.py` — manejar evento `waiting_credentials` en `_stream_mode()`, añadir rama `elif cmd["type"] == "credentials"`
- `src/aetherdeploy/agent/nodes/execution.py` — emitir progreso granular en cada sub-paso

**Frontend:**
- `cli-ui/src/types.ts` — añadir `waiting_credentials` a `AgentEvent` y `credentials` a `UserCommand`
- `cli-ui/src/hooks/useAgent.ts` — manejar `waiting_credentials`, exponer `waitingCredentials` y `credentialFields`
- `cli-ui/src/components/CredentialsInput.tsx` — componente nuevo
- `cli-ui/src/App.tsx` — renderizar `CredentialsInput` cuando `waitingCredentials === true`

### Seguridad
- Las credenciales **nunca** se almacenan en disco ni en el historial de mensajes de la UI.
- Se inyectan como variables de entorno del proceso hijo Python (`proc.env`) via IPC, no como texto en mensajes.
- El campo de entrada muestra `*` para valores sensibles.
- El backend no loguea los valores de credenciales, solo confirma "credenciales recibidas".
