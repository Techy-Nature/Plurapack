const test = require("node:test");
const assert = require("node:assert/strict");
const h = require("../dashboard_helpers.js");

test("automatic form names fill the first collision-free number", () => {
  assert.equal(h.nextFormName([]), "Custom form 0");
  assert.equal(h.nextFormName([{ displayName: "Custom form 0" }]), "Custom form 1");
  assert.equal(h.nextFormName([{ displayName: "Custom form 0" }, { displayName: "Custom form 2" }]), "Custom form 1");
});

test("new members receive a collision-free placeholder name", () => {
  assert.equal(h.nextMemberName([]), "New member");
  assert.equal(h.nextMemberName([{ name: "Alex" }]), "New member");
  assert.equal(h.nextMemberName([{ name: "New member" }]), "New member 2");
  assert.equal(h.nextMemberName([
    { name: "New member" }, { name: "New member 2" }, { name: "New member 4" },
  ]), "New member 3");
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
  const patch = h.consolidatedPatch({
    name: "Alex", pronouns: "they/them", avatar: "https://example.com/avatar.png",
    banner: null, ignored: "x",
  }, h.PROFILE_FIELDS.member);
  assert.deepEqual(patch, {
    avatar: "https://example.com/avatar.png", name: "Alex", pronouns: "they/them", banner: null,
  });
});

test("image fields are included in every profile patch", () => {
  assert.deepEqual(
    h.consolidatedPatch({ logo: "https://example.com/logo.png", banner: null }, h.PROFILE_FIELDS.system),
    { logo: "https://example.com/logo.png", banner: null },
  );
  assert.deepEqual(
    h.consolidatedPatch({ picture: "https://example.com/form.png", banner: null }, h.PROFILE_FIELDS.form),
    { picture: "https://example.com/form.png", banner: null },
  );
});

test("failed saves preserve the draft and editing state", () => {
  const draft = { name: "Unsaved name", description: "Unsaved text" };
  const failed = h.failedSave({ editing: true, draft }, "Couldn't save");
  assert.equal(failed.editing, true);
  assert.strictEqual(failed.draft, draft);
});

test("image URL validation accepts hosted images and removal", () => {
  assert.equal(h.imageUrlError(""), null);
  assert.equal(h.imageUrlError("https://example.com/avatar.png"), null);
  assert.equal(h.imageUrlError("http://example.com/banner.jpg"), null);
});

test("image URL validation rejects incomplete and unsafe URLs", () => {
  assert.equal(h.imageUrlError("example.com/avatar.png"), "Enter a complete image URL.");
  assert.equal(h.imageUrlError("data:image/png;base64,abc"), "Image URLs must use http or https.");
});

test("API error details are converted to readable messages", () => {
  assert.equal(h.apiErrorMessage({ detail: "Proxy is already used" }), "Proxy is already used");
  assert.equal(h.apiErrorMessage({ detail: [{ loc: ["body", "avatar"], msg: "Input should be a valid URL" }] }),
    "avatar: Input should be a valid URL");
  assert.equal(h.apiErrorMessage({ detail: { reason: "bad image" } }), '{"reason":"bad image"}');
});

test("all voice playback modes are enabled", () => {
  assert.deepEqual(h.VOICE_MODES.filter((mode) => mode.enabled).map((mode) => mode.id), ["off", "send", "local", "both"]);
  assert.deepEqual(h.VOICE_MODES.filter((mode) => !mode.enabled), []);
});

test("system is the initial and return view", () => {
  assert.deepEqual(h.initialView(), { kind: "system" });
  assert.deepEqual(h.selectSystem(), { kind: "system" });
});

test("groups have their own view and endpoint namespace", () => {
  assert.deepEqual(h.selectGroup("abcd1234"), { kind: "group", groupId: "abcd1234" });
  assert.equal(h.groupEndpoint("system id", "abcd1234"),
    "/api/systems/system%20id/groups/abcd1234");
  assert.deepEqual(h.deletionRequest("group", { systemId: "sys", groupId: "abcd1234" }), {
    method: "DELETE", path: "/api/systems/sys/groups/abcd1234",
  });
});

test("group patches contain only editable fields", () => {
  assert.deepEqual(h.consolidatedPatch({ id: "fixed", name: "Crew", alias: "crew", avatar: null },
    h.PROFILE_FIELDS.group), { avatar: null, name: "Crew", alias: "crew" });
});

test("member filtering preserves stable membership IDs", () => {
  const members = [{ id: "abc12", name: "Alex" }, { id: "def34", name: "River" }];
  const selected = new Set(["def34"]);
  assert.deepEqual(h.filterMembers(members, "ale").map(member => member.id), ["abc12"]);
  assert.deepEqual([...selected], ["def34"]);
});

test("membership draft supports checking and unchecking stable IDs", () => {
  let draft = h.membershipDraft(["abc12"]);
  draft = h.updateMembership(draft, "def34", true);
  assert.deepEqual([...draft], ["abc12", "def34"]);
  draft = h.updateMembership(draft, "abc12", false);
  assert.deepEqual([...draft], ["def34"]);
  assert.deepEqual(h.membershipPayload(draft), { memberIds: ["def34"] });
});

test("filtering visible members does not change the membership draft", () => {
  const members = [
    { id: "abc12", name: "Alex" },
    { id: "def34", name: "River" },
    { id: "fed43", name: "Nest" },
  ];
  const draft = h.membershipDraft(["abc12", "fed43"]);
  assert.deepEqual(h.filterMembers(members, "river").map(member => member.id), ["def34"]);
  assert.deepEqual(h.filterMembers(members, "").map(member => member.id),
    ["abc12", "def34", "fed43"]);
  assert.deepEqual(h.membershipPayload(draft), { memberIds: ["abc12", "fed43"] });
});

test("membership updates are independent from unsaved profile edits", () => {
  const profileDraft = h.enterEdit("group", {
    name: "Unsaved name", alias: "unsaved", avatar: null, memberIds: ["abc12"],
  }).draft;
  const membership = h.updateMembership(h.membershipDraft(profileDraft.memberIds), "def34", true);
  assert.deepEqual(h.membershipPayload(membership), { memberIds: ["abc12", "def34"] });
  assert.equal(profileDraft.name, "Unsaved name");
  assert.equal(profileDraft.alias, "unsaved");
});

test("active-group refresh preserves profile and membership drafts", () => {
  const refreshedSystem = { id: "system", activeGroupId: "abcd1234" };
  const profileDraft = { name: "Unsaved name", alias: "unsaved" };
  const memberDraft = h.membershipDraft(["abc12", "def34"]);
  const state = h.preservedGroupRefresh(refreshedSystem, true, profileDraft, memberDraft);

  assert.strictEqual(state.system, refreshedSystem);
  assert.equal(state.editing, true);
  assert.strictEqual(state.profileDraft, profileDraft);
  assert.strictEqual(state.memberDraft, memberDraft);
});
