// planner-web static UI — vanilla JS, no framework.
//
// Talks to the FastAPI backend over the documented JSON endpoints.
// All errors are surfaced as human-readable text via ``showToast`` —
// never a raw traceback. The backend's ``PlannerError`` exception
// handler already filters tracebacks out of HTTP responses; this
// file's job is to render the JSON shape we get back as friendly UI.
//
// Run history auto-refreshes every 4 seconds while the page is
// visible so operators see state transitions (queued → running →
// done / failed) without manual reload. The refresh is paused when
// the page is hidden to keep idle CPU at zero.

(function () {
  "use strict";

  // ---- state ---------------------------------------------------------

  const state = {
    env: "development",         // active environment tab
    runIds: new Set(),          // run ids currently in the table
    refreshTimer: null,
    modelConfigPath: null,      // path returned by GET /api/model-config
  };

  // ---- DOM helpers ---------------------------------------------------

  const $ = (id) => document.getElementById(id);

  function showToast(message, level) {
    const region = $("toast-region");
    const note = document.createElement("div");
    note.className = "toast toast-" + (level || "info");
    note.textContent = message;
    region.appendChild(note);
    setTimeout(() => note.remove(), 6000);
  }

  function setEnvWarning() {
    const warning = $("env-warning");
    if (state.env === "production") {
      warning.hidden = false;
    } else {
      warning.hidden = true;
    }
  }

  // ---- API client ----------------------------------------------------

  async function api(path, options) {
    const opts = options || {};
    const resp = await fetch(path, {
      method: opts.method || "GET",
      headers: Object.assign(
        { "Content-Type": "application/json" },
        opts.headers || {}
      ),
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
    if (!resp.ok) {
      let msg = "HTTP " + resp.status;
      try {
        const detail = await resp.json();
        msg = detail.detail
          ? detail.detail.message || detail.detail.error || msg
          : msg;
      } catch (_) {
        // body wasn't JSON; keep the generic HTTP message
      }
      throw new Error(msg);
    }
    if (resp.status === 204) return null;
    return resp.json();
  }

  // ---- error formatting (P0A-3) ---------------------------------------

  // formatUserError: turn a server / network error into a user-friendly
  // message. The server returns a stable JSON shape
  // {error: <type>, message: <raw>}; the type is engineering semantics,
  // the message may be technical. This helper translates the type into
  // a human sentence and appends the raw message in parentheses for
  // debugging. Unknown types fall back to a generic sentence. No
  // traceback is ever shown (the backend filters tracebacks at the
  // HTTP boundary; see planner/web/app.py::_planner_error_handler).
  function formatUserError(err) {
    var errType = null;
    var rawMsg = "";
    if (err && typeof err === "object") {
      if (err.detail && err.detail.error) {
        errType = err.detail.error;
        rawMsg = err.detail.message || "";
      } else if (err.message) {
        rawMsg = err.message;
      }
    } else if (typeof err === "string") {
      rawMsg = err;
    }
    var map = {
      "BrokenReferenceError":
        "分镜表里有引用了不存在的角色 / 场景 / 道具 ID。建议检查 shot_list 和 bibles 的一致性，或切到 deterministic 重新生成。",
      "ProviderOutputError":
        "模型返回的格式无法解析（可能是 JSON 不合法或字段缺失）。建议：① 重试一次；② 切到 deterministic 模式；③ 检查 base_url / model 配置。",
      "ConfigError":
        "配置问题。",
      "EnvironmentBoundaryError":
        "环境 / 路径问题。",
      "ProviderUnavailableError":
        "模型未通过健康检查。可切到 deterministic 或检查 API key env var 是否设置。",
      "ScriptReadError":
        "剧本读取失败。请检查文件路径 / 编码 / 大小。",
    };
    var prefix = "运行失败";
    if (errType && map[errType]) {
      prefix = map[errType];
    } else if (errType === "UploadValidationError") {
      prefix = "上传失败";
    } else if (errType) {
      prefix = "运行失败（" + errType + "）";
    }
    if (rawMsg) {
      return prefix + " 详情：" + rawMsg;
    }
    return prefix;
  }

  // ---- P0A-4: out-dir live preview ------------------------------------

  // bindOutDirPreview: when the user types a parent folder in
  // #out-dir, show a real-time preview of the actual run subdirectory
  // the backend would create. Frontend estimate only (precision: 1
  // second; backend run_id is yyyymmdd-HHMMSS-microseconds-randomhex —
  // see planner/web/run_service.py::generate_run_id). UX is the goal,
  // not contract fidelity.
  function bindOutDirPreview() {
    var outInput = $("out-dir");
    var preview = $("out-dir-preview");
    if (!outInput || !preview) return;
    var pad = function (n) { return String(n).padStart(2, "0"); };
    var fmt = function (date) {
      if (isNaN(date.getTime())) return "默认子目录";
      return date.getFullYear() +
        pad(date.getMonth() + 1) +
        pad(date.getDate()) +
        "-" +
        pad(date.getHours()) +
        pad(date.getMinutes()) +
        pad(date.getSeconds());
    };
    var update = function () {
      var v = outInput.value.trim();
      if (!v) {
        preview.textContent = "默认子目录";
        return;
      }
      // Strip trailing slashes and append /<timestamp>/
      var parent = v.replace(/\/+$/, "");
      preview.textContent = parent + "/" + fmt(new Date()) + "/";
    };
    outInput.addEventListener("input", update);
    update();
  }

  // ---- env tabs ------------------------------------------------------

  function bindEnvTabs() {
    document.querySelectorAll(".env-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        const env = btn.id.replace("env-", "");
        if (env === state.env) return;
        state.env = env;
        document.querySelectorAll(".env-tab").forEach((b) => {
          b.setAttribute(
            "aria-selected",
            b.id === "env-" + env ? "true" : "false"
          );
        });
        setEnvWarning();
        // Reset run list because env-scoped.
        $("run-list").innerHTML = '<p class="hint">加载中…</p>';
        refreshRuns();
      });
    });
  }

  // ---- upload + run --------------------------------------------------

  async function uploadScript(file) {
    var fd = new FormData();
    fd.append("file", file);
    var resp;
    try {
      resp = await fetch("/api/upload-script", { method: "POST", body: fd });
    } catch (networkErr) {
      // Network / fetch-level error (server unreachable, CORS, etc.)
      var err = new Error("网络错误，无法连接服务器。");
      err.detail = null;
      throw err;
    }
    if (!resp.ok) {
      // Parse {detail: {error, message}} from response and attach to
      // the Error so formatUserError can map by error type
      // (UploadValidationError → "上传失败 ..." prefix).
      var detail = null;
      try {
        detail = (await resp.json()).detail;
      } catch (_) {
        // body wasn't JSON; keep detail null
      }
      var err2 = new Error("Upload failed");
      err2.detail = detail;
      throw err2;
    }
    return resp.json();
  }

  function bindRunControls() {
    $("upload-input").addEventListener("change", async (ev) => {
      const file = ev.target.files[0];
      if (!file) return;
      try {
        const result = await uploadScript(file);
        $("upload-status").textContent =
          "已上传：" + file.name + "（" + result.size_bytes + " 字节）";
        $("script-path").value = result.saved_path;
        showToast("已上传 " + file.name, "info");
      } catch (err) {
        showToast(formatUserError(err), "error");
      }
    });

    $("run-btn").addEventListener("click", async () => {
      const scriptPath = $("script-path").value.trim();
      if (!scriptPath) {
        showToast("请先选择剧本。", "error");
        return;
      }
      const body = {
        env: state.env,
        script_path: scriptPath,
        out_dir: $("out-dir").value.trim() || null,
        model_config_path: state.modelConfigPath || null,
      };
      try {
        const result = await api("/api/runs", { method: "POST", body: body });
        showToast(
          "运行已启动：" + result.run_id + "（" + result.status + "）",
          "info"
        );
        state.runIds.add(result.run_id);
        await refreshRuns();
      } catch (err) {
        showToast(formatUserError(err), "error");
      }
    });

    $("batch-btn").addEventListener("click", async () => {
      const scriptsDir = $("batch-scripts-dir").value.trim();
      if (!scriptsDir) {
        showToast("请先选择批量剧本文本目录。", "error");
        return;
      }
      const body = {
        env: state.env,
        scripts_dir: scriptsDir,
        out_dir: $("out-dir").value.trim() || null,
        fail_fast: true,
        skip_validation: false,
        model_config_path: state.modelConfigPath || null,
      };
      try {
        const result = await api("/api/batches", { method: "POST", body: body });
        showToast(
          "批量任务已启动：" + result.batch_id + "（" + result.status + "）",
          "info"
        );
        await refreshRuns();
      } catch (err) {
        showToast(formatUserError(err), "error");
      }
    });
  }

  // ---- model config load / save --------------------------------------

  async function loadModelConfig() {
    try {
      const result = await api("/api/model-config");
      state.modelConfigPath = result.path || null;
      const cfg = result.config || {};
      if (cfg.planner_provider) {
        const sel = $("provider-select");
        if (sel) sel.value = cfg.planner_provider;
      }
      // Fill the openai_compatible section as the canonical v1.0
      // runtime target. The openai/anthropic sections are skeletons.
      const section = cfg.openai_compatible || {};
      if (section.model) $("model-name").value = section.model;
      if (section.base_url) $("base-url").value = section.base_url;
      if (section.api_key_env) $("api-key-env").value = section.api_key_env;
      $("enable-real-calls").checked = !!cfg.enable_real_model_calls;
      $("allow-fallback").checked = !!cfg.allow_provider_fallback;
      $("model-config-status").textContent =
        "已加载自 " + (result.path || "（默认）");
    } catch (err) {
      $("model-config-status").textContent =
        "加载模型配置失败：" + err.message;
    }
  }

  function bindModelConfigControls() {
    $("save-model-config-btn").addEventListener("click", async () => {
      const cfg = {
        planner_provider: $("provider-select").value,
        enable_real_model_calls: $("enable-real-calls").checked,
        allow_provider_fallback: $("allow-fallback").checked,
        openai_compatible: {
          base_url: $("base-url").value.trim() || "https://api.openai.com/v1",
          model: $("model-name").value.trim() || "gpt-4o-mini",
          api_key_env: $("api-key-env").value.trim() || "OPENAI_API_KEY",
        },
      };
      try {
        const result = await api("/api/model-config", {
          method: "PUT",
          body: { config: cfg },
        });
        state.modelConfigPath = result.path || state.modelConfigPath;
        $("model-config-status").textContent =
          "已保存到 " + (result.path || "（默认）");
        showToast("模型配置已保存。", "info");
      } catch (err) {
        showToast("保存失败：" + err.message, "error");
      }
    });
  }

  // ---- run history + drawer -----------------------------------------

  async function refreshRuns() {
    try {
      const result = await api(
        "/api/runs?env=" + state.env + "&limit=50"
      );
      renderRunList(result.runs || []);
    } catch (err) {
      showToast("加载运行历史失败：" + err.message, "error");
    }
  }

  function renderRunList(runs) {
    const list = $("run-list");
    if (!runs.length) {
      list.innerHTML = '<p class="hint">暂无运行记录。</p>';
      return;
    }
    list.innerHTML = "";
    runs.forEach((run) => {
      const row = document.createElement("button");
      row.type = "button";
      row.className = "run-row run-" + run.status;
      row.innerHTML =
        '<span class="run-id">' + run.run_id + "</span>" +
        '<span class="run-status">' + run.status + "</span>" +
        '<span class="run-env">' + run.env + "</span>";
      row.addEventListener("click", () => openDrawer(run.run_id));
      list.appendChild(row);
      state.runIds.add(run.run_id);
    });
  }

  async function openDrawer(runId) {
    const drawer = $("run-drawer");
    const body = $("drawer-body");
    drawer.hidden = false;
    body.innerHTML = '<p class="hint">加载中…</p>';
    try {
      const result = await api("/api/runs/" + runId + "/summary");
      renderDrawer(result);
    } catch (err) {
      body.innerHTML =
        '<p class="error">加载运行详情失败：' +
        err.message +
        "</p>";
    }
  }

  function renderDrawer(rec) {
    const body = $("drawer-body");
    const summary = rec.summary || {};
    const audit = summary
      ? [
          ["requested_provider", summary.requested_provider],
          ["effective_provider", summary.effective_provider],
          ["fallback_used", summary.fallback_used],
          ["fallback_reason", summary.fallback_reason],
          ["env", summary.env],
        ]
          .map(
            (p) =>
              "<dt>" +
              p[0] +
              "</dt><dd>" +
              (p[1] === null || p[1] === undefined ? "—" : String(p[1])) +
              "</dd>"
          )
          .join("")
      : "";
    const fallbackBanner =
      summary.fallback_used
        ? '<div class="banner banner-warning">已使用回退。实际模型来源：' +
          (summary.effective_provider || "?") +
          "。原因：" +
          (summary.fallback_reason || "?") +
          "</div>"
        : "";
    const counts = summary.counts || rec.counts || {};
    const countsHtml = Object.keys(counts).length
      ? "<dl>" +
        Object.keys(counts)
          .map(
            (k) =>
              "<dt>" +
              k +
              "</dt><dd>" +
              String(counts[k]) +
              "</dd>"
          )
          .join("") +
        "</dl>"
      : '<p class="hint">暂无产物。</p>';
    // run_summary.json's ``artifacts`` is a dict ``{name: path}``, not
    // an array. Older builds assumed ``.map()`` and crashed the drawer
    // when a completed run was opened. Normalize to a name list so
    // both shapes render.
    var artifactNames = [];
    if (summary.artifacts) {
      if (Array.isArray(summary.artifacts)) {
        artifactNames = summary.artifacts;
      } else if (typeof summary.artifacts === "object") {
        artifactNames = Object.keys(summary.artifacts);
      }
    }
    var artifactLinks = artifactNames
      .map(
        (a) =>
          '<li><a href="/api/runs/' +
          rec.run_id +
          "/artifacts/" +
          a +
          '" target="_blank">' +
          a +
          "</a></li>"
      )
      .join("");
    body.innerHTML =
      fallbackBanner +
      "<h4>审计信息</h4><dl>" +
      audit +
      "</dl>" +
      "<h4>产物计数</h4>" +
      countsHtml +
      '<h4>产物列表</h4><ul class="artifact-list">' +
      artifactLinks +
      "</ul>";
  }

  $("drawer-close").addEventListener("click", () => {
    $("run-drawer").hidden = true;
  });

  // ---- background refresh -------------------------------------------

  function startRefreshLoop() {
    const tick = () => {
      if (!document.hidden) {
        refreshRuns().catch(() => {});
      }
    };
    state.refreshTimer = setInterval(tick, 4000);
  }

  // ---- boot ----------------------------------------------------------

  document.addEventListener("DOMContentLoaded", () => {
    bindEnvTabs();
    bindRunControls();
    bindModelConfigControls();
    setEnvWarning();
    bindOutDirPreview();
    loadModelConfig();
    refreshRuns();
    startRefreshLoop();
  });
})();