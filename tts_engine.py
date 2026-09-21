"""
tts_engine.py

Text-to-speech for the "Hoshikawa Sara" assistant persona:
  1. edge-tts synthesizes the line as generic English speech.
  2. RVC (Retrieval-based Voice Conversion) reshapes that speech into
     Sara's voice using a trained model + index file.
  3. The result is played back locally (sounddevice, falling back to the
     OS-native player if sounddevice isn't available).
"""

import asyncio
import os
import platform
import random
import subprocess
import tempfile
import threading
import time
import traceback
import wave

import torch
from rvc_python.infer import RVCInference

try:
    import numpy as np
    import sounddevice as sd
    SD_AVAILABLE = True
except ImportError:
    SD_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.path.join(
    BASE_DIR, "Models", "sara_hoshikawa_nijisanji_6202",
    "SaraHoshikawa_e330_s29700.pth",
)
INDEX_PATH = os.path.join(
    BASE_DIR, "Models", "sara_hoshikawa_nijisanji_6202",
    "added_IVF4176_Flat_nprobe_1_SaraHoshikawa_v2.index",
)

rvc = None

is_audio_playing = False
audio_lock = threading.Lock()

last_spoken_text = ""
text_lock = threading.Lock()

current_spectrum = [0.0] * 20
spectrum_lock = threading.Lock()

# --- Persona dialogue pools -------------------------------------------------

CONNECT_MESSAGES = [
    "Tadah! The brightest shining star is officially connected, Did you miss me?",
    "Ohh, connection successful! You're so lucky to have a cute system assistant like me handling things.",
    "System booted and fully operational! Bow down to your favorite idol assistant!",
]

DISCONNECT_MESSAGES = [
    "Ehh?! Wait, the hardware just disconnected! Hey, fix it!",
    "Connection lost! Don't tell me you broke it already?",
    "Warning! Heartbeat dropped. The system ran away from me! Come back here!",
]

UNCLEAR_MESSAGES = [
    "Ehh? I couldn't catch that at all! Speak up, will you?",
    "That sounded like total gibberish to me. Try saying it again clearly!",
    "Huh? What did you just say? My star ears didn't hear a thing!",
    "Error 404, instructions not found! Try giving me a real command, jeez.",
]

WAKE_GREETINGS = [
    "The world's cutest idol, Hoshikawa Sara, has arrived! What do you want, hmm?",
    "You called the brightest star! What's going on?",
    "Ehh? Did you miss me already? What's up?",
    "Ehee~, did you call for me? Spill it, what's on your mind?",
]


def initialize_tts() -> None:
    """Load the RVC model + index into memory. Call once at startup."""
    global rvc

    if not os.path.exists(MODEL_PATH) or not os.path.exists(INDEX_PATH):
        raise FileNotFoundError("RVC model or index file missing from project directory!")

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"Initializing RVC Inference engine on device: {device}...")
    rvc = RVCInference(device=device)

    print("Loading Sara Hoshikawa model weights into memory...")
    rvc.load_model(MODEL_PATH)
    rvc.set_params(
        f0up_key=8,
        f0method="rmvpe",
        file_index=INDEX_PATH,
        index_rate=0.4,
        protect=0.3,
    )
    print("RVC engine is loaded and ready!")


def play_audio(file_path: str) -> None:
    """Play a WAV file and block until playback finishes."""
    global is_audio_playing

    if not file_path or not os.path.exists(file_path):
        print(f"[Playback Error] Audio file path does not exist: {file_path}")
        return

    try:
        with wave.open(file_path, "r") as wf:
            channels = wf.getnchannels()
            rate = wf.getframerate()
            frames = wf.getnframes()
            duration = frames / float(rate)
            raw = wf.readframes(frames)
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32767.0
            if channels > 1:
                samples = samples.reshape(-1, channels).mean(axis=1)
    except Exception as exc:
        print(f"[Playback Error] Failed to read wav file: {exc}")
        return

    print(f"[Playback] Playing audio file: {file_path} (Duration: {duration:.2f}s)")

    with audio_lock:
        is_audio_playing = True
        try:
            if SD_AVAILABLE:
                sd.play(samples, samplerate=rate)
                sd.wait()
                time.sleep(0.3)  # Small buffer so the mic doesn't pick up playback tail
            elif platform.system() == "Windows":
                import winsound
                winsound.PlaySound(file_path, winsound.SND_FILENAME)
                time.sleep(duration)
            elif platform.system() == "Darwin":
                subprocess.run(["afplay", file_path])
                time.sleep(0.3)
            else:
                subprocess.run(["aplay", file_path])
                time.sleep(0.3)
        except Exception as exc:
            print(f"[Playback Exception]: {exc}")
        finally:
            is_audio_playing = False
            print("[System] Audio finished. Microphone is now active.")


