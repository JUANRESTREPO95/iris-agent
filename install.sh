#!/usr/bin/env bash
# Instalador de Iris Agent (Linux: Ubuntu/Debian, incluido WSL)
#
#   sudo ./install.sh
#   sudo ./install.sh --phone +573001112233 --model deepseek-v4.1-flash:cloud
#   sudo ./install.sh --update
#   sudo ./install.sh --uninstall
#
# Instala: Node 22, Claude Code, Ollama (usuario iris), iris-core, iris-bridge.

set -Eeuo pipefail

APP_DIR=/opt/iris
CONF_DIR=/etc/iris
ENV_FILE=$CONF_DIR/iris.env
DATA_DIR=/var/lib/iris
IRIS_USER=iris
IRIS_HOME=/home/iris
DEFAULT_MODEL=deepseek-v4.1-flash:cloud
REPO_URL_DEFAULT=https://github.com/JUANRESTREPO95/iris-agent
REPO_REF_DEFAULT=main
INSTALL_VERSION=0.1.0
LOG_DIR=/var/log/iris
INSTALL_LOG=$LOG_DIR/install.log
BACKUP_DIR=/var/backups/iris
STATE_FILE=$CONF_DIR/install-state.json

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"
SOURCE_TMP=""
ORIG_ARGS=("$@")

MODE=install
ASSUME_YES=0
SKIP_SIGNIN=0
SKIP_PAIR=0
PURGE=0
PHONE="${IRIS_PHONE:-}"
MODEL="${IRIS_MODEL:-}"
PROVIDER="${IRIS_PROVIDER:-}"
ANTHROPIC_KEY="${IRIS_ANTHROPIC_KEY:-}"
OLLAMA_KEY="${IRIS_OLLAMA_KEY:-}"
OAUTH_TOKEN="${IRIS_OAUTH_TOKEN:-}"
CUSTOM_BASE="${IRIS_CUSTOM_BASE:-}"
CUSTOM_TOKEN="${IRIS_CUSTOM_TOKEN:-}"

c_reset=$'\033[0m'; c_cyan=$'\033[1;36m'; c_green=$'\033[1;32m'
c_yellow=$'\033[1;33m'; c_red=$'\033[1;31m'; c_dim=$'\033[2m'

