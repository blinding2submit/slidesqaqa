#!/usr/bin/env python3

"""
This application provides a deck-level structural analysis and targeted question
generation (slidesqaqa) pipeline for PDF presentations. Using the Gemini API, it extracts
text and visual modalities from PDF slides, builds contextual overlapping windows,
and produces a comprehensive JSON annotation detailing slide roles, learning goals,
and customized comprehension questions.

The pipeline explicitly handles deck-level reconciliation, assessing initial per-slide
question assignments and rewriting or modifying them to balance instructional coverage
and pedagogical scaffolding across the entire slide deck.

Live app: https://slidesqaqa-974767694043.us-west1.run.app/
Repo: https://github.com/blinding2submit/slidesqaqa
"""

from __future__ import annotations  # Enable postponed evaluation of annotations

import io  # For handling byte streams in memory (e.g., image manipulation)
import json  # For encoding and decoding structured data to/from the LLM
import math  # For mathematical operations (e.g., calculating grid rows/cols)
import os  # For interacting with the operating system, like reading ENV variables
import re  # For text processing and cleanup using regular expressions
import textwrap  # For wrapping or formatting text strings
import uuid  # For generating unique identifiers for processing jobs
from dataclasses import dataclass  # For defining clean, typed data container classes
from datetime import datetime, timezone  # For generating accurate UTC timestamps
from pathlib import Path  # For object-oriented file system path manipulations
import urllib.request  # For downloading PDF files from external URLs
import hashlib  # For computing MD5 hashes to verify file/URL uniqueness
from typing import Any, Dict, Iterable, List, Optional  # For robust static type hinting

import fitz  # PyMuPDF: A high-performance PDF rendering and text extraction library
from flask import Flask, Response, render_template_string, request, stream_with_context, send_file, send_from_directory  # Web framework components
from google import genai  # Google's Generative AI SDK client
from google.genai import types  # Google GenAI type definitions (e.g., passing image Parts)
from PIL import Image, ImageDraw, ImageOps  # Python Imaging Library for creating contact sheets
from pydantic import BaseModel, Field, ValidationError  # For defining strict schemas and validating LLM output
from werkzeug.utils import secure_filename  # For sanitizing potentially dangerous user-uploaded filenames


# --- Constants & Configuration ---

# Application title used across logging and context contexts
APP_TITLE = "Slide Deck Q&A Quality Assurance app"

# The Gemini model variant to use. Defaults to a highly capable reasoning model.
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")

# Hard upper limit on the number of questions allowed per individual slide.
MAX_QUESTIONS_PER_SLIDE = 5

# Local directory where uploaded/downloaded PDFs and output JSON files are temporarily stored.
UPLOAD_DIR = Path("jobs")

# Set of allowed file extensions for uploads to prevent dangerous file executions.
ALLOWED_EXTENSIONS = {".pdf"}

# Markers used to identify the final structured payload within the streamed string response.
BEGIN_JSON_MARKER = "\n===== FINAL JSON BEGIN =====\n"
END_JSON_MARKER = "\n===== FINAL JSON END =====\n"

HTML_PAGE = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Slide Deck Q&A Quality Assurance app</title>
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <link rel="icon" href="/scroll.svg" type="image/svg+xml">
  <style>
    :root {
      --bg: #0b1020;
      --panel: #121931;
      --panel-2: #0f1530;
      --text: #e7ecff;
      --muted: #a8b3d6;
      --accent: #7aa2ff;
      --accent-2: #9fe3c1;
      --border: rgba(255,255,255,0.12);
      --danger: #ffb4b4;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #0a0f1f 0%, #0d1428 100%);
      color: var(--text);
    }
    .wrap {
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px;
    }
    h1 {
      margin: 0 0 6px 0;
      font-size: 28px;
      line-height: 1.2;
    }
    .sub {
      margin: 0 0 18px 0;
      color: var(--muted);
      max-width: 900px;
    }
    .grid {
      display: grid;
      grid-template-columns: 380px 1fr;
      gap: 18px;
      align-items: start;
    }
    .panel {
      background: rgba(18,25,49,0.92);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 16px;
      backdrop-filter: blur(8px);
      box-shadow: 0 14px 36px rgba(0,0,0,0.28);
    }
    .panel a {
      color: var(--accent);
    }
    .panel a:visited {
      color: #cba6ff;
    }
    label {
      display: block;
      font-size: 13px;
      color: var(--muted);
      margin-bottom: 6px;
    }
    input[type="text"], input[type="password"], textarea, input[type="file"] {
      width: 100%;
      border: 1px solid var(--border);
      background: var(--panel-2);
      color: var(--text);
      border-radius: 10px;
      padding: 10px 12px;
      font: inherit;
    }
    textarea {
      min-height: 110px;
      resize: vertical;
    }
    .row { margin-bottom: 14px; }
    .hint {
      font-size: 12px;
      color: var(--muted);
      margin-top: 6px;
      line-height: 1.4;
    }
    .btns {
      display: flex;
      gap: 10px;
      margin-top: 6px;
      flex-wrap: wrap;
    }
    button {
      appearance: none;
      border: none;
      border-radius: 10px;
      padding: 10px 14px;
      font: inherit;
      cursor: pointer;
      background: var(--accent);
      color: #081022;
      font-weight: 600;
    }
    button.secondary {
      background: rgba(255,255,255,0.08);
      color: var(--text);
      border: 1px solid var(--border);
    }
    button:disabled {
      opacity: 0.55;
      cursor: not-allowed;
    }
    .status, .json {
      min-height: 280px;
      max-height: 75vh;
      overflow: auto;
      border: 1px solid var(--border);
      background: #091022;
      border-radius: 12px;
      padding: 14px;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12.5px;
      line-height: 1.45;
    }
    .json {
      background: #0a1226;
    }
    .meta {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      margin-bottom: 10px;
      color: var(--muted);
      font-size: 13px;
    }
    .ok { color: var(--accent-2); }
    .err { color: var(--danger); }
    .footer {
      margin-top: 16px;
      color: var(--muted);
      font-size: 12px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 10px;
    }
    th, td {
      border: 1px solid var(--border);
      padding: 8px 12px;
      text-align: left;
    }
    th {
      background: rgba(255,255,255,0.05);
      font-weight: 600;
    }
    h2 {
      margin-top: 0;
      margin-bottom: 12px;
      font-size: 20px;
      color: var(--accent);
    }
    h3 {
      font-size: 16px;
      margin-top: 16px;
      margin-bottom: 8px;
    }
    .viz-section {
      margin-bottom: 16px;
    }
    @media (max-width: 960px) {
      .grid { grid-template-columns: 1fr; }
    }

    .github-corner {
      position: fixed;
      top: 0;
      right: 0;
      z-index: 1000;
      width: 200px;
      height: 200px;
      pointer-events: none;
    }

    .github-corner a {
      position: absolute;
      top: 40px;
      right: -50px;
      width: 260px;
      padding: 10px 0;
      background: #111;
      color: #fff;
      text-align: center;
      text-decoration: none;
      font-weight: 700;
      letter-spacing: 0.5px;
      transform: rotate(45deg);
      box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35);
      pointer-events: auto;
    }

    .github-corner a:hover {
      background: #1f4aa8;
    }

    @media (max-width: 700px) {
      .github-corner {
        width: 150px;
        height: 150px;
      }

      .github-corner a {
        top: 26px;
        right: -58px;
        width: 210px;
        font-size: 13px;
      }
    }
  </style>
</head>
<body>
  <div class="github-corner" aria-hidden="false">
    <a href="https://github.com/blinding2submit/slidesqaqa" target="_blank">&nbsp;&nbsp;&nbsp;&nbsp;Fork me on GitHub&nbsp;&nbsp;&nbsp;</a>
  </div>
