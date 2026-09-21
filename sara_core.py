#cd "D:\Visual Studio Projects\Projects\SARA Voice PC Control" 
#.\venv\Scripts\Activate.ps1 

import os
import platform
import random
import socket
import subprocess
import sys
import tempfile
import threading
import time
import wave
import re
import glob
import webbrowser
import math

import torch
torch.jit.script = lambda fn: fn

import subprocess

# Path to your existing batch file
bat_path = r"D:\Visual Studio Projects\Projects\SARA Voice PC Control\FanControl.Releases-master\FanControl.Releases-master\StartFanControl.bat"

# Run the batch file in the background without popping up an extra command window
subprocess.Popen(
    ["cmd.exe", "/c", bat_path], creationflags=subprocess.CREATE_NO_WINDOW
)

from datetime import datetime

# Try importing sounddevice for PC mic support
try:
    import sounddevice as sd
    import numpy as np
    PC_MIC_AVAILABLE = True
except ImportError:
    PC_MIC_AVAILABLE = False

# Try importing Pillow for image processing
try:
    from PIL import Image, ImageFilter
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# PySide6 UI Imports
from PySide6.QtCore import Qt, QTimer, QObject, Signal, QRectF, QPoint, QPropertyAnimation, QEasingCurve, QRect, QParallelAnimationGroup
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor, QFont, QPainterPath, QLinearGradient, QIcon
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QTextEdit, 
    QFrame, QVBoxLayout, QHBoxLayout, QSizePolicy, QPushButton, QGraphicsDropShadowEffect, QProgressBar
)

if hasattr(subprocess, "STARTUPINFO"):
    _original_init = subprocess.Popen.__init__

    def _patched_init(self, args, executable=None, args_is_string=None, **kwargs):
        if "startupinfo" not in kwargs:
            si = subprocess.STARTUPINFO()
            si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            kwargs["startupinfo"] = si
        if "creationflags" not in kwargs:
            kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
        _original_init(self, args, executable=executable, **kwargs)

    subprocess.Popen.__init__ = _patched_init

class _SafeStream:
    def __init__(self, original, fallback):
        self.original = original
        self.fallback = fallback

    def write(self, text):
        try:
            stripped = text.strip()
            if any(k in stripped for k in ["--enable-", "libavutil", "libavcodec", "libavformat", "libavdevice", "libavfilter", "libswscale", "libswresample"]):
                return
            if self.original:
                self.original.write(text)
            else:
                self.fallback.write(text)
        except Exception:
            pass

    def flush(self):
        try:
            if self.original:
                self.original.flush()
        except Exception:
            pass

import io
if sys.stdout is None:
    sys.stdout = io.StringIO()
else:
    sys.stdout = _SafeStream(sys.stdout, io.StringIO())

if sys.stderr is None:
    sys.stderr = io.StringIO()
else:
    sys.stderr = _SafeStream(sys.stderr, io.StringIO())

from flask import Flask, jsonify, request
from faster_whisper import WhisperModel

app = Flask(__name__)

SINGLE_INSTANCE_PORTS = [5002, 5003]
lock_sockets = []

def check_single_instance():
    global lock_sockets
    for port in SINGLE_INSTANCE_PORTS:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            s.listen(1)
            lock_sockets.append(s)
        except socket.error:
            sys.exit(0)

# Global UI / Audio References
main_window_ref = None
tts_module = None
whisper_model = None
last_seen_time = 0
device_is_connected = False
is_audio_playing = False
is_greeting_played = False
awake_until_time = 0

spectrum_heights = [14.0] * 32
audio_amplitude = 0.0
audio_lock = threading.Lock()

WALLPAPER_DIR = r"D:\Visual Studio Projects\Projects\SARA Voice PC Control\wallpapers"
wallpaper_images = []

class UIBridge(QObject):
    append_console_signal = Signal(str)
    append_chat_signal = Signal(str, str)
    ai_status_signal = Signal(str, str)
    esp_status_signal = Signal(str, str)

ui_bridge = UIBridge()

def get_theme_color():
    return (255, 195, 0)

def load_wallpaper_list():
    global wallpaper_images
    wallpaper_images = []
    if os.path.exists(WALLPAPER_DIR):
        valid_exts = (".png", ".jpg", ".jpeg", ".webp", ".bmp")
        for f in os.listdir(WALLPAPER_DIR):
            if f.lower().endswith(valid_exts):
                wallpaper_images.append(os.path.join(WALLPAPER_DIR, f))
    if not wallpaper_images:
        print(f"[Warning] No wallpapers found in {WALLPAPER_DIR}")

def log_to_console(message):
    cleaned = message.replace("(Awake: True)", "").replace("(Awake: False)", "")
    cleaned = cleaned.replace("Pure name called. Sara is awake for the next single instruction.", "Wake word triggered. Listening for instruction.")
    cleaned = cleaned.replace("Single follow-up instruction processed. Deactivating awake state.", "Command executed. Returning to sleep.")
    cleaned = cleaned.replace("Wake word + command recognized. Executing action and going back to sleep.", "Command recognized and executed.")
    ui_bridge.append_console_signal.emit(cleaned.strip())

def log_to_chat(sender, message):
    timestamp = datetime.now().strftime("%I:%M %p")
    if sender == "You":
        formatted = f"[{timestamp}]  You: {message}\n"
    elif sender == "Sara":
        formatted = f"[{timestamp}]  Hoshikawa Sara: {message}\n"
    elif sender == "System":
        formatted = f"[{timestamp}]  {message}\n"
    else:
        formatted = f"[{timestamp}]  {sender}: {message}\n"
    ui_bridge.append_chat_signal.emit(sender, formatted)

def update_ai_status(text, color="#FF3333"):
    ui_bridge.ai_status_signal.emit(text, color)

def update_esp_status(text, color="#FF3333"):
    ui_bridge.esp_status_signal.emit(text, color)