log_to_file() {
  [[ -n ${INSTALL_LOG:-} ]] || return 0
  { printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$INSTALL_LOG"; } 2>/dev/null || true
}

log()  { printf '%s[iris]%s %s\n' "$c_cyan" "$c_reset" "$*"; log_to_file "[iris] $*"; }
ok()   { printf '%s[ ok ]%s %s\n' "$c_green" "$c_reset" "$*"; log_to_file "[ ok ] $*"; }
warn() { printf '%s[aviso]%s %s\n' "$c_yellow" "$c_reset" "$*"; log_to_file "[aviso] $*"; }
die()  { printf '%s[error]%s %s\n' "$c_red" "$c_reset" "$*" >&2; log_to_file "[error] $*"; exit 1; }
dim()  { printf '%s%s%s\n' "$c_dim" "$*" "$c_reset"; }

# Ejecuta un comando, guarda toda su salida en el log y, si falla, muestra
# las últimas líneas y la ruta del log completo.
run_logged() {
  local label="$1"; shift
  log "$label"
  local rc=0
  if [[ -n ${INSTALL_LOG:-} ]]; then
    printf '\n==> %s (%s)\n' "$label" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >>"$INSTALL_LOG" 2>/dev/null || true
    "$@" >>"$INSTALL_LOG" 2>&1 || rc=$?
  else
    "$@" || rc=$?
  fi
  if (( rc != 0 )); then
    printf '%s[error]%s %s falló (código %s)\n' "$c_red" "$c_reset" "$label" "$rc" >&2
    if [[ -n ${INSTALL_LOG:-} ]]; then
      tail -n 20 "$INSTALL_LOG" | sed 's/^/    /' >&2
      printf '    log completo: %s\n' "$INSTALL_LOG" >&2
    fi
    log_to_file "[error] $label falló (código $rc)"
    return "$rc"
  fi
  return 0
}

json_escape() {
  printf '%s' "${1:-}" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' | tr -d '\n\r'
}

# Recibo de la instalación (sin secretos) para diagnóstico y `iris doctor`.
write_state() {
  local outcome="$1" detail="${2:-}"
  [[ ${MODE:-install} == uninstall ]] && return 0
  mkdir -p "$(dirname "$STATE_FILE")" 2>/dev/null || return 0
  local tmp="${STATE_FILE}.tmp.$$"
  {
    printf '{\n'
    printf '  "version": "%s",\n' "$(json_escape "$INSTALL_VERSION")"
    printf '  "outcome": "%s",\n' "$(json_escape "$outcome")"
    printf '  "detail": "%s",\n' "$(json_escape "$detail")"
    printf '  "mode": "%s",\n' "$(json_escape "${MODE:-install}")"
    printf '  "provider": "%s",\n' "$(json_escape "${PROVIDER:-}")"
    printf '  "model": "%s",\n' "$(json_escape "${MODEL:-}")"
    printf '  "phone_set": %s,\n' "$([[ -n ${PHONE:-} ]] && echo true || echo false)"
    printf '  "ports": {"bridge": %s, "core": %s},\n' "${BRIDGE_PORT_VALUE:-0}" "${CORE_PORT_VALUE:-0}"
    printf '  "node": "%s",\n' "$(json_escape "$(node -v 2>/dev/null || true)")"
    printf '  "claude": "%s",\n' "$(json_escape "$(claude --version 2>/dev/null | head -n1 || true)")"
    printf '  "ollama": "%s",\n' "$(json_escape "$(ollama --version 2>/dev/null | head -n1 || true)")"
    printf '  "source": "%s",\n' "$(json_escape "${SOURCE_DIR_TO_SAVE:-${SRC_DIR:-}}")"
    printf '  "updated_at": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '  "log": "%s"\n' "$(json_escape "${INSTALL_LOG:-}")"
    printf '}\n'
  } >"$tmp" 2>/dev/null && mv -f "$tmp" "$STATE_FILE" 2>/dev/null && chmod 0644 "$STATE_FILE" 2>/dev/null \
    || rm -f "$tmp" 2>/dev/null
}

# Si el instalador llegó solo (curl | bash), descarga el resto del proyecto.
ensure_sources() {
  if [[ -f $SRC_DIR/bridge/server.js && -d $SRC_DIR/core/iris_core ]]; then
    return 0
  fi

  local repo="${IRIS_REPO_URL:-$REPO_URL_DEFAULT}"
  local ref="${IRIS_REPO_REF:-$REPO_REF_DEFAULT}"
  local url="${IRIS_SOURCE_TARBALL:-${repo%/}/archive/refs/heads/${ref}.tar.gz}"

  SOURCE_TMP="$(mktemp -d /tmp/iris-src.XXXXXX)" || die "No pude crear el directorio temporal."
  log "Descargando Iris desde $repo ($ref)…"
  run_logged "Descargando el código fuente" \
    curl -fsSL "$url" -o "$SOURCE_TMP/src.tar.gz" \
    || die "No pude descargar el código fuente (ver $INSTALL_LOG)."
  run_logged "Extrayendo el código fuente" \
    tar -xzf "$SOURCE_TMP/src.tar.gz" -C "$SOURCE_TMP" --strip-components=1 \
    || die "No pude extraer el código fuente (ver $INSTALL_LOG)."
  rm -f "$SOURCE_TMP/src.tar.gz"

  [[ -f $SOURCE_TMP/bridge/server.js && -d $SOURCE_TMP/core/iris_core ]] \
    || die "El paquete descargado no tiene el formato esperado."
  SRC_DIR="$SOURCE_TMP"
}

cleanup_source_tmp() {
  [[ -n ${SOURCE_TMP:-} ]] && rm -rf "$SOURCE_TMP" 2>/dev/null || true
}

# Respaldo del código y la configuración antes de sobrescribirlos.
backup_existing() {
  local paths=() rel_app="${APP_DIR#/}" rel_env="${ENV_FILE#/}"
  if [[ -d $APP_DIR/bridge ]]; then paths+=("$rel_app/bridge"); fi
  if [[ -d $APP_DIR/core ]]; then paths+=("$rel_app/core"); fi
  if [[ -d $APP_DIR/systemd ]]; then paths+=("$rel_app/systemd"); fi
  if [[ -d $APP_DIR/bin ]]; then paths+=("$rel_app/bin"); fi
  if [[ -f $APP_DIR/install.sh ]]; then paths+=("$rel_app/install.sh"); fi
  if [[ -f $ENV_FILE ]]; then paths+=("$rel_env"); fi
  (( ${#paths[@]} > 0 )) || return 0

  mkdir -p "$BACKUP_DIR" 2>/dev/null || { warn "No pude crear $BACKUP_DIR; sigo sin respaldo."; return 0; }
  chmod 0700 "$BACKUP_DIR" 2>/dev/null || true
  local stamp out
  stamp="$(date -u +%Y%m%d-%H%M%S)"
  out="$BACKUP_DIR/iris-$stamp.tar.gz"

  if tar -czf "$out" -C / \
      --exclude="$rel_app/core/venv" \
      --exclude="$rel_app/bridge/node_modules" \
      "${paths[@]}" 2>>"${INSTALL_LOG:-/dev/null}"; then
    chmod 0600 "$out" 2>/dev/null || true
    log "Respaldo guardado en $out"
    ls -1t "$BACKUP_DIR"/iris-*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f 2>/dev/null || true
  else
    warn "No pude crear el respaldo; continúo sin él."
    rm -f "$out" 2>/dev/null || true
  fi
}

fetch_cloud_models() {
  local html slugs
  html="$(curl -fsSL --max-time 20 'https://ollama.com/search?c=cloud' 2>/dev/null || true)"
  [[ -n $html ]] || return 1
  slugs="$(printf '%s' "$html" \
    | grep -oE 'href="/library/[^"]+"' \
    | sed -E 's|href="/library/||; s|"$||' \
    | sort -u)"
  [[ -n $slugs ]] || return 1
  printf '%s\n' "$slugs"
}

normalize_ollama_model() {
  local provider="$1"
  if [[ $provider == ollama-cloud ]]; then
    # La API directa no usa el sufijo :cloud (solo el Ollama local).
    MODEL="${MODEL%:cloud}"
  else
    # Ollama local usa :cloud para enrutar el modelo a la nube.
    [[ $MODEL == *:cloud ]] || MODEL="${MODEL}:cloud"
  fi
  [[ -n $MODEL ]] || die "El modelo no puede quedar vacío."
}

validate_ollama_key() {
  local key="$1" model="${2:-deepseek-v4.1-flash}" code
  code="$(curl -s -o /tmp/iris-key-test.json -w '%{http_code}' --max-time 60 \
    https://ollama.com/api/chat \
    -H "Authorization: Bearer $key" \
    -H 'content-type: application/json' \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"di OK\"}],\"stream\":false}" \
    2>/dev/null || echo 000)"
  case "$code" in
    401) return 2 ;;
    000) return 1 ;;
    *) return 0 ;;
  esac
}

confirm() {
  local answer=""
  [[ $ASSUME_YES -eq 1 ]] && return 0
  read -rp "$1 [s/N]: " answer </dev/tty || true
  [[ "${answer,,}" == "s" || "${answer,,}" == "si" || "${answer,,}" == "sí" || "${answer,,}" == "y" ]]
}

ask() {
  local __var=$1 __prompt=$2 __default=${3:-} __answer=""
  if [[ $ASSUME_YES -eq 1 ]]; then
    printf -v "$__var" '%s' "$__default"
    return
  fi
  if [[ -n $__default ]]; then
    read -rp "$__prompt [$__default]: " __answer </dev/tty || true
    printf -v "$__var" '%s' "${__answer:-$__default}"
  else
    read -rp "$__prompt: " __answer </dev/tty || true
    printf -v "$__var" '%s' "$__answer"
  fi
}

usage() {
  cat <<'EOF'
Uso: sudo ./install.sh [opciones]

  --phone NUM        número de WhatsApp autorizado (E.164)
  --model NAME       modelo (Ollama o alias de Claude; vacío = predeterminado)
  --provider NAME    ollama-local | claude-subscription | claude-api | custom
  --yes              no preguntar (usa las variables de entorno de abajo)
  --skip-signin      no iniciar sesión en Ollama
  --skip-pair        no emparejar WhatsApp ahora
  --update           actualizar código y reiniciar (conserva la configuración)
  --uninstall        detener y quitar los servicios
  --purge            con --uninstall: borra también datos y usuario iris
  --help             esta ayuda

Variables para modo no interactivo:
  IRIS_PROVIDER, IRIS_PHONE, IRIS_MODEL,
  IRIS_OAUTH_TOKEN (suscripción), IRIS_ANTHROPIC_KEY (API de Claude),
  IRIS_CUSTOM_BASE / IRIS_CUSTOM_TOKEN
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --phone) PHONE="${2:-}"; shift 2 ;;
    --model) MODEL="${2:-}"; shift 2 ;;
    --provider) PROVIDER="${2:-}"; shift 2 ;;
    --yes|-y) ASSUME_YES=1; shift ;;
    --skip-signin) SKIP_SIGNIN=1; shift ;;
    --skip-pair) SKIP_PAIR=1; shift ;;
    --update) MODE=update; shift ;;
    --uninstall) MODE=uninstall; shift ;;
    --purge) PURGE=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "Opción desconocida: $1 (usa --help)" ;;
  esac
