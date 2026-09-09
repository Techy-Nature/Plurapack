const members = [
  { name: "Nova Everlight", alias: "Nova", pronouns: "they / them", color: "#9B87F5", time: "Now", fronting: true, proxy: "nv:", id: "nv-2841", description: "The spark behind new ideas. Usually around during creative projects and late-night conversations. Loves astronomy, warm tea, and collecting tiny things.", tags: ["Creative", "Night owl", "Stargazer"], forms: [{ id: "f7a2c", displayName: "Nova Solstice", picture: "", soma: "Starlit hair, silver eyes, and a constellation of freckles." }] },
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
  $("#formList").innerHTML = (member.forms || []).length ? member.forms.map(form => `<button class="form-row" type="button" data-form-id="${form.id}"><span class="form-picture">${escapeHTML(initials(form.displayName))}</span><span><strong>${escapeHTML(form.displayName)}</strong><small>${escapeHTML(form.soma || "No soma description")}</small></span><code>${form.id}</code></button>`).join("") : '<p class="empty-forms">No forms yet. The member profile is currently used.</p>';
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
$("#memberForm").addEventListener("submit", event => {
  event.preventDefault(); const data = new FormData(event.currentTarget); const name = data.get("name").trim(); if (!name) return;
  const member = { name, alias: data.get("alias").trim() || name.split(" ")[0], pronouns: data.get("pronouns").trim() || "pronouns unset", color: /^#[0-9A-Fa-f]{6}$/.test(data.get("colorText")) ? data.get("colorText").toUpperCase() : data.get("color").toUpperCase(), time: "New", proxy: data.get("proxy").trim() || `${name.slice(0, 2).toLowerCase()}:`, id: `${name.slice(0, 2).toLowerCase()}-${Math.floor(1000 + Math.random() * 9000)}`, description: data.get("description").trim() || undefined, tags: ["New member"], forms: [] };
  members.unshift(member); $("#memberCount").textContent = members.length; $("#memberPill").textContent = members.length; closeModal(); showMember(member);
  const toast = $("#toast"); toast.textContent = `${name} was added`; toast.classList.add("show"); setTimeout(() => toast.classList.remove("show"), 2200);
});
$("#copyColor").addEventListener("click", async () => { await navigator.clipboard?.writeText($("#colorCode").textContent); const toast = $("#toast"); toast.textContent = "Color copied"; toast.classList.add("show"); setTimeout(() => toast.classList.remove("show"), 1800); });
const formModal = $("#formModal");
$("#openFormModal").addEventListener("click", () => formModal.showModal());
function closeFormModal() { formModal.close(); $("#formForm").reset(); }
$("#closeFormModal").addEventListener("click", closeFormModal);
$("#cancelFormModal").addEventListener("click", closeFormModal);
$("#formForm").addEventListener("submit", event => {
  event.preventDefault(); const data = new FormData(event.currentTarget); const displayName = data.get("displayName").trim(); if (!displayName) return;
  const picture = data.get("picture").trim(); if (picture && !/^https?:\/\//i.test(picture)) return;
  const form = { id: `f${Math.random().toString(16).slice(2, 6)}`, displayName, picture, soma: data.get("soma").trim() };
  (activeMember.forms ||= []).push(form); closeFormModal(); showMember(activeMember);
  const toast = $("#toast"); toast.textContent = `Form ${form.id} connected to ${activeMember.name}`; toast.classList.add("show"); setTimeout(() => toast.classList.remove("show"), 2200);
});
$("#formList").addEventListener("click", event => { const row = event.target.closest(".form-row"); if (!row) return; const form = activeMember.forms.find(item => item.id === row.dataset.formId); $("#detailName").textContent = form.displayName; if (form.soma) $("#detailDescription").textContent = form.soma; if (form.picture) { $("#detailAvatar").style.backgroundImage = `url("${encodeURI(form.picture)}")`; $("#detailAvatar").style.backgroundSize = "cover"; } const toast = $("#toast"); toast.textContent = `Front switched using ${form.id}`; toast.classList.add("show"); setTimeout(() => toast.classList.remove("show"), 1800); });
renderList();