def load_ai_models_in_background():
    global tts_module, whisper_model

    log_to_console("[System] Loading Whisper English STT Model (medium for higher accuracy)...")
    update_esp_status("Audio: Loading STT", color="#FF3333")
    try:
        whisper_model = WhisperModel("medium", device="cuda", compute_type="int8_float16")
        log_to_console("[System] Whisper STT Model loaded successfully.")
        update_esp_status("Audio: Mic Ready", color="#2ecc71")
    except Exception as e:
        log_to_console(f"[Error] Failed to load Whisper Model: {e}")
        update_esp_status("Audio: Load Error", color="#FF3333")

    log_to_console("[System] Loading RVC Voice Model...")
    update_ai_status("AI: Loading Weights", color="#FF3333")
    try:
        import tts_engine as tts
        if hasattr(tts, "initialize_tts"):
            tts.initialize_tts()
        elif hasattr(tts, "init_tts"):
            tts.init_tts()
        elif hasattr(tts, "load_model"):
            tts.load_model()
        
        tts_module = tts
        log_to_console("[System] RVC Voice Model loaded successfully.")
        update_ai_status("AI: Sara Ready", color="#2ecc71")
        
        bat_path = r"D:\Visual Studio Projects\Projects\SARA Voice PC Control\FanControl.Releases-master\FanControl.Releases-master\StartFanControl.bat"
        if os.path.exists(bat_path):
            log_to_console("[System] Triggering startup batch file to fix lighting...")
            subprocess.Popen([bat_path], shell=True)

        trigger_voice_alert("connect")
    except Exception as e:
        log_to_console(f"[Error] Failed to load TTS Engine: {e}")
        update_ai_status("AI: Load Error", color="#FF3333")

def play_audio_with_reactivity(audio_path):
    global audio_amplitude
    if not os.path.exists(audio_path):
        return

    try:
        import soundfile as sf
        data, samplerate = sf.read(audio_path)
        if len(data.shape) > 1:
            data = data.mean(axis=1)

        def callback(outdata, frames, time_info, status):
            nonlocal data
            global audio_amplitude
            if len(data) == 0:
                raise sd.CallbackStop()
            
            chunk = data[:frames]
            if len(chunk) < frames:
                chunk = np.pad(chunk, (0, frames - len(chunk)))
            
            audio_amplitude = float(np.max(np.abs(chunk)))
            outdata[:] = chunk.reshape(-1, 1)
            data = data[frames:]

        with sd.OutputStream(samplerate=samplerate, channels=1, callback=callback):
            while len(data) > 0:
                time.sleep(0.01)
    except Exception:
        if tts_module and hasattr(tts_module, "play_audio"):
            tts_module.play_audio(audio_path)

def trigger_voice_alert(message_type, custom_text=None, is_pure_wake=False):
    global is_audio_playing, is_greeting_played

    if message_type == "connect":
        if is_greeting_played:
            return
        is_greeting_played = True

    def _run():
        global is_audio_playing, audio_amplitude
        while tts_module is None:
            time.sleep(0.5)

        if message_type == "connect":
            time.sleep(1.0)

        try:
            if hasattr(tts_module, "play_character_alert"):
                res = tts_module.play_character_alert(message_type, custom_text, is_pure_wake=is_pure_wake)
            else:
                res = None

            if isinstance(res, tuple):
                audio_path, spoken_text = res
            else:
                audio_path = res
                spoken_text = custom_text if custom_text else f"[{message_type} alert]"

            log_to_chat("Sara", spoken_text)

            if audio_path and os.path.exists(audio_path):
                with audio_lock:
                    is_audio_playing = True

                try:
                    play_audio_with_reactivity(audio_path)
                    time.sleep(0.05) 
                except Exception as ex:
                    log_to_console(f"Audio Playback Error: {ex}")
        except Exception as e:
            log_to_console(f"Audio Alert Error: {e}")
        finally:
            with audio_lock:
                is_audio_playing = False
                audio_amplitude = 0.0
            log_to_chat("System", "waiting for instruction")

    threading.Thread(target=_run, daemon=True).start()

def fix_glued_command(text):
    text = text.strip().lower()
    text = re.sub(r'\b(sarah|hoshikawa|hoshkawa)\b', 'sara', text)
    actions = ['open', 'launch', 'start', 'run', 'search']
    for act in actions:
        text = re.sub(rf'\b{act}\s*([a-z0-9._-]+)', rf'{act} \1', text)
    for prep in ['on', 'in', 'using']:
        for browser in ['operagx', 'opera', 'chrome', 'edge', 'firefox', 'google']:
            text = re.sub(rf'\b{prep}{browser}\b', f' {prep} {browser}', text)
    return text

FAN_CONTROL_PATH = r"D:\Visual Studio Projects\Projects\SARA Voice PC Control\FanControl.Releases-master\FanControl.Releases-master\FanControl.exe"
CONFIG_DIR = r"D:\Visual Studio Projects\Projects\SARA Voice PC Control\FanControl.Releases-master\FanControl.Releases-master\Configurations"

def check_fan_action(command_text):
    command_lower = command_text.lower()
    if any(phrase in command_lower for phrase in [
        "fan to max", "fans to max", "max fan", "max fans", "full fan", 
        "fan to next", "fans to next", "fan to nax", "fans to nax",
        "fen tu mecs", "fence to max", "fans to max", "fan to macs", "fenn tu dumax",
        "fan profile to max", "fans profile to max", "max profile",
        "thanks to max", "thx to max", "thank you max", "sara thanks to max",
        "fan to mix", "fans to mix", "mix fan", "mix fans", "mix profile", 
        "fan to miks", "fans to miks", "miks fan", "miks fans"
    ]):
        return "max"
    elif any(phrase in command_lower for phrase in [
        "fan to default", "fans to normal", "reset fan", "auto fan", "normal fan",
        "fen tu normal", "fence to normal", "fans to normal", "fan profile to normal", "normal profile"
    ]):
        return "normal"
    
    match = re.search(r'(?:fan|fans)\s+(?:to\s+|profile\s+to\s+|profile\s+)([a-z0-9_-]+)', command_lower)
    if match:
        profile = match.group(1)
        if profile in ["mix", "miks", "next", "nax", "mecs", "macs"]:
            return "max"
        return profile
    return None

def switch_fan_profile(profile_name):
    profile_lower = profile_name.lower()
    config_filename = "Max.json" if profile_lower == "max" else ("Normal.json" if profile_lower == "normal" else f"{profile_name}.json")
    config_path = os.path.join(CONFIG_DIR, config_filename)

    if not os.path.exists(config_path):
        log_to_console(f"[System] Fan profile '{profile_name}' does not exist at {config_path}")
        return False, f"Sorry, the {profile_name} fan profile does not exist."

    success = False
    if os.path.exists(FAN_CONTROL_PATH):
        try:
            cmd = [FAN_CONTROL_PATH, "-c", config_path]
            subprocess.Popen(cmd, shell=True)
            log_to_console(f"[System] Successfully executed FanControl for profile: {profile_name}")
            success = True
        except Exception as ex:
            log_to_console(f"[Error] Direct FanControl execution failed: {ex}")

    if not success:
        try:
            task_name = "RunFanControl"
            subprocess.Popen(["schtasks", "/run", "/tn", task_name], shell=True)
            log_to_console(f"[System] Triggered Fan Control profile switch via Task Scheduler: {profile_name}")
            success = True
        except Exception as e:
            log_to_console(f"[Error] Failed to switch fan profile via task scheduler: {e}")

    if success:
        return True, f"Fan speed maxed out! What, are you worried my system is gonna overheat from all my brilliance? Well, you're welcome!" if profile_lower == "max" else f"Switching fan profile to {profile_name} now!"
    else:
        return False, f"Failed to switch fan profile to {profile_name}."

