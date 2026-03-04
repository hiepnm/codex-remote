import os
import time
import shlex
import hmac
import shutil
import requests
import subprocess
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["TG_BOT_TOKEN"]
ALLOWED_CHAT_ID = int(os.environ["TG_ALLOWED_CHAT_ID"])
COMMAND_SECRET = os.environ.get("COMMAND_SECRET", "").strip()
WORKDIR = os.environ.get("WORKDIR", os.getcwd())
BOT_NAME = os.environ.get("BOT_NAME", "codex-remote")
CODEX_BIN = os.environ.get("CODEX_BIN", "").strip()
OFFSET_FILE = os.environ.get("OFFSET_FILE", ".telegram_offset")

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

def log(msg: str):
    print(msg, flush=True)

def load_offset() -> int | None:
    try:
        with open(OFFSET_FILE, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            return None
        return int(raw)
    except FileNotFoundError:
        return None
    except Exception as e:
        log(f"[{BOT_NAME}] offset load failed: {e}")
        return None

def save_offset(offset: int):
    try:
        with open(OFFSET_FILE, "w", encoding="utf-8") as f:
            f.write(str(offset))
    except Exception as e:
        log(f"[{BOT_NAME}] offset save failed: {e}")

def resolve_codex_bin() -> str:
    # Prefer explicit path if provided.
    if CODEX_BIN:
        return CODEX_BIN
    # Avoid asdf shim to prevent `.tool-versions` dependency in current cwd.
    try:
        p = subprocess.run(
            ["asdf", "which", "codex"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
        candidate = (p.stdout or "").strip()
        if p.returncode == 0 and candidate:
            return candidate
    except Exception:
        pass
    # Fallback to whatever is on PATH.
    return shutil.which("codex") or "codex"

def send(chat_id: int, text: str):
    try:
        requests.post(
            f"{API}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=(10, 30),
        )
    except requests.RequestException:
        pass

def has_any_flag(argv: list[str], flags: set[str]) -> bool:
    return any(a in flags for a in argv)

def maybe_add_workdir(argv: list[str]) -> list[str]:
    # Nếu bạn muốn bot KHÔNG auto -C, thì return argv luôn và xoá hàm này.
    if has_any_flag(argv, {"-C", "--cd"}):
        return argv
    return argv[:1] + ["-C", WORKDIR] + argv[1:]

def normalize_cd_position(argv: list[str]) -> list[str]:
    # Work around codex-cli quirk: `codex -C <dir> exec ...` may ignore `-C`.
    # Rewrite to: `codex exec -C <dir> ...`.
    if not argv or argv[0] != "codex":
        return argv

    subcommands = {
        "exec", "review", "login", "logout", "mcp", "mcp-server", "app-server",
        "app", "completion", "sandbox", "debug", "apply", "resume", "fork",
        "cloud", "features", "help",
    }
    args = argv[1:]
    subcmd_idx = next((i for i, a in enumerate(args) if a in subcommands), None)
    if subcmd_idx is None:
        return argv

    before = args[:subcmd_idx]
    subcmd = args[subcmd_idx]
    after = args[subcmd_idx + 1:]

    cd_tokens: list[str] = []
    kept: list[str] = []
    i = 0
    while i < len(before):
        token = before[i]
        if token in {"-C", "--cd"} and i + 1 < len(before):
            cd_tokens.extend([token, before[i + 1]])
            i += 2
            continue
        if token.startswith("--cd="):
            cd_tokens.append(token)
        elif token.startswith("-C") and len(token) > 2:
            cd_tokens.append(token)
        else:
            kept.append(token)
        i += 1

    if not cd_tokens:
        return argv

    return ["codex"] + kept + [subcmd] + cd_tokens + after

def run_cmd(argv: list[str], timeout: int = 1800) -> tuple[int, str]:
    run_argv = argv[:]
    if run_argv and run_argv[0] == "codex":
        run_argv[0] = resolve_codex_bin()
    p = subprocess.run(
        run_argv,
        cwd=WORKDIR,  # cwd mặc định, codex tự xử lý -C/--cd
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
    )
    out = (p.stdout or "")[-3500:]
    return p.returncode, out

def main():
    offset = load_offset()
    last_poll_error_at = 0.0
    if COMMAND_SECRET:
        usage = "/codex <secret> <codex-cli-args>"
    else:
        usage = "/codex <codex-cli-args>"
    log(f"[{BOT_NAME}] started | workdir={WORKDIR} | usage={usage} | offset={offset}")
    send(ALLOWED_CHAT_ID, f"✅ {BOT_NAME} online. Use: {usage}")

    while True:
        params = {"timeout": 50}
        if offset is not None:
            params["offset"] = offset

        try:
            r = requests.get(f"{API}/getUpdates", params=params, timeout=(10, 70))
            r.raise_for_status()
            data = r.json()
        except requests.RequestException:
            now = time.time()
            if now - last_poll_error_at >= 30:
                log(f"[{BOT_NAME}] telegram unavailable, retrying...")
                last_poll_error_at = now
            time.sleep(2)
            continue

        if not data.get("ok"):
            time.sleep(2)
            continue

        for upd in data.get("result", []):
            offset = upd["update_id"] + 1
            save_offset(offset)
            msg = upd.get("message") or {}
            chat = msg.get("chat") or {}
            chat_id = chat.get("id")
            text = (msg.get("text") or "").strip()

            if chat_id != ALLOWED_CHAT_ID:
                continue

            if text.startswith("/start"):
                if COMMAND_SECRET:
                    send(chat_id, "Send: /codex <secret> <args>. Example: /codex mysecret exec \"hello\"")
                else:
                    send(chat_id, "Send: /codex <args>. Example: /codex exec \"hello\"")
                continue

            if not text.startswith("/codex "):
                continue

            body = text[len("/codex "):].strip()
            # Normalize Telegram “smart dashes” to ASCII hyphen.
            body = (
                body.replace("—", "-")
                .replace("–", "-")
                .replace("−", "-")
                .replace("‑", "-")
            )
            if not body:
                if COMMAND_SECRET:
                    send(chat_id, "Example:\n/codex mysecret exec \"hello\"\n/codex mysecret --help")
                else:
                    send(chat_id, "Example:\n/codex exec \"hello\"\n/codex --help")
                continue

            try:
                parts = shlex.split(body)
                if COMMAND_SECRET:
                    if len(parts) < 2:
                        send(chat_id, "Usage: /codex <secret> <codex-cli-args>")
                        continue
                    provided_secret = parts[0]
                    if not hmac.compare_digest(provided_secret, COMMAND_SECRET):
                        send(chat_id, "⛔ Unauthorized")
                        continue
                    argv = ["codex"] + parts[1:]
                else:
                    if not parts:
                        send(chat_id, "Usage: /codex <codex-cli-args>")
                        continue
                    argv = ["codex"] + parts
                argv = maybe_add_workdir(argv)  # <- nếu không thích auto -C, mình sẽ chỉ bạn bỏ
                argv = normalize_cd_position(argv)
                send(chat_id, f"⏳ Running:\n{shlex.join(argv)}")

                code, out = run_cmd(argv, timeout=1800)
                status = "✅ Done" if code == 0 else f"❌ Exit {code}"
                send(chat_id, f"{status}\n\n{out}")

            except ValueError as e:
                send(chat_id, f"⚠️ Parse error: {e}\nTip: nhớ đóng dấu \"...\"")
            except subprocess.TimeoutExpired:
                send(chat_id, "⏱️ Timeout. Task took too long.")
            except Exception as e:
                send(chat_id, f"⚠️ Error: {e}")

        time.sleep(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log(f"[{BOT_NAME}] stopped")
