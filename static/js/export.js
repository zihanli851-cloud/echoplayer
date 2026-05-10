(function () {
  function parseFilename(disposition, fallbackName) {
    const value = disposition || "";
    const utfMatch = value.match(/filename\*=UTF-8''([^;]+)/i);
    if (utfMatch && utfMatch[1]) {
      try {
        return decodeURIComponent(utfMatch[1]);
      } catch (error) {
        return fallbackName;
      }
    }
    const match = value.match(/filename="([^"]+)"/i);
    return match && match[1] ? match[1] : fallbackName;
  }

  async function downloadPayload(endpoint, payload, fallbackName) {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!response.ok) {
      throw new Error(`request failed: ${endpoint}`);
    }
    const blob = await response.blob();
    const filename = parseFilename(response.headers.get("Content-Disposition"), fallbackName);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  function bindExportButton(button, config) {
    if (!button || !config || !config.payload) return;
    button.addEventListener("click", async function () {
      const originalText = button.textContent;
      button.disabled = true;
      button.textContent = config.pendingText;
      try {
        await downloadPayload(config.endpoint, config.payload, config.fallbackName);
      } catch (error) {
        window.alert(config.errorText);
      } finally {
        button.disabled = false;
        button.textContent = originalText;
      }
    });
  }

  window.EchoPaperExport = {
    bindExportButton,
  };
})();
