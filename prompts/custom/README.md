# ─────────────────────────────────────────────────────────────────────────────
# FOLDER: prompts/custom/
# PURPOSE: Drop your custom document prompt files here.
#
# STEPS TO ADD A CUSTOM DOCUMENT TYPE:
#
# 1. Create your prompt files in this folder:
#       prompts/custom/p1_schema.md      ← Phase 1 (schema design)
#       prompts/custom/p2_populate.md    ← Phase 2 (populate) — optional
#
# 2. Create your JS template in templates/:
#       templates/custom_template.js
#
# 3. Add an entry to config/document_types.yaml (keys are UPPERCASE):
#
#    CUSTOM:
#      label: "Custom"
#      description: "Your custom document"
#      llm_calls: 2
#      p1_prompt: "prompts/custom/p1_schema.md"
#      p2_prompt: "prompts/custom/p2_populate.md"
#      js_template: "templates/custom_template.js"
#      output_dir: "outputs/custom/"
#      output_filename: "Custom_output.docx"
#
# 4. That's it. The UI card and pipeline routing are automatic.
# ─────────────────────────────────────────────────────────────────────────────

# This folder is intentionally empty.
# Add your custom prompt .md files here when ready.