<div class="wrap">
  <img src="scroll.svg" width=30 align=right />
  <h1>Slide Deck Q&A Quality Assurance app</h1>
  <p class="sub">
    Upload a PDF slide deck (or just provide its URL), give it a citation, and the app will stream planning status while it builds
    a hierarchical JSON annotation with deck analysis, variable per-slide question budgets, and slide-level question sets.
  </p>

  <div class="panel" style="margin-bottom: 24px;">
    <h2>Theory of Operation</h2>
    <p>The system operates by first extracting text and visual information from the uploaded PDF presentation deck. Large decks are chunked into contiguous, overlapping sliding windows (e.g., 8 slides per window with a 2-slide overlap). This preserves contextual awareness, allowing the system to track transitions and narrative flow across boundaries without hitting the generative model's context limits.</p>
    <p>Following extraction, the system infers crucial instructional attributes for every slide, such as its modality (e.g., diagram, table, text) and its specific role in the presentation (e.g., mechanism, summary, agenda). These attributes strictly dictate the mix of generated question types (e.g., diagram labeling vs. multiple-choice) and the initial per-slide question budget, balancing instructional importance with evidence richness.</p>
    <p>Finally, the system executes targeted slide-level question generation and performs deck-level reconciliation using precise 1-5 rubrics. Provisional question sets are evaluated on Coverage (from poor facts to strong representation of core concepts), Scaffolding (from random questions to coherent progression), and Fidelity (ensuring answers are derivable purely from the slide). The reconciliation step uses these scores to zero out redundancies, balance coverage across learning goals, and shape a cohesive final question distribution.</p>
  </div>

  <div class="grid">
    <div class="panel">
      <form id="deck-form">
        <div class="row">
          <label for="deck_file">PDF deck</label>
          <input id="deck_file" name="deck_file" type="file" accept=".pdf,application/pdf">
          <div class="hint">One PDF only. Either upload a file or provide a URL below.</div>
        </div>

        <div class="row">
          <label for="citation">Academic citation</label>
          <textarea id="citation" name="citation" placeholder="Author, A. A. (2026). Lecture X: Title [Course lecture slides]. Course name, Institution. Or e.g., Author26" required></textarea>
        </div>

        <div class="row">
          <label for="deck_url">Canonical deck URL (optional)</label>
          <input id="deck_url" name="deck_url" type="text" placeholder="https://example.edu/lecture.pdf">
        </div>

        <div class="row" style="display: flex; gap: 10px;">
          <div style="flex: 1;">
            <label for="start_page">Start PDF page</label>
            <input id="start_page" name="start_page" type="text" value="1" pattern="^[1-9][0-9]*$" title="Must be a positive integer or blank">
          </div>
          <div style="flex: 1;">
            <label for="end_page">End PDF page</label>
            <input id="end_page" name="end_page" type="text" placeholder="End" pattern="^[1-9][0-9]*$" title="Must be a positive integer or blank">
          </div>
        </div>

        <div class="row" style="display: flex; gap: 10px;">
          <div style="flex: 1;">
            <label for="budget_mode_mean" style="display: flex; align-items: center; gap: 6px; cursor: pointer;">
              <input type="radio" name="budget_mode" id="budget_mode_mean" value="mean" style="width: auto; margin: 0;">
              Questions per slide
            </label>
            <input id="target_mean" name="target_mean" type="text" value="2.5" pattern="^[0-9]+(\\.[0-9]+)?$" title="Must be a positive number or blank">
          </div>
          <div style="flex: 1;">
            <label for="budget_mode_total" style="display: flex; align-items: center; gap: 6px; cursor: pointer;">
              <input type="radio" name="budget_mode" id="budget_mode_total" value="total" style="width: auto; margin: 0;">
              Total questions
            </label>
            <input id="target_total" name="target_total" type="text" placeholder="" pattern="^[1-9][0-9]*$" title="Must be a positive integer or blank">
          </div>
        </div>

        <div class="row">
          <label for="model">Gemini model</label>
          <input id="model" name="model" type="text" value="{{ default_model }}" required>
        </div>

        <div class="row">
          <label for="api_key"><a href="https://aistudio.google.com/api-keys" target="_blank" style="color: inherit; text-decoration: underline;">Bring your own Gemini API key</a> (required)</label>
          <input id="api_key" name="api_key" type="password" required>
        </div>

        <div class="btns">
          <button id="submit-btn" type="submit">Analyze deck</button>
          <button id="download-btn" class="secondary" type="button" disabled>Download JSON</button>
        </div>
      </form>

      <div class="footer">
        This page streams status logs from Flask while the analysis runs.
      </div>
    </div>

    <div class="panel">
      <div class="meta">
        <div>Status log</div>
        <div id="run-state">Idle</div>
      </div>
      <pre id="status" class="status"></pre>
      <div class="meta" style="margin-top: 16px;">
        <div>Final JSON</div>
        <div id="json-state">No result yet</div>
      </div>
      <pre id="json" class="json"></pre>
    </div>
  </div>

  <div id="visualizations" style="display: none; margin-top: 24px;">
    <div class="panel" style="margin-bottom: 24px;">
      <h2>Executive Summary</h2>
      <div id="exec-summary"></div>
    </div>
    <div class="panel" style="margin-bottom: 24px;">
      <h2>Deck Outline</h2>
      <div id="deck-outline"></div>
    </div>
    <div class="panel" style="margin-bottom: 24px;">
      <h2>Question Bank</h2>
      <div id="question-bank"></div>
    </div>
    <div class="panel" style="margin-bottom: 24px;">
      <h2>Evaluation Matrix</h2>
      <div id="evaluation-matrix" style="overflow-x: auto;"></div>
    </div>
  </div>
</div>

