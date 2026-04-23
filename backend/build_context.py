from pathlib import Path

try:
    from all2md import to_markdown
except Exception:
    to_markdown = None

try:
    # Import order matters for layout-aware extraction.
    import pymupdf.layout  # noqa: F401
    import pymupdf4llm
except Exception:
    pymupdf4llm = None

_gemini_import_error = None
try:
    from backend.llm_client import gemini_call
except Exception as exc:
    _gemini_import_error = exc
    gemini_call = None


SUPPORTED_DOC_EXT = {".pdf", ".docx", ".txt", ".md"}
SUPPORTED_IMG_EXT = {".png", ".jpg", ".jpeg"}
MAX_FILES_FOR_CONTEXT = 20  # Token-saving limit for this POC


def _safe_read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def doc_to_md(file_path: str) -> str:
    path = Path(file_path)
    ext = path.suffix.lower()

    if ext == ".md" or ext == ".txt":
        return _safe_read_text(path)

    if ext == ".pdf" and pymupdf4llm is not None:
        try:
            # Drop repetitive page artifacts where possible.
            return pymupdf4llm.to_markdown(str(path), header=False, footer=False).strip()
        except Exception as exc:
            # Fall back to all2md path below if available.
            if to_markdown is None:
                return f"(Could not parse {path.name} with pymupdf4llm: {exc})"

    if to_markdown is None:
        return f"(Skipping {path.name}: all2md not installed.)"

    try:
        return to_markdown(str(path)).strip()
    except Exception as exc:
        return f"(Could not parse {path.name}: {exc})"


def image_to_md(
    image_path: Path,
    prompt: str = (
        "Analyze this image and provide concise bullet points covering: "
        "(1) key visible content, "
        "(2) describe the image,"
        "(3) try to identify the context of the image, think like why the user has given, what he might want fomr this image, and then return the context of the image in a few bullet points."
    ),
) -> str:
    if gemini_call is None:
        import_error_text = f"{_gemini_import_error}" if _gemini_import_error else "unknown import error"
        return (
            f"- Image file: {image_path.name}\n"
            f"- Description: (Gemini helper import failed: {import_error_text})"
        )

    try:
        description = gemini_call(image_path, prompt=prompt).strip()
    except Exception as exc:
        return f"- Image file: {image_path.name}\n- Description: (Gemini call failed: {exc})"

    if not description:
        description = "(No description returned by Gemini)"
    return f"- Image file: {image_path.name}\n- Description:\n{description}"


def build_context_from_files(file_paths, process_images: bool = False) -> str:
    paths = [Path(p) for p in file_paths][:MAX_FILES_FOR_CONTEXT]

    docs_md = []
    images_md = []

    for f in paths:
        ext = f.suffix.lower()
        if ext in SUPPORTED_DOC_EXT:
            docs_md.append(f"### {f.name}\n\n{doc_to_md(str(f))}".strip())
        elif ext in SUPPORTED_IMG_EXT and process_images:
            images_md.append(f"### {f.name}\n\n{image_to_md(f)}".strip())

    context = "# DOCUMENTS\n\n"
    context += "\n\n".join(docs_md) if docs_md else "(No supported documents found)"

    context += "\n\n# IMAGES\n\n"
    if process_images:
        context += "\n\n".join(images_md) if images_md else "(No supported images found)"
    else:
        context += "(Skipped: set process_images=True to enable)"

    return context.strip()


def build_context(folder_path: str, process_images: bool = True) -> str:
    folder = Path(folder_path)
    files = [
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in (SUPPORTED_DOC_EXT | SUPPORTED_IMG_EXT)
    ]
    files = sorted(files)[:MAX_FILES_FOR_CONTEXT]
    return build_context_from_files(files, process_images=process_images)