done

# Para pruebas automatizadas: cargar solo las funciones y salir.
if [[ ${IRIS_INSTALL_LIB_ONLY:-0} == 1 ]]; then
  return 0 2>/dev/null || exit 0
fi

# ── Root ────────────────────────────────────────────────────────────────────

if [[ $EUID -ne 0 ]]; then
  command -v sudo >/dev/null || die "Necesito root y no hay sudo."
  # Con `curl | bash` el script llega por stdin y no se puede relanzar con sudo.
  if [[ ! -t 0 ]]; then
    die "Necesito permisos de root. Ejecuta de nuevo así:
  curl -fsSL https://raw.githubusercontent.com/JUANRESTREPO95/iris-agent/main/install.sh | sudo bash"
  fi
  exec sudo -E bash "$0" "${ORIG_ARGS[@]}"
fi

trap 'write_state failed "línea $LINENO"; die "Falló la línea $LINENO. Revisa el mensaje anterior."' ERR
trap cleanup_source_tmp EXIT

# ── Sistema ────────────────────────────────────────────────────────────────

[[ -f /etc/os-release ]] || die "No puedo identificar la distribución."
# shellcheck disable=SC1091
. /etc/os-release
case "${ID:-} ${ID_LIKE:-}" in
  *debian*|*ubuntu*) : ;;
  *) die "Por ahora solo soporto Debian/Ubuntu (detectado: ${PRETTY_NAME:-?})." ;;
esac

IS_WSL=0
if grep -qi microsoft /proc/version 2>/dev/null; then
  IS_WSL=1
  warn "Estás en WSL. Sirve para probar, pero WSL se apaga con Windows: no es un 24/7 real."
fi

if [[ "$(cat /proc/1/comm 2>/dev/null)" != "systemd" ]]; then
  die "systemd no está activo (PID 1 = $(cat /proc/1/comm 2>/dev/null || echo ?)).
En WSL habilítalo con:
  echo -e '[boot]\nsystemd=true' | sudo tee /etc/wsl.conf
y reinicia WSL:  wsl --shutdown"
fi

# ── Log y respaldos ─────────────────────────────────────────────────────────

mkdir -p "$CONF_DIR" "$LOG_DIR" 2>/dev/null || true
chmod 0700 "$LOG_DIR" 2>/dev/null || true
mkdir -p "$BACKUP_DIR" 2>/dev/null && chmod 0700 "$BACKUP_DIR" 2>/dev/null || true
: >>"$INSTALL_LOG" 2>/dev/null || INSTALL_LOG=""
if [[ -n ${INSTALL_LOG:-} ]]; then
  printf '\n===== Iris %s — %s (modo %s) =====\n' \
    "$INSTALL_VERSION" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$MODE" >>"$INSTALL_LOG" 2>/dev/null || true
fi

# ── Desinstalar ────────────────────────────────────────────────────────────

if [[ $MODE == uninstall ]]; then
  log "Deteniendo servicios…"
  systemctl disable --now iris-core.service iris-bridge.service iris-ollama.service 2>/dev/null || true
  rm -f /etc/systemd/system/iris-core.service \
        /etc/systemd/system/iris-bridge.service \
        /etc/systemd/system/iris-ollama.service
  systemctl daemon-reload
  rm -f /usr/local/bin/iris "$STATE_FILE"
  ok "Servicios eliminados."
  if [[ $PURGE -eq 1 ]] || { [[ $ASSUME_YES -eq 0 ]] && confirm "¿Borrar también /opt/iris, /var/lib/iris, /etc/iris y el usuario iris?"; }; then
    rm -rf "$APP_DIR" "$DATA_DIR" "$CONF_DIR"
    if id -u "$IRIS_USER" >/dev/null 2>&1; then
      userdel -r "$IRIS_USER" 2>/dev/null || userdel "$IRIS_USER" 2>/dev/null || true
    fi
    ok "Datos y usuario eliminados."
  else
    dim "Se conservaron /opt/iris, /var/lib/iris y /etc/iris."
  fi
  exit 0
fi

# ── Preguntas ──────────────────────────────────────────────────────────────

read_secret() {
  local __var=$1 __prompt=$2 __value=""
  read -rsp "$__prompt: " __value </dev/tty || true
  echo
  printf -v "$__var" '%s' "$__value"
}

provider_label() {
  case "$1" in
    claude-subscription) echo "Claude (suscripción Pro/Max/Team)" ;;
    claude-api) echo "Claude (clave de API)" ;;
    ollama-cloud) echo "Ollama Cloud (API directa, sin Ollama local)" ;;
    ollama-local) echo "Ollama Cloud (Ollama local)" ;;
    custom) echo "Otro endpoint compatible con Anthropic" ;;
    *) echo "$1" ;;
  esac
}

ensure_whiptail() {
  command -v whiptail >/dev/null 2>&1 && return 0
  [[ $ASSUME_YES -eq 0 ]] || return 1
  dim "  Preparando el menú interactivo…"
  export DEBIAN_FRONTEND=noninteractive
  if ! apt-get install -y -qq whiptail >/dev/null 2>&1; then
    apt-get update -qq >/dev/null 2>&1 || true
    apt-get install -y -qq whiptail >/dev/null 2>&1 || true
  fi
  command -v whiptail >/dev/null 2>&1
}