<script>
(() => {
  const form = document.getElementById("deck-form");
  const statusEl = document.getElementById("status");
  const jsonEl = document.getElementById("json");
  const runStateEl = document.getElementById("run-state");
  const jsonStateEl = document.getElementById("json-state");
  const submitBtn = document.getElementById("submit-btn");
  const downloadBtn = document.getElementById("download-btn");

  const visualizerEl = document.getElementById("visualizations");
  const execSummaryEl = document.getElementById("exec-summary");
  const deckOutlineEl = document.getElementById("deck-outline");
  const questionBankEl = document.getElementById("question-bank");
  const evalMatrixEl = document.getElementById("evaluation-matrix");

  const BEGIN = "===== FINAL JSON BEGIN =====";
  const END = "===== FINAL JSON END =====";
  let latestJson = "";

  function setRunning(isRunning) {
    submitBtn.disabled = isRunning;
    runStateEl.textContent = isRunning ? "Running" : "Idle";
    runStateEl.className = isRunning ? "ok" : "";
  }

  function clearOutput() {
    statusEl.textContent = "";
    jsonEl.textContent = "";
    jsonStateEl.textContent = "No result yet";
    jsonStateEl.className = "";
    latestJson = "";
    downloadBtn.disabled = true;

    visualizerEl.style.display = "none";
    execSummaryEl.innerHTML = "";
    deckOutlineEl.innerHTML = "";
    questionBankEl.innerHTML = "";
    evalMatrixEl.innerHTML = "";
  }

  function escapeHtml(unsafe) {
    if (typeof unsafe !== 'string') return '';
    return unsafe
         .replace(/&/g, "&amp;")
         .replace(/</g, "&lt;")
         .replace(/>/g, "&gt;")
         .replace(/"/g, "&quot;")
         .replace(/'/g, "&#039;");
  }

  function renderExecSummary(data) {
    const analysis = data.deck_analysis || {};
    const recon = data.reconciliation || {};

    let html = `<div class="viz-section">
      <p><strong>Topic:</strong> ${escapeHtml(analysis.deck_topic)}</p>
      <p><strong>Target Audience:</strong> ${escapeHtml(analysis.target_audience)}</p>
      <p><strong>Global Notes:</strong> ${escapeHtml(analysis.global_notes)}</p>
    </div>`;

    if (analysis.learning_goals && analysis.learning_goals.length > 0) {
      html += `<div class="viz-section">
        <h3>Learning Goals</h3>
        <ul>
          ${analysis.learning_goals.map(g => `<li>${escapeHtml(g)}</li>`).join('')}
        </ul>
      </div>`;
    }

    if (recon.uncovered_learning_goals && recon.uncovered_learning_goals.length > 0) {
      html += `<div class="viz-section">
        <h3>Uncovered Learning Goals</h3>
        <ul>
          ${recon.uncovered_learning_goals.map(g => `<li>${escapeHtml(g)}</li>`).join('')}
        </ul>
      </div>`;
    }

    execSummaryEl.innerHTML = html;
  }

  function renderDeckOutline(data) {
    const analysis = data.deck_analysis || {};
    const slides = data.slides || [];
    const slidesByNum = {};
    slides.forEach(s => slidesByNum[s.slide_number] = s);

    let html = '';
    if (analysis.sections && analysis.sections.length > 0) {
      html += `<ol>`;
      analysis.sections.forEach(sec => {
        html += `<li>
          <strong>${escapeHtml(sec.section_title)}</strong>
          <p style="margin: 4px 0 8px 0; color: var(--muted);">${escapeHtml(sec.section_summary)}</p>
          <ul>`;
        for (let i = sec.start_slide; i <= sec.end_slide; i++) {
          const s = slidesByNum[i];
          if (s) {
            html += `<li><em>Slide ${s.slide_number}:</em> ${escapeHtml(s.slide_title)} - ${escapeHtml(s.local_summary)}</li>`;
          }
        }
        html += `  </ul>
        </li>`;
      });
      html += `</ol>`;
    } else {
      html = '<p>No sections found.</p>';
    }
    deckOutlineEl.innerHTML = html;
  }

  function renderQuestionBank(data) {
    const slides = data.slides || [];
    let html = '';

    let hasQuestions = false;
    slides.forEach(s => {
      if (s.questions && s.questions.length > 0) {
        hasQuestions = true;
        html += `<h3>Slide ${s.slide_number}: ${escapeHtml(s.slide_title)}</h3>`;
        html += `<dl style="margin-bottom: 24px;">`;
        s.questions.forEach(q => {
          html += `<dt style="font-weight: bold; margin-top: 12px;">Q: ${escapeHtml(q.prompt)}</dt>`;
          if (q.options && q.options.length > 0) {
            html += `<dd style="margin-left: 20px; margin-top: 4px;">
              <ul style="margin: 0; padding-left: 20px;">
                ${q.options.map(opt => `<li>${escapeHtml(opt)}</li>`).join('')}
              </ul>
            </dd>`;
          }
          html += `<dd style="margin-left: 20px; margin-top: 4px; font-style: italic; color: var(--accent-2);">A: ${escapeHtml(q.answer)}</dd>`;
        });
        html += `</dl>`;
      }
    });

    if (!hasQuestions) {
      html = '<p>No questions generated for this deck.</p>';
    }

    questionBankEl.innerHTML = html;
  }

  function renderEvalMatrix(data) {
    const slides = data.slides || [];
    const reconActions = data.reconciliation?.revised_slide_actions || [];
    const reconMap = {};
    reconActions.forEach(a => reconMap[a.slide_number] = a);

    let html = `<table>
      <thead>
        <tr>
          <th>Slide</th>
          <th>Role</th>
          <th>Budget</th>
          <th>Coverage</th>
          <th>Scaffolding</th>
          <th>Reconciliation Note</th>
        </tr>
      </thead>
      <tbody>`;

    slides.forEach(s => {
      const evalData = s.evaluation || {};
      const recon = reconMap[s.slide_number];
      const covScore = evalData.coverage_score !== null && evalData.coverage_score !== undefined ? evalData.coverage_score : '-';
      const scafScore = evalData.scaffolding_score !== null && evalData.scaffolding_score !== undefined ? evalData.scaffolding_score : '-';
      const reconNote = recon ? `${escapeHtml(recon.action)}: ${escapeHtml(recon.reason)}` : '';

      html += `<tr>
        <td>${s.slide_number}</td>
        <td>${escapeHtml(s.role_in_deck)}</td>
        <td>${s.question_budget}</td>
        <td>${covScore}</td>
        <td>${scafScore}</td>
        <td>${reconNote}</td>
      </tr>`;
    });

    html += `</tbody></table>`;
    evalMatrixEl.innerHTML = html;
  }

  function renderVisualizations(data) {
    visualizerEl.style.display = "block";
    renderExecSummary(data);
    renderDeckOutline(data);
    renderQuestionBank(data);
    renderEvalMatrix(data);
  }

  const deckFile = document.getElementById("deck_file");
  const deckUrl = document.getElementById("deck_url");

  function updateFileRequirement() {
    if (deckUrl.value.trim() !== "") {
      deckFile.required = false;
    } else {
      deckFile.required = true;
    }
  }

  deckUrl.addEventListener("input", updateFileRequirement);
  // Run once on load
  updateFileRequirement();

  // --- Budget Radio Button Logic ---
  let lastCheckedRadio = document.querySelector('input[name="budget_mode"]:checked');
  const budgetRadios = document.querySelectorAll('input[name="budget_mode"]');
  budgetRadios.forEach(radio => {
    radio.addEventListener('click', function(e) {
      if (lastCheckedRadio === this) {
        this.checked = false;
        lastCheckedRadio = null;
      } else {
        lastCheckedRadio = this;
      }
    });
  });

  const targetMeanInput = document.getElementById('target_mean');
  const targetTotalInput = document.getElementById('target_total');
  const budgetModeMean = document.getElementById('budget_mode_mean');
  const budgetModeTotal = document.getElementById('budget_mode_total');

  function updateRadioFromInput(inputEl, radioEl) {
    if (inputEl.value.trim() !== "") {
      radioEl.checked = true;
      lastCheckedRadio = radioEl;
    } else {
      radioEl.checked = false;
      if (lastCheckedRadio === radioEl) {
        lastCheckedRadio = null;
      }
    }
  }

  targetMeanInput.addEventListener('input', () => updateRadioFromInput(targetMeanInput, budgetModeMean));
  targetTotalInput.addEventListener('input', () => updateRadioFromInput(targetTotalInput, budgetModeTotal));

  downloadBtn.addEventListener("click", () => {
    if (!latestJson) return;
    const blob = new Blob([latestJson], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "deck_annotation.json";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  });

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    clearOutput();
    setRunning(true);
    jsonStateEl.textContent = "Waiting for result";

    const formData = new FormData(form);

    try {
      const resp = await fetch("/analyze", {
        method: "POST",
        body: formData
      });

      if (!resp.ok || !resp.body) {
        const text = await resp.text();
        throw new Error(text || `HTTP ${resp.status}`);
      }

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let allText = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        allText += chunk;

        const beginIdx = allText.indexOf(BEGIN);
        if (beginIdx === -1) {
          statusEl.textContent = allText;
        } else {
          statusEl.textContent = allText.slice(0, beginIdx).trimEnd();
          const afterBegin = allText.slice(beginIdx + BEGIN.length);
          const endIdx = afterBegin.indexOf(END);
          if (endIdx === -1) {
            jsonEl.textContent = afterBegin.trimStart();
            jsonStateEl.textContent = "Receiving JSON";
            jsonStateEl.className = "ok";
          } else {
            latestJson = afterBegin.slice(0, endIdx).trim();
            jsonEl.textContent = latestJson;
            jsonStateEl.textContent = "JSON complete";
            jsonStateEl.className = "ok";
            downloadBtn.disabled = false;

            try {
              const parsed = JSON.parse(latestJson);
              renderVisualizations(parsed);
            } catch (e) {
              console.error("Failed to parse JSON for visualizations:", e);
            }
          }
        }

        statusEl.scrollTop = statusEl.scrollHeight;
        jsonEl.scrollTop = jsonEl.scrollHeight;
      }

      if (!latestJson) {
        jsonStateEl.textContent = "No final JSON marker found";
        jsonStateEl.className = "err";
      }
    } catch (err) {
      statusEl.textContent += "\\n[client error] " + (err && err.message ? err.message : String(err));
      runStateEl.textContent = "Error";
      runStateEl.className = "err";
      jsonStateEl.textContent = "Failed";
      jsonStateEl.className = "err";
    } finally {
      setRunning(false);
    }
  });
})();
</script>
</body>
</html>
"""


FIELD_DESCRIPTIONS: Dict[str, str] = {
    "schema_version": "Version of this JSON schema.",
    "deck_metadata": "Metadata and citation for the source slide deck.",
    "deck_analysis": "Whole-deck interpretation including topic, section structure, learning goals, and global coverage notes.",
    "reconciliation": "Deck-level reconciliation output describing final balancing, reductions, or expansions after provisional slide annotations.",
    "slides": "Ordered list of slide records, one per slide in the deck.",
    "deck_metadata.deck_id": "Stable identifier for this deck.",
    "deck_metadata.deck": "Full academic citation for the deck.",
    "deck_metadata.deck_url": "Original source URL for the deck, if known.",
    "deck_metadata.source_file": "Local uploaded PDF filename.",
    "deck_metadata.total_slides": "Total number of PDF pages processed as slides.",
    "deck_metadata.processed_at": "UTC timestamp when this JSON was produced.",
    "deck_analysis.deck_topic": "Short description of the overall topic of the deck.",
    "deck_analysis.target_audience": "Estimated audience level; for example undergraduate, graduate, or mixed.",
    "deck_analysis.learning_goals": "List of deck-level learning goals inferred from the slides.",
    "deck_analysis.sections": "Ordered list of section objects with start and end slides, title, and summary.",
    "deck_analysis.coverage_targets": "Deck-level content targets such as text, diagram, table, chart, layout-aware, or image-plus-text.",
    "deck_analysis.global_notes": "Important global caveats, ambiguities, or observations.",
    "reconciliation.revised_slide_actions": "Per-slide reconciliation actions after reviewing the provisional full-deck annotation set.",
    "reconciliation.deck_reconciliation_notes": "Global notes about redundancy, balancing, and quality adjustments across the deck.",
    "reconciliation.uncovered_learning_goals": "Deck learning goals that remain weakly covered after reconciliation.",
    "reconciliation.redundancy_warnings": "Warnings about overlapping or repeated question sets across slides.",
    "slides[].slide_id": "Stable identifier for a slide within the deck.",
    "slides[].slide_number": "1-based slide number corresponding to the PDF page order.",
    "slides[].slide_title": "Visible title on the slide if present; otherwise a concise generated title.",
    "slides[].modality_type": "Dominant visual form of the slide; for example text, diagram, table, chart, layout-aware, image-plus-text, or mixed.",
    "slides[].role_in_deck": "Instructional role of the slide within the deck; for example title, agenda, transition, definition, example, mechanism, result, summary, or appendix.",
    "slides[].local_summary": "One- or two-sentence summary of the slide's main instructional content.",
    "slides[].key_concepts": "List of key concepts explicitly present on the slide.",
    "slides[].evidence_regions": "List of human-readable descriptions of important visible regions on the slide.",
    "slides[].eligible_for_questions": "Whether the slide should receive any comprehension questions.",
    "slides[].eligibility_reason": "Explanation for why the slide should or should not receive questions.",
    "slides[].question_budget": "Recommended number of questions for this slide in deck context.",
    "slides[].question_mix": "Recommended mix of question types for this slide.",
    "slides[].questions": "Variable-length list of question objects for this slide.",
    "slides[].questions[].question_id": "Stable identifier for a question within a slide.",
    "slides[].questions[].question_type": "Controlled label for the question form or reasoning type.",
    "slides[].questions[].prompt": "Question text shown to the learner.",
    "slides[].questions[].options": "List of answer options for a multiple-choice item; empty otherwise.",
    "slides[].questions[].answer": "Gold answer or bounded reference answer grounded in the slide.",
    "slides[].questions[].evidence_span": "Short description of where the answer is visible on the slide.",
    "slides[].questions[].difficulty": "Relative difficulty label such as low, medium, or high.",
    "slides[].questions[].purpose": "Instructional purpose such as terminology, relation check, interpretation, or synthesis.",
    "slides[].questions[].fidelity_score": "1-5 judgment of whether the question is answerable from the slide alone.",
    "slides[].questions[].fidelity_notes": "Short rationale for the fidelity score.",
    "slides[].evaluation": "Slide-level evaluation for the full question bundle.",
    "slides[].evaluation.coverage_score": "1-5 score for how well the slide's question bundle covers the slide's important content; null when the slide intentionally has no questions.",
    "slides[].evaluation.coverage_notes": "Short rationale for the coverage score.",
    "slides[].evaluation.scaffolding_score": "1-5 score for how well the question bundle forms an instructional progression; null when the slide intentionally has no questions.",
    "slides[].evaluation.scaffolding_notes": "Short rationale for the scaffolding score."
}

# Restricted vocabulary representing the dominant visual forms a slide might take.
MODALITY_CHOICES = ["text", "diagram", "table", "chart", "layout-aware", "image-plus-text", "mixed"]

# Restricted vocabulary representing the pedagogical or structural purpose of a slide.
ROLE_CHOICES = ["title", "agenda", "transition", "definition", "example", "mechanism", "comparison", "result", "summary", "administrative", "appendix", "review", "reference"]

# Restricted vocabulary representing the specific format or cognitive skill targeted by a generated question.
QUESTION_TYPE_CHOICES = ["fill_blank", "mcq", "open_ended", "short_answer", "diagram_labeling", "comparison", "interpretation", "evidence_localization"]


class LocalSectionHypothesis(BaseModel):
    section_title: str
    start_slide: int
    end_slide: int
    section_summary: str


class SlidePlan(BaseModel):
    slide_number: int
    slide_title: str
    local_summary: str
    modality_type: str
    role_in_deck: str
    eligible_for_questions: bool
    eligibility_reason: str
    question_budget: int = Field(ge=0, le=MAX_QUESTIONS_PER_SLIDE)
    question_mix: List[str]


class WindowPlan(BaseModel):
    local_section_hypotheses: List[LocalSectionHypothesis]
    slides: List[SlidePlan]


class SectionModel(BaseModel):
    section_id: str
    start_slide: int
    end_slide: int
    section_title: str
    section_summary: str


class DeckPlan(BaseModel):
    deck_topic: str
    target_audience: str
    learning_goals: List[str]
    sections: List[SectionModel]
    coverage_targets: List[str]
    global_notes: str
    slides: List[SlidePlan]


class QuestionModel(BaseModel):
    question_id: str
    question_type: str
    prompt: str
    options: List[str] = Field(default_factory=list)
    answer: str
    evidence_span: str
    difficulty: str
    purpose: str
    fidelity_score: int = Field(ge=1, le=5)
    fidelity_notes: str


class SlideEvaluationModel(BaseModel):
    coverage_score: int = Field(ge=1, le=5)
    coverage_notes: str
    scaffolding_score: int = Field(ge=1, le=5)
    scaffolding_notes: str


class SlideAnnotationModel(BaseModel):
    key_concepts: List[str]
    evidence_regions: List[str]
    questions: List[QuestionModel]
    evaluation: SlideEvaluationModel


class ReconcileAction(BaseModel):
    slide_number: int
    action: str
    new_question_budget: int = Field(ge=0, le=MAX_QUESTIONS_PER_SLIDE)
    reason: str


class ReconciliationModel(BaseModel):
    revised_slide_actions: List[ReconcileAction]
    deck_reconciliation_notes: str
    uncovered_learning_goals: List[str]
    redundancy_warnings: List[str]


@dataclass
class SlideAsset:
    slide_number: int
    png_bytes: bytes
    text: str
    text_snippet: str


# --- LLM Prompts ---

# Prompt for the initial sliding-window analysis phase. Instructs the LLM to extract roles, modalities, and budgets.
WINDOW_PLANNER_PROMPT = """
You are analyzing a contiguous window from a larger lecture slide deck.

