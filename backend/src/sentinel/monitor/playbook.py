"""Defense flow and protection tips per detection rule (for the alert test and alert details).

Flow: what SENTINEL does itself. Tips: what you can additionally configure, usually with
admin rights, therefore only a recommendation, never automatic.
Sources: BSI (ransomware catalog of measures, router/Wi-Fi), Microsoft Learn (Defender ASR,
controlled folder access, LSA protection), MITRE ATT&CK M1034, common SSH hardening.
"""

from __future__ import annotations

from typing import Any

SOURCES: dict[str, tuple[str, str]] = {
    "bsi_ransomware": ("BSI: Ransomware", "https://www.bsi.bund.de"),
    "bsi_router": ("BSI: Router and Wi-Fi", "https://www.bsi.bund.de"),
    "ms_asr": (
        "Microsoft: Defender ASR rules",
        "https://learn.microsoft.com/en-us/defender-endpoint/attack-surface-reduction-rules-reference",
    ),
    "ms_cfa": (
        "Microsoft: Controlled folder access",
        "https://learn.microsoft.com/en-us/defender-endpoint/controlled-folders",
    ),
    "ms_lsa": (
        "Microsoft: LSA protection",
        "https://learn.microsoft.com/en-us/windows-server/security/credentials-protection-and-management/configuring-additional-lsa-protection",
    ),
    "mitre_usb": ("MITRE ATT&CK M1034", "https://attack.mitre.org/mitigations/M1034/"),
}

# Steps: detect -> check -> defend -> result
DEFENSE: dict[str, list[str]] = {
    "malware_file": [
        "New file discovered in Downloads",
        "SHA-256 compared with the malware list, Windows Defender scans locally",
        "File moved to quarantine (undoable)",
        "File can no longer be started",
    ],
    "mass_file_change": [
        "Hundreds of file changes detected within a few seconds",
        "Pattern matches encryption (ransomware)",
        "Rescue copy of the important folders + loud alarm, in guardian mode end the process",
        "Your documents are backed up, the attacker is stopped",
    ],
    "office_spawns_shell": [
        "Sysmon reports: Word starts PowerShell",
        "Office programs normally don't start command lines - typical macro attack",
        "End the PowerShell process (automatic in guardian mode, otherwise with approval)",
        "The attack from the document does not continue",
    ],
    "lsass_access": [
        "Sysmon reports access to the password store LSASS",
        "Only Windows itself may do that - foreign access means password theft",
        "End the foreign process, alarm with sound",
        "Credentials stay protected",
    ],
    "temp_exec": [
        "Program start from the Temp folder detected",
        "Legitimate programs don't run from Temp",
        "File to quarantine, process suggested for review",
        "Hidden program is isolated",
    ],
    "temp_listener": [
        "Program from Temp opens a network port",
        "Combination matches a backdoor",
        "End the process (approval), port check",
        "Backdoor is closed",
    ],
    "double_extension": [
        "File with a double extension discovered (photo.jpg.exe)",
        "Disguised program instead of an image",
        "File to quarantine",
        "You can no longer open it by accident",
    ],
    "new_executable": [
        "New program file in Downloads",
        "Hash and Defender check",
        "Quarantine if something is found, otherwise just a notice",
        "You know what lands on your PC",
    ],
    "new_autostart": [
        "New autostart entry found",
        "Compared with the known list - new and unknown",
        "Notice to you (changing autostart needs your confirmation)",
        "Nothing starts secretly with Windows",
    ],
    "new_listener": [
        "New open port discovered",
        "Program and path are checked",
        "Notice, close the program on request",
        "No program is reachable from outside unnoticed",
    ],
    "usb_new_device": [
        "New USB device connected, it identifies as a keyboard",
        "Unknown keyboards are typical for BadUSB sticks",
        "Loud alarm - unplug the stick immediately",
        "You notice the attack before it types",
    ],
    "browser_extension_new": [
        "New browser extension installed",
        "Permissions checked: may read all sites",
        "Warning with name and permissions",
        "You decide whether it may stay",
    ],
    "webcam_new_app": [
        "Windows reports: new program uses the camera",
        "Program is not marked as known",
        "Warning with program path",
        "Spying is noticed immediately",
    ],
    "microphone_new_app": [
        "New program uses the microphone",
        "Program is not marked as known",
        "Notice with program path",
        "Secret eavesdropping is noticed",
    ],
    "new_device": [
        "New MAC address on the home network",
        "Vendor looked up, compared with known devices",
        'Port check of the device, question "I know it / I don\'t know it"',
        "Foreign devices are noticed immediately",
    ],
    "pihole_client_spike": [
        "Pi-hole: one device makes an unusually high number of DNS queries",
        "Typical for malware phoning home",
        "Warning with device, Pi-hole blocks known malicious domains",
        "Infected device is found",
    ],
    "cert_expiring": [
        "Your service's certificate expires soon",
        "Expiry date checked",
        "Reminder in good time beforehand",
        "No outage, no browser warnings",
    ],
    "server_ssh_bruteforce": [
        "auth.log: many failed SSH logins",
        "Origin checked (attack map)",
        "Warning and push - the server agent itself changes nothing",
        "You see the attack and can block the IP",
    ],
    "server_security_updates": [
        "Pending security updates on the server",
        "Known vulnerabilities not yet closed",
        "Daily reminder",
        "Vulnerabilities don't stay open for long",
    ],
    "server_service_failed": [
        "A service on the server has failed",
        "Status checked with systemctl",
        "Warning and push",
        "Outages are noticed immediately",
    ],
    "server_docker": [
        "Container exited or keeps restarting",
        "State compared with the last check",
        "Warning with container name",
        "Crashed or tampered containers are noticed",
    ],
    "server_disk_full": [
        "Server disk almost full",
        "Fill level above the limit",
        "Warning",
        "No outage from a full disk",
    ],
    "server_backup": [
        "Newest file in the backup folder is too old",
        "Backup apparently no longer runs",
        "Daily warning until backups run again",
        "If it comes to it, there is a current backup",
    ],
}