# Menú con flechas (whiptail). Recibe título, pregunta y pares etiqueta/descripción.
# Si whiptail no está disponible, cae a una lista numerada.
ui_menu() {
  local title="$1" prompt="$2"; shift 2
  if command -v whiptail >/dev/null 2>&1; then
    local result
    result="$(whiptail --title "$title" --menu "$prompt" 22 82 12 \
      "$@" 3>&1 1>&2 2>&3 </dev/tty)" || return 1
    printf '%s' "$result"
    return 0
  fi

  local -a tags=()
  local i=1 tag desc
  printf '\n%s\n%s\n' "$title" "$prompt" >&2
  while [[ $# -ge 2 ]]; do
    tags+=("$1")
    printf '  %2d) %s — %s\n' "$i" "$1" "$2" >&2
    i=$((i + 1))
    shift 2
  done
  local choice
  read -rp "Número: " choice </dev/tty || return 1
  [[ $choice =~ ^[0-9]+$ ]] || return 1
  (( choice >= 1 && choice <= ${#tags[@]} )) || return 1
  printf '%s' "${tags[$((choice - 1))]}"
}

ui_input() {
  local __var=$1 __prompt=$2 __default=${3:-} __value=""
  if command -v whiptail >/dev/null 2>&1 && [[ $ASSUME_YES -eq 0 ]]; then
    __value="$(whiptail --title "Iris — instalación" --inputbox "$__prompt" 10 76 "$__default" \
      3>&1 1>&2 2>&3 </dev/tty)" || return 1
  else
    ask __value "$__prompt" "$__default"
  fi
  printf -v "$__var" '%s' "$__value"
}

ui_password() {
  local __var=$1 __prompt=$2 __value=""
  if command -v whiptail >/dev/null 2>&1 && [[ $ASSUME_YES -eq 0 ]]; then
    __value="$(whiptail --title "Iris — credenciales" --passwordbox "$__prompt" 10 76 \
      3>&1 1>&2 2>&3 </dev/tty)" || return 1
  else
    read_secret __value "$__prompt"
  fi
  printf -v "$__var" '%s' "$__value"
}

ui_yesno() {
  local prompt="$1"
  if command -v whiptail >/dev/null 2>&1 && [[ $ASSUME_YES -eq 0 ]]; then
    whiptail --title "Iris — confirmar" --yesno "$prompt" 16 76 </dev/tty
  else
    confirm "$prompt"
  fi
}

ui_msgbox() {
  local text="$1"
  if command -v whiptail >/dev/null 2>&1 && [[ $ASSUME_YES -eq 0 ]]; then
    whiptail --title "Iris" --msgbox "$text" 14 76 </dev/tty || true
  else
    log "$text"
  fi
}

select_claude_model() {
  [[ -n $MODEL ]] && return 0
  local choice
  choice="$(ui_menu "Iris — modelo de Claude" "¿Qué modelo quieres usar?" \
    "__default__" "Predeterminado de Claude Code" \
    "sonnet" "Sonnet (equilibrado)" \
    "opus" "Opus (máxima capacidad)" \
    "haiku" "Haiku (rápido)" \
    "__other__" "Escribir otro alias o nombre")" \
    || die "Selección cancelada."
  if [[ $choice == __default__ ]]; then
    MODEL=""
  elif [[ $choice == __other__ ]]; then
    ask MODEL "Modelo (alias o nombre completo)" ""
  else
    MODEL="$choice"
  fi
}

select_ollama_model() {
  local provider="$1"
  [[ -n $MODEL ]] && { normalize_ollama_model "$provider"; return 0; }

  local -a models=()
  local m

  # Con clave válida, /api/tags da los nombres exactos de la API.
  if [[ $provider == ollama-cloud && -n ${OLLAMA_KEY:-} ]]; then
    while IFS= read -r m; do
      [[ -n $m ]] && models+=("$m")
    done < <(curl -fsSL --max-time 20 https://ollama.com/api/tags \
      -H "Authorization: Bearer $OLLAMA_KEY" 2>/dev/null \
      | python3 -c 'import json,sys
try:
    for x in json.load(sys.stdin).get("models", []):
        if x.get("name"): print(x["name"])
except Exception:
    pass' 2>/dev/null || true)
  fi

  # Si no hubo lista (o es Ollama local), usar el catálogo cloud de la web.
  if (( ${#models[@]} == 0 )); then
    while IFS= read -r m; do
      [[ -n $m ]] && models+=("$m")
    done < <(fetch_cloud_models || true)
  fi

  # Dejar deepseek-v4.1-flash primero (será la opción preseleccionada).
  local -a ordered=()
  for m in "${models[@]}"; do
    if [[ $m == "deepseek-v4.1-flash" ]]; then ordered+=("$m"); fi
  done
  for m in "${models[@]}"; do
    if [[ $m != "deepseek-v4.1-flash" ]]; then ordered+=("$m"); fi
  done
  if (( ${#ordered[@]} > 0 )); then models=("${ordered[@]}"); fi

  local -a menu=()
  for m in "${models[@]}"; do
    menu+=("$m" "$m")
  done
  menu+=("__other__" "Escribir otro modelo")

  local choice
  choice="$(ui_menu "Iris — modelo de Ollama" "Elige el modelo cloud" "${menu[@]}")" \
    || die "Selección cancelada."

  if [[ $choice == __other__ ]]; then
    ask MODEL "Modelo" "${models[0]:-deepseek-v4.1-flash}"
  else
    MODEL="$choice"
  fi
  normalize_ollama_model "$provider"
}

choose_provider() {
  if [[ -n $PROVIDER ]]; then
    case $PROVIDER in
      claude-subscription|claude-api|ollama-local|custom) return 0 ;;
      *) die "Proveedor no válido: $PROVIDER" ;;
    esac
  fi
  if [[ $ASSUME_YES -eq 1 ]]; then
    PROVIDER=ollama-local
    return 0
  fi

  ensure_whiptail || true
  local choice
  choice="$(ui_menu "Iris — proveedor del modelo" "¿Cómo quieres conectar el modelo?" \
    "ollama-local" "Ollama — instala y firma (modelos :cloud)" \
    "claude-subscription" "Claude — suscripción Pro/Max/Team" \
    "claude-api" "Claude — clave de API (console.anthropic.com)" \
    "custom" "Otro endpoint compatible con Anthropic")" \
    || die "Selección cancelada."
  PROVIDER="$choice"
  echo
  ok "Proveedor: $(provider_label "$PROVIDER")"
}

collect_provider_config() {
  case $PROVIDER in
    claude-subscription)
      select_claude_model
      ;;
    claude-api)
      if [[ -z $ANTHROPIC_KEY ]]; then
        ui_password ANTHROPIC_KEY "Clave API de Anthropic (sk-ant-api...)" \
          || die "Instalación cancelada."
      fi
      [[ -n $ANTHROPIC_KEY ]] || die "Necesito la clave de Anthropic."
      select_claude_model
      ;;
    ollama-local)
      select_ollama_model ollama-local
      ;;
    custom)
      if [[ -z $CUSTOM_BASE ]]; then
        ui_input CUSTOM_BASE "URL base compatible con Anthropic" "https://" \
          || die "Instalación cancelada."
      fi
      [[ $CUSTOM_BASE =~ ^https?:// ]] || die "URL no válida."
      if [[ -z $CUSTOM_TOKEN ]]; then
        ui_password CUSTOM_TOKEN "Token del endpoint" || die "Instalación cancelada."
      fi
      [[ -n $CUSTOM_TOKEN ]] || die "Necesito el token del endpoint."
      if [[ -z $MODEL ]]; then
        ui_input MODEL "Modelo" "" || die "Instalación cancelada."
      fi
      [[ -n $MODEL ]] || die "El modelo no puede quedar vacío."
      ;;
    *) die "Proveedor no válido: $PROVIDER" ;;
  esac
}

infer_provider_from_env() {
  if [[ -n ${IRIS_PROVIDER:-} ]]; then printf '%s' "$IRIS_PROVIDER"; return; fi
  if [[ -n ${CLAUDE_CODE_OAUTH_TOKEN:-} ]]; then printf 'claude-subscription'; return; fi
  case "${ANTHROPIC_BASE_URL:-}" in
    https://ollama.com*) printf 'ollama-cloud'; return ;;
    http://127.0.0.1:11434*|http://localhost:11434*) printf 'ollama-local'; return ;;
    "") : ;;
    *) printf 'custom'; return ;;
  esac
  if [[ -n ${ANTHROPIC_API_KEY:-} ]]; then printf 'claude-api'; return; fi
  printf 'ollama-local'
}

if [[ $MODE == update ]]; then
  [[ -f $ENV_FILE ]] || die "No hay instalación previa ($ENV_FILE no existe)."
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  PHONE="${IRIS_PHONE:-$PHONE}"
  MODEL="${IRIS_MODEL:-$MODEL}"
  if [[ -z $PROVIDER ]]; then
    PROVIDER="$(infer_provider_from_env)"
  fi
  SKIP_SIGNIN=1
  SKIP_PAIR=1
  log "Modo actualización: se conserva la configuración."
else
  ensure_whiptail || true

  if [[ -z $PHONE ]]; then
    ui_input PHONE "Número de WhatsApp autorizado (con indicativo, ej. +573001112233)" "" \
      || die "Instalación cancelada."
    PHONE="${PHONE//[[:space:]]/}"
    [[ $PHONE =~ ^\+?[0-9]{8,15}$ ]] || die "Número no válido: $PHONE"
  fi

  choose_provider
  collect_provider_config

  echo
  log "Resumen de la instalación:"
  dim "  Número:    $PHONE"
  dim "  Proveedor: $(provider_label "$PROVIDER")"
  dim "  Modelo:    ${MODEL:-(predeterminado del proveedor)}"
  dim "  Usuario:   $IRIS_USER"
  dim "  Código:    $APP_DIR"
  dim "  Datos:     $DATA_DIR"
  echo

  ui_yesno "Resumen:

  Número:    $PHONE
  Proveedor: $(provider_label "$PROVIDER")
  Modelo:    ${MODEL:-(predeterminado del proveedor)}

¿Continuar con la instalación?" || { log "Cancelado."; exit 0; }
fi

# ── Dependencias del sistema ────────────────────────────────────────────────

write_state in_progress ""
export DEBIAN_FRONTEND=noninteractive
run_logged "Actualizando índices de paquetes" apt-get update -qq \
  || die "No pude actualizar apt (ver $INSTALL_LOG)."
run_logged "Instalando paquetes base" apt-get install -y -qq --no-install-recommends \
  curl ca-certificates git ffmpeg python3 python3-venv python3-pip openssl xz-utils zstd iproute2 \
  || die "No pude instalar los paquetes base (ver $INSTALL_LOG)."
ok "Paquetes base listos."

# ── Código fuente ───────────────────────────────────────────────────────────

ensure_sources

# ── Node.js 22 ──────────────────────────────────────────────────────────────

node_major() { node -v 2>/dev/null | sed -E 's/^v([0-9]+).*/\1/' || true; }
NODE_MAJOR="$(node_major || true)"
if [[ -z $NODE_MAJOR || $NODE_MAJOR -lt 20 ]]; then
  case "$(uname -m)" in
    x86_64|amd64) NODE_ARCH=x64 ;;
    aarch64|arm64) NODE_ARCH=arm64 ;;
    *) die "Arquitectura no soportada: $(uname -m)" ;;
  esac
  NODE_BASE=https://nodejs.org/dist/latest-v22.x
  NODE_SUMS="$(mktemp)"
  run_logged "Consultando la versión de Node.js" \
    curl -fsSL "$NODE_BASE/SHASUMS256.txt" -o "$NODE_SUMS" \
    || die "No pude consultar la versión de Node.js (ver $INSTALL_LOG)."

  NODE_FILE="$(awk -v suffix="linux-${NODE_ARCH}.tar.xz" '$2 ~ suffix"$" {print $2; exit}' "$NODE_SUMS")"
  [[ -n $NODE_FILE ]] || die "No pude resolver el archivo de Node.js."

  run_logged "Descargando Node.js" \
    curl -fsSL "$NODE_BASE/$NODE_FILE" -o "/tmp/$NODE_FILE" \
    || die "No pude descargar Node.js (ver $INSTALL_LOG)."

  NODE_EXPECTED="$(awk -v f="$NODE_FILE" '$2 == f {print $1; exit}' "$NODE_SUMS")"
  NODE_ACTUAL="$(sha256sum "/tmp/$NODE_FILE" | awk '{print $1}')"
  if [[ -z $NODE_EXPECTED || $NODE_EXPECTED != "$NODE_ACTUAL" ]]; then
    rm -f "/tmp/$NODE_FILE" "$NODE_SUMS"
    die "La suma SHA256 de Node.js no coincide (esperado: ${NODE_EXPECTED:-?}, obtenido: $NODE_ACTUAL)."
  fi
  ok "Checksum de Node.js verificado."

  run_logged "Instalando Node.js" \
    tar -xJf "/tmp/$NODE_FILE" -C /usr/local --strip-components=1 \
    || die "No pude extraer Node.js (ver $INSTALL_LOG)."
  rm -f "/tmp/$NODE_FILE" "$NODE_SUMS"
fi
command -v node >/dev/null || die "Node.js no quedó instalado."
ok "Node.js $(node -v)"

# ── Claude Code ─────────────────────────────────────────────────────────────

if ! command -v claude >/dev/null; then
  run_logged "Instalando Claude Code" \
    npm install -g --no-audit --no-fund @anthropic-ai/claude-code \
    || die "No pude instalar Claude Code (ver $INSTALL_LOG)."
fi
CLAUDE_BIN="$(command -v claude)"
[[ -n $CLAUDE_BIN ]] || die "Claude Code no quedó instalado."
ok "Claude Code listo ($CLAUDE_BIN)"

# ── Ollama ──────────────────────────────────────────────────────────────────

OLLAMA_BIN=""
if [[ $PROVIDER == ollama-local ]]; then
  if ! command -v ollama >/dev/null; then
    dim "  La descarga de Ollama puede tardar varios minutos."
    if ! run_logged "Instalando Ollama" bash -c 'set -o pipefail; curl -fsSL https://ollama.com/install.sh | sh'; then
      die "Falló el instalador oficial de Ollama. Revisa $INSTALL_LOG.
Pista: en WSL suele faltar zstd (sudo apt-get install -y zstd)."
    fi
  fi
  OLLAMA_BIN="$(command -v ollama || true)"
  [[ -n $OLLAMA_BIN ]] || die "Ollama no quedó instalado."
  # El instalador oficial crea un servicio del sistema; usaremos uno propio como iris.
  systemctl disable --now ollama.service 2>/dev/null || true
  ok "Ollama listo ($OLLAMA_BIN)"
else
  log "Ollama local no hace falta: $(provider_label "$PROVIDER")."
fi

# ── Usuario y carpetas ──────────────────────────────────────────────────────

if ! id -u "$IRIS_USER" >/dev/null 2>&1; then
  log "Creando usuario $IRIS_USER…"
  getent group "$IRIS_USER" >/dev/null || groupadd "$IRIS_USER"
  useradd --create-home --home-dir "$IRIS_HOME" --shell /bin/bash \
    --gid "$IRIS_USER" "$IRIS_USER"
  passwd -l "$IRIS_USER" >/dev/null 2>&1 || true
fi
install -d -m 0755 "$APP_DIR" "$CONF_DIR"
install -d -o "$IRIS_USER" -g "$IRIS_USER" -m 0750 \
  "$DATA_DIR" "$DATA_DIR/media" "$DATA_DIR/logs" \
  "$IRIS_HOME/workspace" "$IRIS_HOME/.claude"
chown "$IRIS_USER:$IRIS_USER" "$DATA_DIR" "$DATA_DIR/media" "$DATA_DIR/logs" \
  "$IRIS_HOME/workspace" "$IRIS_HOME/.claude"

# Claude Code en modo headless no debe quedarse en el onboarding interactivo.
CLAUDE_JSON="$IRIS_HOME/.claude.json"
if [[ ! -f $CLAUDE_JSON ]]; then
  cat > "$CLAUDE_JSON" <<'JSON'
{
  "hasCompletedOnboarding": true
}
JSON
  chown "$IRIS_USER:$IRIS_USER" "$CLAUDE_JSON"
  chmod 0600 "$CLAUDE_JSON"
fi
ok "Usuario y carpetas listos."

if [[ $PROVIDER == claude-subscription && -z $OAUTH_TOKEN ]]; then
  log "Autorización de Claude (suscripción)"
  dim "Se abrirá el navegador. Autoriza y copia el token que se imprima."
  TOKEN_OUT="$(mktemp)"
  sudo -u "$IRIS_USER" -H "$CLAUDE_BIN" setup-token </dev/tty 2>&1 \
    | tee "$TOKEN_OUT" || true
  OAUTH_TOKEN="$(grep -oE 'sk-ant-oat[0-9]*-[A-Za-z0-9_-]+' "$TOKEN_OUT" | tail -1)"
  rm -f "$TOKEN_OUT"
  if [[ -z $OAUTH_TOKEN ]]; then
    warn "No pude capturar el token automáticamente."
    ui_password OAUTH_TOKEN "Pega el token (sk-ant-oat01-...)" \
      || die "Instalación cancelada."
  fi
  [[ -n $OAUTH_TOKEN ]] || die "No obtuve el token de la suscripción."
  ok "Token de suscripción guardado."
fi

# ── Respaldo antes de tocar el código ───────────────────────────────────────

backup_existing

# ── Código ──────────────────────────────────────────────────────────────────

log "Copiando Iris…"
[[ -f "$SRC_DIR/bridge/server.js" ]] || die "No encuentro bridge/server.js junto al instalador."
[[ -d "$SRC_DIR/core/iris_core" ]] || die "No encuentro core/iris_core junto al instalador."

if [[ "$(readlink -f "$SRC_DIR")" != "$(readlink -f "$APP_DIR")" ]]; then
  rm -rf "$APP_DIR/bridge" "$APP_DIR/core" "$APP_DIR/systemd" "$APP_DIR/bin"
  cp -a "$SRC_DIR/bridge" "$APP_DIR/bridge"
  cp -a "$SRC_DIR/core" "$APP_DIR/core"
  cp -a "$SRC_DIR/systemd" "$APP_DIR/systemd"
  [[ -d "$SRC_DIR/bin" ]] && cp -a "$SRC_DIR/bin" "$APP_DIR/bin"
  install -m 0755 "$SRC_DIR/install.sh" "$APP_DIR/install.sh"
else
  log "El instalador corre desde $APP_DIR: se reutiliza el código ya copiado."
fi
[[ -d "$APP_DIR/systemd" ]] || die "Falta $APP_DIR/systemd (¿instalación incompleta?)."
if [[ -f "$APP_DIR/bin/iris" ]]; then
  install -m 0755 "$APP_DIR/bin/iris" /usr/local/bin/iris
fi
chown -R root:root "$APP_DIR/bridge" "$APP_DIR/core" "$APP_DIR/systemd"
chmod -R a+rX "$APP_DIR"

run_logged "Instalando dependencias de Node" \
  bash -c 'cd "$1" && exec npm install --omit=dev --no-audit --no-fund' _ "$APP_DIR/bridge" \
  || die "Falló npm install (ver $INSTALL_LOG)."
chown -R root:root "$APP_DIR/bridge"
chmod -R a+rX "$APP_DIR/bridge"

run_logged "Creando entorno de Python" python3 -m venv "$APP_DIR/core/venv" \
  || die "No pude crear el entorno de Python (ver $INSTALL_LOG)."
run_logged "Actualizando pip" "$APP_DIR/core/venv/bin/pip" install --quiet --upgrade pip wheel \
  || die "Falló pip (ver $INSTALL_LOG)."
run_logged "Instalando dependencias de Python" \
  "$APP_DIR/core/venv/bin/pip" install --quiet -r "$APP_DIR/core/requirements.txt" \
  || die "Falló pip install (ver $INSTALL_LOG)."
chmod -R a+rX "$APP_DIR/core/venv"
ok "Código instalado."

# ── Configuración ───────────────────────────────────────────────────────────

TOKEN="${IRIS_BRIDGE_TOKEN:-}"
SOURCE_DIR_TO_SAVE="$SRC_DIR"
if [[ -f $ENV_FILE ]]; then
  if [[ -z $TOKEN ]]; then
    TOKEN="$(sed -n 's/^IRIS_BRIDGE_TOKEN=//p' "$ENV_FILE" | head -n1)"
  fi
  EXISTING_SRC="$(sed -n 's/^IRIS_SOURCE_DIR=//p' "$ENV_FILE" | head -n1)"
  if [[ $MODE == update && -n $EXISTING_SRC \
        && "$(readlink -f "$SRC_DIR")" == "$(readlink -f "$APP_DIR")" ]]; then
    SOURCE_DIR_TO_SAVE="$EXISTING_SRC"
  fi
fi
if [[ -n ${SOURCE_TMP:-} ]]; then
  SOURCE_DIR_TO_SAVE=""
fi
[[ -n $TOKEN ]] || TOKEN="$(openssl rand -hex 32)"

pick_free_port() {
  local p=$1
  while (( p < 65535 )) && ss -ltnH "sport = :$p" 2>/dev/null | grep -q .; do
    p=$((p + 1))
  done
  printf '%s' "$p"
}

EXISTING_BRIDGE_PORT="$(sed -n 's/^IRIS_BRIDGE_PORT=//p' "$ENV_FILE" 2>/dev/null | head -n1 || true)"
EXISTING_CORE_PORT="$(sed -n 's/^IRIS_CORE_PORT=//p' "$ENV_FILE" 2>/dev/null | head -n1 || true)"
if [[ $MODE == update && -n ${EXISTING_BRIDGE_PORT:-} ]]; then
  BRIDGE_PORT_VALUE="$EXISTING_BRIDGE_PORT"
else
  BRIDGE_PORT_VALUE="$(pick_free_port 3003)"
fi
if [[ $MODE == update && -n ${EXISTING_CORE_PORT:-} ]]; then
  CORE_PORT_VALUE="$EXISTING_CORE_PORT"
else
  CORE_PORT_VALUE="$(pick_free_port $((BRIDGE_PORT_VALUE + 1)))"
fi
[[ -n ${BRIDGE_PORT_VALUE:-} && -n ${CORE_PORT_VALUE:-} ]] || die "No pude asignar puertos libres."
if [[ "$BRIDGE_PORT_VALUE" != "3003" ]]; then
  warn "El puerto 3003 está ocupado; usaré $BRIDGE_PORT_VALUE para el puente."
fi
if [[ "$CORE_PORT_VALUE" != "3004" ]]; then
  warn "El puerto 3004 está ocupado; usaré $CORE_PORT_VALUE para el núcleo."
fi

if [[ $MODE != update ]]; then
  umask 077
  {
    echo "# Iris Agent — configuración (generada por install.sh)"
    echo "IRIS_PHONE=$PHONE"
    echo "IRIS_PROVIDER=$PROVIDER"
    echo "IRIS_MODEL=$MODEL"
    echo "IRIS_WORKSPACE=$IRIS_HOME/workspace"
    echo "IRIS_DATA=$DATA_DIR"
    echo "IRIS_WA_SESSION=$DATA_DIR/whatsapp-session"
    echo "IRIS_BRIDGE_URL=http://127.0.0.1:$BRIDGE_PORT_VALUE"
    echo "IRIS_BRIDGE_PORT=$BRIDGE_PORT_VALUE"
    echo "IRIS_CORE_URL=http://127.0.0.1:$CORE_PORT_VALUE"
    echo "IRIS_BRIDGE_TOKEN=$TOKEN"
    echo "IRIS_CORE_BIND=127.0.0.1"
    echo "IRIS_CORE_PORT=$CORE_PORT_VALUE"
    echo "IRIS_CLAUDE_BIN=$CLAUDE_BIN"
    echo "IRIS_CLAUDE_TIMEOUT=1800"
    echo "IRIS_PERMISSION_MODE=bypassPermissions"
    echo "IRIS_MAX_CHARS=4000"
    echo "IRIS_SOURCE_DIR=$SOURCE_DIR_TO_SAVE"
    echo "IRIS_REPO_URL=${IRIS_REPO_URL:-$REPO_URL_DEFAULT}"
    echo "IRIS_REPO_REF=${IRIS_REPO_REF:-$REPO_REF_DEFAULT}"
    case $PROVIDER in
      claude-subscription)
        echo "CLAUDE_CODE_OAUTH_TOKEN=$OAUTH_TOKEN"
        ;;
      claude-api)
        echo "ANTHROPIC_API_KEY=$ANTHROPIC_KEY"
        ;;
      ollama-cloud)
        echo "ANTHROPIC_BASE_URL=https://ollama.com"
        echo "ANTHROPIC_AUTH_TOKEN=$OLLAMA_KEY"
        echo "ANTHROPIC_API_KEY="
        ;;
      ollama-local)
        # Mismos valores que configura `ollama launch claude`.
        echo "ANTHROPIC_BASE_URL=http://127.0.0.1:11434"
        echo "ANTHROPIC_AUTH_TOKEN=ollama"
        echo "ANTHROPIC_API_KEY=ollama"
        ;;
      custom)
        echo "ANTHROPIC_BASE_URL=$CUSTOM_BASE"
        echo "ANTHROPIC_AUTH_TOKEN=$CUSTOM_TOKEN"
        echo "ANTHROPIC_API_KEY="
        ;;
    esac
  } >"$ENV_FILE"
  umask 022
  chown root:"$IRIS_USER" "$ENV_FILE"
  chmod 0640 "$ENV_FILE"
  ok "Configuración en $ENV_FILE"
