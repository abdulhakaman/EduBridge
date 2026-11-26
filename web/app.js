const state = { selectedId: null, incomingOpen: false, signedIn: false, publicAddress: "" };

async function savePublicKey(response) {
  const fingerprint = response.headers.get("X-Key-Fingerprint");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "edubridge_instructor_public.pem";
  link.click();
  URL.revokeObjectURL(url);
  window.alert(`Public key downloaded.\n\nFingerprint:\n${fingerprint}\n\nShare only the public key.`);
}

const $ = (id) => document.getElementById(id);

function showTab(name) {
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.toggle("active", tab.dataset.tab === name));
  document.querySelectorAll(".panel").forEach((panel) => panel.classList.toggle("active", panel.id === name));
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => showTab(tab.dataset.tab));
});

function showApp(signedIn) {
  state.signedIn = signedIn;
  $("auth").classList.toggle("hidden", signedIn);
  $("app-tabs").classList.toggle("hidden", !signedIn);
  $("app-main").classList.toggle("hidden", !signedIn);
  $("logout").classList.toggle("hidden", !signedIn);
}

async function api(url, options = {}) {
  const response = await fetch(url, { credentials: "same-origin", ...options });
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/x-pem-file")) {
    if (!response.ok) {
      const err = await response.json().catch(() => ({ error: response.statusText }));
      throw new Error(err.error || "Request failed");
    }
    return response;
  }
  const body = contentType.includes("application/json") ? await response.json() : {};
  if (!response.ok) {
    throw new Error(body.error || response.statusText);
  }
  return body;
}

function card(html, id, onSelect) {
  const el = document.createElement("article");
  el.className = "card";
  if (id && Number(id) === Number(state.selectedId)) el.classList.add("selected");
  el.innerHTML = html;
  if (onSelect) {
    el.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      onSelect();
    });
  }
  return el;
}

function setIfIdle(id, value) {
  const el = $(id);
  if (document.activeElement === el) return;
  const next = value ?? "";
  if (el.value !== next) el.value = next;
}