def launch_app_dynamically(app_name):
    app_name = app_name.lower().strip()
    log_to_console(f"[System] Searching dynamically for app: '{app_name}'")
    
    app_name = re.sub(r'\b(browser|app|application|exe)\b', '', app_name).strip()
    clean_query = re.sub(r'[^a-z0-9]', '', app_name)

    search_paths = [
        os.path.expandvars(r"%ProgramFiles%\**\*.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\**\*.exe"),
        os.path.expandvars(r"%LocalAppData%\Programs\**\*.exe"),
        os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs\**\*.lnk"),
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs\**\*.lnk")
    ]
    
    desktop_path = os.path.expandvars(r"%UserProfile%\Desktop")
    public_desktop = os.path.expandvars(r"%Public%\Desktop")
    
    for path in [desktop_path, public_desktop]:
        if os.path.exists(path):
            search_paths.append(os.path.join(path, f"*{app_name}*.lnk"))
            search_paths.append(os.path.join(path, f"*{app_name}*.exe"))

    all_matches = []
    for pattern in search_paths:
        try:
            all_matches.extend(glob.glob(pattern, recursive=True))
        except Exception:
            continue
    
    all_matches.sort(key=lambda x: (not x.endswith('.lnk'), len(x)))

    matched_path = None
    for m in all_matches:
        filename = os.path.basename(m).lower()
        clean_filename = re.sub(r'[^a-z0-9]', '', filename)
        if any(bad in filename for bad in ['install', 'setup', 'update', 'uninst', 'error', 'reporter', 'helper', 'crash']):
            continue
        if clean_query in clean_filename or all(term in clean_filename for term in clean_query.split()):
            matched_path = m
            break

    if matched_path:
        log_to_console(f"[System] Found: {matched_path}")
        try:
            if matched_path.lower().endswith('.lnk'):
                os.startfile(matched_path)
            else:
                subprocess.Popen([matched_path])
            return True
        except Exception as e:
            log_to_console(f"[Error] Failed to launch {matched_path}: {e}")
            return False
    else:
        log_to_console(f"[Error] Could not find clean executable or shortcut for '{app_name}'")
        return False

BROWSER_MAP = {
    "chrome": [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe"
    ],
    "google": [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe"
    ],
    "opera gx": [
        r"%LocalAppData%\Programs\Opera GX\launcher.exe",
        r"%ProgramFiles%\Opera GX\launcher.exe"
    ],
    "opera": [
        r"%LocalAppData%\Programs\Opera\launcher.exe",
        r"%ProgramFiles%\Opera\launcher.exe"
    ]
}

def find_browser_dynamically(browser_name):
    browser_name = browser_name.lower().strip()
    paths = BROWSER_MAP.get(browser_name, [])
    for p in paths:
        expanded = os.path.expandvars(p)
        if os.path.exists(expanded):
            return expanded
            
    search_dirs = [
        os.path.expandvars(r"%ProgramFiles%"),
        os.path.expandvars(r"%ProgramFiles(x86)%"),
        os.path.expandvars(r"%LocalAppData%\Programs"),
        os.path.expandvars(r"%AppData%\Microsoft\Windows\Start Menu\Programs"),
        os.path.expandvars(r"%ProgramData%\Microsoft\Windows\Start Menu\Programs")
    ]
    
    for d in search_dirs:
        if not os.path.exists(d):
            continue
        for root, dirs, files in os.walk(d):
            for file in files:
                full_path = os.path.join(root, file)
                if browser_name.replace(" ", "") in full_path.lower().replace(" ", ""):
                    if file.lower() in ["launcher.exe", "opera.exe", "chrome.exe"]:
                        return full_path
                    elif file.lower().endswith(".lnk") and "opera" in browser_name:
                        return full_path
    return None

def open_url_dynamically(command_target):
    command_target = command_target.strip()
    chosen_browser = None
    for b_name in ["chrome", "google", "opera gx", "opera"]:
        if any(f" {marker}{b_name}" in f" {command_target.lower()}" for marker in ["in ", "using ", "on "]):
            chosen_browser = b_name
            pattern = re.compile(rf"\s+(in|using|on)\s+{b_name}", re.IGNORECASE)
            command_target = pattern.sub("", command_target).strip()
            break

    if not command_target.startswith(("http://", "https://")):
        if "." in command_target and " " not in command_target:
            url_target = "https://" + command_target
        else:
            url_target = f"https://www.google.com/search?q={command_target.replace(' ', '+')}"
    else:
        url_target = command_target

    log_to_console(f"[System] Opening: '{url_target}' (Browser: {chosen_browser or 'Default'})")

    try:
        if chosen_browser:
            browser_path = find_browser_dynamically(chosen_browser)
            if browser_path and os.path.exists(browser_path):
                if browser_path.lower().endswith('.lnk'):
                    dir_name = os.path.dirname(browser_path)
                    found_exe = None
                    for root, dirs, files in os.walk(os.path.dirname(dir_name)):
                        for f in files:
                            if f.lower() in ["launcher.exe", "opera.exe", "chrome.exe"]:
                                found_exe = os.path.join(root, f)
                                break
                        if found_exe:
                            break
                    if found_exe:
                        subprocess.Popen([found_exe, url_target])
                    else:
                        os.startfile(browser_path)
                else:
                    if "opera" in chosen_browser:
                        base_dir = os.path.dirname(browser_path)
                        version_exes = glob.glob(os.path.join(base_dir, "*", "opera.exe"))
                        if version_exes:
                            subprocess.Popen([version_exes[0], url_target])
                        else:
                            subprocess.Popen([browser_path, url_target])
                    else:
                        subprocess.Popen([browser_path, url_target])
            else:
                log_to_console(f"[Error] Could not find executable path for {chosen_browser}. Aborting launch.")
        else:
            webbrowser.open(url_target)
        log_to_console("[System] Browser launch command initiated.")
    except Exception as e:
        log_to_console(f"[Error] Failed to launch browser: {e}")

