"use strict";
/**
 * bpd_template.js — Business Process Document .docx generator
 * DN-Studio by DataNeurus
 *
 * CONTRACT (CLI):
 *   node bpd_template.js <populated-bpd.json> <output.docx>
 *   Logs "SUCCESS: <path>" on completion
 *
 * ── Visual parity ────────────────────────────────────────────────────────────
 * Every colour, size, spacing, indent and border value is taken
 * DIRECTLY from JS_template.js (BRD generator). Nothing is guessed.
 *
 * ── Hierarchical numbering ───────────────────────────────────────────────────
 * Numbers are DYNAMICALLY COMPUTED from array index at render time:
 *   H1  →  "1."        colour: C.primary "003366"  (same as label)
 *   H2  →  "1.1"       colour: C.accent  "0070C0"  (same as label)
 *   H3  →  "1.1.1"     colour: C.dark    "404040"  (same as label)
 *
 * ── Supported H3 formats ────────────────────────────────────────────────────
 *   PARAGRAPH[n]          → n prose paragraphs
 *   BULLETS[n]            → n bullet points
 *   NUMBERED[n]           → n numbered steps
 *   TABLE[n_rows,n_cols]  → header row + n_rows data rows, n_cols columns
 *   FLOWCHART             → styled process flow diagram box
 */

var fs   = require("fs");
var path = require("path");

// ── Resolve docx module (mirrors JS_template.js strategy) ────────────────────
function requireDocx() {
  var candidates = [];
  var nodePath = process.env.NODE_PATH || "";
  nodePath.split(path.delimiter).filter(Boolean).forEach(function(p) {
    candidates.push(path.join(p, "docx"));
  });
  var thisDir = path.dirname(path.resolve(__filename));
  candidates.push(path.join(thisDir, "..", "..", "scripts", "node_modules", "docx"));
  candidates.push(path.join(thisDir, "..", "node_modules", "docx"));
  candidates.push(path.join(thisDir, "node_modules", "docx"));
  candidates.push("docx");
  for (var i = 0; i < candidates.length; i++) {
    try { return require(candidates[i]); } catch (_) {}
  }
  throw new Error("Cannot find module 'docx'. Run: cd templates && npm install");
}
var docx = requireDocx();

var Document        = docx.Document;
var Packer          = docx.Packer;
var Paragraph       = docx.Paragraph;
var TextRun         = docx.TextRun;
var Table           = docx.Table;
var TableRow        = docx.TableRow;
var TableCell       = docx.TableCell;
var Header          = docx.Header;
var Footer          = docx.Footer;
var AlignmentType   = docx.AlignmentType;
var HeadingLevel    = docx.HeadingLevel;
var BorderStyle     = docx.BorderStyle;
var WidthType       = docx.WidthType;
var ShadingType     = docx.ShadingType;
var PageNumber      = docx.PageNumber;
var PageBreak       = docx.PageBreak;
var LevelFormat     = docx.LevelFormat;
var TableOfContents = docx.TableOfContents;
var TabStopType     = docx.TabStopType;
var TabStopPosition = docx.TabStopPosition;

// ── CLI ───────────────────────────────────────────────────────────────────────
var jsonPath   = process.argv[2];
var outputPath = process.argv[3];
if (!jsonPath || !outputPath) {
  console.error("Usage: node bpd_template.js <populated-bpd.json> <output.docx>");
  process.exit(1);
}
var rawData = JSON.parse(fs.readFileSync(jsonPath, "utf-8"));

// ══════════════════════════════════════════════════════════════════════════════
// PALETTE — exact copy from JS_template.js
// ══════════════════════════════════════════════════════════════════════════════
var C = {
  primary:     "003366",   // H1, table header bg, cover title
  accent:      "0070C0",   // H2, dividers, borders
  lightBlue:   "D6E4F0",   // KV label bg
  dark:        "404040",   // H3, body text
  mid:         "666666",   // header / footer text
  border:      "CCCCCC",   // table cell borders
  white:       "FFFFFF",   // table header text
  placeholder: "DDDDDD",   // placeholder bg (matches JS_template)
  note:        "888888",   // inferred notes, null messages
};
var FONT = "Arial";
var PW   = 9360; // content width in DXA — US Letter, 1" all margins

