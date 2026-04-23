const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  AlignmentType, BorderStyle, WidthType, ShadingType,
  VerticalAlign, LevelFormat, PageOrientation
} = require("docx");
const fs = require("fs");
const path = require("path");

// ─── COLOUR PALETTE ────────────────────────────────────────────────────────
const DARK_BLUE = "1F3864";
const MID_BLUE = "2E75B6";
const LIGHT_BLUE = "D6E4F0";
const TEAL_HDR = "1F4E79";
const ALT_ROW = "EBF5FB";
const WHITE = "FFFFFF";
const GREY_BORDER = "ADB9CA";
const GREEN_FULL = "D9EAD3";
const ORANGE_PART = "FCE5CD";
const RED_NONE = "F4CCCC";
const RICEFW_COLS = {
  Report: "D9EAD3",
  Interface: "FFF2CC",
  Enhancement: "FCE5CD",
  Form: "EAD1DC",
  Conversion: "CFE2F3",
  Workflow: "F4CCCC",
};

const border = { style: BorderStyle.SINGLE, size: 1, color: GREY_BORDER };
const borders = { top: border, bottom: border, left: border, right: border };
const noBorder = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };
const noBorders = { top: noBorder, bottom: noBorder, left: noBorder, right: noBorder };

function cell(
  text,
  {
    width,
    fill = WHITE,
    bold = false,
    color = "000000",
    fontSize = 18,
    align = AlignmentType.LEFT,
    vAlign = VerticalAlign.CENTER,
  } = {}
) {
  return new TableCell({
    borders,
    width: { size: width, type: WidthType.DXA },
    shading: { fill, type: ShadingType.CLEAR },
    verticalAlign: vAlign,
    margins: { top: 80, bottom: 80, left: 120, right: 120 },
    children: [
      new Paragraph({
        alignment: align,
        children: [new TextRun({ text: String(text ?? ""), bold, color, size: fontSize, font: "Arial" })],
      }),
    ],
  });
}

function hdrCell(text, width) {
  return new TableCell({
    borders,
    width: { size: width, type: WidthType.DXA },
    shading: { fill: TEAL_HDR, type: ShadingType.CLEAR },
    verticalAlign: VerticalAlign.CENTER,
    margins: { top: 100, bottom: 100, left: 120, right: 120 },
    children: [
      new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [new TextRun({ text: String(text ?? ""), bold: true, color: WHITE, size: 18, font: "Arial" })],
      }),
    ],
  });
}

function para(text, { bold = false, size = 22, before = 80, after = 80, color = "000000" } = {}) {
  return new Paragraph({
    spacing: { before, after },
    children: [new TextRun({ text: String(text ?? ""), bold, size, font: "Arial", color })],
  });
}

function sectionHeading(text) {
  return new Paragraph({
    spacing: { before: 300, after: 120 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: MID_BLUE, space: 1 } },
    children: [new TextRun({ text: String(text ?? ""), bold: true, size: 28, font: "Arial", color: DARK_BLUE })],
  });
}

function bullet(text) {
  return new Paragraph({
    numbering: { reference: "bullets", level: 0 },
    spacing: { before: 40, after: 40 },
    children: [new TextRun({ text: String(text ?? ""), size: 20, font: "Arial" })],
  });
}

function buildCapBadge(status) {
  const fills = { FULL: GREEN_FULL, PARTIAL: ORANGE_PART, NONE: RED_NONE };
  return fills[status] || WHITE;
}

function safeArray(v) {
  return Array.isArray(v) ? v : [];
}