For each slide in this window:
- infer slide_title
- write a short local_summary
- assign modality_type
- assign role_in_deck
- decide whether the slide is eligible for learner-facing comprehension questions
- give an eligibility_reason
- assign a question_budget from 0 to 5
- assign a question_mix

Important:
- Use neighboring slides in the window to reason about redundancy and transitions.
- It is acceptable to assign zero questions.
- Do not force a fixed number of questions.
- Favor low budgets for title, agenda, transition, administrative, appendix, and repeated recap slides.
- Favor higher budgets for rich mechanism, comparison, result, diagram, chart, table, or synthesis slides.
- question_mix must use only these values:
  ["fill_blank", "mcq", "open_ended", "short_answer", "diagram_labeling", "comparison", "interpretation", "evidence_localization"]
- modality_type must use only these values:
  ["text", "diagram", "table", "chart", "layout-aware", "image-plus-text", "mixed"]
- role_in_deck must use only these values:
  ["title", "agenda", "transition", "definition", "example", "mechanism", "comparison", "result", "summary", "administrative", "appendix", "review", "reference"]

Return JSON only. Do not include explanatory prose outside JSON.
""".strip()


# Prompt for the synthesis phase. Instructs the LLM to merge overlapping window plans into a coherent deck outline.
DECK_SYNTHESIS_PROMPT = """
You are merging overlapping window-level analyses of one lecture slide deck into one final deck plan.