else
  log "Modo actualización: se conserva $ENV_FILE"
fi

# ── Servicios ──────────────────────────────────────────────────────────────

log "Instalando servicios systemd…"
install -m 0644 "$SRC_DIR/systemd/iris-bridge.service" /etc/systemd/system/iris-bridge.service
install -m 0644 "$SRC_DIR/systemd/iris-core.service" /etc/systemd/system/iris-core.service
ENABLE_UNITS=(iris-bridge.service iris-core.service)
if [[ $PROVIDER == ollama-local ]]; then
  install -m 0644 "$SRC_DIR/systemd/iris-ollama.service" /etc/systemd/system/iris-ollama.service
  ENABLE_UNITS+=(iris-ollama.service)
else
  systemctl disable --now iris-ollama.service 2>/dev/null || true
  rm -f /etc/systemd/system/iris-ollama.service
fi
systemctl daemon-reload
systemctl enable "${ENABLE_UNITS[@]}" >/dev/null
ok "Servicios instalados."

# ── Ollama: arranque y sesión ───────────────────────────────────────────────

SIGNIN_OK=1
if [[ $PROVIDER == ollama-local ]]; then
  systemctl restart iris-ollama.service
  sleep 1
  systemctl is-active --quiet iris-ollama.service \
    || die "Ollama no arrancó (¿puerto 11434 ocupado?). Revisa: journalctl -u iris-ollama -n 50"
  for _ in $(seq 1 30); do
    curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
    sleep 1
  done
  curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 \
    || die "Ollama no responde. Revisa: journalctl -u iris-ollama -n 50"

  SIGNIN_OK=0
  if [[ $SKIP_SIGNIN -eq 0 ]]; then
    echo
    log "Sesión de Ollama Cloud"
    dim "Se abrirá una URL. Ábrela en el navegador, autoriza y vuelve aquí."
    # </dev/tty es imprescindible: bajo `curl | bash` el stdin es el script y
    # `ollama signin` imprimiría la URL y saldría sin esperar la autorización.
    sudo -u "$IRIS_USER" -H "$OLLAMA_BIN" signin </dev/tty || true

    SIGNIN_OUT="$(mktemp)"
    for _ in 1 2 3 4 5; do
      if timeout 180 sudo -u "$IRIS_USER" -H "$OLLAMA_BIN" run "$MODEL" "Responde solo: OK" \
           >"$SIGNIN_OUT" 2>&1 \
         && ! grep -qiE "need to be signed in|sign in was cancelled|failed to sign in" "$SIGNIN_OUT"; then
        SIGNIN_OK=1
        ok "Sesión de Ollama verificada."
        break
      fi
      warn "Aún no puedo usar $MODEL (sesión sin completar o modelo no disponible)."
      dim "  Si la URL caducó, ejecuta en otra terminal:  sudo -u iris -H ollama signin"
      [[ $ASSUME_YES -eq 1 ]] && break
      read -rp "Completa el inicio de sesión y pulsa Enter para reintentar (o escribe 'saltar'): " answer </dev/tty || true
      [[ "${answer:-}" == "saltar" ]] && break
    done
    rm -f "$SIGNIN_OUT"
  fi

  if ! run_logged "Verificando modelo $MODEL" \
      sudo -u "$IRIS_USER" -H "$OLLAMA_BIN" pull "$MODEL"; then
    warn "No pude descargar/verificar '$MODEL'. Se intentará al primer uso."
  fi

  if [[ $SKIP_SIGNIN -eq 0 && $SIGNIN_OK -eq 0 ]]; then
    warn "Seguimos sin sesión verificada de Ollama. Iris no podrá responder hasta que la completes:"
    dim "  sudo -u iris -H ollama signin"
  fi
