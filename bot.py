import shutil
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    raw = " ".join(context.args).strip() if context.args else ""
    await _render_delete_dir(update.effective_chat.id, raw_path=raw or None, edit=None, context=context)

async def _render_delete_dir(chat_id: int, raw_path: str | None, edit, context: ContextTypes.DEFAULT_TYPE):
    path = _safe_browse_path(raw_path)
    if not path.exists() or not path.is_dir():
        await context.bot.send_message(chat_id=chat_id, text=f"Directory not found: {path}")
        return
    entries = sorted(list(path.iterdir()), key=lambda p: (not p.is_dir(), p.name.lower()))
    BROWSE_STATE[chat_id] = {"path": str(path), "entries": [str(p) for p in entries], "offset": 0, "delete": True}
    await _render_delete_dir_page(chat_id, context, edit)

async def _render_delete_dir_page(chat_id: int, context: ContextTypes.DEFAULT_TYPE, edit):
    st = BROWSE_STATE.get(chat_id)
    if not st:
        await context.bot.send_message(chat_id=chat_id, text="No browse state. Run /delete")
        return
    path = Path(st["path"])
    entries = [Path(p) for p in st["entries"]]
    offset = int(st.get("offset", 0))
    page_size = 12
    page = entries[offset:offset + page_size]
    rows = []
    for i, p in enumerate(page):
        idx = offset + i
        icon = "📁" if p.is_dir() else "📄"
        # Add delete button for each file/folder
        rows.append([
            InlineKeyboardButton(f"{icon} {p.name}", callback_data=f"delnav:{idx}"),
            InlineKeyboardButton("❌", callback_data=f"delreq:{idx}")
        ])
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data="del_prev"))
    if offset + page_size < len(entries):
        nav.append(InlineKeyboardButton("Next ➡️", callback_data="del_next"))
    if nav:
        rows.append(nav)
    # Add delete folder button if not root
    if path != FILE_ROOT and _within_root(path.parent.resolve(), FILE_ROOT):
        rows.append([InlineKeyboardButton("🗑️ Delete This Folder", callback_data="delreq_folder")])
    if path != FILE_ROOT:
        rows.append([InlineKeyboardButton("⬆️ Up", callback_data="del_up")])
    text = (
        f"Delete browser: {path}\n"
        f"Items: {len(entries)} (showing {offset + 1}-{min(offset + page_size, len(entries))})\n"
        "Tap ❌ to delete, folder to enter."
    )
    markup = InlineKeyboardMarkup(rows)
    if edit is not None:
        await edit.edit_message_text(text, reply_markup=markup)
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)

async def on_delete_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    if not query.from_user or query.from_user.id != OWNER_ID:
        await query.answer("Unauthorized", show_alert=True)
        return
    chat_id = query.message.chat_id
    data = query.data or ""
    st = BROWSE_STATE.get(chat_id)
    if not st:
        await query.edit_message_text("Browse state expired. Use /delete")
        return
    path = Path(st["path"])
    entries = [Path(p) for p in st["entries"]]
    offset = int(st.get("offset", 0))
    page_size = 12
    # Navigation
    if data == "del_prev":
        st["offset"] = max(0, offset - page_size)
        await _render_delete_dir_page(chat_id, context, query)
        return
    if data == "del_next":
        max_off = max(0, len(entries) - page_size)
        st["offset"] = min(max_off, offset + page_size)
        await _render_delete_dir_page(chat_id, context, query)
        return
    if data == "del_up":
        await _render_delete_dir(chat_id, raw_path=str(path.parent), edit=query, context=context)
        return
    if data.startswith("delnav:"):
        try:
            idx = int(data.split(":", 1)[1])
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text="Invalid selection.")
            return
        if idx < 0 or idx >= len(entries):
            await context.bot.send_message(chat_id=chat_id, text="Invalid selection index.")
            return
        chosen = entries[idx]
        if chosen.is_dir():
            await _render_delete_dir(chat_id, raw_path=str(chosen), edit=query, context=context)
        else:
            await query.answer()
        return
    # File/folder delete request
    if data.startswith("delreq:"):
        try:
            idx = int(data.split(":", 1)[1])
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text="Invalid selection.")
            return
        if idx < 0 or idx >= len(entries):
            await context.bot.send_message(chat_id=chat_id, text="Invalid selection index.")
            return
        chosen = entries[idx]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm Delete", callback_data=f"delconf:{idx}"),
             InlineKeyboardButton("❌ Cancel", callback_data="delcancel")]
        ])
        await query.edit_message_text(f"Confirm delete: {chosen}", reply_markup=kb)
        return
    if data == "delreq_folder":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm Delete Folder", callback_data="delconf_folder"),
             InlineKeyboardButton("❌ Cancel", callback_data="delcancel")]
        ])
        await query.edit_message_text(f"Confirm delete folder: {path}", reply_markup=kb)
        return
    if data == "delcancel":
        await _render_delete_dir_page(chat_id, context, query)
        return
    # Confirm file delete
    if data.startswith("delconf:"):
        try:
            idx = int(data.split(":", 1)[1])
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text="Invalid selection.")
            return
        if idx < 0 or idx >= len(entries):
            await context.bot.send_message(chat_id=chat_id, text="Invalid selection index.")
            return
        chosen = entries[idx]
        try:
            if chosen.is_file():
                chosen.unlink()
                await query.edit_message_text(f"Deleted file: {chosen}")
            elif chosen.is_dir():
                shutil.rmtree(chosen)
                await query.edit_message_text(f"Deleted folder: {chosen}")
            else:
                await query.edit_message_text(f"Not a file or folder: {chosen}")
        except Exception as e:
            await query.edit_message_text(f"Delete failed: {e}")
        await _render_delete_dir_page(chat_id, context, None)
        return
    # Confirm folder delete
    if data == "delconf_folder":
        try:
            shutil.rmtree(path)
            await query.edit_message_text(f"Deleted folder: {path}")
        except Exception as e:
            await query.edit_message_text(f"Delete failed: {e}")
        return
import argparse
import asyncio
import datetime
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("copilot-bridge")