Goals:
1. Infer the deck topic and likely target audience.
2. Infer deck-level learning goals.
3. Produce section boundaries for the full deck.
4. Resolve conflicting window-level slide plans conservatively.
5. Return exactly one slide plan object per slide number.

Important:
- Preserve zero-question slides when they are non-instructional, redundant, or too thin.
- Some slides may deserve more than three questions.
- Keep question budgets based on instructional importance, self-containedness, evidence richness, and novelty.
- Use only the allowed label vocabularies already present in the window plans.
- Sections should be contiguous and ordered.

Return JSON only. Do not include explanatory prose outside JSON.
""".strip()


# Prompt for generating the actual comprehension questions for a single slide based on its synthesized plan.
SLIDE_ANNOTATOR_PROMPT = """
You are generating slidesqaqa annotations for one slide within a lecture deck.

Use both the local slide evidence and the provided deck context.

Your tasks:
1. Identify key_concepts explicitly present on the slide.
2. Identify 2 to 6 evidence_regions as short human-readable descriptions of important visible regions.
3. Generate exactly the assigned question budget in the supplied question mix.
4. Every question must be answerable from the slide alone.
5. Every answer must be bounded and evidence-grounded.
6. Use deck context only to decide what is educationally important. Do not answer from hidden lecture knowledge.
7. Avoid redundancy with the neighboring slides when possible.

Question-writing guidance:
- On text slides, favor terminology, distinctions, and concise explanation.
- On diagram slides, favor component labeling, relationships, flow, and mechanism.
- On table/chart slides, favor lookup, comparison, trend, and interpretation.
- On layout-aware slides, favor spatial or grouping-based reasoning when relevant.
- If a question_type is mcq, include exactly 4 options.
- If a question_type is not mcq, options must be an empty list.
- fidelity_score must be an integer from 1 to 5.
- coverage_score and scaffolding_score must be integers from 1 to 5.

Coverage guidance:
- 1 means poor coverage or repeated tiny facts.
- 3 means adequate coverage of the main concept and at least one secondary element.
- 5 means strong coverage of the slide's important visible content.

Scaffolding guidance:
- 1 means random or disconnected.
- 3 means reasonable progression.
- 5 means coherent progression from simpler to deeper understanding.

Return JSON only. Do not include explanatory prose outside JSON.
""".strip()


# Prompt for the final reconciliation phase. Evaluates the generated question set and balances coverage/scaffolding.
RECONCILIATION_PROMPT = """
You are reconciling a provisional slidesqaqa annotation set for a full lecture deck.

You are given:
- deck metadata
- deck analysis
- all slide plans
- all provisional slide annotations

Your task is to improve the deck as a whole.

Goals:
1. Detect redundant question sets across nearby slides.
2. Detect slides that should have fewer questions.
3. Detect rich slides that deserve more questions.
4. Detect places where learning-goal coverage is unbalanced.
5. Detect weak scaffolding within sections.

Rules:
- Do not force similar budgets across all slides.
- Preserve zero-question slides when they are truly non-instructional or redundant.
- Prefer deleting weak or redundant questions rather than inventing extra ones.
- Use this action vocabulary only:
  ["keep", "reduce", "expand", "zero_out", "rewrite"]
- For each slide, return one action and a new_question_budget between 0 and 5.

Return JSON only. Do not include explanatory prose outside JSON.
""".strip()


app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024


def utc_now() -> str:
    """Returns the current UTC time as an ISO 8601 string, excluding microseconds."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def log_line(message: str) -> str:
    """Formats a message into a standard log line with a timestamp prefix."""
    return f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n"


def safe_slug(value: str) -> str:
    """Converts a given string into a safe file path slug. Non-alphanumerics are replaced with hyphens."""
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value or "deck"


def is_pdf_filename(filename: str) -> bool:
    """Checks if the provided filename belongs to an allowed extension (e.g. .pdf)."""
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def clamp_budget(value: Any) -> int:
    """
    Forces the provided question budget value to be an integer within the allowable bounds
    (0 to MAX_QUESTIONS_PER_SLIDE).
    """
    try:
        n = int(value)
    except Exception:
        n = 0
    return max(0, min(MAX_QUESTIONS_PER_SLIDE, n))


def normalize_modality(value: str) -> str:
    """Ensures the modality string is valid according to predefined MODALITY_CHOICES."""
    return value if value in MODALITY_CHOICES else "mixed"


def normalize_role(value: str) -> str:
    """Ensures the slide role string is valid according to predefined ROLE_CHOICES."""
    return value if value in ROLE_CHOICES else "reference"


def normalize_mix(mix: Iterable[str], budget: int, modality: str) -> List[str]:
    """
    Filters the question mix to only contain permitted question types.
    Falls back to a dynamic default mix if the list is empty or entirely invalid.
    Truncates the list based on the assigned budget.
    """
    cleaned = [m for m in mix if m in QUESTION_TYPE_CHOICES]
    if not cleaned:
        cleaned = default_question_mix(modality, budget)
    return cleaned[:max(0, budget)]


def default_question_mix(modality: str, budget: int) -> List[str]:
    """
    Returns a standard blend of question types uniquely matched to the
    dominant visual modality of the slide (e.g., diagram vs. table).
    """
    if budget <= 0:
        return []
    if modality == "diagram":
        base = ["diagram_labeling", "comparison", "open_ended", "evidence_localization", "interpretation"]
    elif modality in {"table", "chart"}:
        base = ["short_answer", "comparison", "interpretation", "mcq", "open_ended"]
    elif modality == "layout-aware":
        base = ["evidence_localization", "comparison", "open_ended", "mcq", "interpretation"]
    elif modality == "image-plus-text":
        base = ["evidence_localization", "mcq", "open_ended", "comparison", "interpretation"]
    else:
        # Default fallback for "text" or "mixed" modality
        base = ["fill_blank", "mcq", "open_ended", "short_answer", "comparison"]
    return base[:budget]


