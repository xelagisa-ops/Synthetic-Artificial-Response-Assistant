import sounddevice as sd
import numpy as np
import requests
import time

SERVER_URL = "http://127.0.0.1:5000/upload_audio"
SAMPLE_RATE = 16000  # Must match the 16kHz setting in pc_server.py
CHANNELS = 1
CHUNK_DURATION = 4.0  # Duration of each recorded chunk in seconds (adjust as needed)

def main():
    print("\n--- Hoshikawa Hands-Free PC Mic Client ---")
    print("Listening automatically... Speak anytime! (Press Ctrl+C to stop)")
    
    samples_per_chunk = int(SAMPLE_RATE * CHUNK_DURATION)

    try:
        while True:
            # Record a continuous block of audio automatically
            audio_data = sd.rec(samples_per_chunk, samplerate=SAMPLE_RATE, channels=CHANNELS, dtype='int16')
            sd.wait() # Wait until the chunk finishes recording
            
            audio_bytes = audio_data.tobytes()

            try:
                # Send the chunk straight to your Flask server
                response = requests.post(SERVER_URL, data=audio_bytes, timeout=5)
                if response.status_code == 200:
                    res_json = response.json()
                    # Optional: Print server feedback if it recognized speech or woke up
                    if res_json.get("status") == "success":
                        print(f"[Client] Sent chunk successfully. Response: {res_json}")
            except requests.exceptions.ConnectionError:
                print("⚠️ [Client Error] Could not connect to server. Is pc_server.py running?")
                time.sleep(2)
            except Exception as e:
                print(f"❌ [Client Error]: {e}")

    except KeyboardInterrupt:
        print("\nStopping hands-free microphone client.")

if __name__ == "__main__":
    main()