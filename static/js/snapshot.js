(function () {
  window.EchoPaperSnapshot = {
    init: function initSnapshot(payload) {
      if (window.EchoPaperExport) {
        window.EchoPaperExport.bindExportButton(document.getElementById("export-json"), {
          endpoint: "/api/reports/export-json",
          payload,
          fallbackName: "echopaper-report.json",
          pendingText: "导出中...",
          errorText: "JSON 导出失败，请稍后重试。",
        });
        window.EchoPaperExport.bindExportButton(document.getElementById("export-pdf"), {
          endpoint: "/api/reports/export-pdf",
          payload,
          fallbackName: "echopaper-report.pdf",
          pendingText: "生成中...",
          errorText: "PDF 导出失败，请稍后重试。",
        });
      }

      function updateExportPayloadReviewStatus(itemId, status) {
        const rows = payload?.duplicate_comparison?.code_rows;
        if (!Array.isArray(rows)) return;
        rows.forEach(function (row) {
          if (row && row.review_item_id === itemId) row.review_status = status;
        });
      }

      document.querySelectorAll(".review-select").forEach(function (select) {
        select.addEventListener("change", async function () {
          const itemId = select.dataset.reviewItemId;
          const state = document.querySelector(`[data-review-state-for="${itemId}"]`);
          if (!itemId) return;
          if (state) state.textContent = "保存中...";
          select.disabled = true;
          try {
            const response = await fetch(`/api/review-items/${itemId}`, {
              method: "PATCH",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ status: select.value }),
            });
            if (!response.ok) throw new Error("review update failed");
            updateExportPayloadReviewStatus(itemId, select.value);
            if (state) state.textContent = "已保存";
          } catch (error) {
            if (state) state.textContent = "保存失败";
          } finally {
            select.disabled = false;
          }
        });
      });

      window.updateExportPayloadReviewStatus = updateExportPayloadReviewStatus;
      window.__echoPaperReviewCompat = "updateExportPayloadReviewStatus";
    },
  };
})();
