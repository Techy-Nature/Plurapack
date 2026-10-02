(function (root, factory) {
  const helpers = factory();
  if (typeof module === "object" && module.exports) module.exports = helpers;
  root.PlurapackDashboard = helpers;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const PROFILE_FIELDS = Object.freeze({
    system: ["logo", "displayName", "description", "tag", "showSystemTag", "banner"],
    member: ["avatar", "name", "color", "pronouns", "description", "alias", "prefix", "suffix", "banner"],
    form: ["picture", "displayName", "pronouns", "soma", "prefix", "suffix", "banner"],
    group: ["avatar", "name", "alias"],
  });
  const VOICE_MODES = Object.freeze([
    { id: "off", label: "Off", enabled: true },
    { id: "send", label: "Send", enabled: true },
    { id: "local", label: "Browser", enabled: true },
    { id: "both", label: "Both", enabled: true },
  ]);

  function nextFormName(forms) {
    const names = new Set(forms.map((form) => form.displayName));
    let number = 0;
    while (names.has(`Custom form ${number}`)) number += 1;
    return `Custom form ${number}`;
  }

  function nextMemberName(members) {
    const names = new Set(members.map((member) => member.name));
    if (!names.has("New member")) return "New member";
    let number = 2;
    while (names.has(`New member ${number}`)) number += 1;
    return `New member ${number}`;
  }

  function initialView() { return { kind: "system" }; }
  function selectSystem() { return initialView(); }
  function selectMember(memberId) { return { kind: "member", memberId }; }
  function selectGroup(groupId) { return { kind: "group", groupId }; }
  function selectForm(view, formId) {
    if (view.kind !== "member") throw new Error("A form requires a selected member.");
    return { ...view, formId };
  }
  function memberEndpoint(systemId, memberId) {
    return `/api/systems/${encodeURIComponent(systemId)}/members/${encodeURIComponent(memberId)}`;
  }
  function formEndpoint(systemId, memberId, formId) {
    return `${memberEndpoint(systemId, memberId)}/forms/${encodeURIComponent(formId)}`;
  }
  function groupEndpoint(systemId, groupId) {
    return `/api/systems/${encodeURIComponent(systemId)}/groups/${encodeURIComponent(groupId)}`;
  }
  function deletionRequest(kind, ids) {
    const path = kind === "form"
      ? formEndpoint(ids.systemId, ids.memberId, ids.formId)
      : kind === "group" ? groupEndpoint(ids.systemId, ids.groupId)
      : memberEndpoint(ids.systemId, ids.memberId);
    return { method: "DELETE", path };
  }
  function enterEdit(kind, profile, form) {
    const draft = typeof structuredClone === "function"
      ? structuredClone({ ...profile, ...(form ? { form } : {}) })
      : JSON.parse(JSON.stringify({ ...profile, ...(form ? { form } : {}) }));
    return { editing: true, draft, editableFields: [...PROFILE_FIELDS[kind]] };
  }
  function consolidatedPatch(fields, allowedFields) {
    return Object.fromEntries(allowedFields.filter((key) => Object.hasOwn(fields, key)).map((key) => [key, fields[key]]));
  }
  function failedSave(state, error) {
    return { ...state, editing: true, error, draft: state.draft };
  }

  function filterMembers(members, query) {
    const normalized = query.trim().toLowerCase();
    return members.filter((member) => member.name.toLowerCase().includes(normalized));
  }

  function membershipDraft(memberIds) {
    return new Set(memberIds || []);
  }

  function updateMembership(current, memberId, checked) {
    const updated = new Set(current);
    if (checked) updated.add(memberId);
    else updated.delete(memberId);
    return updated;
  }

  function membershipPayload(current) {
    return { memberIds: [...current] };
  }

  function imageUrlError(value) {
    if (!value) return null;
    let url;
    try {
      url = new URL(value);
    } catch {
      return "Enter a complete image URL.";
    }
    if (!["http:", "https:"].includes(url.protocol)) return "Image URLs must use http or https.";
    return null;
  }

  function apiErrorMessage(payload, fallback = "Something went wrong.") {
    const detail = payload?.detail ?? payload?.message;
    if (typeof detail === "string" && detail) return detail;
    if (Array.isArray(detail)) {
      const messages = detail.map((item) => {
        if (typeof item === "string") return item;
        const location = Array.isArray(item?.loc) ? item.loc.filter((part) => part !== "body").join(".") : "";
        return `${location ? `${location}: ` : ""}${item?.msg || JSON.stringify(item)}`;
      });
      if (messages.length) return messages.join("; ");
    }
    if (detail && typeof detail === "object") return JSON.stringify(detail);
    return fallback;
  }

  return { PROFILE_FIELDS, VOICE_MODES, nextFormName, nextMemberName, initialView, selectSystem,
    selectMember, selectGroup, selectForm, memberEndpoint, formEndpoint, groupEndpoint,
    deletionRequest, enterEdit, consolidatedPatch, failedSave, filterMembers,
    membershipDraft, updateMembership, membershipPayload,
    imageUrlError, apiErrorMessage };
});