LAUNCH_SEARCH_PATTERN = re.compile(r'\b(launch|open|start|run|search)\s+(.*)', re.IGNORECASE)

def sanitize_chat_display_text(text):
    t_lower = text.lower()
    if any(m in t_lower for m in [
        "fence to max", "fen tu mecs", "fan to macs", "fenn tu dumax", "fan to nax", 
        "fans to nax", "thanks to max", "thx to max", "thank you max", "sara thanks to max",
        "fan to max", "fans to max", "fan to mix", "fans to mix", "fan to miks", "fans to miks"
    ]):
        return "Sara, fans to max."
    if any(m in t_lower for m in ["fence to normal", "fen tu normal", "fan to normal", "fans to normal"]):
        return "Sara, fans to normal."
    return text

def handle_command_actions(command_text):
    command_text = fix_glued_command(command_text)
    fan_action = check_fan_action(command_text)
    if fan_action:
        _, msg = switch_fan_profile(fan_action)
        return True, msg
    
    match = LAUNCH_SEARCH_PATTERN.search(command_text)
    if match:
        action = match.group(1).lower()
        app_target = match.group(2).strip()
        cleaned_command = app_target.replace("_", " ")
        
        response_msg = f"Got it! Opening {cleaned_command} right now, watch me work my magic!"
            
        if ("." in app_target and " " not in app_target) or \
           " in " in app_target or " using " in app_target or " on " in app_target or \
           app_target.startswith("http"):
            threading.Thread(target=open_url_dynamically, args=(app_target,)).start()
        elif app_target:
            threading.Thread(target=launch_app_dynamically, args=(app_target,)).start()
            
        return True, response_msg
        
    return False, None

def classify_intent_using_text(text):
    text_lower = text.lower()
    if any(k in text_lower for k in [
        "fan", "fans", "fence", "profile", "cooling", "max", "next", "nax", 
        "normal", "default", "thanks to max", "thx to max", "mix", "miks"
    ]):
        if any(k in text_lower for k in ["max", "next", "nax", "full", "mecs", "macs", "thanks to max", "mix", "miks"]):
            return "fan_max"
        if any(k in text_lower for k in ["normal", "default", "reset", "auto"]):
            return "fan_normal"
    if any(k in text_lower for k in ["open", "launch", "start", "run", "search", "youtube", "google", "opera", "chrome", ".com"]):
        return "launch_action"
    return "general"

def process_transcript(spoken_text):
    global awake_until_time
    
    normalized_spoken = fix_glued_command(spoken_text)
    clean_text = re.sub(r'[^\w\s\.:/\-_]', '', normalized_spoken).lower().strip()
    clean_text = clean_text.strip('.,!?- ')
    
    if not clean_text or len(clean_text) < 2:
        return

    is_currently_awake = time.time() < awake_until_time
    log_to_console(f"[PC Mic Heard]: '{clean_text}'")

    wake_patterns = ["sara", "sarah", "hoshikawa", "hoshkawa"]
    has_wake_word = any(p in clean_text for p in wake_patterns)

    command_text = clean_text
    for p in sorted(wake_patterns, key=len, reverse=True):
        command_text = command_text.replace(p, "").strip()

    command_text = command_text.strip('.,!?- ')
    filler_noise = ["um", "uh", "ah", "oh", "hey", "hi", "so", "ok", "okay", "the", "a", "an"]
    if command_text in filler_noise:
        command_text = ""

    is_pure_name = any(clean_text == p for p in wake_patterns) or (has_wake_word and not command_text)

    if not has_wake_word and not is_currently_awake:
        log_to_console(f"[PC Mic Ignored]: '{clean_text}' (Sara is asleep)")
        return

    target_eval_text = command_text if command_text else clean_text
    intent = classify_intent_using_text(target_eval_text)
    
    fan_action = check_fan_action(clean_text) or check_fan_action(command_text)
    if not fan_action:
        if intent == "fan_max":
            fan_action = "max"
        elif intent == "fan_normal":
            fan_action = "normal"

    chat_display_text = sanitize_chat_display_text(normalized_spoken)

    if has_wake_word and is_pure_name:
        awake_until_time = time.time() + 30.0
        log_to_console("[System] Pure name called. Sara is awake for the next single instruction.")
        log_to_chat("You", "Sara")
        trigger_voice_alert("command", is_pure_wake=True)
        return

    action_handled = False
    response_message = None
    if fan_action:
        _, response_message = switch_fan_profile(fan_action)
        action_handled = True
    elif intent == "launch_action":
        action_handled, response_message = handle_command_actions(command_text if command_text else clean_text)
        if not action_handled and has_wake_word:
            action_handled, response_message = handle_command_actions(clean_text)

    if not action_handled:
        safe_text = (command_text if command_text else clean_text).replace("_", " ")
        general_responses = [
            f"Ehh?! {safe_text}? What is that even supposed to mean, are you making things up just to tease me?",
            f"Hah? {safe_text}?! Don't just mumble random words and expect me to know what you want, baka!",
            f"E-excuse me?! {safe_text}?! If you're going to give me orders, at least make sense!",
        ]
        response_message = random.choice(general_responses)

    if has_wake_word:
        awake_until_time = 0
        log_to_console("[System] Wake word + command recognized.")
        log_to_chat("You", chat_display_text)
        trigger_voice_alert("command", custom_text=response_message, is_pure_wake=False)
    elif is_currently_awake:
        awake_until_time = 0 
        log_to_console("[System] Single follow-up instruction processed.")
        log_to_chat("You", chat_display_text)
        trigger_voice_alert("command", custom_text=response_message, is_pure_wake=False)

def pc_mic_listener_loop():
    if not PC_MIC_AVAILABLE:
        log_to_console("[Error] 'sounddevice' or 'numpy' not found. PC mic disabled.")
        update_esp_status("Audio: Unavailable", "#FF3333")
        return

    sample_rate = 16000
    block_duration = 0.5
    block_size = int(sample_rate * block_duration)

    recording_buffer = []
    is_speaking = False
    silent_chunks_count = 0
    max_silent_chunks = 3

    def callback(indata, frames, time_info, status):
        nonlocal is_speaking, silent_chunks_count, recording_buffer
        if is_audio_playing:
            return

        audio_flat = indata.flatten()
        volume = np.max(np.abs(audio_flat))

        if volume > 0.025:
            if not is_speaking:
                is_speaking = True
                recording_buffer = []
            silent_chunks_count = 0
            recording_buffer.append(audio_flat.copy())
        elif is_speaking:
            recording_buffer.append(audio_flat.copy())
            silent_chunks_count += 1
            if silent_chunks_count >= max_silent_chunks:
                is_speaking = False
                complete_audio = np.concatenate(recording_buffer)
                recording_buffer = []
                if len(complete_audio) > 0:
                    threading.Thread(target=transcribe_dynamic_block, args=(complete_audio, sample_rate)).start()

    try:
        log_to_console("[System] Initializing VAD PC Microphone listener...")
        with sd.InputStream(samplerate=sample_rate, channels=1, callback=callback, blocksize=block_size, dtype='float32'):
            log_to_console("[System] VAD PC Microphone listener active and ready.")
            while True:
                time.sleep(0.1)
    except Exception as e:
        log_to_console(f"PC Mic Stream Error: {e}")
        update_esp_status("Audio: Error", "#FF3333")

