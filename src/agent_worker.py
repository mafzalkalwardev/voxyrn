"""Keep one agent slot's Pulse sinks up. systemd restarts this process.

Dialing stays in the operator console, which admits accounts onto these
slots. This worker only owns the audio devices for slot N and exits if they
cannot be created, so Restart=always brings the slot back.
"""
from __future__ import annotations

import argparse
import sys
import time

from src.agent_fleet import prepare_slot_audio, slot_audio
from src.linux_audio import pactl_available


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Voxyrn agent slot keeper")
    parser.add_argument("--slot", type=int, required=True)
    args = parser.parse_args(argv)
    if args.slot < 1:
        print("slot must be >= 1", file=sys.stderr)
        return 2
    tts, speaker, monitor = slot_audio(args.slot)
    print(f"agent {args.slot}: tts={tts} speaker={speaker} capture={monitor}")
    if not pactl_available():
        print("pactl is not available", file=sys.stderr)
        return 1

    class _Slot:
        index = args.slot
        loopback_device = tts
        pulse_sink = speaker

    while True:
        prepare_slot_audio(_Slot())  # type: ignore[arg-type]
        time.sleep(30)


if __name__ == "__main__":
    raise SystemExit(main())
