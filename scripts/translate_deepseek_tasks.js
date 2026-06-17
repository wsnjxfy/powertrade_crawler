const fs = require("node:fs");
const path = require("node:path");
const readline = require("node:readline/promises");
const { stdin: input, stdout: output } = require("node:process");

const puppeteer = require("puppeteer-core");
const { parse } = require("csv-parse/sync");
const { stringify } = require("csv-stringify/sync");

const DEFAULT_CSV = path.join(
  "translation_tasks",
  "gridstatus_description_translation_tasks.csv",
);
const DEFAULT_DEEPSEEK_URL = "https://chat.deepseek.com/";
const DEFAULT_USER_DATA_DIR = path.join(".browser-profiles", "deepseek");
const FIELDNAMES = [
  "id",
  "dataset_id",
  "description",
  "description_chinese",
  "translation_status",
  "translation_error",
];

function parseArgs(argv) {
  const options = {
    csvPath: DEFAULT_CSV,
    limit: null,
    headless: false,
    delayMs: 5000,
    batchSize: 50,
    batchPauseMs: 300000,
    responseTimeoutMs: 180000,
    stableMs: 4000,
    deepseekUrl: DEFAULT_DEEPSEEK_URL,
    userDataDir: DEFAULT_USER_DATA_DIR,
  };

  const positionals = [];
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--limit") {
      options.limit = Number(argv[++i]);
    } else if (arg === "--headless") {
      options.headless = true;
    } else if (arg === "--delay-ms") {
      options.delayMs = Number(argv[++i]);
    } else if (arg === "--batch-size") {
      options.batchSize = Number(argv[++i]);
    } else if (arg === "--batch-pause-ms") {
      options.batchPauseMs = Number(argv[++i]);
    } else if (arg === "--timeout-ms") {
      options.responseTimeoutMs = Number(argv[++i]);
    } else if (arg === "--stable-ms") {
      options.stableMs = Number(argv[++i]);
    } else if (arg === "--url") {
      options.deepseekUrl = argv[++i];
    } else if (arg === "--user-data-dir") {
      options.userDataDir = argv[++i];
    } else if (arg === "--help" || arg === "-h") {
      printHelp();
      process.exit(0);
    } else {
      positionals.push(arg);
    }
  }
  if (positionals[0]) {
    options.csvPath = positionals[0];
  }
  return options;
}

function printHelp() {
  console.log(`Usage:
  node scripts/translate_deepseek_tasks.js [csvPath] [options]

Options:
  --limit N              Translate at most N pending rows.
  --headless             Run browser in headless mode.
  --delay-ms N           Wait after each row. Default: 5000.
  --batch-size N         Pause after every N translated rows. Default: 50.
  --batch-pause-ms N     Batch pause duration. Default: 300000.
  --timeout-ms N         Max wait for one response. Default: 180000.
  --stable-ms N          Response text must stay unchanged for this long. Default: 4000.
  --url URL              DeepSeek chat URL. Default: ${DEFAULT_DEEPSEEK_URL}
  --user-data-dir DIR    Browser profile dir. Default: ${DEFAULT_USER_DATA_DIR}

Environment overrides:
  DEEPSEEK_CHROME_PATH   Chrome/Edge executable path.
  DEEPSEEK_INPUT_SELECTOR
  DEEPSEEK_SEND_SELECTOR
  DEEPSEEK_RESPONSE_SELECTOR
`);
}

function resolveExecutablePath() {
  if (process.env.DEEPSEEK_CHROME_PATH) {
    return process.env.DEEPSEEK_CHROME_PATH;
  }

  const candidates = [
    path.join(
      process.env.LOCALAPPDATA || "",
      "Google",
      "Chrome",
      "Application",
      "chrome.exe",
    ),
    path.join(
      process.env.ProgramFiles || "",
      "Google",
      "Chrome",
      "Application",
      "chrome.exe",
    ),
    path.join(
      process.env["ProgramFiles(x86)"] || "",
      "Microsoft",
      "Edge",
      "Application",
      "msedge.exe",
    ),
    path.join(
      process.env.ProgramFiles || "",
      "Microsoft",
      "Edge",
      "Application",
      "msedge.exe",
    ),
  ];

  const executablePath = candidates.find((candidate) => candidate && fs.existsSync(candidate));
  if (!executablePath) {
    throw new Error(
      "Could not find Chrome or Edge. Set DEEPSEEK_CHROME_PATH to your browser executable.",
    );
  }
  return executablePath;
}

