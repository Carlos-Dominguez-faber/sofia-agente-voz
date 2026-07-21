# El dashboard de Sofía — README

Panel web para el dueño de la clínica. Muestra lo que hizo el agente de voz
—llamadas, citas, temperatura de leads, funnel— y deja editar el prompt, disparar
una llamada y ver el estado de los servicios.

**No tiene base de datos.** Lee en vivo de dos fuentes: GoHighLevel (CRM) y el
backend de Sofía en Modal. Para el porqué de cada decisión, lee
[`BLUEPRINT.md`](./BLUEPRINT.md).

---

## Requisitos

- **Node 20+**
- El **backend de Sofía ya desplegado en Modal** (el dashboard lee de él; no
  funciona sin él).
- El **`DASHBOARD_API_TOKEN`** que configuraste en el Modal Secret del backend.

---

## 1. Configurar el entorno

El panel se configura con **cuatro** variables, todas de **solo servidor**. Ninguna
lleva el prefijo `NEXT_PUBLIC_` a propósito: ese prefijo inyectaría el valor en el
bundle del navegador, y eso convierte un token en un token publicado.

Copia la plantilla y llénala:

```bash
cd dashboard
cp .env.example .env.local
```

`.env.local`:

```bash
# URL pública del backend en Modal (la imprime `modal deploy`).
BACKEND_URL=https://<tu-cuenta>--agente-voz-ghl-fastapi-app.modal.run

# Debe coincidir EXACTAMENTE con el DASHBOARD_API_TOKEN del Modal Secret.
# Si no coinciden, el panel recibe 401 en todo.
DASHBOARD_API_TOKEN=<el mismo token del backend>

# La contraseña para abrir el panel. Un cliente, una contraseña.
#   python3 -c "import secrets; print(secrets.token_urlsafe(12))"
DASHBOARD_PASSWORD=<genera una>

# Firma la cookie de sesión. No tiene relación con la contraseña.
#   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
DASHBOARD_SESSION_SECRET=<genera otra>
```

> **El token del panel y el del backend son el MISMO valor.** El panel lo usa para
> autenticarse contra Modal. Si los cambias en un lado, cámbialos en los dos.

---

## 2. Correr en local

```bash
cd dashboard
npm install
npm run dev
```

Abre `http://localhost:3000`, entra con tu `DASHBOARD_PASSWORD`, y deberías ver las
siete secciones con datos reales de tu Location.

**Si todas dicen "Dato no disponible":** casi siempre es una de tres cosas —el
`BACKEND_URL` está mal, el token no coincide con el del backend, o el backend no
tiene desplegados los endpoints de lectura (`/dashboard/*`)—.

---

## 3. Apuntarlo a otro backend

Todo lo que define a qué backend habla el panel es `BACKEND_URL` + `DASHBOARD_API_TOKEN`
en `.env.local`. Para apuntarlo a otra clínica —otro Modal, otra Location— cambias
esas dos variables y reinicias. No hay nada hardcodeado en el código.

---

## 4. Desplegar

El panel es una app Next.js estándar. Lo único no negociable: **las cuatro variables
de entorno se configuran en el panel de tu hosting**, nunca se commitean.

### Vercel, importando el repo desde GitHub

Este panel vive en el **subdirectorio `dashboard/`** de un repo que también contiene
el backend en Python. Eso hace que **el Root Directory sea obligatorio** — sin él,
Vercel intenta construir desde la raíz del repo, no encuentra `package.json` y falla.

Al importar el proyecto en Vercel, configura exactamente esto:

| Ajuste | Valor |
|--------|-------|
| **Framework Preset** | Next.js (Vercel lo detecta solo) |
| **Root Directory** | `dashboard` ← **crítico**, sin esto no compila |
| **Build Command** | `next build` (default; no lo cambies) |
| **Output Directory** | (déjalo en blanco; Next lo maneja) |
| **Install Command** | `npm install` (default) |
| **Node.js Version** | **20.x** (o superior) — también está fijado en `package.json` con `engines.node` |

