(function () {
  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
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

        if (filterRiskOnly && filterRiskOnly.checked && !["high", "suspect", "warn"].includes(riskLevel)) {
          return false;
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
    },
  };
})();
