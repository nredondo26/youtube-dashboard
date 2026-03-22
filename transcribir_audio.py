import whisper
import os

def transcribe_audio():
    # Ruta del archivo
    audio_path = r"C:\Users\nerb\Downloads\Nunca les reces a las ANIMAS del purgatorio ｜ Relatos siniestros de ALMAS EN PENA.mp3"
    
    if not os.path.exists(audio_path):
        print(f"Error: No se encontró el archivo en {audio_path}")
        return

    print("Cargando modelo Whisper (esto puede tardar la primera vez)...")
    model = whisper.load_model("base")
    
    print("Transcribiendo audio... Por favor espera.")
    result = model.transcribe(audio_path, language="es")
    
    # Guardar texto puro
    with open("transcripcion.txt", "w", encoding="utf-8") as f:
        f.write(result["text"])
    
    # Guardar formato guion (con tiempos)
    with open("guion_tiempos.txt", "w", encoding="utf-8") as f:
        for segment in result["segments"]:
            start = segment["start"]
            end = segment["end"]
            text = segment["text"].strip()
            f.write(f"[{int(start//60):02d}:{int(start%60):02d} - {int(end//60):02d}:{int(end%60):02d}] {text}\n")

    print("\n¡Hecho! Se han generado los archivos:")
    print("- transcripcion.txt (Texto corrido)")
    print("- guion_tiempos.txt (Texto con marcas de tiempo)")

if __name__ == "__main__":
    transcribe_audio()
