const members = [
  { name: "Nova Everlight", alias: "Nova", pronouns: "they / them", color: "#9B87F5", time: "Now", fronting: true, proxy: "nv:", id: "nv-2841", defaultFormId: "f7a2c", description: "The spark behind new ideas. Usually around during creative projects and late-night conversations. Loves astronomy, warm tea, and collecting tiny things.", tags: ["Creative", "Night owl", "Stargazer"], forms: [{ id: "f7a2c", displayName: "Nova Solstice", picture: "", soma: "Starlit hair, silver eyes, and a constellation of freckles." }] },
  { name: "Milo", pronouns: "he / him", color: "#F0A981", time: "Now", fronting: true, proxy: "mi:", id: "mi-1093", tags: ["Baker", "Optimist"] },
  { name: "Echo", pronouns: "she / they", color: "#79D6C4", time: "2h", proxy: "ec:", id: "ec-7530", tags: ["Music", "Quiet"] },
  { name: "Sage", pronouns: "they / she", color: "#A9C780", time: "1d", proxy: "sg:", id: "sg-6612", tags: ["Nature", "Reader"] },
  { name: "Atlas", pronouns: "he / they", color: "#78AEEB", time: "3d", proxy: "at:", id: "at-4820", tags: ["Maps", "Calm"] },
  { name: "Wren", pronouns: "she / her", color: "#E89AB3", time: "5d", proxy: "wr:", id: "wr-2284", tags: ["Writer", "Dreamer"] },
  { name: "Rowan", pronouns: "he / him", color: "#D3926B", time: "1w", proxy: "ro:", id: "ro-5028", tags: ["Hiking", "Coffee"] },
  { name: "Lumi", pronouns: "they / them", color: "#D6CB79", time: "2w", proxy: "lu:", id: "lu-7719", tags: ["Sunshine", "Crafts"] }
];

const $ = (selector) => document.querySelector(selector);
const list = $("#memberList");
let activeMember = members[0];
let currentAccount;
let currentSystem;

class ApiError extends Error {
  constructor(response, message) { super(message); this.status = response.status; }
}
async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin", ...options,
    headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}), ...options.headers }
  });
  if (response.status === 401) {
    const returnTo = `${location.pathname}${location.search}${location.hash}`;
    location.assign(`/login?returnTo=${encodeURIComponent(returnTo)}`);
    throw new ApiError(response, "Your session has expired.");
  }
  if (response.status === 403) throw new ApiError(response, "You do not have access to this system.");
  if (!response.ok) {
    let message = `The server returned ${response.status}.`;
    try { message = (await response.json()).message || message; } catch { /* The error body may not be JSON. */ }
    throw new ApiError(response, message);
  }
  return response.status === 204 ? null : response.json();
}
function showToast(message, error = false) { const toast = $("#toast"); toast.textContent = message; toast.classList.toggle("error", error); toast.classList.add("show"); setTimeout(() => toast.classList.remove("show"), 2400); }
function setBusy(form, busy) { form.querySelectorAll("button,input,textarea").forEach(control => { control.disabled = busy; }); form.setAttribute("aria-busy", String(busy)); }