// ══════════════════════════════════════════════════════════════════════════════
// FORMAT STRING PARSER  (n, n_rows, n_cols — fully dynamic)
// ══════════════════════════════════════════════════════════════════════════════
function parseFormat(fmt) {
  if (!fmt) return { type:"PARAGRAPH", n:1 };
  fmt = String(fmt).trim();
  if (fmt.indexOf("TABLE") === 0) {
    var tm = fmt.match(/TABLE\[(\d+)[,\s]+(\d+)\]/);
    return tm
      ? { type:"TABLE", rows:parseInt(tm[1],10), cols:parseInt(tm[2],10) }
      : { type:"TABLE", rows:3, cols:2 };
  }
  if (fmt.indexOf("FLOWCHART") === 0) return { type:"FLOWCHART" };
  var pm = fmt.match(/^(PARAGRAPH|BULLETS|NUMBERED)\[(\d+)\]/);
  return pm
    ? { type:pm[1], n:parseInt(pm[2],10) }
    : { type:"PARAGRAPH", n:1 };
}

// ══════════════════════════════════════════════════════════════════════════════
// NUMBERING CONFIG — exact indent from JS_template.js
//   bullet  → left:720, hanging:360  (IDENTICAL to JS_template)
//   numbered→ left:720, hanging:360  (same indent, decimal format)
//   40 refs each — counter increments per H3 to ensure list restart
// ══════════════════════════════════════════════════════════════════════════════
var numberingConfig = [];
for (var bi = 0; bi < 40; bi++) {
  numberingConfig.push({
    reference: "b" + bi,
    levels: [{
      level:     0,
      format:    LevelFormat.BULLET,
      text:      "\u2022",
      alignment: AlignmentType.LEFT,
      style:     { paragraph: { indent: { left:720, hanging:360 } } }
    }]
  });
}
for (var ni = 0; ni < 40; ni++) {
  numberingConfig.push({
    reference: "n" + ni,
    levels: [{
      level:     0,
      format:    LevelFormat.DECIMAL,
      text:      "%1.",
      alignment: AlignmentType.LEFT,
      style:     { paragraph: { indent: { left:720, hanging:360 } } }
    }]
  });
}

// Per-H3 counters — each BULLETS/NUMBERED block gets its own ref
var bulletCounter   = 0;
var numberedCounter = 0;

// ══════════════════════════════════════════════════════════════════════════════
// SHARED HELPERS — exact from JS_template.js
// ══════════════════════════════════════════════════════════════════════════════
function bd(c)  { return { style:BorderStyle.SINGLE, size:1, color:c||C.border }; }
function bds(c) { var b = bd(c); return { top:b, bottom:b, left:b, right:b }; }

// sp(n) — exact from JS_template: before:n, after:0
function sp(n) {
  return new Paragraph({ spacing:{ before:n||100, after:0 }, children:[new TextRun("")] });
}