function render(data) {
  $("status").textContent = data.status;
  $("progress").style.width = `${data.progress || 0}%`;
  $("cancel-send").disabled = !data.transfer_busy;

  setIfIdle("full-name", data.profile.full_name || "");
  setIfIdle("academic-id", data.profile.academic_id || "");
  setIfIdle("department", data.profile.department || "");
  setIfIdle("role", data.profile.role || "Student");

  $("whoami").textContent = data.user ? `Signed in as ${data.user.username}` : "";
  setIfIdle("account-username", data.user ? data.user.username : "");
  showAccess(data.share || {});
  $("share-status").textContent = data.share.running
    ? "LAN sharing is on."
    : "LAN sharing is optional and only works on the same Wi-Fi.";
  $("share-address").textContent = `LAN address: ${data.share.local_ip}:${data.share.port}`;
  $("toggle-share").textContent = data.share.running ? "Stop LAN sharing" : "Start LAN sharing";

  const resources = $("resource-list");
  resources.innerHTML = "";
  data.resources.forEach((item) => {
    const el = card(
      `<h4>${escapeHtml(item.title)}</h4>
       <div class="meta"><span>${escapeHtml(item.course_code)}</span><span>${escapeHtml(item.academic_year)}</span><span>${escapeHtml(item.file_name)}</span><span class="badge ${item.signature === "Signed" ? "signed" : ""}">${escapeHtml(item.signature)}</span>${item.signer ? `<span>${escapeHtml(item.signer)}</span>` : ""}</div>
       <div class="actions">
         <button data-act="open">Download</button>
         <button data-act="edit">Edit</button>
         <button data-act="remove">Remove</button>
       </div>`,
      item.id,
      () => {
        state.selectedId = item.id;
        refresh();
      },
    );
    el.querySelector("[data-act=open]").onclick = () => downloadResource(item.id);
    el.querySelector("[data-act=edit]").onclick = () => editResource(item);
    el.querySelector("[data-act=remove]").onclick = () => removeResource(item);
    resources.appendChild(el);
  });
  $("resource-count").textContent = `${data.resources.length} resource${data.resources.length === 1 ? "" : "s"}`;

  const inbox = $("inbox-list");
  inbox.innerHTML = "";
  if (!(data.inbox || []).length) {
    inbox.innerHTML = `<p class="muted">No waiting files. Send to a username below.</p>`;
  }
  (data.inbox || []).forEach((item) => {
    const el = card(
      `<h4>${escapeHtml(item.title)}</h4>
       <p class="muted">From ${escapeHtml(item.sender_name)} (@${escapeHtml(item.sender_username)})</p>
       <button data-act="accept">Accept</button>
       <button data-act="decline" class="ghost">Decline</button>`,
    );
    el.querySelector("[data-act=accept]").onclick = () => decideShare(item.id, true);
    el.querySelector("[data-act=decline]").onclick = () => decideShare(item.id, false);
    inbox.appendChild(el);
  });

  const sendFiles = $("send-files");
  sendFiles.innerHTML = "";
  const signed = data.resources.filter((item) => item.signature === "Signed");
  const chosen = data.resources.find((item) => Number(item.id) === Number(state.selectedId));
  if (!signed.length) {
    sendFiles.innerHTML = `<p class="muted">No signed files yet. Add a file, then sign it under Signatures.</p>`;
  }
  signed.forEach((item) => {
    const el = card(
      `<h4>${escapeHtml(item.title)}</h4>
       <div class="meta"><span>${escapeHtml(item.course_code)}</span><span>${escapeHtml(item.academic_year)}</span><span>${escapeHtml(item.file_name)}</span></div>`,
      item.id,
      () => {
        state.selectedId = item.id;
        refresh();
      },
    );
    sendFiles.appendChild(el);
  });
  if (chosen && chosen.signature === "Signed") {
    $("send-choice").textContent = `Selected: ${chosen.title}`;
  } else if (signed.length) {
    $("send-choice").textContent = "Choose a signed file, then a username.";
  } else {
    $("send-choice").textContent = "";
  }
  const shareTab = document.querySelector('.tab[data-tab="share"]');
  const waiting = (data.inbox || []).length;
  shareTab.textContent = waiting ? `Share (${waiting})` : "Share";

  const peers = $("peer-list");
  peers.innerHTML = "";
  if (!data.peers.length) {
    peers.innerHTML = `<p class="muted">No nearby EduBridge devices yet. You can still type an address.</p>`;
  }
  data.peers.forEach((peer) => {
    const el = card(
      `<h4>${escapeHtml(peer.name)}</h4><p class="muted">${escapeHtml(peer.address)}</p>`,
      null,
      () => {
        $("peer-address").value = peer.address;
      },
    );
    peers.appendChild(el);
  });

  const trust = $("trust-list");
  trust.innerHTML = "";
  data.trusted.forEach((item) => {
    const el = card(
      `<h4>${escapeHtml(item.instructor_name)}</h4>
       <p class="fingerprint">${escapeHtml(item.fingerprint)}</p>
       <button data-act="untrust">Remove</button>`,
    );
    el.querySelector("[data-act=untrust]").onclick = () => untrust(item.fingerprint);
    trust.appendChild(el);
  });

  for (const alert of data.alerts || []) {
    window.alert(`${alert.title}\n\n${alert.message}`);
  }

  if (data.pending_incoming && data.pending_incoming.id && !state.incomingOpen) {
    const incoming = data.pending_incoming;
    $("incoming-share-id").value = incoming.id;
    $("incoming-text").textContent =
      `Signed file from ${incoming.sender_name}. ${incoming.title} (${incoming.course}) file ${incoming.name} (${incoming.size_mb} MB). Accept this file?`;
    $("incoming-modal").classList.remove("hidden");
    state.incomingOpen = true;
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function refresh() {
  try {
    const query = $("search").value.trim();
    const data = await api(`/api/state?q=${encodeURIComponent(query)}`);
    showApp(true);
    render(data);
  } catch (err) {
    if (String(err.message).includes("Sign in first")) {
      showApp(false);
      return;
    }
    throw err;
  }
}

async function downloadResource(id) {
  const response = await fetch(`/api/resources/${id}/file`);
  if (response.status === 409) {
    const body = await response.json();
    if (!window.confirm(`${body.error}\n\nDownload it anyway?`)) return;
    window.location.href = `/api/resources/${id}/file?anyway=1`;
    return;
  }
  if (!response.ok) {
    const body = await response.json().catch(() => ({ error: response.statusText }));
    throw new Error(body.error || "Could not download file");
  }
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "";
  link.click();
  URL.revokeObjectURL(url);
}

async function editResource(item) {
  const title = window.prompt("Title", item.title);
  if (title == null) return;
  const course = window.prompt("Course code", item.course_code);
  if (course == null) return;
  const year = window.prompt("Academic year", item.academic_year);
  if (year == null) return;
  const result = await api(`/api/resources/${item.id}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, course_code: course, academic_year: year }),
  });
  if (result.cleared_signature) {
    window.alert("The course details changed, so the instructor signature was cleared. Sign the file again before sharing it.");
  }
  refresh();
}

async function removeResource(item) {
  if (!window.confirm(`Remove '${item.title}' from the library?`)) return;
  await api(`/api/resources/${item.id}`, { method: "DELETE" });
  if (state.selectedId === item.id) state.selectedId = null;
  refresh();
}

async function untrust(fingerprint) {
  if (!window.confirm("Stop trusting this instructor?")) return;
  await api(`/api/trust/${encodeURIComponent(fingerprint)}`, { method: "DELETE" });
  refresh();
}

$("search").addEventListener("input", () => refresh().catch(showError));
$("search-clear").onclick = () => {
  $("search").value = "";
  refresh().catch(showError);
};

$("add-open").onclick = () => $("add-modal").classList.remove("hidden");
$("add-cancel").onclick = () => $("add-modal").classList.add("hidden");
$("add-file").addEventListener("change", () => {
  const file = $("add-file").files[0];
  if (file && !$("add-title").value) {
    $("add-title").value = file.name.replace(/\.[^.]+$/, "");
  }
});
$("add-year").value = String(new Date().getFullYear());
$("add-form").onsubmit = async (event) => {
  event.preventDefault();
  const form = new FormData();
  form.append("file", $("add-file").files[0]);
  form.append("title", $("add-title").value);
  form.append("course_code", $("add-course").value);
  form.append("academic_year", $("add-year").value);
  try {
    await api("/api/resources", { method: "POST", body: form });
    $("add-modal").classList.add("hidden");
    $("add-form").reset();
    $("add-year").value = String(new Date().getFullYear());
    refresh();
  } catch (err) {
    showError(err);
  }
};

$("save-account").onclick = async () => {
  try {
    const user = await api("/api/account", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: $("account-username").value,
        current_password: $("account-current").value,
        new_password: $("account-new").value,
        confirmation: $("account-new2").value,
      }),
    });
    $("account-current").value = "";
    $("account-new").value = "";
    $("account-new2").value = "";
    $("whoami").textContent = `Signed in as ${user.username}`;
    refresh();
  } catch (err) {
    showError(err);
  }
};

$("save-profile").onclick = async () => {
  try {
    await api("/api/profile", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        full_name: $("full-name").value,
        academic_id: $("academic-id").value,
        department: $("department").value,
        role: $("role").value,
      }),
    });
    refresh();
  } catch (err) {
    showError(err);
  }
};

$("toggle-share").onclick = async () => {
  try {
    await api("/api/share/toggle", { method: "POST" });
    refresh();
  } catch (err) {
    showError(err);
  }
};

let userSearchTimer = 0;
$("share-username").addEventListener("input", () => {
  clearTimeout(userSearchTimer);
  userSearchTimer = setTimeout(() => searchUsers().catch(showError), 200);
});

async function searchUsers() {
  const query = $("share-username").value.trim();
  const box = $("user-matches");
  box.innerHTML = "";
  if (query.length < 2) return;
  const people = await api(`/api/users?q=${encodeURIComponent(query)}`);
  if (!people.length) {
    box.innerHTML = `<p class="muted">No account matches that name.</p>`;
    return;
  }
  people.forEach((person) => {
    const el = card(
      `<h4>@${escapeHtml(person.username)}</h4><p class="muted">${escapeHtml(person.full_name)} · ${escapeHtml(person.role)}</p>`,
    );
    el.addEventListener("click", () => {
      $("share-username").value = person.username;
      box.innerHTML = "";
    });
    box.appendChild(el);
  });
}

$("send-file").onclick = async () => {
  if (!state.selectedId) {
    window.alert("Choose a resource from the list first.");
    return;
  }
  const username = $("share-username").value.trim();
  if (!username) {
    window.alert("Enter the other person's EduBridge username.");
    return;
  }
  try {
    const result = await api("/api/share/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resource_id: state.selectedId, username }),
    });
    $("user-matches").innerHTML = "";
    $("status").textContent = `Sent to @${result.recipient}. They can accept it from any network.`;
    refresh();
  } catch (err) {
    showError(err);
  }
};

$("send-lan").onclick = async () => {
  if (!state.selectedId) {
    window.alert("Choose a resource from the list first.");
    return;
  }
  try {
    await api("/api/share/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resource_id: state.selectedId, address: $("peer-address").value }),
    });
    refresh();
  } catch (err) {
    showError(err);
  }
};

$("cancel-send").onclick = async () => {
  await api("/api/share/cancel", { method: "POST" });
  refresh();
};

$("create-key").onclick = async () => {
  try {
    const response = await api("/api/signing/key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        password: $("key-pass").value,
        confirmation: $("key-pass2").value,
        replace: false,
      }),
    });
    await savePublicKey(response);
    refresh();
  } catch (err) {
    if (String(err.message).includes("already exists") && window.confirm("Replace the existing signing key?")) {
      try {
        const response = await api("/api/signing/key", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            password: $("key-pass").value,
            confirmation: $("key-pass2").value,
            replace: true,
          }),
        });
        await savePublicKey(response);
        refresh();
      } catch (retry) {
        showError(retry);
      }
    } else {
      showError(err);
    }
  }
};

$("sign-file").onclick = async () => {
  if (!state.selectedId) {
    window.alert("Choose a resource from the list first.");
    return;
  }
  const password = window.prompt("Enter your private key passphrase:");
  if (!password) return;
  try {
    await api(`/api/signing/sign/${state.selectedId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    refresh();
  } catch (err) {
    showError(err);
  }
};

$("trust-file").addEventListener("change", async () => {
  const file = $("trust-file").files[0];
  if (!file) return;
  const form = new FormData();
  form.append("file", file);
  try {
    const info = await api("/api/trust/preview", { method: "POST", body: form });
    $("trust-fingerprint").textContent = `Fingerprint: ${info.fingerprint}`;
  } catch (err) {
    showError(err);
  }
});

$("import-key").onclick = async () => {
  const file = $("trust-file").files[0];
  if (!file) {
    window.alert("Choose an instructor public key.");
    return;
  }
  const form = new FormData();
  form.append("file", file);
  form.append("instructor_name", $("trust-name").value);
  form.append("confirmed", $("trust-confirm").checked ? "1" : "0");
  try {
    await api("/api/trust", { method: "POST", body: form });
    refresh();
  } catch (err) {
    showError(err);
  }
};

async function decideShare(shareId, approved) {
  try {
    await api("/api/share/incoming", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ approved, share_id: shareId }),
    });
    $("incoming-modal").classList.add("hidden");
    state.incomingOpen = false;
    refresh();
  } catch (err) {
    showError(err);
  }
}

