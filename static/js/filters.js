(function () {
  window.EchoPaperFilters = {
    initHistoryBankJobPanel: function initHistoryBankJobPanel() {
      const button = document.getElementById("rebuild-history-bank");
      const statusBox = document.getElementById("history-bank-job-status");
      if (!button || !statusBox) return;

      function escapeHtml(value) {
        return String(value).replace(/[&<>"']/g, function (char) {
          return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char];
        });
      }

      function renderJobStatus(payload) {
        const result = payload.result || {};
        const statusText = {
          queued: "排队中",
          running: "重建中",
          completed: "已完成",
          failed: "失败",
        }[payload.status] || payload.status;
        const detail = payload.status === "completed"
          ? `PDF ${result.total_files || 0} 份，成功 ${result.loaded_files || 0} 份，题目 ${result.question_count || 0} 道。`
          : (payload.error || `任务 ID：${payload.job_id}`);
        statusBox.innerHTML = `<strong>题库任务：${statusText}</strong> ${escapeHtml(detail)}`;
      }

      async function pollJob(jobId) {
        try {
          const response = await fetch(`/api/history-bank/jobs/${jobId}`);
          if (!response.ok) throw new Error("poll failed");
          const payload = await response.json();
          renderJobStatus(payload);
          if (payload.status === "queued" || payload.status === "running") {
            window.setTimeout(function () { pollJob(jobId); }, 1500);
            return;
          }
        } catch (error) {
          statusBox.innerHTML = "<strong>题库任务</strong> 查询失败，请刷新页面查看。";
        }
        button.disabled = false;
      }

      button.addEventListener("click", async function () {
        button.disabled = true;
        statusBox.style.display = "block";
        statusBox.innerHTML = "<strong>题库任务</strong> 正在提交后台重建...";
        try {
          const response = await fetch("/history-bank/rebuild", { method: "POST" });
          if (!response.ok) throw new Error("submit failed");
          const payload = await response.json();
          renderJobStatus(payload);
          pollJob(payload.job_id);
        } catch (error) {
          statusBox.innerHTML = "<strong>题库任务</strong> 提交失败，请稍后重试。";
          button.disabled = false;
        }
      });
    },
  };
})();