def transcribe_dynamic_block(audio_np, sample_rate):
    if whisper_model is None or audio_np is None or len(audio_np) == 0:
        return
    try:
        segments, info = whisper_model.transcribe(
            audio_np,
            language="en",
            temperature=0.0,
            without_timestamps=True,
            condition_on_previous_text=False
        )
        
        segments_list = list(segments)
        if segments_list:
            avg_no_speech_prob = sum(seg.no_speech_prob for seg in segments_list) / len(segments_list)
            if avg_no_speech_prob > 0.4:
                return
            spoken_text = " ".join(seg.text for seg in segments_list).strip()
            if spoken_text:
                process_transcript(spoken_text)
    except Exception as e:
        log_to_console(f"Transcription Error: {e}")

def run_flask():
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)

def fit_image_to_box(img, target_w, target_h, bg_color):
    try:
        bg_img = img.copy()
        bg_w, bg_h = bg_img.size
        target_aspect = target_w / target_h
        bg_aspect = bg_w / bg_h
        if bg_aspect > target_aspect:
            new_h = target_h
            new_w = int(target_h * bg_aspect)
            bg_img = bg_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            x_crop = (new_w - target_w) // 2
            bg_img = bg_img.crop((x_crop, 0, x_crop + target_w, target_h))
        else:
            new_w = target_w
            new_h = int(target_w / bg_aspect)
            bg_img = bg_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            y_crop = (new_h - target_h) // 2
            bg_img = bg_img.crop((0, y_crop, target_w, y_crop + target_h))
        
        if PIL_AVAILABLE:
            canvas = bg_img.filter(ImageFilter.GaussianBlur(24))
        else:
            canvas = bg_img
    except Exception:
        canvas = Image.new("RGB", (target_w, target_h), bg_color)

    img_w, img_h = img.size
    target_aspect = target_w / target_h
    img_aspect = img_w / img_h

    if img_aspect > target_aspect:
        new_w = target_w
        new_h = int(target_w / img_aspect)
        img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        y_offset = (target_h - new_h) // 2
        canvas.paste(img_resized, (0, y_offset))
    else:
        new_h = target_h
        new_w = int(target_h * img_aspect)
        img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
        x_offset = (target_w - new_w) // 2
        canvas.paste(img_resized, (x_offset, 0))

    return canvas