def extract_text(page: fitz.Page) -> str:
    """Extracts raw text data from a single PyMuPDF page object, standardizing newlines."""
    text = page.get_text("text") or ""
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def make_text_snippet(text: str, max_chars: int = 800) -> str:
    """Compresses extracted text to a maximum character length for inclusion in prompt contexts."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def render_page_png(page: fitz.Page, dpi: int = 144) -> bytes:
    """Renders a PDF page to a PNG byte array via PyMuPDF (fitz), scaling according to the requested DPI."""
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=matrix, alpha=False)
    return pix.tobytes("png")


def preprocess_pdf(pdf_path: Path, dpi: int = 144) -> List[SlideAsset]:
    """
    Opens a PDF file and loops over every page, extracting its raw text and rendering a PNG.
    Returns a list of structured SlideAsset data classes.
    """
    doc = fitz.open(pdf_path)
    slides: List[SlideAsset] = []
    try:
        for idx in range(doc.page_count):
            page = doc[idx]
            png_bytes = render_page_png(page, dpi=dpi)
            text = extract_text(page)
            slides.append(
                SlideAsset(
                    slide_number=idx + 1,
                    png_bytes=png_bytes,
                    text=text,
                    text_snippet=make_text_snippet(text),
                )
            )
    finally:
        doc.close()
    return slides


def make_contact_sheet(slides: List[SlideAsset], thumb_width: int = 360, cols: int = 2) -> bytes:
    """
    Stitches multiple slide images together into a single "contact sheet" PNG grid.
    This provides the LLM with sequential visual context in a single large image,
    lowering total multi-modal payload requests while retaining transition information.
    """
    thumbs: List[Image.Image] = []
    for slide in slides:
        img = Image.open(io.BytesIO(slide.png_bytes)).convert("RGB")
        img.thumbnail((thumb_width, thumb_width), Image.Resampling.LANCZOS)

        # Add a border and text header above the thumbnail
        canvas = Image.new("RGB", (thumb_width + 24, img.height + 48), "white")
        canvas.paste(img, (12, 28))
        draw = ImageDraw.Draw(canvas)
        draw.rectangle((0, 0, canvas.width, 24), fill=(245, 247, 252))
        draw.text((10, 6), f"Slide {slide.slide_number}", fill=(20, 25, 35))
        canvas = ImageOps.expand(canvas, border=1, fill=(200, 205, 215))
        thumbs.append(canvas)

    if not thumbs:
        blank = Image.new("RGB", (800, 600), "white")
        out = io.BytesIO()
        blank.save(out, format="PNG")
        return out.getvalue()

    # Calculate grid dimensions based on image counts
    cols = max(1, cols)
    rows = math.ceil(len(thumbs) / cols)
    cell_w = max(img.width for img in thumbs)
    cell_h = max(img.height for img in thumbs)
    margin = 16

    # Paste thumbnails into the global sheet
    sheet = Image.new("RGB", (cols * cell_w + (cols + 1) * margin, rows * cell_h + (rows + 1) * margin), (238, 242, 250))
    for i, img in enumerate(thumbs):
        r = i // cols
        c = i % cols
        x = margin + c * (cell_w + margin)
        y = margin + r * (cell_h + margin)
        sheet.paste(img, (x, y))
    out = io.BytesIO()
    sheet.save(out, format="PNG")
    return out.getvalue()


def iter_windows(total: int, size: int = 8, overlap: int = 2) -> Iterable[tuple[int, int]]:
    """
    Generator that creates tuple boundaries (start, end) defining a sliding
    window over a slide deck, with a configurable amount of slide overlap.
    """
    if total <= 0:
        return
    start = 1
    while start <= total:
        end = min(total, start + size - 1)
        yield start, end
        if end >= total:
            break
        # Shift the start window while retaining 'overlap' amount of previous frames
        start = max(start + 1, end - overlap + 1)


def get_client(api_key_override: Optional[str] = None) -> genai.Client:
    """Retrieves a Google GenAI client instance using the provided API key."""
    api_key = (api_key_override or "").strip()
    if not api_key:
        raise RuntimeError("Gemini API key is required.")
    return genai.Client(api_key=api_key)


def generate_structured(client: genai.Client, model: str, contents: List[Any], schema_model: type[BaseModel], temperature: float = 0.2) -> BaseModel:
    """
    Wraps the GenAI client call to guarantee JSON schema structured output.
    Returns the populated Pydantic schema model on success.
    """
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config={
            "temperature": temperature,
            "response_mime_type": "application/json",
            "response_json_schema": schema_model.model_json_schema(),
        },
    )
    text = (response.text or "").strip()
    if not text:
        raise RuntimeError("Gemini returned an empty response.")
    try:
        return schema_model.model_validate_json(text)
    except ValidationError as exc:
        raise RuntimeError(f"Gemini returned JSON that did not validate for {schema_model.__name__}: {exc}") from exc


def window_prompt_text(start_slide: int, end_slide: int, slides: List[SlideAsset]) -> str:
    """Assembles the LLM text prompt for the sliding-window analysis phase."""
    parts = [WINDOW_PLANNER_PROMPT, "", f"Window range: slides {start_slide} to {end_slide}.", "", "Native text snippets by slide:"]
    for slide in slides:
        snippet = slide.text_snippet or "(no extractable text)"
        parts.append(f"\nSlide {slide.slide_number} text snippet:\n{snippet}")
    return "\n".join(parts)


def synthesis_prompt_text(window_outputs: List[Dict[str, Any]], total_slides: int, citation: str, budget_constraint: str = "") -> str:
    """Assembles the LLM text prompt to merge multiple window plans into a coherent whole deck."""
    serialized = json.dumps(window_outputs, ensure_ascii=False, indent=2)
    parts = [
        DECK_SYNTHESIS_PROMPT,
        "",
        f"Deck citation:\n{citation}",
        f"Total slides: {total_slides}",
    ]
    if budget_constraint:
        parts.extend(["", f"CONSTRAINT: {budget_constraint}"])
    parts.extend([
        "",
        "Window analyses JSON:",
        serialized,
    ])
    return "\n".join(parts)


def slide_prompt_text(plan: Dict[str, Any], deck_plan: Dict[str, Any], prev_summary: str, next_summary: str) -> str:
    """Assembles the LLM text prompt targeting generation of comprehension questions for a specific slide."""
    compact_deck_context = {
        "deck_topic": deck_plan.get("deck_topic", ""),
        "target_audience": deck_plan.get("target_audience", ""),
        "learning_goals": deck_plan.get("learning_goals", []),
        "sections": deck_plan.get("sections", []),
    }
    compact_slide_context = {
        "slide_plan": plan,
        "previous_slide_summary": prev_summary,
        "next_slide_summary": next_summary,
    }
    return "\n".join(
        [
            SLIDE_ANNOTATOR_PROMPT,
            "",
            "Deck context JSON:",
            json.dumps(compact_deck_context, ensure_ascii=False, indent=2),
            "",
            "Target slide context JSON:",
            json.dumps(compact_slide_context, ensure_ascii=False, indent=2),
        ]
    )


def reconciliation_prompt_text(deck_metadata: Dict[str, Any], deck_analysis: Dict[str, Any], slides: List[Dict[str, Any]], budget_constraint: str = "") -> str:
    """Assembles the LLM text prompt asking it to evaluate the complete generated question set and issue revisions."""
    compact_slides = []
    for slide in slides:
        compact_slides.append(
            {
                "slide_number": slide["slide_number"],
                "slide_title": slide["slide_title"],
                "role_in_deck": slide["role_in_deck"],
                "modality_type": slide["modality_type"],
                "eligible_for_questions": slide["eligible_for_questions"],
                "question_budget": slide["question_budget"],
                "question_count": len(slide.get("questions", [])),
                "local_summary": slide.get("local_summary", ""),
                "question_prompts": [q.get("prompt", "") for q in slide.get("questions", [])],
                "coverage_score": slide.get("evaluation", {}).get("coverage_score"),
                "scaffolding_score": slide.get("evaluation", {}).get("scaffolding_score"),
            }
        )
    parts = [
        RECONCILIATION_PROMPT,
        "",
    ]
    if budget_constraint:
        parts.extend([f"CONSTRAINT: {budget_constraint}", ""])
    parts.extend([
        "Deck metadata JSON:",
        json.dumps(deck_metadata, ensure_ascii=False, indent=2),
        "",
        "Deck analysis JSON:",
        json.dumps(deck_analysis, ensure_ascii=False, indent=2),
        "",
        "Provisional slide annotation summary JSON:",
        json.dumps(compact_slides, ensure_ascii=False, indent=2),
    ])
    return "\n".join(parts)


def question_sort_key(question: Dict[str, Any]) -> tuple[int, str]:
    """Generates a sorting key to order questions by a pedagogical progression type."""
    order = {
        "fill_blank": 0,
        "short_answer": 1,
        "mcq": 2,
        "diagram_labeling": 3,
        "comparison": 4,
        "evidence_localization": 5,
        "interpretation": 6,
        "open_ended": 7,
    }
    return (order.get(question.get("question_type", ""), 99), question.get("question_id", ""))


def apply_slide_plan_heuristics(plans: List[SlidePlan], slides: List[SlideAsset]) -> List[SlidePlan]:
    """
    Runs basic static analysis (heuristics) over provisional plan assignments to zero out budgets
    on exact duplicate slides or overly thin transitional slides.
    """
    by_num = {s.slide_number: s for s in slides}
    revised: List[SlidePlan] = []
    previous_text_norm = ""
    for plan in plans:
        text = by_num.get(plan.slide_number).text_snippet if by_num.get(plan.slide_number) else ""
        text_norm = re.sub(r"\s+", " ", (text or "")).strip().lower()
        title_like = plan.role_in_deck in {"title", "agenda", "transition", "administrative", "appendix"}
        duplicate_like = previous_text_norm and text_norm and text_norm == previous_text_norm

        # Zero out titles with low budgets
        if title_like and plan.question_budget <= 1:
            plan.question_budget = 0
            plan.eligible_for_questions = False
            plan.eligibility_reason = f"{plan.role_in_deck} slide; zeroed by heuristic."
            plan.question_mix = []
        # Zero out explicit duplicate text slides
        elif duplicate_like:
            plan.question_budget = 0
            plan.eligible_for_questions = False
            plan.eligibility_reason = "Near-duplicate of previous slide; zeroed by heuristic."
            plan.question_mix = []
        else:
            plan.question_budget = clamp_budget(plan.question_budget)
            plan.modality_type = normalize_modality(plan.modality_type)
            plan.role_in_deck = normalize_role(plan.role_in_deck)
            plan.question_mix = normalize_mix(plan.question_mix, plan.question_budget, plan.modality_type)
            if plan.question_budget == 0:
                plan.eligible_for_questions = False
                if not plan.eligibility_reason:
                    plan.eligibility_reason = "No questions assigned."
                plan.question_mix = []
        revised.append(plan)
        previous_text_norm = text_norm or previous_text_norm
    return revised


def build_deck_metadata(job_id: str, citation: str, deck_url: str, source_file: str, total_slides: int) -> Dict[str, Any]:
    """Helper formatting function to construct the final deck_metadata JSON blob."""
    return {
        "deck_id": job_id,
        "deck": citation,
        "deck_url": deck_url,
        "source_file": source_file,
        "total_slides": total_slides,
        "processed_at": utc_now(),
    }


def build_empty_slide_record(plan: SlidePlan) -> Dict[str, Any]:
    """Constructs a completely barren slide record for skipped or zero-budget slides."""
    return {
        "slide_id": f"slide-{plan.slide_number:03d}",
        "slide_number": plan.slide_number,
        "slide_title": plan.slide_title,
        "modality_type": normalize_modality(plan.modality_type),
        "role_in_deck": normalize_role(plan.role_in_deck),
        "local_summary": plan.local_summary,
        "key_concepts": [],
        "evidence_regions": [],
        "eligible_for_questions": False,
        "eligibility_reason": plan.eligibility_reason or "No questions assigned.",
        "question_budget": 0,
        "question_mix": [],
        "questions": [],
        "evaluation": {
            "coverage_score": None,
            "coverage_notes": "No questions for this slide by design.",
            "scaffolding_score": None,
            "scaffolding_notes": "No questions for this slide by design.",
        },
    }


def build_slide_record(plan: SlidePlan, annotation: SlideAnnotationModel) -> Dict[str, Any]:
    """
    Constructs a populated slide record by marrying the synthesis `SlidePlan` instructions
    with the actual output `SlideAnnotationModel` generation. Limits options and orders questions.
    """
    questions: List[Dict[str, Any]] = []
    for idx, q in enumerate(annotation.questions[: plan.question_budget], start=1):
        question_type = q.question_type if q.question_type in QUESTION_TYPE_CHOICES else default_question_mix(plan.modality_type, plan.question_budget)[min(idx - 1, max(0, plan.question_budget - 1))]
        options = list(q.options) if question_type == "mcq" else []

        # Ensure MCQs always have exactly 4 options
        if question_type == "mcq":
            options = options[:4]
            while len(options) < 4:
                options.append(f"Option {len(options) + 1}")

        questions.append(
            {
                "question_id": q.question_id or f"q{idx}",
                "question_type": question_type,
                "prompt": q.prompt.strip(),
                "options": options,
                "answer": q.answer.strip(),
                "evidence_span": q.evidence_span.strip(),
                "difficulty": q.difficulty.strip() or "medium",
                "purpose": q.purpose.strip() or "fact_recall",
                "fidelity_score": int(q.fidelity_score),
                "fidelity_notes": q.fidelity_notes.strip(),
            }
        )

    # Standardize question order before returning
    questions.sort(key=question_sort_key)
    return {
        "slide_id": f"slide-{plan.slide_number:03d}",
        "slide_number": plan.slide_number,
        "slide_title": plan.slide_title,
        "modality_type": normalize_modality(plan.modality_type),
        "role_in_deck": normalize_role(plan.role_in_deck),
        "local_summary": plan.local_summary,
        "key_concepts": annotation.key_concepts,
        "evidence_regions": annotation.evidence_regions,
        "eligible_for_questions": True,
        "eligibility_reason": plan.eligibility_reason,
        "question_budget": plan.question_budget,
        "question_mix": normalize_mix(plan.question_mix, plan.question_budget, plan.modality_type),
        "questions": questions,
        "evaluation": {
            "coverage_score": int(annotation.evaluation.coverage_score),
            "coverage_notes": annotation.evaluation.coverage_notes,
            "scaffolding_score": int(annotation.evaluation.scaffolding_score),
            "scaffolding_notes": annotation.evaluation.scaffolding_notes,
        },
    }


def build_final_json(deck_metadata: Dict[str, Any], deck_analysis: Dict[str, Any], reconciliation: Dict[str, Any], slides: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Wraps all intermediate JSON structures into the final, compliant output format."""
    slides = sorted(slides, key=lambda s: s["slide_number"])
    return {
        "schema_version": "1.0",
        "field_descriptions": FIELD_DESCRIPTIONS,
        "deck_metadata": deck_metadata,
        "deck_analysis": deck_analysis,
        "reconciliation": reconciliation,
        "slides": slides,
    }