else
  log "Modelo: $(provider_label "$PROVIDER")${MODEL:+ ($MODEL)}."
fi

if [[ $SIGNIN_OK -eq 1 ]]; then
  log "Prueba rápida de Claude Code${MODEL:+ con $MODEL}…"
  TEST_SCRIPT="$(mktemp)"
  {
    echo '#!/usr/bin/env bash'
    echo "set -a; . '$ENV_FILE'; set +a"
    echo "cd '$IRIS_HOME/workspace'"
    if [[ -n $MODEL ]]; then
      printf "exec '%s' -p 'Responde solo: OK' --output-format json --model '%s' --dangerously-skip-permissions\n" \
        "$CLAUDE_BIN" "$MODEL"
    else
      printf "exec '%s' -p 'Responde solo: OK' --output-format json --dangerously-skip-permissions\n" \
        "$CLAUDE_BIN"
    fi
  } >"$TEST_SCRIPT"
  chmod 0700 "$TEST_SCRIPT"
  chown "$IRIS_USER":"$IRIS_USER" "$TEST_SCRIPT"

  if timeout 180 sudo -u "$IRIS_USER" -H bash "$TEST_SCRIPT" >/tmp/iris-test.json 2>/tmp/iris-test.err; then
    if grep -qE '"is_error"[[:space:]]*:[[:space:]]*true|"type"[[:space:]]*:[[:space:]]*"error"' /tmp/iris-test.json \
       || ! grep -qE '"session_id"|"result"' /tmp/iris-test.json; then
      warn "Claude respondió con un error inesperado:"
      sed -n '1,5p' /tmp/iris-test.json
    else
      ok "Claude Code respondió."
    fi
    rm -f /tmp/iris-test.json /tmp/iris-test.err
  else
    warn "La prueba con Claude falló. Revisa:"
    sed -n '1,6p' /tmp/iris-test.err 2>/dev/null || true
    dim "Revisa las credenciales del proveedor o el modelo elegido."
  fi
  rm -f "$TEST_SCRIPT"
