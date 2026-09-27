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
  });
  const VOICE_MODES = Object.freeze([
    { id: "off", label: "Off", enabled: true },
    { id: "send", label: "Send", enabled: true },
    { id: "browser", label: "Browser", enabled: false },
    { id: "both", label: "Both", enabled: false },
  ]);

  function nextFormName(forms) {
    const names = new Set(forms.map((form) => form.displayName));
    let number = 0;
    while (names.has(`Custom form ${number}`)) number += 1;
    return `Custom form ${number}`;
  }

  function initialView() { return { kind: "system" }; }
  function selectSystem() { return initialView(); }
  function selectMember(memberId) { return { kind: "member", memberId }; }
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
  function deletionRequest(kind, ids) {
    const path = kind === "form"
      ? formEndpoint(ids.systemId, ids.memberId, ids.formId)
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

  return { PROFILE_FIELDS, VOICE_MODES, nextFormName, initialView, selectSystem,
    selectMember, selectForm, memberEndpoint, formEndpoint, deletionRequest,
    enterEdit, consolidatedPatch, failedSave };
});
