import os
import time
import shlex
import hmac
import html
import re
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
TELEGRAM_CHUNK_SIZE = 3500
ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
TOKENS_USED_RE = re.compile(r"\ntokens used\s*\n[0-9,]+\s*\n", re.IGNORECASE)
SUPPORTED_VIEW_MODES = {"pre", "html"}

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

def send(chat_id: int, text: str, parse_mode: str | None = None) -> bool:
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(
            f"{API}/sendMessage",
            json=payload,
            timeout=(10, 30),
        )
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        log(f"[{BOT_NAME}] send failed: {e.__class__.__name__}: {e}")
        return False

def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)

def extract_final_text(raw: str) -> str:
    clean = strip_ansi(raw).replace("\r\n", "\n").strip()
    if not clean:
        return ""

    # Newer codex-cli often appends the assistant's final message after this marker.
    marker_matches = list(TOKENS_USED_RE.finditer(clean))
    if marker_matches:
        tail = clean[marker_matches[-1].end() :].strip()
        if tail:
            return tail

    # Fallback: keep only the last assistant block if transcript-style output is present.
    if "\ncodex\n" in clean:
        tail = clean.split("\ncodex\n")[-1].strip()
        if tail:
            return tail

    return clean

def extract_line_value(clean: str, key: str) -> str:
    key_lower = key.lower()
    for line in clean.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.lower().startswith(key_lower):
            return stripped[len(key) :].strip()
    m = re.search(rf"(?mi)^\s*{re.escape(key)}\s*(.+)$", clean)
    return m.group(1).strip() if m else "-"

def extract_user_instruction(clean: str) -> str:
    m = re.search(r"(?s)\nuser\n(.*?)(?:\nmcp startup:|\nthinking\n|\ncodex\n|$)", clean)
    if not m:
        return "-"
    text = m.group(1).strip()
    return text or "-"

def extract_tokens_used(clean: str) -> str:
    m = re.search(r"(?is)\ntokens used\s*\n([0-9,]+)\s*\n", clean)
    return m.group(1).strip() if m else "-"

def parse_output_fields(raw: str) -> dict[str, str]:
    clean = strip_ansi(raw).replace("\r\n", "\n").strip()
    final_answer = extract_final_text(clean).rstrip() or "(no output)"
    return {
        "workdir": extract_line_value(clean, "workdir:"),
        "sandbox": extract_line_value(clean, "sandbox:"),
        "session_id": extract_line_value(clean, "session id:"),
        "user_instruction": extract_user_instruction(clean),
        "codex_answer": final_answer,
        "tokens_used": extract_tokens_used(clean),
    }

def build_telegram_report(status: str, fields: dict[str, str]) -> str:
    return (
        f"{status}\n"
        "----\n"
        f"workdir: {fields['workdir']}\n"
        f"sandbox: {fields['sandbox']}\n"
        f"session id: {fields['session_id']}\n"
        "----\n"
        "user\n"
        f"{fields['user_instruction']}\n"
        "codex:\n"
        f"{fields['codex_answer']}\n\n"
        "tokens used\n"
        f"{fields['tokens_used']}"
    )

def send_chunked(chat_id: int, text: str, chunk_size: int = TELEGRAM_CHUNK_SIZE):
    if not text:
        send(chat_id, "")
        return
    for i in range(0, len(text), chunk_size):
        send(chat_id, text[i : i + chunk_size])

def send_pre_chunks(chat_id: int, text: str):
    safe_chunk_size = 3200
    for i in range(0, len(text), safe_chunk_size):
        chunk = text[i : i + safe_chunk_size]
        send(chat_id, f"<pre>{html.escape(chunk)}</pre>", parse_mode="HTML")

def markdown_inline_to_html(text: str) -> str:
    code_spans: list[str] = []

    def code_repl(match: re.Match[str]) -> str:
        code_spans.append(match.group(1))
        return f"@@CODE{len(code_spans)-1}@@"

    # Protect inline code first so markdown formatting doesn't touch it.
    text = re.sub(r"`([^`\n]+)`", code_repl, text)
    escaped = html.escape(text)

    # Basic inline markdown conversion.
    escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"__(.+?)__", r"<b>\1</b>", escaped)
    escaped = re.sub(r"\*(.+?)\*", r"<i>\1</i>", escaped)
    escaped = re.sub(r"_(.+?)_", r"<i>\1</i>", escaped)
    escaped = re.sub(r"~~(.+?)~~", r"<s>\1</s>", escaped)

    for i, code in enumerate(code_spans):
        escaped = escaped.replace(f"@@CODE{i}@@", f"<code>{html.escape(code)}</code>")
    return escaped