class SaraRibbon(QWidget):
    """A compact bow with plump, diamond-like ears, tilted 30 degrees left and sized for a 64x64 canvas."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(64, 64)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        painter.save()

        # Tilt 30 degrees left (counter-clockwise) around the center (32, 32)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        painter.translate(cx, cy)
        painter.rotate(-30)
        painter.translate(-cx, -cy)

        # Right Ear (Yellow Gradient) - Drawn first so it's in the back
        path_right = QPainterPath()
        path_right.moveTo(32, 54)
        path_right.cubicTo(28, 40, 32, 23, 42, 12)
        path_right.cubicTo(52, 31, 44, 43, 32, 54)
        path_right.closeSubpath()

        grad_right = QLinearGradient(52, 12, 32, 54)
        grad_right.setColorAt(0.0, QColor("#FFF59D"))
        grad_right.setColorAt(0.5, QColor("#FFD54F"))
        grad_right.setColorAt(1.0, QColor("#F57F17"))
        painter.setBrush(grad_right)
        painter.drawPath(path_right)

        # Left Ear (Pink Gradient) - Drawn last so it's in the front
        path_left = QPainterPath()
        path_left.moveTo(32, 54)
        path_left.cubicTo(20, 43, 12, 31, 22, 12)
        path_left.cubicTo(32, 23, 36, 40, 32, 54)
        path_left.closeSubpath()

        grad_left = QLinearGradient(12, 12, 32, 54)
        grad_left.setColorAt(0.0, QColor("#FF80AB"))
        grad_left.setColorAt(0.5, QColor("#FF4081"))
        grad_left.setColorAt(1.0, QColor("#AD1457"))
        painter.setBrush(grad_left)
        painter.drawPath(path_left)

        painter.restore()

class SynthesizerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(300, 100)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def paintEvent(self, event):
        global spectrum_heights, audio_amplitude, is_audio_playing
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        width = self.width()
        height = self.height()
        if width <= 0 or height <= 0:
            return

        painter.setBrush(QColor(35, 35, 35, 220))
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(QRectF(12, 18, width - 24, height - 36), 10, 10)

        cx, cy = width / 2, height / 2
        num_bars = 32
        bar_width = 5.0
        spacing = 2.0
        total_width = num_bars * (bar_width + spacing) - spacing
        start_x = max(0, (width - total_width) / 2)

        for i in range(num_bars):
            if is_audio_playing and audio_amplitude > 0.001:
                center_distance = abs(i - (num_bars / 2)) / (num_bars / 2)
                individual_multiplier = 1.0 - (center_distance * 0.3)
                boost = random.uniform(0.8, 1.4)
                target_h = int(min(max(audio_amplitude * 180 * individual_multiplier * boost, 4), height - 44))
            else:
                phase = time.time() * 4.5 + (i * 0.28)
                target_h = int(6 + 5 * abs(math.sin(phase)))

            spectrum_heights[i] = spectrum_heights[i] * 0.5 + target_h * 0.5
            h = spectrum_heights[i]

            x = start_x + i * (bar_width + spacing)
            y1, y2 = cy - (h / 2), cy + (h / 2)

            if i % 3 == 0:
                bar_color = QColor("#FF477E")
            elif i % 3 == 1:
                bar_color = QColor("#00B4D8")
            else:
                bar_color = QColor("#ffc300")

            painter.setBrush(bar_color)
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(QRectF(x, y1, bar_width, y2 - y1), 2, 2)

class RoundedProgressBar(QProgressBar):
    """Custom-painted, rounded 3D progress bar with clean clipped ends."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextVisible(False)
        self.setMinimumHeight(1)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent; border: none;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.setPen(Qt.NoPen)

        # Leave a tiny margin so the rounded edge is never clipped.
        rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        if rect.width() <= 2 or rect.height() <= 2:
            return

        radius = rect.height() / 2.0

        # Dark lower edge / shadow gives the track a subtle 3D depth.
        shadow_rect = rect.translated(0, 0)
        shadow_gradient = QLinearGradient(shadow_rect.topLeft(), shadow_rect.bottomLeft())
        shadow_gradient.setColorAt(0.0, QColor(180, 154, 75, 100))
        shadow_gradient.setColorAt(1.0, QColor(120, 105, 65, 150))
        painter.setBrush(shadow_gradient)
        painter.drawRoundedRect(shadow_rect, radius, radius)

        # Recessed track with a soft top-to-bottom gradient.
        track_gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        track_gradient.setColorAt(0.0, QColor("#FFF7D6"))
        track_gradient.setColorAt(0.45, QColor("#E9DCA8"))
        track_gradient.setColorAt(1.0, QColor("#C8B77B"))
        painter.setBrush(track_gradient)
        painter.drawRoundedRect(rect, radius, radius)

        span = self.maximum() - self.minimum()
        if span <= 0:
            return

        fraction = (self.value() - self.minimum()) / span
        fraction = max(0.0, min(1.0, fraction))
        fill_width = rect.width() * fraction
        if fill_width <= 0.0:
            return

        # Clip the fill to the full pill shape, preventing square corners.
        pill_path = QPainterPath()
        pill_path.addRoundedRect(rect, radius, radius)
        painter.save()
        painter.setClipPath(pill_path)

        fill_rect = QRectF(rect.left(), rect.top(), fill_width, rect.height())
        fill_gradient = QLinearGradient(fill_rect.topLeft(), fill_rect.bottomLeft())
        fill_gradient.setColorAt(0.0, QColor("#FFF27A"))
        fill_gradient.setColorAt(0.22, QColor("#FFD84A"))
        fill_gradient.setColorAt(0.70, QColor("#FFC52F"))
        fill_gradient.setColorAt(1.0, QColor("#E89B16"))
        painter.setBrush(fill_gradient)
        painter.drawRect(fill_rect)

        # Glossy upper highlight and warm lower shading for a 3D appearance.
        highlight_rect = QRectF(rect.left(), rect.top(), fill_width, rect.height() * 0.34)
        highlight_gradient = QLinearGradient(highlight_rect.topLeft(), highlight_rect.bottomLeft())
        highlight_gradient.setColorAt(0.0, QColor(255, 255, 255, 150))
        highlight_gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter.setBrush(highlight_gradient)
        painter.drawRect(highlight_rect)

        lower_rect = QRectF(rect.left(), rect.top() + rect.height() * 0.72,
                            fill_width, rect.height() * 0.28)
        lower_gradient = QLinearGradient(lower_rect.topLeft(), lower_rect.bottomLeft())
        lower_gradient.setColorAt(0.0, QColor(170, 95, 0, 0))
        lower_gradient.setColorAt(1.0, QColor(150, 75, 0, 90))
        painter.setBrush(lower_gradient)
        painter.drawRect(lower_rect)
        painter.restore()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        load_wallpaper_list()

        self.setWindowTitle("Hoshikawa Sara Voice Assistant")
        base_dir = os.path.dirname(os.path.abspath(__file__))
        icon_candidates = [
            os.path.join(base_dir, "Icon", "sara_app_icon_generated_highres.ico"),
            os.path.join(base_dir, "Icon", "sara_app_icon_clean.ico"),
            os.path.join(base_dir, "Icon", "sara_app_icon.ico"),
            os.path.join(base_dir, "sara_app_icon_generated_highres.ico"),
            os.path.join(base_dir, "sara_app_icon_clean.ico"),
            os.path.join(base_dir, "sara_app_icon.ico"),
            os.path.join(base_dir, "wallpapers", "sara_app_icon_generated_highres.ico"),
            os.path.join(base_dir, "wallpapers", "sara_app_icon_clean.ico"),
            os.path.join(base_dir, "wallpapers", "sara_app_icon.ico"),
            os.path.join(WALLPAPER_DIR, "sara_app_icon_generated_highres.ico"),
            os.path.join(WALLPAPER_DIR, "sara_app_icon_clean.ico"),
            os.path.join(WALLPAPER_DIR, "sara_app_icon.ico"),
        ]
        app_icon_path = next((path for path in icon_candidates if os.path.exists(path)), None)
        if app_icon_path:
            self.setWindowIcon(QIcon(app_icon_path))
        self.setFixedSize(1160, 680)

        self.bg_label = QLabel(self)
        self.bg_label.setGeometry(0, 0, 1160, 680)

        self.curr_left_pil = None
        self.curr_right_pil = None
        self.next_left_pil = None
        self.next_right_pil = None
        self.is_animating = False
        self.anim_start_time = 0.0

        scrollbar_stylesheet = """
            QScrollBar:vertical {
                border: none;
                background: rgba(0, 0, 0, 20);
                width: 6px;
                margin: 0px;
                border-radius: 3px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 71, 126, 180);
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """

        light_box_stylesheet = """
            QTextEdit {
                background-color: rgba(255, 255, 255, 140);
                border: 1px solid #ffffff;
                border-radius: 10px;
                color: #2B2B2B;
                font-family: 'Comic Sans MS', 'Segoe UI Rounded', 'Bubblegum Sans', sans-serif;
                font-size: 8.5pt;
                padding: 12px 14px;
            }
        """ + scrollbar_stylesheet

        # Sara high-resolution generated status bar asset with live progress overlays.
        # Match the artwork width to the chat log box width (300 px) while preserving
        # the original artwork aspect ratio.
        chat_box_width = 300
        artwork_width, artwork_height = 520, 162
        artwork_scale = chat_box_width / artwork_width
        status_width = chat_box_width
        status_height = round(artwork_height * artwork_scale)

        self.status_box = QFrame(self)
        self.status_box.setGeometry(20, 20, status_width, status_height)
        self.status_box.setStyleSheet("QFrame { background: transparent; border: none; }")

        base_dir = os.path.dirname(os.path.abspath(__file__))
        status_asset_candidates = [
            os.path.join(base_dir, "Icon", "sara_statusbar_generated_highres.png"),
            os.path.join(base_dir, "sara_statusbar_generated_highres.png"),
            os.path.join(base_dir, "wallpapers", "sara_statusbar_generated_highres.png"),
            os.path.join(WALLPAPER_DIR, "sara_statusbar_generated_highres.png"),
        ]
        status_asset_path = next((p for p in status_asset_candidates if os.path.exists(p)), None)

        self.status_art = QLabel(self.status_box)
        self.status_art.setGeometry(0, 0, status_width, status_height)
        self.status_art.setScaledContents(True)
        self.status_art.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        if status_asset_path:
            self.status_art.setPixmap(QPixmap(status_asset_path))
        self.status_art.lower()

        # The artwork contains a static bar. Hide that portion with a rounded
        # track, then place a rounded determinate progress bar over it.
        # Original bar coordinates are scaled from the 520x162 artwork size so
        # the live overlay stays aligned after the artwork is reduced to 300 px wide.
        bar_x = round(195 * artwork_scale)
        bar_y = round(105 * artwork_scale)
        bar_w = round(280 * artwork_scale)
        bar_h = round(28 * artwork_scale)

        # No separate grey backdrop: RoundedProgressBar paints its own 3D track.

        self.sara_ribbon = SaraRibbon(self)
        self.sara_ribbon.hide()

        # Do not draw floating status text over the artwork. The generated image
        # already contains its own labels, while the progress bar remains live.
        self.ai_text_lbl = QLabel(self.status_box)
        self.ai_text_lbl.hide()
        self.esp_text_lbl = QLabel(self.status_box)
        self.esp_text_lbl.hide()

        self.status_progress = RoundedProgressBar(self.status_box)
        self.status_progress.setGeometry(bar_x, bar_y, bar_w, bar_h)
        self.status_progress.setRange(0, 100)
        self.status_progress.setValue(0)
        self.status_progress.setTextVisible(False)
        self.status_progress.setAttribute(Qt.WA_TranslucentBackground, True)
        self.status_progress.raise_()

        # Each subsystem advances independently; the displayed bar is their average.
        self.ai_progress_value = 0
        self.esp_progress_value = 0
        self.ai_ready = False
        self.esp_ready = False
        self.ai_error = False
        self.esp_error = False

        self.chat_widget = QTextEdit(self)
        self.chat_widget.setGeometry(20, 560, 300, 100)
        self.chat_widget.setReadOnly(True)
        self.chat_widget.setStyleSheet(light_box_stylesheet)

        self.synth_widget = SynthesizerWidget(self)
        self.synth_widget.setGeometry(430, 560, 300, 100)

        self.log_toggle_btn = QPushButton("▼", self)
        self.log_toggle_btn.setGeometry(950, 536, 80, 15)
        self.log_toggle_btn.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 140);
                color: #2B2B2B;
                font-family: 'Segoe UI', sans-serif;
                font-size: 7.5pt;
                font-weight: bold;
                border: 1px solid #ffffff;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 200);
            }
        """)
        self.log_toggle_btn.clicked.connect(self.toggle_system_logs)

        self.console_widget = QTextEdit(self)
        self.console_widget.setGeometry(840, 560, 300, 100)
        self.console_widget.setReadOnly(True)
        self.console_widget.setStyleSheet(light_box_stylesheet)

        self.init_wallpapers()

        self.wallpaper_timer = QTimer(self)
        self.wallpaper_timer.timeout.connect(self.start_wallpaper_transition)
        self.wallpaper_timer.start(10000)

        self.trans_timer = QTimer(self)
        self.trans_timer.timeout.connect(self.update_transition_frame)

        self.anim_timer = QTimer(self)
        self.anim_timer.timeout.connect(self.synth_widget.update)
        self.anim_timer.start(25)

        self.loading_timer = QTimer(self)
        self.loading_timer.timeout.connect(self.animate_loading_status)
        self.loading_timer.start(500)

        ui_bridge.append_console_signal.connect(self.append_console)
        ui_bridge.append_chat_signal.connect(self.append_chat)
        ui_bridge.ai_status_signal.connect(self.update_ai_status_text)
        ui_bridge.esp_status_signal.connect(self.update_esp_status_text)

    def animate_loading_status(self):
        # Determinate-looking progress: advance gradually and pause at 92% until
        # the corresponding subsystem reports Ready. It never loops backwards.
        if not self.ai_ready and not self.ai_error:
            self.ai_progress_value = min(92, self.ai_progress_value + 4)
        if not self.esp_ready and not self.esp_error:
            self.esp_progress_value = min(92, self.esp_progress_value + 4)

        self.refresh_combined_progress()

    def refresh_combined_progress(self):
        values = [self.ai_progress_value, self.esp_progress_value]
        self.status_progress.setValue(round(sum(values) / len(values)))

    def init_wallpapers(self):
        global wallpaper_images
        w, h = 1160, 680
        bg_color = get_theme_color()
        border_px = 1
        col_w = (w - border_px) // 2
        right_w = w - col_w - border_px

        if wallpaper_images and PIL_AVAILABLE:
            left_path = random.choice(wallpaper_images)
            right_path = random.choice(wallpaper_images)
            if len(wallpaper_images) > 1:
                while right_path == left_path:
                    right_path = random.choice(wallpaper_images)

            try:
                self.curr_left_pil = fit_image_to_box(Image.open(left_path).convert("RGB"), col_w, h, bg_color)
            except Exception:
                self.curr_left_pil = Image.new("RGB", (col_w, h), bg_color)

            try:
                self.curr_right_pil = fit_image_to_box(Image.open(right_path).convert("RGB"), right_w, h, bg_color)
            except Exception:
                self.curr_right_pil = Image.new("RGB", (right_w, h), bg_color)

            self.render_static_background(self.curr_left_pil, self.curr_right_pil)

    def render_static_background(self, left_img, right_img):
        w, h = 1160, 680
        bg_color = get_theme_color()
        border_px = 1
        col_w = (w - border_px) // 2

        canvas = Image.new("RGB", (w, h), bg_color)
        canvas.paste(left_img, (0, 0))
        canvas.paste(right_img, (col_w + border_px, 0))

        data = canvas.convert("RGBA").tobytes("raw", "RGBA")
        q_img = QImage(data, canvas.width, canvas.height, QImage.Format_RGBA8888)
        self.bg_label.setPixmap(QPixmap.fromImage(q_img))

    def start_wallpaper_transition(self):
        if self.is_animating or not wallpaper_images or not PIL_AVAILABLE:
            return

        w, h = 1160, 680
        bg_color = get_theme_color()
        border_px = 1
        col_w = (w - border_px) // 2
        right_w = w - col_w - border_px

        left_path = random.choice(wallpaper_images)
        right_path = random.choice(wallpaper_images)
        if len(wallpaper_images) > 1:
            while right_path == left_path:
                right_path = random.choice(wallpaper_images)

        try:
            self.next_left_pil = fit_image_to_box(Image.open(left_path).convert("RGB"), col_w, h, bg_color)
        except Exception:
            self.next_left_pil = Image.new("RGB", (col_w, h), bg_color)

        try:
            self.next_right_pil = fit_image_to_box(Image.open(right_path).convert("RGB"), right_w, h, bg_color)
        except Exception:
            self.next_right_pil = Image.new("RGB", (right_w, h), bg_color)

        self.is_animating = True
        self.anim_start_time = time.time()
        self.trans_timer.start(16)

    def update_transition_frame(self):
        elapsed = time.time() - self.anim_start_time
        move_duration = 0.8
        delay_right = 0.3
        total_duration = move_duration + delay_right

        t_left_raw = min(1.0, max(0.0, elapsed / move_duration))
        t_right_raw = min(1.0, max(0.0, (elapsed - delay_right) / move_duration))

        w, h = 1160, 680
        bg_color = get_theme_color()
        border_px = 1
        col_w = (w - border_px) // 2
        right_w = w - col_w - border_px

        def ease(val):
            if val <= 0: return 0.0
            if val >= 1: return 1.0
            if val < 0.5:
                return 4 * val * val * val
            else:
                return 1 - pow(-2 * val + 2, 3) / 2

        t_progress_left = ease(t_left_raw)
        t_progress_right = ease(t_right_raw)

        offset_y_left = int(t_progress_left * h)
        offset_y_right = int(t_progress_right * h)

        canvas = Image.new("RGB", (w, h), bg_color)

        left_strip = Image.new("RGB", (col_w, h * 2), bg_color)
        left_strip.paste(self.curr_left_pil, (0, 0))
        left_strip.paste(self.next_left_pil, (0, h))
        left_col_frame = left_strip.crop((0, offset_y_left, col_w, offset_y_left + h))
        canvas.paste(left_col_frame, (0, 0))

        right_strip = Image.new("RGB", (right_w, h * 2), bg_color)
        right_strip.paste(self.next_right_pil, (0, 0))
        right_strip.paste(self.curr_right_pil, (0, h))
        right_col_frame = right_strip.crop((0, h - offset_y_right, right_w, 2 * h - offset_y_right))
        canvas.paste(right_col_frame, (col_w + border_px, 0))

        data = canvas.convert("RGBA").tobytes("raw", "RGBA")
        q_img = QImage(data, canvas.width, canvas.height, QImage.Format_RGBA8888)
        self.bg_label.setPixmap(QPixmap.fromImage(q_img))

        if elapsed >= total_duration:
            self.trans_timer.stop()
            self.curr_left_pil = self.next_left_pil
            self.curr_right_pil = self.next_right_pil
            self.is_animating = False

    def append_console(self, text):
        self.console_widget.append(text)

    def append_chat(self, sender, formatted_text):
        self.chat_widget.insertPlainText(formatted_text)
        self.chat_widget.verticalScrollBar().setValue(self.chat_widget.verticalScrollBar().maximum())

    def update_ai_status_text(self, text, color):
        self.ai_text_lbl.setText(text)
        is_ready = "Ready" in text
        is_error = "Error" in text
        self.ai_ready = is_ready
        self.ai_error = is_error

        if is_ready:
            self.ai_progress_value = 100
        elif is_error:
            self.ai_progress_value = 0
        else:
            self.ai_progress_value = max(self.ai_progress_value, 8)

        self.refresh_combined_progress()

    def update_esp_status_text(self, text, color):
        self.esp_text_lbl.setText(text)
        is_ready = "Ready" in text or "Mic" in text or "Connected" in text
        is_error = "Error" in text or "Unavailable" in text
        self.esp_ready = is_ready
        self.esp_error = is_error

        if is_ready:
            self.esp_progress_value = 100
        elif is_error:
            self.esp_progress_value = 0
        else:
            self.esp_progress_value = max(self.esp_progress_value, 8)

        self.refresh_combined_progress()

    def toggle_system_logs(self):
        is_visible = self.console_widget.isVisible()
        self.console_widget.setVisible(not is_visible)
        
        # Switch arrow direction: ▲ when closed, ▼ when open
        self.log_toggle_btn.setText("▲" if is_visible else "▼")

    def toggle_system_logs(self):
        # Prevent spam-clicking while animating
        if hasattr(self, 'anim_group') and self.anim_group.state() == QParallelAnimationGroup.Running:
            return

        self.anim_group = QParallelAnimationGroup(self)

        # Animation for the log box
        console_anim = QPropertyAnimation(self.console_widget, b"geometry")
        console_anim.setDuration(250)
        console_anim.setEasingCurve(QEasingCurve.InOutQuad)

        # Animation for the toggle button
        btn_anim = QPropertyAnimation(self.log_toggle_btn, b"geometry")
        btn_anim.setDuration(250)
        btn_anim.setEasingCurve(QEasingCurve.InOutQuad)

        current_console_rect = self.console_widget.geometry()
        current_btn_rect = self.log_toggle_btn.geometry()

        if current_console_rect.height() > 20:
            # Collapse downwards: console anchors at y=660, height shrinks to 0
            console_anim.setStartValue(current_console_rect)
            console_anim.setEndValue(QRect(840, 660, 300, 0))

            # Button follows downwards (from y=536 to y=636)
            btn_anim.setStartValue(current_btn_rect)
            btn_anim.setEndValue(QRect(950, 636, 80, 15))

            self.log_toggle_btn.setText("▲")
        else:
            # Expand upwards: console top pushes back to y=560, height expands to 100
            console_anim.setStartValue(current_console_rect)
            console_anim.setEndValue(QRect(840, 560, 300, 100))

            # Button follows upwards (from y=636 back to y=536)
            btn_anim.setStartValue(current_btn_rect)
            btn_anim.setEndValue(QRect(950, 536, 80, 15))

            self.log_toggle_btn.setText("▼")

        self.anim_group.addAnimation(console_anim)
        self.anim_group.addAnimation(btn_anim)
        self.anim_group.start()

if __name__ == "__main__":
    #check_single_instance()

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=load_ai_models_in_background, daemon=True).start()

    if PC_MIC_AVAILABLE:
        threading.Thread(target=pc_mic_listener_loop, daemon=True).start()
    else:
        update_esp_status("Audio: Unavailable", "#FF3333")

    qt_app = QApplication(sys.argv)
    main_window_ref = MainWindow()
    main_window_ref.show()
    sys.exit(qt_app.exec())