def _synthesize_base_speech(text: str, output_path: str, voice: str = "en-US-JennyNeural") -> None:
    """Generate plain English speech via edge-tts into output_path (mp3)."""
    import edge_tts

    cleaned_text = text.strip()
    if cleaned_text.isupper():
        cleaned_text = cleaned_text.capitalize()

    async def _run():
        communicate = edge_tts.Communicate(cleaned_text, voice)
        await communicate.save(output_path)

    # Runs in a background thread (see play_character_alert), so there's no
    # existing event loop to conflict with — asyncio.run() is safe here.
    asyncio.run(_run())


def generate_tts_audio(message: str, filename_base: str = "alert_output") -> str | None:
    """Synthesize `message` and voice-convert it. Returns the output WAV path, or None on failure."""
    if rvc is None:
        print("TTS Error: RVC engine not initialized!")
        return None

    temp_dir = tempfile.mkdtemp()
    temp_audio = os.path.join(temp_dir, f"{filename_base}_temp.mp3")
    output_audio = os.path.join(BASE_DIR, f"{filename_base}.wav")

    if os.path.exists(output_audio):
        try:
            os.remove(output_audio)
        except OSError:
            pass

    try:
        print(f"[TTS] Synthesizing speech via Edge-TTS for: '{message}'")
        _synthesize_base_speech(message, temp_audio)

        if not os.path.exists(temp_audio) or os.path.getsize(temp_audio) == 0:
            print("[TTS Error] Edge-TTS failed to save temporary audio file or file is empty.")
            return None

        print("[TTS] Running RVC voice conversion...")
        rvc.infer_file(input_path=temp_audio, output_path=output_audio)

        if os.path.exists(output_audio) and os.path.getsize(output_audio) > 0:
            print(f"[TTS Success] Generated output at: {output_audio}")
            return output_audio

        print("[TTS Error] RVC finished, but output wav file is missing or empty.")
        return None

    except Exception:
        print("--- RVC / TTS EXCEPTION ---")
        traceback.print_exc()
        print("---------------------------")
        return None
    finally:
        if os.path.exists(temp_audio):
            try:
                os.remove(temp_audio)
            except OSError:
                pass
        try:
            os.rmdir(temp_dir)
        except OSError:
            pass


def play_character_alert(message_type: str, custom_text: str | None = None, is_pure_wake: bool = False):
    """
    Pick the line to speak for a given event type, synthesize it, and
    return (audio_path, text_spoken). Does not play the audio itself —
    the caller is responsible for that (see pc_server.py's trigger_voice_alert).
    """
    global last_spoken_text

    if message_type == "connect":
        text_to_speak = random.choice(CONNECT_MESSAGES)
    elif message_type == "disconnect":
        text_to_speak = random.choice(DISCONNECT_MESSAGES)
    elif message_type == "command":
        if is_pure_wake:
            text_to_speak = random.choice(WAKE_GREETINGS)
        elif custom_text and custom_text.strip():
            text_to_speak = custom_text
        else:
            text_to_speak = random.choice(UNCLEAR_MESSAGES)
    else:
        text_to_speak = custom_text or "Acknowledged."

    with text_lock:
        last_spoken_text = text_to_speak

    output_audio_path = generate_tts_audio(text_to_speak)
    return output_audio_path, text_to_speak