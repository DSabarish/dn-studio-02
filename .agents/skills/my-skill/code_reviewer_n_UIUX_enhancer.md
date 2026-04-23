---
name: streamlit-gemini-code-review
description: >
  Use this skill when the user wants a critical code review AND UI/UX review of a
  Streamlit + Gemini 2.5 Flash + GCS + Cloud Run application. Triggers include:
  "review my app", "audit the code", "reduce duplication", "merge files",
  "make the UI better", "human approval", "streamline", or any combination of
  "code review" and "ui review" on a Streamlit/Python project.
  Goal: surgically reduce dead code, merge similar files, surface every flaw that
  blocks human approval, and produce a prioritised fix list — not a rewrite.
license: Complete terms in LICENSE.txt
---

## Purpose

Perform two reviews in a single pass:

1. **Critical Code Review** — correctness, performance, duplication, dead code, security
2. **UI/UX Review** — human-approval blockers, layout, feedback, accessibility, aesthetics

Both reviews are **surgical and opinionated**. The output is a prioritised, actionable
fix list — not a rewrite, not a compliment sandwich.

---

## Golden Rules (Never Break These)

1. **Read every `.py` file before writing a single comment.**
2. **Never rename a Streamlit page file** — it breaks URL routing.
3. **Never flag a function as dead without grepping every call site first.**
4. **Never suggest merging files unless the combined result stays under ~300 lines.**
5. **Never recommend a refactor that changes behaviour** — flag it instead as
   "BEHAVIOUR CHANGE RISK: verify before applying."
6. **Duplicate logic is always a bug.** Two code paths that do the same thing
   will eventually diverge — call this out explicitly.

---

## Phase 0 — Map the Codebase First

Run these before reading anything:

```bash
# Full file tree
find . -type f | grep -v __pycache__ | grep -v .git | sort

# Heaviest files
find . -name "*.py" | xargs wc -l | sort -rn | head -20

# Find duplicate import patterns (GCS clients created in >1 file)
grep -rn "storage.Client\|genai.configure\|GenerativeModel" . --include="*.py"

# Find hardcoded strings (bucket names, model names, project IDs)
grep -rn "gs://\|gemini-\|\.appspot\.com\|project=" . --include="*.py"

# Find dead session state keys (set but never read, or read but never set)
grep -rn "st.session_state\." . --include="*.py" | sort
```

Internally answer before proceeding:
- What does the app actually do? What is the user flow end-to-end?
- How many places create a GCS client? How many call `genai.configure`?
- Which files are similar enough to merge?
- What blocks human approval today (crashes, blank screens, no feedback)?

---

## Phase 1 — Critical Code Review

### 1.1 Duplication Scanner

For each pair of similar files or functions, produce one entry:

```
DUPLICATE — [file_a.py:fn_name] and [file_b.py:fn_name]
Both do: <one sentence>
Fix: Extract to services/gcs.py → upload_bytes(). Delete both originals.
Risk: LOW — pure function, no side effects.
```

### 1.2 Dead Code Scanner

Only flag after grepping call sites:

```
DEAD CODE — [file.py:function_name] (line N)
Grep result: 0 call sites found.
Fix: Delete.
```

### 1.3 Hardcoded Values Scanner

```
HARDCODED — [file.py line N]: bucket_name = "my-real-bucket"
Fix: Move to config.py → GCS_BUCKET_NAME = os.getenv("GCS_BUCKET_NAME", "")
Risk: SECURITY — never commit real bucket/project names.
```

### 1.4 Performance Audit

Check every function that touches GCS or Gemini:

```
PERF — [file.py:fn_name]
Problem: GCS client created on every rerun (no cache).
Fix: Wrap with @st.cache_resource.

PERF — [file.py:fn_name]
Problem: list_blobs() called at module level — runs on every keystroke.
Fix: Move inside button callback. Add @st.cache_data(ttl=300).
```

### 1.5 Error Handling Audit