def load_env(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def build_env() -> dict:
    here = Path(__file__).resolve().parent
    values = load_env(here / ".env")
    env = os.environ.copy()
    env.update(values)
    return env


def parse_cli_overrides(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--telegram-token")
    parser.add_argument("--owner-id")
    parser.add_argument("--copilot-mode", choices=["ask", "suggest", "explain"])
    parser.add_argument("--max-output-chars", type=int)
    parser.add_argument("--cli-timeout", type=int)
    parser.add_argument("--safe-mode", choices=["on", "off"])
    parser.add_argument("--file-root")
    parser.add_argument("--storage-root")
    parser.add_argument("--state-file")
    args, _ = parser.parse_known_args(argv)
    return {k: v for k, v in vars(args).items() if v is not None}


ENV = build_env()
TOKEN = ENV.get("TELEGRAM_TOKEN", "")
OWNER_ID = int(ENV.get("OWNER_ID", "0"))
COPILOT_MODE = ENV.get("COPILOT_MODE", "suggest").strip()
if COPILOT_MODE not in {"suggest", "explain"}:
    COPILOT_MODE = "suggest"
MAX_OUTPUT_CHARS = int(ENV.get("MAX_OUTPUT_CHARS", "12000"))
CLI_TIMEOUT = int(ENV.get("CLI_TIMEOUT", "180"))
SAFE_MODE = ENV.get("SAFE_MODE", "on").strip().lower() != "off"
FILE_ROOT = Path(ENV.get("FILE_ROOT", "/opt")).resolve()
STORAGE_ROOT = Path(ENV.get("STORAGE_ROOT", "/opt/copilot-bridge/storage")).resolve()
STATE_FILE = Path(ENV.get("STATE_FILE", "/opt/copilot-bridge/state.json")).resolve()
MAX_CONTEXT_TURNS = int(ENV.get("MAX_CONTEXT_TURNS", "8"))

LAST_RUN: dict[str, object] = {
    "request_id": "-",
    "at": "-",
    "mode": COPILOT_MODE,
    "rc": None,
    "duration_ms": None,
    "command": "-",
    "error": "",
}
STATS = {
    "total": 0,
    "success": 0,
    "failed": 0,
    "last_success_at": "-",
}
BROWSE_STATE: dict[int, dict] = {}
OUTBOX_STATE: dict[int, list[str]] = {}
LAST_SELECTED_FILE: dict[int, str] = {}
LAST_MEDIA: dict[int, list[dict]] = {}
TIMEOUT_CONTINUE_STATE: dict[int, dict] = {}
CHAT_STATE: dict[int, dict] = {}

MODEL_PRESETS = [
    "default",
    "gpt-5.2",
    "gpt-5",
    "gpt-4.1",
    "claude-sonnet-4",
    "gemini-2.0-flash",
]


def _extract_json_array(text: str) -> list[str]:
    s = (text or "").strip()
    if not s:
        return []
    start = s.find("[")
    end = s.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        payload = json.loads(s[start:end + 1])
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    out = []
    for item in payload:
        if isinstance(item, str):
            v = item.strip()
            if v:
                out.append(v)
    return out


def _extract_model_tokens(text: str) -> list[str]:
    # Parse model-like IDs from plain text, markdown bullets, or prose.
    s = (text or "").replace("`", " ")
    if not s.strip():
        return []
    pattern = r"\b[a-z][a-z0-9]*(?:[._-][a-z0-9]+)+\b"
    blocked = {
        "allow-all",
        "silent",
        "list-models",
        "list-available-models",
        "model-id",
    }
    found = []
    for m in re.findall(pattern, s.lower()):
        if m in blocked:
            continue
        # Keep likely model IDs only.
        if any(k in m for k in ["gpt", "claude", "gemini", "o1", "o3", "llama", "sonnet", "haiku", "flash", "pro"]):
            found.append(m)
    # Preserve order, remove duplicates.
    seen = set()
    out = []
    for item in found:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


async def _fetch_models_realtime() -> list[str]:
    # Strategy 1: ask for strict JSON array for machine-safe parsing.
    prompts = [
        "Return ONLY a JSON array of available Copilot model IDs for this account.",
        "List available model IDs only, one per line.",
        "list models",
    ]

    for p in prompts:
        try:
            rc, out, err = await run_cli(["copilot", "-p", p, "--allow-all", "--silent"])
            text = (out or "") + "\n" + (err or "")
            if rc not in {0, 124}:
                continue
            models = _extract_json_array(text)
            if not models:
                models = _extract_model_tokens(text)
            if models:
                return models
        except Exception:
            continue

    return []


HELP_TEXT = (
    "Copilot bridge commands:\n"
    "/mode [suggest|explain] - set default style\n"
    "/safe [on|off] - strict safety mode\n"
    "/health - runtime status and auth check\n"
    "/last - show last request details\n"
    "/limits - show current timeout/output limits\n"
    "/clear [all] - clear conversation memory (or all runtime state)\n"
    "/models - show realtime model buttons\n"
    "/modelsraw - show raw realtime model output\n"
    "/model [name] - view or set current model\n"
    "/files [path] - browse server directories and send files\n"
    "/findfile <name> - search stored files by name and send\n"
    "/sendlast - send last selected or last uploaded file\n"
    "/continue - continue last timed-out task\n"
    "/analyzeimage - analyze last image (metadata + OCR if available)\n"
    "/analyzeaudio - analyze/transcribe last audio if whisper exists\n"
    "/analyzevideo - lightweight video analysis (metadata + keyframes)\n"
    "/auth - show gh auth status\n"
    "/help - this help\n\n"
    "Any normal message is sent directly to Copilot CLI prompt mode.\n"
    "Use raw CLI style too, for example:\n"
    "copilot -p \"create nginx reverse proxy\" --allow-all --silent"
)


def sanitize(text: str) -> str:
    t = (text or "").replace("\r\n", "\n").strip()
    if not t:
        return "(empty output)"
    if len(t) > MAX_OUTPUT_CHARS:
        t = t[:MAX_OUTPUT_CHARS] + "\n\n... [truncated]"
    return t


def _utc_now() -> str:
    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def _is_raw_passthrough(msg: str) -> bool:
    return msg.startswith("copilot ") or msg.startswith("gh copilot ")


def _is_forbidden_in_safe_mode(msg: str) -> bool:
    lower = msg.lower()
    blocked = [
        "--allow-all",
        "--yolo",
        "--allow-all-tools",
        "--allow-all-paths",
        "--allow-all-urls",
        "--acp",
    ]
    return any(tok in lower for tok in blocked)


def _human_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    if size < 1024 * 1024:
        return f"{size/1024:.1f}KB"
    if size < 1024 * 1024 * 1024:
        return f"{size/(1024*1024):.1f}MB"
    return f"{size/(1024*1024*1024):.1f}GB"


def _within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _safe_browse_path(raw_path: str | None) -> Path:
    if not raw_path:
        return FILE_ROOT
    p = Path(raw_path)
    if not p.is_absolute():
        p = FILE_ROOT / p
    p = p.resolve()
    if not _within_root(p, FILE_ROOT):
        return FILE_ROOT
    return p


def _chunk_text(text: str, n: int = 3900) -> list[str]:
    return [text[i:i + n] for i in range(0, len(text), n)] or ["(empty output)"]


def _state_for(chat_id: int) -> dict:
    st = CHAT_STATE.setdefault(chat_id, {"history": [], "model": "default"})
    if "history" not in st:
        st["history"] = []
    if "model" not in st:
        st["model"] = "default"
    return st


def _save_state() -> None:
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = {"chats": {str(k): v for k, v in CHAT_STATE.items()}}
        STATE_FILE.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("state save failed: %s", e)


def _load_state() -> None:
    if not STATE_FILE.exists():
        return
    try:
        raw = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        chats = raw.get("chats", {}) if isinstance(raw, dict) else {}
        for k, v in chats.items():
            try:
                cid = int(k)
            except Exception:
                continue
            if isinstance(v, dict):
                CHAT_STATE[cid] = {
                    "history": list(v.get("history", []))[-(MAX_CONTEXT_TURNS * 2):],
                    "model": str(v.get("model", "default") or "default"),
                }
    except Exception as e:
        logger.warning("state load failed: %s", e)


def _append_history(chat_id: int, role: str, content: str) -> None:
    st = _state_for(chat_id)
    st["history"].append({"role": role, "content": content[:1600]})
    st["history"] = st["history"][-(MAX_CONTEXT_TURNS * 2):]
    _save_state()


def _build_contextual_prompt(chat_id: int, user_text: str, mode: str) -> str:
    st = _state_for(chat_id)
    history = st.get("history", [])[-(MAX_CONTEXT_TURNS * 2):]
    if not history:
        if mode == "explain":
            return f"Explain this clearly and practically:\n{user_text}"
        return user_text

    lines = ["Conversation context:"]
    for item in history:
        r = "User" if item.get("role") == "user" else "Assistant"
        lines.append(f"{r}: {item.get('content', '')}")
    lines.append("---")
    if mode == "explain":
        lines.append("Now explain the following clearly and continue context-aware:")
    else:
        lines.append("Continue this session context-aware and answer the latest user request:")
    lines.append(user_text)
    return "\n".join(lines)


def _get_chat_model(chat_id: int) -> str:
    return str(_state_for(chat_id).get("model", "default") or "default")


def _set_chat_model(chat_id: int, model: str) -> None:
    st = _state_for(chat_id)
    st["model"] = model.strip() if model.strip() else "default"
    _save_state()


def _last_media_item(chat_id: int) -> dict | None:
    items = LAST_MEDIA.get(chat_id) or []
    return items[0] if items else None


def _remember_media(chat_id: int, item: dict) -> None:
    arr = LAST_MEDIA.setdefault(chat_id, [])
    arr.insert(0, item)
    del arr[20:]


def _image_probe(path: Path) -> str:
    try:
        from PIL import Image

        with Image.open(path) as im:
            return f"Image: {im.format or 'unknown'} {im.width}x{im.height}"
    except Exception:
        return "Image received. Basic metadata only (install Pillow for dimensions)."


def _ocr_image(path: Path, max_chars: int = 800) -> str:
    if not shutil.which("tesseract"):
        return "OCR unavailable (tesseract not installed)."
    try:
        r = subprocess.run(
            ["tesseract", str(path), "stdout"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        text = (r.stdout or "").strip()
        if not text:
            return "OCR: no readable text detected."
        clipped = text[:max_chars]
        if len(text) > max_chars:
            clipped += "..."
        return f"OCR:\n{clipped}"
    except Exception as e:
        return f"OCR failed: {e}"


def _audio_probe(path: Path) -> str:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration,bit_rate",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
        if r.returncode == 0 and r.stdout.strip():
            lines = [x.strip() for x in r.stdout.splitlines() if x.strip()]
            dur = lines[0] if lines else "?"
            br = lines[1] if len(lines) > 1 else "?"
            return f"Audio metadata: duration={dur}s bitrate={br}"
    except Exception:
        pass
    return "Audio received and stored. Advanced transcription is not enabled yet."


def _transcribe_audio_if_available(path: Path, max_chars: int = 1200) -> str:
    """Best-effort transcription using whisper CLI if installed.
    This is optional and intentionally bounded for low-resource instances.
    """
    if not shutil.which("whisper"):
        return "Transcription unavailable (whisper CLI not installed)."
    out_dir = STORAGE_ROOT / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        cmd = [
            "whisper",
            str(path),
            "--model",
            "tiny",
            "--output_format",
            "txt",
            "--output_dir",
            str(out_dir),
            "--fp16",
            "False",
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            return f"Transcription failed: {err[:300]}"
        txt_path = out_dir / f"{path.stem}.txt"
        if not txt_path.exists():
            return "Transcription completed but output file not found."
        content = txt_path.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            return "Transcription completed but no text was extracted."
        clipped = content[:max_chars] + ("..." if len(content) > max_chars else "")
        return f"Transcript:\n{clipped}"
    except subprocess.TimeoutExpired:
        return "Transcription timed out (audio too long or CPU too slow)."
    except Exception as e:
        return f"Transcription error: {e}"


def _video_probe(path: Path) -> str:
    """Lightweight video analysis: metadata + sparse keyframe extraction.
    No heavy semantic inference by default.
    """
    meta = "Video received."
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            lines = [x.strip() for x in r.stdout.splitlines() if x.strip()]
            if len(lines) >= 4:
                w, h, fps, dur = lines[0], lines[1], lines[2], lines[3]
                meta = f"Video metadata: {w}x{h}, fps={fps}, duration={dur}s"
    except Exception:
        pass

    if not shutil.which("ffmpeg"):
        return meta + "\nKeyframe extraction unavailable (ffmpeg not installed)."

    frames_dir = STORAGE_ROOT / "video_frames" / path.stem
    frames_dir.mkdir(parents=True, exist_ok=True)
    frame_out = frames_dir / "frame_%02d.jpg"
    # 1 frame every 10 seconds, max 3 frames to stay lightweight.
    try:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(path),
            "-vf",
            "fps=1/10,scale=640:-1",
            "-frames:v",
            "3",
            str(frame_out),
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        frames = sorted(frames_dir.glob("frame_*.jpg"))
        if not frames:
            return meta + "\nNo frames extracted."
        ocr_notes = []
        for f in frames[:2]:
            ocr_notes.append(_ocr_image(f, max_chars=240))
        return (
            meta
            + f"\nExtracted {len(frames)} keyframes to {frames_dir}."
            + ("\n" + "\n".join(ocr_notes) if ocr_notes else "")
        )
    except Exception as e:
        return meta + f"\nKeyframe extraction failed: {e}"


def _find_files_by_query(query: str, limit: int = 8) -> list[Path]:
    q = (query or "").strip().lower()
    if not q:
        return []
    tokens = [t for t in re.split(r"\s+", q) if t]
    roots = [STORAGE_ROOT, FILE_ROOT]
    candidates: list[tuple[int, Path]] = []
    seen = 0
    max_scan = 3000
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if seen >= max_scan:
                break
            seen += 1
            if not p.is_file():
                continue
            name = p.name.lower()
            score = 0
            if q in name:
                score += 100
            for t in tokens:
                if t in name:
                    score += 20
            if score > 0:
                # Prefer storage files and recent changes slightly.
                if _within_root(p, STORAGE_ROOT):
                    score += 10
                try:
                    score += int(min(10, (time.time() - p.stat().st_mtime) // -86400 + 10))
                except Exception:
                    pass
                candidates.append((score, p))
        if seen >= max_scan:
            break
    candidates.sort(key=lambda x: (-x[0], x[1].name.lower()))
    return [p for _, p in candidates[:limit]]


async def run_cli(command: list[str]) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=CLI_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        stdout, stderr = await proc.communicate()
        out_t = stdout.decode(errors="replace") if stdout else ""
        err_t = stderr.decode(errors="replace") if stderr else ""
        err = f"Timed out after {CLI_TIMEOUT}s"
        if err_t.strip():
            err += f"\n{err_t.strip()[:800]}"
        return 124, out_t, err
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


def build_copilot_cmd(user_text: str, mode: str, model: str = "default") -> list[str]:
    msg = user_text.strip()

    # Backward compatibility for old gh-copilot style
    if msg.startswith("gh copilot "):
        msg = msg[len("gh copilot "):].strip()

    # Raw new-cli invocation passthrough
    if msg.startswith("copilot "):
        return shlex.split(msg)

    # New CLI uses prompt mode (-p). Map explain style via instruction prefix.
    m = mode if mode in {"suggest", "explain"} else "suggest"
    prompt = msg if m == "suggest" else f"Explain this command clearly: {msg}"
    cmd = ["copilot"]
    if model and model != "default":
        cmd.extend(["--model", model])
    cmd.extend(["-p", prompt, "--allow-all", "--silent"])
    return cmd


async def ensure_owner(update: Update) -> bool:
    if not update.effective_user or update.effective_user.id != OWNER_ID:
        if update.message:
            await update.message.reply_text("Unauthorized.")
        return False
    return True


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    await update.message.reply_text(HELP_TEXT)


async def cmd_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    global COPILOT_MODE
    if not context.args:
        await update.message.reply_text(f"Current mode: {COPILOT_MODE}")
        return
    mode = context.args[0].strip().lower()
    if mode not in {"suggest", "explain"}:
        await update.message.reply_text("Invalid mode. Use suggest or explain.")
        return
    COPILOT_MODE = mode
    await update.message.reply_text(f"Mode set to: {COPILOT_MODE}")


async def cmd_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    raw = " ".join(context.args).strip() if context.args else ""
    await _render_dir(update.effective_chat.id, raw_path=raw or None, edit=None, context=context)


async def cmd_sendlast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    chat_id = update.effective_chat.id
    path = LAST_SELECTED_FILE.get(chat_id)
    if not path:
        item = _last_media_item(chat_id)
        path = item.get("path") if item else None
    if not path:
        await update.message.reply_text("No file selected yet. Use /files or upload a file first.")
        return
    await _send_path_to_chat(chat_id, Path(path), context)


async def _run_continuation(chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    st = TIMEOUT_CONTINUE_STATE.get(chat_id)
    if not st:
        await context.bot.send_message(chat_id=chat_id, text="No timed-out task to continue.")
        return

    prev = (st.get("partial") or "")[-2000:]
    prompt = (
        "Continue the previous answer from where you stopped. "
        "Do not repeat content already provided.\n\n"
        f"Original request:\n{st.get('prompt', '')}\n\n"
        f"Already sent (tail):\n{prev}"
    )

    request_id = uuid.uuid4().hex[:8]
    model = _get_chat_model(chat_id)
    cmd = build_copilot_cmd(prompt, COPILOT_MODE, model)
    await context.bot.send_message(chat_id=chat_id, text=f"[{request_id}] Continuing timed-out task...")
    start = time.perf_counter()
    rc, out, err = await run_cli(cmd)
    duration_ms = int((time.perf_counter() - start) * 1000)

    LAST_RUN.update(
        {
            "request_id": request_id,
            "at": _utc_now(),
            "mode": COPILOT_MODE,
            "command": " ".join(cmd[:3]),
            "rc": rc,
            "duration_ms": duration_ms,
            "error": (err or "")[:1000],
        }
    )

    body = sanitize((out or "").strip())
    if rc == 124:
        st["attempts"] = int(st.get("attempts", 0)) + 1
        st["partial"] = (st.get("partial", "") + "\n" + body).strip()[-12000:]
        TIMEOUT_CONTINUE_STATE[chat_id] = st
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Continue ▶", callback_data="cont_task")]])
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"[{request_id}] Still timed out after {duration_ms}ms.",
            reply_markup=kb,
        )
        return

    if rc != 0:
        body = sanitize((out + "\n" + err).strip())
        body = f"[{request_id}] Continue failed (rc={rc}, {duration_ms}ms).\n{body}"
    else:
        body = f"[{request_id}] Continue completed ({duration_ms}ms).\n{body}"

    TIMEOUT_CONTINUE_STATE.pop(chat_id, None)
    _append_history(chat_id, "assistant", (out or "").strip()[:1600])
    for chunk in _chunk_text(body):
        await context.bot.send_message(chat_id=chat_id, text=chunk)


async def cmd_continue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    await _run_continuation(update.effective_chat.id, context)


async def cmd_findfile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    query = " ".join(context.args).strip()
    if not query:
        await update.message.reply_text("Usage: /findfile <name or part>")
        return
    matches = _find_files_by_query(query)
    if not matches:
        await update.message.reply_text(f"No file matched: {query}")
        return
    best = matches[0]
    LAST_SELECTED_FILE[update.effective_chat.id] = str(best)
    lines = [f"Matched {len(matches)} file(s). Sending best match:", str(best)]
    await update.message.reply_text("\n".join(lines))
    await _send_path_to_chat(update.effective_chat.id, best, context)


async def cmd_analyzeimage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    item = _last_media_item(update.effective_chat.id)
    if not item or item.get("kind") != "images":
        await update.message.reply_text("No recent image found. Upload an image first.")
        return
    p = Path(item["path"])
    lines = [
        f"Image file: {p}",
        _image_probe(p),
        _ocr_image(p),
    ]
    for chunk in _chunk_text("\n".join(lines)):
        await update.message.reply_text(chunk)


async def cmd_analyzeaudio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    item = _last_media_item(update.effective_chat.id)
    if not item or item.get("kind") not in {"audio", "voice"}:
        await update.message.reply_text("No recent audio/voice found. Upload one first.")
        return
    p = Path(item["path"])
    lines = [
        f"Audio file: {p}",
        _audio_probe(p),
        _transcribe_audio_if_available(p),
    ]
    for chunk in _chunk_text("\n".join(lines)):
        await update.message.reply_text(chunk)


async def cmd_analyzevideo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    item = _last_media_item(update.effective_chat.id)
    if not item or item.get("kind") != "video":
        await update.message.reply_text("No recent video found. Upload one first.")
        return
    p = Path(item["path"])
    text = f"Video file: {p}\n{_video_probe(p)}"
    for chunk in _chunk_text(text):
        await update.message.reply_text(chunk)


async def cmd_safe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    global SAFE_MODE
    if not context.args:
        await update.message.reply_text(f"Safe mode: {'on' if SAFE_MODE else 'off'}")
        return
    val = context.args[0].strip().lower()
    if val not in {"on", "off"}:
        await update.message.reply_text("Usage: /safe [on|off]")
        return
    SAFE_MODE = val == "on"
    await update.message.reply_text(f"Safe mode set to: {val}")


async def cmd_limits(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    lines = [
        "Bridge limits:",
        f"mode: {COPILOT_MODE}",
        f"safe_mode: {'on' if SAFE_MODE else 'off'}",
        f"current_model: {_get_chat_model(update.effective_chat.id)}",
        f"max_context_turns: {MAX_CONTEXT_TURNS}",
        f"timeout_seconds: {CLI_TIMEOUT}",
        f"max_output_chars: {MAX_OUTPUT_CHARS}",
        f"owner_id: {OWNER_ID}",
    ]
    await update.message.reply_text("\n".join(lines))


async def cmd_models(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    chat_id = update.effective_chat.id
    current = _get_chat_model(chat_id)
    # Run Copilot CLI to get available models
    try:
        models = await _fetch_models_realtime()
        source = "Copilot realtime"
        if not models:
            models = list(MODEL_PRESETS)
            source = "fallback presets"

        rows = []
        for m in models:
            label = f"✅ {m}" if m == current else m
            rows.append([InlineKeyboardButton(label, callback_data=f"modelsel:{m}")])

        await update.message.reply_text(
            f"Available models ({source}, current: {current}):",
            reply_markup=InlineKeyboardMarkup(rows)
        )
    except Exception as e:
        rows = [[InlineKeyboardButton(m, callback_data=f"modelsel:{m}")] for m in MODEL_PRESETS]
        await update.message.reply_text(
            f"Models fetch failed: {e}\nShowing presets (current: {current}).",
            reply_markup=InlineKeyboardMarkup(rows)
        )


async def cmd_modelsraw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    rc, out, err = await run_cli([
        "copilot",
        "-p",
        "Return ONLY a JSON array of available Copilot model IDs for this account.",
        "--allow-all",
        "--silent",
    ])
    text = f"rc={rc}\n\nSTDOUT:\n{out or '(empty)'}\n\nSTDERR:\n{err or '(empty)'}"
    for chunk in _chunk_text(text):
        await update.message.reply_text(chunk)
async def on_model_select_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    if not query.from_user or query.from_user.id != OWNER_ID:
        await query.answer("Unauthorized", show_alert=True)
        return
    chat_id = query.message.chat_id
    data = query.data or ""
    if not data.startswith("modelsel:"):
        await query.answer()
        return
    model = data.split(":", 1)[1]
    _set_chat_model(chat_id, model)
    await query.answer(f"Model set to: {model}", show_alert=True)
    await query.edit_message_text(f"Model switched to: {model}")


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_text(f"Current model: {_get_chat_model(chat_id)}")
        return
    new_model = " ".join(context.args).strip()
    _set_chat_model(chat_id, new_model)
    await update.message.reply_text(f"Model set to: {_get_chat_model(chat_id)}")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    chat_id = update.effective_chat.id
    hard = bool(context.args and context.args[0].strip().lower() == "all")

    st = _state_for(chat_id)
    st["history"] = []
    _save_state()

    if hard:
        OUTBOX_STATE.pop(chat_id, None)
        LAST_SELECTED_FILE.pop(chat_id, None)
        LAST_MEDIA.pop(chat_id, None)
        BROWSE_STATE.pop(chat_id, None)
        TIMEOUT_CONTINUE_STATE.pop(chat_id, None)
        st["model"] = "default"
        _save_state()
        await update.message.reply_text("Cleared conversation memory and chat runtime state.")
    else:
        await update.message.reply_text("Cleared conversation memory for this chat.")


async def cmd_auth(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    rc, out, err = await run_cli(["gh", "auth", "status"])
    txt = sanitize((out + "\n" + err).strip())
    await update.message.reply_text(f"rc={rc}\n{txt}")


async def cmd_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    lines = [
        "Last request:",
        f"request_id: {LAST_RUN.get('request_id', '-')}",
        f"at: {LAST_RUN.get('at', '-')}",
        f"mode: {LAST_RUN.get('mode', '-')}",
        f"rc: {LAST_RUN.get('rc', '-')}",
        f"duration_ms: {LAST_RUN.get('duration_ms', '-')}",
        f"command: {LAST_RUN.get('command', '-')}",
    ]
    err = str(LAST_RUN.get("error", "") or "").strip()
    if err:
        lines.append(f"error: {err[:300]}")
    await update.message.reply_text("\n".join(lines))


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    rc, out, err = await run_cli(["gh", "auth", "status"])
    auth_ok = rc == 0
    lines = [
        "Bridge health:",
        f"status: ok",
        f"auth: {'ok' if auth_ok else 'failed'}",
        f"mode: {COPILOT_MODE}",
        f"safe_mode: {'on' if SAFE_MODE else 'off'}",
        f"requests: total={STATS['total']} success={STATS['success']} failed={STATS['failed']}",
        f"last_success_at: {STATS['last_success_at']}",
        f"last_request_id: {LAST_RUN.get('request_id', '-')}",
    ]
    if not auth_ok:
        lines.append(f"auth_error: {sanitize((out + ' ' + err).strip())[:400]}")
    await update.message.reply_text("\n".join(lines))


async def _send_path_to_chat(chat_id: int, path: Path, context: ContextTypes.DEFAULT_TYPE):
    if not path.exists() or not path.is_file():
        await context.bot.send_message(chat_id=chat_id, text=f"File not found: {path}")
        return
    size = path.stat().st_size
    caption = f"{path}\n{_human_size(size)}"
    try:
        with open(path, "rb") as f:
            await context.bot.send_document(chat_id=chat_id, document=f, caption=caption[:1024])
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"Failed to send file: {e}")


async def _render_dir(chat_id: int, raw_path: str | None, edit, context: ContextTypes.DEFAULT_TYPE):
    path = _safe_browse_path(raw_path)
    if not path.exists() or not path.is_dir():
        await context.bot.send_message(chat_id=chat_id, text=f"Directory not found: {path}")
        return

    entries = sorted(list(path.iterdir()), key=lambda p: (not p.is_dir(), p.name.lower()))
    BROWSE_STATE[chat_id] = {"path": str(path), "entries": [str(p) for p in entries], "offset": 0}
    await _render_dir_page(chat_id, context, edit)


async def _render_dir_page(chat_id: int, context: ContextTypes.DEFAULT_TYPE, edit):
    st = BROWSE_STATE.get(chat_id)
    if not st:
        await context.bot.send_message(chat_id=chat_id, text="No browse state. Run /files")
        return
    path = Path(st["path"])
    entries = [Path(p) for p in st["entries"]]
    offset = int(st.get("offset", 0))
    page_size = 12
    page = entries[offset:offset + page_size]

    rows = []
    for i, p in enumerate(page):
        idx = offset + i
        icon = "📁" if p.is_dir() else "📄"
        rows.append([InlineKeyboardButton(f"{icon} {p.name}", callback_data=f"fs:{idx}")])

    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅ Prev", callback_data="fs_prev"))
    if offset + page_size < len(entries):
        nav.append(InlineKeyboardButton("Next ➡", callback_data="fs_next"))
    if nav:
        rows.append(nav)

    if path != FILE_ROOT and _within_root(path.parent.resolve(), FILE_ROOT):
        rows.append([InlineKeyboardButton("⬆ Up", callback_data="fs_up")])

    rows.append([InlineKeyboardButton("📤 Send Last Selected", callback_data="fs_sendlast")])

    text = (
        f"Browse: {path}\n"
        f"Items: {len(entries)} (showing {offset + 1}-{min(offset + page_size, len(entries))})\n"
        "Tap folder to open. Tap file to send."
    )
    markup = InlineKeyboardMarkup(rows)
    if edit is not None:
        await edit.edit_message_text(text, reply_markup=markup)
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup)


async def on_file_browser_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    if not query.from_user or query.from_user.id != OWNER_ID:
        await query.answer("Unauthorized", show_alert=True)
        return
    chat_id = query.message.chat_id
    data = query.data or ""
    await query.answer()

    st = BROWSE_STATE.get(chat_id)
    if not st:
        await query.edit_message_text("Browse state expired. Use /files")
        return

    if data == "fs_prev":
        st["offset"] = max(0, int(st.get("offset", 0)) - 12)
        await _render_dir_page(chat_id, context, query)
        return
    if data == "fs_next":
        max_off = max(0, len(st["entries"]) - 12)
        st["offset"] = min(max_off, int(st.get("offset", 0)) + 12)
        await _render_dir_page(chat_id, context, query)
        return
    if data == "fs_up":
        await _render_dir(chat_id, raw_path=str(Path(st["path"]).parent), edit=query, context=context)
        return
    if data == "fs_sendlast":
        path = LAST_SELECTED_FILE.get(chat_id)
        if not path:
            item = _last_media_item(chat_id)
            path = item.get("path") if item else None
        if not path:
            await context.bot.send_message(chat_id=chat_id, text="No last file available.")
            return
        await _send_path_to_chat(chat_id, Path(path), context)
        return

    if data.startswith("fs:"):
        try:
            idx = int(data.split(":", 1)[1])
        except Exception:
            await context.bot.send_message(chat_id=chat_id, text="Invalid selection.")
            return
        entries = st["entries"]
        if idx < 0 or idx >= len(entries):
            await context.bot.send_message(chat_id=chat_id, text="Invalid selection index.")
            return
        chosen = Path(entries[idx])
        if chosen.is_dir():
            await _render_dir(chat_id, raw_path=str(chosen), edit=query, context=context)
            return
        LAST_SELECTED_FILE[chat_id] = str(chosen)
        await _send_path_to_chat(chat_id, chosen, context)


async def on_continue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    if not query.from_user or query.from_user.id != OWNER_ID:
        await query.answer("Unauthorized", show_alert=True)
        return
    chat_id = query.message.chat_id
    await query.answer()
    chunks = OUTBOX_STATE.get(chat_id) or []
    if not chunks:
        await query.edit_message_reply_markup(reply_markup=None)
        return
    next_chunk = chunks.pop(0)
    if chunks:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Continue ▶", callback_data="cont_out")]])
        await context.bot.send_message(chat_id=chat_id, text=next_chunk, reply_markup=kb)
    else:
        await context.bot.send_message(chat_id=chat_id, text=next_chunk)
    OUTBOX_STATE[chat_id] = chunks


async def on_task_continue_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    if not query.from_user or query.from_user.id != OWNER_ID:
        await query.answer("Unauthorized", show_alert=True)
        return
    chat_id = query.message.chat_id
    await query.answer()
    await _run_continuation(chat_id, context)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return
    text = (update.message.text or "").strip()
    if not text:
        return

    if SAFE_MODE:
        if _is_raw_passthrough(text):
            await update.message.reply_text(
                "Safe mode is ON: raw copilot command passthrough is disabled. "
                "Send plain text prompt, or run /safe off."
            )
            return
        if _is_forbidden_in_safe_mode(text):
            await update.message.reply_text(
                "Safe mode blocked this prompt due to high-risk flags. "
                "Run /safe off if you want unrestricted passthrough."
            )
            return

    request_id = uuid.uuid4().hex[:8]
    chat_id = update.effective_chat.id
    model = _get_chat_model(chat_id)
    prompt = _build_contextual_prompt(chat_id, text, COPILOT_MODE)
    _append_history(chat_id, "user", text)
    cmd = build_copilot_cmd(prompt, COPILOT_MODE, model)
    preview = " ".join(cmd[:3])
    LAST_RUN.update(
        {
            "request_id": request_id,
            "at": _utc_now(),
            "mode": COPILOT_MODE,
            "command": preview,
            "rc": None,
            "duration_ms": None,
            "error": "",
        }
    )
    STATS["total"] += 1
    await update.message.reply_text(f"[{request_id}] Running: {' '.join(cmd[:2])} ...")
    start = time.perf_counter()
    rc, out, err = await run_cli(cmd)
    duration_ms = int((time.perf_counter() - start) * 1000)

    LAST_RUN["rc"] = rc
    LAST_RUN["duration_ms"] = duration_ms
    LAST_RUN["error"] = (err or "")[:1000]
    if rc == 0:
        STATS["success"] += 1
        STATS["last_success_at"] = _utc_now()
    else:
        STATS["failed"] += 1

    logger.info(
        "request_id=%s rc=%s duration_ms=%s mode=%s cmd=%s",
        request_id,
        rc,
        duration_ms,
        COPILOT_MODE,
        preview,
    )

    body = sanitize((out or "").strip())
    if rc != 0:
        body = sanitize((out + "\n" + err).strip())
        body = f"[{request_id}] Command failed (rc={rc}, {duration_ms}ms).\n{body}"
        if rc == 124:
            TIMEOUT_CONTINUE_STATE[chat_id] = {
                "prompt": text,
                "partial": (out or "")[-6000:],
                "attempts": 0,
                "created_at": _utc_now(),
            }
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("Continue ▶", callback_data="cont_task")]])
            await update.message.reply_text(
                f"[{request_id}] Task timed out after {duration_ms}ms. Tap Continue to keep going.",
                reply_markup=kb,
            )
    else:
        body = f"[{request_id}] ({duration_ms}ms)\n{body}"
        _append_history(chat_id, "assistant", (out or "").strip()[:1600])

    chunks = _chunk_text(body)
    first = chunks.pop(0)
    if chunks:
        OUTBOX_STATE[chat_id] = chunks
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("Continue ▶", callback_data="cont_out")]])
        await update.message.reply_text(first, reply_markup=kb)
    else:
        await update.message.reply_text(first)


async def handle_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await ensure_owner(update):
        return

    msg = update.message
    chat_id = update.effective_chat.id
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)

    tg_file = None
    media_kind = "unknown"
    ext = ""
    original_name = None

    if msg.photo:
        tg_file = await msg.photo[-1].get_file()
        media_kind, ext = "images", ".jpg"
    elif msg.voice:
        tg_file = await msg.voice.get_file()
        media_kind, ext = "voice", ".ogg"
    elif msg.audio:
        tg_file = await msg.audio.get_file()
        media_kind, ext = "audio", ".mp3"
        original_name = msg.audio.file_name
    elif msg.video:
        tg_file = await msg.video.get_file()
        media_kind, ext = "video", ".mp4"
        original_name = msg.video.file_name
    elif msg.document:
        tg_file = await msg.document.get_file()
        media_kind = "documents"
        original_name = msg.document.file_name
        ext = Path(original_name).suffix if original_name else ""
    elif msg.sticker:
        tg_file = await msg.sticker.get_file()
        media_kind, ext = "stickers", ".webp"

    if not tg_file:
        await msg.reply_text("Unsupported media type.")
        return

    ts = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    day = datetime.datetime.utcnow().strftime("%Y%m%d")
    folder = STORAGE_ROOT / media_kind / day
    folder.mkdir(parents=True, exist_ok=True)
    fname = original_name or f"{media_kind}_{ts}{ext}"
    save_path = folder / fname
    await tg_file.download_to_drive(str(save_path))

    stat = save_path.stat()
    LAST_SELECTED_FILE[chat_id] = str(save_path)
    _remember_media(
        chat_id,
        {
            "path": str(save_path),
            "kind": media_kind,
            "size": stat.st_size,
            "ts": _utc_now(),
        },
    )

    notes = [
        f"Stored: {save_path}",
        f"Type: {media_kind}",
        f"Size: {_human_size(stat.st_size)}",
    ]
    if media_kind == "images":
        notes.append(_image_probe(save_path))
        notes.append(_ocr_image(save_path, max_chars=280))
    elif media_kind in {"audio", "voice"}:
        notes.append(_audio_probe(save_path))
        notes.append(_transcribe_audio_if_available(save_path, max_chars=280))
    elif media_kind == "video":
        notes.append(_video_probe(save_path))
    else:
        notes.append("File stored and ready for follow-up tasks.")

    await msg.reply_text("\n".join(notes))


async def main() -> None:
    global ENV, TOKEN, OWNER_ID, COPILOT_MODE, MAX_OUTPUT_CHARS, CLI_TIMEOUT, SAFE_MODE, FILE_ROOT, STORAGE_ROOT, STATE_FILE

    overrides = parse_cli_overrides(sys.argv[1:])
    env_file = Path(overrides.get("env_file", ".env"))
    if not env_file.is_absolute():
        env_file = Path(__file__).resolve().parent / env_file
    ENV = build_env()
    ENV.update(load_env(env_file))
    if overrides.get("telegram_token"):
        ENV["TELEGRAM_TOKEN"] = str(overrides["telegram_token"])
    if overrides.get("owner_id"):
        ENV["OWNER_ID"] = str(overrides["owner_id"])
    if overrides.get("copilot_mode"):
        ENV["COPILOT_MODE"] = str(overrides["copilot_mode"])
    if overrides.get("max_output_chars") is not None:
        ENV["MAX_OUTPUT_CHARS"] = str(overrides["max_output_chars"])
    if overrides.get("cli_timeout") is not None:
        ENV["CLI_TIMEOUT"] = str(overrides["cli_timeout"])
    if overrides.get("safe_mode"):
        ENV["SAFE_MODE"] = str(overrides["safe_mode"])
    if overrides.get("file_root"):
        ENV["FILE_ROOT"] = str(overrides["file_root"])
    if overrides.get("storage_root"):
        ENV["STORAGE_ROOT"] = str(overrides["storage_root"])
    if overrides.get("state_file"):
        ENV["STATE_FILE"] = str(overrides["state_file"])

    TOKEN = ENV.get("TELEGRAM_TOKEN", "")
    OWNER_ID = int(ENV.get("OWNER_ID", "0"))
    COPILOT_MODE = ENV.get("COPILOT_MODE", "suggest").strip()
    if COPILOT_MODE not in {"suggest", "explain"}:
        COPILOT_MODE = "suggest"
    MAX_OUTPUT_CHARS = int(ENV.get("MAX_OUTPUT_CHARS", "12000"))
    CLI_TIMEOUT = int(ENV.get("CLI_TIMEOUT", "180"))
    SAFE_MODE = ENV.get("SAFE_MODE", "on").strip().lower() != "off"
    FILE_ROOT = Path(ENV.get("FILE_ROOT", "/opt")).resolve()
    STORAGE_ROOT = Path(ENV.get("STORAGE_ROOT", "/opt/copilot-bridge/storage")).resolve()
    STATE_FILE = Path(ENV.get("STATE_FILE", "/opt/copilot-bridge/state.json")).resolve()

    if not TOKEN or OWNER_ID == 0:
        raise RuntimeError("Missing TELEGRAM_TOKEN or OWNER_ID in .env")

    _load_state()

    app = Application.builder().token(TOKEN).concurrent_updates(True).build()
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("mode", cmd_mode))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(CommandHandler("modelsraw", cmd_modelsraw))
    app.add_handler(CallbackQueryHandler(on_model_select_callback, pattern=r"^modelsel:"))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("safe", cmd_safe))
    app.add_handler(CommandHandler("limits", cmd_limits))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("last", cmd_last))
    app.add_handler(CommandHandler("files", cmd_files))
    app.add_handler(CommandHandler("delete", cmd_delete))
    app.add_handler(CallbackQueryHandler(on_delete_callback, pattern=r"^del"))
    app.add_handler(CommandHandler("findfile", cmd_findfile))
    app.add_handler(CommandHandler("sendlast", cmd_sendlast))
    app.add_handler(CommandHandler("continue", cmd_continue))
    app.add_handler(CommandHandler("analyzeimage", cmd_analyzeimage))
    app.add_handler(CommandHandler("analyzeaudio", cmd_analyzeaudio))
    app.add_handler(CommandHandler("analyzevideo", cmd_analyzevideo))
    app.add_handler(CommandHandler("auth", cmd_auth))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    media_filter = (
        filters.PHOTO
        | filters.VOICE
        | filters.AUDIO
        | filters.VIDEO
        | filters.Document.ALL
        | filters.Sticker.ALL
    )
    app.add_handler(MessageHandler(media_filter, handle_media))
    app.add_handler(CallbackQueryHandler(on_file_browser_callback, pattern=r"^fs"))
    app.add_handler(CallbackQueryHandler(on_continue_callback, pattern=r"^cont_out$"))
    app.add_handler(CallbackQueryHandler(on_task_continue_callback, pattern=r"^cont_task$"))
    logger.info(
        "bridge starting owner_id=%s mode=%s safe_mode=%s timeout=%ss max_output=%s",
        OWNER_ID,
        COPILOT_MODE,
        SAFE_MODE,
        CLI_TIMEOUT,
        MAX_OUTPUT_CHARS,
    )
    await app.bot.set_my_commands(
        [
            BotCommand("help", "Show help"),
            BotCommand("mode", "Set default mode: suggest/explain"),
            BotCommand("models", "List realtime models as buttons"),
            BotCommand("modelsraw", "Show raw realtime model output"),
            BotCommand("model", "Get/set model for this chat"),
            BotCommand("clear", "Clear memory (/clear all for hard reset)"),
            BotCommand("safe", "Set safety mode on/off"),
            BotCommand("health", "Bridge health and auth"),
            BotCommand("last", "Show last request details"),
            BotCommand("limits", "Show limits and configuration"),
            BotCommand("files", "Browse files and send from server"),
            BotCommand("findfile", "Find file by name and send"),
            BotCommand("sendlast", "Send last selected/uploaded file"),
            BotCommand("continue", "Continue last timed-out task"),
            BotCommand("analyzeimage", "Analyze last image"),
            BotCommand("analyzeaudio", "Analyze/transcribe last audio"),
            BotCommand("analyzevideo", "Lightweight video analysis"),
            BotCommand("auth", "Show gh auth status"),
        ]
    )
    await app.initialize()
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True)
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
