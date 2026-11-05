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
- Cuenta de Ollama Cloud (o cambiar `ANTHROPIC_BASE_URL` por otra API compatible).

## Instalación

**Desde GitHub (una línea):**

```bash
curl -fsSL https://raw.githubusercontent.com/JUANRESTREPO95/iris-agent/main/install.sh | sudo bash
```

Con parámetros (sin preguntas):

```bash
curl -fsSL https://raw.githubusercontent.com/JUANRESTREPO95/iris-agent/main/install.sh \
  | sudo bash -s -- --phone +573001112233 --model deepseek-v4.1-flash:cloud
```

**Desde una copia local:**

```bash
cd Iris-agent
sudo bash install.sh
```

El instalador pregunta el número autorizado y el modelo (sugiere
`deepseek-v4.1-flash:cloud`), y hace todo:

1. Instala Node.js 22, Claude Code y Ollama.
2. Crea el usuario **`iris`** (sin sudo) y las carpetas:
   - `/opt/iris` — código (solo lectura para `iris`)
   - `/etc/iris/iris.env` — configuración y token
   - `/var/lib/iris` — cola SQLite, sesión de WhatsApp, adjuntos
   - `/home/iris/workspace` — proyectos que Claude puede tocar
3. Inicia sesión en Ollama Cloud (`ollama signin`, una sola vez).
4. Empareja WhatsApp mostrando el QR en la terminal.
5. Deja activos los servicios `iris-ollama`, `iris-bridge` e `iris-core`.
6. Envía un mensaje de prueba a tu WhatsApp.

Sin preguntas:

```bash
sudo bash install.sh --phone +573001112233 --model deepseek-v4.1-flash:cloud --yes
```

## Uso por WhatsApp

Escríbele normal; todo lo que no sea un comando va a Claude Code.

| Comando | Qué hace |
|---|---|
| `/proyecto [nombre]` | Ver o cambiar de proyecto |
| `/nuevo` | Sesión nueva en el proyecto actual |
| `/modelo [nombre]` | Ver o cambiar el modelo |
| `/estado` | Cola, proyecto, modelo, sesión |
| `/detener` | Detener la tarea en curso |
| `/ayuda` | Lista de comandos |

Cada proyecto tiene su propia sesión de Claude Code y su carpeta en
`/home/iris/workspace`.

## Gestión desde el servidor

```bash
sudo iris status
sudo iris logs core          # también: bridge, ollama, install
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
