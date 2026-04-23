# ================== CONFIG ==================
MODEL_SIZE = "tiny"          # tiny | base | small | medium | large-v3 | turbo
DEVICE = "cpu"               # "cpu" or "cuda"
COMPUTE_TYPE = "int8"        # int8 (fastest CPU), float16 (GPU), int8_float16
BEAM_SIZE = 1                # 1 = fastest, >1 = better accuracy
VAD_FILTER = True            # Skip silence (faster)
BATCH_SIZE = 8               # Increase if GPU available
MAX_WORKERS = 4              # Parallel files to process

# Input (single file or many)
INPUT_FILES = [
    r"test-files\EMS-20250611.mp4",  # audio or video
]

# ============================================

from faster_whisper import WhisperModel
import tempfile
import os
from concurrent.futures import ThreadPoolExecutor, as_completed

from backend.helper import format_timestamp

try:
    import ffmpeg
except ModuleNotFoundError:
    ffmpeg = None


def extract_audio(input_path):
    """Extract mono 16 kHz wav; return (path, is_temp_file)."""
    if ffmpeg is None:
        return input_path, False

    try:
        temp_audio = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
        temp_audio.close()
        ffmpeg.input(input_path).output(
            temp_audio.name,
            ac=1,
            ar=16000
        ).overwrite_output().run(quiet=True)
        return temp_audio.name, True
    except Exception:
        return input_path, False  # likely already audio


def transcribe(file_path):
    print(f"🟢 Loading model for: {file_path}")
    model = WhisperModel(
        MODEL_SIZE,
        device=DEVICE,
        compute_type=COMPUTE_TYPE
    )

    print("🟢 Preparing input...")
    audio_path, is_temp_audio = extract_audio(file_path)

    try:
        print("🟢 Transcribing...")
        segments, info = model.transcribe(
            audio_path,
            beam_size=BEAM_SIZE,
            vad_filter=VAD_FILTER
        )
    finally:
        if is_temp_audio:
            try:
                os.unlink(audio_path)
            except OSError:
                pass

    full_text = []
    timestamped_segments = []
    for segment in segments:
        full_text.append(segment.text)
        timestamped_segments.append({
            "start": float(segment.start),
            "end": float(segment.end),
            "text": segment.text.strip()
        })

    transcription_with_timestamps = "\n".join(
        f"[{format_timestamp(s['start'])} - {format_timestamp(s['end'])}] {s['text']}"
        for s in timestamped_segments
        if s["text"]
    )

    return {
        "file_path": file_path,
        "language": info.language,
        "duration": info.duration,
        "transcription": " ".join(full_text),
        "segments": timestamped_segments,
        "transcription_with_timestamps": transcription_with_timestamps
    }

####################################################################
def transcribe_many(file_paths, max_workers=MAX_WORKERS):
    """Transcribe many files in parallel."""
    files = [p for p in file_paths if p]
    if not files:
        return []

    worker_count = max(1, min(max_workers, len(files)))
    results = [None] * len(files)

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(transcribe, path): idx for idx, path in enumerate(files)
        }

        for future in as_completed(futures):
            idx = futures[future]
            path = files[idx]
            try:
                results[idx] = future.result()
                print(f"✅ Completed: {path}")
            except Exception as exc:
                results[idx] = {
                    "file_path": path,
                    "error": str(exc)
                }
                print(f"❌ Failed: {path} | {exc}")

    return results


if __name__ == "__main__":
    outputs = transcribe_many(INPUT_FILES)

    print("\n===== RESULT =====")
    for result in outputs:
        print(f"\n--- File: {result['file_path']} ---")
        if "error" in result:
            print("Error:", result["error"])
            continue
        print("Language:", result["language"])
        print("Duration:", result["duration"])
        print("Text:\n", result["transcription"])