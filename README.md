# Iris Agent

Claude Code corriendo 24/7 en un servidor Linux, controlable por WhatsApp.

Escribes por WhatsApp → Iris ejecuta Claude Code en un directorio de trabajo →
te responde por el mismo chat.

```
WhatsApp ──► iris-bridge (Node + Baileys) ──HTTP+token──► iris-core (Python)
                                                              │
                                                              ▼
                                                    claude -p (headless)
                                                              │
                                                              ▼
                                                   ~/workspace/<proyecto>
```

## Requisitos

- Linux con **systemd** (Ubuntu/Debian). WSL sirve para pruebas.
- Acceso `sudo`.
- Un número de WhatsApp dedicado (recomendado) para el bot.
- Una forma de acceso al modelo:
  - suscripción de Claude (Pro/Max/Team),
  - clave de API de Anthropic,
  - clave de API de Ollama Cloud (sin instalar Ollama),
  - Ollama local con cuenta de Ollama Cloud,
  - otro endpoint compatible con la API de Anthropic.

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
1) WhatsApp
2) Telegram
3) WhatsApp + Telegram
```

- **WhatsApp**: pide el número autorizado y hace el pareo con QR.
- **Telegram**: pide el token del bot y tu ID numérico. Crea el bot con
  [@BotFather](https://t.me/BotFather) (`/newbot`) y obtén tu ID con
  [@userinfobot](https://t.me/userinfobot). Después abre el bot y pulsa
  `/start` para que pueda escribirte.
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
| 4 | Pide URL base, token y modelo |

Los menús son de terminal, con colores y opciones numeradas: escribes el número y
Enter (Enter solo = opción 1). No cambian el modo del terminal ni necesitan
dependencias extra.

> **Ollama:** el núcleo ejecuta el equivalente headless de
> `ollama launch claude --model deepseek-v4.1-flash:cloud`, con el daemon local y
> el sufijo `:cloud` (que es el que usa Ollama para los modelos en la nube).

Después crea el usuario **`iris`** y las carpetas (`/opt/iris`,
`/etc/iris/iris.env`, `/var/lib/iris`, `/home/iris/workspace`), configura los
puentes elegidos (`iris-bridge` para WhatsApp, `iris-telegram` para Telegram),
deja activo `iris-core` (y `iris-ollama` solo con la opción 4) y envía los
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
export IRIS_PROVIDER=ollama-local
export IRIS_PHONE=+573001112233
export IRIS_MODEL=deepseek-v4.1-flash:cloud
curl -fsSL https://raw.githubusercontent.com/JUANRESTREPO95/iris-agent/main/install.sh \
  | sudo -E bash -s -- --yes
```

Valores de `IRIS_PROVIDER`: `ollama-local`, `claude-subscription`,
`claude-api`, `custom`. Para la suscripción se puede pasar
`IRIS_OAUTH_TOKEN`; para Claude API, `IRIS_ANTHROPIC_KEY`; para un endpoint
propio, `IRIS_CUSTOM_BASE` e `IRIS_CUSTOM_TOKEN`.

## Uso por WhatsApp

Escríbele normal; todo lo que no sea un comando va a Claude Code.

| Comando | Qué hace |
|---|---|
| `/proyecto [nombre]` | Ver o cambiar de proyecto |
| `/nuevo` | Sesión nueva en el proyecto actual |
| `/modelo [nombre]` | Ver o cambiar el modelo |
| `/estado` | Cola, proyecto, modelo, sesión |
| `/detener` | Detener la tarea en curso |
| `/comandos` | Listar comandos y skills de Claude Code |
| `/ayuda` | Lista de comandos |

Cada proyecto tiene su propia sesión de Claude Code y su carpeta en
`/home/iris/workspace`.

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

- **`iris-bridge`**: mantiene la sesión de WhatsApp Web con Baileys. Solo
  acepta mensajes del número configurado. Cuando llega un mensaje, lo reenvía
  al núcleo por HTTP local con un token. Si el núcleo está caído, reintenta.
- **`iris-core`**: guarda el mensaje en SQLite, lo procesa un worker y lanza
  `claude -p --output-format json` dentro de la carpeta del proyecto. La
  respuesta se trocea y se envía de vuelta por el puente.
- **Sesiones**: una por proyecto (`--session-id` / `--resume`), así Claude
  recuerda el contexto entre mensajes.
- **Cola persistente**: si el proceso muere, los mensajes pendientes siguen en
  SQLite.

## Seguridad

- El usuario `iris` **no tiene sudo** y solo Claude corre como él.
- Claude trabaja en `/home/iris/workspace`; tus carpetas personales no están
  montadas ni son accesibles con permisos de escritura.
- El canal bridge ↔ core exige un token aleatorio de 32 bytes.
- Ambos servicios escuchan **solo en 127.0.0.1**.
- El número autorizado está en `IRIS_PHONE`; cualquier otro remitente se
  descarta.

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
  (WhatsApp Cloud API).
- El archivo `/etc/iris/iris.env` contiene el token del canal y la
  configuración: no lo subas a ningún repo.
- La sesión de WhatsApp (`/var/lib/iris/whatsapp-session`) equivale a la cuenta.
  Respáldala cifrada y nunca la compartas.
