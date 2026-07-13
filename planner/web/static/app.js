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
        $("run-list").innerHTML = '<p class="hint">Loading…</p>';
        refreshRuns();
      });
    });
  }

  // ---- upload + run --------------------------------------------------

  async function uploadScript(file) {
    const fd = new FormData();
    fd.append("file", file);
    const resp = await fetch("/api/upload-script", { method: "POST", body: fd });
    if (!resp.ok) {
      const detail = await resp.json().catch(() => ({}));
      throw new Error(
        (detail && detail.detail && detail.detail.message) ||
          "Upload failed (HTTP " + resp.status + ")"
      );
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
          "Uploaded: " + file.name + " (" + result.size_bytes + " bytes)";
        $("script-path").value = result.saved_path;
        showToast("Uploaded " + file.name, "info");
      } catch (err) {
        showToast("Upload failed: " + err.message, "error");
      }
    });

    $("run-btn").addEventListener("click", async () => {
      const scriptPath = $("script-path").value.trim();
      if (!scriptPath) {
        showToast("Pick a script first.", "error");
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
          "Run started: " + result.run_id + " (" + result.status + ")",
          "info"
        );
        state.runIds.add(result.run_id);
        await refreshRuns();
      } catch (err) {
        showToast("Run failed: " + err.message, "error");
      }
    });

    $("batch-btn").addEventListener("click", async () => {
      const scriptsDir = $("batch-scripts-dir").value.trim();
      if (!scriptsDir) {
        showToast("Pick a batch scripts dir first.", "error");
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
          "Batch started: " + result.batch_id + " (" + result.status + ")",
          "info"
        );
        await refreshRuns();
      } catch (err) {
        showToast("Batch failed: " + err.message, "error");
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
        "Loaded from " + (result.path || "(default)");
    } catch (err) {
      $("model-config-status").textContent =
        "Failed to load model config: " + err.message;
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
          "Saved to " + (result.path || "(default)");
        showToast("Model config saved.", "info");
      } catch (err) {
        showToast("Save failed: " + err.message, "error");
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
      showToast("Failed to load runs: " + err.message, "error");
    }
  }

  function renderRunList(runs) {
    const list = $("run-list");
    if (!runs.length) {
      list.innerHTML = '<p class="hint">No runs yet.</p>';
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
    body.innerHTML = '<p class="hint">Loading…</p>';
    try {
      const result = await api("/api/runs/" + runId + "/summary");
      renderDrawer(result);
    } catch (err) {
      body.innerHTML =
        '<p class="error">Failed to load run: ' +
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
        ? '<div class="banner banner-warning">Fallback was used. Effective provider: ' +
          (summary.effective_provider || "?") +
          ". Reason: " +
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
      : '<p class="hint">No artifacts yet.</p>';
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
      "<h4>Audit</h4><dl>" +
      audit +
      "</dl>" +
      "<h4>Counts</h4>" +
      countsHtml +
      '<h4>Artifacts</h4><ul class="artifact-list">' +
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
    loadModelConfig();
    refreshRuns();
    startRefreshLoop();
  });
})();