function readTasks(csvPath) {
  if (!fs.existsSync(csvPath)) {
    throw new Error(`CSV file does not exist: ${csvPath}`);
  }
  const content = fs.readFileSync(csvPath, "utf8").replace(/^\uFEFF/, "");
  const rows = parse(content, {
    columns: true,
    skip_empty_lines: true,
    bom: true,
  });
  for (const row of rows) {
    for (const field of FIELDNAMES) {
      if (!(field in row)) {
        row[field] = "";
      }
    }
  }
  return rows;
}

function writeTasks(csvPath, rows) {
  const csv = stringify(rows, {
    header: true,
    columns: FIELDNAMES,
    bom: true,
  });
  const tempPath = `${csvPath}.tmp`;
  const attempts = 8;
  let lastError = null;

  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    try {
      fs.writeFileSync(tempPath, csv, "utf8");
      fs.renameSync(tempPath, csvPath);
      return;
    } catch (error) {
      lastError = error;
      if (!["EBUSY", "EPERM", "EACCES"].includes(error.code) || attempt === attempts) {
        break;
      }
      console.warn(
        `CSV is locked; retrying save ${attempt}/${attempts}. Close Excel/WPS if it is open.`,
      );
      Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1500);
    }
  }

  const rescuePath = `${csvPath}.autosave.${Date.now()}.csv`;
  fs.writeFileSync(rescuePath, csv, "utf8");
  throw new Error(
    [
      `Could not write CSV because it is locked: ${csvPath}`,
      "Close Excel/WPS/another editor that has the CSV open, then rerun the script.",
      `A rescue copy was written here: ${rescuePath}`,
      `Original error: ${lastError?.message || lastError}`,
    ].join("\n"),
  );
}

function isPending(row) {
  return (
    row.description &&
    !String(row.description_chinese || "").trim() &&
    String(row.translation_status || "pending").toLowerCase() !== "done"
  );
}

function buildStructuredPrompt(row) {
  return [
    "你正在处理一个 CSV 翻译任务。请严格按当前任务翻译，不要引用或延续前面的任务。",
    "",
    `任务 id: ${row.id || ""}`,
    `dataset_id: ${row.dataset_id || ""}`,
    "",
    "请把下面这段 GridStatus 电力市场数据集说明翻译成中文。",
    "要求：",
    "1. 保留 ISO、RTO、BAA、LMP、PJM、ERCOT、CAISO 等专有名词或缩写。",
    "2. 如果专有名词或缩写适合解释，请用括号补充简短中文解释。",
    "3. 语言要适合业务人员检索和理解。",
    "4. 不要把其他任务、其他 dataset_id、历史对话内容混入结果。",
    "5. description_chinese 必须是一个 JSON 字符串；如果需要分行，请在字符串里使用 \\n。",
    "6. 只返回一个 JSON 对象，不要使用 Markdown 代码块，不要输出额外说明。",
    "",
    "JSON 格式必须是：",
    `{"id":"${escapeForJsonExample(row.id || "")}","dataset_id":"${escapeForJsonExample(row.dataset_id || "")}","description_chinese":"中文翻译"}`,
    "",
    "原文：",
    row.description || "",
  ].join("\n");
}