function makeRequirementsTable(requirements) {
  const colWidths = [700, 8000];
  return new Table({
    width: { size: 8700, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [
      new TableRow({
        tableHeader: true,
        children: [hdrCell("ID", colWidths[0]), hdrCell("Requirement", colWidths[1])],
      }),
      ...requirements.map((r, i) =>
        new TableRow({
          children: [
            cell(r.id, { width: colWidths[0], fill: i % 2 === 0 ? WHITE : ALT_ROW, bold: true, fontSize: 18 }),
            cell(r.text, { width: colWidths[1], fill: i % 2 === 0 ? WHITE : ALT_ROW, fontSize: 18 }),
          ],
        })
      ),
    ],
  });
}

function makeNormalizedTable(normalized) {
  const colWidths = [600, 1400, 1200, 2700, 2800];
  return new Table({
    width: { size: 8700, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [
      new TableRow({
        tableHeader: true,
        children: [
          hdrCell("ID", colWidths[0]),
          hdrCell("Actor", colWidths[1]),
          hdrCell("Action", colWidths[2]),
          hdrCell("Object", colWidths[3]),
          hdrCell("Conditions", colWidths[4]),
        ],
      }),
      ...normalized.map((r, i) =>
        new TableRow({
          children: [
            cell(r.id, { width: colWidths[0], fill: i % 2 === 0 ? WHITE : ALT_ROW, bold: true }),
            cell(r.actor, { width: colWidths[1], fill: i % 2 === 0 ? WHITE : ALT_ROW }),
            cell(r.action, { width: colWidths[2], fill: i % 2 === 0 ? WHITE : ALT_ROW }),
            cell(r.object, { width: colWidths[3], fill: i % 2 === 0 ? WHITE : ALT_ROW }),
            cell(r.condition, { width: colWidths[4], fill: i % 2 === 0 ? WHITE : ALT_ROW }),
          ],
        })
      ),
    ],
  });
}

function makeCapabilityTable(capability) {
  const colWidths = [600, 1200, 5700, 1200];
  return new Table({
    width: { size: 8700, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [
      new TableRow({
        tableHeader: true,
        children: [
          hdrCell("ID", colWidths[0]),
          hdrCell("Status", colWidths[1]),
          hdrCell("Assessment Notes", colWidths[2]),
          hdrCell("Gap?", colWidths[3]),
        ],
      }),
      ...capability.map((r, i) =>
        new TableRow({
          children: [
            cell(r.id, { width: colWidths[0], fill: buildCapBadge(r.status), bold: true }),
            cell(r.status, { width: colWidths[1], fill: buildCapBadge(r.status), bold: true, align: AlignmentType.CENTER }),
            cell(r.assessment_note || r.note || "", { width: colWidths[2], fill: i % 2 === 0 ? WHITE : ALT_ROW }),
            cell(r.gap ? "YES" : "NO", {
              width: colWidths[3],
              fill: r.gap ? RED_NONE : GREEN_FULL,
              bold: true,
              align: AlignmentType.CENTER,
            }),
          ],
        })
      ),
    ],
  });
}

function makeGapSummaryTable(gaps) {
  const colWidths = [900, 700, 1300, 5800];
  return new Table({
    width: { size: 8700, type: WidthType.DXA },
    columnWidths: colWidths,
    rows: [
      new TableRow({
        tableHeader: true,
        children: [
          hdrCell("Issue ID", colWidths[0]),
          hdrCell("Req", colWidths[1]),
          hdrCell("RICEFW Type", colWidths[2]),
          hdrCell("Title & Solution Strategy", colWidths[3]),
        ],
      }),
      ...gaps.map((g, i) => {
        const fill = RICEFW_COLS[g.ricefw] || WHITE;
        return new TableRow({
          children: [
            cell(g.gap_id, { width: colWidths[0], fill: i % 2 === 0 ? WHITE : ALT_ROW, bold: true }),
            cell(g.req_id, { width: colWidths[1], fill: i % 2 === 0 ? WHITE : ALT_ROW }),
            cell(g.ricefw, { width: colWidths[2], fill, bold: true, align: AlignmentType.CENTER }),
            new TableCell({
              borders,
              width: { size: colWidths[3], type: WidthType.DXA },
              shading: { fill: i % 2 === 0 ? WHITE : ALT_ROW, type: ShadingType.CLEAR },
              margins: { top: 80, bottom: 80, left: 120, right: 120 },
              children: [
                new Paragraph({
                  spacing: { before: 0, after: 60 },
                  children: [new TextRun({ text: g.title || "", bold: true, size: 18, font: "Arial", color: DARK_BLUE })],
                }),
                ...safeArray(g.solution_bullets).map((d) =>
                  new Paragraph({
                    numbering: { reference: "bullets", level: 0 },
                    spacing: { before: 20, after: 20 },
                    children: [new TextRun({ text: d || "", size: 18, font: "Arial" })],
                  })
                ),
              ],
            }),
          ],
        });
      }),
    ],
  });
}

function extractKeyDecisions(data) {
  const out = [];
  const hasInterface = safeArray(data.gap_analysis).some((g) => g.ricefw === "Interface");
  const hasTax = safeArray(data.requirements).some((r) => /tax/i.test(r.text || ""));
  const hasReclass = safeArray(data.requirements).some((r) => /reclass/i.test(r.text || ""));

  if (hasInterface) {
    out.push("Integration includes interface-driven transfer to downstream invoicing/accounting process.");
  }
  if (hasTax) {
    out.push("Tax handling is a key design area and requires explicit mapping and governance.");
  }
  if (hasReclass) {
    out.push("Post-invoice reclassification logic remains a critical control point.");
  }
  out.push("Gap items are classified into RICEFW for implementation planning.");
  return out.slice(0, 6);
}

async function main() {
  const inputPath = process.argv[2];
  const outputPath = process.argv[3] || path.resolve(process.cwd(), "SAP_Gap_Analysis.docx");

  if (!inputPath) {
    throw new Error("Usage: node js_template.js <input_json_path> <output_docx_path>");
  }

  const raw = fs.readFileSync(inputPath, "utf-8");
  const data = JSON.parse(raw);

  const requirements = safeArray(data.requirements);
  const normalized = safeArray(data.normalized);
  const capability = safeArray(data.capability_assessment);
  const gaps = safeArray(data.gap_analysis);
  const noGap = safeArray(data.no_gap_confirmations);
  const actions = safeArray(data.open_actions);

  const gapReqIds = new Set(gaps.map((g) => g.req_id).filter(Boolean));
  const noGapReqIds = noGap.map((n) => n.id).filter(Boolean);
  const dateText = data.meeting_date || "N/A";

  const doc = new Document({
    numbering: {
      config: [
        {
          reference: "bullets",
          levels: [
            {
              level: 0,
              format: LevelFormat.BULLET,
              text: "\u2022",
              alignment: AlignmentType.LEFT,
              style: { paragraph: { indent: { left: 360, hanging: 220 } } },
            },
          ],
        },
      ],
    },
    styles: {
      default: { document: { run: { font: "Arial", size: 22 } } },
      paragraphStyles: [
        {
          id: "Heading1",
          name: "Heading 1",
          basedOn: "Normal",
          next: "Normal",
          quickFormat: true,
          run: { size: 36, bold: true, font: "Arial", color: DARK_BLUE },
          paragraph: { spacing: { before: 360, after: 200 }, outlineLevel: 0 },
        },
        {
          id: "Heading2",
          name: "Heading 2",
          basedOn: "Normal",
          next: "Normal",
          quickFormat: true,
          run: { size: 28, bold: true, font: "Arial", color: MID_BLUE },
          paragraph: { spacing: { before: 240, after: 120 }, outlineLevel: 1 },
        },
      ],
    },
    sections: [
      {
        properties: {
          page: {
            size: { width: 15840, height: 12240, orientation: PageOrientation.LANDSCAPE },
            margin: { top: 1080, right: 1080, bottom: 1080, left: 1080 },
          },
        },
        children: [
          // ── COVER BLOCK ────────────────────────────────────────────────────
          new Paragraph({
            spacing: { before: 480, after: 80 },
            children: [
              new TextRun({
                text: data.meeting_title || "SAP Gap Analysis",
                bold: true,
                size: 48,
                font: "Arial",
                color: DARK_BLUE,
              }),
            ],
          }),
          new Paragraph({
            spacing: { before: 0, after: 60 },
            children: [
              new TextRun({
                text: "SAP Gap Analysis | RICEFW Classification",
                bold: false,
                size: 28,
                font: "Arial",
                color: "555555",
              }),
            ],
          }),
          new Paragraph({
            spacing: { before: 0, after: 40 },
            children: [
              new TextRun({
                text: `Workshop Date: ${dateText}   |   Document Status: DRAFT`,
                size: 20,
                font: "Arial",
                color: "888888",
              }),
            ],
          }),
          new Paragraph({
            spacing: { before: 20, after: 400 },
            border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: MID_BLUE, space: 1 } },
            children: [new TextRun({ text: "", size: 20 })],
          }),

          // ── SCOPE & CONTEXT ────────────────────────────────────────────────
          sectionHeading("Scope & Context"),
          para(data.scope_context || "No scope context provided.", { size: 20, after: 100 }),
          para("Key design decisions confirmed in the workshop:", { bold: true, size: 20 }),
          ...extractKeyDecisions(data).map((d) => bullet(d)),
          new Paragraph({ spacing: { before: 0, after: 200 }, children: [] }),

          // ── STEP 1 ─────────────────────────────────────────────────────────
          sectionHeading("Step 1 — Requirement Extraction"),
          para(
            "The following atomic business requirements were extracted from the workshop transcript. Each requirement represents a single, actionable need.",
            { size: 20, after: 120 }
          ),
          makeRequirementsTable(requirements),
          new Paragraph({ spacing: { before: 0, after: 200 }, children: [] }),

          // ── STEP 2 ─────────────────────────────────────────────────────────
          sectionHeading("Step 2 — Requirement Normalization"),
          para("Each requirement is decomposed into Actor, Action, Object, and Condition to remove ambiguity.", {
            size: 20,
            after: 120,
          }),
          makeNormalizedTable(normalized),
          new Paragraph({ spacing: { before: 0, after: 200 }, children: [] }),

          // ── STEP 3/4 ───────────────────────────────────────────────────────
          sectionHeading("Steps 3 & 4 — SAP Capability Assessment & Gap Identification"),
          para(
            "Standard SAP S/4HANA IS-U capability is assessed conservatively. PARTIAL or NONE ratings constitute a GAP and proceed to RICEFW classification.",
            { size: 20, after: 80 }
          ),
          para("Legend:   FULL = No Gap   |   PARTIAL = Configuration gap or minor enhancement needed   |   NONE = Custom development required", {
            size: 18,
            color: "555555",
            after: 120,
          }),
          makeCapabilityTable(capability),
          new Paragraph({ spacing: { before: 0, after: 200 }, children: [] }),

          // ── STEP 5/6/7 ─────────────────────────────────────────────────────
          sectionHeading("Steps 5, 6 & 7 — RICEFW Classification, Solution Strategy & Final Gap Analysis Table"),
          para(
            `Only GAP items (${[...gapReqIds].join(", ") || "N/A"}) are carried forward. Requirements ${noGapReqIds.join(", ") || "N/A"} are addressed through standard SAP configuration and require no custom development.`,
            { size: 20, after: 80 }
          ),
          para("RICEFW Colour Key:", { bold: true, size: 18 }),
          new Table({
            width: { size: 8700, type: WidthType.DXA },
            columnWidths: [1450, 1450, 1450, 1450, 1450, 1450],
            rows: [
              new TableRow({
                children: Object.entries(RICEFW_COLS).map(([type, fill]) =>
                  new TableCell({
                    borders,
                    width: { size: 1450, type: WidthType.DXA },
                    shading: { fill, type: ShadingType.CLEAR },
                    margins: { top: 60, bottom: 60, left: 100, right: 100 },
                    children: [
                      new Paragraph({
                        alignment: AlignmentType.CENTER,
                        children: [new TextRun({ text: type, bold: true, size: 18, font: "Arial" })],
                      }),
                    ],
                  })
                ),
              }),
            ],
          }),
          new Paragraph({ spacing: { before: 0, after: 160 }, children: [] }),
          makeGapSummaryTable(gaps),
          new Paragraph({ spacing: { before: 0, after: 200 }, children: [] }),

          // ── NO-GAP ITEMS ────────────────────────────────────────────────────
          sectionHeading("Confirmation: Requirements Covered by Standard SAP Configuration (No Custom Development)"),
          new Table({
            width: { size: 8700, type: WidthType.DXA },
            columnWidths: [700, 2200, 5800],
            rows: [
              new TableRow({
                tableHeader: true,
                children: [hdrCell("Req", 700), hdrCell("Topic", 2200), hdrCell("Resolution", 5800)],
              }),
              ...noGap.map((r, i) =>
                new TableRow({
                  children: [
                    cell(r.id, { width: 700, bold: true, fill: GREEN_FULL }),
                    cell(r.topic || "", { width: 2200, fill: i % 2 === 0 ? WHITE : ALT_ROW }),
                    cell(r.resolution || "", { width: 5800, fill: i % 2 === 0 ? WHITE : ALT_ROW }),
                  ],
                })
              ),
            ],
          }),
          new Paragraph({ spacing: { before: 0, after: 200 }, children: [] }),

          // ── NEXT STEPS ──────────────────────────────────────────────────────
          sectionHeading("Open Actions & Next Steps"),
          new Table({
            width: { size: 8700, type: WidthType.DXA },
            columnWidths: [700, 3500, 2500, 2000],
            rows: [
              new TableRow({
                tableHeader: true,
                children: [hdrCell("#", 700), hdrCell("Action", 3500), hdrCell("Owner", 2500), hdrCell("Target", 2000)],
              }),
              ...actions.map((a, i) =>
                new TableRow({
                  children: [
                    cell(a.action_number, { width: 700, bold: true, fill: i % 2 === 0 ? WHITE : ALT_ROW }),
                    cell(a.description || "", { width: 3500, fill: i % 2 === 0 ? WHITE : ALT_ROW }),
                    cell(a.owner || "", { width: 2500, fill: i % 2 === 0 ? WHITE : ALT_ROW }),
                    cell(a.target || "", { width: 2000, fill: i % 2 === 0 ? WHITE : ALT_ROW }),
                  ],
                })
              ),
            ],
          }),
          new Paragraph({
            spacing: { before: 240, after: 60 },
            border: { top: { style: BorderStyle.SINGLE, size: 4, color: GREY_BORDER, space: 1 } },
            children: [
              new TextRun({
                text: `Document prepared from workshop recording | ${dateText} | DRAFT — For internal review only`,
                size: 16,
                font: "Arial",
                color: "999999",
              }),
            ],
          }),
        ],
      },
    ],
  });

  const buffer = await Packer.toBuffer(doc);
  fs.writeFileSync(outputPath, buffer);
  console.log(`Done. Wrote ${outputPath}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});

