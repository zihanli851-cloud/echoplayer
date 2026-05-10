(function () {
  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function truncateText(text, maxLength) {
    if (!text || text.length <= maxLength) return text || "";
    return `${text.slice(0, maxLength)}...`;
  }

  function normalizeText(value) {
    return String(value || "").trim().toLowerCase();
  }

  window.EchoPaperReport = {
    init: function initReport(config) {
      const exportPayload = config.exportPayload || {};
      const watermarkLayer = document.getElementById("watermark-layer");
      if (watermarkLayer && config.watermarkText) {
        watermarkLayer.innerHTML = Array.from({ length: 20 }, () => `<span>${escapeHtml(config.watermarkText)}</span>`).join("");
      }

      if (window.EchoPaperExport) {
        window.EchoPaperExport.bindExportButton(document.getElementById("export-json"), {
          endpoint: "/api/reports/export-json",
          payload: exportPayload,
          fallbackName: "echopaper-report.json",
          pendingText: "导出中...",
          errorText: "JSON 导出失败，请稍后重试。",
        });
        window.EchoPaperExport.bindExportButton(document.getElementById("export-pdf"), {
          endpoint: "/api/reports/export-pdf",
          payload: exportPayload,
          fallbackName: "echopaper-report.pdf",
          pendingText: "生成中...",
          errorText: "PDF 导出失败，请稍后重试。",
        });
      }

      document.querySelectorAll("select[data-review-item-id]").forEach(function (select) {
        const state = select.parentElement.querySelector(".review-save-state");
        select.addEventListener("change", async function () {
          const itemId = select.dataset.reviewItemId;
          if (!itemId) {
            if (state) state.textContent = "未登记";
            return;
          }
          if (state) state.textContent = "保存中...";
          select.disabled = true;
          try {
            const response = await fetch(`/api/review-items/${itemId}`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ status: select.value }),
            });
            if (!response.ok) throw new Error("review update failed");
            const rows = exportPayload?.duplicate_comparison?.code_rows;
            if (Array.isArray(rows)) {
              rows.forEach(function (row) {
                if (row && row.review_item_id === itemId) row.review_status = select.value;
              });
            }
            if (state) state.textContent = "已保存";
          } catch (error) {
            if (state) state.textContent = "保存失败";
          } finally {
            select.disabled = false;
          }
        });
      });

      document.querySelectorAll("[data-toggle-section]").forEach(function (button) {
        button.addEventListener("click", function () {
          const sectionId = button.dataset.toggleSection;
          const section = document.getElementById(sectionId);
          if (!section) return;
          section.classList.toggle("is-expanded");
          const expanded = section.classList.contains("is-expanded");
          button.textContent = expanded ? "收起明细" : "展开明细";
        });
      });

      const sections = Array.from(document.querySelectorAll(".section-anchor"));
      const filterRiskOnly = document.getElementById("filter-risk-only");
      const filterPendingOnly = document.getElementById("filter-pending-only");
      const filterPaperScope = document.getElementById("filter-paper-scope");
      const filterKeyword = document.getElementById("filter-keyword");

      function sectionMatches(section) {
        const riskLevel = section.dataset.riskLevel || "";
        const paperScope = section.dataset.paperScope || "all";
        const textContent = normalizeText(section.textContent);
        const pendingRows = section.querySelectorAll('select[data-review-item-id] option:checked[value="待确认"]').length;

        if (filterRiskOnly && filterRiskOnly.checked) {
          if (!["high", "suspect", "warn"].includes(riskLevel)) return false;
        }
        if (filterPendingOnly && filterPendingOnly.checked && pendingRows === 0) {
          return false;
        }
        if (filterPaperScope && filterPaperScope.value !== "all") {
          const wanted = filterPaperScope.value;
          if (wanted === "history") {
            if (paperScope !== "history" && !textContent.includes("历史")) return false;
          } else if (paperScope !== "all" && paperScope !== wanted && !textContent.includes(`${wanted.toLowerCase()} 卷`)) {
            return false;
          }
        }
        if (filterKeyword) {
          const keyword = normalizeText(filterKeyword.value);
          if (keyword && !textContent.includes(keyword)) return false;
        }
        return true;
      }

      function applyReportFilters() {
        sections.forEach(function (section) {
          const matched = sectionMatches(section);
          section.style.display = matched ? "" : "none";
          if (
            matched &&
            filterPendingOnly &&
            filterPendingOnly.checked &&
            section.classList.contains("is-collapsible")
          ) {
            section.classList.add("is-expanded");
            const toggle = section.querySelector("[data-toggle-section]");
            if (toggle) toggle.textContent = "收起明细";
          }
        });
      }

      [filterRiskOnly, filterPendingOnly, filterPaperScope].forEach(function (node) {
        if (node) node.addEventListener("change", applyReportFilters);
      });
      if (filterKeyword) {
        filterKeyword.addEventListener("input", applyReportFilters);
      }
      applyReportFilters();

      const agentJobPanel = document.getElementById("agent-job-panel");
      if (!agentJobPanel) return;
      const jobId = agentJobPanel.dataset.agentJobId;
      const statusNode = document.getElementById("agent-job-status");
      const messageNode = document.getElementById("agent-job-message");
      let remainingPolls = 60;

      function renderAgentMetric(label, value, hint) {
        return `
          <article class="metric">
            <strong>${escapeHtml(label)}</strong>
            <span>${escapeHtml(String(value))}</span>
            <small>${escapeHtml(hint)}</small>
          </article>
        `;
      }

      function renderAgentModuleNotes(moduleMetadata) {
        const entries = Object.entries(moduleMetadata || {});
        if (!entries.length) return "";
        const rows = entries.map(function ([name, metadata]) {
          const label = metadata.provider_label || metadata.provider_name || name;
          const note = metadata.provider_note || "";
          const state = metadata.is_placeholder ? "未返回" : "已返回";
          return `<div class="agent-detail-item"><strong>${escapeHtml(name)} / ${escapeHtml(label)}</strong><p>${escapeHtml(state)}${note ? "：" + escapeHtml(note) : ""}</p></div>`;
        }).join("");
        return `<section><h3>Agent 模块状态</h3><div class="agent-detail-list">${rows}</div></section>`;
      }

      function renderAgentQuestionList(questions) {
        if (!questions.length) return `<section><h3>Agent 切题明细</h3><div class="empty"><p>Agent 未返回题目明细。</p></div></section>`;
        const rows = questions.slice(0, 12).map(function (question) {
          return `
            <div class="agent-detail-item">
              <strong>${escapeHtml(question.paper_id || "")} 卷第 ${escapeHtml(question.question_no || "")} 题</strong>
              <p>${escapeHtml(truncateText(question.content || question.raw_block || "", 180))}</p>
            </div>
          `;
        }).join("");
        const suffix = questions.length > 12 ? `<p>仅展示前 12 道，完整数据已写入当前导出 JSON。</p>` : "";
        return `<section><h3>Agent 切题明细</h3><div class="agent-detail-list">${rows}</div>${suffix}</section>`;
      }

      function renderAgentDuplicateList(matches) {
        if (!matches.length) return `<section><h3>Agent 重复明细</h3><div class="empty"><p>Agent 未返回重复题目对。</p></div></section>`;
        const rows = matches.slice(0, 10).map(function (match) {
          return `
            <div class="agent-detail-item">
              <strong>${escapeHtml(match.comparison_type || "")} / ${escapeHtml(String(match.similarity_score || 0))}% / ${escapeHtml(match.level || "")}</strong>
              <p>${escapeHtml(match.source_paper_id || "")} 第 ${escapeHtml(match.source_question_no || "")} 题 ↔ ${escapeHtml(match.target_paper_id || "")} 第 ${escapeHtml(match.target_question_no || "")} 题</p>
            </div>
          `;
        }).join("");
        const suffix = matches.length > 10 ? `<p>仅展示前 10 条，完整数据已写入当前导出 JSON。</p>` : "";
        return `<section><h3>Agent 重复明细</h3><div class="agent-detail-list">${rows}</div>${suffix}</section>`;
      }

      function renderAgentSpellcheckList(issues) {
        if (!issues.length) return `<section><h3>Agent 错字明细</h3><div class="empty"><p>Agent 未返回错字问题。</p></div></section>`;
        const rows = issues.slice(0, 10).map(function (issue) {
          return `
            <div class="agent-detail-item">
              <strong>${escapeHtml(issue.paper_id || "")} 卷第 ${escapeHtml(issue.question_no || "")} 题 / ${escapeHtml(issue.issue_type || "")}</strong>
              <p>${escapeHtml(issue.issue_text || "")} → ${escapeHtml(issue.suggestion || "")}</p>
            </div>
          `;
        }).join("");
        const suffix = issues.length > 10 ? `<p>仅展示前 10 条，完整数据已写入当前导出 JSON。</p>` : "";
        return `<section><h3>Agent 错字明细</h3><div class="agent-detail-list">${rows}</div>${suffix}</section>`;
      }

      function renderAgentResultPayload(resultPayload) {
        const detailNode = document.getElementById("agent-job-detail");
        if (!detailNode || !resultPayload) return;
        const questions = Array.isArray(resultPayload.questions) ? resultPayload.questions : [];
        const matches = Array.isArray(resultPayload.similarity_matches) ? resultPayload.similarity_matches : [];
        const issues = Array.isArray(resultPayload.spellcheck_issues) ? resultPayload.spellcheck_issues : [];
        const moduleMetadata = resultPayload.module_metadata || {};
        detailNode.classList.add("is-visible");
        detailNode.innerHTML = `
          <div class="agent-detail-grid">
            ${renderAgentMetric("Agent 切题", questions.length, "后台返回的题目明细")}
            ${renderAgentMetric("Agent 重复", matches.length, "后台返回的重复题目对")}
            ${renderAgentMetric("Agent 错字", issues.length, "后台返回的错字问题")}
          </div>
          ${renderAgentModuleNotes(moduleMetadata)}
          ${renderAgentQuestionList(questions)}
          ${renderAgentDuplicateList(matches)}
          ${renderAgentSpellcheckList(issues)}
        `;
      }

      window.renderAgentResultPayload = renderAgentResultPayload;

      async function pollAgentJob() {
        if (!jobId || remainingPolls <= 0) return;
        remainingPolls -= 1;
        try {
          const response = await fetch(`/api/agent-jobs/${jobId}`);
          if (!response.ok) return;
          const payload = await response.json();
          if (statusNode) statusNode.textContent = payload.status;
          if (payload.status === "completed") {
            if (messageNode) {
              const result = payload.result || {};
              messageNode.textContent = `Agent 对照已完成：切题 ${result.question_count || 0} 道，重复 ${result.duplicate_count || 0} 条，错字 ${result.spellcheck_count || 0} 条。`;
            }
            if (payload.result_payload) {
              exportPayload.agent_result_payload = payload.result_payload;
              renderAgentResultPayload(payload.result_payload);
            }
            return;
          }
          if (payload.status === "failed") {
            if (messageNode) messageNode.textContent = payload.error || "Agent 对照运行失败。";
            return;
          }
          window.setTimeout(pollAgentJob, 2000);
        } catch (error) {
          window.setTimeout(pollAgentJob, 4000);
        }
      }

      window.setTimeout(pollAgentJob, 1200);
    },
  };
})();