function initials(name) { return name.trim().split(/\s+/).map(part => part[0]).join("").slice(0, 2).toUpperCase(); }
function escapeHTML(value) { const node = document.createElement("span"); node.textContent = value; return node.innerHTML; }
function renderList(query = "") {
  const filtered = members.filter(member => `${member.name} ${member.alias || ""}`.toLowerCase().includes(query.toLowerCase()));
  list.innerHTML = filtered.length ? filtered.map(member => `
    <button class="member-item ${member === activeMember ? "active" : ""}" type="button" data-id="${member.id}">
      <span class="avatar member-avatar ${member.fronting ? "fronting" : ""}" style="--member-color:${member.color}">${escapeHTML(initials(member.name))}</span>
      <span class="member-copy"><strong>${escapeHTML(member.name)}</strong><span>${escapeHTML(member.pronouns)}</span></span><time>${escapeHTML(member.time)}</time>
    </button>`).join("") : '<div class="empty-state">No members found.</div>';
}
function showMember(member) {
  activeMember = member;
  $("#detailAvatar").textContent = initials(member.name);
  $("#detailAvatar").style.background = `linear-gradient(145deg, ${member.color}, #544980)`;
  $("#detailName").textContent = member.name;
  $("#detailAlias").textContent = member.alias || member.name;
  $("#detailPronouns").textContent = member.pronouns;
  $("#frontingBadge").hidden = !member.fronting;
  $("#detailDescription").textContent = member.description || `${member.name} is part of the Lumen System. Their profile is ready for more details, notes, and the things they love.`;
  $("#detailTags").innerHTML = member.tags.map(tag => `<span>${tag}</span>`).join("");
  $("#proxyTag").textContent = `${member.proxy}text`;
  $("#proxyName").textContent = member.name;
  $("#colorCode").textContent = member.color;
  $("#colorSwatch").style.background = member.color;
  $("#memberId").textContent = member.id;
  $("#formMemberName").textContent = member.name;
  $("#formList").innerHTML = (member.forms || []).length ? member.forms.map(form => `<div class="form-row ${member.defaultFormId === form.id ? "default" : ""}" data-form-id="${form.id}"><button class="form-preview" type="button"><span class="form-picture">${escapeHTML(initials(form.displayName))}</span><span><strong>${escapeHTML(form.displayName)}</strong><small>${escapeHTML(form.soma || "No soma description")}</small></span><code>${form.id}</code></button><button class="default-form-button" type="button" aria-label="${member.defaultFormId === form.id ? "Clear" : "Set"} ${escapeHTML(form.displayName)} as default" title="${member.defaultFormId === form.id ? "Clear default" : "Set as default"}">${member.defaultFormId === form.id ? "★ Default" : "☆ Default"}</button></div>`).join("") : '<p class="empty-forms">No forms yet. The member profile is currently used.</p>';
  renderList($("#memberSearch").value);
}
list.addEventListener("click", event => { const item = event.target.closest(".member-item"); if (item) showMember(members.find(member => member.id === item.dataset.id)); });
$("#memberSearch").addEventListener("input", event => renderList(event.target.value));
document.addEventListener("keydown", event => { if ((event.metaKey || event.ctrlKey) && event.key === "k") { event.preventDefault(); $("#memberSearch").focus(); } });