```
BAD EXCEPT — [file.py line N]: bare `except:` silently swallows all errors.
Fix:
    except ValueError as e:
        st.error(f"Invalid input: {e}")
    except Exception as e:
        logger.error("Unexpected: %s", e, exc_info=True)
        st.error("Something went wrong. Please try again.")
```

### 1.6 Session State Audit

```
SESSION STATE BUG — [file.py line N]: reads st.session_state["key"] without
checking existence. Will crash on first load.
Fix: Add to init_session_state() → "key": default_value
```

### 1.7 Security Audit

```
SECURITY — [file.py line N]: API key passed as positional arg and may appear
in logs or tracebacks.
Fix: Load from os.getenv() only. Never log or display the key value.

SECURITY — Dockerfile copies .env into the image.
Fix: Add .env to .dockerignore. Pass secrets as Cloud Run env vars only.
```

### 1.8 Files-to-Merge Candidates

Only recommend merging if:
- Both files are under 150 lines
- They import each other OR share >40% of their top-level imports
- The merged result stays under 300 lines

```
MERGE CANDIDATE — utils/helpers.py + utils/formatters.py
Reason: helpers.py has 3 functions; all 3 are pure text transforms already
in formatters.py with different names.
Action: Move unique functions to formatters.py. Delete helpers.py.
Update 2 import sites: [page_a.py line 4], [page_b.py line 7].
```

---

## Phase 2 — UI/UX Review (Human Approval Focus)

The standard: **would a non-technical stakeholder approve this in a 5-minute demo?**
If the answer is "no" or "maybe", it is a blocking issue.

### 2.1 First Impression Audit

Open the app cold. Answer:
- Is there a title and subtitle that explain what this does in <10 words?
- Is there a clear primary action (one button, obvious placement)?
- Does the page look broken or empty before the user does anything?

```
BLOCKER — App renders a blank white page until the user uploads a file.
Fix: Add a hero section or instructional placeholder:
    st.info("Upload a PDF above to get started.")
    st.image("assets/empty_state.png")  # or use st.markdown with an SVG
```

### 2.2 Feedback Loop Audit

Every user action must have visible feedback within 200ms (spinner, progress, message).

```
BLOCKER — Gemini call takes 4–8s with no spinner. User assumes the app froze.
Fix:
    with st.spinner("Analysing document…"):
        result = generate_text(prompt)

BLOCKER — File upload success is silent. No confirmation shown.
Fix:
    st.success(f"✓ Uploaded: {uploaded_file.name} ({uploaded_file.size // 1024} KB)")
```

### 2.3 Error Message Audit

Every `st.error()` must be:
- Human-readable (no stack traces, no exception class names)
- Actionable ("Try again" / "Check your file format" / "Contact support")

```
BAD UX — st.error(str(e)) exposes internal error: "404 GET https://storage..."
Fix:
    st.error("Could not load the file. Check that the file exists and try again.")
    logger.error("GCS download failed: %s", e, exc_info=True)  # internal only
```

### 2.4 Layout Audit

```
LAYOUT — All inputs stacked vertically in a single column. Wastes horizontal space.
Fix: Use st.columns([1, 2]) — controls left, output right.

LAYOUT — Upload button and Submit button are visually identical weight.
Fix: Style primary CTA with type="primary":
    st.button("Generate Report", type="primary")
    st.button("Reset", type="secondary")
```

### 2.5 Loading State Audit

```
UX — Long Gemini response appears all at once after a 6s wait.
Fix: Stream the response:
    with st.chat_message("assistant"):
        st.write_stream(stream_text(prompt))
```

### 2.6 Empty State Audit

Every list, table, and result area must handle the zero-data case:

```
UX — st.dataframe(df) with empty df renders a confusing 0-row table.
Fix:
    if df.empty:
        st.info("No results yet. Upload a file to begin.")
    else:
        st.dataframe(df)
```

