"""Command line: `sentinel plugins list` and `sentinel plugins reload`."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from sentinel.config import Settings

STATUS = {
    "loaded": "loaded",
    "pending_approval": "waiting for approval",
    "disabled": "disabled",
    "error": "ERROR",
}


def _print(infos: list[dict]) -> None:
    if not infos:
        print("No defense commands found.")
        return
    for info in infos:
        pin = " + PIN" if info.get("requires_pin") else ""
        risk = info.get("risk_level") or "-"
        line = f"{info['name']:<20} {STATUS.get(info['status'], info['status']):<22} "
        line += f"{info['origin']:<8} {risk}{pin}"
        print(line)
        if info.get("error"):
            print(f"    -> {info['error']}")
        if info["status"] == "pending_approval":
            print(f"    -> {info['file']} (SHA-256 {info['sha256'][:16]}...) approve in the app")


def _running_backend(config: Settings, path: str) -> list[dict] | None:
    """Talk to a running backend (only if it was started standalone)."""
    session = config.data_dir / "session.json"
    if not session.is_file():
        return None
    data = json.loads(session.read_text(encoding="utf-8"))
    request = urllib.request.Request(
        f"http://127.0.0.1:{int(data['port'])}{path}",
        method="POST",
        headers={"Authorization": f"Bearer {data['token']}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _app_running(data_dir) -> bool:  # type: ignore[no-untyped-def]
    """True (with a notice) if SENTINEL is running - then do not write to the database."""
    from sentinel.db import InstanceLock

    if InstanceLock(data_dir).held_by_other():
        print(
            "SENTINEL is running. Please quit it first (tray icon -> Quit),\n"
            "otherwise the database may get corrupted.",
            file=sys.stderr,
        )
        return True
    return False


def plugins(args: list[str]) -> int:
    from sentinel.config import get_settings
    from sentinel.services import build_services

    command = args[0] if args else "list"
    if command not in ("list", "reload"):
        print("Usage: sentinel plugins [list|reload]", file=sys.stderr)
        return 2
    config = get_settings()
    if command == "reload":
        result = _running_backend(config, "/response/plugins/reload")
        if result is not None:
            print("Running backend reloaded the plugins:")
            _print(result)
            return 0
        print(
            "No standalone backend found. In the app: Settings > "
            "Defense commands > Reload. File check:"
        )
    svc = build_services(config)
    try:
        _print(svc.responses.list())
    finally:
        svc.db.close()
    return 0


def gmail(args: list[str]) -> int:
    """Connect Gmail: prompt for the app password (hidden) and store it in the Windows vault."""
    import getpass

    from sentinel.config import get_settings
    from sentinel.mail.service import MailError
    from sentinel.services import build_services

    command = args[0] if args else "status"
    if command not in ("login", "logout", "status"):
        print("Usage: sentinel gmail [login|logout|status]", file=sys.stderr)
        return 2
    if _app_running(get_settings().data_dir):
        return 1
    svc = build_services(get_settings())
    try:
        if command == "status":
            print(f"connected: {svc.mail.address}" if svc.mail.configured else "not connected")
            return 0
        if command == "logout":
            svc.mail.remove_login()
            print("Gmail disconnected, app password removed from the Windows vault.")
            return 0
        print("Create an app password: https://myaccount.google.com/apppasswords")
        print("(requires 2-step verification; it is only used read-only)")
        address = input("Gmail address: ").strip()
        password = getpass.getpass("App password (16 letters, input hidden): ")
        try:
            svc.mail.save_login(address, password)
        except MailError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print("Connected. The app password is in the Windows Credential Manager.")
        print("Restart SENTINEL so it reads the mail.")
        return 0
    finally:
        svc.db.close()


def gemini(args: list[str]) -> int:
    """Prompt for the Gemini key (hidden), store it in the Windows vault and test it."""
    import asyncio
    import getpass

    from sentinel.config import get_settings
    from sentinel.llm import factory
    from sentinel.llm.base import LLMError
    from sentinel.llm.gemini_provider import GeminiProvider

    command = args[0] if args else "status"
    if command not in ("login", "logout", "status", "test"):
        print("Usage: sentinel gemini [login|logout|status|test]", file=sys.stderr)
        return 2
    config = get_settings()
    if command == "logout":
        import contextlib

        with contextlib.suppress(Exception):  # was already deleted
            factory._keyring().delete_password(*factory.GEMINI_KEYRING)
        print("Gemini key removed from the Windows vault.")
        return 0
    if command == "status":
        print("Key present" if factory.gemini_key(config) else "no key")
        print(f"Model: {config.gemini_model}")
        return 0
    if command == "login":
        print("Key from Google AI Studio: https://aistudio.google.com/apikey")
        key = getpass.getpass("Gemini key (input hidden): ").strip()
        if len(key) < 20:
            print("That does not look like a key.", file=sys.stderr)
            return 1
        factory._keyring().set_password(*factory.GEMINI_KEYRING, key)
        print("Saved in the Windows Credential Manager. Testing now ...")
    key = factory.gemini_key(config)
    if not key:
        print("No key - run first: sentinel gemini login", file=sys.stderr)
        return 1

    async def check() -> int:
        provider = GeminiProvider(api_key=key, model=config.gemini_model)
        try:
            models = await provider.list_models()
            print(f"Key works, {len(models)} models available.")
            result = await provider.complete(system="Answer briefly.", prompt="Just say: OK")
        except LLMError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"Reply from {config.gemini_model}: {result.text.strip()[:80]}")
        print("Done. Restart SENTINEL and Jarvis will be online.")
        return 0

    return asyncio.run(check())


CLAUDE_MODELS = ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1")


def claude(args: list[str]) -> int:
    """Prompt for the Claude API key (hidden), store it in the Windows vault and test it."""
    import asyncio
    import contextlib
    import getpass
    import time

    from sentinel.config import get_settings
    from sentinel.llm import factory
    from sentinel.llm.base import LLMError

    command = args[0] if args else "status"
    if command not in ("login", "logout", "status", "test", "model"):
        print("Usage: sentinel claude [login|logout|status|test|model <id>]", file=sys.stderr)
        return 2
    config = get_settings()
    if command == "model":
        if len(args) < 2 or args[1] not in CLAUDE_MODELS:
            print(f"Current model: {factory.claude_model(config)}")
            print("Switch: sentinel claude model <id> - " + ", ".join(CLAUDE_MODELS))
            return 0 if len(args) < 2 else 1
        factory.save_model(config, args[1], name="claude_model")
        print(f"Model saved: {args[1]}. Restart SENTINEL.")
        return 0
    if command == "logout":
        with contextlib.suppress(Exception):  # was already deleted
            factory._keyring().delete_password(*factory.CLAUDE_KEYRING)
        print("Claude key removed from the Windows vault.")
        return 0
    if command == "status":
        print("Key present" if factory.claude_key(config) else "no key")
        print(f"Model: {factory.claude_model(config)}")
        return 0
    if command == "login":
        print("Key from https://console.anthropic.com/settings/keys (starts with sk-ant-)")
        key = getpass.getpass("Claude key (input hidden): ").strip()
        if not key.startswith("sk-ant-") or len(key) < 30:
            print("That does not look like a Claude key (sk-ant-...).", file=sys.stderr)
            return 1
        factory._keyring().set_password(*factory.CLAUDE_KEYRING, key)
        print("Saved in the Windows Credential Manager. Testing now ...")
    key = factory.claude_key(config)
    if not key:
        print("No key - run first: sentinel claude login", file=sys.stderr)
        return 1

    async def check() -> int:
        from sentinel.llm.anthropic_provider import AnthropicProvider

        model = factory.claude_model(config)
        provider = AnthropicProvider(api_key=key, model=model, effort=config.llm_effort)
        started = time.time()
        try:
            result = await provider.complete(system="Answer briefly.", prompt="Just say: OK")
        except LLMError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"Reply from {model} in {time.time() - started:.1f} s: {result.text.strip()[:80]}")
        print("Done. Restart SENTINEL and Jarvis will use Claude.")
        return 0

    return asyncio.run(check())


def openrouter(args: list[str]) -> int:
    """Prompt for the OpenRouter key (hidden), store it in the Windows vault and test it."""
    import asyncio
    import contextlib
    import getpass
    import time

    from sentinel.config import get_settings
    from sentinel.llm import factory
    from sentinel.llm.base import LLMError
    from sentinel.llm.openrouter_provider import OpenRouterProvider

    command = args[0] if args else "status"
    if command not in ("login", "logout", "status", "test", "model"):
        print("Usage: sentinel openrouter [login|logout|status|test|model <id>]", file=sys.stderr)
        return 2
    config = get_settings()
    if command == "model":
        if len(args) < 2:
            print(f"Current model: {factory.chosen_model(config)}")
            print("Switch: sentinel openrouter model <id>, e.g. minimax/minimax-m3.1-flash:free")
            return 0
        model = args[1].strip()
        if not model or len(model) > 100 or " " in model:
            print("Invalid model ID.", file=sys.stderr)
            return 1
        probe = OpenRouterProvider(api_key="", model=model)
        try:
            exists = asyncio.run(probe.check_model())
        except LLMError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        if not exists:
            print(
                f"{model} does not exist on OpenRouter (or has no tool support).", file=sys.stderr
            )
            return 1
        factory.save_model(config, model)
        print(f"Model saved: {model}. Restart SENTINEL.")
        return 0
    if command == "logout":
        with contextlib.suppress(Exception):  # was already deleted
            factory._keyring().delete_password(*factory.OPENROUTER_KEYRING)
        print("OpenRouter key removed from the Windows vault.")
        return 0
    if command == "status":
        print("Key present" if factory.openrouter_key(config) else "no key")
        print(f"Model: {factory.chosen_model(config)}")
        return 0
    if command == "login":
        print("Key from https://openrouter.ai/settings/keys (starts with sk-or-)")
        key = getpass.getpass("OpenRouter key (input hidden): ").strip()
        if not key.startswith("sk-or-") or len(key) < 20:
            print("That does not look like an OpenRouter key (sk-or-...).", file=sys.stderr)
            return 1
        factory._keyring().set_password(*factory.OPENROUTER_KEYRING, key)
        print("Saved in the Windows Credential Manager. Testing now ...")
    key = factory.openrouter_key(config)
    if not key:
        print("No key - run first: sentinel openrouter login", file=sys.stderr)
        return 1

    async def check() -> int:
        model = factory.chosen_model(config)
        provider = OpenRouterProvider(api_key=key, model=model)
        try:
            if not await provider.check_model():
                print(f"Model {model} does not exist on OpenRouter.")
                return 1
            started = time.time()
            result = await provider.complete(system="Answer briefly.", prompt="Just say: OK")
        except LLMError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        took = time.time() - started
        print(f"Reply from {model} in {took:.1f} s: {result.text.strip()[:80]}")
        print("Done. Restart SENTINEL and Jarvis will use OpenRouter.")
        return 0

    return asyncio.run(check())


def elevenlabs(args: list[str]) -> int:
    """Store the ElevenLabs key (hidden), choose a voice and test it."""
    import asyncio
    import contextlib
    import getpass
    import time

    from sentinel.config import get_settings
    from sentinel.voice import elevenlabs as el

    command = args[0] if args else "status"
    if command not in ("login", "logout", "status", "test", "voices", "voice"):
        print(
            "Usage: sentinel elevenlabs [login|logout|status|test|voices|voice <id>]",
            file=sys.stderr,
        )
        return 2
    config = get_settings()
    voice = el.ElevenLabsVoice(config.config_dir)
    if command == "logout":
        with contextlib.suppress(Exception):  # was already deleted
            el._keyring().delete_password(*el.KEYRING)
        print("ElevenLabs key removed from the Windows vault.")
        return 0
    if command == "status":
        print("Key present" if voice.configured else "no key")
        print(f"Voice: {voice.voice_id} - Model: {voice.model}")
        return 0
    if command == "voice":
        if len(args) < 2 or not args[1].isalnum():
            print(
                "Usage: sentinel elevenlabs voice <id>  (IDs: sentinel elevenlabs voices)",
                file=sys.stderr,
            )
            return 1
        el.save_choice(config.config_dir, voice_id=args[1])
        print(f"Voice saved: {args[1]}. Test it now: sentinel elevenlabs test")
        return 0
    if command == "login":
        print("Key from https://elevenlabs.io/app/settings/api-keys")
        key = getpass.getpass("ElevenLabs key (input hidden): ").strip()
        if len(key) < 20:
            print("That does not look like a key.", file=sys.stderr)
            return 1
        el._keyring().set_password(*el.KEYRING, key)
        print("Saved in the Windows Credential Manager. Testing now ...")
    if not voice.configured:
        print("No key - run first: sentinel elevenlabs login", file=sys.stderr)
        return 1

    async def check() -> int:
        try:
            if command == "voices":
                for item in await voice.voices():
                    print(f"{item['id']}  {item['name']:<20} {item['info']}")
                print("Choose: sentinel elevenlabs voice <id>")
                return 0
            started = time.time()
            audio = await voice.synthesize("Good evening. All systems are running, I am ready.")
        except el.ElevenLabsError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        out = config.data_dir / "elevenlabs-test.mp3"
        out.write_bytes(audio)
        print(f"Voice generated in {time.time() - started:.1f} s ({len(audio) // 1024} KB).")
        print(f"Listen: {out}")
        print("Restart SENTINEL and Jarvis will speak with this voice.")
        return 0

    return asyncio.run(check())


def brain(args: list[str]) -> int:
    """Brain: set the backup password, back up and restore from the server."""
    import asyncio
    import getpass

    from sentinel.brain import backup
    from sentinel.config import get_settings

    command = args[0] if args else "status"
    if command not in ("password", "status", "backup", "restore", "profile"):
        print(
            "Usage: sentinel brain [password|status|backup|restore|profile <file>]",
            file=sys.stderr,
        )
        return 2
    if command == "profile" and len(args) < 2:
        print("Usage: sentinel brain profile <file.json>", file=sys.stderr)
        return 2
    if command == "password":
        print(
            f"Password for the encrypted brain backup (at least {backup.MIN_PASSWORD} characters)."
        )
        print("Write it down: without the password the backup can no longer be opened.")
        first = getpass.getpass("Password (hidden): ")
        second = getpass.getpass("Once more: ")
        if first != second:
            print("The passwords do not match.", file=sys.stderr)
            return 1
        try:
            backup.set_password(first)
        except backup.BackupError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print("Saved (only the derived key is stored in the Windows vault).")
        return 0
    if command == "status":
        print("Password set" if backup.stored_key() else "no backup password")
        return 0

    from sentinel.services import build_services

    if _app_running(get_settings().data_dir):
        return 1
    svc = build_services(get_settings())
    try:
        if command == "profile":
            from pathlib import Path

            from sentinel.brain import profile

            try:
                data = profile.load(Path(args[1]))
                result = profile.apply(svc, data)
                copy = profile.save_copy(svc, data)
            except profile.ProfileError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            print(
                f"Imported: {result['nodes']} thoughts, {result['reminders']} reminders"
                f" ({result['rejected']} rejected)."
            )
            print(f"Copy stored safely: {copy}")
            return 0
        if command == "backup":
            try:
                result = asyncio.run(svc.brain.backup_now())
            except backup.BackupError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
            print(
                "Backed up." if result["status"] == "saved" else "No changes since the last backup."
            )
            return 0
        print("WARNING: This replaces the brain on this PC with the server backup.")
        password = getpass.getpass("Backup password (hidden): ")
        try:
            count = asyncio.run(svc.brain.restore(password))
        except backup.BackupError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(f"Restored: {count} thoughts. Restart SENTINEL.")
        return 0
    finally:
        svc.db.close()


def hermes_cmd(args: list[str]) -> int:
    """Connect the Hermes agent: create a dedicated token and print the MCP setting."""
    from pathlib import Path

    from sentinel import hermes

    command = args[0] if args else "status"
    if command not in ("setup", "off", "status"):
        print("Usage: sentinel hermes [setup|off|status]", file=sys.stderr)
        return 2
    if command == "off":
        hermes.delete_token()
        print("Hermes disconnected. Restart SENTINEL and the old token will no longer work.")
        return 0
    if command == "status":
        print("Hermes set up" if hermes.stored_token() else "Hermes not set up")
        return 0
    hermes.create_token()
    exe = Path(sys.argv[0]).resolve()
    if exe.suffix.lower() != ".exe" and exe.with_suffix(".exe").is_file():
        exe = exe.with_suffix(".exe")
    print("New agent token stored in the Windows vault (only valid for /agent/...).")
    print("1. Restart SENTINEL.")
    print(r"2. Add this to the Hermes configuration (%LOCALAPPDATA%\hermes\config.yaml or")
    print("   ~/.hermes/config.yaml):")
    print()
    print("mcp_servers:")
    print("  sentinel:")
    print(f'    command: "{exe.as_posix()}"')
    print('    args: ["mcp"]')
    print("    timeout: 120")
    print()
    print("3. In Hermes: /reload-mcp")
    return 0


def _sentinel_processes() -> list[str]:
    """Running SENTINEL programs (except this command itself)."""
    import os

    import psutil

    names = {"sentinel-app.exe", "sentinel-backend.exe", "sentinel-app", "sentinel-backend"}
    found = set()
    for proc in psutil.process_iter(["pid", "name"]):
        if proc.info["pid"] != os.getpid() and (proc.info["name"] or "").lower() in names:
            found.add(proc.info["name"])
    return sorted(found)


def db_cmd(args: list[str]) -> int:
    """Check and repair the database (SENTINEL must be closed for this)."""
    import urllib.request

    from sentinel import dbrepair, hermes
    from sentinel.config import get_settings

    command = args[0] if args else "check"
    if command not in ("check", "repair", "restore"):
        print("Usage: sentinel db [check|repair|restore]", file=sys.stderr)
        return 2
    config = get_settings()
    path = config.db_path
    if not path.is_file():
        print(f"No database found: {path}")
        return 1
    result = dbrepair.check(path)
    print(f"Check: {result}")
    if command == "check":
        return 0 if result == "ok" else 1
    if _app_running(config.data_dir):
        return 1
    if command == "restore":
        try:
            restored = dbrepair.restore_latest(path, config.data_dir / "backup" / "auto")
        except FileNotFoundError:
            print("No automatic backup found.", file=sys.stderr)
            return 1
        print(f"Restored from {restored}. Start SENTINEL again now.")
        return 0
    running = _sentinel_processes()
    if running:
        print(
            "SENTINEL is still running (" + ", ".join(running) + "). Please quit it first:\n"
            "  tray icon at the bottom right -> Quit, or in PowerShell:\n"
            "  Get-Process sentinel-app, sentinel-backend -ErrorAction SilentlyContinue"
            " | Stop-Process",
            file=sys.stderr,
        )
        return 1
    port = hermes.read_port(config.data_dir)
    if port:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2):
                print(
                    "SENTINEL is still running. Please quit it first (tray icon -> Quit).",
                    file=sys.stderr,
                )
                return 1
        except OSError:
            pass
    if result == "ok":
        print("The database is healthy - nothing to do.")
        return 0
    print("Repairing ... (everything is backed up first)")
    try:
        report = dbrepair.repair(path)
    except PermissionError:
        print(
            "The database is still in use by a program - quit SENTINEL completely "
            "and try again. Nothing was replaced.",
            file=sys.stderr,
        )
        return 1
    rescued = sum(report["tables"].values())
    for table, count in sorted(report["tables"].items()):
        if count:
            print(f"  {table:<22} {count} entries rescued")
    if not rescued:
        print("Nothing readable left - SENTINEL will start with a new, empty database.")
    print(f"Backup of the old files: {report['backup']}")
    print("Done. Start SENTINEL again now.")
    return 0


def web_cmd(args: list[str]) -> int:
    """Set the Google account for Gmail, Calendar and Drive (address only, never a password)."""
    from sentinel.automation import web
    from sentinel.config import get_settings

    config = get_settings()
    command = args[0] if args else "status"
    if command == "status":
        account = web.load_choice(config.config_dir).get("google_account")
        print(f"Google account: {account or 'none (Gmail opens the last used account)'}")
        return 0
    if command == "google" and len(args) >= 2:
        account = args[1].strip()
        if account.lower() in ("aus", "off", "none"):
            web.save_choice(config.config_dir, google_account="")
            print("No fixed Google account any more.")
            return 0
        if not web.valid_account(account):
            print("That does not look like an email address.", file=sys.stderr)
            return 1
        web.save_choice(config.config_dir, google_account=account)
        print(f"Saved: Gmail, Calendar and Drive now open {account}.")
        print(
            "Being signed in to Google once in the browser is enough - "
            "SENTINEL never needs passwords."
        )
        return 0
    print("Usage: sentinel web [status|google <email>|google off]", file=sys.stderr)
    return 2


def abusech(args: list[str]) -> int:
    """Store the free abuse.ch key for the malware list (hidden)."""
    import contextlib
    import getpass

    from sentinel.pcsec import filescan

    command = args[0] if args else "status"
    if command == "status":
        print("Key present" if filescan.abusech_key() else "no abuse.ch key")
        return 0
    if command == "logout":
        with contextlib.suppress(Exception):  # was already deleted
            filescan._keyring().delete_password(*filescan.ABUSECH_KEYRING)
        print("abuse.ch key removed.")
        return 0
    if command != "login":
        print("Usage: sentinel abusech [login|logout|status]", file=sys.stderr)
        return 2
    print("Free: sign in at https://auth.abuse.ch, then create an Auth key under 'Optional'.")
    key = getpass.getpass("abuse.ch Auth key (input hidden): ").strip()
    if not key.isalnum() or len(key) < 20:
        print("That does not look like an Auth key.", file=sys.stderr)
        return 1
    filescan._keyring().set_password(*filescan.ABUSECH_KEYRING, key)
    print("Saved. Testing the list ...")
    try:
        count = len(filescan.parse_feed(filescan._download(filescan.FEED_URL.format(key=key))))
    except Exception as exc:  # report network or login errors in a readable way
        code = getattr(exc, "code", None)
        print(f"Download failed ({code or type(exc).__name__}).", file=sys.stderr)
        return 1
    print(f"Done: {count} current malware signatures loaded. Restart SENTINEL.")
    return 0


def run(argv: list[str]) -> int | None:
    """Return None if no CLI command is meant (then the server starts)."""
    if argv and argv[0] == "plugins":
        return plugins(argv[1:])
    if argv and argv[0] == "gmail":
        return gmail(argv[1:])
    if argv and argv[0] == "gemini":
        return gemini(argv[1:])
    if argv and argv[0] == "openrouter":
        return openrouter(argv[1:])
    if argv and argv[0] == "claude":
        return claude(argv[1:])
    if argv and argv[0] == "elevenlabs":
        return elevenlabs(argv[1:])
    if argv and argv[0] == "web":
        return web_cmd(argv[1:])
    if argv and argv[0] == "abusech":
        return abusech(argv[1:])
    if argv and argv[0] == "db":
        return db_cmd(argv[1:])
    if argv and argv[0] == "mcp":
        from sentinel.config import get_settings
        from sentinel.hermes import mcp

        return mcp.main(get_settings().data_dir)
    if argv and argv[0] == "hermes":
        return hermes_cmd(argv[1:])
    if argv and argv[0] == "brain":
        return brain(argv[1:])
    if argv and argv[0] in ("-h", "--help", "help"):
        print(
            "sentinel            start the backend\n"
            "sentinel plugins list|reload\n"
            "sentinel gmail login|logout|status\n"
            "sentinel gemini login|logout|status|test\n"
            "sentinel claude login|logout|status|test|model <id>\n"
            "sentinel openrouter login|logout|status|test|model <id>\n"
            "sentinel elevenlabs login|logout|status|test|voices|voice <id>\n"
            "sentinel brain password|status|backup|restore|profile <file>\n"
            "sentinel hermes setup|off|status\n"
            "sentinel db check|repair|restore\n"
            "sentinel abusech login|logout|status\n"
            "sentinel web status|google <email>|google off\n"
            "sentinel mcp        MCP server for Hermes (started by Hermes itself)"
        )
        return 0
    return None
