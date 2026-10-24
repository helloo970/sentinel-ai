"""Microphone selection: filter real devices, clean names, find the choice again."""

from __future__ import annotations

from types import SimpleNamespace

from sentinel.voice import devices

APIS = [{"name": "MME"}, {"name": "Windows WASAPI"}, {"name": "Windows WDM-KS"}]


def fake_sd(default_input: int = 1) -> SimpleNamespace:
    devs = [
        {
            "name": "Microsoft Soundmapper - Input",
            "hostapi": 0,
            "max_input_channels": 2,
            "default_samplerate": 44100,
        },
        {
            "name": "Microphone Array (Realtek(R) Audio",
            "hostapi": 0,
            "max_input_channels": 4,
            "default_samplerate": 44100,
        },
        {
            "name": "Microphone Array (Realtek(R) Audio)",
            "hostapi": 1,
            "max_input_channels": 2,
            "default_samplerate": 48000,
        },
        {
            "name": "Stereomix (Realtek HD Audio)",
            "hostapi": 1,
            "max_input_channels": 2,
            "default_samplerate": 48000,
        },
        {
            "name": "Speakers (Realtek)",
            "hostapi": 0,
            "max_input_channels": 0,
            "default_samplerate": 48000,
        },
        {
            "name": "Headset (@System32\\drivers\\bthhfenum.sys,#2;%1 Hands-Free%0;(Arctis Nova))",
            "hostapi": 0,
            "max_input_channels": 1,
            "default_samplerate": 16000,
        },
        {
            "name": "Mic (WDM only)",
            "hostapi": 2,
            "max_input_channels": 2,
            "default_samplerate": 48000,
        },
    ]
    return SimpleNamespace(
        query_hostapis=lambda: APIS,
        query_devices=lambda: devs,
        default=SimpleNamespace(device=(default_input, 3)),
    )


def test_lists_only_real_inputs_once() -> None:
    found = devices.list_inputs(fake_sd())
    names = [d["name"] for d in found]
    assert names[0] == "Microphone Array (Realtek(R) Audio" and found[0]["default"]
    assert found[0]["api"] == "MME"  # MME preferred, resamples sample rates itself
    assert "Arctis Nova" in names
    assert not any("Stereomix" in n or "Soundmapper" in n or "WDM" in n for n in names)
    assert len(names) == len(set(names))


def test_resolve_saved_choice() -> None:
    sd = fake_sd()
    choice = {"name": "Arctis Nova", "api": "MME"}
    assert devices.resolve(choice, sd)["index"] == 5
    assert devices.resolve({"name": "Does not exist", "api": "MME"}, sd) is None
    assert devices.resolve(None, sd) is None
