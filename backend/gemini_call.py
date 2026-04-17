from pathlib import Path

from google import genai
from google.genai import types

PROJECT_ID = "dn-studio-01"
LOCATION = "asia-south1"
MODEL_NAME = "gemini-2.5-flash"

DEFAULT_PROMPT = (
    "Provide a concise description of the provided content. "
    "Return 2-4 short bullet points based only on observable details."
)

_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}

def gemini_call(image_path: Path, prompt: str = DEFAULT_PROMPT) -> str:
    path = Path(image_path)
    mime_type = _MIME_TYPES.get(path.suffix.lower())
    if mime_type is None:
        raise ValueError(f"Unsupported image type: {path.suffix}")

    final_prompt = (prompt or DEFAULT_PROMPT).strip()
    client = genai.Client(vertexai=True, project=PROJECT_ID, location=LOCATION)

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=[
            final_prompt,
            types.Part.from_bytes(data=path.read_bytes(), mime_type=mime_type),
        ],
        config=types.GenerateContentConfig(
            temperature=0.2,
            max_output_tokens=300,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        ),
    )
    text = (response.text or "").strip()
    if text:
        return text

    # Minimal fallback: some responses populate candidates.parts instead of response.text.
    chunks = []
    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if part_text:
                chunks.append(part_text.strip())
    return "\n".join(c for c in chunks if c).strip()


#-------------------------------------------------------------------
if __name__ == "__main__":
    
    image_path = Path(r"test-files\img_02.png")
    
    prompt = (
        "Analyze this image and provide concise bullet points covering: "
        "(1) key visible content, "
        "(2) describe the image,"
        "(3) try to identify the context of the image, think like why the user has given, what he might want fomr this image, and then return the context of the image in a few bullet points."
    )
    
    description = gemini_call(image_path, prompt=prompt)

    print(description)