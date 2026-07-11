// popup.js:读 chrome.storage,渲染白名单为可点击链接
const DEFAULT_WHITELIST = [
  { port: 33335, host: "127.0.0.1", label: "TRADIS (Docker 管理)" },
  { port: 33331, host: "127.0.0.1", label: "" },
  { port: 33332, host: "127.0.0.1", label: "" },
  { port: 33333, host: "127.0.0.1", label: "" },
  { port: 33334, host: "127.0.0.1", label: "" },
  { port: 33336, host: "127.0.0.1", label: "" },
  { port: 33337, host: "127.0.0.1", label: "" },
  { port: 7860,  host: "127.0.0.1", label: "SD WebUI?" },
  { port: 8088,  host: "192.168.0.123", label: "" },
  { port: 3000,  host: "192.168.0.123", label: "" },
  { port: 9876,  host: "192.168.0.118", label: "" },
];

async function load() {
  return new Promise((resolve) => {
    chrome.storage.local.get(["whitelist"], (r) => {
      resolve(r.whitelist || DEFAULT_WHITELIST);
    });
  });
}

function render(items) {
  const list = document.getElementById("list");
  document.getElementById("count").textContent = `${items.length} 条`;
  if (!items.length) {
    list.innerHTML = '<div class="empty">白名单为空。<a href="#" id="add">添加</a></div>';
    document.getElementById("add").onclick = (e) => {
      e.preventDefault(); chrome.runtime.openOptionsPage();
    };
    return;
  }
  list.innerHTML = "";
  for (const item of items) {
    if (!item.port) continue;
    const url = `https://remote-access-${item.port}.zconnect.cn/`;
    const a = document.createElement("a");
    a.className = "item";
    a.href = url;
    a.target = "_blank";
    // 用 DOM API + textContent 渲染用户输入字段,避免 innerHTML XSS
    const portSpan = document.createElement("span");
    portSpan.className = "port";
    portSpan.textContent = `:${item.port}`;
    const labelSpan = document.createElement("span");
    labelSpan.className = "label";
    labelSpan.textContent = item.label || "(未命名)";
    const hostSpan = document.createElement("span");
    hostSpan.className = "host";
    hostSpan.textContent = item.host || "127.0.0.1";
    a.appendChild(portSpan);
    a.appendChild(labelSpan);
    a.appendChild(hostSpan);
    list.appendChild(a);
  }
}

document.getElementById("options").onclick = (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
};

load().then(render);