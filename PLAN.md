# Plan de construcción — Iris-agent

**Objetivo:** tener **Claude Code corriendo 24/7** en el servidor, controlable por
WhatsApp: Juan escribe, Iris ejecuta comandos, crea cosas y responde por el mismo
chat.

**Principio de diseño (lore):** Iris es el arco, no el extremo. El conector de
WhatsApp y el núcleo de Claude Code son procesos separados unidos por un canal
local autenticado.

---

## Arquitectura objetivo

```
WhatsApp (Juan)
     │  Baileys (WhatsApp Web)
     ▼
┌─────────────────┐   HTTP + token   ┌──────────────────┐
│  iris-bridge    │ ◄──────────────► │    iris-core     │
│  (Node.js)      │                  │  (Python)        │
│  allowlist      │                  │  cola SQLite     │
│  /send /messages│                  │  worker          │
└─────────────────┘                  └────────┬─────────┘
                                              │ subprocess
                                              ▼
                                     ┌──────────────────┐
                                     │  claude -p       │
                                     │  --resume <id>   │
                                     │  (por proyecto)  │
                                     └──────────────────┘
                                              │
                                     ┌──────────────────┐
                                     │ workspace/       │
                                     │ proyecto A/B/…   │
                                     └──────────────────┘
```

- **iris-bridge**: habla WhatsApp. Solo su número puede escribir.
- **iris-core**: recibe, encola, ejecuta Claude Code, devuelve la respuesta.
- **claude**: una sesión por proyecto (`--resume`), ejecutándose dentro de
  `workspace/<proyecto>`.
- **SQLite**: cola persistente con *ack*; si el core muere, no se pierde nada.

---

## Fases

### Fase 0 — Decisiones (bloquea el resto)

| Decisión | Necesito de Juan |
|---|---|
| Número de WhatsApp | ¿Dedicado (chip/eSIM nueva) o el suyo? Recomendado: dedicado |
| Proveedor del modelo | Ollama Cloud en el servidor (`ollama signin`) u otra API |
| Proyecto piloto | Un repo/carpeta para la primera prueba |
| Nombre del usuario Linux | `iris` (recomendado) |

### Fase 1 — Base en el servidor

1. Crear usuario de sistema `iris` (sin sudo).
2. Instalar **Node.js 22 LTS** y **Claude Code** para ese usuario.
3. Instalar **Ollama** y hacer `ollama signin` (una vez, interactivo).
4. Configurar `~/.claude/settings.json` con `ANTHROPIC_BASE_URL` y modelo.
5. Prueba: `claude -p "responde OK"` y `claude -p --resume <id>`.

**Criterio de aceptación:** una orden headless devuelve texto y la sesión se
puede retomar.

### Fase 2 — Conector WhatsApp

1. Crear `iris-bridge/` (Node + Baileys + Express).
2. Endpoints: `GET /health`, `GET /messages`, `POST /send`, `/send-media`,
   `/typing`, `/read`.
3. Allowlist con el número de Juan; cola en memoria como Hermes (luego se
   reemplaza por la cola persistente del core).
4. pairing con QR por SSH.
5. Servicio `systemd --user` o de sistema `iris-bridge`.

**Criterio de aceptación:** Juan escribe por WhatsApp y el bridge lo deja en
`/messages`; un `POST /send` le llega a su teléfono.

### Fase 3 — Núcleo

1. `iris-core/` en Python (FastAPI + SQLite WAL).
2. Tabla `mensajes`: `id, chat_id, texto, estado (nuevo/procesando/respondido/error), intentos, creado`.
3. Worker: toma el mensaje, elige proyecto/sesión, lanza
   `claude -p --output-format stream-json --resume <id>`, guarda la salida.
4. Envía la respuesta por `POST /send` troceada a 4.096 caracteres.
5. Comandos de control:
   - `/proyecto <nombre>` — cambia de proyecto.
   - `/nuevo` — sesión nueva.
   - `/estado` — cola, sesión, uptime.
   - `/detener` — cancela la tarea en curso.
6. `systemd`: `iris-core.service`, reinicio automático.

**Criterio de aceptación:** Juan pide "crea un archivo X en el proyecto Y",
Iris lo crea y confirma por WhatsApp.

### Fase 4 — Aprobaciones

1. Hook `PreToolUse` en Claude Code que consulte a `iris-core`.
2. El core manda por WhatsApp: "Voy a ejecutar: `rm -rf …`. ¿Sí o no?"
3. La respuesta se correlaciona por ID; timeout de 5 min → se deniega.
4. Reglas: comandos destructivos, escritura fuera del workspace, red saliente.

**Criterio de aceptación:** un `rm` no se ejecuta hasta que Juan responde "sí".

### Fase 5 — Aislamiento y operación

**Decisión 2026-11-05:** sin Docker por ahora. El aislamiento es el usuario
`iris` + workspace separado + aprobaciones. Docker queda como paso futuro
opcional.

1. Auditoría: cada comando ejecutado queda en la tabla `audit` (sin secretos).
2. Rotación de logs y respaldos de la sesión de WhatsApp.
3. (Futuro) mover el runner de Claude a un contenedor con solo `workspace/`
   montado.

### Fase 6 — Extras (después)

- Notas de voz (STT/TTS).
- Envío y recepción de archivos.
- Tareas programadas ("cada día a las 8 revisa X").
- Panel web mínimo para ver la cola y las sesiones.

---

## Riesgos asumidos

- **Shell remoto por WhatsApp**: allowlist estricta, número dedicado,
  aprobaciones y contenedor.
- **Baileys no es oficial**: puede romperse o banear el número; alternativa
  `WhatsApp Cloud API` si se vuelve producto.
- **Coste del modelo**: 24/7 con uso real consume; conviene definir límites.
- **Sesión de WhatsApp = credencial**: respaldo cifrado y permisos `600`.

---

## Estado (2026-11-05)

Decisiones cerradas:

- Sin Docker. Instalador **nativo** para Linux/WSL.
- Usuario dedicado **`iris`**, creado por el instalador.
- Ollama dentro del stack (servicio propio como `iris`).
- El modelo se pregunta durante la instalación.
- Juan probará primero en **WSL**.

Construido (v0.1.0):

| Archivo | Qué es |
|---|---|
| `install.sh` | Instalador completo (Node, Claude Code, Ollama, usuario, servicios, pareo, prueba) |
| `bin/iris` | CLI de gestión (`status`, `logs`, `pair`, `model`, `restart`, `update`, `doctor`, `uninstall`) |
| `bridge/` | Puente WhatsApp (Baileys) con allowlist y token |
| `core/` | Núcleo (FastAPI + SQLite + worker de Claude Code) |
| `systemd/` | Servicios `iris-ollama`, `iris-bridge`, `iris-core` |

Pendiente inmediato: **prueba de Juan en WSL** (pareo y primer mensaje).

*(Documento vivo.)*
