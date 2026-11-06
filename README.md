# Iris Agent

Claude Code corriendo 24/7 en un servidor Linux, controlable por Telegram
(por defecto) o por WhatsApp.

Escribes por Telegram → Iris ejecuta Claude Code en un directorio de trabajo →
te responde por el mismo chat, con formato (negrita, tablas y bloques de
código).

```
Telegram ──► iris-telegram (Python, Bot API) ──HTTP+token──┐
                                                           ├──► iris-core (Python)
WhatsApp ──► iris-bridge (Node + Baileys) ──HTTP+token─────┘         │
                                                                     ▼
                                                           claude -p (headless)
                                                                     │
                                                                     ▼
                                                          ~/workspace/<proyecto>
```

## Requisitos

- Linux con **systemd** (Ubuntu/Debian). WSL sirve para pruebas.
- Acceso `sudo`.
- Una forma de acceso al modelo:
  - suscripción de Claude (Pro/Max/Team),
  - clave de API de Anthropic,
  - Ollama local con cuenta de Ollama Cloud,
  - otro endpoint compatible con la API de Anthropic (por ejemplo, la API de
    Ollama Cloud con `https://ollama.com` y su clave).

Según el canal:

- **Telegram** (por defecto): un bot creado en [@BotFather](https://t.me/BotFather)
  y tu ID numérico de usuario. No hace falta número de teléfono.
- **WhatsApp**: un número dedicado (recomendado) para el bot.

## Instalación

**Desde GitHub (recomendado, siempre trae la última versión):**

```bash
rm -rf /tmp/iris-agent-main \
  && curl -fsSL https://github.com/JUANRESTREPO95/iris-agent/archive/refs/heads/main.tar.gz | tar -xz -C /tmp \
  && sudo bash /tmp/iris-agent-main/install.sh
```

Alternativa con `raw` (GitHub la cachea hasta 5 minutos después de un cambio):

```bash
curl -fsSL https://raw.githubusercontent.com/JUANRESTREPO95/iris-agent/main/install.sh | sudo bash
```

El instalador pregunta primero **por dónde quieres usar Iris**:

```
1) Telegram        (por defecto)
2) WhatsApp
3) WhatsApp + Telegram
```

- **Telegram**: pide el token del bot y tu ID numérico. Crea el bot con
  [@BotFather](https://t.me/BotFather) (`/newbot`) y obtén tu ID con
  [@userinfobot](https://t.me/userinfobot). Después abre el bot y pulsa
  `/start` para que pueda escribirte.
- **WhatsApp**: pide el número autorizado y hace el pareo con QR.
- **Ambos**: configura los dos; el mismo Iris atiende por los dos lados.

Luego pregunta **cómo conectar el modelo**:

```
1) Ollama — instala y firma (modelos :cloud)
2) Claude — suscripción Pro/Max/Team (token por navegador)
3) Claude — clave de API (console.anthropic.com)
4) Otro endpoint compatible con Anthropic
```

Según la opción:

| Opción | Qué hace |
|---|---|
| 1 | Instala **Ollama**, hace `ollama signin` y muestra la lista de modelos cloud para elegir con un número |
| 2 | Ejecuta `claude setup-token` (abre el navegador) y guarda el token de un año |
| 3 | Pide la clave `sk-ant-api...` (sin mostrarla) |
| 4 | Pide URL base, token y modelo (sirve para la API de Ollama Cloud: `https://ollama.com`) |

Los menús son de terminal, con colores y opciones numeradas: escribes el número y
Enter (Enter solo = opción 1). No cambian el modo del terminal ni necesitan
dependencias extra.

> **Ollama local:** el núcleo ejecuta el equivalente headless de
> `ollama launch claude --model deepseek-v4.1-flash:cloud`, con el daemon local y
> el sufijo `:cloud` (que es el que usa Ollama para los modelos en la nube).

Después crea el usuario **`iris`** y las carpetas (`/opt/iris`,
`/etc/iris/iris.env`, `/var/lib/iris`, `/home/iris/workspace`), configura los
puentes elegidos (`iris-telegram` para Telegram, `iris-bridge` para WhatsApp),
deja activo `iris-core` (e `iris-ollama` solo con la opción 1) y envía los
mensajes de prueba.

Telegram no necesita QR ni sesión: usa la API oficial del bot con long polling
y solo acepta el ID autorizado.

**Desde una copia local:**

```bash
cd Iris-agent
sudo bash install.sh
```

**Sin preguntas** (las claves van por variables de entorno, no por argumentos):

```bash
export IRIS_CHANNELS=whatsapp
export IRIS_PHONE=+573001112233
export IRIS_PROVIDER=ollama-local
export IRIS_MODEL=deepseek-v4.1-flash:cloud
curl -fsSL https://raw.githubusercontent.com/JUANRESTREPO95/iris-agent/main/install.sh \
  | sudo -E bash -s -- --yes
```

O por Telegram:

```bash
export IRIS_CHANNELS=telegram
export IRIS_TELEGRAM_TOKEN=123456:ABC...
export IRIS_TELEGRAM_USER=123456789
export IRIS_PROVIDER=ollama-local
export IRIS_MODEL=deepseek-v4.1-flash:cloud
curl -fsSL https://raw.githubusercontent.com/JUANRESTREPO95/iris-agent/main/install.sh \
  | sudo -E bash -s -- --yes
```

> Si usas `--yes` sin `IRIS_CHANNELS`, el instalador elige **Telegram**.

Opciones del instalador:

| Opción | Qué hace |
|---|---|
| `--channels NAME` | `whatsapp`, `telegram` o `both` |
| `--phone NUM` | número autorizado de WhatsApp (E.164) |
| `--model NAME` | modelo (Ollama o alias de Claude; vacío = predeterminado) |
| `--provider NAME` | `ollama-local`, `claude-subscription`, `claude-api` o `custom` |
| `--yes` | no preguntar; usa las variables de entorno |
| `--skip-signin` | no iniciar sesión en Ollama |
| `--skip-pair` | no emparejar WhatsApp en este momento |
| `--update` | actualizar código y reiniciar (conserva la configuración) |
| `--uninstall [--purge]` | quitar servicios (y borrar datos y usuario con `--purge`) |
| `--help` | ayuda |

Variables para modo no interactivo: `IRIS_CHANNELS`, `IRIS_PROVIDER`,
`IRIS_PHONE`, `IRIS_MODEL`, `IRIS_TELEGRAM_TOKEN`, `IRIS_TELEGRAM_USER`,
`IRIS_OAUTH_TOKEN` (suscripción), `IRIS_ANTHROPIC_KEY` (API de Claude),
`IRIS_CUSTOM_BASE` / `IRIS_CUSTOM_TOKEN` (endpoint propio).

## Uso

Escríbele normal por Telegram o WhatsApp; todo lo que no sea un comando va a
Claude Code.

| Comando | Qué hace |
|---|---|
| `/proyecto [nombre]` | Ver o cambiar de proyecto |
| `/nuevo` | Sesión nueva en el proyecto actual |
| `/modelo [nombre]` | Ver o cambiar el modelo |
| `/estado` | Cola, proyecto, modelo, sesión |
| `/detener` | Detener la tarea en curso |
| `/id` | Ver el ID del chat (útil para configurar el canal) |
| `/comandos` | Listar comandos y skills de Claude Code |
| `/ayuda` | Lista de comandos |

También acepta alias en inglés: `/help`, `/start`, `/project`, `/new`,
`/reset`, `/model`, `/status`, `/stop`, `/cancelar`, `/commands`, `/skills` y
`/recargar`.

Cada proyecto tiene su propia sesión de Claude Code y su carpeta en
`/home/iris/workspace`.

En Telegram el menú de comandos del bot se sincroniza solo con los comandos de
Iris y con los comandos y skills instalados de Claude Code (los alias que
Telegram no admite, como los guiones, se traducen automáticamente).

**Adjuntos:** acepta fotos, documentos, audios y videos. El puente los descarga
en `/var/lib/iris` y Claude Code los recibe como rutas de archivo.

**Comandos personalizados:** cualquier `/algo` que no sea de Iris se envía tal
cual a Claude Code, que expande sus comandos y skills
(`~/.claude/commands/`, `~/.claude/skills/`, y las variantes del proyecto).
Se descubren en cada mensaje, así que no hace falta reiniciar. Ojo: son los
comandos del usuario **`iris`**; los que tengas instalados en tu Claude Code de
Windows no aparecen automáticamente aquí. `/comandos` te muestra cuáles ve Iris.

## Gestión desde el servidor

```bash
sudo iris status
sudo iris logs core          # también: bridge, telegram, ollama, install
sudo iris pair
sudo iris telegram [token] [id]   # añadir o actualizar el bot de Telegram
sudo iris model deepseek-v4.1-flash:cloud
sudo iris restart
sudo iris update
sudo iris doctor
```

**Diagnóstico e historial**

- Cada instalación/actualización deja un log completo en
  `/var/log/iris/install.log` (`sudo iris logs install`). Si algo falla, el
  instalador muestra las últimas 20 líneas y la ruta.
- `/etc/iris/install-state.json` guarda el resultado, la fecha, el modelo, los
  puertos y la versión de Node/Claude/Ollama (`sudo iris doctor` lo muestra).
- Antes de copiar código nuevo se guarda un respaldo en
  `/var/backups/iris/iris-<fecha>.tar.gz` (se conservan los últimos 5; permisos
  0600). Para restaurar uno:

  ```bash
  sudo tar -xzf /var/backups/iris/iris-<fecha>.tar.gz -C /
  sudo iris restart
  ```

## Cómo funciona

- **`iris-telegram`**: habla con la Bot API de Telegram por long polling. Solo
  acepta mensajes del ID configurado y reenvía al núcleo por HTTP local con un
  token.
- **`iris-bridge`**: mantiene la sesión de WhatsApp Web con Baileys. Solo
  acepta mensajes del número configurado. Cuando llega un mensaje, lo reenvía
  al núcleo por HTTP local con un token. Si el núcleo está caído, reintenta.
- **`iris-core`**: guarda el mensaje en SQLite, lo procesa un worker y lanza
  `claude -p --output-format json` dentro de la carpeta del proyecto. La
  respuesta se trocea y se envía de vuelta por el puente correspondiente.
- **Sesiones**: una por proyecto (`--session-id` / `--resume`), así Claude
  recuerda el contexto entre mensajes.
- **Cola persistente**: si el proceso muere, los mensajes pendientes siguen en
  SQLite.

## Seguridad

- El usuario `iris` **no tiene sudo** y solo Claude corre como él.
- Claude trabaja en `/home/iris/workspace`; tus carpetas personales no están
  montadas ni son accesibles con permisos de escritura.
- El canal puente ↔ núcleo exige un token aleatorio de 32 bytes.
- Los servicios (núcleo y puentes) escuchan **solo en 127.0.0.1**.
- Solo se acepta al remitente autorizado: `IRIS_PHONE` en WhatsApp e
  `IRIS_TELEGRAM_USER` en Telegram. Con `*` como ID de Telegram, el bot acepta
  a cualquiera (útil solo para pruebas).
- Los tokens se piden sin mostrarlos en pantalla.

**Importante:** por defecto Iris arranca Claude con
`--dangerously-skip-permissions` (`IRIS_PERMISSION_MODE=bypassPermissions`)
porque en modo headless no hay ventana donde aprobar permisos. Eso significa
que Claude puede ejecutar comandos sin preguntar **con los permisos del usuario
`iris`** (su home y `/home/iris/workspace`; no tiene sudo). Si quieres cambiar
a `acceptEdits`, edita `/etc/iris/iris.env`:

```bash
IRIS_PERMISSION_MODE=acceptEdits
```

y reinicia: `sudo iris restart`.

## Solución de problemas

| Síntoma | Qué revisar |
|---|---|
| El QR no aparece | `sudo iris logs bridge`; el pareo se hace con `sudo iris pair` |
| WhatsApp se desconecta | normal tras reinicios; el puente reconecta solo |
| Telegram no responde | `sudo iris logs telegram`; comprueba `IRIS_TELEGRAM_USER` y pulsa `/start` en el bot |
| Telegram rechazó el token | vuelve a configurarlo con `sudo iris telegram` |
| El bot no escribe primero | Telegram no deja iniciar la conversación al bot: pulsa `/start` |
| "Ollama no arrancó" | `journalctl -u iris-ollama -n 50` |
| Claude no responde | `sudo iris logs core` |
| Modelo equivocado | `sudo iris model <nombre>` |
| WSL sin systemd | añade `systemd=true` en `/etc/wsl.conf` y reinicia WSL |

## Desinstalar

```bash
sudo iris uninstall          # conserva datos
sudo iris uninstall --purge  # borra datos y usuario iris
```

## Notas

- Baileys es un cliente **no oficial** de WhatsApp Web. Meta puede cambiar el
  protocolo o limitar la cuenta; para un producto conviene la API oficial
  (WhatsApp Cloud API). Telegram usa la API oficial de bots.
- El archivo `/etc/iris/iris.env` contiene los tokens de los canales y la
  configuración: no lo subas a ningún repo.
- La sesión de WhatsApp (`/var/lib/iris/whatsapp-session`) equivale a la cuenta.
  Respáldala cifrada y nunca la compartas.