fi

# ── Pareo de WhatsApp ───────────────────────────────────────────────────────

if [[ $SKIP_PAIR -eq 0 ]]; then
  echo
  log "Pareo de WhatsApp"
  dim "Abre WhatsApp en tu teléfono → Dispositivos vinculados → Vincular dispositivo."
  systemctl stop iris-bridge.service 2>/dev/null || true
  sudo -u "$IRIS_USER" -H bash -c ". '$ENV_FILE'; exec /usr/bin/env node '$APP_DIR/bridge/server.js' --pair" </dev/tty \
    || warn "El pareo no terminó. Puedes reintentarlo con:  sudo iris pair"
fi

# ── Arranque ────────────────────────────────────────────────────────────────

log "Arrancando Iris…"
systemctl restart iris-bridge.service iris-core.service

CORE_OK=0
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:${CORE_PORT_VALUE}/health" >/dev/null 2>&1; then CORE_OK=1; break; fi
  sleep 1
done
[[ $CORE_OK -eq 1 ]] || die "El núcleo no respondió. Revisa: journalctl -u iris-core -n 50"
ok "Núcleo en línea."

for _ in $(seq 1 30); do
  STATUS="$(curl -fsS "http://127.0.0.1:${BRIDGE_PORT_VALUE}/health" 2>/dev/null | sed -n 's/.*"status":"\([^"]*\)".*/\1/p' || true)"
  [[ "$STATUS" == "connected" ]] && break
  sleep 1
