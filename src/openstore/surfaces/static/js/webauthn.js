/* OpenStore shell: base64url + WebAuthn register/assert (S24).
 * Extracted from the duplicated inline copies in the studio templates. */
"use strict";

function b64url(bytes) {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64d(s) {
  const pad = s.replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(pad + "===".slice((pad.length + 3) % 4));
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

/* POST helper is in api.js; these only translate ceremony payloads. */
async function webauthnRegister(api, operatorId, displayName) {
  const begin = await api("/internal/webauthn/register/begin", {
    user_name: operatorId,
    display_name: displayName,
  });
  if (begin.authenticatorSelection) begin.authenticatorSelection.userVerification = "required";
  begin.userVerification = "required";
  const publicKey = {
    ...begin,
    challenge: b64d(begin.challenge),
    user: { ...begin.user, id: b64d(begin.user.id) },
    excludeCredentials: (begin.excludeCredentials || []).map((c) => ({ ...c, id: b64d(c.id) })),
  };
  const cred = await navigator.credentials.create({ publicKey });
  return api("/internal/webauthn/register/complete", {
    credential_id: cred.id,
    client_data_json: cred.response.clientDataJSON ? b64url(new Uint8Array(cred.response.clientDataJSON)) : "",
    attestation_object: b64url(new Uint8Array(cred.response.attestationObject)),
    challenge: begin.challenge,
  });
}

async function webauthnAssert(api, beginPath, beginBody) {
  const begin = await api(beginPath, beginBody || {});
  const assertion = await navigator.credentials.get({
    publicKey: {
      challenge: b64d(begin.challenge),
      rpId: begin.rpId,
      allowCredentials: [],
      userVerification: "required",
    },
  });
  return {
    begin,
    fields: {
      credential_id: assertion.id,
      client_data_json: b64url(new Uint8Array(assertion.response.clientDataJSON)),
      authenticator_data: b64url(new Uint8Array(assertion.response.authenticatorData)),
      signature: b64url(new Uint8Array(assertion.response.signature)),
      challenge: begin.challenge,
    },
  };
}

