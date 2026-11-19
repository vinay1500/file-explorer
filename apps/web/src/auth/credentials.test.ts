import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import {
  MAX_EMAIL_LENGTH,
  MAX_PASSWORD_LENGTH,
  MIN_PASSWORD_LENGTH,
  faultIn,
} from "./credentials";

/**
 * The form's rules are a copy of the server's, and this is what stops the copy
 * from drifting.
 *
 * `apps/api/docs/openapi.json` is committed and the API's own tests keep it in
 * step with the marshmallow schema. A form that allows eight characters
 * produces a 422 the visitor cannot act on; a form that demands twenty rejects
 * passwords the server would have taken. Either way the person on the other
 * side is told something untrue by the application.
 *
 * The generated TypeScript cannot carry this: `openapi-typescript` turns
 * `{"type": "string", "minLength": 12}` into `string`, so the bound exists in
 * the document and nowhere else the client can see. Hence a test that reads
 * the document.
 */

interface Credentials {
  properties: {
    email: { maxLength: number };
    password: { minLength: number; maxLength: number };
  };
}

/**
 * Under jsdom `import.meta.url` is not a `file:` URL, so the document is found
 * by walking up from the working directory instead -- which also means the
 * test passes whether it is run from `apps/web` or from the repository root.
 */
function openapiPath(): string {
  const candidates = [
    resolve(process.cwd(), "../api/docs/openapi.json"),
    resolve(process.cwd(), "apps/api/docs/openapi.json"),
  ];
  const found = candidates.find((path) => existsSync(path));
  if (found === undefined) {
    throw new Error(`no OpenAPI document at any of: ${candidates.join(", ")}`);
  }
  return found;
}

function credentialsSchema(): Credentials {
  const raw = readFileSync(openapiPath(), "utf8");
  const document = JSON.parse(raw) as {
    components: { schemas: Record<string, Credentials | undefined> };
  };
  const schema = document.components.schemas.Credentials;
  // A missing schema would otherwise read as "the bounds agree", which is the
  // vacuous-test shape this project has found five of.
  if (!schema) throw new Error("the OpenAPI document has no Credentials schema");
  return schema;
}

describe("the bounds this form enforces", () => {
  it("match the committed OpenAPI document", () => {
    const schema = credentialsSchema();
    expect(MIN_PASSWORD_LENGTH).toBe(schema.properties.password.minLength);
    expect(MAX_PASSWORD_LENGTH).toBe(schema.properties.password.maxLength);
    expect(MAX_EMAIL_LENGTH).toBe(schema.properties.email.maxLength);
  });
});

describe("faultIn", () => {
  const good = "correct horse battery staple";

  it("passes a plausible pair", () => {
    expect(faultIn("a@b.co", good)).toBeNull();
  });

  it("rejects a password one character short of the server's minimum", () => {
    const short = "x".repeat(MIN_PASSWORD_LENGTH - 1);
    expect(faultIn("a@b.co", short)).toContain(String(MIN_PASSWORD_LENGTH));
  });

  it("accepts a password exactly at the minimum", () => {
    expect(faultIn("a@b.co", "x".repeat(MIN_PASSWORD_LENGTH))).toBeNull();
  });

  it("asks for an address before it asks for anything else", () => {
    expect(faultIn("", good)).toBe("An email address is needed.");
  });

  it("does not try to be clever about what an email address may contain", () => {
    // A legal address this form must not reject: no dot in the domain, which
    // several hand-written patterns refuse, and the server accepts.
    expect(faultIn("someone@localhost", good)).toBeNull();
  });
});