done
if [[ "${STATUS:-}" == "connected" ]]; then
  ok "WhatsApp conectado."
else
  warn "WhatsApp aún no conectado (estado: ${STATUS:-desconocido}). Ejecuta:  sudo iris pair"
fi

# ── Prueba ──────────────────────────────────────────────────────────────────

if [[ "${STATUS:-}" == "connected" ]]; then
  PHONE_DIGITS="$(printf '%s' "$PHONE" | tr -cd '0-9')"
  if curl -fsS -X POST "http://127.0.0.1:${BRIDGE_PORT_VALUE}/send" \
      -H "Authorization: Bearer $TOKEN" \
      -H 'Content-Type: application/json' \
      -d "{\"chatId\":\"${PHONE_DIGITS}@s.whatsapp.net\",\"text\":\"Iris en línea ✅\"}" >/dev/null; then
    ok "Mensaje de prueba enviado a $PHONE"
  else
    warn "No pude enviar el mensaje de prueba."
  fi
fi

# ── Resumen ─────────────────────────────────────────────────────────────────

write_state success "signin_ok=$SIGNIN_OK skip_signin=$SKIP_SIGNIN skip_pair=$SKIP_PAIR"

echo
ok "Iris quedó instalado."
if [[ -f /etc/systemd/system/iris-ollama.service ]]; then
  dim "  Servicios:  iris-ollama  iris-bridge  iris-core"
else
  dim "  Servicios:  iris-bridge  iris-core"
fi
dim "  Config:     $ENV_FILE"
dim "  Datos:      $DATA_DIR"
dim "  Workspace:  $IRIS_HOME/workspace"
dim "  Logs:       journalctl -u iris-core -f"
echo
dim "Prueba: escribe por WhatsApp a $PHONE"
dim "Gestión:  sudo iris status | logs | pair | model <nombre> | restart"