TIPS: dict[str, list[tuple[str, str]]] = {
    "malware_file": [
        ("Keep Windows Defender and all programs up to date", "bsi_ransomware"),
        ("Download only from official sites", "bsi_ransomware"),
    ],
    "mass_file_change": [
        ("Turn on controlled folder access in Windows Defender", "ms_cfa"),
        (
            "Backups following the 3-2-1 rule: one copy disconnected from the network",
            "bsi_ransomware",
        ),
        ("Never pay ransom, inform the police", "bsi_ransomware"),
    ],
    "office_spawns_shell": [
        (
            'Enable the Defender rule "Block Office apps from creating child processes"',
            "ms_asr",
        ),
        ("Block macros in Office files from the internet", "ms_asr"),
    ],
    "lsass_access": [
        ("Turn on LSA protection (RunAsPPL)", "ms_lsa"),
        (
            'Defender rule "Block credential stealing from LSASS"',
            "ms_asr",
        ),
    ],
    "temp_exec": [("Enable the Defender rule against unknown executable files", "ms_asr")],
    "temp_listener": [("Keep the Windows firewall turned on", "bsi_router")],
    "double_extension": [('Show "File name extensions" in Explorer', "bsi_ransomware")],
    "new_executable": [("Only programs from trustworthy sources", "bsi_ransomware")],
    "new_autostart": [("Check autostart regularly in Task Manager", "bsi_ransomware")],
    "new_listener": [("Turn off services you don't need", "bsi_router")],
    "usb_new_device": [
        ("Never plug in foreign USB sticks", "mitre_usb"),
        ("Lock your PC when you walk away (Windows + L)", "mitre_usb"),
    ],
    "browser_extension_new": [
        ("Keep only a few, well-known extensions", "bsi_ransomware"),
        ("Passwords in a password manager instead of the browser", "bsi_ransomware"),
    ],
    "webcam_new_app": [
        ("In Windows privacy settings -> Camera only allow necessary apps", "bsi_router")
    ],
    "microphone_new_app": [
        ("In Windows privacy settings -> Microphone only necessary apps", "bsi_router")
    ],
    "new_device": [
        ("Wi-Fi password with at least 20 characters, WPA3 (otherwise WPA2)", "bsi_router"),
        ("Put guests and smart home on the guest network", "bsi_router"),
    ],
    "pihole_client_spike": [("Check the device and update its firmware", "bsi_router")],
    "cert_expiring": [("Have certificates renewed automatically", "bsi_router")],
    "server_ssh_bruteforce": [
        ("SSH with keys only: PasswordAuthentication no", "bsi_router"),
        ("Forbid root login: PermitRootLogin no", "bsi_router"),
        ("Install Fail2ban, don't expose SSH to the internet", "bsi_router"),
    ],
    "server_security_updates": [("Unattended-upgrades for security updates", "bsi_router")],
    "server_service_failed": [
        ("Check the cause in the log: journalctl -u <service>", "bsi_router")
    ],
    "server_docker": [("Keep images up to date, don't run containers as root", "bsi_router")],
    "server_disk_full": [("Clean up old logs and Docker images", "bsi_router")],
    "server_backup": [
        ("Run backups automatically and regularly", "bsi_ransomware"),
        ("Keep one copy off-site or offline", "bsi_ransomware"),
    ],
}


def for_rule(rule: str) -> dict[str, Any]:
    return {
        "defense": DEFENSE.get(rule, []),
        "tips": [
            {"text": text, "source": SOURCES[key][0], "url": SOURCES[key][1]}
            for text, key in TIPS.get(rule, [])
        ],
    }
