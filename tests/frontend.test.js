const test = require("node:test");
const assert = require("node:assert/strict");
const h = require("../dashboard_helpers.js");

test("automatic form names fill the first collision-free number", () => {
  assert.equal(h.nextFormName([]), "Custom form 0");
  assert.equal(h.nextFormName([{ displayName: "Custom form 0" }]), "Custom form 1");
  assert.equal(h.nextFormName([{ displayName: "Custom form 0" }, { displayName: "Custom form 2" }]), "Custom form 1");
});

test("form selection preserves the selected member", () => {
  const selected = h.selectForm(h.selectMember("mem01"), "form2");
  assert.deepEqual(selected, { kind: "member", memberId: "mem01", formId: "form2" });
});

test("form and member deletion use distinct resource endpoints", () => {
  const ids = { systemId: "system", memberId: "member", formId: "form" };
  assert.deepEqual(h.deletionRequest("form", ids), {
    method: "DELETE", path: "/api/systems/system/members/member/forms/form",
  });
  assert.deepEqual(h.deletionRequest("member", ids), {
    method: "DELETE", path: "/api/systems/system/members/member",
  });
});

test("edit mode exposes the entire current member profile", () => {
  const profile = { name: "Alex", prefix: "a:", description: "Kept" };
  const state = h.enterEdit("member", profile);
  assert.equal(state.editing, true);
  assert.deepEqual(state.editableFields, h.PROFILE_FIELDS.member);
  assert.deepEqual(state.draft, profile);
});

test("apply builds one consolidated profile patch", () => {
  const patch = h.consolidatedPatch({ name: "Alex", pronouns: "they/them", ignored: "x" }, h.PROFILE_FIELDS.member);
  assert.deepEqual(patch, { name: "Alex", pronouns: "they/them" });
});

test("failed saves preserve the draft and editing state", () => {
  const draft = { name: "Unsaved name", description: "Unsaved text" };
  const failed = h.failedSave({ editing: true, draft }, "Couldn't save");
  assert.equal(failed.editing, true);
  assert.strictEqual(failed.draft, draft);
});

test("all voice playback modes are enabled", () => {
  assert.deepEqual(h.VOICE_MODES.filter((mode) => mode.enabled).map((mode) => mode.id), ["off", "send", "local", "both"]);
  assert.deepEqual(h.VOICE_MODES.filter((mode) => !mode.enabled), []);
});

test("system is the initial and return view", () => {
  assert.deepEqual(h.initialView(), { kind: "system" });
  assert.deepEqual(h.selectSystem(), { kind: "system" });
});