function escapeForJsonExample(value) {
  return String(value).replace(/\\/g, "\\\\").replace(/"/g, '\\"');
}

function extractJsonObject(text) {
  const cleaned = text
    .replace(/```json/gi, "```")
    .replace(/```/g, "")
    .trim();

  const direct = tryParseJson(cleaned);
  if (direct) {
    return direct;
  }

  const matches = cleaned.match(/\{[\s\S]*?\}/g) || [];
  for (let i = matches.length - 1; i >= 0; i -= 1) {
    const parsed = tryParseJson(matches[i]);
    if (parsed) {
      return parsed;
    }
  }
  return null;
}

function tryParseJson(text) {
  try {
    const parsed = JSON.parse(text);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed;
    }
  } catch {
    return null;
  }
  return null;
}

function normalizeId(value) {
  return String(value ?? "").trim();
}

function parseStructuredTranslation(text, row) {
  const payload = extractJsonObject(text);
  if (!payload) {
    throw new Error(`DeepSeek response was not valid JSON: ${text.slice(0, 200)}`);
  }

  const expectedId = normalizeId(row.id);
  const actualId = normalizeId(payload.id);
  if (expectedId && actualId !== expectedId) {
    throw new Error(`Response id mismatch: expected ${expectedId}, got ${actualId || "(empty)"}`);
  }

  const expectedDatasetId = normalizeId(row.dataset_id);
  const actualDatasetId = normalizeId(payload.dataset_id);
  if (expectedDatasetId && actualDatasetId !== expectedDatasetId) {
    throw new Error(
      `Response dataset_id mismatch: expected ${expectedDatasetId}, got ${actualDatasetId || "(empty)"}`,
    );
  }

  const translation = String(payload.description_chinese || "").trim();
  if (!translation) {
    throw new Error("Response JSON did not contain description_chinese.");
  }
  return translation;
}

async function waitForUserReady(page, deepseekUrl) {
  await page.goto(deepseekUrl, { waitUntil: "domcontentloaded" });
  const rl = readline.createInterface({ input, output });
  console.log("\nDeepSeek is open in the browser.");
  console.log("Please log in if needed, open a new chat, then press Enter here.");
  await rl.question("Ready? ");
  rl.close();
}

async function fillPrompt(page, prompt) {
  const result = await page.evaluate((text, explicitSelector) => {
    const selectors = explicitSelector
      ? [explicitSelector]
      : [
          "textarea",
          "[contenteditable='true']",
          "div[role='textbox']",
          "main [contenteditable]",
        ];

    const isVisible = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden";
    };

    for (const selector of selectors) {
      const elements = Array.from(document.querySelectorAll(selector)).filter(isVisible);
      const element = elements[elements.length - 1];
      if (!element) {
        continue;
      }

      element.focus();
      if ("value" in element) {
        const proto = element.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
        const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
        if (setter) {
          setter.call(element, text);
        } else {
          element.value = text;
        }
      } else {
        element.textContent = text;
      }
      element.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText" }));
      element.dispatchEvent(new Event("change", { bubbles: true }));
      return { ok: true, selector };
    }
    return { ok: false };
  }, prompt, process.env.DEEPSEEK_INPUT_SELECTOR || "");

  if (!result.ok) {
    throw new Error("Could not find DeepSeek input box. Set DEEPSEEK_INPUT_SELECTOR.");
  }
}

async function clickSend(page) {
  const clicked = await page.evaluate((explicitSelector) => {
    const isVisible = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden";
    };

    if (explicitSelector) {
      const element = document.querySelector(explicitSelector);
      if (element && isVisible(element) && !element.disabled) {
        element.click();
        return true;
      }
      return false;
    }

    const buttons = Array.from(document.querySelectorAll("button")).filter(
      (button) => isVisible(button) && !button.disabled,
    );
    const scored = buttons.map((button, index) => {
      const label = [
        button.innerText,
        button.getAttribute("aria-label"),
        button.getAttribute("title"),
        button.getAttribute("data-testid"),
        button.getAttribute("class"),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();

      let score = index;
      if (button.type === "submit") score += 80;
      if (/send|submit|发送|送出|arrow|paper|plane/.test(label)) score += 120;
      if (/login|登录|new chat|新对话|upload|上传|mic|voice/.test(label)) score -= 200;
      return { button, score };
    });

    scored.sort((a, b) => b.score - a.score);
    if (!scored[0]) {
      return false;
    }
    scored[0].button.click();
    return true;
  }, process.env.DEEPSEEK_SEND_SELECTOR || "");

  if (!clicked) {
    await page.keyboard.press("Enter");
  }
}

async function getResponseCandidates(page) {
  return page.evaluate((explicitSelector) => {
    const selectors = explicitSelector
      ? [explicitSelector]
      : [
          "[data-message-author-role='assistant']",
          "[data-role='assistant']",
          "[class*='assistant']",
          "[class*='Assistant']",
          "[class*='message']",
          "[class*='Message']",
          "[class*='markdown']",
          "[class*='Markdown']",
          "[class*='answer']",
          "[class*='message-content']",
          "[class*='messageContent']",
          "article",
        ];

    const isVisible = (element) => {
      const rect = element.getBoundingClientRect();
      const style = window.getComputedStyle(element);
      return rect.width > 0 && rect.height > 0 && style.visibility !== "hidden";
    };

    const candidates = [];
    const seen = new Set();
    for (const selector of selectors) {
      for (const element of Array.from(document.querySelectorAll(selector))) {
        if (!isVisible(element)) {
          continue;
        }
        const text = (element.innerText || element.textContent || "").trim();
        if (text.length < 2 || seen.has(text)) {
          continue;
        }
        seen.add(text);
        const rect = element.getBoundingClientRect();
        candidates.push({
          text,
          selector,
          top: rect.top + window.scrollY,
          bottom: rect.bottom + window.scrollY,
          length: text.length,
        });
      }
    }
    candidates.sort((a, b) => a.top - b.top || a.length - b.length);
    return candidates;
  }, process.env.DEEPSEEK_RESPONSE_SELECTOR || "");
}

function looksLikeAssistantTranslation(text, row) {
  const normalized = text.trim();
  if (!normalized) {
    return false;
  }
  if (normalized.includes("请把下面这段 GridStatus")) {
    return false;
  }
  if (row.description && normalized.includes(row.description.trim().slice(0, 60))) {
    return false;
  }
  return true;
}

function candidateText(candidate) {
  return typeof candidate === "string" ? candidate : candidate.text || "";
}

function findBestResponse(candidates, beforeTexts, row) {
  const before = new Set(beforeTexts.map(candidateText));
  const fresh = candidates
    .filter((candidate) => !before.has(candidateText(candidate)))
    .filter((candidate) => looksLikeAssistantTranslation(candidateText(candidate), row));

  if (fresh.length === 0) {
    return "";
  }

  const expectedId = normalizeId(row.id);
  const expectedDatasetId = normalizeId(row.dataset_id);

  const matchingJson = fresh
    .map((candidate) => candidateText(candidate))
    .filter((text) => {
      const payload = extractJsonObject(text);
      return (
        payload &&
        (!expectedId || normalizeId(payload.id) === expectedId) &&
        (!expectedDatasetId || normalizeId(payload.dataset_id) === expectedDatasetId) &&
        String(payload.description_chinese || "").trim()
      );
    });
  if (matchingJson.length > 0) {
    return matchingJson.reduce((best, text) => (text.length > best.length ? text : best), "");
  }

  const freshTexts = fresh.map(candidateText);
  const combined = freshTexts.join("\n").trim();
  if (extractJsonObject(combined)) {
    return combined;
  }

  return freshTexts.reduce((best, text) => (text.length > best.length ? text : best), "");
}

async function waitForStableResponse(page, beforeTexts, row, options) {
  const startedAt = Date.now();
  let lastText = "";
  let lastChangedAt = Date.now();

  while (Date.now() - startedAt < options.responseTimeoutMs) {
    const candidates = await getResponseCandidates(page);
    const current = findBestResponse(candidates, beforeTexts, row);

    if (current && current !== lastText) {
      lastText = current;
      lastChangedAt = Date.now();
    }

    if (lastText && Date.now() - lastChangedAt >= options.stableMs) {
      return lastText.trim();
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }

  throw new Error("Timed out waiting for a stable DeepSeek response.");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function formatDuration(ms) {
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const restSeconds = seconds % 60;
  return restSeconds ? `${minutes}m ${restSeconds}s` : `${minutes}m`;
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const csvPath = path.resolve(options.csvPath);
  const rows = readTasks(csvPath);
  const pendingRows = rows.filter(isPending);
  const total = options.limit == null ? pendingRows.length : Math.min(options.limit, pendingRows.length);

  console.log(`CSV: ${csvPath}`);
  console.log(`Pending rows: ${pendingRows.length}`);
  console.log(`This run will translate: ${total}`);
  if (total === 0) {
    return;
  }

  const browser = await puppeteer.launch({
    executablePath: resolveExecutablePath(),
    headless: options.headless,
    userDataDir: path.resolve(options.userDataDir),
    defaultViewport: null,
    args: ["--start-maximized"],
  });

  try {
    const [page] = await browser.pages();
    await waitForUserReady(page, options.deepseekUrl);

    let done = 0;
    for (const row of rows) {
      if (!isPending(row)) {
        continue;
      }
      if (options.limit != null && done >= options.limit) {
        break;
      }

      const label = `${row.id || "?"}/${row.dataset_id || "unknown"}`;
      console.log(`\n[${done + 1}/${total}] Translating ${label}`);
      try {
        const beforeTexts = await getResponseCandidates(page);
        await fillPrompt(page, buildStructuredPrompt(row));
        await clickSend(page);
        const rawResponse = await waitForStableResponse(page, beforeTexts, row, options);
        const translated = parseStructuredTranslation(rawResponse, row);

        row.description_chinese = translated;
        row.translation_status = "done";
        row.translation_error = "";
        done += 1;
        console.log(`Done: ${label}`);
      } catch (error) {
        row.translation_status = "error";
        row.translation_error = error.message || String(error);
        console.error(`Error: ${label}: ${row.translation_error}`);
      } finally {
        writeTasks(csvPath, rows);
      }

      if (
        done > 0 &&
        options.batchSize > 0 &&
        done % options.batchSize === 0 &&
        options.batchPauseMs > 0 &&
        (options.limit == null || done < options.limit)
      ) {
        console.log(
          `Batch pause: translated ${done} rows. Resting ${formatDuration(options.batchPauseMs)}.`,
        );
        await sleep(options.batchPauseMs);
      }

      await sleep(options.delayMs);
    }
    console.log(`\nFinished. Translated ${done} rows. Please inspect the CSV before importing.`);
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
