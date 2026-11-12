<div align="center">

# SARA: Hoshikawa Sara Voice Assistant & PC Control Center
### An advanced, local-first AI desktop voice companion and hardware telemetry control system.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![PyTorch](https://img.shields.io/badge/PyTorch-CUDA-ee4c2c.svg?style=flat-square&logo=pytorch&logoColor=white)](https://pytorch.org/)
[![PySide6](https://img.shields.io/badge/GUI-PySide6-41CD52.svg?style=flat-square&logo=qt&logoColor=white)](https://www.qt.io/)
[![Flask](https://img.shields.io/badge/Backend-Flask-000000.svg?style=flat-square&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](LICENSE)

[Overview](#overview) • [Key Features](#key-features) • [System Architecture](#system-architecture) • [Tech Stack](#tech-stack) • [Project Structure](#project-structure) • [Installation](#installation) • [Configuration](#configuration) • [Usage & Commands](#usage--commands) • [Troubleshooting](#troubleshooting) • [Roadmap](#roadmap) • [License](#license)

</div>

---

## Overview

SARA (Hoshikawa Sara Voice Assistant & PC Control Center) is a fully customized, local-first desktop companion application built to merge interactive character persona synthesis with deep operating system and hardware control. Designed with an immersive PySide6 graphical user interface, dual-column dynamic wallpaper slideshows, and real-time audio reactivity, SARA bridges local artificial intelligence pipelines with physical hardware monitoring.

The system integrates continuous Voice Activity Detection (VAD) microphone listening, local speech-to-text transcription via Faster-Whisper, Retrieval-Based Voice Conversion (RVC) voice styling, and asynchronous text-to-speech synthesis. Beyond conversational interaction, SARA natively manages system processes, dynamic application execution, browser URL routing, thermal cooling profiles via FanControl, and network-level adblocking telemetry feeds from an external ESP32 micro-controller.

---

## Key Features

### 1. Advanced Speech & AI Pipelines
* **Local STT Processing:** Utilizes Faster-Whisper (Medium model optimized via CUDA and INT8-Float16 precision) for low-latency, high-accuracy offline speech recognition.
* **Voice Conversion & Synthesis:** Integrates Edge-TTS paired with local Retrieval-Based Voice Conversion (RVC) weights to render authentic character voice output.
* **Wake Word & Sleep State Management:** Features session-aware state tracking supporting pure name wake triggers and time-bounded follow-up instruction windows.

### 2. Immersive Desktop Graphical User Interface
* **Custom PySide6 UI:** Built from the ground up with custom rounded progress bars, styled ribbon widgets, and dynamic layout scaling.
* **Real-time Audio Spectrum Visualizer:** Renders synchronized 32-bar audio frequency reactivity directly linked to active audio output amplitude.
* **Automated Wallpaper Transitions:** Implements custom smooth dual-column sliding transition timers across localized wallpaper asset directories.

### 3. Hardware & System Automation
* **Dynamic Thermal Profile Switching:** Instantly commands FanControl executables and scheduled Windows tasks to toggle cooling profiles (Normal, Max, Custom).
* **Dynamic Application & URL Launcher:** Employs recursive filesystem searching to locate clean executables (.exe) or shortcuts (.lnk) for app launching, alongside intelligent browser-targeted URL routing (Chrome, Opera GX, Edge).
* **IoT Telemetry Integration:** Houses a Flask background routing server to ingest live heartbeats and connection states from external ESP32-based DNS sinkhole adblockers.

---

## System Architecture

The application operates on a multi-threaded asynchronous architecture separating GUI rendering, background audio monitoring, and local web API routing.

```mermaid
graph TD
    A[Microphone Input Stream] -->|VAD Chunking| B(Faster-Whisper STT Model)
    B -->|Transcribed Text| C{Intent & Wake Word Processor}
    C -->|Wake / Command Match| D[Flask Backend Server]
    D -->|REST / Heartbeat| E[ESP32 Adblocker & IoT Devices]
    C -->|Hardware Action| F[FanControl / Subprocess Launcher]
    C -->|Response Generation| G[Edge-TTS & RVC Voice Engine]
    G -->|Audio Stream & Reactivity| H[PySide6 UI & Synthesizer Visualizer]

Domain,Technology / Framework,Purpose
Core Language,Python 3.10+,Primary application runtime and concurrency management
GUI Framework,PySide6 (Qt for Python),"Desktop window management, custom UI widgets, and render loops"
Machine Learning,"PyTorch, Faster-Whisper, RVC",GPU-accelerated local AI inference and voice cloning
Backend & Routing,Flask,Local HTTP server handling IoT heartbeats and external hooks
Audio Processing,"SoundDevice, NumPy, SoundFile","Real-time audio stream capture, buffer manipulation, and analysis"
OS Interoperability,"Win32 APIs, Subprocesses, Glob","Low-level process control, window handle management, and file indexing"

SARA-Voice-PC-Control/
│
├── wallpapers/                   # Dynamic background image repository
├── Icon/                         # High-resolution application icons and UI assets
├── FanControl.Releases-master/   # Embedded hardware cooling control binaries and profiles
├── tts_engine.py                 # RVC and Edge-TTS synthesis wrapper module
├── adblock_monitor.py            # ESP32 telemetry monitoring module
├── sara_core.py                  # Main application loop, GUI, and audio orchestration
├── Start Server.bat              # Automated launcher script with dependency update checks
└── requirements.txt              # Python package dependency manifest

Configuration & Customization
Wallpapers: Place your preferred .png, .jpg, or .webp wallpapers into the wallpapers/ directory. The application automatically indexes and scales them.

Fan Profiles: Configure your custom cooling profiles (Normal.json, Max.json, etc.) inside the FanControl.Releases-master/.../Configurations/ directory.

Voice Models: Ensure your RVC .pth model weights are placed within the designated model directory referenced by tts_engine.py.

Voice Interaction Examples
Wake Word Activation: Say "Sara" or "Hoshikawa" to wake the assistant for the next single instruction window (active for 30 seconds).

Hardware Control: Say "Sara, fans to max" or "Sara, fans to normal" to dynamically adjust cooling profiles.

App & URL Launching: Say "Sara, launch Chrome" or "Sara, open youtube.com using Opera GX" for dynamic process execution and browser routing.

Troubleshooting
Audio Output Issues: Ensure your Windows default playback device matches your desired audio output endpoint. sounddevice relies directly on the active Windows default endpoint.

Lighting Service Conflicts: Avoid running multiple conflicting RGB software suites simultaneously. Keep FanControl initialized through the single application startup pipeline to prevent SMBus collisions.

Missing Module Errors: Always ensure your virtual environment is active before invoking Python scripts manually from the terminal.

Roadmap[x] Local Faster-Whisper and RVC pipeline integration[x] PySide6 desktop GUI with real-time audio spectrum reactivity[x] Hardware fan control profile automation[x] ESP32 network adblocker telemetry monitoring[ ] Expanded plugin architecture for custom smart-home IoT triggers   [ ] Containerization and cross-platform desktop packaging   