@app.get("/")
def index() -> str:
    return render_template_string(HTML_PAGE, default_model=DEFAULT_MODEL)


@app.get("/scroll.svg")
def serve_favicon() -> Response:
    return send_file("scroll.svg", mimetype="image/svg+xml")


@app.get("/health-check")
def health_check() -> Dict[str, Any]:
    """Returns a simple JSON response indicating the application is healthy."""
    return {
        "schemaVersion": 1,
        "label": "App health",
        "message": "online",
        "color": "brightgreen",
        "status": "ok"
    }


@app.post("/analyze")
def analyze() -> Response:
    uploaded = request.files.get("deck_file")
    citation = (request.form.get("citation") or "").strip()
    deck_url = (request.form.get("deck_url") or "").strip()
    model = (request.form.get("model") or DEFAULT_MODEL).strip()
    api_key_override = (request.form.get("api_key") or "").strip()
    start_page_str = request.form.get("start_page", "1").strip()
    end_page_str = request.form.get("end_page", "").strip()
    budget_mode = request.form.get("budget_mode", "default")
    target_total = request.form.get("target_total", "").strip()
    target_mean = request.form.get("target_mean", "").strip()

    has_file = uploaded is not None and bool(uploaded.filename)
    if not has_file and not deck_url:
        return Response("Either upload a file or provide a URL.\n", status=400, mimetype="text/plain")
    if not citation:
        return Response("Citation is required.\n", status=400, mimetype="text/plain")
    if not api_key_override:
        return Response("Gemini API key is required.\n", status=400, mimetype="text/plain")

    try:
        start_page = int(start_page_str)
        if start_page < 1:
            start_page = 1
    except ValueError:
        start_page = 1

    try:
        end_page = int(end_page_str) if end_page_str else None
    except ValueError:
        end_page = None

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # We will determine job_id and saved_name later
    job_id = ""
    saved_name = "deck.pdf"
    file_bytes = None
    url_bytes = None

    if uploaded and uploaded.filename:
        if not is_pdf_filename(uploaded.filename):
             return Response("Upload must be a .pdf file.\n", status=400, mimetype="text/plain")
        file_bytes = uploaded.read()
        job_id = f"{safe_slug(Path(uploaded.filename).stem)}"
        saved_name = secure_filename(uploaded.filename) or "deck.pdf"

    if deck_url:
        try:
            req = urllib.request.Request(deck_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=30) as response:
                url_bytes = response.read()
        except Exception as e:
            return Response(f"Failed to download from URL: {e}\n", status=400, mimetype="text/plain")

        if not job_id:
            job_id = f"{safe_slug(deck_url.split('/')[-1])}"
            if not job_id.endswith(".pdf"):
                job_id += ".pdf"
            saved_name = secure_filename(deck_url.split('/')[-1]) or "deck.pdf"
            if not saved_name.lower().endswith(".pdf"):
                saved_name += ".pdf"

    if file_bytes and url_bytes:
        file_hash = hashlib.md5(file_bytes).hexdigest()
        url_hash = hashlib.md5(url_bytes).hexdigest()
        if file_hash != url_hash:
             return Response("The uploaded file and the file at the provided URL do not match.\n", status=400, mimetype="text/plain")

    final_bytes = url_bytes if url_bytes else file_bytes

    budget_constraint = ""
    if budget_mode == "total" and target_total:
        try:
            val = int(target_total)
            budget_constraint = f"Adjust your generated question budgets so that the total number of questions for the entire deck is exactly {val}."
        except ValueError:
            pass
    elif budget_mode == "mean" and target_mean:
        try:
            val = float(target_mean)
            budget_constraint = f"Adjust your generated question budgets so that the mean number of questions per slide across the entire deck is exactly {val}."
        except ValueError:
            pass

    job_id = f"{job_id}-{uuid.uuid4().hex[:8]}"
    job_dir = UPLOAD_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = job_dir / saved_name

    with open(pdf_path, 'wb') as f:
        f.write(final_bytes)

    def generate() -> Iterable[str]:
        try:
            yield log_line(f"Saved upload to {pdf_path}")
            yield log_line(f"Model: {model}")

            client = get_client(api_key_override)

            yield log_line("Rendering pages and extracting native PDF text...")
            slides = preprocess_pdf(pdf_path, dpi=144)

            if end_page is not None:
                slides = [s for s in slides if start_page <= s.slide_number <= end_page]
            else:
                slides = [s for s in slides if start_page <= s.slide_number]

            if not slides:
                raise RuntimeError("The PDF did not contain any pages.")
            yield log_line(f"Prepared {len(slides)} slide assets.")

            deck_metadata = build_deck_metadata(
                job_id=job_id,
                citation=citation,
                deck_url=deck_url,
                source_file=pdf_path.name,
                total_slides=len(slides),
            )

            window_outputs: List[Dict[str, Any]] = []
            windows = list(iter_windows(len(slides), size=8, overlap=2))
            for idx, (start, end) in enumerate(windows, start=1):
                window_slides = [s for s in slides if start <= s.slide_number <= end]
                yield log_line(f"Deck planner window {idx}/{len(windows)}: slides {start}-{end}")
                contact_sheet = make_contact_sheet(window_slides, thumb_width=320, cols=2)
                prompt = window_prompt_text(start, end, window_slides)
                plan = generate_structured(
                    client=client,
                    model=model,
                    contents=[
                        prompt,
                        types.Part.from_bytes(data=contact_sheet, mime_type="image/png"),
                    ],
                    schema_model=WindowPlan,
                    temperature=0.2,
                )
                window_outputs.append(plan.model_dump(mode="json"))
                yield log_line(f"Completed window planning for slides {start}-{end}.")

            if budget_constraint:
                yield log_line(f"Budget constraint: {budget_constraint}")

            yield log_line("Synthesizing final deck plan across all windows...")
            deck_plan_model = generate_structured(
                client=client,
                model=model,
                contents=[synthesis_prompt_text(window_outputs, len(slides), citation, budget_constraint)],
                schema_model=DeckPlan,
                temperature=0.1,
            )
            revised_plans = apply_slide_plan_heuristics(deck_plan_model.slides, slides)
            deck_analysis = {
                "deck_topic": deck_plan_model.deck_topic,
                "target_audience": deck_plan_model.target_audience,
                "learning_goals": deck_plan_model.learning_goals,
                "sections": [s.model_dump(mode="json") for s in deck_plan_model.sections],
                "coverage_targets": deck_plan_model.coverage_targets,
                "global_notes": deck_plan_model.global_notes,
            }
            plan_by_slide = {p.slide_number: p for p in revised_plans}
            yield log_line(f"Deck plan ready. Eligible slides: {sum(1 for p in revised_plans if p.eligible_for_questions)} / {len(revised_plans)}")

            provisional_slides: List[Dict[str, Any]] = []
            for plan in revised_plans:
                if not plan.eligible_for_questions or plan.question_budget <= 0:
                    provisional_slides.append(build_empty_slide_record(plan))
                    yield log_line(f"Slide {plan.slide_number}: zero questions ({plan.eligibility_reason})")
                    continue

                slide_asset = slides[plan.slide_number - 1]
                prev_summary = plan_by_slide.get(plan.slide_number - 1).local_summary if (plan.slide_number - 1) in plan_by_slide else ""
                next_summary = plan_by_slide.get(plan.slide_number + 1).local_summary if (plan.slide_number + 1) in plan_by_slide else ""
                prompt = slide_prompt_text(plan.model_dump(mode="json"), deck_plan_model.model_dump(mode="json"), prev_summary, next_summary)

                yield log_line(f"Annotating slide {plan.slide_number} with budget {plan.question_budget}...")
                annotation = generate_structured(
                    client=client,
                    model=model,
                    contents=[
                        prompt,
                        "\nNative PDF text from the target slide:\n" + (slide_asset.text or "(no extractable text)"),
                        types.Part.from_bytes(data=slide_asset.png_bytes, mime_type="image/png"),
                    ],
                    schema_model=SlideAnnotationModel,
                    temperature=0.25,
                )
                provisional_slides.append(build_slide_record(plan, annotation))
                yield log_line(f"Slide {plan.slide_number}: generated {len(provisional_slides[-1]['questions'])} questions.")

            yield log_line("Running deck-level reconciliation...")
            reconciliation_model = generate_structured(
                client=client,
                model=model,
                contents=[reconciliation_prompt_text(deck_metadata, deck_analysis, provisional_slides, budget_constraint)],
                schema_model=ReconciliationModel,
                temperature=0.1,
            )

            action_map = {a.slide_number: a for a in reconciliation_model.revised_slide_actions}
            final_slides: List[Dict[str, Any]] = []
            for slide_record in provisional_slides:
                slide_number = slide_record["slide_number"]
                action = action_map.get(slide_number)
                if action is None:
                    final_slides.append(slide_record)
                    continue

                current_plan = plan_by_slide[slide_number]
                new_budget = clamp_budget(action.new_question_budget)
                act = action.action if action.action in {"keep", "reduce", "expand", "zero_out", "rewrite"} else "keep"

                if act == "zero_out" or new_budget == 0:
                    current_plan.eligible_for_questions = False
                    current_plan.question_budget = 0
                    current_plan.question_mix = []
                    current_plan.eligibility_reason = f"Reconciliation: {action.reason}"
                    final_slides.append(build_empty_slide_record(current_plan))
                    yield log_line(f"Slide {slide_number}: zeroed during reconciliation.")
                    continue

                needs_rerun = act in {"reduce", "expand", "rewrite"} or new_budget != slide_record["question_budget"]
                if not needs_rerun:
                    final_slides.append(slide_record)
                    continue

                current_plan.eligible_for_questions = True
                current_plan.question_budget = new_budget
                current_plan.question_mix = normalize_mix(current_plan.question_mix, current_plan.question_budget, current_plan.modality_type)
                current_plan.eligibility_reason = f"Reconciliation: {action.reason}"
                slide_asset = slides[slide_number - 1]
                prev_summary = plan_by_slide.get(slide_number - 1).local_summary if (slide_number - 1) in plan_by_slide else ""
                next_summary = plan_by_slide.get(slide_number + 1).local_summary if (slide_number + 1) in plan_by_slide else ""
                prompt = slide_prompt_text(current_plan.model_dump(mode="json"), deck_plan_model.model_dump(mode="json"), prev_summary, next_summary)
                yield log_line(f"Slide {slide_number}: rerunning annotation after reconciliation ({act} -> budget {new_budget})...")
                annotation = generate_structured(
                    client=client,
                    model=model,
                    contents=[
                        prompt,
                        "\nNative PDF text from the target slide:\n" + (slide_asset.text or "(no extractable text)"),
                        types.Part.from_bytes(data=slide_asset.png_bytes, mime_type="image/png"),
                    ],
                    schema_model=SlideAnnotationModel,
                    temperature=0.2,
                )
                final_slides.append(build_slide_record(current_plan, annotation))

            reconciliation = reconciliation_model.model_dump(mode="json")
            final_doc = build_final_json(deck_metadata, deck_analysis, reconciliation, final_slides)
            final_json_path = job_dir / f"{safe_slug(Path(saved_name).stem)}_deck_annotation.json"
            final_json_path.write_text(json.dumps(final_doc, ensure_ascii=False, indent=2), encoding="utf-8")
            yield log_line(f"Saved final JSON to {final_json_path}")

            yield BEGIN_JSON_MARKER
            yield json.dumps(final_doc, ensure_ascii=False, indent=2)
            yield END_JSON_MARKER
        except Exception as exc:
            yield log_line(f"ERROR: {exc}")

    response = Response(stream_with_context(generate()), mimetype="text/plain; charset=utf-8")
    response.headers['X-Accel-Buffering'] = 'no'
    return response

if __name__ == "__main__":
    app.run(host='0.0.0.0', port=8080, threaded=True, debug=True)
