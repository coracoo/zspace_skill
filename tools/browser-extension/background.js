// background.js:omnibox "zra <port>" 跳转 + 安装时初始化白名单
const DEFAULT_WHITELIST = [
  { port: 33335, host: "127.0.0.1", label: "TRADIS (Docker 管理)" },
  { port: 7860,  host: "127.0.0.1", label: "SD WebUI?" },
  { port: 8088,  host: "192.168.0.123", label: "" },
];

chrome.runtime.onInstalled.addListener(() => {
  chrome.storage.local.get(["whitelist"], (r) => {
    if (!r.whitelist) chrome.storage.local.set({ whitelist: DEFAULT_WHITELIST });
  });
});

chrome.omnibox.onInputChanged.addListener((text, suggest) => {
  const port = parseInt(text.trim(), 10);
  if (!port || port < 1 || port > 65535) {
    suggest([{ content: "输入端口号 (1-65535)", description: "格式:zra 33335" }]);
    return;
  }
  suggest([{
    content: `https://remote-access-${port}.zconnect.cn/`,
    description: `端口 ${port} → remote-access-${port}.zconnect.cn`,
  }]);
});

chrome.omnibox.onInputEntered.addListener((text) => {
  const t = text.trim();
  // 用户从下拉里选中建议时,content 是完整 URL;直接键入的则是端口号
  if (t.startsWith("http://") || t.startsWith("https://")) {
    chrome.tabs.update(undefined, { url: t });
    return;
  }
  const port = parseInt(t, 10);
  if (port && port > 0 && port <= 65535) {
    chrome.tabs.update(undefined, {
      url: `https://remote-access-${port}.zconnect.cn/`,
    });
  }
});