Luego, **Settings → Environment Variables**, agrega las cuatro (§1) como variables
normales de servidor —**ninguna** marcada como expuesta al cliente, ninguna con
`NEXT_PUBLIC_`—. Next las lee del lado del servidor.

Con eso, cada push a la rama conectada despliega solo. O desde la terminal, dentro
de `dashboard/`:

```bash
vercel        # preview primero
vercel --prod # cuando el preview se vea bien
```

> **El backend se despliega aparte**, con su propio comando —y el sufijo es
> obligatorio:
>
> ```bash
> modal deploy app/main.py::modal_app
> ```
>
> Sin `::modal_app`, Modal busca una variable llamada `app` (la nuestra se llama
> `modal_app`) y falla antes de construir.

---

## 5. Cambiar el branding

Todo lo que el cliente ve como "suyo" vive en **un solo archivo**:
`src/config/branding.ts`. Nombre de la clínica, colores, logo, la línea de soporte
del pie. Cámbialo ahí y listo —no hay nombres de clínica ni colores de marca
regados por el código—.

```ts
export const branding: Branding = {
  clinicName: "Clínica Dental Sonrisa Perfecta",
  tagline: "Panel de recepción",
  agentName: "Sofía",
  logoMark: "🦷", // emoji, o cámbialo por un <img> en el header
  colors: {
    accent: "#0d9488", // el color principal del panel
    accentSoft: "#ccfbf1",
    hot: "#dc2626", // temperatura de leads
    warm: "#d97706",
    cold: "#0284c7",
  },
  locale: "es-MX",
  supportLine: "¿Algo no cuadra? Escríbenos y lo revisamos.",
};
```

---

## 6. Cómo está organizado

```
dashboard/
├── docs/                     ← estás aquí
├── src/
│   ├── config/branding.ts    ← la marca del cliente (un archivo)
│   ├── lib/
│   │   ├── api.ts            ← lo ÚNICO que habla con el backend (server-side)
│   │   ├── env.ts           ← lee las variables de solo-servidor
│   │   └── session.ts       ← firma/verifica la cookie de sesión
│   ├── proxy.ts             ← el gate: exige sesión antes de cada petición
│   ├── app/
│   │   ├── login/           ← la contraseña del panel
│   │   ├── api/             ← proxies EXPLÍCITOS (uno por operación, no comodín)
│   │   └── page.tsx         ← la página con las 7 secciones
│   └── components/          ← una sección por componente + SourceState
└── .env.local              ← tus cuatro variables (NO se commitea)
```

**Las dos reglas que sostienen todo** (detalle en el BLUEPRINT):

1. **El navegador nunca ve una credencial.** Todo pasa por `api.ts` del lado del
   servidor.
2. **El panel solo lee.** Los proxies son explícitos, uno por operación permitida —
   nunca un comodín que pudiera alcanzar los endpoints de acción del agente.

---

## Problemas comunes

| Síntoma                                 | Causa más probable                                                                                |
| --------------------------------------- | ------------------------------------------------------------------------------------------------- |
| Todo dice "Dato no disponible"          | `BACKEND_URL` mal, o el backend no tiene los endpoints `/dashboard/*` desplegados.                |
| Todo da 401 / no carga nada             | El `DASHBOARD_API_TOKEN` del panel no coincide con el del Modal Secret.                           |
| No me deja entrar                       | `DASHBOARD_PASSWORD` mal escrita, o falta `DASHBOARD_SESSION_SECRET`.                             |
| Las llamadas salen "Sin identificar"    | Normal en web calls y en llamadas donde el paciente colgó antes de dar su número. No es un error. |
| El editor del prompt no me deja guardar | Borraste el bloque de reglas de seguridad. Restáuralo con el botón —no se puede publicar sin él—. |
