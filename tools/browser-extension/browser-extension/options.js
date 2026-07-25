// options.js:可视化编辑白名单
const DEFAULT_WHITELIST = [
  { port: 33335, host: "127.0.0.1", label: "TRADIS (Docker 管理)" },
  { port: 7860,  host: "127.0.0.1", label: "SD WebUI?" },
  { port: 8088,  host: "192.168.0.123", label: "" },
];

function render(items) {
  const tbody = document.querySelector("#tbl tbody");
  tbody.innerHTML = "";
  items.forEach((it, i) => {
    const tr = document.createElement("tr");
    // 用 DOM API 构建单元格,避免 innerHTML 属性注入(用户输入的 host/label 可能含 ")
    const tdPort = document.createElement("td");
    const inpPort = document.createElement("input");
    inpPort.type = "number"; inpPort.min = "1"; inpPort.max = "65535";
    inpPort.value = it.port || "";
    inpPort.dataset.i = i; inpPort.dataset.k = "port";
    tdPort.appendChild(inpPort);

    const tdHost = document.createElement("td");
    const inpHost = document.createElement("input");
    inpHost.type = "text";
    inpHost.value = it.host || "";
    inpHost.dataset.i = i; inpHost.dataset.k = "host";
    tdHost.appendChild(inpHost);

    const tdLabel = document.createElement("td");
    const inpLabel = document.createElement("input");
    inpLabel.type = "text";
    inpLabel.value = it.label || "";
    inpLabel.dataset.i = i; inpLabel.dataset.k = "label";
    tdLabel.appendChild(inpLabel);

    const tdDel = document.createElement("td");
    const btnDel = document.createElement("button");
    btnDel.textContent = "删";
    btnDel.dataset.del = i;
    tdDel.appendChild(btnDel);

    tr.appendChild(tdPort);
    tr.appendChild(tdHost);
    tr.appendChild(tdLabel);
    tr.appendChild(tdDel);
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll("button[data-del]").forEach(b => {
    b.onclick = () => {
      const i = +b.dataset.del;
      items.splice(i, 1);
      render(items);
    };
  });
  tbody.querySelectorAll("input").forEach(inp => {
    inp.onchange = () => {
      const i = +inp.dataset.i, k = inp.dataset.k;
      items[i][k] = inp.type === "number" ? +inp.value : inp.value;
    };
  });
}

let items = [];
chrome.storage.local.get(["whitelist"], (r) => {
  items = r.whitelist || JSON.parse(JSON.stringify(DEFAULT_WHITELIST));
  render(items);
});

document.getElementById("add").onclick = () => {
  items.push({ port: 0, host: "127.0.0.1", label: "" });
  render(items);
};
document.getElementById("save").onclick = () => {
  // 过滤掉无效行(port=0 或空)
  const cleaned = items.filter(it => it.port > 0 && it.port <= 65535);
  chrome.storage.local.set({ whitelist: cleaned }, () => {
    document.getElementById("status").textContent = `✓ 已保存 ${cleaned.length} 条`;
    setTimeout(() => document.getElementById("status").textContent = "", 2000);
    items = cleaned;
    render(items);
  });
};
document.getElementById("reset").onclick = () => {
  items = JSON.parse(JSON.stringify(DEFAULT_WHITELIST));
  render(items);
};