# scripts

Helpers that you run yourself. None of them is executed by the app.

| Script | Rights | Purpose |
| --- | --- | --- |
| `dev.ps1` | user | Start the app in development mode (`npm run tauri dev`) |
| `setup-sysmon.ps1` | **administrator, once** | Install Sysmon with `sysmon-config.xml`, add the user to *Event Log Readers* |
| `download-voice-models.ps1` | user | Install voice packages, download the wake word model and an English Piper voice |
| `build-sidecar.ps1` | user | Build the backend with PyInstaller as Tauri sidecar, optionally the installer |