def markdown_to_telegram_html(text: str) -> str:
    text = text.replace("\r\n", "\n")
    parts: list[str] = []
    pos = 0
    fence_re = re.compile(r"```(?:[a-zA-Z0-9_+-]+)?\n(.*?)```", re.DOTALL)

    def convert_non_code(block: str) -> str:
        lines = block.split("\n")
        out: list[str] = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                out.append("")
                continue
            heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
            if heading:
                out.append(f"<b>{markdown_inline_to_html(heading.group(2))}</b>")
                continue
            bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
            if bullet:
                out.append(f"• {markdown_inline_to_html(bullet.group(1))}")
                continue
            out.append(markdown_inline_to_html(line))
        return "\n".join(out)

    for m in fence_re.finditer(text):
        if m.start() > pos:
            parts.append(convert_non_code(text[pos : m.start()]))
        code = html.escape(m.group(1).rstrip("\n"))
        parts.append(f"<pre>{code}</pre>")
        pos = m.end()
    if pos < len(text):
        parts.append(convert_non_code(text[pos:]))
    return "".join(parts).strip()

def send_html_chunks(chat_id: int, html_text: str):
    safe_chunk_size = 3200
    if not html_text:
        send(chat_id, "(no output)", parse_mode="HTML")
        return

    paragraphs = html_text.split("\n\n")
    current = ""
    for p in paragraphs:
        piece = p if not current else f"{current}\n\n{p}"
        if len(piece) <= safe_chunk_size:
            current = piece
            continue
        if current:
            send(chat_id, current, parse_mode="HTML")
        current = p
        while len(current) > safe_chunk_size:
            send(chat_id, current[:safe_chunk_size], parse_mode="HTML")
            current = current[safe_chunk_size:]
    if current:
        send(chat_id, current, parse_mode="HTML")

def send_output_pre(chat_id: int, status: str, fields: dict[str, str]):
    report = build_telegram_report(status, fields)
    send_pre_chunks(chat_id, report)

def send_header_box(chat_id: int, status: str, fields: dict[str, str]):
    header_text = (
        f"{status}\n"
        "----\n"
        f"workdir: {fields['workdir']}\n"
        f"sandbox: {fields['sandbox']}\n"
        f"session id: {fields['session_id']}"
    )
    send_pre_chunks(chat_id, header_text)

def send_output_html(chat_id: int, status: str, fields: dict[str, str]):
    send_header_box(chat_id, status, fields)
    send(chat_id, "<b>user</b>", parse_mode="HTML")
    send_pre_chunks(chat_id, fields["user_instruction"])
    send(chat_id, "<b>codex</b>", parse_mode="HTML")
    send_html_chunks(chat_id, markdown_to_telegram_html(fields["codex_answer"]))
    send(chat_id, f"<b>tokens used</b>\n<code>{html.escape(fields['tokens_used'])}</code>", parse_mode="HTML")

def extract_view_mode(args: list[str]) -> tuple[str, list[str]]:
    mode = "html"
    out: list[str] = []
    i = 0
    while i < len(args):
        token = args[i]
        if token == "-view-mode" and i + 1 < len(args):
            mode = args[i + 1].lower()
            i += 2
            continue
        if token.startswith("-view-mode="):
            mode = token.split("=", 1)[1].lower()
            i += 1
            continue
        out.append(token)
        i += 1
    return mode, out

def send_output(chat_id: int, status: str, out: str, view_mode: str):
    fields = parse_output_fields(out)
    if view_mode == "html":
        send_output_html(chat_id, status, fields)
        return
    send_output_pre(chat_id, status, fields)

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
    out = p.stdout or ""
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
                    cmd_parts = parts[1:]
                else:
                    if not parts:
                        send(chat_id, "Usage: /codex <codex-cli-args>")
                        continue
                    cmd_parts = parts
                view_mode, cmd_parts = extract_view_mode(cmd_parts)
                if view_mode not in SUPPORTED_VIEW_MODES:
                    send(chat_id, "⚠️ Invalid view mode. Use: html, pre")
                    continue
                if not cmd_parts:
                    send(chat_id, "⚠️ Missing codex args after -view-mode")
                    continue
                argv = ["codex"] + cmd_parts
                argv = maybe_add_workdir(argv)  # <- nếu không thích auto -C, mình sẽ chỉ bạn bỏ
                argv = normalize_cd_position(argv)
                send(chat_id, f"⏳ Running ({view_mode}):\n{shlex.join(argv)}")

                code, out = run_cmd(argv, timeout=1800)
                status = "✅ Done" if code == 0 else f"❌ Exit {code}"
                send_output(chat_id, status, out, view_mode)

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