// divider — exact from JS_template: size:12, color:accent, space:1, before:0, after:200
function divider() {
  return new Paragraph({
    border:   { bottom:{ style:BorderStyle.SINGLE, size:12, color:C.accent, space:1 } },
    spacing:  { before:0, after:200 },
    children: [new TextRun("")]
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// HEADING BUILDERS
// Colours and spacing EXACTLY match JS_template.js:
//
//   H1  number + text → C.primary "003366"  spacing before:200 after:120
//   H2  number + text → C.accent  "0070C0"  spacing before:280 after:100
//   H3  number + text → C.dark    "404040"  spacing before:200 after:80
//                       TextRun size:22 (matches JS_template h3())
//
// Numbers are computed dynamically from array index — NOT hardcoded strings
// ══════════════════════════════════════════════════════════════════════════════
function h1Block(num, text, pageBreakBefore) {
  // Compose "1. Section Name" as one string — same colour (C.primary) throughout
  var label = num ? num + " " + text : text;
  return new Paragraph({
    heading:         HeadingLevel.HEADING_1,
    pageBreakBefore: pageBreakBefore !== false,
    children:        [new TextRun({ text:label, bold:true, color:C.primary })],
    spacing:         { before:200, after:120 }  // exact: JS_template h1()
  });
}


function h2Block(num, text) {
  var isInferred = String(text).indexOf("[INFERRED]") >= 0;

  var clean = String(text)
    .replace("[INFERRED]", "")
    .replace(/[\u26A0]\s*INFERRED:[^.]+\./g, "")
    .trim();

  var runs = [
    new TextRun({ text: num + " " + clean, bold: true, color: C.accent })
  ];

  if (isInferred) {
    runs.push(new TextRun({
      text: "   [INFERRED]",
      italics: true,
      color: C.note,
      size: 20,
      font: FONT
    }));
  }

  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    children: runs,

    // 🔥 UPDATED: reduced from 280 → 140 (50% less spacing)
    spacing: { before: 140, after: 0 }
  });
}






function h3Block(num, text) {
  var isInferred = String(text).indexOf("[INFERRED]") >= 0;
  var clean      = String(text).replace("[INFERRED]","").trim();
  var label = num + " " + clean;
  // Issue 3 fix: size:24 matches Heading3 style (was 22 — rendered 1pt too small)
  var runs  = [new TextRun({ text:label, bold:true, color:C.dark, size:24, font:FONT })];
  if (isInferred) {
    // [INFERRED] badge shown ONCE in heading only — no duplicate ⚠ paragraph below
    runs.push(new TextRun({
      text:"  [INFERRED]", italics:true, color:C.note, size:20, font:FONT
    }));
  }
  return new Paragraph({
    heading:  HeadingLevel.HEADING_3,
    children: runs,
    // before:120 — tight gap after H2 (Word takes max(H2.after=0, H3.before=120)=120 DXA=6pt)
    spacing:  { before:120, after:80 }
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// BODY — exact from JS_template.js body()
// alignment:JUSTIFIED, size:22, color:dark, before:60, after:60
// ══════════════════════════════════════════════════════════════════════════════
function bodyBlock(text, opts) {
  opts = opts || {};
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    // First-line indent (tab spacing at paragraph start)
    indent: { firstLine: 720 },  // 720 DXA = 0.5 inch tab
    children:  [new TextRun({
      text:    stripNote(text),
      bold:    opts.bold   || false,
      italics: opts.italic || false,
      color:   opts.color  || C.dark,
      size:    22,
      font:    FONT
    })],
    spacing: { before:60, after:60 }
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// INFERRED NOTE — matches JS_template nullNote() style (! prefix, italic, note colour)
// size:20 matches JS_template nullNote TextRun
// ══════════════════════════════════════════════════════════════════════════════
function inferredNote() {
  // Issue 1 fix: single run — both ⚠ symbol and text share italic=true (was mixed)
  return new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    children:  [new TextRun({
      text:    "\u26A0  INFERRED: Validate with business owner before BRD sign-off.",
      italics: true, color:C.note, size:20, font:FONT
    })],
    spacing: { before:60, after:60 }
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// BULLET — exact from JS_template.js bullet()
// size:22, color:dark, before:40, after:40
// indent lives in numberingConfig: left:720, hanging:360
// ══════════════════════════════════════════════════════════════════════════════
// Strip any ⚠ INFERRED note the LLM may embed despite prompt instructions
function stripNote(text) {
  return String(text||"")
    .replace(/⚠\s*INFERRED:[^.]+\./g, "")
    .replace(/INFERRED:\s*Validate[^.]+\./g, "")
    .replace(/Validate with business owner before BRD sign-off\./g, "")
    .trim();
}

function bulletBlock(text, ref) {
  var clean = stripNote(text);
  return new Paragraph({
    numbering:  { reference:ref, level:0 },
    alignment:  AlignmentType.JUSTIFIED,
    children:   [new TextRun({ text:clean, size:22, color:C.dark, font:FONT })],
    spacing:    { before:40, after:40 }  // exact: JS_template bullet()
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// NUMBERED — BPD addition, not in JS_template
// Mirrors bullet() exactly (same size, colour, spacing, indent)
// Only difference: LevelFormat.DECIMAL in numberingConfig
// ══════════════════════════════════════════════════════════════════════════════
function numberedBlock(text, ref) {
  var clean = stripNote(text);
  return new Paragraph({
    numbering:  { reference:ref, level:0 },
    alignment:  AlignmentType.JUSTIFIED,
    children:   [new TextRun({ text:clean, size:22, color:C.dark, font:FONT })],
    spacing:    { before:40, after:40 }
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// TABLE — exact cell margins from JS_template.js tbl()
//   header: top:100, bottom:100, left:150, right:150  size:20 white bold
//   data:   top:80,  bottom:80,  left:150, right:150  size:20 dark
//   n_cols and n_rows parsed dynamically from format string
//   column widths distributed evenly; last col absorbs rounding
// ══════════════════════════════════════════════════════════════════════════════
function tableBlock(content, cols) {
  var headers = (content && Array.isArray(content.headers)) ? content.headers.slice() : [];
  var rows    = (content && Array.isArray(content.rows))    ? content.rows            : [];
  cols = cols || headers.length || 2;

  while (headers.length < cols) headers.push("Col " + (headers.length + 1));
  headers = headers.slice(0, cols);

  var colW      = Math.floor(PW / cols);
  var colWidths = [];
  for (var c = 0; c < cols; c++) {
    colWidths.push(c === cols - 1 ? PW - colW * (cols - 1) : colW);
  }

  // Header row — exact: JS_template tbl() header
  var hrow = new TableRow({
    children: headers.map(function(h, i) {
      return new TableCell({
        borders: bds(),
        width:   { size:colWidths[i], type:WidthType.DXA },
        shading: { fill:C.primary, type:ShadingType.CLEAR },
        margins: { top:100, bottom:100, left:150, right:150 },  // exact JS_template
        children:[new Paragraph({
          children:[new TextRun({
            text:String(h||""), bold:true, size:20, color:C.white, font:FONT
          })]
        })]
      });
    })
  });

  // Data rows — exact: JS_template tbl() data cells
  var drows = rows.map(function(row, ri) {
    var bg    = (ri % 2 === 0) ? "FFFFFF" : "F4F8FC";
    var cells = [];
    for (var ci = 0; ci < cols; ci++) {
      var val = (Array.isArray(row) && row[ci] !== undefined) ? String(row[ci]) : "\u2014";
      cells.push(new TableCell({
        borders: bds(),
        width:   { size:colWidths[ci], type:WidthType.DXA },
        shading: { fill:bg, type:ShadingType.CLEAR },
        margins: { top:80, bottom:80, left:150, right:150 },    // exact JS_template
        children:[new Paragraph({
          alignment: AlignmentType.JUSTIFIED,
          children:  [new TextRun({ text:val, size:20, color:C.dark, font:FONT })]
        })]
      }));
    }
    return new TableRow({ children:cells });
  });

  return new Table({
    width:        { size:PW, type:WidthType.DXA },
    columnWidths: colWidths,
    rows:         [hrow].concat(drows)
  });
}

// ══════════════════════════════════════════════════════════════════════════════
// FLOWCHART — styled shaded box, Courier New, split on →
// ══════════════════════════════════════════════════════════════════════════════
function flowchartBlock(text) {
  // FIX 6: strip literal newlines LLM may embed, then split on arrow character
  var cleaned = String(text||"").replace(/\n/g, " → ");
  // collapse any double arrows created by the substitution
  while (cleaned.indexOf("→  →") >= 0) { cleaned = cleaned.replace(/→\s*→/g, "→"); }
  var nodes = cleaned.split("→").map(function(s){ return s.trim(); }).filter(Boolean);

  var titleRow = new Table({
    width: { size:PW, type:WidthType.DXA }, columnWidths:[PW],
    rows:  [new TableRow({ children:[new TableCell({
      borders: bds(C.accent),
      width:   { size:PW, type:WidthType.DXA },
      shading: { fill:C.primary, type:ShadingType.CLEAR },
      margins: { top:80, bottom:80, left:200, right:200 },
      children:[new Paragraph({
        alignment: AlignmentType.CENTER,
        children:  [new TextRun({
          text:"PROCESS FLOW DIAGRAM", bold:true, size:18, color:C.white, font:FONT
        })]
      })]
    })]}) ]
  });

  var contentRows = nodes.map(function(node, idx) {
    var isDecision = node.indexOf("<") >= 0;
    var isTerminal = node === "START" || node.indexOf("END") === 0;
    var prefix     = idx === 0 ? "" : "\u2192  ";
    return new Paragraph({
      spacing: { before:50, after:50 },
      children:[new TextRun({
        text:  prefix + node,
        size:  20,
        color: isDecision ? C.accent : (isTerminal ? C.primary : C.dark),
        bold:  isDecision || isTerminal,
        font:  "Courier New"
      })]
    });
  });

  var contentTable = new Table({
    width: { size:PW, type:WidthType.DXA }, columnWidths:[PW],
    rows:  [new TableRow({ children:[new TableCell({
      borders: bds(C.accent),
      width:   { size:PW, type:WidthType.DXA },
      shading: { fill:"F2F8FF", type:ShadingType.CLEAR },
      margins: { top:160, bottom:160, left:280, right:280 },
      children: contentRows
    })]}) ]
  });

  return [titleRow, contentTable];
}

// ══════════════════════════════════════════════════════════════════════════════
// H3 CONTENT DISPATCHER — dynamic format executor
// ══════════════════════════════════════════════════════════════════════════════
function renderH3(h3node) {
  var fmt        = parseFormat(h3node.format);
  var content    = h3node.content;
  var isInferred = String(h3node.name||"").indexOf("[INFERRED]") >= 0;
  var out        = [];

  switch (fmt.type) {

    case "PARAGRAPH": {
      var paras = Array.isArray(content) ? content : [String(content||"")];
      for (var p = 0; p < fmt.n; p++) {
        var raw = paras[p] !== undefined ? String(paras[p]) : "";
        out.push(bodyBlock(stripNote(raw)));
      }
      break;
    }

    case "BULLETS": {
      var items = Array.isArray(content) ? content : [String(content||"")];
      var bref  = "b" + (bulletCounter++);
      for (var b = 0; b < fmt.n; b++) {
        out.push(bulletBlock(items[b] !== undefined ? items[b] : "", bref));
      }
      break;
    }

    case "NUMBERED": {
      var steps = Array.isArray(content) ? content : [String(content||"")];
      var nref  = "n" + (numberedCounter++);
      for (var s = 0; s < fmt.n; s++) {
        out.push(numberedBlock(steps[s] !== undefined ? steps[s] : "", nref));
      }
      break;
    }

    case "TABLE": {
      out.push(sp(60));
      var tContent = (content && typeof content === "object") ? content : { headers:[], rows:[] };
      out.push(tableBlock(tContent, fmt.cols));
      // TABLE note removed — [INFERRED] badge in heading is the single indicator
      break;
    }

    case "FLOWCHART": {
      out.push(sp(80));
      flowchartBlock(content).forEach(function(b){ out.push(b); });
      break;
    }

    default:
      out.push(bodyBlock(String(content||"")));
  }

  // [INFERRED] badge is shown ONCE in the H3 heading.
  // No separate warning paragraph — avoids duplicate display.
  return out;
}

// ══════════════════════════════════════════════════════════════════════════════
// COVER PAGE — matches JS_template cover block exactly
// ══════════════════════════════════════════════════════════════════════════════
function buildCover(meta) {
  var out = [];

  // Top spacer — matches JS_template cover: before:1800
  out.push(new Paragraph({ spacing:{ before:1800, after:0 }, children:[new TextRun("")] }));

  // Main title — size:52 bold primary, centred
  out.push(new Paragraph({
    alignment: AlignmentType.CENTER,
    children:  [new TextRun({
      text:String(meta.title||"BUSINESS PROCESS DOCUMENT").toUpperCase(),
      bold:true, size:52, color:C.primary, font:FONT
    })]
  }));

  out.push(sp(200));

  // Sub-title — size:40 bold accent, centred
  out.push(new Paragraph({
    alignment: AlignmentType.CENTER,
    children:  [new TextRun({
      text:  meta.subtitle || meta.title || "SAP Implementation",
      bold:  true, size:40, color:C.accent, font:FONT
    })]
  }));

  out.push(sp(300));

  // Horizontal rule — exact: JS_template cover divider sz:6
  out.push(new Paragraph({
    alignment: AlignmentType.CENTER,
    border:    { bottom:{ style:BorderStyle.SINGLE, size:6, color:C.accent, space:1 } },
    children:  [new TextRun("")]
  }));

  out.push(sp(200));

  // Metadata rows — size:22, mid label bold, dark value
  var rows = [
    ["Document Type",  meta.document_type  || "Business Process Document (BPD)"],
    ["Schema Phase",   meta.schema_phase   || "POPULATED"],
    ["Authoring Mode", meta.authoring_mode || "AI"],
    ["Version",        meta.version        || "v1.0"],
    ["Date",           meta.date || new Date().toISOString().slice(0,10)],
    ["Status",         meta.status         || "Pending BRD Sign-Off"],
  ];
  rows.forEach(function(pair) {
    out.push(new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing:   { before:80, after:80 },    // exact: JS_template cover rows
      children:  [
        new TextRun({ text:pair[0]+": ", bold:true, size:22, color:C.mid,  font:FONT }),
        new TextRun({ text:pair[1],               size:22, color:C.dark, font:FONT })
      ]
    }));
  });

  out.push(new Paragraph({ children:[new PageBreak()] }));
  return out;
}

// ══════════════════════════════════════════════════════════════════════════════
// MAIN BUILD — walk H1 → H2 → H3, compute numbers dynamically
// ══════════════════════════════════════════════════════════════════════════════
function build(data) {
  var ch        = [];
  var structure = data.structure || [];

  // 1. Cover
  buildCover({
    document_type:  data.document_type,
    schema_phase:   data.schema_phase,
    authoring_mode: data.authoring_mode,
    title:          "BUSINESS PROCESS DOCUMENT",
    subtitle:       data.title || "SAP Implementation",
    version:        data.version,
    date:           data.date,
    status:         data.status
  }).forEach(function(b){ ch.push(b); });

  // 2. Table of Contents — no number prefix on TOC heading
  ch.push(new Paragraph({
    heading:         HeadingLevel.HEADING_1,
    pageBreakBefore: false,
    children:        [new TextRun({ text:"Table of Contents", bold:true, color:C.primary })],
    spacing:         { before:200, after:120 }
  }));
  ch.push(divider());
  ch.push(new TableOfContents("Table of Contents", { hyperlink:false, headingStyleRange:"1-3" }));
  ch.push(new Paragraph({ children:[new PageBreak()] }));

  // 3. Body sections — dynamic numbering from array index
  structure.forEach(function(h1node, h1i) {
    var h1num = (h1i + 1) + ".";

    ch.push(h1Block(h1num, h1node.name, true));
    ch.push(divider());

    (h1node.children || []).forEach(function(h2node, h2i) {
      var h2num = (h1i + 1) + "." + (h2i + 1);

      ch.push(h2Block(h2num, h2node.name));

      // [INFERRED] badge in H2 heading is the single indicator — no duplicate paragraph

      (h2node.children || []).forEach(function(h3node, h3i) {
        var h3num = (h1i + 1) + "." + (h2i + 1) + "." + (h3i + 1);

        ch.push(h3Block(h3num, h3node.name));
        renderH3(h3node).forEach(function(b){ ch.push(b); });
        ch.push(sp(80));
      });

      ch.push(sp(120));
    });
  });

  return ch;
}

// ══════════════════════════════════════════════════════════════════════════════
// DOCUMENT ASSEMBLY
// Paragraph styles, page, header, footer — exact from JS_template.js
// ══════════════════════════════════════════════════════════════════════════════
var children    = build(rawData);
var projectMeta = rawData.title   || "Business Process Document";
var versionMeta = rawData.version || "v1.0";
var dateMeta    = rawData.date || new Date().toISOString().slice(0,10);

var doc = new Document({
  numbering: { config: numberingConfig },

  styles: {
    // Default run — exact: JS_template default document run
    default: { document: { run: { font:FONT, size:22, color:C.dark } } },

    paragraphStyles: [
      // ── Heading 1 — exact from JS_template styles block ──────────────────
      {
        id:"Heading1", name:"Heading 1",
        basedOn:"Normal", next:"Normal", quickFormat:true,
        run:       { size:36, bold:true, font:FONT, color:C.primary },
        paragraph: { spacing:{ before:480, after:160 }, outlineLevel:0 }
      },
      // ── Heading 2 — exact from JS_template styles block ──────────────────
      {
        id:"Heading2", name:"Heading 2",
        basedOn:"Normal", next:"Normal", quickFormat:true,
        run:       { size:28, bold:true, font:FONT, color:C.accent },
        paragraph: { spacing:{ before:320, after:120 }, outlineLevel:1 }
      },
      // ── Heading 3 — exact from JS_template styles block ──────────────────
      {
        id:"Heading3", name:"Heading 3",
        basedOn:"Normal", next:"Normal", quickFormat:true,
        run:       { size:24, bold:true, font:FONT, color:C.dark },
        paragraph: { spacing:{ before:120, after:80  }, outlineLevel:2 }
      }
    ]
  },

  sections: [{
    properties: {
      page: {
        // ── Page size — exact from JS_template ───────────────────────────
        size:   { width:12240, height:15840 },
        // ── Margins — exact from JS_template ────────────────────────────
        margin: { top:1440, right:1440, bottom:1440, left:1440 }
      }
    },

    // ── Header — exact from JS_template ─────────────────────────────────
    headers: {
      default: new Header({ children:[new Paragraph({
        children: [
          new TextRun({
            text: "CONFIDENTIAL DRAFT  |  " + projectMeta,
            size:16, color:C.mid, font:FONT
          }),
          new TextRun({ text:"\t", size:16 }),
          new TextRun({
            children: ["Page ", PageNumber.CURRENT, " of ", PageNumber.TOTAL_PAGES],
            size:16, color:C.mid, font:FONT
          })
        ],
        // tabStop RIGHT at MAX — exact from JS_template
        tabStops: [{ type:TabStopType.RIGHT, position:TabStopPosition.MAX }],
        // border bottom — exact from JS_template: size:4 color:accent space:4
        border:   { bottom:{ style:BorderStyle.SINGLE, size:4, color:C.accent, space:4 } }
      })] })
    },

    // ── Footer — exact from JS_template ─────────────────────────────────
    footers: {
      default: new Footer({ children:[new Paragraph({
        children: [new TextRun({
          text:  versionMeta + "  |  " + dateMeta + "  |  SAP Implementation Project",
          size:16, color:C.mid, font:FONT
        })],
        // border top — exact from JS_template: size:4 color:accent space:4
        border: { top:{ style:BorderStyle.SINGLE, size:4, color:C.accent, space:4 } }
      })] })
    },

    children: children
  }]
});

Packer.toBuffer(doc).then(function(buf) {
  fs.writeFileSync(outputPath, buf);
  console.log("SUCCESS: " + outputPath);
}).catch(function(err) {
  console.error("ERROR:", err.message);
  process.exit(1);
});