### 2.7 Mobile / Narrow Viewport Audit

```
LAYOUT — Three-column metric row collapses unreadably on narrow screens.
Fix: Wrap in a try/except or use responsive column count:
    cols = st.columns(min(3, st.session_state.get("col_count", 3)))
```

### 2.8 Aesthetic Quick Wins (Single CSS Block Only)

Add to `app.py` — one block, no scattered inline styles anywhere else:

```python
st.markdown("""
<style>
    /* Tighten top padding */
    .main .block-container { padding-top: 1.5rem; max-width: 860px; }

    /* Consistent button radius */
    .stButton > button { border-radius: 8px; font-weight: 600; letter-spacing: 0.01em; }

    /* Softer input fields */
    .stTextInput > div > div > input,
    .stTextArea textarea {
        border-radius: 8px;
        border: 1px solid #e0e0e0;
    }

    /* Remove default hamburger menu clutter in demos */
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)
```

---

## Phase 3 — Prioritised Fix List (Output Format)

After both reviews, produce a single list sorted by severity:

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CRITICAL (fix before any demo or deployment)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
1. [SECURITY]   Hardcoded API key in services/gemini.py line 12
2. [BLOCKER UX] No spinner on 6s Gemini call — app appears frozen
3. [CRASH]      KeyError on st.session_state["result"] — key never initialised
4. [SECURITY]   .env copied into Docker image

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HIGH (fix before stakeholder review)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
5. [PERF]       GCS client created on every rerun in 3 files
6. [DUPLICATE]  upload logic duplicated in app.py and pages/upload.py
7. [UX]         Bare st.error(str(e)) leaks internal error messages to users
8. [UX]         Empty state missing on results table

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
MEDIUM (polish pass)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
9.  [CODE]    utils/helpers.py is 3 functions — merge into utils/formatters.py
10. [CODE]    Dead function parse_response() — 0 call sites (grep confirmed)
11. [UX]      Upload success is silent — add st.success() confirmation
12. [LAYOUT]  All inputs in single column — use st.columns([1, 2])

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LOW (nice to have)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
13. [CODE]    print() statements in 4 files — replace with logger.*
14. [CODE]    Missing type hints on 6 public functions
15. [UX]      Add st.page_link() breadcrumb on inner pages
16. [LAYOUT]  Add single CSS block to app.py — remove scattered inline styles
```

---

## Phase 4 — Merge Decision Framework

Use this exact logic before recommending any file merge:

```
Is either file > 150 lines?        → DO NOT merge
Do they import each other?          → MERGE (circular dependency risk)
Do they share > 40% top imports?    → MERGE CANDIDATE — check line count
Is merged result > 300 lines?       → DO NOT merge — extract shared module instead
Are they both thin wrappers (<50L)? → MERGE unconditionally
```

When merging, always:
1. State the new filename
2. List every import site that needs updating (file + line number)
3. Flag any name collisions between the two files

---

## Phase 5 — Communicating Results to the User

Deliver in this order — no fluff, no preamble:

**1. What the app does** (2 sentences max)

**2. The 3 most critical findings** (the ones that would kill a demo)

**3. The full prioritised fix list** (Phase 3 format above)

**4. Merge recommendations** (only if clearly beneficial)

**5. One sentence on what they do NOT need to change**
   (anchors the scope and prevents unnecessary churn)

---

## What NOT to Do

- **Never rewrite the whole app** when a targeted fix will do
- **Never merge files that are large** — splitting is cheaper than debugging a 500-line mega-file
- **Never flag style as a blocker** unless it causes functional confusion
- **Never suggest adding more abstractions** to a small app — flat is fast
- **Never remove session state keys** without checking every `.py` file for reads
- **Never recommend `st.experimental_rerun()`** — it is removed; use `st.rerun()`
- **Never put `st.set_page_config()`** anywhere except the very first Streamlit call in `app.py`
- **Never praise the code before delivering the findings** — get to the point