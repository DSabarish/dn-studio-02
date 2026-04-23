# Architecture - DN Studio

> Auto-generated from repository scan. Last updated: 2026-04-22.
> Re-run after code changes to refresh Mermaid diagrams.

---

## L0 - Slide View

One-glance overview. Designed to fit a 16:9 presentation slide.

```mermaid
flowchart LR
    User(["User"])

    subgraph UI["Streamlit UI"]
        App["app.py"]
        Views["Doc + GAP Views"]
    end

    subgraph Core["Pipeline Core"]
        Pipe["pipeline_service"]
        Proc["runner + ingest"]
        LLM["Gemini client"]
    end

    subgraph IO["Storage + Output"]
        GCS[("GCS bucket")]
        Run[("run/ artifacts")]
        Docs["DOCX/Excel files"]
    end

    User -->|"upload/input"| App
    App -->|"route view"| Views
    Views -->|"trigger run"| Pipe
    Pipe -->|"process files"| Proc
    Pipe -->|"prompt call"| LLM
    Pipe -->|"read/write"| Run
    Pipe -->|"sync/upload"| GCS
    Pipe -->|"generate"| Docs
```

---

## L1 - User View

What a person can do with this system.

```mermaid
flowchart TD
    User(["👤 User"])

    subgraph Actions["What users can do"]
        A1["Choose Doc or GAP view"]
        A2["Upload media/docs or add gs:// URIs"]
        A3["Run full BPD pipeline"]
        A4["Run GAP analysis"]
        A5["Download ZIP, DOCX, Excel, JSON"]
    end

    subgraph Outcomes["What they receive"]
        O1["Transcripts and context files"]
        O2["BPD JSON and DOCX output"]
        O3["SAP GAP DOCX and Excel"]
        O4["Artifacts stored in GCS"]
    end

    User --> A1
    User --> A2
    User --> A3 --> O1
    A3 --> O2
    User --> A4 --> O3
    A3 --> O4
    A4 --> O4
    User --> A5
```

### Notes
- Doc flow supports local uploads and `gs://` inputs.
- Transcription engine is selectable: local Whisper or AssemblyAI API.
- GAP flow operates on selected run folders and emits DOCX/Excel outputs.

---

## L2 - System View

Services, data stores, and communication paths.

```mermaid
flowchart LR
    Browser(["🌐 Browser"])

    subgraph App["DN Studio App Container/Process"]
        ST["Streamlit app.py"]
        DV["ui/doc_view.py"]
        GV["ui/gap_view.py"]
        PS["backend/pipeline_service.py"]
        RUN["backend/runner.py"]
        CTX["backend/build_context.py"]
        BP["backend/build_prompt.py"]
        ART["backend/artifacts.py"]
        LLM["backend/llm_client.py"]
        GCSC["backend/gcs_client.py"]
    end

    subgraph Local["Local Storage"]
        RUNF[("run/ folders")]
        TMP[("temp media files")]
        DOCX[("DOCX/Excel artifacts")]
    end

    subgraph External["External Services"]
        GEM(["Gemini Vertex API"])
        GCS(["Google Cloud Storage"])
        AAI(["AssemblyAI API"])
    end

    Browser -->|"HTTPS"| ST
    ST -->|"function call"| DV
    ST -->|"function call"| GV
    DV -->|"invoke pipeline"| PS
    GV -->|"invoke GAP pipeline"| BP
    PS -->|"load/normalize inputs"| RUN
    PS -->|"build prompt text"| BP
    PS -->|"build context from docs"| CTX
    PS -->|"model generate_content"| LLM
    PS -->|"json->docx via node"| ART
    PS -->|"artifact upload/list/sync"| GCSC
    RUN -->|"write transcripts/json"| RUNF
    RUN -->|"stage/delete temp media"| TMP
    ART -->|"read/write zip/docx/json"| DOCX
    GCSC -->|"GCS API"| GCS
    LLM -->|"Vertex API"| GEM
    RUN -.->|"async polling/upload"| AAI
```

### Notes
- **Deployment**: Docker image runs Streamlit + Python backend + Node (for DOCX template generation).
- **Auth**: Google client libraries use ADC/service-account credentials; AssemblyAI uses `ASSEMBLYAI_API_KEY`.
- **Async**: AssemblyAI path can run parallel workers while preserving ordered persistence.

---

## L3 - Codebase View

Module-level dependency graph for core app flow (dependent -> dependency).

```mermaid
flowchart TD
    Entry(["▶ app.py"])

    subgraph UI["ui/"]
        UDV["doc_view.py"]
        UGV["gap_view.py"]
        USH["shared.py"]
    end

    subgraph Core["backend/"]
        BPS["pipeline_service.py"]
        BRN["runner.py"]
        BIN["ingest.py"]
        BTR["transcriptions.py"]
        BCT["build_context.py"]
        BPR["build_prompt.py"]
        BAR["artifacts.py"]
        BLC["llm_client.py"]
        BGC["gcs_client.py"]
        BPE["pipeline_to_excel.py"]
        BSG["sap_gap_analyser_updated.py"]
    end

    subgraph Root["root"]
        CFG["⭐ config.py"]
    end

    Entry -->|"imports"| UDV
    Entry -->|"imports"| UGV
    Entry -->|"imports"| CFG
    UDV -->|"imports"| BPS
    UDV -->|"imports"| BAR
    UDV -->|"imports"| USH
    UDV -->|"imports"| CFG
    UGV -->|"imports"| BPR
    UGV -->|"imports"| BAR
    UGV -->|"imports"| BPE
    UGV -->|"imports"| BSG
    UGV -->|"imports"| USH
    UGV -->|"imports"| CFG
    USH -->|"imports"| BAR
    BPS -->|"imports"| BRN
    BPS -->|"imports"| BIN
    BPS -->|"imports"| BTR
    BPS -->|"imports"| BCT
    BPS -->|"imports"| BPR
    BPS -->|"imports"| BAR
    BPS -->|"imports"| BLC
    BLC -->|"imports"| CFG
    BIN -->|"imports"| BGC
    BAR -->|"imports"| BGC
```

### Notes
- **Entry point**: `app.py`
- **Core dependency**: `config.py` is shared by UI and model client paths.
- **Circular dependencies**: none clearly detected in the scanned core flow.

---

## Open Questions

- [ ] GAP pipeline internals in `backend/sap_gap_analyser_updated.py` are treated as a black box here (single entry function).
- [ ] Frontend `templates/` JS dependency flow is runtime-invoked via subprocess, not represented as imports.
- [ ] Non-core scripts under `gap/` may represent legacy or standalone flows outside the main Streamlit path.