$("incoming-yes").onclick = () => decideShare($("incoming-share-id").value, true);
$("incoming-no").onclick = () => decideShare($("incoming-share-id").value, false);

$("show-login").onclick = () => {
  $("login-form").classList.remove("hidden");
  $("register-form").classList.add("hidden");
};
$("show-register").onclick = () => {
  $("login-form").classList.add("hidden");
  $("register-form").classList.remove("hidden");
};
$("login-form").onsubmit = async (event) => {
  event.preventDefault();
  try {
    await api("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: $("login-username").value, password: $("login-password").value }),
    });
    await refresh();
  } catch (err) {
    showError(err);
  }
};
$("register-form").onsubmit = async (event) => {
  event.preventDefault();
  try {
    await api("/api/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: $("reg-username").value,
        password: $("reg-password").value,
        full_name: $("reg-name").value,
        academic_id: $("reg-id").value,
        department: $("reg-dept").value,
        role: $("reg-role").value,
      }),
    });
    await refresh();
  } catch (err) {
    showError(err);
  }
};
$("logout").onclick = async () => {
  await api("/api/logout", { method: "POST" });
  state.selectedId = null;
  showApp(false);
};

function showError(err) {
  window.alert(err.message || String(err));
}

function showAccess(info) {
  const url = info.public_url || "";
  state.publicAddress = url;
  $("copy-url").classList.toggle("hidden", !url);
  if (url && info.public_error) {
    $("access-url").textContent = `Fixed address is not connected: ${url}`;
    return;
  }
  if (url) {
    $("access-url").textContent = info.public_stable ? `Fixed address: ${url}` : `Temporary address: ${url}`;
    return;
  }
  if (info.public_error) {
    $("access-url").textContent = "Public HTTPS address is not ready. Start EduBridge again while this computer is online.";
    return;
  }
  if (info.public_pending) {
    $("access-url").textContent = "Creating a public HTTPS address...";
    return;
  }
  $("access-url").textContent = info.local_url ? `This computer: ${info.local_url}` : "";
}

$("copy-url").onclick = async () => {
  const address = state.publicAddress;
  if (!address) return;
  try {
    await navigator.clipboard.writeText(address);
  } catch {
    window.prompt("Copy this address:", address);
    return;
  }
  $("copy-url").textContent = "Copied";
  setTimeout(() => {
    $("copy-url").textContent = "Copy address";
  }, 1500);
};

async function loadAccess() {
  try {
    showAccess(await api("/api/access"));
  } catch {
    /* The page still works if this status request fails once. */
  }
}

loadAccess();
refresh().catch(showError);
setInterval(() => {
  loadAccess();
  if (state.signedIn) refresh().catch(() => {});
}, 1500);