const modal = $("#memberModal");
$("#openModal").addEventListener("click", () => { modal.showModal(); setTimeout(() => $("#newName").focus(), 50); });
function closeModal() { modal.close(); $("#memberForm").reset(); }
$("#closeModal").addEventListener("click", closeModal);
$("#cancelModal").addEventListener("click", closeModal);
modal.addEventListener("click", event => { if (event.target === modal) closeModal(); });
const colorPicker = $("input[name=color]");
const colorText = $("input[name=colorText]");
colorPicker.addEventListener("input", () => { colorText.value = colorPicker.value.toUpperCase(); });
colorText.addEventListener("input", () => { if (/^#[0-9A-Fa-f]{6}$/.test(colorText.value)) colorPicker.value = colorText.value; });
$("#memberForm").addEventListener("submit", async event => {
  event.preventDefault(); const form = event.currentTarget; const data = new FormData(form); const name = data.get("name").trim(); if (!name) return;
  const payload = { name, alias: data.get("alias").trim() || null, pronouns: data.get("pronouns").trim() || null, color: /^#[0-9A-Fa-f]{6}$/.test(data.get("colorText")) ? data.get("colorText").toUpperCase() : data.get("color").toUpperCase(), proxy: data.get("proxy").trim() || null, description: data.get("description").trim() || null };
  try {
    setBusy(form, true);
    const member = await api(`/api/systems/${encodeURIComponent(currentSystem.id)}/members`, { method: "POST", body: JSON.stringify(payload) });
    member.forms ||= []; member.tags ||= []; members.unshift(member); closeModal();
    $("#memberCount").textContent = members.length; $("#memberPill").textContent = members.length; showMember(member); showToast(`${member.name} was added`);
  } catch (error) { if (error.status !== 401) showToast(error.message, true); } finally { setBusy(form, false); }
});
$("#copyColor").addEventListener("click", async () => { await navigator.clipboard?.writeText($("#colorCode").textContent); showToast("Color copied"); });
const formModal = $("#formModal");
$("#openFormModal").addEventListener("click", () => formModal.showModal());
function closeFormModal() { formModal.close(); $("#formForm").reset(); }
$("#closeFormModal").addEventListener("click", closeFormModal);
$("#cancelFormModal").addEventListener("click", closeFormModal);
$("#formForm").addEventListener("submit", async event => {
  event.preventDefault(); const formElement = event.currentTarget; const data = new FormData(formElement); const displayName = data.get("displayName").trim(); if (!displayName) return;
  const picture = data.get("picture").trim(); if (picture && !/^https?:\/\//i.test(picture)) return showToast("Picture must be an HTTP or HTTPS URL.", true);
  try {
    setBusy(formElement, true);
    const form = await api(`/api/systems/${encodeURIComponent(currentSystem.id)}/members/${encodeURIComponent(activeMember.id)}/forms`, { method: "POST", body: JSON.stringify({ displayName, picture: picture || null, soma: data.get("soma").trim() }) });
    (activeMember.forms ||= []).push(form); closeFormModal(); showMember(activeMember); showToast(`Form ${form.id} connected to ${activeMember.name}`);
  } catch (error) { if (error.status !== 401) showToast(error.message, true); } finally { setBusy(formElement, false); }
});
$("#formList").addEventListener("click", async event => { const row = event.target.closest(".form-row"); if (!row) return; const form = activeMember.forms.find(item => item.id === row.dataset.formId); if (event.target.closest(".default-form-button")) { const defaultFormId = activeMember.defaultFormId === form.id ? null : form.id; try { await api(`/api/systems/${encodeURIComponent(currentSystem.id)}/members/${encodeURIComponent(activeMember.id)}`, { method: "PATCH", body: JSON.stringify({ defaultFormId }) }); activeMember.defaultFormId = defaultFormId; showMember(activeMember); showToast(defaultFormId ? `${form.displayName} is now the default form` : "Default form cleared"); } catch (error) { if (error.status !== 401) showToast(error.message, true); } return; } $("#detailName").textContent = form.displayName; if (form.soma) $("#detailDescription").textContent = form.soma; if (form.picture) { $("#detailAvatar").style.backgroundImage = `url("${encodeURI(form.picture)}")`; $("#detailAvatar").style.backgroundSize = "cover"; } showToast(`Front switched using ${form.id}`); });
async function loadDashboard() {
  try {
    currentAccount = await api("/api/account");
    const requestedId = new URLSearchParams(location.search).get("system") || currentAccount.systemId || currentAccount.system?.id;
    if (!requestedId) throw new Error("Your account is not connected to a system yet.");
    currentSystem = await api(`/api/systems/${encodeURIComponent(requestedId)}`);
    currentSystem.id ||= requestedId;
    if (Array.isArray(currentSystem.members) && currentSystem.members.length) members.splice(0, members.length, ...currentSystem.members);
    const accountName = currentAccount.displayName || currentAccount.name || currentAccount.username;
    $("#accountName").textContent = accountName; $("#accountAvatar").textContent = initials(accountName); $("#accountRole").textContent = currentAccount.role || "System admin";
    const systemName = currentSystem.displayName || currentSystem.name; $("#systemName").textContent = systemName; document.title = `Plurapack — ${systemName}`;
    $("#memberCount").textContent = members.length; $("#memberPill").textContent = members.length; $("#fronterCount").textContent = members.filter(member => member.fronting).length;
    showMember(members[0]);
  } catch (error) {
    if (error.status === 401) return;
    $("#dashboard").hidden = true; const state = $("#accessState"); state.hidden = false;
    state.querySelector("h1").textContent = error.status === 403 ? "This system is private" : "Dashboard unavailable";
    state.querySelector("p").textContent = error.status === 403 ? "You’re signed in, but this system doesn’t belong to your account." : error.message;
  }
}
renderList();
loadDashboard();
